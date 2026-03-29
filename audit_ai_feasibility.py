"""
P5 可行性审计脚本
验证三大核心假设：
  1. 能否精确定位营养表区域（排除正标上的无关文字）
  2. 字体名能否可靠映射到 Bold / Regular / Heavy 字重
  3. Y轴间距能否可靠计算行间距和 margin_top
"""
import pdfplumber
from collections import Counter

AI_PATH = "/Users/mulele/Projects/60-ai_tag/2-新的场景/小标签环保标识图/测试3.12/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01.ai"

with pdfplumber.open(AI_PATH) as pdf:
    page = pdf.pages[0]
    chars = page.chars
    words = page.extract_words(extra_attrs=["fontname", "size"])
    lines = page.lines
    rects = page.rects

    # ═══ 审计 1：营养表区域定位 ═══
    print("=" * 60)
    print("审计 1：营养表区域定位")
    print("=" * 60)

    # 找到 "Nutrition" 关键字的位置
    nut_words = [w for w in words if "utrition" in w["text"] or "alories" in w["text"]
                 or "aleur" in w["text"] or w["text"] == "Fat"]
    print(f"\n营养表关键词定位（找到 {len(nut_words)} 个）：")
    for w in nut_words:
        print(f"  '{w['text']:20s}' x0={w['x0']:8.2f}  top={w['top']:8.2f}  bottom={w['bottom']:8.2f}  font={w.get('fontname','?')}")

    # 检查整个页面 x 范围分布，看是否能按 x 区域分离正标和背标
    all_x0 = [w["x0"] for w in words]
    print(f"\n所有文字 x0 范围: [{min(all_x0):.1f}, {max(all_x0):.1f}]")
    # 统计 x0 分布（判断正标/背标是否有明显分界）
    x_bins = Counter()
    for x in all_x0:
        x_bins[int(x // 50) * 50] += 1
    print(f"x0 按 50pt 分桶分布: {dict(sorted(x_bins.items()))}")

    # ═══ 审计 2：字体名映射 ═══
    print("\n" + "=" * 60)
    print("审计 2：字体名 → 字重映射可靠性")
    print("=" * 60)
    font_sizes = {}
    for c in chars:
        fn = c.get("fontname", "?")
        sz = round(c.get("size", 0), 2)
        if fn not in font_sizes:
            font_sizes[fn] = set()
        font_sizes[fn].add(sz)

    print(f"\n发现 {len(font_sizes)} 种字体：")
    for fn, sizes in sorted(font_sizes.items()):
        # 推断字重
        weight = "?"
        fn_lower = fn.lower()
        if "black" in fn_lower or "heavy" in fn_lower:
            weight = "Heavy/Black"
        elif "bold" in fn_lower:
            weight = "Bold"
        elif "light" in fn_lower:
            weight = "Light"
        elif "-r" in fn_lower or "regular" in fn_lower:
            weight = "Regular"
        else:
            weight = "UNKNOWN ⚠️"
        sorted_sizes = sorted(sizes)
        print(f"  {fn:45s} → {weight:15s}  sizes={sorted_sizes}")

    # ═══ 审计 3：线段与间距计算 ═══
    print("\n" + "=" * 60)
    print("审计 3：线段属性与间距可靠性")
    print("=" * 60)

    # 只看营养表区域内的线（x0 > 150 应该是背标区域）
    nut_lines = [l for l in lines if l["x0"] > 140]
    print(f"\n营养表区域线段（x0>140）：共 {len(nut_lines)} 条")

    lw_counter = Counter()
    for l in nut_lines:
        lw = round(l["linewidth"], 3)
        lw_counter[lw] += 1
        span = "半宽" if (l["x1"] - l["x0"]) < 60 else "全宽"
        print(f"  y={l['top']:8.2f}  x=[{l['x0']:.1f}, {l['x1']:.1f}]  linewidth={lw:.3f}  {span}")

    print(f"\n线宽频率统计: {dict(lw_counter)}")
    if len(lw_counter) >= 2:
        sorted_lws = sorted(lw_counter.keys())
        base_lw = sorted_lws[0]
        thick_lw = sorted_lws[-1]
        print(f"  基准线宽(最细) = {base_lw}")
        print(f"  粗线线宽(最粗) = {thick_lw}")
        print(f"  粗细比 = {thick_lw / base_lw:.2f}x")

    # ═══ 审计 4：行间距逆推 ═══
    print("\n" + "=" * 60)
    print("审计 4：Y轴行间距逆推验证")
    print("=" * 60)

    # 取营养表区域内的文字（x0 > 140），按 top 排序
    nut_chars_by_word = [w for w in words if w["x0"] > 140]
    nut_chars_by_word.sort(key=lambda w: w["top"])

    # 按 top 做聚类（同一行的 top 应相近）
    row_groups = []
    current_group = []
    last_top = -99
    for w in nut_chars_by_word:
        if abs(w["top"] - last_top) > 1.5:  # 1.5pt 分界
            if current_group:
                row_groups.append(current_group)
            current_group = [w]
        else:
            current_group.append(w)
        last_top = w["top"]
    if current_group:
        row_groups.append(current_group)

    print(f"\n按 Y 聚类得到 {len(row_groups)} 个文字行：")
    prev_bottom = None
    for i, group in enumerate(row_groups):
        text = " ".join(w["text"] for w in group)
        top = min(w["top"] for w in group)
        bottom = max(w["bottom"] for w in group)
        font = group[0].get("fontname", "?")
        sizes = set(round(w.get("size", w["bottom"] - w["top"]), 2) for w in group)
        gap = f"gap={top - prev_bottom:.2f}" if prev_bottom else "---"
        prev_bottom = bottom
        print(f"  Row {i:2d}: top={top:7.2f} btm={bottom:7.2f} {gap:12s}  "
              f"size={sorted(sizes)}  '{text[:60]}'")
