import pdfplumber

ai_path = "/Users/mulele/Projects/60-ai_tag/2-新的场景/小标签环保标识图/测试3.12/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01.ai"

with pdfplumber.open(ai_path) as pdf:
    # 营养表经常在具体的某一页
    for page_num, page in enumerate(pdf.pages):
        chars = page.chars
        print(f"--- Page {page_num} --- Found {len(chars)} characters.")
        fonts_seen = set()
        for c in chars:
            key = (c.get('fontname'), round(c.get('size', 0), 2))
            if key not in fonts_seen:
                fonts_seen.add(key)
                print(f"Font: {c.get('fontname')}, Size: {c.get('size')}, Example char: '{c.get('text')}'")
        
        # also print first 10 words to see what text is on the page
        words = page.extract_words()
        print("Initial words:", [w['text'] for w in words[:10]])
