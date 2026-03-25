"""
服务端标签 PDF 生成器（Canvas 版）

使用 reportlab Canvas 直接绘制 70mm×69mm 合规标签 PDF，
PyMuPDF 渲染为 PNG 预览图。
布局参考设计师提供的规范图。
"""

import io
import base64
import os
from typing import Optional, Tuple

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import black

import fitz  # PyMuPDF

# --------------------------------------------------
# 常量
# --------------------------------------------------
LABEL_W = 70 * mm    # 裁切尺寸 70mm ≈ 198.4pt
LABEL_H = 69 * mm    # 裁切尺寸 69mm ≈ 195.6pt
MARGIN = 2 * mm       # 出血位 2mm ≈ 5.67pt  → 内容安全区域 66×65mm

# Logo 专区
LOGO_W = 40           # logo 宽度 (pt)
LOGO_H = 5.4 * mm     # logo 高度 5.4mm（设计固定值）
LOGO_PAD = 2          # logo 与文字的间距 (pt)

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
# 最大字号（scale=1.0）和最小字号（scale=0.0）的映射
# 70×69mm 标签固定尺寸（设计师标注的是视觉字高 cap height，需要换算）
# 换算公式: font_pt = visual_mm / (CAP_H_RATIO * 0.3528)
_CAP_H_RATIO = 0.735                 # AliPuHuiTi 字体 cap height 比例（实测反推）
_X_HEIGHT_RATIO = 0.54                # AliPuHuiTi 字体 x-height 比例（sxHeight=540, UPM=1000）
_FIXED_TITLE = 8.0                               # 英文标题 8.0pt
_FIXED_CN    = 9.8                               # 中文标题 9.8pt
_FIXED_NET   = 21                                # Net Volume 固定 21pt
_FIXED_NUT_ROW_H = 2.8 * mm                     # 营养表行高 2.8mm → 7.94pt（直接物理尺寸）
_FIXED_NUT   = _FIXED_NUT_ROW_H - 2             # 营养表字号 = 行高 - 2pt padding → 5.94pt

_SIZE_MAX = {"title": _FIXED_TITLE, "cn": _FIXED_CN, "body": 16, "ingr": 16, "nut": _FIXED_NUT, "net": _FIXED_NET}
_SIZE_MIN = {"title": _FIXED_TITLE, "cn": _FIXED_CN, "body": 4,  "ingr": 4,  "nut": _FIXED_NUT, "net": _FIXED_NET}


def _sizes_at_scale(scale: float) -> dict:
    """按 scale (0.0~1.0) 在最小/最大之间线性插值得到一组字号。"""
    return {
        k: _SIZE_MIN[k] + (_SIZE_MAX[k] - _SIZE_MIN[k]) * scale
        for k in _SIZE_MAX
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
                              leading: float, bold_prefix: str = "",
                              logo_boundary: float = 0,
                              logo_reserve_w: float = 0) -> Tuple[int, float]:
    """估算 L 形区域中文本行数，逐行追踪 cursor 位置切换宽度。

    三段式宽度模型（与渲染器 _draw_wrapped_text._line_width 对称）：
    - cursor < logo_boundary 时：full_width - logo_reserve_w（Logo 避让）
    - logo_boundary <= cursor < nut_boundary 时：full_width（全宽）
    - cursor >= nut_boundary 时：narrow_width（营养表避让）

    每输出一行，cursor 下移 leading。

    Returns:
        (行数, 更新后的 cursor)
    """
    if not text:
        return 0, cursor

    def _avail_at(cur):
        if cur >= nut_boundary:
            return narrow_width
        w = full_width
        if logo_reserve_w > 0 and cur < logo_boundary:
            w -= logo_reserve_w
        return w

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


def _calc_nutrition_height(data: dict, sizes: dict) -> float:
    """计算营养成分表的总高度 (pt)。"""
    nut = data.get("nutrition") or {}
    table_data = nut.get("table_data") or []
    nut_row_h = sizes["nut"] + 2  # 行高 = 字号 + 2pt padding
    title_fs = sizes["nut"] + 2   # 标题字号
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

    # Production date / Best Before
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
                             available_h: float = 0) -> Tuple[float, int]:
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

    # Net Volume 预留高度（仅预留实际字符高度 + 最小间距）
    net_reserve = (_FIXED_NET * _CAP_H_RATIO) if data.get("net_weight") else 0

    # 营养表高度和边界
    right_col_ratio = 0.62
    col_gap = 4
    nut_h = _calc_nutrition_height(data, sizes)

    # 有效宽度（不考虑横向压缩）
    eff_w = content_w
    # 窄宽度：与渲染层 _draw_wrapped_text._line_width() 完全一致
    nut_reserve_w = content_w * right_col_ratio
    eff_nut_narrow = eff_w - nut_reserve_w

    body_leading = sizes["body"] * 1.15
    ingr_leading = sizes["ingr"] * 1.15

    # 营养表顶部到标签顶部的距离（从顶部往下数，到哪里开始变窄）
    # nut_top_y 从底部算起 = nut_h；从顶部算起 = available_h - nut_h
    nut_boundary = (available_h - nut_h - body_leading) if available_h > 0 else 999  # 缓冲区：提前一行开始缩窄，避免全宽文字与营养表标题重叠

    # ----- 逐块追踪累计高度，精确计算行数 -----
    block_gap = 0  # 无额外块间距：_draw_wrapped_text 尾部 leading 已提供 0.15×字号 的统一行距
    cursor = h  # 从 A 区域底部开始
    block_heights = []

    # --- Logo 避让相关 ---
    logo_zone_h = 25
    logo_reserve = 12 * mm
    a_h_for_logo = sizes["title"] * 0.8 + sizes["title"] * 1.15 + a_gap
    if data.get("product_name_cn"):
        a_h_for_logo += sizes["cn"] * 1.15 + a_gap
    logo_remaining_h = max(0, logo_zone_h - (a_h_for_logo - sizes["title"] * 0.8))
    logo_boundary = cursor + logo_remaining_h  # Logo 避让区域的绝对 cursor 边界

    # B+C 块 —— 统一 L 形估算（Logo + 营养表三段式避让）
    # Ingredients
    ingr = data.get("ingredients", "")
    if ingr:
        n, cursor = _count_text_lines_lshape(
            ingr, _FONT_NAME, sizes["ingr"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor, nut_boundary=nut_boundary,
            leading=ingr_leading, bold_prefix="Ingredients: ",
            logo_boundary=logo_boundary, logo_reserve_w=logo_reserve
        )
        bh = n * ingr_leading
        block_heights.append(bh)
        cursor += block_gap

    # Contains
    allergens = data.get("allergens", "")
    if allergens:
        n, cursor = _count_text_lines_lshape(
            allergens, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor, nut_boundary=nut_boundary,
            leading=body_leading, bold_prefix="Contains: ",
            logo_boundary=logo_boundary, logo_reserve_w=logo_reserve
        )
        bh = n * body_leading
        block_heights.append(bh)
        cursor += block_gap

    # Storage
    storage = data.get("storage", "")
    if storage:
        n, cursor = _count_text_lines_lshape(
            storage, _FONT_NAME, sizes["body"],
            full_width=eff_w, narrow_width=eff_nut_narrow,
            cursor=cursor, nut_boundary=nut_boundary,
            leading=body_leading,
            logo_boundary=logo_boundary, logo_reserve_w=logo_reserve
        )
        bh = n * body_leading
        block_heights.append(bh)
        cursor += block_gap

    # Date
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
            leading=body_leading,
            logo_boundary=logo_boundary, logo_reserve_w=logo_reserve
        )
        bh = n * body_leading
        block_heights.append(bh)
        cursor += block_gap

    # Product of
    bh = body_leading
    block_heights.append(bh)
    cursor += bh + block_gap

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
            cursor += block_gap  # cursor 已被 _count_text_lines_lshape 更新

    n_blocks = len(block_heights)
    content_h = sum(block_heights)

    # 总高度 = A + 所有文本块 + 每块后一个间距 + Net Volume 预留
    total_h = h + content_h + block_gap * n_blocks + net_reserve

    return total_h, n_blocks


def _calc_font_sizes(data: dict, country_cfg: Optional[dict] = None) -> Tuple[dict, float, float]:
    """
    两轮自适应字号计算 + 统一间距计算。

    第一轮：纯纵向二分搜索，找到在 content_w 下能放得下的最大字号。
    第二轮：如果纵向有大量空白（L 形悬崖效应），尝试更大字号 + h_scale 压缩。
            更大字号在 content_w 下会溢出，但在 content_w/h_scale 下可以放下。

    Returns:
        (sizes_dict, h_scale, unified_gap)
    """
    _register_font()
    content_w = LABEL_W - 2 * MARGIN
    left_col_w = content_w * 0.38
    available_h = LABEL_H - 2 * MARGIN

    # 法规最小字号（基于 x-height 实际印刷高度）
    # min_font_height_mm 是小写字母（a/e/o）的实际物理高度
    # 换算: font_pt = target_mm / (PT_TO_MM * x_height_ratio)
    min_font_pt = 4.0
    if country_cfg:
        min_mm = country_cfg.get("min_font_height_mm", 1.2)
        min_font_pt = min_mm / (0.3528 * _X_HEIGHT_RATIO)

    # ======== 第一轮：纯纵向二分搜索（h_scale=1.0）========
    lo, hi = 0.0, 1.0
    best_scale = 0.0

    for _ in range(20):
        mid = (lo + hi) / 2
        sizes = _sizes_at_scale(mid)

        # 法规最小字号约束
        if sizes["ingr"] < min_font_pt or sizes["body"] < min_font_pt:
            lo = mid
            continue

        h, _ = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h)
        if h <= available_h:
            best_scale = mid
            lo = mid
        else:
            hi = mid

    sizes = _sizes_at_scale(best_scale)
    h_scale = 1.0

    # ======== 第二轮：如果空白过大，尝试更大字号 + h_scale ========
    est_h, n_blocks = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h)
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
            s2 = _sizes_at_scale(mid2)

            if s2["ingr"] < min_font_pt or s2["body"] < min_font_pt:
                lo2 = mid2
                continue

            # 用原始宽度估算高度，看溢出多少
            h_full, _ = _estimate_content_height(data, s2, content_w, left_col_w, available_h=available_h)

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
                    h_comp, _ = _estimate_content_height(data, s2, eff_w, eff_left, available_h=available_h)
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
            sizes = _sizes_at_scale(best_scale2)
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
        h_test, _ = _estimate_content_height(data, sizes, content_w, left_col_w, available_h=available_h)
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
                h_comp, _ = _estimate_content_height(data, sizes, eff_w, eff_left, available_h=available_h)
                if h_comp <= available_h:
                    best_hs = hs_mid
                    hs_lo = hs_mid
                else:
                    hs_hi = hs_mid

            h_scale = best_hs

    # --------------------------------------------------
    # 统一间距 = body_leading（字号 × 1.15，与行内间距一致）
    # --------------------------------------------------
    unified_gap = 0  # 无额外块间距（_draw_wrapped_text 尾部 leading 已提供统一行距）

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
        if nut_reserve > 0 and cur_y < nut_top_y:
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


# --------------------------------------------------
# 生成 PDF 字节
# --------------------------------------------------
def generate_label_pdf(data: dict, country_cfg: Optional[dict] = None) -> bytes:
    """
    根据产品数据生成 70mm×69mm 标签 PDF。
    使用 flow_layout.py 独立引擎进行 B+C 区域自适应排版。
    A 区域（标题/Logo）、营养表、Net Volume 等固定区域不变。
    """
    _register_font()
    country_cfg = country_cfg or {}

    # 导入独立布局引擎
    from flow_layout import (
        FlowRect, FontConfig, layout_flow_content,
        find_best_font_size, plm_to_blocks, get_min_font_pt,
    )

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))

    # 内容区域边界
    left = MARGIN
    right = LABEL_W - MARGIN
    top = LABEL_H - MARGIN
    bottom = MARGIN
    content_w = right - left

    # 固定字号（营养表、Net Volume 不受自适应影响）
    sizes = {
        "nut":   _FIXED_NUT,
        "net":   _FIXED_NET,
    }

    # ============================================================
    # 区域划定（固定，互不侵占）
    # ============================================================

    # Logo
    logo_path = data.get("brand_logo", "")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if not logo_path or not os.path.isfile(logo_path):
        logo_path = os.path.join(static_dir, "logo_placeholder.png")

    # 营养表
    right_col_ratio = 0.62
    col_gap = 4
    left_col_w = content_w * (1 - right_col_ratio)
    right_col_w = content_w * right_col_ratio
    right_col_x = left + left_col_w + col_gap
    actual_right_w = right_col_w - col_gap
    nut_total_h = _calc_nutrition_height(data, sizes)
    nut_top_y = bottom + nut_total_h

    # Net Volume 预留
    net_weight = data.get("net_weight", "")
    net_reserve = (_FIXED_NET * _CAP_H_RATIO + 2) if net_weight else 0

    # --- 标题区域（固定高度，顶部） ---
    TITLE_ZONE_H = _FIXED_TITLE * 2.5 + 2  # 预留约 2.5 行标题高度
    title_top = top
    title_bottom = top - TITLE_ZONE_H
    title_narrow_w = content_w - LOGO_W - 2   # 第一行：扣除 logo 宽度 + 间距
    logo_row_h = LOGO_H                       # logo 高度决定窄行区域

    # 标题区域为 L 型：
    # R1: logo 旁边的窄区域（1行高度）
    # R2: logo 下方的全宽区域（剩余行）
    title_regions = []
    if logo_row_h > 0:
        title_regions.append(FlowRect(x=left, y=title_top, width=title_narrow_w, height=min(logo_row_h, TITLE_ZONE_H)))
    remaining_h = TITLE_ZONE_H - logo_row_h
    if remaining_h > 0:
        title_regions.append(FlowRect(x=left, y=title_top - logo_row_h, width=content_w, height=remaining_h, seamless=True))

    # --- Content 区域（标题底部 → 营养表顶部 → 左栏） ---
    content_top = title_bottom
    r1_h = content_top - nut_top_y
    r2_h = nut_top_y - (bottom + net_reserve)

    content_regions = []
    if r1_h > 0:
        content_regions.append(FlowRect(x=left, y=content_top, width=content_w, height=r1_h))
    if r2_h > 0:
        content_regions.append(FlowRect(x=left, y=nut_top_y, width=left_col_w, height=r2_h, seamless=True))

    # ============================================================
    # 第一步：计算 content 字号（在固定的 content 区域内）
    # ============================================================
    blocks = plm_to_blocks(data)

    country_code = country_cfg.get("code", "DEFAULT")
    min_mm = country_cfg.get("min_font_height_mm", 1.2)
    min_font_pt = min_mm / (0.3528 * _X_HEIGHT_RATIO)

    content_font_size, content_h_scale = find_best_font_size(
        blocks, content_regions,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        min_size=min_font_pt,
        max_size=16.0,
    )

    # ============================================================
    # 第二步：渲染标题（在固定的标题区域内）
    # min_size = content_font_size × 1.1
    # ============================================================
    en_name = data.get("product_name_en", "PRODUCT NAME")
    cn_name = data.get("product_name_cn", "")

    # 渲染 logo
    if os.path.isfile(logo_path):
        try:
            c.drawImage(logo_path, right - LOGO_W, top - LOGO_H,
                        width=LOGO_W, height=LOGO_H,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    from flow_layout import layout_title
    title_font_size, title_h_scale, title_result = layout_title(
        text_en=en_name,
        text_cn=cn_name,
        flow_regions=title_regions,
        content_font_size=content_font_size,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        canvas=c,
    )

    # ============================================================
    # 第三步：渲染 content（在固定的 content 区域内）
    # ============================================================
    fc = FontConfig(
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        font_size=content_font_size,
        h_scale=content_h_scale,
    )
    layout_flow_content(blocks, content_regions, fc, canvas=c)

    # ============================================================
    # 固定区域：Net Volume + 营养表 + HALAL
    # ============================================================

    # --- Net Volume：底部与营养表齐平 ---
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

    # --- 营养表：固定在右下角 ---
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


def generate_label_preview_html(data: dict, country_cfg: Optional[dict] = None) -> Tuple[str, bytes]:
    """生成标签预览 HTML 和 PDF 字节。"""
    pdf_bytes = generate_label_pdf(data, country_cfg)
    png_b64 = pdf_to_png_base64(pdf_bytes)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
body {{ margin:0; padding:0; background:#4a4a4a; display:flex; flex-direction:column; align-items:center; min-height:100vh; }}
img {{ max-width:100%; background:white; box-shadow:0 4px 16px rgba(0,0,0,0.5); margin:16px; }}
</style></head><body>
<img src="data:image/png;base64,{png_b64}" alt="Label Preview" />
</body></html>"""

    return html, pdf_bytes
