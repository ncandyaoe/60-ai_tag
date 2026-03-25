from app import PLM_EXAMPLE_1
from render_pipeline import render_label
from country_config import get_country_config
from template_classic import build_classic_config
from label_renderer import pdf_to_png_base64
import base64

def main():
    cc = "AU"
    data = PLM_EXAMPLE_1
    country_cfg = get_country_config(cc)
    classic_tmpl = build_classic_config(data)
    pdf_bytes = render_label(classic_tmpl, data, country_cfg)
    
    b64_str = pdf_to_png_base64(pdf_bytes, dpi=216)
    if b64_str.startswith("data:image/png;base64,"):
        b64_str = b64_str.split(",", 1)[1]
        
    png_data = base64.b64decode(b64_str)
    out_path = "/Users/mulele/.gemini/antigravity/brain/98f900e7-0c16-4724-8ced-24a7fcaffdc1/preview_classic.png"
    with open(out_path, "wb") as f:
        f.write(png_data)
    print("PNG Saved:", out_path)

if __name__ == "__main__":
    main()
