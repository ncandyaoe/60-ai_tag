import os
import fitz
from app import PLM_EXAMPLE_8
from template_extractor import extract_template_regions
from render_pipeline import render_label
from country_config import get_country_config

country_cfg = get_country_config("CA")
ai_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "0-需求文档", "1-竖版标签", "荷兰0草老", "多环保标-25500015414 500mL荷兰京东国际0草菇老抽小标签(50x120mm) 202510-02.ai")
cfg_tmpl = extract_template_regions(ai_path)
cfg_tmpl.content_type = "multilingual"

pdf_bytes = render_label(cfg_tmpl, PLM_EXAMPLE_8, country_cfg, show_regions=True)

with open("test_exact.pdf", "wb") as f:
    f.write(pdf_bytes)

doc = fitz.open(stream=pdf_bytes, filetype="pdf")
text = doc[0].get_text()
print("--- FOOTNOTE PRESENT IN PDF: ---")
print("* 5%" in text)
