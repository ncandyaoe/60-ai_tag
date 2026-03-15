"""
服务端标签 PDF 生成器（Canvas 版）

使用 reportlab Canvas 直接绘制 70mm×69mm 合规标签 PDF，
PyMuPDF 渲染为 PNG 预览图。
布局参考设计师提供的规范图。
"""

import io
import base64
import os
import re
from typing import Optional, Tuple, List

from template_config import TemplateConfig, get_default_template, EcoIconConfig

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import black

import fitz  # PyMuPDF

# --------------------------------------------------
# 常量（从默认模板派生，保持向后兼容）
# --------------------------------------------------
_DEFAULT_TPL = get_default_template()

LABEL_W = _DEFAULT_TPL.label_w
LABEL_H = _DEFAULT_TPL.label_h
MARGIN = _DEFAULT_TPL.margin

# Logo 专区
LOGO_W = _DEFAULT_TPL.logo.width_pt
LOGO_H = _DEFAULT_TPL.logo.height_pt
LOGO_PAD = _DEFAULT_TPL.logo.padding_pt

# --------------------------------------------------
# 字体注册
# --------------------------------------------------
_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"          # 默认降级
_FONT_NAME_BOLD = "Helvetica-Bold"
_HAS_REAL_BOLD = True              # 是否有独立的 Bold 字体文件


def _register_font():
    """尝试注册中文字体，优先阿里普惠体，降级到 NISC18030。"""
    global _FONT_REGISTERED, _FONT_NAME, _FONT_NAME_BOLD, _HAS_REAL_BOLD
    if _FONT_REGISTERED:
        return

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    # 优先：阿里巴巴普惠体
    alibaba_r = os.path.join(static_dir, "Alibaba-PuHuiTi-Regular.ttf")
    alibaba_b = os.path.join(static_dir, "Alibaba-PuHuiTi-Bold.ttf")
    if os.path.isfile(alibaba_r) and os.path.getsize(alibaba_r) > 100_000:
        pdfmetrics.registerFont(TTFont("AliPuHuiTi", alibaba_r))
        if os.path.isfile(alibaba_b) and os.path.getsize(alibaba_b) > 100_000:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_b))
            _HAS_REAL_BOLD = True
        else:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_r))
            _HAS_REAL_BOLD = False
        _FONT_NAME = "AliPuHuiTi"
        _FONT_NAME_BOLD = "AliPuHuiTi-Bold"
        _FONT_REGISTERED = True
        return

    # 降级：尝试 static/ 下其他 TTF 字体
    for fallback_name in ["NISC18030.ttf", "STHeiti-Medium.ttf"]:
        fallback_path = os.path.join(static_dir, fallback_name)
        if os.path.isfile(fallback_path):
            try:
                fname = fallback_name.replace(".ttf", "").replace("-", "")
                pdfmetrics.registerFont(TTFont(fname, fallback_path))
                _FONT_NAME = fname
                _FONT_NAME_BOLD = fname
                _FONT_REGISTERED = True
                return
            except Exception:
                continue

    _FONT_REGISTERED = True


# --------------------------------------------------
# 模拟加粗辅助函数
# --------------------------------------------------
def _start_bold(c, font_size: float):
    """开启模拟加粗（描边模式），仅在没有真正 Bold 字体时生效。"""
    if not _HAS_REAL_BOLD:
        c.setStrokeColor(black)
        c.setLineWidth(font_size * 0.04)
        c._code.append('2 Tr')


def _end_bold(c):
    """关闭模拟加粗。"""
    if not _HAS_REAL_BOLD:
        c._code.append('0 Tr')


def _draw_bold_string(c, x: float, y: float, text: str, font_size: float):
    """绘制加粗文字。"""
    c.setFont(_FONT_NAME_BOLD, font_size)
    _start_bold(c, font_size)
    c.drawString(x, y, text)
    _end_bold(c)


def _draw_bold_right_string(c, x: float, y: float, text: str, font_size: float):
    """绘制右对齐加粗文字。"""
    c.setFont(_FONT_NAME_BOLD, font_size)
    _start_bold(c, font_size)
    c.drawRightString(x, y, text)
    _end_bold(c)


# --------------------------------------------------
# 三阶段自适应字号算法
# --------------------------------------------------
# 字体度量常量（字体级别，不随模板变化）
_CAP_H_RATIO = _DEFAULT_TPL.cap_height_ratio
_X_HEIGHT_RATIO = _DEFAULT_TPL.x_height_ratio

# 固定字号（从默认模板派生）
_FIXED_TITLE = _DEFAULT_TPL.fixed_sizes.title_pt
_FIXED_CN    = _DEFAULT_TPL.fixed_sizes.cn_pt
_FIXED_NET   = _DEFAULT_TPL.fixed_sizes.net_pt
_FIXED_NUT_ROW_H = _DEFAULT_TPL.nutrition.row_height_pt
_FIXED_NUT   = _DEFAULT_TPL.nutrition.font_size_pt

# 自适应字号范围（从默认模板派生）
_SIZE_MAX = _DEFAULT_TPL.size_max()
_SIZE_MIN = _DEFAULT_TPL.size_min()


def _sizes_at_scale(scale: float, tpl: 'TemplateConfig' = None) -> dict:
    """按 scale (0.0~1.0) 在最小/最大之间线性插值得到一组字号。"""
    if tpl is None:
        s_max, s_min = _SIZE_MAX, _SIZE_MIN
    else:
        s_max, s_min = tpl.size_max(), tpl.size_min()
    return {
        k: s_min[k] + (s_max[k] - s_min[k]) * scale
        for k in s_max
    }


def _effective_width(w: float, h_scale: float) -> float:
    """横向压缩时等效可用宽度（字更窄 → 同一行能放更多字）。"""
    return w / h_scale if h_scale > 0 else w


def _net_font_size(net_text: str, max_fs: float, max_width: float) -> float:
    """计算 Net Volume 在指定宽度内能使用的最大字号。"""
    _register_font()
    fs = max_fs
    text_w = pdfmetrics.stringWidth(net_text, _FONT_NAME_BOLD, fs)
    if text_w > max_width and text_w > 0:
        fs = fs * max_width / text_w
    return max(fs, _SIZE_MIN["net"])


def _count_text_lines(text: str, font_name: str, font_size: float,
                      max_width: float, bold_prefix: str = "",
                      narrow_width: float = 0, narrow_lines: int = 0) -> int:
    """估算一段文本在给定字号和可用宽度下需要多少行。
    narrow_width/narrow_lines: 前 N 行使用较窄宽度（logo 避让）。
    """
    if not text:
        return 0

    if bold_prefix:
        prefix_w = pdfmetrics.stringWidth(bold_prefix, _FONT_NAME_BOLD, font_size)
        remaining_first_line = (narrow_width if narrow_lines > 0 and narrow_width > 0 else max_width) - prefix_w
    else:
        remaining_first_line = narrow_width if narrow_lines > 0 and narrow_width > 0 else max_width

    words = text.split(' ')
    lines = 1
    current_line = ""
    first_line = True

    for word in words:
        # 判断当前行是否在 logo 区域内
        w_limit = max_width
        if narrow_width > 0 and lines <= narrow_lines:
            w_limit = narrow_width
        avail = remaining_first_line if first_line else w_limit
        test_line = current_line + (" " if current_line else "") + word
        w = pdfmetrics.stringWidth(test_line, font_name, font_size)
        if w <= avail:
            current_line = test_line
        else:
            if current_line:
                lines += 1
                first_line = False
            elif first_line and bold_prefix:
                lines += 1
                first_line = False
            current_line = word

    return lines


def _count_text_lines_lshape(text: str, font_name: str, font_size: float,
                              full_width: float, narrow_width: float,
                              cursor: float, nut_boundary: float,
                              leading: float, bold_prefix: str = "") -> Tuple[int, float]:
    """估算 L 形区域中文本行数，逐行追踪 cursor 位置切换宽度。

    当 cursor < nut_boundary 时使用 full_width，否则使用 narrow_width。
    每输出一行，cursor 下移 leading。

    Returns:
        (行数, 更新后的 cursor)
    """
    if not text:
        return 0, cursor

    def _avail_at(cur):
        return narrow_width if cur >= nut_boundary else full_width

    avail_first = _avail_at(cursor)
    if bold_prefix:
        prefix_w = pdfmetrics.stringWidth(bold_prefix, _FONT_NAME_BOLD, font_size)
        remaining_first_line = avail_first - prefix_w
    else:
        remaining_first_line = avail_first

    words = text.split(' ')
    lines = 1
    current_line = ""
    first_line = True

    for word in words:
        avail = remaining_first_line if first_line else _avail_at(cursor + (lines - 1) * leading)
        test_line = current_line + (" " if current_line else "") + word
        w = pdfmetrics.stringWidth(test_line, font_name, font_size)
        if w <= avail:
            current_line = test_line
        else:
            if current_line:
                lines += 1
                first_line = False
            elif first_line and bold_prefix:
                lines += 1
                first_line = False

            # 检查单词本身是否太长
            next_avail = _avail_at(cursor + (lines - 1) * leading)
            word_w = pdfmetrics.stringWidth(word, font_name, font_size)
            if word_w > next_avail:
                # 字符级断词
                chunk = ""
                for ch in word:
                    test_chunk = chunk + ch
                    cw = pdfmetrics.stringWidth(test_chunk, font_name, font_size)
                    line_avail = _avail_at(cursor + (lines - 1) * leading)
                    if cw > line_avail and chunk:
                        lines += 1
                        first_line = False
                        chunk = ch
                    else:
                        chunk = test_chunk
                current_line = chunk
            else:
                current_line = word

    new_cursor = cursor + lines * leading
    return lines, new_cursor


def _calc_nutrition_height(data: dict, sizes: dict, tpl: 'TemplateConfig' = None) -> float:
    """计算营养成分表的总高度 (pt)。"""
    nut = data.get("nutrition") or {}
    table_data = nut.get("table_data") or []
    font_pad = tpl.nutrition.font_padding_pt if tpl else 2
    nut_row_h = sizes["nut"] + font_pad  # 行高 = 字号 + padding
    title_fs = sizes["nut"] + font_pad   # 标题字号
    h = title_fs + 4              # 标题行高（Nutrition Information 行）
    h += nut_row_h * 2            # 列标题行（含 serving size，两行高）
    h += len(table_data) * nut_row_h  # 数据行
    return h


def _collect_block_heights(data: dict, sizes: dict, content_w: float,
                           left_col_w: float,
                           nut_narrow_w: float = 0) -> list[float]:
    """
    收集 B+C 区域所有信息块的纯文字高度（不含间距）。
    返回列表中每个元素对应一个信息块的高度。
    顺序: Ingredients, Contains, Storage, Date, ProductOf, Mfr, Addr, Importer

    nut_narrow_w: 进入营养表避让区域时使用的窄宽度（0 = 用 left_col_w）。
    """
    _register_font()
    block_heights = []

    eff_w = content_w
    # 营养表避让区域的窄宽度（Manufacturer/Address/Importer 使用）
    eff_nut_w = nut_narrow_w if nut_narrow_w > 0 else left_col_w

    # Logo 专区
    logo_reserve = LOGO_W + LOGO_PAD
    logo_zone_h = LOGO_H + LOGO_PAD

    ingr_leading = sizes["ingr"] * 1.15
    body_leading = sizes["body"] * 1.15

    # 估算 A 区域高度，用于计算 logo 剩余影响范围
    a_h = sizes["title"] * 0.8
    a_h += sizes["title"] * 1.15 + 1
    if data.get("product_name_cn"):
        a_h += sizes["cn"] * 1.15 + 1

    logo_remaining_h = max(0, logo_zone_h - (a_h - sizes["title"] * 0.8))
    narrow_w = eff_w - logo_reserve

    # Ingredients
    ingr = data.get("ingredients", "")
    if ingr:
        narrow_lines_ingr = int(logo_remaining_h / ingr_leading) + 1 if logo_remaining_h > 0 else 0
        n = _count_text_lines(ingr, _FONT_NAME, sizes["ingr"], eff_w, "Ingredients: ",
                             narrow_width=narrow_w, narrow_lines=narrow_lines_ingr)
        block_heights.append(n * ingr_leading)
        logo_remaining_h = max(0, logo_remaining_h - n * ingr_leading)

    # Contains
    allergens = data.get("allergens", "")
    if allergens:
        narrow_lines_allerg = int(logo_remaining_h / body_leading) + 1 if logo_remaining_h > 0 else 0
        n = _count_text_lines(allergens, _FONT_NAME, sizes["body"], eff_w, "Contains: ",
                             narrow_width=narrow_w, narrow_lines=narrow_lines_allerg)
        block_heights.append(n * body_leading)

    # Storage
    storage = data.get("storage", "")
    if storage:
        n = _count_text_lines(storage, _FONT_NAME, sizes["body"], eff_w)
        block_heights.append(n * body_leading)

    # Production date / Best Before（合并为一行估算）
    prod_date = data.get("production_date", "")
    best_before = data.get("best_before", "")
    if prod_date or best_before:
        date_parts = []
        if prod_date:
            date_parts.append(f"Production date: {prod_date}")
        if best_before:
            date_parts.append(f"Best Before: {best_before}")
        date_text = " / ".join(date_parts)
        n = _count_text_lines(date_text, _FONT_NAME_BOLD, sizes["body"], eff_w)
        block_heights.append(n * body_leading)

    # Product of China（全宽，通常在营养表上方）
    block_heights.append(body_leading)

    # Manufacturer / Address / Imported by
    # 渲染时 _draw_wrapped_text 用 content_w，仅 y < nut_top_y 时缩窄
    # 估算也用 eff_w（全宽），与渲染一致；缩窄部分由 c_left_gap 处理
    for field, prefix in [("manufacturer", "Manufacturer: "),
                           ("manufacturer_address", "Address: "),
                           ("importer_info", "Imported by:")]:
        txt = data.get(field, "")
        if txt:
            n = _count_text_lines(txt, _FONT_NAME, sizes["body"], eff_w, prefix)
            block_heights.append(n * body_leading)

    return block_heights


def _estimate_content_height(data: dict, sizes: dict, content_w: float,
                             left_col_w: float,
                             unified_gap: float = 1.0,
                             available_h: float = 0,
                             tpl: 'TemplateConfig' = None) -> Tuple[float, int]:
    """
    估算全部内容所需的总高度 (pt)。
    布局模型：B+C 统一文本流 + L 形区域精确建模。
    通过追踪累计高度，判断每个 C 块是在营养表上方（用全宽）还是下方（用窄宽），
    精确匹配 _draw_wrapped_text 的渲染逻辑。
    纵向估算不考虑横向压缩（h_scale），始终按原始宽度计算。
    """
    # A 区域高度（最后一个元素用 1.0 leading，减少 A→B 过渡空白）
    h = sizes["title"] * 0.8
    a_gap = 1
    if data.get("product_name_cn"):
        h += sizes["title"] * 1.15 + a_gap   # en name (非最后)
        h += sizes["cn"] * 1.0 + a_gap       # cn name (最后，leading 缩减)
    else:
        h += sizes["title"] * 1.0 + a_gap    # en name (最后，leading 缩减)

    # Net Volume 预留高度
    if tpl:
        net_reserve = tpl.net_reserve(bool(data.get("net_weight")))
    else:
        net_reserve = (_FIXED_NET * _CAP_H_RATIO) if data.get("net_weight") else 0

    # 营养表高度和边界
    if tpl:
        right_col_ratio = tpl.nutrition.right_col_ratio
        col_gap = tpl.nutrition.col_gap_pt
    else:
        right_col_ratio = 0.62
        col_gap = 4
    nut_h = _calc_nutrition_height(data, sizes, tpl)

    # 有效宽度（不考虑横向压缩）
    eff_w = content_w
    # 窄宽度：与渲染层 _draw_wrapped_text._line_width() 完全一致
    nut_reserve_w = content_w * right_col_ratio
    eff_nut_narrow = eff_w - nut_reserve_w

    body_leading = sizes["body"] * 1.15
    ingr_leading = sizes["ingr"] * 1.15

    # 营养表顶部到标签顶部的距离（从顶部往下数，到哪里开始变窄）
    # nut_top_y 从底部算起 = nut_h；从顶部算起 = available_h - nut_h
    nut_boundary = available_h - nut_h if available_h > 0 else 999  # 如果没传 available_h，不做修正

    # ----- 逐块追踪累计高度，精确计算行数 -----
    cursor = h  # 从 A 区域底部开始
    block_heights = []

    # --- Logo 避让相关 ---
    if tpl and tpl.logo.enabled:
        logo_zone_h = tpl.logo.zone_h
        logo_reserve = tpl.logo.reserve_w
    elif tpl and not tpl.logo.enabled:
        logo_zone_h = 0
        logo_reserve = 0
    else:
        logo_zone_h = 25
        logo_reserve = 12 * mm
    a_h_for_logo = sizes["title"] * 0.8 + sizes["title"] * 1.15 + a_gap
    if data.get("product_name_cn"):
        a_h_for_logo += sizes["cn"] * 1.15 + a_gap
    logo_remaining_h = max(0, logo_zone_h - (a_h_for_logo - sizes["title"] * 0.8))
    narrow_w_logo = eff_w - logo_reserve

    # B 块 —— 全宽（logo 避让区域内除外）
    # Ingredients
    ingr = data.get("ingredients", "")
    if ingr:
        narrow_lines_ingr = int(logo_remaining_h / ingr_leading) + 1 if logo_remaining_h > 0 else 0
        n = _count_text_lines(ingr, _FONT_NAME, sizes["ingr"], eff_w, "Ingredients: ",
                             narrow_width=narrow_w_logo, narrow_lines=narrow_lines_ingr)
        bh = n * ingr_leading
        block_heights.append(bh)
        cursor += bh + unified_gap
        logo_remaining_h = max(0, logo_remaining_h - bh)

    # Contains
    allergens = data.get("allergens", "")
    if allergens:
        narrow_lines_allerg = int(logo_remaining_h / body_leading) + 1 if logo_remaining_h > 0 else 0
        n = _count_text_lines(allergens, _FONT_NAME, sizes["body"], eff_w, "Contains: ",
                             narrow_width=narrow_w_logo, narrow_lines=narrow_lines_allerg)
        bh = n * body_leading
        block_heights.append(bh)
        cursor += bh + unified_gap

    # Storage（可能进入营养表避让区域，使用 L 形估算）
    storage = data.get("storage", "")
    if storage:
        n, cursor = _count_text_lines_lshape(
            storage, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor, nut_boundary=nut_boundary,
            leading=body_leading
        )
        bh = n * body_leading
        block_heights.append(bh)
        cursor += unified_gap

    # Date（可能进入营养表避让区域，使用 L 形估算）
    prod_date = data.get("production_date", "")
    best_before = data.get("best_before", "")
    if prod_date or best_before:
        date_parts = []
        if prod_date: date_parts.append(f"Production date: {prod_date}")
        if best_before: date_parts.append(f"Best Before: {best_before}")
        date_text = " / ".join(date_parts)
        n, cursor = _count_text_lines_lshape(
            date_text, _FONT_NAME_BOLD, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor, nut_boundary=nut_boundary,
            leading=body_leading
        )
        bh = n * body_leading
        block_heights.append(bh)
        cursor += unified_gap

    # Product of
    bh = body_leading
    block_heights.append(bh)
    cursor += bh + unified_gap

    # C 块 —— 逐行追踪 cursor，L 形区域内混合全宽/窄宽
    c_fields = [("manufacturer", "Manufacturer: "),
                ("manufacturer_address", "Address: "),
                ("importer_info", "Imported by:")]
    for field, prefix in c_fields:
        txt = data.get(field, "")
        if txt:
            n, cursor = _count_text_lines_lshape(
                txt, _FONT_NAME, sizes["body"],
                full_width=eff_w, narrow_width=eff_nut_narrow,
                cursor=cursor, nut_boundary=nut_boundary,
                leading=body_leading, bold_prefix=prefix
            )
            bh = n * body_leading
            block_heights.append(bh)
            cursor += unified_gap  # cursor 已被 _count_text_lines_lshape 更新

    n_blocks = len(block_heights)
    content_h = sum(block_heights)

    # 总高度 = A + 所有文本块 + 每块后一个间距 + Net Volume 预留
    total_h = h + content_h + unified_gap * n_blocks + net_reserve

    return total_h, n_blocks


def _calc_font_sizes(data: dict, country_cfg: Optional[dict] = None,
                     tpl: 'TemplateConfig' = None) -> Tuple[dict, float, float]:
    """
    两轮自适应字号计算 + 统一间距计算。

    第一轮：纯纵向二分搜索，找到在 content_w 下能放得下的最大字号。
    第二轮：如果纵向有大量空白（L 形悬崖效应），尝试更大字号 + h_scale 压缩。
            更大字号在 content_w 下会溢出，但在 content_w/h_scale 下可以放下。

    Returns:
        (sizes_dict, h_scale, unified_gap)
    """
    _register_font()
    if tpl is None:
        tpl = _DEFAULT_TPL
    content_w = tpl.content_w
    left_col_w = content_w * tpl.nutrition.left_col_ratio
    available_h = tpl.content_h

    # 法规最小字号（基于 x-height 实际印刷高度）
    x_height_ratio = tpl.x_height_ratio
    min_font_pt = 4.0
    if country_cfg:
        min_mm = country_cfg.get("min_font_height_mm", 1.2)
        min_font_pt = min_mm / (0.3528 * x_height_ratio)

    # ======== 第一轮：纯纵向二分搜索（h_scale=1.0）========
    lo, hi = 0.0, 1.0
    best_scale = 0.0

    for _ in range(20):
        mid = (lo + hi) / 2
        sizes = _sizes_at_scale(mid, tpl)

        # 法规最小字号约束
        if sizes["ingr"] < min_font_pt or sizes["body"] < min_font_pt:
            lo = mid
            continue

        h, _ = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h, tpl=tpl)
        if h <= available_h:
            best_scale = mid
            lo = mid
        else:
            hi = mid

    sizes = _sizes_at_scale(best_scale, tpl)
    h_scale = 1.0

    # ======== 第二轮：如果空白过大，尝试更大字号 + h_scale ========
    est_h, n_blocks = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h, tpl=tpl)
    leftover = available_h - est_h

    # 判断是否值得第二轮搜索（每块平均空白 > 2pt）
    if leftover > n_blocks * 2 and best_scale < 0.95:
        # 在 best_scale 到 hi(=第一轮溢出边界) 之间搜索更大字号
        # 同时搜索 h_scale，使得 estimate(content_w/h_scale) 纵向放得下
        lo2 = best_scale
        hi2 = min(best_scale + 0.3, 1.0)  # 限制搜索范围，避免字号过大
        best_scale2 = best_scale
        best_hs2 = 1.0

        for _ in range(15):
            mid2 = (lo2 + hi2) / 2
            s2 = _sizes_at_scale(mid2, tpl)

            if s2["ingr"] < min_font_pt or s2["body"] < min_font_pt:
                lo2 = mid2
                continue

            # 用原始宽度估算高度，看溢出多少
            h_full, _ = _estimate_content_height(data, s2, content_w, left_col_w, available_h=available_h, tpl=tpl)

            if h_full <= available_h:
                # 不需要压缩就能放下 → 第一轮应该已找到，但确认一下
                best_scale2 = mid2
                best_hs2 = 1.0
                lo2 = mid2
            else:
                # 需要压缩：二分搜索 h_scale
                # 压缩后等效宽度 = content_w / h_scale，每行放更多字，行数减少
                hs_lo, hs_hi = 0.75, 1.0
                found_hs = False
                for _ in range(10):
                    hs_mid = (hs_lo + hs_hi) / 2
                    eff_w = _effective_width(content_w, hs_mid)
                    eff_left = _effective_width(left_col_w, hs_mid)
                    h_comp, _ = _estimate_content_height(data, s2, eff_w, eff_left, available_h=available_h, tpl=tpl)
                    if h_comp <= available_h:
                        found_hs = True
                        hs_lo = hs_mid  # 尝试更大的 h_scale（更少压缩）
                    else:
                        hs_hi = hs_mid

                if found_hs:
                    best_scale2 = mid2
                    best_hs2 = hs_lo
                    lo2 = mid2
                else:
                    hi2 = mid2

        # 使用第二轮结果（如果改进了）
        if best_scale2 > best_scale:
            sizes = _sizes_at_scale(best_scale2, tpl)
            h_scale = best_hs2

    # ======== 第三轮（兜底）：强制法规最小字号 ========
    # 如果经过前两轮，字号仍低于法规最小要求
    # 直接 clamp body/ingr 到 min_font_pt（不通过 scale 联动），避免短内容误压缩
    if sizes["body"] < min_font_pt or sizes["ingr"] < min_font_pt:
        # 直接修改 sizes，不重新走 _sizes_at_scale（保持 title/cn/net 等不变）
        sizes = dict(sizes)  # 拷贝以避免修改缓存
        sizes["body"] = max(sizes["body"], min_font_pt)
        sizes["ingr"] = max(sizes["ingr"], min_font_pt)

        # 先试 h_scale=1.0 是否能放下
        h_test, _ = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h, tpl=tpl)
        if h_test <= available_h:
            # 短内容：不需要任何压缩
            h_scale = 1.0
        else:
            # 需要压缩：二分搜索 h_scale（下限 0.3）
            hs_lo, hs_hi = 0.3, 1.0
            best_hs = 0.3

            for _ in range(15):
                hs_mid = (hs_lo + hs_hi) / 2
                eff_w = _effective_width(content_w, hs_mid)
                eff_left = _effective_width(left_col_w, hs_mid)
                h_comp, _ = _estimate_content_height(data, sizes, eff_w, eff_left, available_h=available_h, tpl=tpl)
                if h_comp <= available_h:
                    best_hs = hs_mid
                    hs_lo = hs_mid
                else:
                    hs_hi = hs_mid

            h_scale = best_hs

    # --------------------------------------------------
    # 统一间距计算
    # --------------------------------------------------
    # 用最终的 h_scale 估算高度
    if h_scale < 1.0:
        eff_w = _effective_width(content_w, h_scale)
        eff_left = _effective_width(left_col_w, h_scale)
        est_h, n_blocks = _estimate_content_height(data, sizes, eff_w, eff_left, available_h=available_h, tpl=tpl)
    else:
        est_h, n_blocks = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h, tpl=tpl)

    # A 区域固定高度（与 _estimate_content_height 保持一致）
    a_h = sizes["title"] * 0.8
    if data.get("product_name_cn"):
        a_h += sizes["title"] * 1.15 + 1  # en name
        a_h += sizes["cn"] * 1.0 + 1      # cn name (最后，leading 缩减)
    else:
        a_h += sizes["title"] * 1.0 + 1   # en name (最后)

    # Net Volume 预留
    net_reserve = (_FIXED_NET * _CAP_H_RATIO) if data.get("net_weight") else 0

    # 从 est_h 反推 content_h = est_h - a_h - n_blocks * 1.0 (binary search gap) - net_reserve
    content_h = est_h - a_h - n_blocks * 1.0 - net_reserve

    # 剩余空间均匀分配为间距
    base_h = a_h + content_h + net_reserve
    unified_gap = (available_h - base_h) / max(n_blocks, 1)

    # 限制范围：最小 1pt，最大不超过 3pt（保持紧凑布局，避免大块空白）
    max_gap = 3.0
    unified_gap = max(1.0, min(unified_gap, max_gap))

    return sizes, h_scale, unified_gap


# --------------------------------------------------
# 后置横向压缩：检测 L 形正文区域最大行宽
# --------------------------------------------------
def _calc_lshape_h_scale(data: dict, sizes: dict, content_w: float) -> float:
    """
    模拟 L 形正文区域所有文本块的逐行排版，
    找出最宽的一行（含粗体前缀），计算需要的横向压缩比。

    Returns:
        h_scale: 1.0 表示不需要压缩，<1.0 表示需要压缩
    """
    _register_font()
    max_w = 0.0

    ingr_fs = sizes["ingr"]
    body_fs = sizes["body"]

    def _scan_max_width(text: str, font_name: str, font_size: float,
                        max_width: float, bold_prefix: str = ""):
        """扫描一段文本，找出最宽行的像素宽度。"""
        nonlocal max_w
        if not text:
            return

        # 第一行含粗体前缀
        if bold_prefix:
            prefix_w = pdfmetrics.stringWidth(bold_prefix, _FONT_NAME_BOLD, font_size)
        else:
            prefix_w = 0

        words = text.split(' ')
        current_line = ""
        first_line = True

        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            w = pdfmetrics.stringWidth(test_line, font_name, font_size)

            # 第一行需要加上前缀宽度来判断是否换行
            line_total = (w + prefix_w) if first_line else w

            if line_total <= max_width:
                current_line = test_line
            else:
                # 记录当前行的实际宽度
                if current_line:
                    cur_w = pdfmetrics.stringWidth(current_line, font_name, font_size)
                    actual_w = (cur_w + prefix_w) if first_line else cur_w
                    max_w = max(max_w, actual_w)
                    first_line = False
                elif first_line and bold_prefix:
                    max_w = max(max_w, prefix_w)
                    first_line = False

                # 检查单词本身是否超宽
                word_w = pdfmetrics.stringWidth(word, font_name, font_size)
                if word_w > max_width:
                    # 字符级断词：记录超宽片段
                    chunk = ""
                    for ch in word:
                        test_chunk = chunk + ch
                        cw = pdfmetrics.stringWidth(test_chunk, font_name, font_size)
                        if cw > max_width and chunk:
                            max_w = max(max_w, pdfmetrics.stringWidth(chunk, font_name, font_size))
                            chunk = ch
                        else:
                            chunk = test_chunk
                    current_line = chunk
                else:
                    current_line = word

        # 最后一行
        if current_line:
            cur_w = pdfmetrics.stringWidth(current_line, font_name, font_size)
            actual_w = (cur_w + prefix_w) if first_line else cur_w
            max_w = max(max_w, actual_w)

    # --- 扫描所有 L 形正文块 ---

    # Ingredients
    ingr = data.get("ingredients", "")
    if ingr:
        _scan_max_width(ingr, _FONT_NAME, ingr_fs, content_w, "Ingredients: ")

    # Contains
    allergens = data.get("allergens", "")
    if allergens:
        _scan_max_width(allergens, _FONT_NAME, body_fs, content_w, "Contains: ")

    # Storage
    storage = data.get("storage", "")
    if storage:
        _scan_max_width(storage, _FONT_NAME, body_fs, content_w)

    # Production date / Best Before
    prod_date = data.get("production_date", "")
    best_before = data.get("best_before", "")
    if prod_date and best_before:
        # 合并行：各段文本宽度之和
        segments = [
            ("Production date: ", _FONT_NAME_BOLD),
            (f"{prod_date} / ", _FONT_NAME),
            ("Best Before: ", _FONT_NAME_BOLD),
            (best_before, _FONT_NAME),
        ]
        line_w = sum(pdfmetrics.stringWidth(s, f, body_fs) for s, f in segments)
        max_w = max(max_w, line_w)
    elif prod_date:
        _scan_max_width(prod_date, _FONT_NAME, body_fs, content_w, "Production date: ")
    elif best_before:
        _scan_max_width(best_before, _FONT_NAME, body_fs, content_w, "Best Before: ")

    # Product of
    origin = data.get("origin", "China")
    origin_w = pdfmetrics.stringWidth(f"Product of {origin}", _FONT_NAME_BOLD, body_fs)
    max_w = max(max_w, origin_w)

    # Manufacturer / Address / Imported by
    for field, prefix in [("manufacturer", "Manufacturer: "),
                          ("manufacturer_address", "Address: "),
                          ("importer_info", "Imported by:")]:
        txt = data.get(field, "")
        if txt:
            _scan_max_width(txt, _FONT_NAME, body_fs, content_w, prefix)

    # 计算压缩比
    if max_w <= content_w or max_w <= 0:
        return 1.0
    return content_w / max_w


# --------------------------------------------------
# Canvas 辅助：分段加粗换行绘制（用于日期行等有多个加粗标签的场景）
# --------------------------------------------------
def _draw_segments_wrapped(c, segments, x: float, y: float, max_width: float,
                           font_size: float, leading: float = 0,
                           h_scale: float = 1.0,
                           nut_top_y: float = 99999, nut_reserve: float = 0,
                           min_y: float = -999) -> float:
    """
    在 Canvas 上绘制多段文本（每段可指定不同字体），自动换行。
    segments: [(text, font_name), ...]
    用于 "Production date: xxx / Best Before: xxx" 等需要多处加粗的场景。
    """
    if not leading:
        leading = font_size * 1.15

    eff_width = _effective_width(max_width, h_scale)

    def _line_w(cur_y):
        w = eff_width
        if nut_reserve > 0 and cur_y <= nut_top_y + 2:
            w -= nut_reserve
        return w

    # 将所有 segments 拆解成 tokens: (word_or_space, font_name)
    tokens = []
    for text, font_name in segments:
        words = text.split(' ')
        for i, w in enumerate(words):
            if i > 0:
                tokens.append((' ', font_name))
            if w:
                tokens.append((w, font_name))

    # 逐 token 排列，超宽换行
    lines = []  # 每行是 [(text, font_name, x_offset), ...]
    cur_line = []
    cur_w = 0
    sim_y = y

    for token_text, token_font in tokens:
        tw = pdfmetrics.stringWidth(token_text, token_font, font_size)
        avail = _line_w(sim_y)

        if cur_w + tw > avail and cur_line:
            # 当前行满，换行
            lines.append(cur_line)
            sim_y -= leading
            cur_line = []
            cur_w = 0
            # 跳过行首空格
            if token_text.strip() == '':
                continue

        cur_line.append((token_text, token_font))
        cur_w += tw

    if cur_line:
        lines.append(cur_line)

    # 绘制（h_scale 由调用方通过 PDF Tz 操作符全局设置，此处不重复设置）
    for line_tokens in lines:
        if y < min_y:
            break
        cx = x
        for token_text, token_font in line_tokens:
            c.setFont(token_font, font_size)
            c.drawString(cx, y, token_text)
            cx += pdfmetrics.stringWidth(token_text, token_font, font_size) * h_scale
        y -= leading

    return y


# --------------------------------------------------
# Canvas 辅助：自动换行绘制文本
# --------------------------------------------------
def _draw_wrapped_text(c, text: str, x: float, y: float, max_width: float,
                       font_name: str, font_size: float, leading: float = 0,
                       bold_prefix: str = "", h_scale: float = 1.0,
                       logo_bottom_y: float = -999, logo_reserve: float = 0,
                       nut_top_y: float = 99999, nut_reserve: float = 0,
                       min_y: float = -999) -> float:
    """
    在 Canvas 上绘制自动换行的文本。
    y 是文本块顶部，直接在 y 位置绘制 baseline，然后按 leading 下移。
    logo_bottom_y / logo_reserve: 当 y > logo_bottom_y 时，可用宽度减少 logo_reserve。
    min_y: 底部硬约束，低于此 y 坐标的行不绘制（防止侵入 Net Volume 等固定区域）。
    返回绘制结束后的 y 坐标 = y - n * leading。
    """
    if not leading:
        leading = font_size * 1.15

    # 横向压缩时等效宽度增大
    eff_width = _effective_width(max_width, h_scale)

    def _line_width(cur_y):
        """当前 y 坐标下的可用宽度（考虑 logo 和营养表避让）。"""
        w = eff_width
        if logo_reserve > 0 and cur_y > logo_bottom_y:
            w -= logo_reserve
        # 使用 <= 并加 2pt 缓冲，仅防止文字紧贴营养表边框时穿过标题区域
        if nut_reserve > 0 and cur_y <= nut_top_y + 2:
            w -= nut_reserve
        return w

    # 如果有粗体前缀，先绘制前缀
    current_x = x
    first_line_w = _line_width(y)
    if bold_prefix:
        c.setFont(_FONT_NAME_BOLD, font_size)
        prefix_w = pdfmetrics.stringWidth(bold_prefix, _FONT_NAME_BOLD, font_size) * h_scale
        c.drawString(current_x, y, bold_prefix)
        current_x += prefix_w
        remaining_first_line = first_line_w - pdfmetrics.stringWidth(bold_prefix, _FONT_NAME_BOLD, font_size)
    else:
        remaining_first_line = first_line_w

    c.setFont(font_name, font_size)

    # 将文本拆分为单词，逐行排列
    words = text.split(' ')
    lines = []
    current_line = ""
    first_line = True
    sim_y = y  # 模拟 y 坐标来决定每行宽度

    for word in words:
        if first_line and not lines:
            avail = remaining_first_line
        else:
            avail = _line_width(sim_y)

        test_line = current_line + (" " if current_line else "") + word
        w = pdfmetrics.stringWidth(test_line, font_name, font_size)

        if w <= avail:
            current_line = test_line
        else:
            # 将已有内容换行
            if current_line:
                lines.append((current_line, first_line and len(lines) == 0))
                first_line = False
                sim_y -= leading
            elif first_line and bold_prefix:
                lines.append(("", True))
                first_line = False
                sim_y -= leading

            # 检查单词本身是否太长
            next_avail = _line_width(sim_y) if not first_line else remaining_first_line
            word_w = pdfmetrics.stringWidth(word, font_name, font_size)
            if word_w > next_avail:
                # 字符级断词：逐字符拆分超宽单词
                chunk = ""
                for ch in word:
                    test_chunk = chunk + ch
                    cw = pdfmetrics.stringWidth(test_chunk, font_name, font_size)
                    if cw > _line_width(sim_y) and chunk:
                        lines.append((chunk, first_line and len(lines) == 0))
                        first_line = False
                        sim_y -= leading
                        chunk = ch
                    else:
                        chunk = test_chunk
                current_line = chunk
            else:
                current_line = word

    if current_line:
        lines.append((current_line, first_line and len(lines) == 0))

    # 绘制各行：直接在 y 绘制，然后按 leading 下移
    draw_y = y
    for i, (line, is_first) in enumerate(lines):
        if draw_y < min_y:
            break  # 底部硬约束：不侵入 Net Volume 等固定区域
        draw_x = current_x if (is_first and bold_prefix) else x
        c.setFont(font_name, font_size)
        c.drawString(draw_x, draw_y, line)
        draw_y -= leading

    return max(draw_y, min_y)


# --------------------------------------------------
# Canvas 辅助：绘制营养信息表格
# --------------------------------------------------
def _draw_nutrition_table(c, data: dict, country_cfg: dict,
                          x: float, y: float, width: float,
                          font_size: float) -> float:
    """在 canvas 上绘制营养信息表格（匹配设计稿格式）。"""
    nutrition = data.get("nutrition") or {}
    nut_title = country_cfg.get("nutrition_title", "Nutrition Information")
    table_data_raw = nutrition.get("table_data") or []
    serving_size = nutrition.get("serving_size", "")

    row_h = font_size + 2  # 行高 = 字号 + 2pt padding
    pad = (row_h - font_size) / 2

    # 列宽分配
    col1_w = width * 0.48
    col2_w = width * 0.30
    col3_w = width * 0.22

    # --- 标题行 "Nutrition Information"（表格内部第一行，跨全列）---
    title_fs = font_size + 2
    title_row_h = title_fs + 4  # 标题行高
    table_top = y
    c.setLineWidth(1.0)

    # 标题文字居中
    cap_h_title = title_fs * _CAP_H_RATIO
    title_text_y = y - title_row_h + (title_row_h - cap_h_title) / 2
    c.setFont(_FONT_NAME_BOLD, title_fs)
    c.drawCentredString(x + width / 2, title_text_y, nut_title)
    y -= title_row_h

    # 标题行底线（较粗）
    c.setLineWidth(1.0)
    c.line(x, y, x + width, y)

    # --- 列标题行：合并 serving size → "Per serving / (15 mL)"，占 2 个行高 ---
    col_hdr_h = row_h * 2
    hdr_font_size = font_size - 0.5
    hdr_cap_h = hdr_font_size * _CAP_H_RATIO
    # "Per serving" 在上半格居中, "(15 mL)" 在下半格居中
    line1_y = y - row_h + (row_h - hdr_cap_h) / 2
    line2_y = y - row_h * 2 + (row_h - hdr_cap_h) / 2
    # "NRV%" 在整个 2 行高区域内居中
    nrv_y = y - col_hdr_h + (col_hdr_h - hdr_cap_h) / 2

    c.setFont(_FONT_NAME, hdr_font_size)
    c.drawCentredString(x + col1_w + col2_w / 2, line1_y, "Per serving")
    if serving_size:
        c.drawCentredString(x + col1_w + col2_w / 2, line2_y, f"({serving_size})")
    c.drawCentredString(x + col1_w + col2_w + col3_w / 2, nrv_y, "NRV%")

    y -= col_hdr_h
    c.setLineWidth(0.5)
    c.line(x, y, x + width, y)

    # --- 数据行 ---
    for item in table_data_raw:
        name = item.get("name", "")
        per_serving = str(item.get("per_serving", ""))
        nrv = str(item.get("nrv", ""))
        is_sub = item.get("is_sub", False)

        # 文字垂直居中：baseline = 单元格底部 + (row_h - cap_height) / 2
        cap_h = font_size * _CAP_H_RATIO
        text_y = y - row_h + (row_h - cap_h) / 2

        name_x = x + 10 if is_sub else x + 2
        name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD

        # 裁剪名称确保不溢出第一列
        c.setFont(name_font, font_size)
        display_name = name
        max_name_w = col1_w - (name_x - x) - 2
        while pdfmetrics.stringWidth(display_name, name_font, font_size) > max_name_w and len(display_name) > 3:
            display_name = display_name[:-1]
        c.drawString(name_x, text_y, display_name)

        c.setFont(_FONT_NAME, font_size)
        c.drawCentredString(x + col1_w + col2_w / 2, text_y, per_serving)
        if nrv:
            c.drawCentredString(x + col1_w + col2_w + col3_w / 2, text_y, nrv)

        y -= row_h
        c.setLineWidth(0.3)
        c.line(x, y, x + width, y)

    table_bottom = y

    # --- 外框（从标题行顶部到表格底部）---
    c.setLineWidth(1.0)
    c.line(x, table_top, x, table_bottom)          # 左
    c.line(x + width, table_top, x + width, table_bottom)  # 右
    c.line(x, table_top, x + width, table_top)      # 上
    c.line(x, table_bottom, x + width, table_bottom)  # 下
    # 列分隔线（从列标题行开始，不穿过标题行）
    col_hdr_top = table_top - title_row_h
    c.setLineWidth(0.5)
    c.line(x + col1_w, col_hdr_top, x + col1_w, table_bottom)
    c.line(x + col1_w + col2_w, col_hdr_top, x + col1_w + col2_w, table_bottom)

    return table_bottom


# ==================================================
# Stacked 布局辅助函数（50×120mm 等纯垂直流标签）
# ==================================================

def _parse_underline_segments(text: str) -> List[tuple]:
    """将含 <u>...</u> 标记的文本解析为 [(text, underline_bool), ...] 段。

    例：'Water, <u>Soybeans</u> (23%)' →
        [('Water, ', False), ('Soybeans', True), (' (23%)', False)]
    """
    segments = []
    pattern = re.compile(r'<u>(.*?)</u>', re.DOTALL)
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            segments.append((text[last_end:m.start()], False))
        segments.append((m.group(1), True))
        last_end = m.end()
    if last_end < len(text):
        segments.append((text[last_end:], False))
    return segments if segments else [(text, False)]


def _draw_text_with_underline(c, text: str, x: float, y: float, max_width: float,
                              font_name: str, font_size: float, leading: float = 0,
                              bold_prefix: str = "", h_scale: float = 1.0,
                              min_y: float = -999) -> float:
    """绘制含 <u>下划线</u> 标记的自动换行文本。

    先解析 <u> 标记，然后逐词排版，对标记为下划线的词额外画线。
    """
    if not leading:
        leading = font_size * 1.15

    eff_width = _effective_width(max_width, h_scale)
    segments = _parse_underline_segments(text)

    # 将所有 segments 拆解成 tokens: (word, font_name, underline)
    tokens = []
    for seg_text, underline in segments:
        words = seg_text.split(' ')
        for i, w in enumerate(words):
            if i > 0:
                tokens.append((' ', font_name, False))
            if w:
                tokens.append((w, font_name, underline))

    # 处理 bold_prefix
    prefix_tokens = []
    if bold_prefix:
        prefix_words = bold_prefix.split(' ')
        for i, w in enumerate(prefix_words):
            if i > 0:
                prefix_tokens.append((' ', _FONT_NAME_BOLD, False))
            if w:
                prefix_tokens.append((w, _FONT_NAME_BOLD, False))
        tokens = prefix_tokens + tokens

    # 按行分 tokens
    lines = []  # 每行是 [(word, font, underline), ...]
    cur_line = []
    cur_w = 0.0

    for token_text, token_font, token_ul in tokens:
        tw = pdfmetrics.stringWidth(token_text, token_font, font_size)
        if cur_w + tw > eff_width and cur_line:
            lines.append(cur_line)
            cur_line = []
            cur_w = 0.0
            if token_text.strip() == '':
                continue
        cur_line.append((token_text, token_font, token_ul))
        cur_w += tw

    if cur_line:
        lines.append(cur_line)

    # 绘制
    draw_y = y
    for line_tokens in lines:
        if draw_y < min_y:
            break
        cx = x
        for token_text, token_font, token_ul in line_tokens:
            c.setFont(token_font, font_size)
            tw = pdfmetrics.stringWidth(token_text, token_font, font_size) * h_scale
            if bold_prefix and token_font == _FONT_NAME_BOLD:
                _start_bold(c, font_size)
                c.drawString(cx, draw_y, token_text)
                _end_bold(c)
            else:
                c.drawString(cx, draw_y, token_text)
            if token_ul:
                # 下划线：baseline 下方 1pt
                ul_y = draw_y - 1
                c.setLineWidth(0.4)
                c.line(cx, ul_y, cx + tw, ul_y)
            cx += tw
        draw_y -= leading

    return max(draw_y, min_y)


def _draw_nutrition_table_eu(c, data: dict, country_cfg: dict,
                             x: float, y: float, width: float,
                             font_size: float) -> float:
    """绘制欧盟格式营养表（2列：名称 | 数值，无 NRV% 列）。"""
    nutrition = data.get("nutrition") or {}
    nut_title = nutrition.get("title", "Nutrition declaration / Voedingswaardevermelding / "
                              "Información nutricional / Nährwertdeklaration / "
                              "Déclaration nutritionnelle")
    per_label = nutrition.get("per_label", "")
    serving_size = nutrition.get("serving_size", "100mL")
    table_data_raw = nutrition.get("table_data") or []

    row_h = font_size + 2
    pad = (row_h - font_size) / 2

    # 列宽：名称 75%，数值 25%
    col1_w = width * 0.75
    col2_w = width * 0.25

    table_top = y
    c.setLineWidth(0.5)

    # --- 标题行（多语，可能很长，需要换行）---
    title_fs = font_size
    title_leading = title_fs * 1.15
    # 估算标题行数
    title_lines = 1
    title_w = pdfmetrics.stringWidth(nut_title, _FONT_NAME_BOLD, title_fs)
    if title_w > width - 4:
        title_lines = int(title_w / (width - 4)) + 1
    title_row_h = title_leading * title_lines + 2

    # 绘制标题（加粗，居中逐行）
    c.setFont(_FONT_NAME_BOLD, title_fs)
    _start_bold(c, title_fs)
    # 简单换行绘制标题
    title_words = nut_title.split(' ')
    t_lines = []
    t_cur = ""
    for tw in title_words:
        test = t_cur + (" " if t_cur else "") + tw
        if pdfmetrics.stringWidth(test, _FONT_NAME_BOLD, title_fs) > width - 4:
            if t_cur:
                t_lines.append(t_cur)
            t_cur = tw
        else:
            t_cur = test
    if t_cur:
        t_lines.append(t_cur)
    ty = y - title_fs * 0.8
    for tl in t_lines:
        c.drawString(x + 2, ty, tl)
        ty -= title_leading
    _end_bold(c)

    title_row_h = (y - ty) + 2
    y -= title_row_h
    c.setLineWidth(1.0)
    c.line(x, y, x + width, y)

    # --- Per serving 行 ---
    per_text = per_label if per_label else f"Nutrition facts per / Voedingswaarde per / Valor nutricional por / Nährwerte pro / Valeur nutritive pour  {serving_size}"
    per_row_h = row_h
    # 估算 per 行是否需要多行
    per_w = pdfmetrics.stringWidth(per_text, _FONT_NAME, font_size)
    per_lines_count = max(1, int(per_w / (width - 4)) + 1)
    per_row_h = row_h * per_lines_count

    c.setFont(_FONT_NAME, font_size)
    # 简单换行
    per_words = per_text.split(' ')
    p_lines = []
    p_cur = ""
    for pw in per_words:
        test = p_cur + (" " if p_cur else "") + pw
        if pdfmetrics.stringWidth(test, _FONT_NAME, font_size) > width - 4:
            if p_cur:
                p_lines.append(p_cur)
            p_cur = pw
        else:
            p_cur = test
    if p_cur:
        p_lines.append(p_cur)

    cap_h = font_size * _CAP_H_RATIO
    py = y - cap_h - 1
    for pl in p_lines:
        c.drawString(x + 2, py, pl)
        py -= font_size * 1.1

    per_row_h = (y - py) + 1
    y -= per_row_h
    c.setLineWidth(0.5)
    c.line(x, y, x + width, y)

    # --- 数据行 ---
    for item in table_data_raw:
        name = item.get("name", "")
        per_serving = str(item.get("per_serving", ""))
        is_sub = item.get("is_sub", False)

        # 估算名称行数
        name_x = x + 8 if is_sub else x + 2
        max_name_w = col1_w - (name_x - x) - 2
        name_w = pdfmetrics.stringWidth(name, _FONT_NAME if is_sub else _FONT_NAME_BOLD, font_size)
        name_lines_n = max(1, int(name_w / max_name_w) + (1 if name_w > max_name_w else 0))
        actual_row_h = row_h * name_lines_n

        # 名称绘制（可能多行）
        name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD
        c.setFont(name_font, font_size)
        if name_lines_n > 1:
            # 换行绘制名称
            n_words = name.split(' ')
            n_lines = []
            n_cur = ""
            for nw in n_words:
                test = n_cur + (" " if n_cur else "") + nw
                if pdfmetrics.stringWidth(test, name_font, font_size) > max_name_w:
                    if n_cur:
                        n_lines.append(n_cur)
                    n_cur = nw
                else:
                    n_cur = test
            if n_cur:
                n_lines.append(n_cur)
            # 多行名称：第一行从顶部 padding 开始
            ny = y - pad - cap_h
            if not is_sub:
                _start_bold(c, font_size)
            for nl in n_lines:
                c.drawString(name_x, ny, nl)
                ny -= font_size * 1.1
            if not is_sub:
                _end_bold(c)
        else:
            # 单行名称：在行高内垂直居中
            text_y = y - pad - cap_h
            if not is_sub:
                _start_bold(c, font_size)
            c.drawString(name_x, text_y, name)
            if not is_sub:
                _end_bold(c)

        # 数值（右对齐，垂直居中）
        c.setFont(_FONT_NAME, font_size)
        text_y = y - pad - cap_h
        c.drawRightString(x + width - 2, text_y, per_serving)

        y -= actual_row_h
        c.setLineWidth(0.3)
        c.line(x, y, x + width, y)

    table_bottom = y

    # --- 外框 ---
    c.setLineWidth(1.0)
    c.line(x, table_top, x, table_bottom)
    c.line(x + width, table_top, x + width, table_bottom)
    c.line(x, table_top, x + width, table_top)
    c.line(x, table_bottom, x + width, table_bottom)
    # 列分隔线（从 per 行到底部）
    col_sep_top = table_top - title_row_h
    c.setLineWidth(0.5)
    c.line(x + col1_w, col_sep_top, x + col1_w, table_bottom)

    return table_bottom


def _draw_eco_icons(c, tpl: 'TemplateConfig', x: float, y: float,
                    width: float) -> float:
    """绘制环保标识图标行。"""
    if not tpl.eco_icons.enabled or not tpl.eco_icons.icons:
        return y

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    icon_h = tpl.eco_icons.height_pt
    icons = tpl.eco_icons.icons
    n = len(icons)
    if n == 0:
        return y

    # 按图标数量平分宽度
    slot_w = width / n
    icon_y = y - icon_h

    for i, icon_name in enumerate(icons):
        icon_path = os.path.join(static_dir, icon_name)
        if os.path.isfile(icon_path):
            try:
                c.drawImage(icon_path, x + i * slot_w, icon_y,
                            width=slot_w, height=icon_h,
                            preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

    return icon_y


def _calc_eu_nutrition_height(data: dict, font_size: float, content_w: float = 130) -> float:
    """估算欧盟格式营养表总高度（精确计算多行名称）。"""
    _register_font()
    nutrition = data.get("nutrition") or {}
    table_data = nutrition.get("table_data") or []
    nut_title = nutrition.get("title", "Nutrition declaration / Voedingswaardevermelding / "
                              "Información nutricional / Nährwertdeklaration / "
                              "Déclaration nutritionnelle")
    per_label = nutrition.get("per_label", "")
    serving_size = nutrition.get("serving_size", "100mL")
    row_h = font_size + 2
    col1_w = content_w * 0.75

    # 标题行（精确计算）
    title_w = pdfmetrics.stringWidth(nut_title, _FONT_NAME_BOLD, font_size)
    title_lines = max(1, int(title_w / (content_w - 4)) + (1 if title_w > content_w - 4 else 0))
    h = title_lines * (font_size * 1.15) + font_size * 0.8 + 2

    # Per 行
    per_text = per_label if per_label else f"Nutrition facts per {serving_size}"
    per_w = pdfmetrics.stringWidth(per_text, _FONT_NAME, font_size)
    per_lines = max(1, int(per_w / (content_w - 4)) + (1 if per_w > content_w - 4 else 0))
    h += per_lines * (font_size * 1.1) + font_size * 0.8 + 1

    # 数据行（精确计算每行名称的行数）
    for item in table_data:
        name = item.get("name", "")
        is_sub = item.get("is_sub", False)
        name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD
        name_x_offset = 8 if is_sub else 2
        max_name_w = col1_w - name_x_offset - 2
        name_w = pdfmetrics.stringWidth(name, name_font, font_size)
        name_lines = max(1, int(name_w / max_name_w) + (1 if name_w > max_name_w else 0))
        h += row_h * name_lines

    # 安全余量（防止估算偏低导致与环保标识重叠）
    h += 4
    return h


def _calc_stacked_fixed_heights(data: dict, tpl: 'TemplateConfig',
                                 content_w: float, nut_fs: float,
                                 title_fs: float, cn_fs: float,
                                 net_fs: float) -> Tuple[float, float]:
    """精确计算 stacked 布局的顶部和底部固定区域高度。

    Returns: (fixed_top_h, fixed_bottom_h)
    """
    _register_font()

    # --- 顶部固定：标题区（word-wrap 估算）---
    en_name = data.get("product_name_en", "PRODUCT NAME")

    # 解析：\n\n 前面是多语品名，后面是中文品名
    if '\n\n' in en_name:
        parts = en_name.split('\n\n', 1)
        title_text = parts[0].replace('\n', ' ').strip()
        cn_inline = parts[1].strip()
    else:
        title_text = en_name.replace('\n', ' ').strip()
        cn_inline = ""

    a_gap = 1
    logo_reserve = (tpl.logo.width_pt + tpl.logo.padding_pt) if tpl.logo.enabled else 0
    title_avail_w = content_w - logo_reserve

    # 固定 4 行标题（与绘制代码 _split_into_n_lines 一致）
    TARGET_LINES = 4
    title_leading = title_fs * 1.15 + a_gap

    fixed_top = title_fs * 0.8  # 顶部 padding
    fixed_top += TARGET_LINES * title_leading
    cn_name = data.get("product_name_cn", "")
    if cn_name and cn_name not in en_name and cn_name not in title_text:
        fixed_top += cn_fs * 1.0 + a_gap

    # --- 底部固定：营养表 + 环保标识（Net Volume 在文字流区域内）---
    fixed_bottom = 0.0

    # 环保标识
    if tpl.eco_icons.enabled:
        fixed_bottom += tpl.eco_icons.height_pt

    # 营养表
    fixed_bottom += _calc_eu_nutrition_height(data, nut_fs, content_w)

    return fixed_top, fixed_bottom


def _estimate_text_only_height(data: dict, sizes: dict,
                               content_w: float) -> Tuple[float, int]:
    """只估算中间自适应文字流的高度（不含固定区域）。

    Returns: (text_height, n_blocks)
    """
    _register_font()
    h = 0.0
    n_blocks = 0
    body_leading = sizes["body"] * 1.15

    # Ingredients
    ingr = data.get("ingredients", "")
    if ingr:
        clean = re.sub(r'</?u>', '', ingr)
        n = _count_text_lines(clean, _FONT_NAME, sizes["ingr"], content_w)
        h += n * sizes["ingr"] * 1.15
        n_blocks += 1

    # Storage
    storage = data.get("storage", "")
    if storage:
        n = _count_text_lines(storage, _FONT_NAME, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Usage
    usage = data.get("usage", "")
    if usage:
        n = _count_text_lines(usage, _FONT_NAME, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Best before
    best_before = data.get("best_before", "")
    if best_before:
        n = _count_text_lines(best_before, _FONT_NAME, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Product of
    product_of = data.get("product_of", "")
    if product_of:
        n = _count_text_lines(product_of, _FONT_NAME_BOLD, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Importer
    imp = data.get("importer_info", "")
    if imp:
        imp_full = "Importer/Importeur/Importador/Importeur/Importateur: " + imp
        n = _count_text_lines(imp_full, _FONT_NAME, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Importer address
    imp_addr = data.get("importer_address", "")
    if imp_addr:
        n = _count_text_lines(imp_addr, _FONT_NAME, sizes["body"], content_w)
        h += n * body_leading
        n_blocks += 1

    # Net Volume（在文字流区域内，和 importer 地址并排）
    if data.get("net_weight"):
        h += sizes["net"] * 1.2
        n_blocks += 1

    return h, n_blocks


def _calc_stacked_font_sizes(data: dict, country_cfg: Optional[dict] = None,
                             tpl: 'TemplateConfig' = None) -> Tuple[dict, float, float]:
    """stacked 布局自适应字号计算。

    核心逻辑：
    1. 精确算出固定区域（标题 + 底部）高度
    2. available_h = content_h - fixed_top - fixed_bottom
    3. 二分搜索使 text_only_height <= available_h
    """
    _register_font()
    if tpl is None:
        tpl = _DEFAULT_TPL
    content_w = tpl.content_w
    total_h = tpl.content_h

    # 法规最小字号
    x_height_ratio = tpl.x_height_ratio
    min_font_pt = 3.0
    if country_cfg:
        min_mm = country_cfg.get("min_font_height_mm", 1.2)
        min_font_pt = min_mm / (0.3528 * x_height_ratio)

    # 二分搜索最佳 scale
    lo, hi = 0.0, 1.0
    best_scale = 0.0

    for _ in range(25):
        mid = (lo + hi) / 2
        sizes = _sizes_at_scale(mid, tpl)
        if sizes["ingr"] < min_font_pt or sizes["body"] < min_font_pt:
            lo = mid
            continue

        fixed_top, fixed_bottom = _calc_stacked_fixed_heights(
            data, tpl, content_w,
            sizes["nut"], sizes["title"], sizes["cn"], sizes["net"]
        )
        available_h = total_h - fixed_top - fixed_bottom
        text_h, _ = _estimate_text_only_height(data, sizes, content_w)

        if text_h <= available_h:
            best_scale = mid
            lo = mid
        else:
            hi = mid

    sizes = _sizes_at_scale(best_scale, tpl)
    h_scale = 1.0

    # 若最小字号仍溢出 → 搜索 h_scale 缩放
    sizes = dict(sizes)
    sizes["body"] = max(sizes["body"], min_font_pt)
    sizes["ingr"] = max(sizes["ingr"], min_font_pt)

    fixed_top, fixed_bottom = _calc_stacked_fixed_heights(
        data, tpl, content_w,
        sizes["nut"], sizes["title"], sizes["cn"], sizes["net"]
    )
    available_h = total_h - fixed_top - fixed_bottom
    text_h, _ = _estimate_text_only_height(data, sizes, content_w)

    if text_h > available_h:
        hs_lo, hs_hi = 0.3, 1.0
        best_hs = 0.3
        for _ in range(15):
            hs_mid = (hs_lo + hs_hi) / 2
            eff_w = _effective_width(content_w, hs_mid)
            h_comp, _ = _estimate_text_only_height(data, sizes, eff_w)
            if h_comp <= available_h:
                best_hs = hs_mid
                hs_lo = hs_mid
            else:
                hs_hi = hs_mid
        h_scale = best_hs

    # 计算均匀间距
    eff_w = _effective_width(content_w, h_scale) if h_scale < 1.0 else content_w
    text_h_final, n_blocks = _estimate_text_only_height(data, sizes, eff_w)
    leftover = available_h - text_h_final
    unified_gap = max(1.0, min(leftover / max(n_blocks, 1), 3.0)) if n_blocks > 0 else 1.0

    return sizes, h_scale, unified_gap


def _generate_stacked_pdf(data: dict, country_cfg: dict,
                          tpl: 'TemplateConfig') -> bytes:
    """生成纯垂直流布局的标签 PDF（50×120mm 等）。

    绘制顺序：底部固定 → 顶部固定 → 中间自适应填充。
    """
    _register_font()
    country_cfg = country_cfg or {}
    sizes, h_scale, unified_gap = _calc_stacked_font_sizes(data, country_cfg, tpl)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(tpl.label_w, tpl.label_h))

    left = tpl.margin
    right = tpl.label_w - tpl.margin
    top = tpl.label_h - tpl.margin
    bottom = tpl.margin
    content_w = right - left
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    # ==================================================================
    # 第 1 步：绘制底部固定区域（从底部往上）
    # ==================================================================

    # 1a) 环保标识（最底部）
    eco_top_y = bottom
    if tpl.eco_icons.enabled:
        _draw_eco_icons(c, tpl, left, bottom + tpl.eco_icons.height_pt, content_w)
        eco_top_y = bottom + tpl.eco_icons.height_pt

    # 1b) 营养表（环保标识上方）
    nut_h = _calc_eu_nutrition_height(data, sizes["nut"], content_w)
    nut_top_y = eco_top_y + nut_h
    _draw_nutrition_table_eu(c, data, country_cfg, left, nut_top_y, content_w, sizes["nut"])

    # min_y = 营养表顶部 + 安全间距
    min_y = nut_top_y + 2

    # ==================================================================
    # 第 2 步：绘制顶部固定区域（标题 + Logo）
    # ==================================================================
    y = top - sizes["title"] * 0.8

    logo_path = data.get("brand_logo", "")
    if not logo_path or not os.path.isfile(logo_path):
        logo_path = os.path.join(static_dir, "logo_placeholder.png")

    _logo_w = tpl.logo.width_pt if tpl.logo.enabled else 0
    _logo_h = tpl.logo.height_pt if tpl.logo.enabled else 0
    _logo_pad = tpl.logo.padding_pt if tpl.logo.enabled else 0
    logo_reserve = _logo_w + _logo_pad

    if tpl.logo.enabled and os.path.isfile(logo_path):
        try:
            c.drawImage(logo_path, right - _logo_w, top - _logo_h,
                        width=_logo_w, height=_logo_h,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    a_gap = 1
    en_name = data.get("product_name_en", "PRODUCT NAME")
    cn_name = data.get("product_name_cn", "")

    # 解析：\n\n 前面是多语品名，后面是中文品名
    if '\n\n' in en_name:
        parts = en_name.split('\n\n', 1)
        title_text = parts[0].replace('\n', ' ').strip()
        cn_inline = parts[1].strip()
    else:
        title_text = en_name.replace('\n', ' ').strip()
        cn_inline = ""

    title_avail_w = content_w - logo_reserve
    title_fs = sizes["title"]
    title_leading = title_fs * 1.15 + a_gap

    # Logo 影响的行数
    logo_lines = max(1, int(_logo_h / title_leading) + 1) if _logo_h > 0 else 0

    # 将标题文字固定分成 4 行
    TARGET_LINES = 4
    full_title = title_text + ("  " + cn_inline if cn_inline else "")
    title_words = full_title.split()
    total_words = len(title_words)

    # 贪婪分行：尽量让每行长度均匀（考虑前几行窄宽避让 logo）
    def _split_into_n_lines(words, n, narrow_w, full_w, n_narrow, fs):
        """将 words 分成恰好 n 行,前 n_narrow 行用 narrow_w,后面用 full_w"""
        lines = []
        wi = 0
        for li in range(n):
            avail = narrow_w if li < n_narrow else full_w
            remaining_lines = n - li
            remaining_words = len(words) - wi
            words_per_line = max(1, remaining_words // remaining_lines)
            
            # 取 words_per_line 个词作为这一行
            line_words = words[wi:wi + words_per_line]
            wi += words_per_line
            
            # 如果这行文字宽度还有空间,继续贪婪加词（但要给后面行留词）
            while wi < len(words) and (len(words) - wi) > (n - li - 1):
                test = " ".join(line_words + [words[wi]])
                test_w = pdfmetrics.stringWidth(test, _FONT_NAME_BOLD, fs)
                if test_w <= avail:
                    line_words.append(words[wi])
                    wi += 1
                else:
                    break
            
            lines.append(" ".join(line_words))
        
        # 如果还有剩余词,追加到最后一行
        if wi < len(words):
            remaining = " ".join(words[wi:])
            lines[-1] = lines[-1] + " " + remaining
        
        return lines

    wrapped_lines = _split_into_n_lines(
        title_words, TARGET_LINES,
        title_avail_w, content_w, logo_lines, title_fs
    )

    # 计算统一 Tz（取所有行中最需要压缩的比率）
    title_tz = 100
    for li, tl in enumerate(wrapped_lines):
        line_w = pdfmetrics.stringWidth(tl, _FONT_NAME_BOLD, title_fs)
        avail = title_avail_w if li < logo_lines else content_w
        if line_w > avail:
            tz = max(50, int(avail / line_w * 100))
            title_tz = min(title_tz, tz)

    c.setFont(_FONT_NAME_BOLD, title_fs)
    _start_bold(c, title_fs)
    if title_tz < 100:
        c._code.append(f'{title_tz} Tz')
    for tl in wrapped_lines:
        c.drawString(left, y, tl)
        y -= title_leading
    if title_tz < 100:
        c._code.append('100 Tz')
    _end_bold(c)

    if cn_name and cn_name not in en_name and cn_name not in title_text:
        c.setFont(_FONT_NAME_BOLD, sizes["cn"])
        _start_bold(c, sizes["cn"])
        c.drawString(left, y, cn_name)
        _end_bold(c)
        y -= sizes["cn"] * 1.0 + a_gap

    # ==================================================================
    # 第 3 步：中间自适应文字流（在 y 和 min_y 之间）
    # ==================================================================
    if h_scale < 1.0:
        tz_pct = int(h_scale * 100)
        c._code.append(f'{tz_pct} Tz')

    # Ingredients（含过敏原下划线）
    ingredients = data.get("ingredients", "")
    if ingredients:
        y = _draw_text_with_underline(
            c, ingredients, left, y,
            content_w, _FONT_NAME, sizes["ingr"],
            h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap

    # Storage
    storage = data.get("storage", "")
    if storage:
        y = _draw_wrapped_text(
            c, storage, left, y,
            content_w, _FONT_NAME, sizes["body"],
            h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap

    # Usage
    usage = data.get("usage", "")
    if usage:
        y = _draw_wrapped_text(
            c, usage, left, y,
            content_w, _FONT_NAME, sizes["body"],
            h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap

    # Best before（加粗前缀到冒号为止）
    best_before = data.get("best_before", "")
    if best_before:
        if ':' in best_before:
            bb_prefix, bb_body = best_before.split(':', 1)
            y = _draw_wrapped_text(
                c, bb_body.strip(), left, y,
                content_w, _FONT_NAME, sizes["body"],
                bold_prefix=bb_prefix.strip() + ': ',
                h_scale=h_scale, min_y=min_y
            )
        else:
            y = _draw_wrapped_text(
                c, best_before, left, y,
                content_w, _FONT_NAME_BOLD, sizes["body"],
                h_scale=h_scale, min_y=min_y
            )
        y -= unified_gap

    # Product of
    product_of = data.get("product_of", "")
    if product_of:
        y = _draw_wrapped_text(
            c, product_of, left, y,
            content_w, _FONT_NAME_BOLD, sizes["body"],
            h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap
    else:
        origin = data.get("origin", "China")
        if y >= min_y:
            _draw_bold_string(c, left, y, f"Product of {origin}", sizes["body"])
        y -= sizes["body"] * 1.15 + unified_gap

    # Importer
    imp = data.get("importer_info", "")
    if imp:
        imp_prefix = "Importer/Importeur/Importador/Importeur/Importateur: "
        y = _draw_wrapped_text(
            c, imp, left, y,
            content_w, _FONT_NAME, sizes["body"],
            bold_prefix=imp_prefix, h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap

    # Importer address + Net Volume（地址左对齐，500mL 右对齐同行）
    imp_addr = data.get("importer_address", "")
    net_weight = data.get("net_weight", "")
    if imp_addr:
        y = _draw_wrapped_text(
            c, imp_addr, left, y,
            content_w, _FONT_NAME, sizes["body"],
            h_scale=h_scale, min_y=min_y
        )
        y -= unified_gap

    # Net Volume（大号文字，右对齐，在文字流区域内）
    if net_weight and y >= min_y:
        actual_net_fs = sizes["net"]
        text_w = pdfmetrics.stringWidth(net_weight, _FONT_NAME_BOLD, actual_net_fs)
        net_x = right - text_w
        if text_w > content_w:
            net_tz = max(50, int(content_w / text_w * 100))
            t = c.beginText(left, y - actual_net_fs)
            t.setFont(_FONT_NAME_BOLD, actual_net_fs)
            t._code.append(f'{net_tz} Tz')
            t.textOut(net_weight)
            t._code.append('100 Tz')
            c.drawText(t)
        else:
            _draw_bold_string(c, net_x, y - actual_net_fs, net_weight, actual_net_fs)
        y -= actual_net_fs * 1.2

    if h_scale < 1.0:
        c._code.append('100 Tz')

    c.save()
    return buf.getvalue()


# --------------------------------------------------
# 生成 PDF 字节
# --------------------------------------------------
def generate_label_pdf(data: dict, country_cfg: Optional[dict] = None,
                       tpl: 'TemplateConfig' = None) -> bytes:
    """
    根据产品数据和模板配置生成标签 PDF。
    根据模板布局类型分发到不同渲染路径。
    """
    _register_font()
    if tpl is None:
        tpl = _DEFAULT_TPL
    country_cfg = country_cfg or {}

    # 布局分发
    if tpl.layout.type == "stacked":
        return _generate_stacked_pdf(data, country_cfg, tpl)

    # ---- 以下为原 l_shape 逻辑 ----
    sizes, h_scale, unified_gap = _calc_font_sizes(data, country_cfg, tpl=tpl)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(tpl.label_w, tpl.label_h))

    # 内容区域边界
    left = tpl.margin
    right = tpl.label_w - tpl.margin
    top = tpl.label_h - tpl.margin
    bottom = tpl.margin
    content_w = right - left

    # 首行 baseline 下移 ascent，让字符顶部不超出 margin
    y = top - sizes["title"] * 0.8

    # 后置横向压缩：仅对 L 形正文区域生效（由两轮搜索确定）
    # h_scale 已由 _calc_font_sizes 两轮搜索确定，不再此处计算
    # h_scale_post = _calc_lshape_h_scale(data, sizes, content_w)  # 备用

    # ============================================================
    # 区域 A：产品英文名 + 中文名（左）、Logo（右上）
    # ============================================================
    logo_path = data.get("brand_logo", "")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if not logo_path or not os.path.isfile(logo_path):
        logo_path = os.path.join(static_dir, "logo_placeholder.png")

    # Logo 专区：顶部与 product_name_en 齐平
    _logo_w = tpl.logo.width_pt if tpl.logo.enabled else 0
    _logo_h = tpl.logo.height_pt if tpl.logo.enabled else 0
    _logo_pad = tpl.logo.padding_pt if tpl.logo.enabled else 0
    logo_bottom_y = top - _logo_h - _logo_pad
    logo_reserve = _logo_w + _logo_pad

    if tpl.logo.enabled and os.path.isfile(logo_path):
        try:
            c.drawImage(logo_path, right - _logo_w, top - _logo_h,
                        width=_logo_w, height=_logo_h,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    # 英文名（粗体大号）+ 中文名，超宽时统一横向压缩
    a_gap = 1  # A 区域内部最小间距 (pt)

    en_name = data.get("product_name_en", "PRODUCT NAME")
    cn_name = data.get("product_name_cn", "")

    # 标题可用宽度 = 内容区宽度 - logo 避让
    title_avail_w = content_w - logo_reserve
    en_w = pdfmetrics.stringWidth(en_name, _FONT_NAME_BOLD, sizes["title"])
    cn_w = pdfmetrics.stringWidth(cn_name, _FONT_NAME_BOLD, sizes["cn"]) if cn_name else 0
    max_title_w = max(en_w, cn_w)
    # 统一 Tz 压缩比（中英文一致）
    title_tz = min(100, int(title_avail_w / max_title_w * 100)) if max_title_w > title_avail_w else 100

    if title_tz < 100:
        c._code.append(f'{title_tz} Tz')

    c.setFont(_FONT_NAME_BOLD, sizes["title"])
    c.drawString(left, y, en_name)

    # 中文名
    if cn_name:
        y -= sizes["title"] * 1.15 + a_gap  # en name (非最后)
        c.setFont(_FONT_NAME_BOLD, sizes["cn"])
        c.drawString(left, y, cn_name)
        y -= sizes["cn"] * 1.0 + a_gap      # cn name (最后，leading 缩减)
    else:
        y -= sizes["title"] * 1.0 + a_gap   # en name (最后，leading 缩减)

    if title_tz < 100:
        c._code.append('100 Tz')  # 标题区结束，恢复正常

    # ============================================================
    # 区域 B+C：统一间距信息流
    # ============================================================

    # ---- B 区域：全宽文字信息 ----

    # 提前计算营养表边界，供 B+C 区域所有文字块避让
    right_col_ratio = tpl.nutrition.right_col_ratio
    col_gap = tpl.nutrition.col_gap_pt
    left_col_w = content_w * (1 - right_col_ratio)
    right_col_w = content_w * right_col_ratio
    right_col_x = left + left_col_w + col_gap
    actual_right_w = right_col_w - col_gap
    body_leading = sizes["body"] * 1.15

    nut_total_h = _calc_nutrition_height(data, sizes, tpl)
    nut_top_y = bottom + nut_total_h  # 营养表顶部 y 坐标
    nut_reserve = _effective_width(right_col_w, h_scale)  # 营养表避让宽度

    # 在 L 形正文区域开始前设置横向压缩
    if h_scale < 1.0:
        tz_pct = int(h_scale * 100)
        c._code.append(f'{tz_pct} Tz')

    # Ingredients
    ingredients = data.get("ingredients", "")
    if ingredients:
        y = _draw_wrapped_text(
            c, ingredients, left, y,
            content_w, _FONT_NAME, sizes["ingr"],
            bold_prefix="Ingredients: ", h_scale=h_scale,
            logo_bottom_y=logo_bottom_y, logo_reserve=logo_reserve,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve
        )
        y -= unified_gap

    # Contains
    allergens = data.get("allergens", "")
    if allergens:
        y = _draw_wrapped_text(
            c, allergens, left, y,
            content_w, _FONT_NAME, sizes["body"],
            bold_prefix="Contains: ", h_scale=h_scale,
            logo_bottom_y=logo_bottom_y, logo_reserve=logo_reserve,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve
        )
        y -= unified_gap

    # Storage
    storage = data.get("storage", "")
    if storage:
        y = _draw_wrapped_text(
            c, storage, left, y,
            content_w, _FONT_NAME, sizes["body"],
            h_scale=h_scale,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve
        )
        y -= unified_gap

    # Production date / Best Before（单行，双标签加粗）
    prod_date = data.get("production_date", "")
    best_before = data.get("best_before", "")
    if prod_date or best_before:
        if prod_date and best_before:
            # 分段渲染：Production date: 和 Best Before: 都加粗
            segments = [
                ("Production date: ", _FONT_NAME_BOLD),
                (f"{prod_date} / ", _FONT_NAME),
                ("Best Before: ", _FONT_NAME_BOLD),
                (best_before, _FONT_NAME),
            ]
            y = _draw_segments_wrapped(
                c, segments, left, y,
                content_w, sizes["body"],
                h_scale=h_scale,
                nut_top_y=nut_top_y, nut_reserve=nut_reserve
            )
        elif prod_date:
            y = _draw_wrapped_text(
                c, prod_date, left, y,
                content_w, _FONT_NAME, sizes["body"],
                bold_prefix="Production date: ", h_scale=h_scale,
                nut_top_y=nut_top_y, nut_reserve=nut_reserve
            )
        else:
            y = _draw_wrapped_text(
                c, best_before, left, y,
                content_w, _FONT_NAME, sizes["body"],
                bold_prefix="Best Before: ", h_scale=h_scale,
                nut_top_y=nut_top_y, nut_reserve=nut_reserve
            )
        y -= unified_gap

    # ============================================================
    # 区域 C：左栏（厂商信息 + Net Volume）+ 右栏（营养表）
    # ============================================================
    net_weight = data.get("net_weight", "")

    # Net Volume 预留高度（baseline 在 bottom，文字向上延伸 cap height）
    net_reserve = tpl.net_reserve(bool(net_weight))

    # 营养表边界（已在 B 区域前计算 nut_top_y, nut_reserve, right_col_x, actual_right_w 等）

    # ------------------------------------------------------------------
    # C 左栏间距计算
    # ------------------------------------------------------------------
    eff_w = _effective_width(content_w, h_scale)
    eff_nut_narrow = eff_w - nut_reserve

    # 从顶部坐标换算 cursor（估算坐标系：从顶部往下，0=标签顶部内边距）
    # y 是 PDF 坐标（底部原点），cursor 是从顶部往下的距离
    cursor_c = (tpl.label_h - tpl.margin) - y  # 当前已用高度
    nut_boundary_c = (tpl.label_h - 2 * tpl.margin) - nut_total_h  # nut_boundary 从顶部算

    c_block_heights = []
    # Product of
    c_block_heights.append(body_leading)
    cursor_c += body_leading
    # Manufacturer
    mfr = data.get("manufacturer", "")
    if mfr:
        n, cursor_c = _count_text_lines_lshape(
            mfr, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor_c, nut_boundary=nut_boundary_c,
            leading=body_leading, bold_prefix="Manufacturer: "
        )
        c_block_heights.append(n * body_leading)
    # Address
    addr = data.get("manufacturer_address", "")
    if addr:
        n, cursor_c = _count_text_lines_lshape(
            addr, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor_c, nut_boundary=nut_boundary_c,
            leading=body_leading, bold_prefix="Address: "
        )
        c_block_heights.append(n * body_leading)
    # Imported by
    imp = data.get("importer_info", "")
    if imp:
        n, cursor_c = _count_text_lines_lshape(
            imp, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor_c, nut_boundary=nut_boundary_c,
            leading=body_leading, bold_prefix="Imported by:"
        )
        c_block_heights.append(n * body_leading)

    c_content_h = sum(c_block_heights)
    n_c_gaps = len(c_block_heights)  # 间隔数 = 块数（每个块后面一个间隔，最后一个间隔在 Net Volume 前）

    # C 区域可用高度 = 当前 y 到 Net Volume baseline (bottom) 的距离 - Net Volume cap height
    c_available = y - bottom - net_reserve
    c_left_gap = max(1.0, (c_available - c_content_h) / max(n_c_gaps, 1))
    # 上限：不超过 body leading 的 1.5 倍，防止过于稀疏
    c_left_gap = min(c_left_gap, body_leading * 1.5)

    # ------------------------------------------------------------------
    # 开始绘制 C 左栏
    # ------------------------------------------------------------------
    # 底部硬约束：C 块文字不可侵入 Net Volume 区域
    c_bottom_limit = bottom + net_reserve

    # --- Product of China ---
    origin = data.get("origin", "China")
    c.setFont(_FONT_NAME_BOLD, sizes["body"])
    c.drawString(left, y, f"Product of {origin}")
    y -= body_leading + c_left_gap

    # --- Manufacturer（进入营养表避让区域，自动缩宽）---
    if mfr and y > c_bottom_limit:
        y = _draw_wrapped_text(
            c, mfr, left, y,
            content_w, _FONT_NAME, sizes["body"],
            bold_prefix="Manufacturer: ", h_scale=h_scale,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve,
            min_y=c_bottom_limit
        )
        y = max(y, c_bottom_limit)
        y -= c_left_gap

    # --- Address ---
    if addr and y > c_bottom_limit:
        y = _draw_wrapped_text(
            c, addr, left, y,
            content_w, _FONT_NAME, sizes["body"],
            bold_prefix="Address: ", h_scale=h_scale,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve,
            min_y=c_bottom_limit
        )
        y = max(y, c_bottom_limit)
        y -= c_left_gap

    # --- Imported by ---
    if imp and y > c_bottom_limit:
        y = _draw_wrapped_text(
            c, imp, left, y,
            content_w, _FONT_NAME, sizes["body"],
            bold_prefix="Imported by:", h_scale=h_scale,
            nut_top_y=nut_top_y, nut_reserve=nut_reserve,
            min_y=c_bottom_limit
        )

    # L 形正文区域结束，重置横向压缩
    if h_scale < 1.0:
        c._code.append('100 Tz')

    # --- Net Volume：底部与营养表齐平（baseline 贴 MARGIN，保证 2mm 出血）---
    if net_weight:
        actual_net_fs = sizes["net"]
        text_w = pdfmetrics.stringWidth(net_weight, _FONT_NAME_BOLD, actual_net_fs)
        net_tz = min(100, int(left_col_w / text_w * 100))
        net_y = bottom
        t = c.beginText(left, net_y)
        t.setFont(_FONT_NAME_BOLD, actual_net_fs)
        t._code.append(f'{net_tz} Tz')
        t.textOut(net_weight)
        t._code.append('100 Tz')
        c.drawText(t)

    # --- 营养表：固定在右下角（底部与 Net Volume 齐平）---
    nut_start_y = bottom + nut_total_h
    _draw_nutrition_table(
        c, data, country_cfg,
        right_col_x, nut_start_y,
        actual_right_w,
        sizes["nut"]
    )

    # HALAL 标识
    if data.get("is_halal"):
        c.setFont(_FONT_NAME_BOLD, 5)
        c.drawString(left, bottom, "☪ HALAL")

    c.save()
    return buf.getvalue()


# --------------------------------------------------
# PDF → PNG 预览（PyMuPDF）
# --------------------------------------------------
def pdf_to_png_base64(pdf_bytes: bytes, dpi: int = 216) -> str:
    """将 PDF 第一页渲染为 PNG base64 字符串。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png_bytes).decode()


def generate_label_preview_html(data: dict, country_cfg: Optional[dict] = None,
                                tpl: 'TemplateConfig' = None) -> Tuple[str, bytes]:
    """生成标签预览 HTML 和 PDF 字节。"""
    pdf_bytes = generate_label_pdf(data, country_cfg, tpl=tpl)
    png_b64 = pdf_to_png_base64(pdf_bytes)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
body {{ margin:0; padding:0; background:#4a4a4a; display:flex; flex-direction:column; align-items:center; min-height:100vh; }}
img {{ max-width:100%; background:white; box-shadow:0 4px 16px rgba(0,0,0,0.5); margin:16px; }}
</style></head><body>
<img src="data:image/png;base64,{png_b64}" alt="Label Preview" />
</body></html>"""

    return html, pdf_bytes
