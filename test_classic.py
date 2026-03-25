from app import PLM_EXAMPLE_1
from render_pipeline import render_label
from country_config import get_country_config
from template_classic import build_classic_config

def main():
    try:
        cc = "AU"
        data = PLM_EXAMPLE_1
        country_cfg = get_country_config(cc)
        classic_tmpl = build_classic_config(data)
        pdf_bytes = render_label(classic_tmpl, data, country_cfg)
        with open("test_classic.pdf", "wb") as f:
            f.write(pdf_bytes)
        print("Success, saved test_classic.pdf")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
