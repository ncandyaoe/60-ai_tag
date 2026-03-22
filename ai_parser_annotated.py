#!/usr/bin/env python3
"""
ai_parser_annotated.py — 从设计师标注的 .ai 文件中读取区域坐标

设计师在 .ai 文件中用不同颜色的矩形标注各区域：
  🔴 红色   → title     (标题)
  🔵 蓝色   → content   (正文/配料)
  🟢 绿色   → net_volume(净含量)
  🟣 紫色   → nutrition  (营养表)
  🟡 黄色   → logo      (品牌/商标)
  🟤 青色   → eco_icons  (环保标识)

用法:
  python ai_parser_annotated.py template.ai --preview -o output.yaml
  python ai_parser_annotated.py template.ai --scan   # 仅扫描，列出所有矩形色值
"""

import argparse
import math
import os
import yaml
import fitz  # PyMuPDF

# =====================================================================
# 颜色→Zone 映射（HSV 容差匹配）
# =====================================================================

# 定义标准色 → zone_id（RGB 0-1 浮点）
# H = 目标色相, s_min/v_min = 饱和度/明度最低门槛
COLOR_MAP = {
    'title':      {'h': 0,   's_min': 0.5, 'v_min': 0.5},   # 红 (H ≈ 0/360)
    'content':    {'h': 210, 's_min': 0.4, 'v_min': 0.3},   # 蓝 (H ≈ 200-240)
    'net_volume': {'h': 120, 's_min': 0.5, 'v_min': 0.3},   # 绿 (H ≈ 100-150)
    'nutrition':  {'h': 290, 's_min': 0.3, 'v_min': 0.3},   # 紫/品红 (H ≈ 270-310)
    'logo':       {'h': 50,  's_min': 0.5, 'v_min': 0.5},   # 黄 (H ≈ 40-70)
    'eco_icons':  {'h': 175, 's_min': 0.7, 'v_min': 0.4},   # 青 (H ≈ 170-190, 高饱和度)
}

# 色相容差（度）
HUE_TOLERANCE = 35

BLEED_MM = 2.0


def rgb_to_hsv(r, g, b):
    """RGB (0-1) → HSV (H: 0-360, S: 0-1, V: 0-1)"""
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    v = mx
    s = 0 if mx == 0 else d / mx
    if d == 0:
        h = 0
    elif mx == r:
        h = 60 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60 * (((b - r) / d) + 2)
    else:
        h = 60 * (((r - g) / d) + 4)
    return h, s, v


def match_color(rgb_tuple):
    """
    将 RGB 颜色元组匹配到 zone_id。
    返回 (zone_id, confidence) 或 (None, 0)。
    """
    if rgb_tuple is None or len(rgb_tuple) < 3:
        return None, 0

    r, g, b = rgb_tuple[:3]
    h, s, v = rgb_to_hsv(r, g, b)

    best_match = None
    best_diff = 999

    for zone_id, spec in COLOR_MAP.items():
        target_h = spec['h']
        # 色相差（环形）
        h_diff = abs(h - target_h)
        if h_diff > 180:
            h_diff = 360 - h_diff
        # 饱和度和明度阈值
        if s < spec['s_min'] or v < spec['v_min']:
            continue
        if h_diff <= HUE_TOLERANCE and h_diff < best_diff:
            best_diff = h_diff
            best_match = zone_id

    confidence = round(1.0 - best_diff / HUE_TOLERANCE, 2) if best_match else 0
    return best_match, confidence


def pt_to_mm(pt_val):
    """Points → mm"""
    return pt_val / 72 * 25.4


def mm_to_pt(mm_val):
    """mm → Points"""
    return mm_val / 25.4 * 72


# =====================================================================
# 核心: 从 .ai 读取标注矩形
# =====================================================================

def scan_annotations(ai_path: str, verbose=False):
    """
    扫描 .ai 文件中所有矢量矩形，返回:
    [{'zone_id': str, 'rect_pt': fitz.Rect, 'color_rgb': tuple, 'confidence': float}, ...]
    """
    doc = fitz.open(ai_path)
    page = doc[0]
    pw_pt, ph_pt = page.rect.width, page.rect.height
    pw_mm, ph_mm = pt_to_mm(pw_pt), pt_to_mm(ph_pt)

    print(f"📂 扫描: {os.path.basename(ai_path)}")
    print(f"  📐 页面: {pw_mm:.1f} × {ph_mm:.1f} mm")

    drawings = page.get_drawings()
    rects = []

    for d in drawings:
        items = d.get('items', [])
        # 跳过纯线条（只有 1 个 'l'）
        if len(items) < 2 and not any(it[0] == 're' for it in items):
            continue

        # 必须有 fill 颜色（有填充的路径才是标注区域）
        color = d.get('fill')
        if not color or len(color) < 3:
            continue

        rect = d.get('rect')
        if not rect:
            continue

        # 过滤掉几乎全页大小的矩形（是页面边框，不是标注）
        r = fitz.Rect(rect)
        if r.width > pw_pt * 0.95 and r.height > ph_pt * 0.95:
            continue

        # 过滤掉太小的矩形（< 2mm 任一边）
        if pt_to_mm(r.width) < 2 or pt_to_mm(r.height) < 2:
            continue

        zone_id, confidence = match_color(color)
        h, s, v = rgb_to_hsv(*color[:3])

        entry = {
            'zone_id': zone_id,
            'rect_pt': r,
            'color_rgb': tuple(round(c, 3) for c in color[:3]),
            'color_hsv': (round(h, 1), round(s, 2), round(v, 2)),
            'confidence': confidence,
        }
        rects.append(entry)

        if verbose:
            tag = f"→ {zone_id}" if zone_id else "→ ?"
            x1, y1 = pt_to_mm(r.x0), pt_to_mm(r.y0)
            x2, y2 = pt_to_mm(r.x1), pt_to_mm(r.y1)
            print(f"    🟥 ({x1:.1f},{y1:.1f})→({x2:.1f},{y2:.1f}) "
                  f"RGB={entry['color_rgb']} HSV=({h:.0f}°,{s:.0%},{v:.0%}) "
                  f"{tag} ({confidence:.0%})")

    doc.close()
    return rects, pw_mm, ph_mm


def build_zones_from_annotations(annotations, pw_mm, ph_mm):
    """标注矩形 → Zone 列表（YAML 友好的 dict）

    构建后自动检测重叠，添加 avoid_zone 和调整宽度。
    """
    zones = []
    seen = set()

    for ann in sorted(annotations, key=lambda a: a['rect_pt'].y0):
        zone_id = ann['zone_id']
        if not zone_id or zone_id in seen:
            continue  # 跳过未识别颜色和重复 zone
        seen.add(zone_id)

        r = ann['rect_pt']
        x_mm = round(pt_to_mm(r.x0), 1)
        y_mm = round(pt_to_mm(r.y0), 1)
        w_mm = round(pt_to_mm(r.width), 1)
        h_mm = round(pt_to_mm(r.height), 1)

        # 出血位裁剪
        if y_mm < BLEED_MM:
            h_mm -= (BLEED_MM - y_mm)
            y_mm = BLEED_MM
        if y_mm + h_mm > ph_mm - BLEED_MM:
            h_mm = ph_mm - BLEED_MM - y_mm
        if x_mm < BLEED_MM:
            w_mm -= (BLEED_MM - x_mm)
            x_mm = BLEED_MM
        if x_mm + w_mm > pw_mm - BLEED_MM:
            w_mm = pw_mm - BLEED_MM - x_mm

        h_mm = round(max(h_mm, 0.1), 1)
        w_mm = round(max(w_mm, 0.1), 1)

        # Zone 类型推断
        type_map = {
            'title': 'title',
            'content': 'flow',
            'net_volume': 'value',
            'nutrition': 'table',
            'logo': 'static',
            'eco_icons': 'static',
        }

        zone = {
            'id': zone_id,
            'type': type_map.get(zone_id, 'static'),
            'y_mm': round(y_mm, 1),
            'h_mm': round(h_mm, 1),
            'x_mm': round(x_mm, 1),
            'w_mm': round(w_mm, 1),
        }

        zones.append(zone)

    # ====================================================================
    # 重叠检测后处理
    # 保留 zone 的完整 bbox，但记录障碍物的精确位置
    # 渲染器根据 obstacle 信息将 L 型 zone 拆为两个子矩形绘制
    # ====================================================================
    zone_by_id = {z['id']: z for z in zones}

    def _rects_overlap(a, b):
        """检查两个 zone 是否在 x+y 方向都重叠"""
        a_x1 = a.get('x_mm', BLEED_MM)
        a_x2 = a_x1 + a.get('w_mm', pw_mm - 2 * BLEED_MM)
        a_y1 = a['y_mm']
        a_y2 = a_y1 + a['h_mm']
        b_x1 = b.get('x_mm', BLEED_MM)
        b_x2 = b_x1 + b.get('w_mm', pw_mm - 2 * BLEED_MM)
        b_y1 = b['y_mm']
        b_y2 = b_y1 + b['h_mm']
        return (a_x1 < b_x2 and a_x2 > b_x1 and
                a_y1 < b_y2 and a_y2 > b_y1)

    # 定义避让优先级：大 zone 要避让小 zone
    avoid_rules = [
        ('title', 'logo'),
        ('title', 'net_volume'),
        ('content', 'logo'),
        ('content', 'net_volume'),
    ]

    for big_id, small_id in avoid_rules:
        big = zone_by_id.get(big_id)
        small = zone_by_id.get(small_id)
        if not big or not small:
            continue
        if not _rects_overlap(big, small):
            continue

        # 记录障碍物的精确坐标（相对于页面的 mm 坐标）
        obstacle = {
            'zone_id': small_id,
            'x_mm': small['x_mm'],
            'y_mm': small['y_mm'],
            'w_mm': small['w_mm'],
            'h_mm': small['h_mm'],
        }

        # 附加到大 zone（可能有多个 obstacle）
        if 'obstacles' not in big:
            big['obstacles'] = []
        big['obstacles'].append(obstacle)

    # 全宽简化：如果宽度 > 页面 90% 且没有 obstacle → 移除 x/w
    content_w = pw_mm - 2 * BLEED_MM
    for z in zones:
        if z.get('w_mm', 0) > content_w * 0.9 and 'obstacles' not in z:
            z.pop('x_mm', None)
            z.pop('w_mm', None)

    return zones


def extract_zone_styles(ai_path: str, zones: list, pw_mm: float, ph_mm: float):
    """从 .ai 模板中提取每个 zone 内的样式信息（字号、线宽、列宽、行高等）。

    直接挂载到 zone dict 的 'style' key 上。
    """
    doc = fitz.open(ai_path)
    page = doc[0]
    drawings = page.get_drawings()
    text_dict = page.get_text('dict')

    for zone in zones:
        zone_id = zone['id']
        y1_mm = zone['y_mm']
        y2_mm = y1_mm + zone['h_mm']
        x1_mm = zone.get('x_mm', BLEED_MM)
        x2_mm = x1_mm + zone.get('w_mm', pw_mm - 2 * BLEED_MM)

        # 转为 pt（fitz 坐标系 = 左上角原点）
        y1_pt = mm_to_pt(y1_mm)
        y2_pt = mm_to_pt(y2_mm)
        x1_pt = mm_to_pt(x1_mm)
        x2_pt = mm_to_pt(x2_mm)

        # --- 提取文字样式 ---
        font_sizes = []
        font_names = set()
        span_y_positions = []  # 用于计算行数
        for block in text_dict.get('blocks', []):
            if block.get('type') != 0:
                continue
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    sy = span['origin'][1]
                    sx = span['origin'][0]
                    # 扩大 Y 范围搜索（模板文字可能略超出 zone 边界）
                    if y1_pt - 5 <= sy <= y2_pt + 2 and x1_pt - 5 <= sx <= x2_pt + 5:
                        fs = round(span['size'], 1)
                        font_sizes.append(fs)
                        font_names.add(span['font'])
                        span_y_positions.append(sy)

        # --- 提取线条样式 ---
        h_lines = []  # 水平线 y 位置 (mm)
        v_lines = []  # 垂直线 x 位置 (mm)
        border_widths = []

        for d in drawings:
            rect = d.get('rect')
            if not rect:
                continue
            r = fitz.Rect(rect)

            # 只看与 zone 重叠的绘图
            if r.y1 < y1_pt - 5 or r.y0 > y2_pt + 5:
                continue

            lw = d.get('width') or 0
            items = d.get('items', [])
            for it in items:
                if it[0] == 'l':  # 线段
                    p1, p2 = it[1], it[2]
                    if (abs(p1.y - p2.y) < 2 and
                            y1_pt - 2 <= p1.y <= y2_pt + 2):
                        h_lines.append({
                            'y_mm': round(pt_to_mm(p1.y), 1),
                            'width_pt': round(lw, 2)
                        })
                    elif (abs(p1.x - p2.x) < 2 and
                          x1_pt - 2 <= p1.x <= x2_pt + 2):
                        v_lines.append({
                            'x_mm': round(pt_to_mm(p1.x), 1),
                            'width_pt': round(lw, 2)
                        })
                elif it[0] == 're':  # 矩形
                    border_widths.append(round(lw, 2))

        # --- 组装 style ---
        style = {}

        if font_sizes:
            unique_fs = sorted(set(font_sizes))
            style['font_sizes'] = unique_fs
            style['fonts'] = sorted(font_names)

        # --- zone-specific 样式规则 ---
        from collections import Counter

        if zone_id == 'title' and font_sizes:
            # title: 最常见字号 → min_font_size（确保不比模板小）
            fs_counter = Counter(font_sizes)
            most_common_fs = fs_counter.most_common(1)[0][0]
            style['min_font_size'] = most_common_fs
            # 计算模板行数：只统计该字号的文字行的 Y 坐标
            title_y = [y for y, fs in zip(span_y_positions, font_sizes)
                       if abs(fs - most_common_fs) < 0.5]
            if title_y:
                # 按 2pt 容差分组 Y 坐标
                title_y_sorted = sorted(set(round(y, 0) for y in title_y))
                distinct_lines = [title_y_sorted[0]]
                for yy in title_y_sorted[1:]:
                    if yy - distinct_lines[-1] > 2:
                        distinct_lines.append(yy)
                style['line_count'] = len(distinct_lines)

        elif zone_id == 'content' and font_sizes:
            # content: 过滤掉 net_volume 的大字号（10+ pt），取最常见小字号
            content_fs = [fs for fs in font_sizes if fs < 8]
            if content_fs:
                fs_counter = Counter(content_fs)
                style['min_font_size'] = fs_counter.most_common(1)[0][0]

        elif zone_id == 'net_volume' and font_sizes:
            # net_volume: 最大字号 → 固定字号
            style['font_size'] = max(font_sizes)

        elif zone_id == 'nutrition' and font_sizes:
            # nutrition: 按出现频率分组
            fs_counter = Counter(font_sizes)
            most_common_fs = fs_counter.most_common(1)[0][0]
            style['data_font_size'] = most_common_fs
            all_unique = sorted(set(font_sizes))
            style['header_font_size'] = all_unique[0]
            style['value_font_size'] = all_unique[-1]

        elif zone_id == 'eco_icons':
            # eco_icons: 从图像块提取图标尺寸
            for block in text_dict.get('blocks', []):
                if block.get('type') == 1:  # image block
                    bbox = block.get('bbox', (0, 0, 0, 0))
                    img_y = bbox[1]
                    if y1_pt - 5 <= img_y <= y2_pt + 5:
                        style['icon_area_w_mm'] = round(pt_to_mm(bbox[2] - bbox[0]), 1)
                        style['icon_area_h_mm'] = round(pt_to_mm(bbox[3] - bbox[1]), 1)
                        break
            # 也检查独立绘图矩形（单个图标~6-7mm方形）
            icon_rects = []
            for d in drawings:
                rect = d.get('rect')
                if not rect:
                    continue
                r = fitz.Rect(rect)
                rh = pt_to_mm(r.height)
                rw = pt_to_mm(r.width)
                if (y1_pt - 2 <= r.y0 <= y2_pt + 2 and
                        3 < rh < 15 and 3 < rw < 15):  # 合理的图标尺寸
                    icon_rects.append(rh)
            if icon_rects:
                style['icon_height_mm'] = round(max(icon_rects), 1)

        if h_lines:
            h_lines_sorted = sorted(h_lines, key=lambda x: x['y_mm'])
            # 计算行高
            if len(h_lines_sorted) >= 2:
                row_heights = [round(h_lines_sorted[i+1]['y_mm'] - h_lines_sorted[i]['y_mm'], 1)
                               for i in range(len(h_lines_sorted) - 1)]
                style['row_heights_mm'] = row_heights
                style['avg_row_height_mm'] = round(sum(row_heights) / len(row_heights), 1)
            # 过滤掉 width=0 的行线
            valid_h_lines = [h for h in h_lines_sorted if h['width_pt'] > 0]
            if valid_h_lines:
                style['line_width_pt'] = valid_h_lines[0]['width_pt']
            else:
                style['line_width_pt'] = 0.25  # 默认值

        if v_lines:
            # 列分隔的 x 位置：过滤掉与 zone 左右边缘重合的垂直线
            zone_w = zone.get('w_mm', pw_mm - 2 * BLEED_MM)
            zone_x = zone.get('x_mm', BLEED_MM)
            zone_right = zone_x + zone_w
            interior_v = [vl for vl in v_lines
                          if vl['x_mm'] > zone_x + 2 and vl['x_mm'] < zone_right - 2]
            if interior_v:
                vl_x = interior_v[0]['x_mm']
                col_ratio = round((vl_x - zone_x) / zone_w, 3)
                style['col_sep_x_mm'] = vl_x
                style['name_col_ratio'] = col_ratio

        if border_widths:
            valid_bw = [bw for bw in border_widths if bw > 0]
            style['border_width_pt'] = max(valid_bw) if valid_bw else 0.25

        if style:
            zone['style'] = style

    doc.close()


def zones_to_yaml(zones, pw_mm, ph_mm, ai_path):
    """生成 YAML 配置"""
    name = os.path.splitext(os.path.basename(ai_path))[0]
    # 截断过长的名字
    if len(name) > 50:
        name = name[:50]

    config = {
        'template_id': name,
        'display_name': f'Annotated ({pw_mm:.0f}×{ph_mm:.0f}mm)',
        'label_size': {
            'width_mm': pw_mm,
            'height_mm': ph_mm,
            'margin_mm': BLEED_MM,
        },
        'zones': zones,
    }
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


# =====================================================================
# 预览渲染
# =====================================================================

def render_preview(ai_path, zones, pw_mm, ph_mm, output_path=None):
    """在设计稿上画出 zone 边框"""
    doc = fitz.open(ai_path)
    page = doc[0]

    colors = {
        'title':      (1, 0, 0),
        'logo':       (0, 0.5, 0),
        'content':    (0, 0, 1),
        'nutrition':  (0.5, 0, 0.5),
        'eco_icons':  (0, 0.3, 0.3),
        'net_volume': (0, 0.6, 0),
    }

    for z in zones:
        color = colors.get(z['id'], (0.5, 0.5, 0.5))
        y = mm_to_pt(z['y_mm'])
        h = mm_to_pt(z['h_mm'])

        if 'x_mm' in z:
            x = mm_to_pt(z['x_mm'])
            w = mm_to_pt(z['w_mm'])
        else:
            x = mm_to_pt(BLEED_MM)
            w = page.rect.width - 2 * mm_to_pt(BLEED_MM)

        rect = fitz.Rect(x, y, x + w, y + h)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(color=color, fill=color, fill_opacity=0.15, width=1.5)
        shape.commit()

        label = f"{z['id']} ({z['type']})"
        try:
            shape2 = page.new_shape()
            shape2.insert_text(fitz.Point(rect.x0 + 2, rect.y0 + 10),
                               label, fontsize=7, color=color)
            shape2.commit()
        except Exception:
            pass

    if not output_path:
        output_path = ai_path.replace('.ai', '_annotated_zones.png')

    pix = page.get_pixmap(dpi=300)
    pix.save(output_path)
    doc.close()
    print(f"🖼️  预览图: {output_path}")
    return output_path


# =====================================================================
# 主流程
# =====================================================================

def parse_annotated(ai_path, output_yaml=None, preview=False, scan_only=False):
    """主入口"""
    annotations, pw_mm, ph_mm = scan_annotations(ai_path, verbose=True)

    matched = [a for a in annotations if a['zone_id']]
    unmatched = [a for a in annotations if not a['zone_id']]

    print(f"\n📊 扫描结果: {len(matched)} 个标注矩形识别, {len(unmatched)} 个未识别")

    if scan_only:
        if unmatched:
            print("\n⚠️  未识别矩形（颜色不在映射表中）:")
            for a in unmatched:
                r = a['rect_pt']
                x1, y1 = pt_to_mm(r.x0), pt_to_mm(r.y0)
                x2, y2 = pt_to_mm(r.x1), pt_to_mm(r.y1)
                print(f"    ({x1:.1f},{y1:.1f})→({x2:.1f},{y2:.1f}) "
                      f"RGB={a['color_rgb']} HSV={a['color_hsv']}")
        return

    if not matched:
        print("❌ 未找到任何标注矩形！请在 .ai 中用彩色矩形标注区域。")
        print("   颜色映射: 红=title 蓝=content 绿=net_volume 紫=nutrition 黄=logo 青=eco_icons")
        return

    # 构建 zones
    zones = build_zones_from_annotations(matched, pw_mm, ph_mm)

    print(f"\n🔍 识别区域: {len(zones)} 个")
    print("-" * 50)
    for z in zones:
        xw = f" x={z.get('x_mm','-'):.1f} w={z.get('w_mm','-'):.1f}" if 'x_mm' in z else ""
        print(f"  [{z['type']:<6}] {z['id']:<12} y={z['y_mm']:>5.1f}mm "
              f" h={z['h_mm']:>5.1f}mm{xw}")

    # YAML
    yaml_str = zones_to_yaml(zones, pw_mm, ph_mm, ai_path)
    print(f"\n📝 YAML:\n{'-'*50}")
    print(yaml_str)

    if output_yaml:
        with open(output_yaml, 'w') as f:
            f.write(yaml_str)
        print(f"💾 已保存: {output_yaml}")

    # 预览图
    if preview:
        render_preview(ai_path, zones, pw_mm, ph_mm)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='从设计师标注的 .ai 文件读取 zone 坐标')
    parser.add_argument('ai_file', help='.ai 文件路径')
    parser.add_argument('-o', '--output', help='YAML 输出路径')
    parser.add_argument('--preview', action='store_true', help='生成预览图')
    parser.add_argument('--scan', action='store_true',
                        help='仅扫描，列出所有矩形色值（不生成 zone）')
    args = parser.parse_args()

    if not os.path.exists(args.ai_file):
        print(f"❌ 文件不存在: {args.ai_file}")
        exit(1)

    parse_annotated(args.ai_file, args.output, args.preview, args.scan)
