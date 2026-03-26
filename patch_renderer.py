import re

with open('region_renderers.py', 'r') as f:
    content = f.read()

# Find the start of the function
start_idx = content.find('def render_nutrition(')

# Find the end of the function (the next def or end of file)
next_def_idx = content.find('\ndef ', start_idx + 1)
if next_def_idx == -1:
    next_def_idx = len(content)

old_func = content[start_idx:next_def_idx]

new_func = """def render_nutrition(
    canvas,
    region,
    data: dict,
    country_cfg: dict,
    nut_table_type: str = "",
    country_code: str = "DEFAULT",
) -> float:
    from reportlab.pdfbase import pdfmetrics
    from nut_layouts import get_nut_layout
    layout = get_nut_layout(country_code, override_type=nut_table_type or None)

    c = canvas
    x = region.x
    width = region.width
    nutrition = data.get("nutrition") or {}
    table_data = nutrition.get("table_data") or []

    # ── 计算列绝对宽度和 X 偏移 ──
    col_widths = [col.width_ratio * width for col in layout.columns]
    col_x_offsets = []
    curr_x = x
    for w in col_widths:
        col_x_offsets.append(curr_x)
        curr_x += w

    # ── PASS 1: 计算标题区产生的最严苛横向压缩比 (min_tz) ──
    min_tz = 1.0
    base_fs = 10.0
    for hdr in layout.header_rows:
        local_fs = base_fs * getattr(hdr, "font_ratio", 1.0)
        if hdr.span_full:
            text = hdr.cells[0]
            if hdr.template:
                text = _format_template_text(text, nutrition)
            if hdr.bold and nutrition.get("nut_title"):
                text = nutrition["nut_title"]
            elif not hdr.bold and hdr.span_full and nutrition.get("nut_subtitle"):
                text = nutrition["nut_subtitle"]
            req_w = pdfmetrics.stringWidth(text, _FONT_NAME_BOLD if hdr.bold else _FONT_NAME, local_fs)
            lpad = 4 if hdr.fill_color else 2
            avail_w = width - lpad * 2
            if req_w > 0 and avail_w / req_w < min_tz:
                min_tz = avail_w / req_w
        else:
            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(col_widths) or not cell_text:
                    continue
                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                lines = cell_text.split("\\n")
                for line_text in lines:
                    req_w = pdfmetrics.stringWidth(line_text, _FONT_NAME_BOLD if hdr.bold else _FONT_NAME, local_fs)
                    if req_w > 0 and col_widths[ci] / req_w < min_tz:
                        min_tz = col_widths[ci] / req_w

    # PASS 1.5: 计算数据行 min_tz
    for item in table_data:
        is_sub = item.get("is_sub", False)
        for ci, col in enumerate(layout.columns):
            val = str(item.get(col.key, ""))
            if not val:
                continue
            if col.key == "name":
                indent = layout.sub_indent if is_sub else 2
                if layout.name_mapping and (val.startswith(" ") or val.startswith("-")):
                    indent = 8
                display_val = layout.name_mapping.get(val.strip().lower(), val.strip()) if layout.name_mapping else val
                req_w = pdfmetrics.stringWidth(display_val, _FONT_NAME, base_fs)
                avail_w = col_widths[ci] - indent - 2
                if req_w > 0 and avail_w / req_w < min_tz:
                    min_tz = avail_w / req_w
            else:
                req_w = pdfmetrics.stringWidth(val, _FONT_NAME, base_fs)
                if req_w > 0 and (col_widths[ci] - 2) / req_w < min_tz:
                    min_tz = (col_widths[ci] - 2) / req_w

    global_tz = min(100.0, min_tz * 100)

    # ── PASS 2: 使用 global_tz 统一渲染 ──
    n_data_rows = len(table_data)
    total_rows_weighted = layout.n_header_rows + n_data_rows

    if total_rows_weighted == 0:
        return region.y - region.height

    row_h = region.height / total_rows_weighted
    padding = 4.0
    font_size = row_h - padding

    def _baseline_y(rect_y, rect_h, f_size):
        cap_h = f_size * 0.72
        descent_p = f_size * 0.20
        box = cap_h + descent_p
        return rect_y - (rect_h - box) / 2 - cap_h

    y = region.y
    table_top = y
    col_sep_start_y = None   

    # ── 标题区渲染 ──
    for hdr in layout.header_rows:
        hdr_h = row_h * hdr.height_ratio
        cell_h = hdr_h * 2 if hdr.multi_line else hdr_h

        if hdr.fill_color:
            r_fill, g_fill, b_fill = hdr.fill_color
            from reportlab.lib.colors import Color as _Color
            c.setFillColor(_Color(r_fill, g_fill, b_fill))
            c.rect(x, y - cell_h, width, cell_h, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)

        txt_r, txt_g, txt_b = hdr.text_color
        c.setFillColorRGB(txt_r, txt_g, txt_b)

        if hdr.multi_line:
            local_fs = font_size * getattr(hdr, "font_ratio", 1.0)
            leading = local_fs * 0.85
            cap_h = local_fs * 0.72
            descent = local_fs * 0.20
            block_h = cap_h + leading + descent
            block_top = y - (cell_h - block_h) / 2
            line1_y = block_top - cap_h
            line2_y = line1_y - leading

            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(col_widths) or not cell_text:
                    continue
                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                lines = cell_text.split("\\n")
                font = _FONT_NAME_BOLD if hdr.bold else _FONT_NAME
                _draw_compressed_text(
                    c, lines[0], col_x_offsets[ci], line1_y,
                    font, local_fs, col_widths[ci], align=layout.columns[ci].align,
                    tz_override=global_tz,
                )
                if len(lines) > 1:
                    _draw_compressed_text(
                        c, lines[1], col_x_offsets[ci], line2_y,
                        font, local_fs, col_widths[ci], align=layout.columns[ci].align,
                        tz_override=global_tz,
                    )
            y -= cell_h
        elif hdr.span_full:
            text = hdr.cells[0]
            if hdr.template:
                text = _format_template_text(text, nutrition)
            if hdr.bold and nutrition.get("nut_title"):
                text = nutrition["nut_title"]
            elif not hdr.bold and hdr.span_full and nutrition.get("nut_subtitle"):
                text = nutrition["nut_subtitle"]
            
            local_fs = font_size * getattr(hdr, "font_ratio", 1.0)
            text_y = _baseline_y(y, cell_h, local_fs)
            font = _FONT_NAME_BOLD if hdr.bold else _FONT_NAME
            lpad = 4 if hdr.fill_color else 2
            _draw_compressed_text(
                c, text, x + lpad, text_y,
                font, local_fs, width - lpad * 2,
                align=hdr.align, tz_override=global_tz,
            )
            y -= cell_h
        else:
            local_fs = font_size * getattr(hdr, "font_ratio", 1.0)
            text_y = _baseline_y(y, cell_h, local_fs)
            for ci, cell_text in enumerate(hdr.cells):
                if ci >= len(col_widths) or not cell_text:
                    continue
                if hdr.template:
                    cell_text = _format_template_text(cell_text, nutrition)
                font = _FONT_NAME_BOLD if hdr.bold else _FONT_NAME
                _draw_compressed_text(
                    c, cell_text, col_x_offsets[ci], text_y,
                    font, local_fs, col_widths[ci], align=layout.columns[ci].align,
                    tz_override=global_tz,
                )
            y -= cell_h

        c.setFillColorRGB(0, 0, 0)

        if hdr.draw_line_below:
            lw = hdr.line_width_below if hdr.line_width_below > 0 else layout.header_line_width
            c.setLineWidth(lw)
            c.line(x, y, x + width, y)

        if col_sep_start_y is None and not hdr.span_full:
            col_sep_start_y = y + cell_h

    # ── 数据行渲染 ──
    for item in table_data:
        is_sub = item.get("is_sub", False)
        text_y = _baseline_y(y, row_h, font_size)

        for ci, col in enumerate(layout.columns):
            val = str(item.get(col.key, ""))
            if not val:
                continue

            if col.key == "name":
                indent = layout.sub_indent if is_sub else 2
                if layout.name_mapping and (val.startswith(" ") or val.startswith("-")):
                    indent = 8
                display_val = layout.name_mapping.get(val.strip().lower(), val.strip()) if layout.name_mapping else val
                max_w = col_widths[ci] - indent - 2
                _draw_compressed_text(
                    c, display_val, col_x_offsets[ci] + indent, text_y,
                    _FONT_NAME, font_size, max_w,
                    align=col.align, tz_override=global_tz,
                )
            else:
                _draw_compressed_text(
                    c, val, col_x_offsets[ci], text_y,
                    _FONT_NAME, font_size, col_widths[ci] - 2,
                    align=col.align, tz_override=global_tz,
                )

        y -= row_h
        if layout.draw_data_row_lines:
            c.setLineWidth(layout.data_row_line_width)
            c.line(x, y, x + width, y)

    table_bottom = y

    # ── 外框 ──
    c.setLineWidth(layout.border_line_width)
    c.line(x, table_top, x, table_bottom)             
    c.line(x + width, table_top, x + width, table_bottom)  
    c.line(x, table_top, x + width, table_top)        
    c.line(x, table_bottom, x + width, table_bottom)  

    # ── 垂直列分隔线 ──
    if col_sep_start_y is not None:
        c.setLineWidth(layout.border_line_width)
        for cx in col_x_offsets[1:]:
            c.line(cx, col_sep_start_y, cx, table_bottom)

    return table_bottom
"""

content = content.replace(old_func, new_func)

with open('region_renderers.py', 'w') as f:
    f.write(content)

print("Patch applied.")
