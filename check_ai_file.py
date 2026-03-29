import pdfplumber
import sys

ai_path = "/Users/mulele/Projects/60-ai_tag/2-新的场景/小标签环保标识图/测试3.12/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01.ai"

try:
    with pdfplumber.open(ai_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        lines = page.lines
        bboxes = page.rects
        
        print(f"Total pages: {len(pdf.pages)}")
        print(f"Extracted {len(words)} words.")
        if words:
            print("Sample words:", words[:10])
        print(f"Extracted {len(lines)} lines/strokes.")
        if lines:
            print("Sample lines:", lines[:5])
            
except Exception as e:
    print(f"Error reading file: {e}")
