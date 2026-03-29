from reportlab.pdfbase import pdfmetrics
import os
from nut_layouts import get_nut_layout, load_excel_config, get_data_row_template
from app import PLM_EXAMPLE_8

load_excel_config()
layout = get_nut_layout("CA")
tmpl = get_data_row_template("CA")

table_data_raw = PLM_EXAMPLE_8["nutrition"]["table_data"]
plm_by_name = {row.get("name", ""): row for row in table_data_raw}
table_data = []
for t_row in tmpl:
    name = t_row.get("name", "")
    merged_row = dict(t_row)
    if name in plm_by_name:
        for k, v in plm_by_name[name].items():
            if k not in merged_row or k in ("value", "nrv", "per_serving", "per_100g"):
                merged_row[k] = v
    table_data.append(merged_row)

font_size = 4.72
c_pad = font_size * 0.15
print("Evaluating string widths at 4.72pt using Helvetica:")

max_nrv_w = 0.0
max_name_w = 0.0

for item in table_data:
    val = str(item.get("nrv", ""))
    if val:
        w = pdfmetrics.stringWidth(val, "Helvetica", font_size)
        if w > max_nrv_w: max_nrv_w = w
    
    name = str(item.get("name", ""))
    if name:
        is_sub = item.get("is_sub", False)
        s_sub_indent = font_size * (layout.sub_indent / 10.0)
        indent = s_sub_indent if is_sub else c_pad
        display_val = layout.name_mapping.get(name.strip().lower(), name.strip()) if layout.name_mapping else name
        w = pdfmetrics.stringWidth(display_val, "Helvetica", font_size) + indent
        if w > max_name_w: max_name_w = w

print(f"Max NRV width: {max_nrv_w:.2f} pt (plus padding {c_pad*2:.2f} = {max_nrv_w + c_pad*2:.2f})")
print(f"Max Name width: {max_name_w:.2f} pt (plus padding {c_pad*2:.2f} = {max_name_w + c_pad*2:.2f})")
print(f"Total needed width: {max_nrv_w + max_name_w + c_pad*4:.2f} pt")
