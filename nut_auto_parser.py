#!/usr/bin/env python3
"""
nut_auto_parser.py — AI 智能营养表模板解析器
============================================

从设计师原生的 .ai 文件（含 PDF 兼容流）中自动逆向提取：
  - 字号比率 (font_ratio)
  - 线宽倍率 (line_width_below)
  - 行间距 (margin_top / height_ratio)
  - 字体映射 (font_override)

输出可直接粘贴进 nut_layouts.py 的 NutHeaderRow / NutritionLayout 代码。

Usage:
    python nut_auto_parser.py <path_to_ai_file>
"""

import sys
import pdfplumber
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import List, Optional

# ══════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════

# 锚定词：用于定位营养表左上角
ANCHOR_KEYWORDS = ["Nutrition", "Valeur", "Calories", "Fat", "Sodium"]

# 字体字重映射规则（去掉 PDF 子集化前缀后匹配）
WEIGHT_RULES = [
    ("Black",    "heavy"),
    ("Heavy",    "heavy"),
    ("BoldMT",   "bold"),
    ("Bold",     "bold"),
    ("-B",       "bold"),
    ("-R",       "regular"),
    ("MT",       "regular"),   # ArialMT = Arial Regular
    ("Light",    "light"),
    ("Regular",  "regular"),
]


# ══════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════

@dataclass
class ParsedTextRow:
    """从 .ai 中提取的一行文字"""
    y_top: float
    y_bottom: float
    text: str
    font_name: str          # 去掉 PDF 前缀后的字体名
    font_weight: str        # heavy / bold / regular / light
    font_size: float        # 主要字号（行内最大字号）
    all_sizes: list         # 行内所有字号
    x_left: float
    x_right: float


@dataclass
class ParsedLine:
    """从 .ai 中提取的一条横线"""
    y: float
    x_left: float
    x_right: float
    linewidth: float
    is_full_width: bool     # 跨越整个表格宽度


@dataclass
class NutTableAnalysis:
    """营养表完整解析结果"""
    # 基准量
    base_font_size: float       # 基准字号（数据行的常用字号）
    base_line_width: float      # 基准线宽（最细线）

    # 表格边界
    bbox_x0: float
    bbox_y0: float              # top
    bbox_x1: float
    bbox_y1: float              # bottom

    # 解析出的行和线
    rows: List[ParsedTextRow] = field(default_factory=list)
    lines: List[ParsedLine] = field(default_factory=list)


# ══════════════════════════════════════════════════════════
# 核心函数
# ══════════════════════════════════════════════════════════

def strip_pdf_prefix(fontname: str) -> str:
    """去掉 PDF 子集化前缀 (如 JKPZLV+Arial-Black → Arial-Black)"""
    if "+" in fontname:
        return fontname.split("+", 1)[1]
    return fontname


def infer_weight(fontname: str) -> str:
    """根据字体名推断字重"""
    clean = strip_pdf_prefix(fontname)
    for pattern, weight in WEIGHT_RULES:
        if pattern in clean:
            return weight
    return "regular"  # 默认作 regular


def find_nut_table_page(pdf) -> Optional[int]:
    """扫描所有页面，找到含有营养表的页"""
    for i, page in enumerate(pdf.pages):
        words = page.extract_words()
        texts = [w["text"] for w in words]
        if any("utrition" in t for t in texts):
            return i
    return None


def extract_nut_bbox(words, lines_raw, page_width, page_height):
    """
    通过锚定词定位营养表区域的 BoundingBox。
    策略：找到 "Nutrition" 的 x0 作为左边界，用最大的 x1 作为右边界。
    """
    # 找 "Nutrition" 锚点
    anchor = None
    for w in words:
        if "utrition" in w["text"]:
            anchor = w
            break

    if not anchor:
        print("⚠️  未找到 'Nutrition' 锚定词，将使用页面右半边")
        return page_width / 2, 0, page_width, page_height

    x_left = anchor["x0"] - 2  # 留点余量

    # 在 x_left 右侧的所有元素中找最大 x1 和最大 bottom
    relevant_words = [w for w in words if w["x0"] >= x_left - 5]
    if not relevant_words:
        return x_left, 0, page_width, page_height

    x_right = max(w["x1"] for w in relevant_words) + 2
    y_top = min(w["top"] for w in relevant_words) - 2
    y_bottom = max(w["bottom"] for w in relevant_words) + 5

    # 用线段进一步扩展底部（底部粗线可能在最后一个文字之下）
    nut_lines = [l for l in lines_raw if l["x0"] >= x_left - 5]
    if nut_lines:
        line_bottom = max(l["top"] for l in nut_lines) + 2
        y_bottom = max(y_bottom, line_bottom)

    return x_left, y_top, x_right, y_bottom


def cluster_words_to_rows(words, threshold=1.5) -> List[dict]:
    """将 words 按 Y 坐标聚类为行"""
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: w["top"])
    rows = []
    current_row = [words_sorted[0]]

    for w in words_sorted[1:]:
        if abs(w["top"] - current_row[0]["top"]) <= threshold:
            current_row.append(w)
        else:
            rows.append(current_row)
            current_row = [w]
    rows.append(current_row)
    return rows


def analyze_ai_file(ai_path: str) -> NutTableAnalysis:
    """主解析入口"""
    with pdfplumber.open(ai_path) as pdf:
        page_idx = find_nut_table_page(pdf)
        if page_idx is None:
            raise RuntimeError("在 AI 文件中未找到含有 'Nutrition' 的页面")

        page = pdf.pages[page_idx]
        all_words = page.extract_words(extra_attrs=["fontname", "size"])
        all_chars = page.chars
        all_lines = page.lines

        pw, ph = float(page.width), float(page.height)

        # Step 1: 定位营养表 BBox
        bbox = extract_nut_bbox(all_words, all_lines, pw, ph)
        bx0, by0, bx1, by1 = bbox
        print(f"📐 营养表 BBox: x=[{bx0:.1f}, {bx1:.1f}]  y=[{by0:.1f}, {by1:.1f}]")

        # Step 2: 过滤出营养表区域内的元素
        nut_words = [w for w in all_words if w["x0"] >= bx0 - 2 and w["x1"] <= bx1 + 2
                     and w["top"] >= by0 - 2 and w["bottom"] <= by1 + 2]
        nut_chars = [c for c in all_chars if c["x0"] >= bx0 - 2 and c["x1"] <= bx1 + 2
                     and c["top"] >= by0 - 2 and c["bottom"] <= by1 + 2]
        nut_lines_raw = [l for l in all_lines if l["x0"] >= bx0 - 5]

        # Step 3: 解析字号基准
        size_counter = Counter()
        for c in nut_chars:
            sz = round(c.get("size", 0), 2)
            if sz > 0:
                size_counter[sz] += 1

        # 基准字号 = 出现最多的字号
        base_fs = size_counter.most_common(1)[0][0] if size_counter else 6.0
        print(f"📏 基准字号 (base_fs): {base_fs} pt")
        print(f"   字号分布: {dict(size_counter.most_common(8))}")

        # Step 4: 解析线宽基准
        lw_counter = Counter()
        for l in nut_lines_raw:
            lw = round(l["linewidth"], 3)
            lw_counter[lw] += 1
        base_lw = min(lw_counter.keys()) if lw_counter else 0.5
        print(f"📏 基准线宽 (base_lw): {base_lw} pt")
        print(f"   线宽分布: {dict(lw_counter)}")

        # 表格总宽度（用于判断半宽/全宽线）
        table_width = bx1 - bx0

        # Step 5: 聚类文字行
        word_rows = cluster_words_to_rows(nut_words, threshold=1.8)

        parsed_rows = []
        for row_words in word_rows:
            text = " ".join(w["text"] for w in row_words)
            y_top = min(w["top"] for w in row_words)
            y_bottom = max(w["bottom"] for w in row_words)
            x_left = min(w["x0"] for w in row_words)
            x_right = max(w["x1"] for w in row_words)

            # 获取行内的所有字号和字体
            row_chars = [c for c in nut_chars
                         if c["top"] >= y_top - 0.5 and c["bottom"] <= y_bottom + 0.5]
            sizes = sorted(set(round(c.get("size", 0), 2) for c in row_chars if c.get("size", 0) > 0))
            main_size = max(sizes) if sizes else base_fs

            # 字体 - 取出现频率最高的
            font_counter = Counter(strip_pdf_prefix(c.get("fontname", "")) for c in row_chars)
            main_font = font_counter.most_common(1)[0][0] if font_counter else "Unknown"
            weight = infer_weight(main_font)

            parsed_rows.append(ParsedTextRow(
                y_top=y_top, y_bottom=y_bottom,
                text=text, font_name=main_font,
                font_weight=weight, font_size=main_size,
                all_sizes=sizes, x_left=x_left, x_right=x_right
            ))

        # Step 6: 解析线段
        parsed_lines = []
        for l in nut_lines_raw:
            lw = round(l["linewidth"], 3)
            span = (l["x1"] - l["x0"]) / table_width
            parsed_lines.append(ParsedLine(
                y=l["top"], x_left=l["x0"], x_right=l["x1"],
                linewidth=lw, is_full_width=(span > 0.8)
            ))
        parsed_lines.sort(key=lambda l: l.y)

        return NutTableAnalysis(
            base_font_size=base_fs,
            base_line_width=base_lw,
            bbox_x0=bx0, bbox_y0=by0,
            bbox_x1=bx1, bbox_y1=by1,
            rows=parsed_rows,
            lines=parsed_lines,
        )


# ══════════════════════════════════════════════════════════
# 报告生成
# ══════════════════════════════════════════════════════════

def generate_report(analysis: NutTableAnalysis):
    """生成人类可读的解析报告 + 可粘贴代码"""
    base_fs = analysis.base_font_size
    base_lw = analysis.base_line_width
    rows = analysis.rows
    lines = analysis.lines

    print("\n" + "═" * 70)
    print("  营养表自动解析报告")
    print("═" * 70)

    # ── 行详情 ──
    print(f"\n{'─'*70}")
    print(f"{'Row':>4}  {'Text':40s}  {'Size':>5}  {'Ratio':>5}  {'Weight':>8}  {'Gap':>6}")
    print(f"{'─'*70}")

    prev_bottom = None
    for i, row in enumerate(rows):
        ratio = row.font_size / base_fs
        gap_str = "---"
        if prev_bottom is not None:
            gap = row.y_top - prev_bottom
            gap_str = f"{gap:+.2f}"
        prev_bottom = row.y_bottom

        text_short = row.text[:38] + ".." if len(row.text) > 40 else row.text
        print(f"  {i:2d}  {text_short:40s}  {row.font_size:5.2f}  {ratio:5.2f}  "
              f"{row.font_weight:>8s}  {gap_str:>6s}")
        if len(row.all_sizes) > 1:
            sub_ratios = [f"{s / base_fs:.2f}" for s in row.all_sizes]
            print(f"      └─ 行内多字号: {row.all_sizes}  ratios={sub_ratios}")

    # ── 线段详情 ──
    print(f"\n{'─'*70}")
    print(f"{'Line':>5}  {'Y':>8}  {'Width':>7}  {'Ratio':>6}  {'Span':>8}")
    print(f"{'─'*70}")

    for i, line in enumerate(lines):
        ratio = line.linewidth / base_lw
        span_str = "全宽" if line.is_full_width else "半宽"
        print(f"  {i:3d}  {line.y:8.2f}  {line.linewidth:7.3f}  {ratio:6.2f}x  {span_str:>8}")

    # ── 生成可粘贴代码 ──
    print(f"\n{'═'*70}")
    print("  可粘贴参数（仅供参考，需人工微调行分组）")
    print(f"{'═'*70}")

    print(f"\n# 基准量")
    print(f"# base_font_size = {base_fs} pt")
    print(f"# base_line_width = {base_lw} pt")

    print(f"\n# 线宽参数")
    unique_lws = sorted(set(l.linewidth for l in lines))
    for lw in unique_lws:
        ratio = lw / base_lw
        if ratio > 1.5:
            print(f"# 粗线 {lw:.3f}pt → line_width_below = -{ratio:.1f}  (负数=倍率)")
        else:
            print(f"# 细线 {lw:.3f}pt → line_width_below = 标准默认")

    print(f"\n# 字号参数 (font_ratio = size / base_fs)")
    seen_ratios = set()
    for row in rows:
        ratio = round(row.font_size / base_fs, 2)
        if ratio not in seen_ratios:
            seen_ratios.add(ratio)
            print(f"# '{row.text[:30]}' → font_ratio = {ratio:.2f}")

    # ── 行间距参数 ──
    print(f"\n# 行间距 (gap 值, 正=呼吸 负=紧贴)")
    prev_bottom = None
    for i, row in enumerate(rows):
        if prev_bottom is not None:
            gap = row.y_top - prev_bottom
            label = "紧贴" if gap < 0 else "呼吸"
            print(f"# Row {i-1}→{i}: gap={gap:+.2f}pt ({label})  "
                  f"'{rows[i-1].text[:20]}' → '{row.text[:20]}'")
        prev_bottom = row.y_bottom

    # ── 生成完整字典配置代码 ──
    print(f"\n{'═'*70}")
    print("  💻 可直接复制的 Python 字典代码块（草稿）")
    print(f"{'═'*70}")
    print('    "NEW_COUNTRY": NutritionLayout(')
    print('        name="Auto-Parsed Layout",')
    print('        columns=[')
    print('            NutColumn("name",        0.50, "left"),')
    print('            NutColumn("per_serving", 0.50, "right"),')
    print('        ],')
    print('        header_rows=[')
    
    prev_bottom = None
    for i, row in enumerate(rows):
        ratio = round(row.font_size / base_fs, 2)
        font_override = ""
        if row.font_weight == "heavy":
            font_override = ', font_override="AliPuHuiTi-Heavy"'
        elif row.font_weight == "bold" and ratio > 1.0:
            font_override = ', font_override="AliPuHuiTi-Bold"'
            
        margin_str = ""
        if prev_bottom is not None:
            gap = row.y_top - prev_bottom
            if gap < -0.5:
                margin_str = f", margin_top={gap:+.1f}"
        prev_bottom = row.y_bottom
        
        line_below = False
        lw_str = ""
        for line in lines:
            # 判断行底部距离线段 Y 的距离
            if 0 <= (line.y - row.y_bottom) <= 6:
                line_below = True
                ratio_lw = line.linewidth / base_lw
                if ratio_lw > 1.5:
                    lw_str = f', line_width_below=-{ratio_lw:.1f}'
                break
        
        bold_str = ", bold=True" if row.font_weight in ("bold", "heavy") else ""
        fr_str = f", font_ratio={ratio:.2f}, height_ratio={ratio:.2f}" if ratio != 1.0 else ""
        draw_line_str = "True" if line_below else "False"
        
        text_safe = row.text.replace('"', '\\"')
        
        code_line1 = f'                cells=["{text_safe}"], span_full=True{bold_str}, align="left",'
        code_line2 = f'                draw_line_below={draw_line_str}'
        if lw_str or margin_str or fr_str or font_override:
            code_line2 += f'{lw_str}{margin_str}{fr_str}{font_override}'
        code_line2 += ","
        
        print('            NutHeaderRow(')
        print(code_line1)
        print(code_line2)
        print('                independent_tz=True, horizontal_padding=2.0,')
        print('            ),')
    
    print('        ],')
    print('        draw_data_row_lines=True,')
    print(f'        line_height_ratio=1.45,')
    print(f'        reference_font_size={base_fs:.2f},')
    print('    ),')


# ══════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        # 默认使用已知的加拿大模板
        ai_path = "/Users/mulele/Projects/60-ai_tag/2-新的场景/小标签环保标识图/测试3.12/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01/P526573 800g加拿大合一鲜香黄豆酱打印技术小标签(正标43X28mm+背标90X55mm) 202602-01.ai"
    else:
        ai_path = sys.argv[1]

    print(f"🔍 解析文件: {ai_path}")
    print()

    analysis = analyze_ai_file(ai_path)
    generate_report(analysis)


if __name__ == "__main__":
    main()
