#!/usr/bin/env python3
"""
generate_from_zones.py — Zone-Based 标签渲染管线

从设计师标注的 zone 坐标 + 产品数据 JSON 生成最终标签 PDF/PNG。

用法:
  python generate_from_zones.py <zones.yaml | annotated.ai> <product_data.json> [-o output.pdf] [--preview]

管线:
  设计师标注.ai → ai_parser_annotated.py → zones YAML
                                                ↓
                  产品数据 JSON → generate_from_zones.py → 标签 PDF + PNG 预览
"""

import argparse
import io
import json
import os
import textwrap
import yaml

import fitz  # PyMuPDF (for preview)
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import black, HexColor

# --------------------------------------------------
# 常量
# --------------------------------------------------
BLEED_MM = 2.0

# EU 法规字高要求
MIN_XHEIGHT_MM = 1.2  # 小写 a/o/e 最低字高
X_HEIGHT_RATIO = 0.56  # Alibaba PuHuiTi x-height / font-size
CAP_HEIGHT_RATIO = 0.715  # 数字/大写 cap-height / font-size
PT_PER_MM = 1 / 0.3528  # 1mm = 2.8346pt

# content / nutrition 最小字号 = 1.2mm / 0.56 / 0.3528 ≈ 6.1pt
MIN_FONT_SIZE_CONTENT = round(MIN_XHEIGHT_MM / X_HEIGHT_RATIO * PT_PER_MM, 1)  # 6.1pt

# net_volume 字高分级 (德国强制, EU 推荐)
# min_font_size = min_height_mm / cap_height_ratio / 0.3528
NET_VOLUME_GRADES = [
    (1000, round(6.0 / CAP_HEIGHT_RATIO * PT_PER_MM, 1)),  # >1000: ≥23.8pt
    (200,  round(4.0 / CAP_HEIGHT_RATIO * PT_PER_MM, 1)),  # >200:  ≥15.9pt
    (50,   round(3.0 / CAP_HEIGHT_RATIO * PT_PER_MM, 1)),  # >50:   ≥11.9pt
    (0,    round(2.0 / CAP_HEIGHT_RATIO * PT_PER_MM, 1)),  # ≤50:   ≥7.9pt
]

# --------------------------------------------------
# 字体注册（复用 label_renderer 的逻辑）
# --------------------------------------------------
_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"
_FONT_NAME_BOLD = "Helvetica-Bold"


def _register_font():
    global _FONT_REGISTERED, _FONT_NAME, _FONT_NAME_BOLD
    if _FONT_REGISTERED:
        return

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    alibaba_r = os.path.join(static_dir, "Alibaba-PuHuiTi-Regular.ttf")
    alibaba_b = os.path.join(static_dir, "Alibaba-PuHuiTi-Bold.ttf")

    if os.path.isfile(alibaba_r) and os.path.getsize(alibaba_r) > 100_000:
        pdfmetrics.registerFont(TTFont("AliPuHuiTi", alibaba_r))
        if os.path.isfile(alibaba_b) and os.path.getsize(alibaba_b) > 100_000:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_b))
        else:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_r))
        _FONT_NAME = "AliPuHuiTi"
        _FONT_NAME_BOLD = "AliPuHuiTi-Bold"

    _FONT_REGISTERED = True


# --------------------------------------------------
# 坐标转换
# --------------------------------------------------
def mm2pt(v):
    return v * mm


def y_mm_to_canvas(y_mm, page_h_mm):
    """zone 坐标 (左上角 mm) → ReportLab canvas y (左下角 pt)"""
    return (page_h_mm - y_mm) * mm


# --------------------------------------------------
# 文字绘制辅助
# --------------------------------------------------
def _wrap_text(text, font_name, font_size, max_width_pt):
    """
    智能断行：按字符宽度将文本拆成多行。
    返回 list[str]。
    """
    lines = []
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            lines.append('')
            continue

        words = paragraph.split(' ')
        current_line = ''
        for word in words:
            test = f"{current_line} {word}".strip() if current_line else word
            w = pdfmetrics.stringWidth(test, font_name, font_size)
            if w <= max_width_pt:
                current_line = test
            else:
                if current_line:
                    lines.append(current_line)
                # 如果单个词就超宽，强制放入
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines


def _find_max_font_size(text, font_name, box_w_pt, box_h_pt,
                         min_size=3.0, max_size=20.0, leading_ratio=1.2):
    """
    二分搜索最大字号，使 text 在 box 内完全容纳。
    """
    lo, hi = min_size, max_size
    best = lo

    for _ in range(20):  # 20 轮二分，精度 < 0.01pt
        mid = (lo + hi) / 2
        lines = _wrap_text(text, font_name, mid, box_w_pt)
        total_h = len(lines) * mid * leading_ratio
        if total_h <= box_h_pt:
            best = mid
            lo = mid
        else:
            hi = mid

    return best


def _draw_text_in_box(c, text, font_name, font_size, x_pt, y_top_pt, w_pt, h_pt,
                       leading_ratio=1.2, color=black, bold_prefix=None):
    """
    在指定框内绘制自动换行文本。
    y_top_pt = 框顶部 canvas y。
    """
    c.setFillColor(color)
    c.setFont(font_name, font_size)

    leading = font_size * leading_ratio
    lines = _wrap_text(text, font_name, font_size, w_pt)

    y = y_top_pt - font_size * 0.85  # 首行 baseline

    for line in lines:
        if y < y_top_pt - h_pt:
            break  # 超出底部边界
        c.drawString(x_pt, y, line)
        y -= leading


def _draw_text_l_shape(c, text, font_name, font_size,
                       zone, page_h_mm, leading_ratio=1.2, h_scale=100, color=black):
    """
    在 L 型区域内绘制文本（word-wrap + obstacle 避让）。
    h_scale: 水平缩放比例（100=正常, 80=80%宽度），用于放不下时横向压缩。
    """
    _register_font()
    c.setFillColor(color)
    c.setFont(font_name, font_size)

    zone_x = mm2pt(zone.get('x_mm', BLEED_MM))
    zone_y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    zone_w = mm2pt(zone.get('w_mm', 46))
    zone_h = mm2pt(zone['h_mm'])
    zone_y_bottom = zone_y_top - zone_h

    obstacles = zone.get('obstacles', [])
    # 预计算 obstacle 的 canvas 坐标范围
    obs_ranges = []
    for obs in obstacles:
        obs_y_top_canvas = y_mm_to_canvas(obs['y_mm'], page_h_mm)
        obs_y_bot_canvas = y_mm_to_canvas(obs['y_mm'] + obs['h_mm'], page_h_mm)
        obs_x_left = mm2pt(obs['x_mm'])
        obs_x_right = mm2pt(obs['x_mm'] + obs['w_mm'])
        obs_ranges.append({
            'y_top': obs_y_top_canvas,
            'y_bot': obs_y_bot_canvas,
            'x_left': obs_x_left,
            'x_right': obs_x_right,
        })

    leading = font_size * leading_ratio
    y = zone_y_top - font_size * 0.85  # 首行 baseline

    # 将文本拆成 words 便于逐行填充
    all_words = []
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            all_words.append('\n')  # 段落分隔
            continue
        all_words.extend(paragraph.split(' '))
        all_words.append('\n')
    # 去掉末尾多余换行
    while all_words and all_words[-1] == '\n':
        all_words.pop()

    # 水平缩放系数：影响 stringWidth 计算
    scale_factor = h_scale / 100.0

    word_idx = 0
    while word_idx < len(all_words) and y >= zone_y_bottom - 0.1:
        # 计算当前行的可用宽度
        line_w = zone_w
        line_x = zone_x

        for obs in obs_ranges:
            # 当前行的 baseline 是否在 obstacle 的 y 范围内
            # baseline y 在 [obs_y_bot, obs_y_top] 之间
            line_top = y + font_size * 0.85  # 行顶部
            if line_top > obs['y_bot'] and y < obs['y_top']:
                # 障碍物在右侧
                if obs['x_left'] > zone_x + zone_w * 0.3:
                    line_w = obs['x_left'] - zone_x - mm2pt(1)  # 留 1mm 间距
                # 障碍物在左侧
                elif obs['x_right'] < zone_x + zone_w * 0.7:
                    new_x = obs['x_right'] + mm2pt(1)
                    line_w = (zone_x + zone_w) - new_x
                    line_x = new_x

        # 填充当前行（考虑水平压缩后实际宽度更大）
        effective_line_w = line_w / scale_factor  # 压缩后可容纳更多文字
        current_line = ''
        while word_idx < len(all_words):
            word = all_words[word_idx]
            if word == '\n':
                word_idx += 1
                break

            test = f"{current_line} {word}".strip() if current_line else word
            w = pdfmetrics.stringWidth(test, font_name, font_size)
            if w <= effective_line_w:
                current_line = test
                word_idx += 1
            else:
                if not current_line:
                    # 单词超宽，强制放入
                    current_line = word
                    word_idx += 1
                break

        if current_line:
            if h_scale != 100:
                to = c.beginText(line_x, y)
                to.setFont(font_name, font_size)
                to.setHorizScale(h_scale)
                to.textOut(current_line)
                c.drawText(to)
            else:
                c.drawString(line_x, y, current_line)
        y -= leading

    return y  # 返回最后绘制位置


def _find_max_font_size_l_shape(text, font_name, zone, page_h_mm,
                                 min_size=3.0, max_size=20.0, leading_ratio=1.2):
    """
    二分搜索最大字号，使 text 在 L 型 zone 内完全容纳。
    """
    zone_h = mm2pt(zone['h_mm'])

    lo, hi = min_size, max_size
    best = lo

    obstacles = zone.get('obstacles', [])
    if not obstacles:
        # 无障碍物，简单矩形
        box_w = mm2pt(zone.get('w_mm', 46))
        return _find_max_font_size(text, font_name, box_w, zone_h,
                                    min_size, max_size, leading_ratio)

    # 有障碍物 → 模拟 L 型排版计算行数
    zone_x = mm2pt(zone.get('x_mm', BLEED_MM))
    zone_w = mm2pt(zone.get('w_mm', 46))

    obs_ranges = []
    for obs in obstacles:
        obs_y_top_canvas = y_mm_to_canvas(obs['y_mm'], page_h_mm)
        obs_y_bot_canvas = y_mm_to_canvas(obs['y_mm'] + obs['h_mm'], page_h_mm)
        obs_x_left = mm2pt(obs['x_mm'])
        obs_ranges.append({
            'y_top': obs_y_top_canvas,
            'y_bot': obs_y_bot_canvas,
            'x_left': obs_x_left,
        })

    zone_y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    zone_y_bottom = zone_y_top - zone_h

    for _ in range(20):
        mid = (lo + hi) / 2
        leading = mid * leading_ratio
        y = zone_y_top - mid * 0.85

        all_words = []
        for paragraph in text.split('\n'):
            if not paragraph.strip():
                all_words.append('\n')
                continue
            all_words.extend(paragraph.split(' '))
            all_words.append('\n')
        while all_words and all_words[-1] == '\n':
            all_words.pop()

        word_idx = 0
        fits = True
        while word_idx < len(all_words):
            if y < zone_y_bottom:
                fits = False
                break

            line_w = zone_w
            for obs in obs_ranges:
                line_top = y + mid * 0.85
                if line_top > obs['y_bot'] and y < obs['y_top']:
                    if obs['x_left'] > zone_x + zone_w * 0.3:
                        line_w = obs['x_left'] - zone_x - mm2pt(1)

            current_line = ''
            while word_idx < len(all_words):
                word = all_words[word_idx]
                if word == '\n':
                    word_idx += 1
                    break
                test = f"{current_line} {word}".strip() if current_line else word
                w = pdfmetrics.stringWidth(test, font_name, mid)
                if w <= line_w:
                    current_line = test
                    word_idx += 1
                else:
                    if not current_line:
                        current_line = word
                        word_idx += 1
                    break
            y -= leading

        if fits:
            best = mid
            lo = mid
        else:
            hi = mid

    return best


def _calc_h_scale_l_shape(text, font_name, font_size, zone, page_h_mm,
                           leading_ratio=1.15, min_scale=60, target_lines=None):
    """
    计算使文本在 L 型区域内完全容纳所需的水平压缩比例（100=正常, 60=极限压缩）。
    target_lines: 如果指定，找到使文本排成恰好 N 行的最大 h_scale。
    """
    _register_font()
    zone_h = mm2pt(zone['h_mm'])
    zone_x = mm2pt(zone.get('x_mm', BLEED_MM))
    zone_w = mm2pt(zone.get('w_mm', 46))

    obstacles = zone.get('obstacles', [])
    obs_ranges = []
    for obs in obstacles:
        obs_y_top_canvas = y_mm_to_canvas(obs['y_mm'], page_h_mm)
        obs_y_bot_canvas = y_mm_to_canvas(obs['y_mm'] + obs['h_mm'], page_h_mm)
        obs_x_left = mm2pt(obs['x_mm'])
        obs_ranges.append({
            'y_top': obs_y_top_canvas,
            'y_bot': obs_y_bot_canvas,
            'x_left': obs_x_left,
        })

    zone_y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    zone_y_bottom = zone_y_top - zone_h

    all_words = []
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            all_words.append('\n')
            continue
        all_words.extend(paragraph.split(' '))
        all_words.append('\n')
    while all_words and all_words[-1] == '\n':
        all_words.pop()

    leading = font_size * leading_ratio

    def _sim_layout(scale_pct):
        """模拟排版，返回 (行数, 是否放得下)"""
        scale_factor = scale_pct / 100.0
        y = zone_y_top - font_size * 0.85
        word_idx = 0
        n_lines = 0
        fits = True

        while word_idx < len(all_words):
            if y < zone_y_bottom:
                fits = False
                break

            line_w = zone_w
            for obs in obs_ranges:
                line_top = y + font_size * 0.85
                if line_top > obs['y_bot'] and y < obs['y_top']:
                    if obs['x_left'] > zone_x + zone_w * 0.3:
                        line_w = obs['x_left'] - zone_x - mm2pt(1)

            effective_w = line_w / scale_factor

            current_line = ''
            while word_idx < len(all_words):
                word = all_words[word_idx]
                if word == '\n':
                    word_idx += 1
                    break
                test = f"{current_line} {word}".strip() if current_line else word
                w = pdfmetrics.stringWidth(test, font_name, font_size)
                if w <= effective_w:
                    current_line = test
                    word_idx += 1
                else:
                    if not current_line:
                        current_line = word
                        word_idx += 1
                    break
            n_lines += 1
            y -= leading

        return n_lines, fits

    lo, hi = min_scale, 100
    best = lo

    for _ in range(15):
        mid = (lo + hi) / 2
        n_lines, fits = _sim_layout(mid)

        if target_lines is not None:
            # 目标行数模式：行数必须 <= target_lines 且全部放下
            if fits and n_lines <= target_lines:
                best = mid
                lo = mid
            else:
                hi = mid
        else:
            # 原有模式：只要放得下就行
            if fits:
                best = mid
                lo = mid
            else:
                hi = mid

    return round(best)


# ==================================================================
# 法规辅助函数
# ==================================================================

def _net_volume_min_font_size(net_text: str) -> float:
    """根据净含量数值确定最低字号 (EU 法规, 基于 cap-height)。

    Args:
        net_text: 净含量文本, 如 "500 mL", "1.5 L", "250g"

    Returns:
        最低字号 (pt)
    """
    import re
    # 提取数值 (支持小数)
    match = re.search(r'([\d.]+)\s*(m[Ll]|[Ll]|[gG]|kg|KG)', net_text)
    if not match:
        return NET_VOLUME_GRADES[-1][1]  # 默认最低档

    value = float(match.group(1))
    unit = match.group(2).lower()

    # 统一转为 mL 或 g
    if unit in ('l',):
        value *= 1000  # L → mL
    elif unit in ('kg',):
        value *= 1000  # kg → g

    for threshold, min_fs in NET_VOLUME_GRADES:
        if value > threshold:
            return min_fs
    return NET_VOLUME_GRADES[-1][1]


def _precalc_content_font_size(zones: list, product_data: dict, page_h_mm: float) -> float:
    """预计算 content 区域字号（供 title 联动使用）。

    规则：
    - 从模板 style 获取 min_font_size
    - 兜底 EU 法规最低 6.1pt
    - 在 L 型 zone 内二分搜索最大字号
    """
    _register_font()

    # 找 content zone
    content_zone = None
    for z in zones:
        if z.get('id') == 'content':
            content_zone = z
            break

    if not content_zone:
        return MIN_FONT_SIZE_CONTENT

    # 获取 content 文本
    sections = []
    for field in ['ingredients', 'storage', 'usage', 'best_before',
                  'product_of', 'importer_info', 'importer_address']:
        text = product_data.get(field, '')
        if text:
            sections.append(text)
    full_text = '\n'.join(sections)

    # 最小字号：max(模板提取值, EU 法规下限)
    style = content_zone.get('style', {})
    min_fs = max(style.get('min_font_size', MIN_FONT_SIZE_CONTENT), MIN_FONT_SIZE_CONTENT)

    # 二分搜索最大字号（结果不会低于 min_fs）
    font_size = _find_max_font_size_l_shape(
        full_text, _FONT_NAME, content_zone, page_h_mm,
        min_size=min_fs, max_size=min_fs,  # 固定字号 = min_fs
        leading_ratio=1.15)

    return font_size


# ==================================================================
# Zone 绘制函数
# ==================================================================

def draw_title(c, zone, data, page_h_mm, content_font_size=None):
    """绘制标题区域（产品名称 + 中文名），支持 L 型避让 + 横向压缩

    Args:
        content_font_size: content 区域预计算的字号，title = content × 1.1
    """
    en_name = data.get('product_name_en', 'PRODUCT NAME')

    # 分离多语品名和中文品名
    if '\n\n' in en_name:
        parts = en_name.split('\n\n', 1)
        title_text = parts[0].replace('\n', ' ').strip()
        cn_name = parts[1].strip()
    else:
        title_text = en_name.replace('\n', ' ').strip()
        cn_name = data.get('product_name_cn', '')

    full_title = title_text
    if cn_name:
        full_title += f"  {cn_name}"

    # 字号规则：title = content × 1.1，不低于模板 min_font_size
    style = zone.get('style', {})
    target_lines = style.get('line_count')
    leading_ratio = 1.15

    if content_font_size:
        # ❶ 核心规则：title 比 content 大 10%
        min_fs = round(content_font_size * 1.1, 1)
        print(f"    📏 title字号 = content({content_font_size:.1f}pt) × 1.1 = {min_fs:.1f}pt")
    else:
        min_fs = max(style.get('min_font_size', 4.0), MIN_FONT_SIZE_CONTENT)

    # 如果有目标行数，从 zone 高度反推 leading_ratio
    if target_lines and target_lines > 1:
        zone_h_pt = mm2pt(zone['h_mm'])
        leading_ratio = (zone_h_pt - min_fs * 0.85) / ((target_lines - 1) * min_fs)
        leading_ratio = min(leading_ratio, 2.0)
        print(f"    📏 目标{target_lines}行, leading_ratio={leading_ratio:.2f}")

    font_size = _find_max_font_size_l_shape(full_title, _FONT_NAME_BOLD, zone, page_h_mm,
                                             min_size=min_fs, max_size=min_fs,
                                             leading_ratio=leading_ratio)

    # 字号已到下限或有目标行数 → 计算横向压缩
    h_scale = 100
    if abs(font_size - min_fs) < 0.1 or target_lines:
        h_scale = _calc_h_scale_l_shape(full_title, _FONT_NAME_BOLD, font_size,
                                         zone, page_h_mm,
                                         leading_ratio=leading_ratio,
                                         target_lines=target_lines)
        if h_scale < 100:
            print(f"    ↔️  title 横向压缩: {h_scale}%")

    c.setFont(_FONT_NAME_BOLD, font_size)
    _draw_text_l_shape(c, full_title, _FONT_NAME_BOLD, font_size,
                       zone, page_h_mm, leading_ratio=leading_ratio, h_scale=h_scale)


def draw_content(c, zone, data, page_h_mm):
    """绘制正文区域（配料表 + 储存条件 + 用途等），支持 L 型避让 + 横向压缩"""
    sections = []
    for field in ['ingredients', 'storage', 'usage', 'best_before',
                  'product_of', 'importer_info', 'importer_address']:
        text = data.get(field, '')
        if text:
            sections.append(text)

    full_text = '\n'.join(sections)

    # 最小字号：max(模板提取值, EU 法规 1.2mm 字高下限 = 6.1pt)
    style = zone.get('style', {})
    min_fs = max(style.get('min_font_size', MIN_FONT_SIZE_CONTENT), MIN_FONT_SIZE_CONTENT)
    font_size = _find_max_font_size_l_shape(full_text, _FONT_NAME, zone, page_h_mm,
                                             min_size=min_fs, max_size=min_fs,
                                             leading_ratio=1.15)

    # 如果字号已到硬下限，计算横向压缩比例
    h_scale = 100
    if abs(font_size - min_fs) < 0.1:
        h_scale = _calc_h_scale_l_shape(full_text, _FONT_NAME, font_size,
                                         zone, page_h_mm, leading_ratio=1.15)
        if h_scale < 100:
            print(f"    ↔️  content 横向压缩: {h_scale}%")

    _draw_text_l_shape(c, full_text, _FONT_NAME, font_size,
                       zone, page_h_mm, leading_ratio=1.15, h_scale=h_scale)


def draw_net_volume(c, zone, data, page_h_mm):
    """绘制净含量（大号字/自适应，强制 EU 字高分级）"""
    x_pt = mm2pt(zone.get('x_mm', BLEED_MM))
    y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    w_pt = mm2pt(zone.get('w_mm', 8))
    h_pt = mm2pt(zone['h_mm'])

    net_text = data.get('net_weight', '')
    if not net_text:
        return

    # EU 法规：按净含量数值分级确定最低字号
    grade_min_fs = _net_volume_min_font_size(net_text)
    style = zone.get('style', {})
    h_scale = 100

    # 字号 = max(模板提取值, 分级最低值)
    if 'font_size' in style:
        font_size = max(style['font_size'], grade_min_fs)
    else:
        font_size = max(grade_min_fs,
                        _find_max_font_size(net_text, _FONT_NAME_BOLD, w_pt, h_pt,
                                             min_size=grade_min_fs, max_size=24.0))

    print(f"    📏 net_volume 分级: {net_text} → 最低{grade_min_fs}pt, 使用{font_size}pt")

    # 文字超宽 → 横向压缩（不可突破最低字号）
    text_w = pdfmetrics.stringWidth(net_text, _FONT_NAME_BOLD, font_size)
    if text_w > w_pt:
        h_scale = int(w_pt / text_w * 100)
        h_scale = max(h_scale, 50)
        print(f"    ↔️  net_volume 横向压缩: {h_scale}% (保持{font_size}pt)")

    c.setFont(_FONT_NAME_BOLD, font_size)
    y_baseline = y_top - font_size * 0.85
    if h_scale < 100:
        c.saveState()
        c.translate(x_pt, y_baseline)
        c.scale(h_scale / 100.0, 1)
        c.drawString(0, 0, net_text)
        c.restoreState()
    else:
        c.drawString(x_pt, y_baseline, net_text)


def draw_nutrition(c, zone, data, page_h_mm):
    """绘制营养成分表（优先使用模板提取的样式，兜底二分搜索）"""
    x = mm2pt(zone.get('x_mm', BLEED_MM))
    y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    width = mm2pt(zone.get('w_mm', 46))
    h_pt = mm2pt(zone['h_mm'])

    nutrition = data.get('nutrition') or {}
    table_data = nutrition.get('table_data', [])
    serving_size = nutrition.get('serving_size', '')

    if not table_data:
        return

    nut_title = nutrition.get('title',
        "Nutrition declaration / Voedingswaardevermelding / "
        "Información nutricional / Nährwertdeklaration / "
        "Déclaration nutritionnelle")
    per_text = nutrition.get('per_label', '') or (
        f"Nutrition facts per / Voedingswaarde per / "
        f"Valor nutricional por / Nährwerte pro / "
        f"Valeur nutritive pour  {serving_size}")

    CAP_H_RATIO = 0.735
    style = zone.get('style', {})

    _register_font()

    # 模板行高（仅在使用模板字号时生效）
    tpl_row_h = mm2pt(style['avg_row_height_mm']) if 'avg_row_height_mm' in style else None

    # 通用高度估算函数
    def _estimate_table_height(fs, col_ratio=0.78, use_tpl_rh=False, title_fs=None):
        rh = tpl_row_h if (use_tpl_rh and tpl_row_h) else (fs + 2)
        # title: 用 title_fs（默认 = fs）计算换行
        tfs = title_fs or fs
        t_lines = _wrap_text(nut_title, _FONT_NAME_BOLD, tfs, width - 4)
        title_h = len(t_lines) * tfs * 1.15 + 1
        # per serving: 紧凑行高
        p_lines = _wrap_text(per_text, _FONT_NAME, fs, width - 4)
        per_h = len(p_lines) * fs * 1.1 + 1
        # 数据行
        c1w = width * col_ratio
        data_h = 0
        for item in table_data:
            if use_tpl_rh:
                # 模板行高模式：每行固定 rh，名称超宽时由渲染时横向压缩
                data_h += rh
            else:
                name = item.get('name', '')
                is_sub = item.get('is_sub', False)
                name_x_offset = 8 if is_sub else 2
                max_nw = c1w - name_x_offset - 2
                name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD
                nw = pdfmetrics.stringWidth(name, name_font, fs)
                n_lines = max(1, int(nw / max_nw) + (1 if nw > max_nw else 0))
                data_h += rh + max(0, n_lines - 1) * fs * 1.1
        return title_h + per_h + data_h

    # --- 从模板提取的样式（优先）+ 溢出检查 ---
    col_ratio = style.get('name_col_ratio', 0.78)
    use_template = False

    if 'data_font_size' in style:
        # 信任模板样式：设计师已验证布局，直接使用提取的字号
        font_size = style['data_font_size']
        title_font_size = style.get('header_font_size', font_size)
        # value_font_size 过滤异常值（>8pt 通常是标题/中文等非数据字号）
        raw_vfs = style.get('value_font_size', font_size)
        value_font_size = font_size if raw_vfs > 8.0 else raw_vfs
        use_template = True
        print(f"    📏 使用模板样式: 数据={font_size}pt 标题={title_font_size}pt 值={value_font_size}pt")

    if not use_template:
        # 二分搜索：找最大能放进 zone 的字号
        # 下限 = EU 法规 6.1pt，如果 6.1pt 也放不下则强制使用（靠横向压缩兜底）
        lo, hi = MIN_FONT_SIZE_CONTENT, max(MIN_FONT_SIZE_CONTENT, 8.0)
        font_size = lo  # 至少保证 6.1pt
        for _ in range(15):
            mid = (lo + hi) / 2
            if _estimate_table_height(mid, col_ratio, use_tpl_rh=False) <= h_pt:
                font_size = mid
                lo = mid
            else:
                hi = mid
        # 如果最低字号仍溢出，切换到强制模板模式（固定行高 + 横向压缩名称）
        if _estimate_table_height(font_size, col_ratio, use_tpl_rh=False) > h_pt:
            font_size = MIN_FONT_SIZE_CONTENT
            use_template = True
            tpl_row_h = None  # 强制使用动态行高，不用模板行高
            print(f"    ⚠️  nutrition 6.1pt 换行溢出，切换固定行高+横向压缩模式")
        title_font_size = font_size
        value_font_size = font_size
        print(f"    📏 二分搜索字号: {font_size:.1f}pt")

    # 行高计算
    if use_template and tpl_row_h:
        row_h = tpl_row_h
    elif use_template and not tpl_row_h:
        # 强制模板模式：动态计算行高 = (zone高度 - 标题行高 - serving行高) / 数据行数
        tfs = title_font_size or font_size
        t_lines = _wrap_text(nut_title, _FONT_NAME_BOLD, tfs, width - 4)
        title_h = len(t_lines) * tfs * 1.15 + 3
        p_lines = _wrap_text(per_text, _FONT_NAME, font_size, width - 4)
        per_h = len(p_lines) * font_size * 1.1 + 2
        remaining = h_pt - title_h - per_h
        n_rows = max(len(table_data), 1)
        row_h = max(remaining / n_rows, font_size + 0.5)  # 至少比字号大 0.5pt
        print(f"    📏 动态行高: {row_h:.1f}pt ({n_rows}行, 剩余{remaining:.0f}pt)")
    else:
        row_h = font_size + 2
    pad = (row_h - font_size) / 2

    # 列宽：优先使用模板提取的列宽比
    name_col_ratio = style.get('name_col_ratio', 0.78)
    col1_w = width * name_col_ratio
    col2_w = width * (1 - name_col_ratio)

    # 线宽：优先使用模板提取值
    data_line_width = style.get('line_width_pt', 0.3)
    border_line_width = style.get('border_width_pt', 1.0)

    table_top = y_top
    y = y_top

    c.setStrokeColor(black)

    # --- 标题行（多语，需要换行）---
    c.setFont(_FONT_NAME_BOLD, title_font_size)
    title_lines = _wrap_text(nut_title, _FONT_NAME_BOLD, title_font_size, width - 4)
    ty = y - title_font_size * 0.8
    for tl in title_lines:
        c.drawString(x + 2, ty, tl)
        ty -= title_font_size * 1.15
    title_row_h = (y - ty) + 2
    y -= title_row_h
    c.setLineWidth(border_line_width)  # 标题下方用外框线宽
    c.line(x, y, x + width, y)

    # --- Per serving 行 ---
    c.setFont(_FONT_NAME, font_size)
    per_lines = _wrap_text(per_text, _FONT_NAME, font_size, width - 4)
    cap_h = font_size * CAP_H_RATIO
    py = y - cap_h - 1
    for pl in per_lines:
        c.drawString(x + 2, py, pl)
        py -= font_size * 1.1
    per_row_h = (y - py) + 1
    y -= per_row_h
    c.setLineWidth(data_line_width)
    c.line(x, y, x + width, y)

    # --- 列分隔线起始位置 ---
    col_sep_top = y

    # --- 数据行 ---
    for item in table_data:
        name = item.get('name', '')
        per_serving = str(item.get('per_serving', ''))
        is_sub = item.get('is_sub', False)

        name_x = x + 8 if is_sub else x + 2
        max_name_w = col1_w - (name_x - x) - 2
        name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD

        # 估算名称行数
        name_w = pdfmetrics.stringWidth(name, name_font, font_size)

        if use_template:
            # 模板模式：固定行高，超宽名称横向压缩
            actual_row_h = row_h
            if y - actual_row_h < y_top - h_pt:
                break

            c.setFont(name_font, font_size)
            text_y = y - pad - cap_h
            if name_w > max_name_w:
                # 横向压缩名称
                name_h_scale = max_name_w / name_w
                c.saveState()
                c.translate(name_x, text_y)
                c.scale(name_h_scale, 1)
                c.drawString(0, 0, name)
                c.restoreState()
            else:
                c.drawString(name_x, text_y, name)
        else:
            # 非模板模式：允许换行扩展
            name_lines_n = max(1, int(name_w / max_name_w) + (1 if name_w > max_name_w else 0))
            actual_row_h = row_h * name_lines_n
            if y - actual_row_h < y_top - h_pt:
                break

            c.setFont(name_font, font_size)
            if name_lines_n > 1:
                n_lines = _wrap_text(name, name_font, font_size, max_name_w)
                ny = y - pad - cap_h
                for nl in n_lines:
                    c.drawString(name_x, ny, nl)
                    ny -= font_size * 1.1
            else:
                text_y = y - pad - cap_h
                c.drawString(name_x, text_y, name)

        # 数值（右对齐）
        c.setFont(_FONT_NAME, font_size)
        text_y = y - pad - cap_h
        if per_serving:
            c.drawRightString(x + width - 2, text_y, per_serving)

        y -= actual_row_h
        c.setLineWidth(data_line_width)
        c.line(x, y, x + width, y)

    table_bottom = y

    # --- 外框 ---
    c.setLineWidth(border_line_width)
    c.line(x, table_top, x, table_bottom)            # 左
    c.line(x + width, table_top, x + width, table_bottom)  # 右
    c.line(x, table_top, x + width, table_top)        # 上
    c.line(x, table_bottom, x + width, table_bottom)  # 下

    # 列分隔线（从 per 行到底部）
    c.setLineWidth(data_line_width)
    c.line(x + col1_w, col_sep_top, x + col1_w, table_bottom)


def draw_logo(c, zone, data, page_h_mm):
    """绘制品牌 Logo"""
    x_pt = mm2pt(zone.get('x_mm', BLEED_MM))
    y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    w_pt = mm2pt(zone.get('w_mm', 8))
    h_pt = mm2pt(zone['h_mm'])

    logo_path = data.get('brand_logo', '')
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if not logo_path or not os.path.isfile(logo_path):
        logo_path = os.path.join(static_dir, "logo_placeholder.png")

    if os.path.isfile(logo_path):
        try:
            c.drawImage(logo_path, x_pt, y_top - h_pt,
                        width=w_pt, height=h_pt,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass


def draw_eco_icons(c, zone, data, page_h_mm):
    """绘制环保标识"""
    x_pt = mm2pt(zone.get('x_mm', BLEED_MM))
    y_top = y_mm_to_canvas(zone['y_mm'], page_h_mm)
    w_pt = mm2pt(zone.get('w_mm', 46))
    h_pt = mm2pt(zone['h_mm'])

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    eco_dir = os.path.join(static_dir, "eco_icons")

    # 尝试加载环保图标
    icon_files = []
    if os.path.isdir(eco_dir):
        icon_files = sorted([f for f in os.listdir(eco_dir)
                            if f.lower().endswith(('.png', '.jpg', '.svg'))])

    if not icon_files:
        # 画占位文字
        c.setFont(_FONT_NAME, 5)
        c.setFillColor(HexColor('#999999'))
        c.drawString(x_pt + 2, y_top - h_pt / 2, "[Eco Icons]")
        return

    # 从模板提取图标尺寸（固定值）
    style = zone.get('style', {})
    n = len(icon_files)

    # 计算每个图标的实际宽高比，确定合理尺寸
    icon_dims = []
    for fname in icon_files:
        icon_path = os.path.join(eco_dir, fname)
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(icon_path)
            iw, ih = img.getSize()
            aspect = iw / ih if ih > 0 else 1.0
            icon_dims.append((fname, aspect))
        except Exception:
            icon_dims.append((fname, 1.0))

    # 图标高度 = zone 高度（设计师模板也是这样）
    icon_h = h_pt
    if 'icon_height_mm' in style:
        icon_h = mm2pt(style['icon_height_mm'])

    # 每个图标宽度按宽高比计算
    icon_widths = [icon_h * aspect for (_, aspect) in icon_dims]
    total_icon_w = sum(icon_widths)

    # 如果总宽度超过 zone 宽度，按比例缩小
    if total_icon_w > w_pt * 0.95:
        scale = w_pt * 0.9 / total_icon_w
        icon_widths = [w * scale for w in icon_widths]
        icon_h *= scale
        total_icon_w = sum(icon_widths)

    # 间距：图标之间均匀分布，两端留少量边距
    if n > 1:
        remaining = w_pt - total_icon_w
        edge_gap = remaining * 0.15  # 两端留 15% 空白
        inner_gap = (remaining - 2 * edge_gap) / (n - 1) if n > 1 else 0
    else:
        edge_gap = (w_pt - total_icon_w) / 2
        inner_gap = 0

    ix = x_pt + edge_gap
    y_bottom = y_top - h_pt + (h_pt - icon_h) / 2  # 垂直居中

    for i, fname in enumerate(icon_files):
        icon_path = os.path.join(eco_dir, fname)
        try:
            c.drawImage(icon_path, ix, y_bottom,
                        width=icon_widths[i], height=icon_h,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass
        ix += icon_widths[i] + (inner_gap if i < n - 1 else 0)


# ==================================================================
# Zone 分发器
# ==================================================================
ZONE_DRAWERS = {
    'title':      draw_title,
    'content':    draw_content,
    'net_volume': draw_net_volume,
    'nutrition':  draw_nutrition,
    'logo':       draw_logo,
    'eco_icons':  draw_eco_icons,
}


# ==================================================================
# 主渲染入口
# ==================================================================
def generate_pdf_from_zones(zones_config: dict, product_data: dict,
                             output_path: str = None) -> bytes:
    """
    核心渲染函数：根据 zone 坐标 + 产品数据生成 PDF。

    Args:
        zones_config: 带 label_size 和 zones 列表的配置 dict
        product_data: PLM 产品数据 dict
        output_path: 可选，PDF 保存路径

    Returns:
        PDF 二进制数据 (bytes)
    """
    _register_font()

    label_size = zones_config.get('label_size', {})
    pw_mm = label_size.get('width_mm', 50)
    ph_mm = label_size.get('height_mm', 120)

    zones = zones_config.get('zones', [])

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(mm2pt(pw_mm), mm2pt(ph_mm)))

    # 预计算 content 字号（供 title 联动使用：title = content × 1.1）
    content_font_size = _precalc_content_font_size(zones, product_data, ph_mm)
    print(f"  📏 content 字号: {content_font_size:.1f}pt (EU 下限={MIN_FONT_SIZE_CONTENT}pt)")

    # 遍历 zones，分发到对应绘制函数（zone 坐标来自模板，不做修改）
    for zone in zones:
        zone_id = zone.get('id', '')
        drawer = ZONE_DRAWERS.get(zone_id)
        if drawer:
            print(f"  \U0001f58a\ufe0f  绘制 {zone_id:<12} y={zone['y_mm']:.1f}mm h={zone['h_mm']:.1f}mm")
            if zone_id == 'title':
                drawer(c, zone, product_data, ph_mm, content_font_size=content_font_size)
            else:
                drawer(c, zone, product_data, ph_mm)
        else:
            print(f"  ⚠️  未知 zone: {zone_id}")

    c.showPage()
    c.save()

    pdf_bytes = buf.getvalue()

    if output_path:
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)
        print(f"\n💾 PDF 已保存: {output_path} ({len(pdf_bytes)//1024} KB)")

    return pdf_bytes


def render_preview(pdf_bytes: bytes, output_path: str, dpi: int = 300):
    """PDF → PNG 预览图"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    pix.save(output_path)
    doc.close()
    print(f"🖼️  预览图: {output_path}")


# ==================================================================
# CLI 入口
# ==================================================================
def main():
    parser = argparse.ArgumentParser(
        description='Zone-Based 标签渲染管线')
    parser.add_argument('zones_input',
                        help='Zone 配置文件 (.yaml) 或设计师标注 (.ai)')
    parser.add_argument('product_data', help='产品数据 JSON 文件')
    parser.add_argument('-o', '--output', help='PDF 输出路径',
                        default='label_output.pdf')
    parser.add_argument('--preview', action='store_true', help='生成 PNG 预览图')
    args = parser.parse_args()

    # Step 1: 加载 zones
    zones_input = args.zones_input
    if zones_input.endswith('.ai'):
        # 直接从 .ai 解析
        print("📐 从 .ai 解析标注...")
        from ai_parser_annotated import (parse_annotated, scan_annotations,
                                         build_zones_from_annotations, zones_to_yaml,
                                         extract_zone_styles)
        annotations, pw_mm, ph_mm = scan_annotations(zones_input, verbose=False)
        matched = [a for a in annotations if a['zone_id']]
        zones_list = build_zones_from_annotations(matched, pw_mm, ph_mm)
        # 从模板提取样式信息
        extract_zone_styles(zones_input, zones_list, pw_mm, ph_mm)
        zones_config = {
            'label_size': {'width_mm': pw_mm, 'height_mm': ph_mm, 'margin_mm': BLEED_MM},
            'zones': zones_list,
        }
    else:
        with open(zones_input, 'r') as f:
            zones_config = yaml.safe_load(f)

    # Step 2: 加载产品数据
    with open(args.product_data, 'r', encoding='utf-8') as f:
        product_data = json.load(f)

    print(f"\n📦 产品: {product_data.get('product_name_en', '?')[:60]}...")
    print(f"📐 标签: {zones_config['label_size']['width_mm']:.0f}×"
          f"{zones_config['label_size']['height_mm']:.0f}mm")
    print(f"🔍 区域: {len(zones_config['zones'])} 个\n")

    # Step 3: 渲染 PDF
    pdf_bytes = generate_pdf_from_zones(zones_config, product_data, args.output)

    # Step 4: 预览图
    if args.preview:
        preview_path = args.output.replace('.pdf', '_preview.png')
        render_preview(pdf_bytes, preview_path)

    print("\n✅ 完成！")


if __name__ == '__main__':
    main()
