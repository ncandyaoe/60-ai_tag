from render_pipeline import render_label
from app import PLM_EXAMPLE_8
from nut_layouts import CA_LAYOUT_CONFIG if hasattr('nut_layouts', 'CA_LAYOUT_CONFIG') else None # Wait, layout is loaded by template

import fitz

pdf_bytes = render_label("classic", PLM_EXAMPLE_8, None)
with open("test_ca_heavy.pdf", "wb") as f:
    f.write(pdf_bytes)

doc = fitz.open(stream=pdf_bytes, filetype="pdf")
pix = doc[0].get_pixmap(dpi=300)
pix.save("test_ca_heavy.png")
print("Rendered successfully")
