from render_pipeline import render_label
from label_renderer import pdf_to_png_base64
import base64
from country_config import get_country_config

AU_DATA = {
    "product_name_en": "MUSHROOM DARK SOY SAUCE",
    "target_country": "AU",
    "nutrition": {
        "serving_size": "15mL",
        "servings": "33",
        "table_data": [
            {"name": "Energy", "per_serving": "120 kJ", "nrv": "800 kJ"},
            {"name": "Protein", "per_serving": "1.0 g", "nrv": "6.7 g"},
            {"name": "Fat, total", "per_serving": "0 g", "nrv": "0 g"},
            {"name": " - saturated", "per_serving": "0 g", "nrv": "0 g", "is_sub": True},
            {"name": "Carbohydrate", "per_serving": "4.8 g", "nrv": "32.0 g"},
            {"name": " - sugars", "per_serving": "1.1 g", "nrv": "7.1 g", "is_sub": True},
            {"name": "Sodium", "per_serving": "1130 mg", "nrv": "7530 mg"},
        ]
    }
}

# The layout expects per_100g in the third column for AU. Let's fix the data keys.
for row in AU_DATA["nutrition"]["table_data"]:
    row["per_100g"] = row.pop("nrv")

ai_path = "0-需求文档/1-竖版标签/荷兰0草老/25500015414 500mL荷兰京东国际0草菇老抽小标签(50x120mm) 202510-02.ai"

cfg = get_country_config("AU")
pdf_bytes = render_label(ai_path, AU_DATA, cfg)
b64_str = pdf_to_png_base64(pdf_bytes, dpi=216)
if b64_str.startswith("data:image/png;base64,"):
    b64_str = b64_str.split(",", 1)[1]

out_path = "/Users/mulele/.gemini/antigravity/brain/b36d2247-0089-4009-ab00-8bcdb6fb289a/au_preview.png"
with open(out_path, "wb") as f:
    f.write(base64.b64decode(b64_str))

print("Saved AU preview to", out_path)
