import fitz
import sys

def check_pages(ai_path):
    print(f"Checking {ai_path}")
    doc = fitz.open(ai_path)
    print(f"Total Pages (Artboards): {len(doc)}")
    for i in range(len(doc)):
        page = doc[i]
        print(f"  Page {i}: rect={page.rect}, mediabox={page.mediabox}")
        drawings = page.get_drawings()
        print(f"  Drawings count: {len(drawings)}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_pages(sys.argv[1])
