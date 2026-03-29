"""
区域渲染器

每个函数负责一个独立的语义区域，输入为 TemplateRegion/FlowRect + canvas + data。
函数之间通过返回值传递依赖（如 content_font_size → title min_size）。
"""

import os
from typing import List, Optional, Tuple

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from flow_layout import (
    FlowRect, FontConfig, TextBlock,
    layout_flow_content, find_best_font_size,
    plm_to_blocks, layout_title,
)
from template_extractor import TemplateRegion

# ---------------------------------------------------------------------------
# 字体常量（与 label_renderer.py 保持一致）
# ---------------------------------------------------------------------------
_FONT_NAME = "AliPuHuiTi"
_FONT_NAME_BOLD = "AliPuHuiTi-Bold"
_FONT_NAME_HEAVY = "AliPuHuiTi-Heavy"
_CAP_H_RATIO = 0.735
_X_HEIGHT_RATIO = 0.54

# 固定字号
_NUT_ROW_H = 7.94        # 营养表行高 2.8mm → 7.94pt
_NUT_FONT = _NUT_ROW_H - 2  # 营养表字号 ≈ 5.94pt
_NET_FONT = 21             # Net Volume 固定 21pt


# ---------------------------------------------------------------------------
# render_content — 内容区域
# ---------------------------------------------------------------------------

def render_content(
    canvas: Optional[Canvas],
    regions: List[FlowRect],
    data: dict,
    country_cfg: dict,
) -> Tuple[float, float]:
    """
    内容区域渲染器。

    Args:
        canvas:      ReportLab Canvas （None = dry-run，仅计算字号）
        regions:     Content 的 FlowRect 列表（倒 L 型等）
        data:        PLM 数据
        country_cfg: 国家法规配置

    Returns:
        (font_size, h_scale) — content 的自适应字号和横向压缩比
    """
    # 查找对应的 country_code 进行传递
    from country_config import COUNTRY_REGISTRY
    target_country = "DEFAULT"
    for code, cfg in COUNTRY_REGISTRY.items():
        if cfg == country_cfg:
            target_country = code
            break
            
    blocks = plm_to_blocks(data, target_country=target_country)

    # 法规最小字号
    min_mm = country_cfg.get("min_font_height_mm", 1.2)
    min_font_pt = min_mm / (0.3528 * _X_HEIGHT_RATIO)

    font_size, h_scale = find_best_font_size(
        blocks, regions,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        min_size=min_font_pt,
        max_size=16.0,
        optimize_fill=True,
    )

    if canvas is not None:
        fc = FontConfig(
            font_name=_FONT_NAME,
            font_name_bold=_FONT_NAME_BOLD,
            font_size=font_size,
            h_scale=h_scale,
        )
        layout_flow_content(blocks, regions, fc, canvas=canvas)

    return font_size, h_scale


# ---------------------------------------------------------------------------
# render_title — 标题区域
# ---------------------------------------------------------------------------

def render_title(
    canvas: Optional[Canvas],
    regions: List[FlowRect],
    data: dict,
    country_cfg: Optional[dict] = None,
) -> Tuple[float, float, object]:
    """
    标题区域渲染器（已与 content 完全解耦）。

    Args:
        canvas:             ReportLab Canvas（None = 仅计算）
        regions:            标题的 FlowRect 列表（可能 L 型）
        data:               PLM 数据（需含 product_name_en / product_name_cn）
        country_cfg:        国家法规配置

    Returns:
        (font_size, h_scale, LayoutResult) — 标题的自适应结果及布局详情
    """
    en_name = data.get("product_name_en", "PRODUCT NAME")
    cn_name = data.get("product_name_cn", "")
    country_code = (country_cfg or {}).get("code", "DEFAULT")

    font_size, h_scale, result = layout_title(
        text_en=en_name,
        text_cn=cn_name,
        flow_regions=regions,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        country_code=country_code,
        canvas=canvas,
    )

    return font_size, h_scale, result


# ---------------------------------------------------------------------------
# render_nutrition — 营养表
# ---------------------------------------------------------------------------

def _calc_tz(text: str, font_name: str, font_size: float, max_w: float) -> int:
    """计算文本所需的 Tz 值（100 = 无压缩，越小越窄）。"""
    if not text or max_w <= 0:
        return 100
    tw = pdfmetrics.stringWidth(text, font_name, font_size)
    if tw <= 0:
        return 100
    return min(100, int(max_w / tw * 100))


def _draw_compressed_text(
    c: Canvas, text: str, x: float, y: float,
    font_name: str, font_size: float,
    max_w: float, align: str = "left",
    tz_override: int = -1,
):
    """
    绘制文本，支持横向 Tz 压缩。

    Args:
        c:           ReportLab Canvas
        text:        待绘制文本
        x:           锚点 x（left 的左边 / center 的区间起点 / right 的右边）
        y:           baseline y
        font_name:   字体名
        font_size:   字号
        max_w:       可用宽度
        align:       "left" | "center" | "right"
        tz_override: 强制使用的 Tz 值（>0 时生效），用于全表统一压缩
    """
    if not text:
        return
    tw = pdfmetrics.stringWidth(text, font_name, font_size)

    # 确定 Tz
    if tz_override > 0:
        tz = tz_override
    else:
        tz = min(100, int(max_w / tw * 100)) if tw > max_w else 100

    actual_w = tw * tz / 100.0

    # 对齐计算
    if align == "center":
        draw_x = x + (max_w - actual_w) / 2
    elif align == "right":
        draw_x = x + max_w - actual_w
    else:
        draw_x = x

    t = c.beginText(draw_x, y)
    t.setFont(font_name, font_size)
    if tz < 100:
        t._code.append(f'{tz} Tz')
    t.textOut(text)
    if tz < 100:
        t._code.append('100 Tz')
    c.drawText(t)

def _format_template_text(text: str, data: dict) -> str:
    """如果文本包含 {key} 占位符，尝试用 data 中的值替换"""
    if "{" in text and "}" in text:
        try:
            return text.format(**data)
        except KeyError:
            pass
    return text


def _wrap_text(text: str, font_name: str, font_size: float, max_w: float, tz: float = 100.0) -> list:
    """将长文本按可用宽度自动断词折行，返回行列表。
    
    Args:
        text:      长文本
        font_name: 字体名
        font_size: 字号
        max_w:     可用宽度（pt）
        tz:        横向压缩百分比（100=无压缩）
    Returns:
        list[str]: 折行后的文本行列表
    """
    if not text or max_w <= 0:
        return [text] if text else [""]
    words = text.split()
    if not words:
        return [text]
    lines = []
    current = ""
    for w in words:
        test = f"{current} {w}".strip() if current else w
        tw = pdfmetrics.stringWidth(test, font_name, font_size) * tz / 100.0
        if tw <= max_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines if lines else [text]


def render_nutrition(
    canvas,
    region,
    data: dict,
    country_cfg: dict,
    nut_table_type: str = "",
    country_code: str = "DEFAULT",
) -> float:
    from reportlab.pdfbase import pdfmetrics
    from nut_layouts import get_nut_layout, load_excel_config, get_data_row_template
    load_excel_config()  # 每次渲染前热重载 Excel（如果有）
    layout = get_nut_layout(country_code, override_type=nut_table_type or None)

    c = canvas
    x = region.x
    width = region.width
    nutrition = data.get("nutrition") or {}
    table_data = nutrition.get("table_data") or []

    # ── 从 Excel 数据行模板合并显示属性 ──
    tmpl = get_data_row_template(country_code)
    if tmpl:
        # 按 name 建立 PLM 值索引
        plm_by_name = {}
        for row in table_data:
            name = row.get("name", "")
            plm_by_name[name] = row
        
        # 以 Excel 模板为骨架，PLM 值填充
        merged = []
        for t_row in tmpl:
            name = t_row.get("name", "")
            merged_row = dict(t_row)  # 模板的显示属性
            if name in plm_by_name:
                # PLM 的含量值（value, nrv, per_serving 等）补入
                for k, v in plm_by_name[name].items():
                    if k not in merged_row or k in ("value", "nrv", "per_serving", "per_100g"):
                        merged_row[k] = v
            merged.append(merged_row)
        table_data = merged

    # ── 计算列绝对宽度和 X 偏移 ──
    col_widths = [col.width_ratio * width for col in layout.columns]
    col_x_offsets = []
    curr_x = x
    for w in col_widths:
        col_x_offsets.append(curr_x)
        curr_x += w

    # ── PASS A: 先找实际字号（二分法）──
    n_data_rows = len(table_data)

    # 最低字号：为了保证在极小物理框内能完整把表格画完（不黏连、不切断），移除硬性下限。
    # 合规性拦截已在 app.py 的外层调用产生警告，此处只负责如实自适应排版。
    MIN_FS = 1.0
    MAX_FS = 16.0

    pad_ratio = 0.0
    line_ratio = getattr(layout, 'line_height_ratio', 1.15)

    def _total_height(fs):
        ref_fs = getattr(layout, 'reference_font_size', 0.0)
        sc = fs / ref_fs if ref_fs and ref_fs > 0 else 1.0
        pad_y = fs * pad_ratio
        lh = fs * line_ratio
        h = pad_y  # 顶边留白
        for hdr in layout.header_rows:
            h += getattr(hdr, "margin_top", 0.0) * sc  # 标题行上间距（可负）
            cell_rows = 2 if hdr.multi_line else 1
            h += lh * cell_rows * hdr.height_ratio
            if hdr.draw_line_below:
                h += pad_y * 2  # 下边留白 + 下一行的上沿留白
                h += getattr(hdr, 'margin_below', 0.0) * sc  # 底线后额外间距
        
        for item in table_data:
            h += item.get("margin_top", 0.0) * sc  # 数据行上间距
            h += lh * item.get("height_ratio", 1.0)
        # 底部行：预留空间（用 footer 字号对应的 leading × height_ratio）
        # 与渲染时按 height_ratio 比例分配的逻辑保持一致
        if layout.footer_rows:
            est_footer_leading = fs * 0.85 * 1.15  # footer_fs * leading_ratio
            for ftr in layout.footer_rows:
                h += est_footer_leading * ftr.height_ratio
        h += pad_y  # 底边留白
        return h

    lo, hi = MIN_FS, MAX_FS
    for _ in range(20):
        mid = (lo + hi) / 2
        if _total_height(mid) <= region.height:
            lo = mid
        else:
            hi = mid
    font_size = lo
    pad_y = font_size * pad_ratio
    lh = font_size * line_ratio
    row_h = lh  # 为了兼容下方的 row_h 调用

    # ── 自适应缩放：所有绝对 pt 值按 actual_fs / reference_fs 缩放 ──
    ref_fs = getattr(layout, 'reference_font_size', 0.0)
    scale = font_size / ref_fs if ref_fs and ref_fs > 0 else 1.0
    m_scale = scale  # 专门用于垂直 margin 的缩放比例
    title_scale = 1.0 # 专门用于大字号标题的阻缩放比例
    struct_scale = 1.0 # 结构性（负数）margin 的约束缩放比例

    # ── 优雅降级：当字号已到法规下限但仍溢出时，压缩 margin 和 行高 ──
    if _total_height(font_size) > region.height:
        # 计算不含可压缩 margin 的基础高度
        base_h = pad_y  # 顶边
        for hdr in layout.header_rows:
            mt = getattr(hdr, "margin_top", 0.0)
            if mt < 0:
                base_h += mt * scale
            cell_rows = 2 if hdr.multi_line else 1
            base_h += lh * cell_rows * hdr.height_ratio
            if hdr.draw_line_below:
                base_h += pad_y * 2
                mb = getattr(hdr, 'margin_below', 0.0)
                if mb < 0:
                    base_h += mb * scale
        for item in table_data:
            mt = item.get("margin_top", 0.0)
            if mt < 0:
                base_h += mt * scale
            base_h += lh * item.get("height_ratio", 1.0)
        # 底部行：与 _total_height 保持一致
        if layout.footer_rows:
            est_footer_leading = font_size * 0.85 * 1.15
            for ftr in layout.footer_rows:
                base_h += est_footer_leading * ftr.height_ratio
        base_h += pad_y  # 底边

        if base_h > region.height:
            # 死守字体底线保护：寻找全表的“压缩极限”，绝对不让任何行的局部高度(local_row_h)跌穿字体自身的物理高度限制，造成上下横线粘黏
            min_allowed_lh_scale = 0.0
            
            for hdr in layout.header_rows:
                hr = getattr(hdr, "height_ratio", 1.0)
                if hdr.multi_line:
                    # 双行文本折叠极限：cap(0.73) + gap(0.95) + desc(0.22) = 1.9。给予 1.95 的绝对安全比例
                    req_scale = (font_size * 1.95) / (lh * hr * 2) if hr > 0 else 0
                else:
                    # 单行文本极限：字高 0.95。给予 1.05 的安全比
                    req_scale = (font_size * 1.05) / (lh * hr) if hr > 0 else 0
                if req_scale > min_allowed_lh_scale:
                    min_allowed_lh_scale = req_scale
                    
            for item in table_data:
                hr = item.get("height_ratio", 1.0)
                req_scale = (font_size * 1.05) / (lh * hr) if hr > 0 else 0
                if req_scale > min_allowed_lh_scale:
                    min_allowed_lh_scale = req_scale
            # 底部行不参与 lh_scale 约束

            # 只能在不重叠的安全范围内极度压缩行高 (lh)
            lh_scale = max(min_allowed_lh_scale, region.height / base_h)
            
            lh = lh * lh_scale
            row_h = lh
            m_scale = 0.0 # 可压缩 margin 彻底归零
            title_scale = lh_scale # 让超出常规字号的标题字号也跟着行高同比例压缩
            struct_scale = lh_scale # 负数 margin 等比压缩退让
            pad_y = pad_y * lh_scale
        else:
            # 仅仅压缩 margin 即可
            margin_total = _total_height(font_size) - base_h
            if margin_total > 0:
                avail_for_margin = region.height - base_h
                margin_scale = avail_for_margin / margin_total
                m_scale = scale * margin_scale

    # 缩放布局级别的绝对 pt 值
    s_col_padding = layout.col_padding * scale
    s_sub_indent = layout.sub_indent * scale
    s_data_row_line_padding = layout.data_row_line_padding * scale
    s_data_row_line_width = layout.data_row_line_width * scale
    s_header_line_width = layout.header_line_width * scale
    s_border_line_width = layout.border_line_width * scale
    s_outer_border_lw = (layout.outer_border_line_width or layout.border_line_width) * scale
    s_thick_line_width = layout.thick_line_width * scale

    # ── PASS B: 用实际 font_size 计算横向压缩比 (min_tz) ──
    min_tz = 1.0

    # 提取列边距 padding (缩放后)
    c_pad = s_col_padding

    for hdr in layout.header_rows:
        if getattr(hdr, 'independent_tz', False):
            continue  # 独立压缩行不参与全局 min_tz 计算
        if getattr(hdr, 'col_width_ratios', None):
            continue  # 自定义列宽行独立计算 Tz，不拖累数据行
        base_fs = font_size * getattr(hdr, "font_ratio", 1.0)
        font = getattr(hdr, "font_override", None) or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
        if hdr.span_full:
            local_fs = base_fs
            text = hdr.cells[0]
            if hdr.template:
                text = _format_template_text(text, nutrition)
            if hdr.bold and nutrition.get("nut_title"):
                text = nutrition["nut_title"]
            elif not hdr.bold and nutrition.get("nut_subtitle"):
                text = nutrition["nut_subtitle"]
            req_w = pdfmetrics.stringWidth(text, font, local_fs)
            hp = getattr(hdr, 'horizontal_padding', None)
            if hp is not None:
                lpad = hp
            else:
                lpad = 4 if hdr.fill_color else (0 if getattr(hdr, 'independent_tz', False) else c_pad)
            avail_w = width - lpad * 2
            if req_w > 0 and avail_w / req_w < min_tz:
                min_tz = avail_w / req_w
        else:
            local_cw = [width * r for r in hdr.col_width_ratios] if getattr(hdr, "col_width_ratios", None) else col_widths
            font_over = getattr(hdr, "font_override", None)
            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(local_cw) or not cell_text:
                    continue
                if isinstance(font_over, list):
                    font = font_over[ci] if ci < len(font_over) and font_over[ci] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                else:
                    font = font_over or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)

                font_ratios = getattr(hdr, "font_ratios", None)
                local_fs = (font_size * font_ratios[ci]) if font_ratios and ci < len(font_ratios) else base_fs

                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                for line_text in cell_text.split("\n"):
                    req_w = pdfmetrics.stringWidth(line_text, font, local_fs)
                    if req_w > 0 and (local_cw[ci] - c_pad * 2) / req_w < min_tz:
                        min_tz = (local_cw[ci] - c_pad * 2) / req_w

    for item in table_data:
        is_sub = item.get("is_sub", False)
        for ci, col in enumerate(layout.columns):
            val = str(item.get(col.key, ""))
            if not val:
                continue
            font = getattr(col, "font_override", None) or _FONT_NAME
            if col.key == "name":
                indent = s_sub_indent if is_sub else c_pad
                display_val = layout.name_mapping.get(val.strip().lower(), val.strip()) if layout.name_mapping else val
                req_w = pdfmetrics.stringWidth(display_val, font, font_size)
                avail_w = col_widths[ci] - indent - c_pad
                if req_w > 0 and avail_w / req_w < min_tz:
                    min_tz = avail_w / req_w
            else:
                req_w = pdfmetrics.stringWidth(val, font, font_size)
                if req_w > 0 and (col_widths[ci] - c_pad * 2) / req_w < min_tz:
                    min_tz = (col_widths[ci] - c_pad * 2) / req_w

    global_tz = min(100.0, min_tz * 100)

    def _baseline_y(rect_y, f_size, r_h=None, text=None):
        """计算文字基线 y 坐标，使文字在行高中垂直居中。
        rect_y: 行的顶边 y 坐标
        f_size: 字号
        r_h:    行高（如不传则使用全局 row_h）
        """
        if r_h is None:
            r_h = row_h
        cap_h = f_size * _CAP_H_RATIO       # 大写字母高度
        
        has_descender = True
        if text is not None:
            has_descender = any(c in text for c in "gjpqy(),")
            
        if not has_descender:
            # 纯上层字母平底居中
            return rect_y - r_h / 2 - cap_h / 2
        else:
            desc_h = f_size * 0.22      # 下行字母深度（g, p, y 等）
            glyph_h = cap_h + desc_h    # 字体视觉总高度
            # 居中: 行高中心 - 字形中心偏移 = baseline
            return rect_y - (r_h - glyph_h) / 2 - cap_h

    y = region.y - pad_y  # 顶边预留 pad_y
    table_top = y + pad_y
    col_sep_start_y = None   
    last_header_line_y = None

    # ── 标题区渲染 ──
    for hdr in layout.header_rows:
        mt = getattr(hdr, "margin_top", 0.0)
        actual_mt = mt * scale * struct_scale if mt < 0 else mt * m_scale
        y -= actual_mt
        row_top_y = y
        raw_base_fs = font_size * getattr(hdr, "font_ratio", 1.0)
        # 用 title_scale 对大标题施加同频阻尼，但不侵犯国家最低限高 font_size
        base_fs = max(font_size, raw_base_fs * title_scale)
        local_lh = lh * getattr(hdr, "height_ratio", 1.0)
        cell_h = local_lh * 2 if hdr.multi_line else local_lh

        if hdr.fill_color:
            r_fill, g_fill, b_fill = hdr.fill_color
            from reportlab.lib.colors import Color as _Color
            c.setFillColor(_Color(r_fill, g_fill, b_fill))
            c.rect(x, y - cell_h, width, cell_h, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)

        txt_r, txt_g, txt_b = hdr.text_color
        c.setFillColorRGB(txt_r, txt_g, txt_b)

        if hdr.multi_line:
            local_cw = [width * r for r in hdr.col_width_ratios] if getattr(hdr, "col_width_ratios", None) else col_widths
            local_cx = []
            curr_x = x
            for cw in local_cw:
                local_cx.append(curr_x)
                curr_x += cw
            font_over = getattr(hdr, "font_override", None)
            font_ratios = getattr(hdr, "font_ratios", None)

            # 自定义列宽行：独立计算本行 Tz，不使用 global_tz
            if getattr(hdr, 'col_width_ratios', None):
                row_min_tz = 1.0
                for ci2, ct2 in enumerate(hdr.cells):
                    if ci2 >= len(local_cw) or not ct2:
                        continue
                    if hdr.template:
                        ct2 = _format_template_text(ct2, nutrition)
                    f_over2 = font_over[ci2] if isinstance(font_over, list) and ci2 < len(font_over) and font_over[ci2] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                    fr2 = (font_size * font_ratios[ci2]) if font_ratios and ci2 < len(font_ratios) else base_fs
                    for ln2 in ct2.split("\n"):
                        rw2 = pdfmetrics.stringWidth(ln2, f_over2, fr2)
                        if rw2 > 0:
                            ratio2 = (local_cw[ci2] - c_pad * 2) / rw2
                            if ratio2 < row_min_tz:
                                row_min_tz = ratio2
                local_tz = min(100.0, row_min_tz * 100)
            else:
                local_tz = global_tz

            valign = getattr(hdr, "valign", "center")
            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(local_cw) or not cell_text:
                    continue
                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                lines = cell_text.split("\n")
                if isinstance(font_over, list):
                    font = font_over[ci] if ci < len(font_over) and font_over[ci] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                else:
                    font = font_over or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                
                if font_ratios and ci < len(font_ratios):
                    raw_local_fs = font_size * font_ratios[ci]
                    local_fs = max(font_size, raw_local_fs * title_scale)
                else:
                    local_fs = base_fs
                
                if valign == "ca_header":
                    margin_y = 1.5 * scale
                    if len(lines) == 1:
                        # 左侧 Calories 紧贴底端粗线对齐（消除空隙），给横线上半厚度预留 margin_y 空间
                        text_y = (y - cell_h) + margin_y
                        _draw_compressed_text(
                            c, lines[0], local_cx[ci] + c_pad, text_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
                    else:
                        # 右侧双行平铺上下边界锁定对齐
                        cap_h = local_fs * _CAP_H_RATIO
                        desc_h = local_fs * 0.22
                        tight_gap = local_fs * 0.95
                        
                        # 防挤压保护：如果上下留白导致两行重叠，则降级为紧凑居中排列
                        if (cell_h - margin_y * 2) < (cap_h + tight_gap + desc_h):
                            block_h = cap_h + tight_gap + desc_h
                            margin_y_adj = (cell_h - block_h) / 2
                            line1_y = y - margin_y_adj - cap_h
                            line2_y = line1_y - tight_gap
                        else:
                            line1_y = y - margin_y - cap_h
                            line2_y = (y - cell_h) + margin_y + desc_h
                        
                        _draw_compressed_text(
                            c, lines[0], local_cx[ci] + c_pad, line1_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
                        _draw_compressed_text(
                            c, lines[1], local_cx[ci] + c_pad, line2_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
                else:
                    if len(lines) == 1:
                        text_y = _baseline_y(y, local_fs, cell_h, text=lines[0])
                        _draw_compressed_text(
                            c, lines[0], local_cx[ci] + c_pad, text_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
                    else:
                        cap_h = local_fs * _CAP_H_RATIO
                        desc_h = local_fs * 0.22
                        tight_gap = local_fs * 0.95  # 极小行距用于紧凑排列
                        block_h = tight_gap + cap_h + desc_h
                        top_margin = (cell_h - block_h) / 2
                        line1_y = y - top_margin - cap_h
                        line2_y = line1_y - tight_gap
                        
                        _draw_compressed_text(
                            c, lines[0], local_cx[ci] + c_pad, line1_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
                        _draw_compressed_text(
                            c, lines[1], local_cx[ci] + c_pad, line2_y,
                            font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                            tz_override=local_tz,
                        )
            y -= cell_h
        elif hdr.span_full:
            local_fs = base_fs
            text = hdr.cells[0]
            if hdr.template:
                text = _format_template_text(text, nutrition)
            if hdr.bold and nutrition.get("nut_title"):
                text = nutrition["nut_title"]
            elif not hdr.bold and hdr.span_full and nutrition.get("nut_subtitle"):
                text = nutrition["nut_subtitle"]
            text_y = _baseline_y(y, local_fs, cell_h, text=text)

            font = getattr(hdr, "font_override", None) or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
            
            hp = getattr(hdr, 'horizontal_padding', None)
            if hp is not None:
                lpad = hp
            else:
                lpad = 4 if hdr.fill_color else (0 if getattr(hdr, 'independent_tz', False) else c_pad)
            avail_w = width - lpad * 2
            
            override_tz = global_tz
            if getattr(hdr, 'independent_tz', False):
                req_w = pdfmetrics.stringWidth(text, font, local_fs)
                override_tz = min(100.0, (avail_w / req_w) * 100) if req_w > 0 else 100.0
                
            _draw_compressed_text(
                c, text, x + lpad, text_y,
                font, local_fs, avail_w,
                align=hdr.align, tz_override=override_tz,
            )
            y -= cell_h
        else:
            local_cw = [width * r for r in hdr.col_width_ratios] if getattr(hdr, "col_width_ratios", None) else col_widths
            local_cx = []
            curr_x = x
            for cw in local_cw:
                local_cx.append(curr_x)
                curr_x += cw
            font_over = getattr(hdr, "font_override", None)
            font_ratios = getattr(hdr, "font_ratios", None)

            row_text = "".join(hdr.cells)
            text_y = _baseline_y(y, base_fs, cell_h, text=row_text)
            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(local_cw) or not cell_text:
                    continue
                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                    
                if isinstance(font_over, list):
                    font = font_over[ci] if ci < len(font_over) and font_over[ci] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                else:
                    font = font_over or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                if font_ratios and ci < len(font_ratios):
                    raw_local_fs = font_size * font_ratios[ci]
                    local_fs = max(font_size, raw_local_fs * title_scale)
                else:
                    local_fs = base_fs
                    
                _draw_compressed_text(
                    c, cell_text, local_cx[ci] + c_pad, text_y,
                    font, local_fs, local_cw[ci] - c_pad * 2, align=layout.columns[ci].align,
                    tz_override=global_tz,
                )
            y -= cell_h

        c.setFillColorRGB(0, 0, 0)

        if hdr.draw_line_below:
            y -= pad_y
            
            l_pad = getattr(hdr, 'line_left_padding', 0.0)
            # 未显式设置时，自动继承布局全局值
            if not l_pad: l_pad = getattr(hdr, 'line_padding', 0.0) or s_data_row_line_padding
            
            r_pad = getattr(hdr, 'line_right_padding', 0.0)
            if not r_pad: r_pad = getattr(hdr, 'line_padding', 0.0) or s_data_row_line_padding
                
            lw = getattr(hdr, 'line_width_below', 0.0) or s_header_line_width
            if lw < 0:
                lw = s_border_line_width * abs(lw)
            c.setLineWidth(lw * scale if lw == s_header_line_width else lw)
            
            span_idx = getattr(hdr, 'line_span_col', None)
            
            if span_idx is not None and span_idx < len(col_widths):
                local_cw = [width * r for r in hdr.col_width_ratios] if getattr(hdr, "col_width_ratios", None) else col_widths
                local_cx = []
                curr_x = x
                for cw in local_cw:
                    local_cx.append(curr_x)
                    curr_x += cw

                # 计算 span 列文字的实际渲染宽度，横线只画到文字末端
                span_text = ""
                if span_idx < len(hdr.cells):
                    span_text = hdr.cells[span_idx]
                    if hdr.template:
                        span_text = _format_template_text(span_text, nutrition)
                
                if span_text:
                    font_over_line = getattr(hdr, "font_override", None)
                    if isinstance(font_over_line, list):
                        span_font = font_over_line[span_idx] if span_idx < len(font_over_line) and font_over_line[span_idx] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                    else:
                        span_font = font_over_line or (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                    font_ratios_line = getattr(hdr, "font_ratios", None)
                    span_fs = (font_size * font_ratios_line[span_idx]) if font_ratios_line and span_idx < len(font_ratios_line) else font_size * getattr(hdr, "font_ratio", 1.0)
                    span_fs = max(font_size, span_fs * title_scale)
                    # 使用该行的 local_tz 计算实际文字宽度
                    if getattr(hdr, 'col_width_ratios', None):
                        row_min_tz_l = 1.0
                        for ci_l, ct_l in enumerate(hdr.cells):
                            if ci_l >= len(local_cw) or not ct_l:
                                continue
                            if hdr.template:
                                ct_l = _format_template_text(ct_l, nutrition)
                            f_l = font_over_line[ci_l] if isinstance(font_over_line, list) and ci_l < len(font_over_line) and font_over_line[ci_l] else (_FONT_NAME_BOLD if hdr.bold else _FONT_NAME)
                            fr_l = (font_size * font_ratios_line[ci_l]) if font_ratios_line and ci_l < len(font_ratios_line) else font_size * getattr(hdr, "font_ratio", 1.0)
                            for ln_l in ct_l.split("\n"):
                                rw_l = pdfmetrics.stringWidth(ln_l, f_l, fr_l)
                                if rw_l > 0:
                                    ratio_l = (local_cw[ci_l] - c_pad * 2) / rw_l
                                    if ratio_l < row_min_tz_l:
                                        row_min_tz_l = ratio_l
                        line_tz = min(100.0, row_min_tz_l * 100) / 100.0
                    else:
                        line_tz = global_tz / 100.0
                    
                    raw_text_w = pdfmetrics.stringWidth(span_text, span_font, span_fs)
                    actual_text_w = raw_text_w * line_tz
                    span_end_x = x + l_pad + actual_text_w
                else:
                    span_end_x = local_cx[span_idx] + local_cw[span_idx]
                
                c.line(x + l_pad, y, span_end_x, y)
            else:
                c.line(x + l_pad, y, x + width - r_pad, y)
                
            last_header_line_y = y
            y -= pad_y
            mb = getattr(hdr, 'margin_below', 0.0)
            actual_mb = mb * scale * struct_scale if mb < 0 else mb * m_scale
            y -= actual_mb

        # 竖线起始计算：优先用 col_sep_here 标记；如果没有，则送第一个非 span_full 行开始
        if getattr(hdr, 'col_sep_here', False) and col_sep_start_y is None:
            col_sep_start_y = row_top_y
        elif col_sep_start_y is None and not hdr.span_full:
            col_sep_start_y = row_top_y

    data_start_y = y

    # ── 数据行渲染 ──
    for row_idx, item in enumerate(table_data):
        mt = item.get("margin_top", 0.0)
        actual_mt = mt * scale * struct_scale if mt < 0 else mt * m_scale
        y -= actual_mt
        is_sub = item.get("is_sub", False)
        row_top_y = y
        local_row_h = row_h * item.get("height_ratio", 1.0)
        
        row_text = "".join(str(item.get(col.key, "")) for col in layout.columns)
        text_y = _baseline_y(y, font_size, r_h=local_row_h, text=row_text)
        
        # 探测当前行除去第一列（名称列）以外，是否全部为空
        row_is_empty_after_name = True
        for next_ci in range(1, len(layout.columns)):
            if str(item.get(layout.columns[next_ci].key, "")):
                row_is_empty_after_name = False
                break

        for ci, col in enumerate(layout.columns):
            val = str(item.get(col.key, ""))
            if not val:
                continue

            # 探测后续列是否全部为空
            is_empty_after = True
            for next_ci in range(ci + 1, len(layout.columns)):
                if str(item.get(layout.columns[next_ci].key, "")):
                    is_empty_after = False
                    break

            if col.key == "name":
                indent = s_sub_indent if is_sub else c_pad
                if layout.name_mapping and (val.startswith(" ") or val.startswith("-")):
                    indent = 8
                display_val = layout.name_mapping.get(val.strip().lower(), val.strip()) if layout.name_mapping else val
                
                # 若后续全空且无列分割线，允许当前文本跨界伸展至表宽，避免不必要的横向缩放
                if is_empty_after and not getattr(layout, 'draw_col_sep', True):
                    max_w = width - (col_x_offsets[ci] - x) - indent - c_pad
                else:
                    max_w = col_widths[ci] - indent - c_pad

                explicit_bold = item.get("bold")
                value_part = item.get("value", "")
                
                # 决定名称部分的字体
                if item.get("heavy"):
                    font_to_use = _FONT_NAME_HEAVY
                elif explicit_bold is not None:
                    font_to_use = _FONT_NAME_BOLD if explicit_bold else _FONT_NAME
                else:
                    font_to_use = _FONT_NAME_BOLD if not is_sub and getattr(layout, 'bold_main_items', False) else _FONT_NAME

                if value_part:
                    # 名称和数值分离渲染：name 用指定字重，value 始终 Regular
                    display_name = display_val
                    name_with_space = display_name + " "
                    name_w = pdfmetrics.stringWidth(name_with_space, font_to_use, font_size) * (global_tz / 100.0)
                    _draw_compressed_text(
                        c, display_name, col_x_offsets[ci] + indent, text_y,
                        font_to_use, font_size, max_w,
                        align="left", tz_override=global_tz,
                    )
                    _draw_compressed_text(
                        c, value_part, col_x_offsets[ci] + indent + name_w, text_y,
                        _FONT_NAME, font_size, max_w - name_w,
                        align="left", tz_override=global_tz,
                    )
                else:
                    _draw_compressed_text(
                        c, display_val, col_x_offsets[ci] + indent, text_y,
                        font_to_use, font_size, max_w,
                        align=col.align, tz_override=global_tz,
                    )
            else:
                _draw_compressed_text(
                    c, val, col_x_offsets[ci] + c_pad, text_y,
                    _FONT_NAME, font_size, col_widths[ci] - c_pad * 2,
                    align=col.align, tz_override=global_tz,
                )

        y -= local_row_h
        
        # ── 逐行绘制：行分割横线 ──
        is_last_row = row_idx == len(table_data) - 1
        has_footers = len(layout.footer_rows) > 0
        if layout.draw_data_row_lines and (not is_last_row or has_footers) and not item.get("hide_line_below", False):
            lw = s_thick_line_width if item.get("thick_line_below") else s_data_row_line_width
            c.setLineWidth(lw)
            
            # 读取布局全局 padding 设定
            line_pad = s_data_row_line_padding
            # 兼容个别数据行强制开启
            if item.get("padded_line_below", False):
                line_pad = c_pad
                
            c.line(x + line_pad, y, x + width - line_pad, y)

    # ── 底部行渲染（footer_rows — 流式引擎自适应）──
    if layout.footer_rows:
        # 1) 计算 footer 可用区域：数据行结束 y 到 表格底部(含底部留白)
        footer_top_y = y
        table_bottom_target = region.y - region.height + pad_y
        footer_avail_h = footer_top_y - table_bottom_target
        if footer_avail_h < font_size:
            footer_avail_h = font_size * len(layout.footer_rows) * 1.5

        # 按 height_ratio 比例分配剩余空间给每个 block
        total_hr = sum(ftr.height_ratio for ftr in layout.footer_rows)
        if total_hr <= 0:
            total_hr = len(layout.footer_rows)

        footer_w = width - c_pad * 2

        # 2) 逐 block 独立二分搜索最大可用字号
        #    避免 find_best_font_size 跨 region 流动导致 PASS1/PASS2 不一致
        footer_max_fs = font_size * 0.85
        footer_min_fs = 4.0
        footer_fs = footer_max_fs  # 从最大开始

        for ftr in layout.footer_rows:
            block_h = footer_avail_h * (ftr.height_ratio / total_hr)
            block = TextBlock(text=ftr.text)
            lo_f, hi_f = footer_min_fs, footer_max_fs
            for _ in range(15):
                mid_f = (lo_f + hi_f) / 2
                test_fc = FontConfig(
                    font_name=_FONT_NAME, font_name_bold=_FONT_NAME_BOLD,
                    font_size=mid_f, leading_ratio=1.15, h_scale=1.0,
                )
                test_region = FlowRect(x=0, y=block_h, width=footer_w, height=block_h)
                test_result = layout_flow_content([block], [test_region], test_fc)
                if test_result.overflow:
                    hi_f = mid_f
                else:
                    lo_f = mid_f
            # 该 block 的最大可用字号
            if lo_f < footer_fs:
                footer_fs = lo_f

        footer_fc = FontConfig(
            font_name=_FONT_NAME, font_name_bold=_FONT_NAME_BOLD,
            font_size=footer_fs, leading_ratio=1.15, h_scale=1.0,  # 不压缩
        )

        # 3) 逐 block 渲染 + 精确分隔线
        cur_footer_y = footer_top_y
        for fi, ftr in enumerate(layout.footer_rows):
            block = TextBlock(text=ftr.text)
            block_h = footer_avail_h * (ftr.height_ratio / total_hr)

            block_region = FlowRect(
                x=x + c_pad, y=cur_footer_y,
                width=footer_w, height=block_h,
            )
            block_result = layout_flow_content(
                [block], [block_region], footer_fc, canvas=c,
            )

            cur_footer_y -= block_h

            # 画分隔线
            if ftr.draw_line_below:
                flw = s_thick_line_width if ftr.thick_line_below else s_data_row_line_width
                c.setLineWidth(flw)
                c.line(x + s_data_row_line_padding, cur_footer_y,
                       x + width - s_data_row_line_padding, cur_footer_y)

        y = cur_footer_y

    y -= pad_y
    table_bottom = y

    # ── 外框 ──
    outer_lw = s_outer_border_lw
    c.setLineWidth(outer_lw)
    c.line(x, table_top, x, table_bottom)             
    c.line(x + width, table_top, x + width, table_bottom)  
    c.line(x, table_top, x + width, table_top)        
    c.line(x, table_bottom, x + width, table_bottom)  

    # ── 垂直列分隔线（一笔贯穿） ──
    if getattr(layout, 'draw_col_sep', True):
        start_y = col_sep_start_y if col_sep_start_y is not None else (last_header_line_y if last_header_line_y is not None else data_start_y)
        # col_sep_in_data=True: 竖线延伸至表格底部
        # col_sep_in_data=False: 竖线只在列头区内（即到 data_start_y）
        col_sep_end_y = table_bottom if getattr(layout, 'col_sep_in_data', True) else data_start_y
        if start_y > col_sep_end_y:
            c.setLineWidth(s_border_line_width)
            for cx in col_x_offsets[1:]:
                c.line(cx, start_y, cx, col_sep_end_y)

    return table_bottom

def render_net_volume(
    canvas: Canvas,
    region: TemplateRegion,
    data: dict,
):
    """
    Net Volume 渲染器（自适应字号）。

    在 region 指定的矩形内渲染净含量文字。
    字号自动适配区域高度，确保：
      - 字形完全在区域内（ascender 不超顶、descender 不超底）
      - 横向超宽时自动压缩（Tz 缩放）

    Args:
        canvas: ReportLab Canvas
        region: Net Volume 的 TemplateRegion
        data:   PLM 数据（需含 net_weight）
    """
    net_weight = data.get("net_weight", "")
    if not net_weight:
        return

    c = canvas

    # ── 自适应字号：基于真实字体视觉度量填满区域高度 ──
    # 净含量通常为数字+单位（如 725 g, 500 mL）
    # 它的实际视觉最高点是数字和大写字母的 Cap Height（大写高度），而不是字形的 ascent
    # 视觉最低点是带有 descender 的小写字母（如 g）底部
    face = pdfmetrics.getFont(_FONT_NAME_BOLD).face
    real_descent_ratio = abs(face.descent) / 1000.0

    # 视觉可见高度比例 = CapHeight比例 + 下沉比例
    visible_ratio = _CAP_H_RATIO + real_descent_ratio

    # 让可见高度完全等于区域高度，实现“上下用满”
    font_size = region.height / visible_ratio

    # baseline 定位：让 descender 底部恰好对齐区域底边
    descent_pt = font_size * real_descent_ratio
    y = region.bottom + descent_pt
    x = region.x

    # ── 横向压缩 ──
    text_w = pdfmetrics.stringWidth(net_weight, _FONT_NAME_BOLD, font_size)
    tz = min(100, int(region.width / text_w * 100)) if text_w > 0 else 100

    t = c.beginText(x, y)
    t.setFont(_FONT_NAME_BOLD, font_size)
    t._code.append(f'{tz} Tz')
    t.textOut(net_weight)
    t._code.append('100 Tz')
    c.drawText(t)


# ---------------------------------------------------------------------------
# render_logo — Logo
# ---------------------------------------------------------------------------

def render_logo(
    canvas: Canvas,
    region: TemplateRegion,
    logo_path: str,
):
    """
    Logo 渲染器。

    在 region 指定的矩形内绘制 logo 图片。

    Args:
        canvas:    ReportLab Canvas
        region:    Logo 的 TemplateRegion
        logo_path: Logo 文件路径
    """
    if not logo_path or not os.path.isfile(logo_path):
        return

    try:
        # region.y = 顶部（PDF坐标），region.bottom = 底部
        canvas.drawImage(
            logo_path,
            region.x, region.bottom,
            width=region.width, height=region.height,
            preserveAspectRatio=True, mask='auto',
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# render_eco_icons — 环保标
# ---------------------------------------------------------------------------

def render_eco_icons(
    canvas: Canvas,
    slots: list,
    data: dict,
    country_cfg: Optional[dict] = None,
):
    """
    环保标渲染器 —— 每个槽位独立渲染一个 PNG 图标。

    slots[i] 与 country_cfg["eco_icons"][i] 一一配对。
    如果槽位数 > 图标数，多余的槽位留空。
    如果图标数 > 槽位数，多余的图标被忽略。

    Args:
        canvas:      ReportLab Canvas
        slots:       List[TemplateRegion]，按从左到右排序
        data:        PLM 数据（预留给产品级别的环保标选择）
        country_cfg: 国家法规配置（含 eco_icons 文件名列表）
    """
    eco_icon_names = (country_cfg or {}).get("eco_icons", [])
    if not eco_icon_names or not slots:
        return

    # 定位 static/eco_icons/ 目录
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    eco_dir = os.path.join(_this_dir, "static", "eco_icons")

    # 逐槽渲染
    for i, slot in enumerate(slots):
        if i >= len(eco_icon_names):
            break  # 没有更多图标了

        path = os.path.join(eco_dir, eco_icon_names[i])
        if not os.path.exists(path):
            continue

        # 读取图标原始尺寸
        from PIL import Image as PILImage
        img = PILImage.open(path)
        img_w, img_h = img.width, img.height
        img.close()

        # 等比缩放到槽位内（fit 模式），移除 padding 以最大化显示
        avail_w = slot.width
        avail_h = slot.height
        if avail_w <= 0 or avail_h <= 0:
            continue

        aspect = img_w / img_h
        # 先尝试按高度填满
        draw_h = avail_h
        draw_w = draw_h * aspect
        # 如果宽度超出，改按宽度填满
        if draw_w > avail_w:
            draw_w = avail_w
            draw_h = draw_w / aspect

        # 在槽位中居中
        slot_bottom = slot.y - slot.height
        x = slot.x + (slot.width - draw_w) / 2
        y = slot_bottom + (slot.height - draw_h) / 2

        canvas.drawImage(
            path, x, y,
            width=draw_w, height=draw_h,
            preserveAspectRatio=True,
            mask='auto',
        )
        avail_h = slot.height
        if avail_w <= 0 or avail_h <= 0:
            continue

        aspect = img_w / img_h
        # 先尝试按高度填满
        draw_h = avail_h
        draw_w = draw_h * aspect
        # 如果宽度超出，改按宽度填满
        if draw_w > avail_w:
            draw_w = avail_w
            draw_h = draw_w / aspect

        # 在槽位中居中
        slot_bottom = slot.y - slot.height
        x = slot.x + (slot.width - draw_w) / 2
        y = slot_bottom + (slot.height - draw_h) / 2

        canvas.drawImage(
            path, x, y,
            width=draw_w, height=draw_h,
            preserveAspectRatio=True,
            mask='auto',
        )
