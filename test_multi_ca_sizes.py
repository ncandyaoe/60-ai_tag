import os
import fitz
from reportlab.lib.units import mm
from template_extractor import TemplateConfig, TemplateRegion
from render_pipeline import render_label

PLM_EXAMPLE_8 = {
    "product_name_en": "DELICIOUS LIGHT SOY SAUCE",
    "product_name_cn": "鲜味生抽",
    "net_weight": "500 mL",
    "ingredients": "Water, Soybeans, Salt, Wheat, Monosodium glutamate, Wheat flour, Glucose-fructose, Caramel, Rice, Disodium 5'-inosinate, Disodium 5'-ribonucleotide, Potassium sorbate, Sucralose.\nContains: Soybeans, Wheat.",
    "allergens": "Eau, Soja, Sel, Blé, Glutamate monosodique, Farine de blé, Fructose-glucose, Couleur caramel, Riz, 5'-inosinate disodique, 5'-ribonucléotide disodique, Sorbate de potassium, Sucralose.\nContient: Soja, Blé.",
    "storage": "Best before: see the package/Meilleur avant: voir emballage\nPlease keep it in cool and dry place. Tightly close lid after use and keep refrigerated.\nVeuillez le conserver dans un endroit frais et sec. Bien fermer le couvercle après utilisation et conserver au réfrigérateur.",
    "target_country": "CA",
    "nutrition": {
        "serving_size": "1 tbsp (18 g)",
        "serving_size_fr": "1 cuillère à soupe (18 g)",
        "servings_per_package": "",
        "table_data": [
            {"name": "Fat / Lipides", "value": "1 g", "nrv": "1 %", "heavy": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Saturated / saturées", "value": "0 g", "nrv": "0 %", "is_sub": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "+ Trans / trans", "value": "0 g", "nrv": "", "is_sub": True, "padded_line_below": True, "height_ratio": 0.8},
            {"name": "Carbohydrate / Glucides", "value": "3 g", "nrv": "", "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Fibre / Fibres", "value": "1 g", "nrv": "4 %", "is_sub": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Sugars / Sucres", "value": "2 g", "nrv": "2 %", "is_sub": True, "padded_line_below": True, "height_ratio": 0.8},
            {"name": "Protein / Protéines", "value": "2 g", "nrv": "", "heavy": True, "padded_line_below": True},
            {"name": "Cholesterol / Cholestérol", "value": "0 mg", "nrv": "", "heavy": True, "padded_line_below": True},
            {"name": "Sodium", "value": "890 mg", "nrv": "39 %", "heavy": True, "thick_line_below": True},
            {"name": "Potassium", "value": "100 mg", "nrv": "3 %"},
            {"name": "Calcium", "value": "10 mg", "nrv": "1 %"},
            {"name": "Iron / Fer", "value": "0.2 mg", "nrv": "1 %", "thick_line_below": True},
            {"name": "* 5% or less is a little, 15% or more is a lot", "bold": False, "hide_line_below": True, "height_ratio": 0.72, "margin_top": 2.0},
            {"name": "* 5% ou moins c'est peu, 15% ou plus c'est beaucoup", "bold": False, "hide_line_below": True, "height_ratio": 0.72}
        ]
    }
}


def build_dynamic_config(w_mm, h_mm):
    """Dynamically builds a reasonable TemplateConfig for a given size."""
    LABEL_W = w_mm * mm
    LABEL_H = h_mm * mm
    MARGIN = 2 * mm

    left = MARGIN
    right = LABEL_W - MARGIN
    top = LABEL_H - MARGIN
    bottom = MARGIN
    
    content_w = right - left
    
    # If wide, side-by-side (content left, nut right)
    # If tall, top-bottom (content top, nut bottom)
    if w_mm > h_mm * 0.8:  # Wide or Square-ish
        nut_w = min(content_w * 0.5, 50 * mm)  # Nut takes up to 50mm or 50%
        left_col_w = content_w - nut_w - 2 * mm
        
        nut_x = left + left_col_w + 2 * mm
        
        nut_table = TemplateRegion(x=nut_x, y=top, width=nut_w, height=top - bottom)
        content_rects = [TemplateRegion(x=left, y=top - 15*mm, width=left_col_w, height=top - bottom - 15*mm)]
        title_rects = [TemplateRegion(x=left, y=top, width=left_col_w, height=15*mm)]
        net_volume = TemplateRegion(x=left, y=bottom + 5*mm, width=left_col_w, height=5*mm)
    else:  # Tall
        nut_h = min((top - bottom) * 0.5, 60 * mm)
        nut_y = bottom + nut_h
        
        nut_table = TemplateRegion(x=left, y=nut_y, width=content_w, height=nut_h)
        title_rects = [TemplateRegion(x=left, y=top, width=content_w, height=15*mm)]
        content_rects = [TemplateRegion(x=left, y=top - 15*mm, width=content_w, height=top - bottom - nut_h - 15*mm)]
        net_volume = TemplateRegion(x=left, y=bottom + 5*mm, width=content_w, height=5*mm)

    return TemplateConfig(
        page_width=LABEL_W,
        page_height=LABEL_H,
        source_file=f"dynamic_{w_mm}x{h_mm}",
        title_rects=title_rects,
        content_rects=content_rects,
        nut_table=nut_table,
        net_volume=net_volume,
        logo=None,
    )

sizes = [
    (100, 70, "wide"),
    (70, 100, "tall"),
    (43, 28, "extra_small"), 
    (60, 60, "square")
]

for w, h, name in sizes:
    tmpl = build_dynamic_config(w, h)
    pdf_bytes = render_label(tmpl, PLM_EXAMPLE_8, None)
    
    pdf_path = f"test_ca_{name}.pdf"
    png_path = f"test_ca_{name}.png"
    
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=300)
    pix.save(png_path)
    print(f"Rendered: {png_path} ({w}x{h}mm)")
