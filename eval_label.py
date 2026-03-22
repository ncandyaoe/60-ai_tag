#!/usr/bin/env python3
"""
eval_label.py — 标签渲染质量自动评估（目标函数）

用 fitz 从 .ai 模板和渲染 PDF 提取相同的结构化特征，纯数值对比打分。
不需要 LLM，完全程序化。

用法:
  # 对比 .ai 模板和渲染 PDF
  python eval_label.py template.ai rendered.pdf

  # 一键：从 .ai 渲染 + 评估
  python eval_label.py template.ai product_data.json --render

评分维度 (per-zone, 满分 100):
  1. 行数匹配        (25分) — 每多/少一行扣分
  2. Y 位置对齐       (20分) — 每行 Y 坐标偏移量
  3. 字号匹配         (15分) — 最常见字号差异
  4. 文字完整性       (20分) — 字符覆盖率
  5. Zone 填充率      (10分) — 文字填充比例对比
  6. 行间距均匀性     (10分) — 标准差对比
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import Counter

import fitz  # PyMuPDF


# =====================================================================
# 常量
# =====================================================================
BLEED_MM = 2.0

# Zone 权重（用于总分加权）
ZONE_WEIGHTS = {
    'title': 25,
    'content': 30,
    'net_volume': 10,
    'nutrition': 25,
    'eco_icons': 10,
}

# Y 分组容差 (pt)
Y_GROUP_TOLERANCE = 2.0


def pt_to_mm(pt_val):
    return pt_val / 72 * 25.4


def mm_to_pt(mm_val):
    return mm_val / 25.4 * 72


# =====================================================================
# 特征提取
# =====================================================================

def _group_y_positions(y_list, tolerance=Y_GROUP_TOLERANCE):
    """将 Y 坐标按容差分组，返回去重后的行 Y 坐标列表"""
    if not y_list:
        return []
    sorted_ys = sorted(y_list)
    groups = [sorted_ys[0]]
    for y in sorted_ys[1:]:
        if y - groups[-1] > tolerance:
            groups.append(y)
    return groups


def _extract_images_in_zone(page, x1_pt, y1_pt, x2_pt, y2_pt, tolerance=5):
    """提取 zone 内的图像 bbox 信息"""
    images_in_zone = []
    try:
        img_info = page.get_image_info()
    except Exception:
        img_info = []
    zone_area = max(1, (x2_pt - x1_pt) * (y2_pt - y1_pt))
    for ii in img_info:
        bbox = ii.get('bbox', [0, 0, 0, 0])
        # 检测图像是否与 zone 重叠
        ix1 = max(bbox[0], x1_pt - tolerance)
        iy1 = max(bbox[1], y1_pt - tolerance)
        ix2 = min(bbox[2], x2_pt + tolerance)
        iy2 = min(bbox[3], y2_pt + tolerance)
        if ix1 < ix2 and iy1 < iy2:
            img_w = bbox[2] - bbox[0]
            img_h = bbox[3] - bbox[1]
            images_in_zone.append({
                'bbox': bbox,
                'width': img_w,
                'height': img_h,
                'area': img_w * img_h,
            })
    total_img_area = sum(img['area'] for img in images_in_zone)
    return {
        'image_count': len(images_in_zone),
        'total_image_area': total_img_area,
        'image_coverage': total_img_area / zone_area,  # 图像面积 / zone 面积
        'images': images_in_zone,
    }


def _extract_text_width_ratio(span_data, zone_w_pt):
    """计算文字视觉宽度占 zone 宽度的比例（用于 net_volume 等单行 zone）"""
    if not span_data or zone_w_pt <= 0:
        return 0
    # 找到主文字行的 max bbox width
    max_span_w = 0
    for s in span_data:
        sbbox = s.get('bbox')
        if sbbox:
            sw = sbbox[2] - sbbox[0]
            max_span_w = max(max_span_w, sw)
    return max_span_w / zone_w_pt


def extract_features(pdf_path, zones_config):
    """
    从 PDF 或 .ai 提取 per-zone 结构化特征。

    Args:
        pdf_path: PDF 或 .ai 文件路径
        zones_config: zones 配置 dict（含 label_size 和 zones）

    Returns:
        dict: {zone_id: {line_count, line_ys, main_font_size, total_chars, ...}}
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    text_dict = page.get_text('dict')
    drawings = page.get_drawings()

    zones = zones_config.get('zones', [])
    features = {}

    for zone in zones:
        zone_id = zone.get('id', '')
        if zone_id == 'logo':
            continue  # logo 是纯图像，不评估

        y1_mm = zone['y_mm']
        y2_mm = y1_mm + zone['h_mm']
        x1_mm = zone.get('x_mm', BLEED_MM)
        x2_mm = x1_mm + zone.get('w_mm', 46)

        y1_pt = mm_to_pt(y1_mm)
        y2_pt = mm_to_pt(y2_mm)
        x1_pt = mm_to_pt(x1_mm)
        x2_pt = mm_to_pt(x2_mm)
        zone_h_pt = y2_pt - y1_pt
        zone_w_pt = x2_pt - x1_pt

        # === eco_icons: 用图像特征代替文字特征 ===
        if zone_id == 'eco_icons':
            img_feats = _extract_images_in_zone(page, x1_pt, y1_pt, x2_pt, y2_pt)
            features[zone_id] = {
                'zone_type': 'image',
                'zone_height': zone_h_pt,
                'zone_width': zone_w_pt,
                **img_feats,
            }
            continue

        # === 文字型 zone: 收集 text spans ===
        span_data = []
        for block in text_dict.get('blocks', []):
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    sy = span['origin'][1]
                    sx = span['origin'][0]
                    sbbox = span.get('bbox', None)
                    if (y1_pt - 5 <= sy <= y2_pt + 5 and
                            x1_pt - 5 <= sx <= x2_pt + 5):
                        text = span.get('text', '').strip()
                        if text:
                            span_data.append({
                                'y': sy,
                                'x': sx,
                                'font_size': round(span['size'], 1),
                                'text': text,
                                'chars': len(text),
                                'bbox': sbbox,
                            })

        # 按 Y 分组计算行
        all_ys = [s['y'] for s in span_data]
        line_ys = _group_y_positions(all_ys)
        line_count = len(line_ys)

        # 字号统计
        font_sizes = [s['font_size'] for s in span_data]
        if font_sizes:
            fs_counter = Counter(font_sizes)
            main_font_size = fs_counter.most_common(1)[0][0]
        else:
            main_font_size = 0

        # 总字符数
        total_chars = sum(s['chars'] for s in span_data)

        # 文字实际占用高度
        if line_ys:
            text_top = min(line_ys)
            text_bottom = max(line_ys) + main_font_size
            text_height = text_bottom - text_top
        else:
            text_height = 0

        # 行间距
        line_spacings = []
        if len(line_ys) >= 2:
            for i in range(len(line_ys) - 1):
                line_spacings.append(line_ys[i + 1] - line_ys[i])

        # 文字视觉宽度比（主要用于 net_volume）
        text_width_ratio = _extract_text_width_ratio(span_data, zone_w_pt)

        # Nutrition 表格线
        h_line_ys = []
        if zone_id == 'nutrition':
            for d in drawings:
                items = d.get('items', [])
                for it in items:
                    if it[0] == 'l':
                        p1, p2 = it[1], it[2]
                        if (abs(p1.y - p2.y) < 2 and
                                y1_pt - 2 <= p1.y <= y2_pt + 2):
                            h_line_ys.append(p1.y)

        features[zone_id] = {
            'zone_type': 'text',
            'line_count': line_count,
            'line_ys': line_ys,
            'main_font_size': main_font_size,
            'total_chars': total_chars,
            'text_height': text_height,
            'zone_height': zone_h_pt,
            'zone_width': zone_w_pt,
            'line_spacings': line_spacings,
            'text_width_ratio': text_width_ratio,
            'h_line_ys': sorted(set(round(y, 0) for y in h_line_ys)),
            'font_sizes': sorted(set(font_sizes)),
            'span_count': len(span_data),
            'style_font_size': zone.get('style', {}).get('font_size', 0) or zone.get('style', {}).get('min_font_size', 0),
            'all_text': ' '.join(s['text'] for s in span_data),
        }

    doc.close()
    return features


# =====================================================================
# 评分函数
# =====================================================================

def score_zone_image(ref, gen, zone_id):
    """
    图像型 zone (eco_icons) 的评分，满分 100。

    维度:
      1. 图片数量匹配 (30分)
      2. 图像覆盖率匹配 (40分)
      3. 图像存在性 (30分)
    """
    scores = {}
    ref_count = ref.get('image_count', 0)
    gen_count = gen.get('image_count', 0)

    # --- 1. 图片数量匹配 (30分) ---
    # 设计师可能合并多图为1张复合图，或我们拆分为多文件
    # 只要双方都有图就给保底20分，差异仅扣剩余10分
    if ref_count > 0 and gen_count > 0:
        ratio = min(gen_count, ref_count) / max(gen_count, ref_count)
        scores['image_count'] = round(20 + 10 * ratio, 1)  # 20保底 + 10按比例
    elif ref_count == 0 and gen_count == 0:
        scores['image_count'] = 30
    elif ref_count > 0 and gen_count == 0:
        scores['image_count'] = 0
    else:
        scores['image_count'] = 15

    # --- 2. 图像覆盖率匹配 (40分) ---
    ref_cov = ref.get('image_coverage', 0)
    gen_cov = gen.get('image_coverage', 0)
    if ref_cov > 0:
        cov_ratio = min(gen_cov / ref_cov, 1.5) if ref_cov > 0 else 0
        # 覆盖率越接近参考值越好
        cov_diff = abs(1.0 - cov_ratio)
        scores['image_coverage'] = max(0, round(40 * (1 - cov_diff), 1))
    else:
        scores['image_coverage'] = 40 if gen_cov == 0 else 20

    # --- 3. 图像存在性 (30分) ---
    if ref_count > 0 and gen_count > 0:
        scores['image_presence'] = 30
    elif ref_count == 0 and gen_count == 0:
        scores['image_presence'] = 30
    elif ref_count > 0 and gen_count == 0:
        scores['image_presence'] = 0  # 参考有图，渲染无图
    else:
        scores['image_presence'] = 15

    scores['total'] = round(sum(scores.values()), 1)
    return scores


def score_zone(ref, gen, zone_id):
    """
    文字型 zone 的评分，返回 {维度名: 分数} + total，满分 100。

    Args:
        ref: 参考特征 (from .ai)
        gen: 渲染特征 (from rendered PDF)
        zone_id: zone 名称
    """
    # 图像型 zone 走专用评分
    if ref.get('zone_type') == 'image' or gen.get('zone_type') == 'image':
        return score_zone_image(ref, gen, zone_id)

    scores = {}

    # --- 1. 行数匹配 (15分, 原 20) ---
    ref_lines = ref['line_count']
    gen_lines = gen['line_count']
    if ref_lines > 0:
        line_diff = abs(ref_lines - gen_lines)
        penalty_per_line = 15 / ref_lines
        scores['line_count'] = max(0, round(15 - line_diff * penalty_per_line, 1))
    else:
        scores['line_count'] = 15 if gen_lines == 0 else 0

    # --- 2. Y 位置对齐 (15分, 原 20) ---
    if ref['line_ys'] and gen['line_ys']:
        if ref_lines == gen_lines and ref_lines > 0:
            y_errors = [abs(r - g) for r, g in zip(ref['line_ys'], gen['line_ys'])]
            avg_err_mm = pt_to_mm(sum(y_errors) / len(y_errors))
        else:
            first_err = abs(ref['line_ys'][0] - gen['line_ys'][0])
            last_err = abs(ref['line_ys'][-1] - gen['line_ys'][-1])
            avg_err_mm = pt_to_mm((first_err + last_err) / 2)
        scores['y_alignment'] = max(0, round(15 - avg_err_mm * 4, 1))
    else:
        scores['y_alignment'] = 0 if (ref['line_ys'] or gen['line_ys']) else 15

    # --- 3. 字号匹配 (15分) ---
    ref_fs = ref['main_font_size']
    gen_fs = gen.get('style_font_size', 0) or gen['main_font_size']
    if ref_fs > 0 and gen_fs > 0:
        fs_diff = abs(ref_fs - gen_fs)
        scores['font_size'] = max(0, round(15 - fs_diff * 10, 1))
    else:
        scores['font_size'] = 0 if ref_fs > 0 else 15

    # --- 4. 文字完整性 (20分) ---
    if ref['total_chars'] > 0:
        coverage = min(gen['total_chars'] / ref['total_chars'], 1.2)
        if coverage < 0.8:
            scores['completeness'] = round(coverage * 15, 1)
        else:
            scores['completeness'] = round(min(coverage, 1.0) * 20, 1)
    else:
        scores['completeness'] = 20 if gen['total_chars'] == 0 else 15

    # --- 5. Zone 填充率 (10分) — 罚分加重 ---
    ref_fill = ref['text_height'] / ref['zone_height'] if ref['zone_height'] > 0 else 0
    gen_fill = gen['text_height'] / gen['zone_height'] if gen['zone_height'] > 0 else 0
    fill_diff = abs(ref_fill - gen_fill)
    scores['fill_ratio'] = max(0, round(10 - fill_diff * 30, 1))  # 30→更严格

    # --- 6. 行间距均匀性 (10分) — 罚分加重 ---
    ref_spacings = ref.get('line_spacings', [])
    gen_spacings = gen.get('line_spacings', [])
    if len(ref_spacings) >= 2 and len(gen_spacings) >= 2:
        ref_mean = statistics.mean(ref_spacings)
        gen_mean = statistics.mean(gen_spacings)
        mean_diff_mm = pt_to_mm(abs(ref_mean - gen_mean))
        scores['spacing_uniformity'] = max(0, round(10 - mean_diff_mm * 6, 1))  # 6→更严格
    elif len(ref_spacings) < 2 and len(gen_spacings) < 2:
        scores['spacing_uniformity'] = 10
    else:
        scores['spacing_uniformity'] = 5

    # --- 7. 文字视觉宽度比 (10分, 新增) ---
    ref_tw = ref.get('text_width_ratio', 0)
    gen_tw = gen.get('text_width_ratio', 0)
    if ref_tw > 0 and gen_tw > 0:
        tw_diff = abs(ref_tw - gen_tw)
        scores['text_width'] = max(0, round(10 - tw_diff * 30, 1))
    elif ref_tw == 0 and gen_tw == 0:
        scores['text_width'] = 10
    else:
        scores['text_width'] = 5

    # --- 8. nutrition 关键行完整性 (5分, 仅 nutrition zone) ---
    if zone_id == 'nutrition':
        NUTRITION_KEYWORDS = ['energy', 'fat', 'carbohydrate', 'protein', 'salt']
        ref_text = ref.get('all_text', '').lower()
        gen_text = gen.get('all_text', '').lower()
        # 只检查参考里存在的关键词
        ref_kws = [kw for kw in NUTRITION_KEYWORDS if kw in ref_text]
        if ref_kws:
            gen_found = sum(1 for kw in ref_kws if kw in gen_text)
            scores['data_rows'] = round(5 * gen_found / len(ref_kws), 1)
        else:
            scores['data_rows'] = 5
    
    scores['total'] = round(sum(scores.values()), 1)
    return scores


def score_label(ref_features, gen_features):
    """
    总分 + 逐 zone 逐维度报告。

    Returns:
        {
            'zones': {zone_id: {维度: 分数, total: 分数}},
            'total': 加权总分,
            'details': 人类可读报告字符串,
        }
    """
    zone_scores = {}
    weighted_sum = 0
    total_weight = 0

    for zone_id in ref_features:
        if zone_id not in gen_features:
            continue
        ref = ref_features[zone_id]
        gen = gen_features[zone_id]
        scores = score_zone(ref, gen, zone_id)
        zone_scores[zone_id] = scores

        weight = ZONE_WEIGHTS.get(zone_id, 10)
        weighted_sum += scores['total'] * weight
        total_weight += weight

    total_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0

    # 生成报告
    lines = []
    lines.append(f"\n{'='*65}")
    lines.append(f"📊 标签渲染质量评估报告")
    lines.append(f"{'='*65}")
    lines.append(f"\n🏆 总分: {total_score}/100\n")

    # 文字型维度
    text_dim_names = {
        'line_count': '行数匹配',
        'y_alignment': 'Y位置对齐',
        'font_size': '字号匹配',
        'completeness': '文字完整',
        'fill_ratio': '填充比例',
        'spacing_uniformity': '间距均匀',
        'text_width': '视觉宽度',
        'data_rows': '关键行',
    }
    text_dim_max = {
        'line_count': 15, 'y_alignment': 15, 'font_size': 15,
        'completeness': 20, 'fill_ratio': 10, 'spacing_uniformity': 10,
        'text_width': 10, 'data_rows': 5,
    }

    # 图像型维度
    image_dim_names = {
        'image_count': '图片数量',
        'image_coverage': '覆盖率',
        'image_presence': '图片存在',
    }
    image_dim_max = {
        'image_count': 30, 'image_coverage': 40, 'image_presence': 30,
    }

    for zone_id in ['title', 'content', 'net_volume', 'nutrition', 'eco_icons']:
        if zone_id not in zone_scores:
            continue
        scores = zone_scores[zone_id]
        ref = ref_features[zone_id]
        gen = gen_features[zone_id]
        weight = ZONE_WEIGHTS.get(zone_id, 10)
        is_image = ref.get('zone_type') == 'image'

        lines.append(f"{'─'*65}")
        lines.append(f"📦 {zone_id} (权重{weight}) — 得分: {scores['total']}/100")
        lines.append(f"{'─'*65}")

        if is_image:
            # 图像型 zone 报告
            for dim_key, dim_label in image_dim_names.items():
                score = scores.get(dim_key, 0)
                max_score = image_dim_max[dim_key]
                bar = '█' * int(score / max_score * 10) + '░' * (10 - int(score / max_score * 10))
                lines.append(f"  {dim_label:8} {bar} {score:5.1f}/{max_score}")
            lines.append(f"  ── 对比明细 ──")
            lines.append(f"  图片:  参考={ref.get('image_count',0)}个  渲染={gen.get('image_count',0)}个")
            ref_cov = ref.get('image_coverage', 0) * 100
            gen_cov = gen.get('image_coverage', 0) * 100
            lines.append(f"  覆盖:  参考={ref_cov:.0f}%  渲染={gen_cov:.0f}%")
        else:
            # 文字型 zone 报告
            for dim_key, dim_label in text_dim_names.items():
                if dim_key == 'data_rows' and zone_id != 'nutrition':
                    continue  # 关键行仅 nutrition 显示
                score = scores.get(dim_key, 0)
                max_score = text_dim_max[dim_key]
                bar = '█' * int(score / max_score * 10) + '░' * (10 - int(score / max_score * 10))
                lines.append(f"  {dim_label:8} {bar} {score:5.1f}/{max_score}")
            lines.append(f"  ── 对比明细 ──")
            lines.append(f"  行数:  参考={ref['line_count']}  渲染={gen['line_count']}")
            lines.append(f"  字号:  参考={ref['main_font_size']}pt  渲染={gen['main_font_size']}pt")
            lines.append(f"  字符:  参考={ref['total_chars']}  渲染={gen['total_chars']}")
            ref_fill = ref['text_height'] / ref['zone_height'] * 100 if ref['zone_height'] > 0 else 0
            gen_fill = gen['text_height'] / gen['zone_height'] * 100 if gen['zone_height'] > 0 else 0
            lines.append(f"  填充:  参考={ref_fill:.0f}%  渲染={gen_fill:.0f}%")
            if ref.get('line_spacings') and gen.get('line_spacings'):
                ref_sp = pt_to_mm(statistics.mean(ref['line_spacings']))
                gen_sp = pt_to_mm(statistics.mean(gen['line_spacings']))
                lines.append(f"  行距:  参考={ref_sp:.2f}mm  渲染={gen_sp:.2f}mm")
            ref_tw = ref.get('text_width_ratio', 0)
            gen_tw = gen.get('text_width_ratio', 0)
            if ref_tw > 0 or gen_tw > 0:
                lines.append(f"  宽度:  参考={ref_tw:.0%}  渲染={gen_tw:.0%}")
        lines.append("")

    # 最差维度分析
    lines.append(f"{'='*65}")
    lines.append(f"🔍 最差维度排名（全局）")
    lines.append(f"{'='*65}")

    all_dim_scores = []
    for zone_id, scores in zone_scores.items():
        ref = ref_features.get(zone_id, {})
        is_image = ref.get('zone_type') == 'image'
        dim_map = image_dim_names if is_image else text_dim_names
        max_map = image_dim_max if is_image else text_dim_max
        for dim_key, dim_label in dim_map.items():
            if dim_key == 'data_rows' and zone_id != 'nutrition':
                continue
            max_score = max_map[dim_key]
            s = scores.get(dim_key, 0)
            pct = s / max_score * 100 if max_score > 0 else 100
            all_dim_scores.append((zone_id, dim_label, pct, s, max_score))

    all_dim_scores.sort(key=lambda x: x[2])
    for zone_id, dim_label, pct, score, max_s in all_dim_scores[:5]:
        status = '🔴' if pct < 50 else '🟡' if pct < 80 else '🟢'
        lines.append(f"  {status} {zone_id}.{dim_label}: {score}/{max_s} ({pct:.0f}%)")

    report = '\n'.join(lines)
    return {
        'zones': zone_scores,
        'total': total_score,
        'details': report,
    }


# =====================================================================
# CLI
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description='标签渲染质量自动评估 — .ai 模板 vs 渲染 PDF 对比')
    parser.add_argument('reference', help='.ai 模板文件')
    parser.add_argument('target', help='渲染 PDF 文件，或 product_data.json（配合 --render）')
    parser.add_argument('--render', action='store_true',
                        help='先渲染再评估（target 为 product_data.json）')
    parser.add_argument('-o', '--output', default=None,
                        help='评估报告输出路径 (JSON)')
    args = parser.parse_args()

    # 解析 .ai → zones_config
    from ai_parser_annotated import (
        scan_annotations, build_zones_from_annotations, extract_zone_styles
    )

    ai_path = args.reference
    annotations, pw_mm, ph_mm = scan_annotations(ai_path, verbose=False)
    matched = [a for a in annotations if a['zone_id']]
    zones_list = build_zones_from_annotations(matched, pw_mm, ph_mm)
    extract_zone_styles(ai_path, zones_list, pw_mm, ph_mm)

    zones_config = {
        'label_size': {'width_mm': pw_mm, 'height_mm': ph_mm},
        'zones': zones_list,
    }

    # 提取参考特征（从 .ai）
    print(f"\n📐 从 .ai 提取参考特征...")
    ref_features = extract_features(ai_path, zones_config)

    # 渲染或直接读取 target
    if args.render:
        from generate_from_zones import generate_pdf_from_zones
        with open(args.target, 'r', encoding='utf-8') as f:
            product_data = json.load(f)

        print(f"🖨️  渲染 PDF...")
        tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        pdf_bytes = generate_pdf_from_zones(zones_config, product_data)
        tmp.write(pdf_bytes)
        tmp.close()
        rendered_path = tmp.name
    else:
        rendered_path = args.target

    # 提取渲染特征
    print(f"📄 从渲染 PDF 提取特征...")
    gen_features = extract_features(rendered_path, zones_config)

    # 计算分数
    result = score_label(ref_features, gen_features)
    print(result['details'])

    # 清理临时文件
    if args.render:
        os.unlink(rendered_path)

    # 保存 JSON 报告
    if args.output:
        report = {
            'total_score': result['total'],
            'zones': result['zones'],
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n💾 报告已保存: {args.output}")

    return result['total']


if __name__ == '__main__':
    try:
        score = main()
        sys.exit(0 if score >= 85 else 1)
    except Exception as e:
        import traceback
        print(f"\n💥 评估异常: {e}")
        traceback.print_exc()
        sys.exit(2)
