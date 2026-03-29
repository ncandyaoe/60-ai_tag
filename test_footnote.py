import pymupdf
from render_pipeline import render_label
from template_classic import build_classic_config
from app import PLM_EXAMPLE_8

tmpl = build_classic_config(PLM_EXAMPLE_8)
pdf_bytes = render_label(tmpl, PLM_EXAMPLE_8, None)

with open("test_footnote.pdf", "wb") as f:
    f.write(pdf_bytes)

doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
text = doc[0].get_text()
print("--- FOOTNOTE PRESENT IN PDF: ---")
print("* 5%" in text)
print("--- LAST 500 CHARACTERS: ---")
print(text[-500:])
