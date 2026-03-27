from render_pipeline import render_label
from template_classic import build_classic_config
from app import PLM_EXAMPLE_8
import fitz

tmpl = build_classic_config(PLM_EXAMPLE_8)
pdf_bytes = render_label(tmpl, PLM_EXAMPLE_8, None)
with open("test_ca_heavy.pdf", "wb") as f:
    f.write(pdf_bytes)

doc = fitz.open(stream=pdf_bytes, filetype="pdf")
pix = doc[0].get_pixmap(dpi=300)
pix.save("test_ca_heavy.png")
print("Rendered successfully to test_ca_heavy.png")
