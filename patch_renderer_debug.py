import sys
from unittest.mock import patch
import region_renderers

original_draw = region_renderers._draw_compressed_text

def mocked_draw(canvas, text, x, y, font, size, max_width, align, tz_override=None):
    if "* 5%" in str(text):
        print(f"DEBUG: FOOTNOTE DRAWN at y={y}, size={size}, tz={tz_override}")
    return original_draw(canvas, text, x, y, font, size, max_width, align, tz_override)

region_renderers._draw_compressed_text = mocked_draw

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

print(f"Region height: {cfg_tmpl.nut_table.height}")

pdf_bytes = render_label(cfg_tmpl, PLM_EXAMPLE_8, country_cfg, show_regions=True)

