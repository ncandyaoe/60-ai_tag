"""
.ai 设计文件解析器 — 双引擎融合版 (PaddleOCR + PyMuPDF)

三阶段流水线：
  Stage 1: 并行提取 — PaddleOCR(语义标签) + PyMuPDF(精确坐标+字体)
  Stage 2: 融合   — 按 y 坐标匹配，生成 FusedElement
  Stage 3: Zone   — 基于统一特征的规则分类器

流程：
  .ai → PyMuPDF 转 PNG → [PaddleOCR API + PyMuPDF spans] → 融合 → Zone 识别 → YAML

Usage:
    python ai_parser.py <ai_file> [--output yaml_path] [--preview]
"""

import sys
import os
import json
import re
import base64
import yaml
import fitz  # PyMuPDF
import requests
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

# PaddleOCR API 配置
PADDLE_API_URL = "https://92f9hdc5x6kbu6c2.aistudio-app.com/layout-parsing"
PADDLE_TOKEN = os.getenv('paddleOCR_api_key', '')


# =====================================================================
# 数据模型
# =====================================================================

@dataclass
class FusedElement:
    """双引擎融合后的统一元素"""
    source: str = 'fused'       # paddle | pymupdf | fused
    semantic_label: str = ''    # text | image | table | footer_image
    x1_mm: float = 0
    y1_mm: float = 0
    x2_mm: float = 0
    y2_mm: float = 0
    font_size_pt: float = 0
    is_bold: bool = False
    font_name: str = ''
    text: str = ''
    score: float = 0

    @property
    def w_mm(self): return self.x2_mm - self.x1_mm

    @property
    def h_mm(self): return self.y2_mm - self.y1_mm

    @property
    def cx_mm(self): return (self.x1_mm + self.x2_mm) / 2

    @property
    def cy_mm(self): return (self.y1_mm + self.y2_mm) / 2


@dataclass
class Zone:
    """识别出的标签区域（支持 L 型异形）"""
    id: str
    type: str               # title | flow | table | static | value
    y_mm: float
    h_mm: float
    x_mm: float = 0
    w_mm: float = 0
    font_pt: float = 0
    shape: str = 'rect'     # rect | L | inverted_L
    avoid_zone: str = ''
    blocks: List[FusedElement] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


# =====================================================================
# 工具函数
# =====================================================================

def ai_to_png(ai_path: str, dpi: int = 300) -> str:
    """用 PyMuPDF 把 .ai 文件栅格化为 PNG"""
    doc = fitz.open(ai_path)
    page = doc[0]
    pw_mm = page.rect.width / 72 * 25.4
    ph_mm = page.rect.height / 72 * 25.4
    pix = page.get_pixmap(dpi=dpi)
    png_path = ai_path.rsplit('.', 1)[0] + '_tmp.png'
    pix.save(png_path)
    doc.close()
    return png_path, pw_mm, ph_mm, pix.width, pix.height


def call_paddle_ocr(img_path: str, cache_path: str = None) -> dict:
    """调用 PaddleOCR layout-parsing API（带缓存）"""
    if cache_path is None:
        cache_path = img_path.rsplit('.', 1)[0] + '_paddle.json'

    if os.path.exists(cache_path):
        print(f"  📋 使用缓存: {os.path.basename(cache_path)}")
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    with open(img_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode('ascii')

    headers = {
        'Authorization': f'token {PADDLE_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        'file': img_b64,
        'fileType': 1,
        'useDocOrientationClassify': False,
        'useDocUnwarping': False,
        'useChartRecognition': False,
    }

    print(f"  🔍 调用 PaddleOCR API (timeout=180s)...")
    resp = requests.post(PADDLE_API_URL, json=payload, headers=headers, timeout=180)

    if resp.status_code != 200:
        raise RuntimeError(f"PaddleOCR API 错误: {resp.status_code} — {resp.text[:500]}")

    data = resp.json()
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 已缓存: {os.path.basename(cache_path)}")
    return data


# =====================================================================
# Stage 1: 并行提取
# =====================================================================

def extract_paddle_elements(data: dict, pw_mm: float, ph_mm: float) -> List[FusedElement]:
    """从 PaddleOCR 响应提取元素（语义标签 + 块级坐标）"""
    result = data.get('result', {})
    lpr = result.get('layoutParsingResults', [])
    if not lpr:
        raise ValueError("PaddleOCR 未返回 layoutParsingResults")

    pr = lpr[0].get('prunedResult', {})
    px_w = pr.get('width', 1)
    px_h = pr.get('height', 1)
    px_per_mm_x = px_w / pw_mm
    px_per_mm_y = px_h / ph_mm

    elements = []
    for item in pr.get('parsing_res_list', []):
        bbox = item.get('block_bbox', [0, 0, 0, 0])
        elements.append(FusedElement(
            source='paddle',
            semantic_label=item.get('block_label', ''),
            text=item.get('block_content', ''),
            x1_mm=round(bbox[0] / px_per_mm_x, 1),
            y1_mm=round(bbox[1] / px_per_mm_y, 1),
            x2_mm=round(bbox[2] / px_per_mm_x, 1),
            y2_mm=round(bbox[3] / px_per_mm_y, 1),
        ))

    elements.sort(key=lambda e: e.y1_mm)
    return elements


def extract_pymupdf_spans(ai_path: str) -> List[FusedElement]:
    """从 .ai 文件提取 PyMuPDF span 级元素（精确坐标 + 字体属性）"""
    doc = fitz.open(ai_path)
    page = doc[0]
    blocks = page.get_text('dict')['blocks']

    elements = []
    for block in blocks:
        if block['type'] != 0:
            continue
        for line in block.get('lines', []):
            bbox = line['bbox']
            spans = line.get('spans', [])
            if not spans:
                continue

            text = ''.join(s.get('text', '') for s in spans)
            main_span = max(spans, key=lambda s: len(s.get('text', '')))
            is_bold = any(bool(s.get('flags', 0) & (1 << 4)) for s in spans)

            elements.append(FusedElement(
                source='pymupdf',
                semantic_label='text',
                x1_mm=round(bbox[0] / 72 * 25.4, 1),
                y1_mm=round(bbox[1] / 72 * 25.4, 1),
                x2_mm=round(bbox[2] / 72 * 25.4, 1),
                y2_mm=round(bbox[3] / 72 * 25.4, 1),
                font_size_pt=round(main_span.get('size', 0), 1),
                is_bold=is_bold,
                font_name=main_span.get('font', ''),
                text=text.strip(),
            ))

    doc.close()
    elements.sort(key=lambda e: e.y1_mm)
    return elements


def _extract_net_volume_span(ai_path: str, pattern) -> Optional[dict]:
    """从 PyMuPDF 提取最大字号的净含量 span 信息"""
    doc = fitz.open(ai_path)
    page = doc[0]
    blocks = page.get_text('dict')['blocks']

    best = None
    for block in blocks:
        if block['type'] != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                text = span.get('text', '')
                match = pattern.search(text)
                if match and span.get('size', 0) > 8:
                    bbox = span['bbox']
                    font_pt = round(span.get('size', 0), 1)
                    h_mm = round(font_pt * 1.2 / 72 * 25.4, 1)
                    center_mm = round((bbox[1] + bbox[3]) / 2 / 72 * 25.4, 1)
                    if best is None or font_pt > best['font_pt']:
                        best = {
                            'value': match.group(),
                            'y_mm': round(center_mm - h_mm / 2, 1),
                            'x_mm': round(bbox[0] / 72 * 25.4, 1),
                            'h_mm': h_mm,
                            'w_mm': round((bbox[2] - bbox[0]) / 72 * 25.4, 1),
                            'font_pt': font_pt,
                        }
    doc.close()
    return best


# =====================================================================
# Stage 2: 融合
# =====================================================================

def fuse_elements(paddle_elems: List[FusedElement],
                  pymupdf_elems: List[FusedElement],
                  pw_mm: float, ph_mm: float) -> List[FusedElement]:
    """
    融合双引擎结果：
    - image/table/footer_image：保留 PaddleOCR（PyMuPDF 检测不到矢量图）
    - text 行：使用 PyMuPDF 行级精度（坐标 + Bold + 字号）
    """
    fused = []

    # A. 非文字区块直接来自 PaddleOCR
    for pe in paddle_elems:
        if pe.semantic_label in ('image', 'table', 'footer_image'):
            pe.source = 'fused'
            fused.append(pe)

    # B. 文字行来自 PyMuPDF（精确坐标 + 字体属性）
    for me in pymupdf_elems:
        fe = FusedElement(
            source='fused',
            semantic_label='text',
            x1_mm=me.x1_mm, y1_mm=me.y1_mm,
            x2_mm=me.x2_mm, y2_mm=me.y2_mm,
            font_size_pt=me.font_size_pt,
            is_bold=me.is_bold,
            font_name=me.font_name,
            text=me.text,
        )
        fused.append(fe)

    fused.sort(key=lambda e: e.y1_mm)
    return fused


# =====================================================================
# Stage 3: 基于 FusedElement 的统一 Zone 识别
# =====================================================================

# 关键词配置
TITLE_STOP_KW = ['Ingredients', 'Ingrediënten', 'Ingredientes', 'Zutaten', 'Ingrédients']
CONTENT_KW = ['Store ', 'Best before', 'Product of', 'Importer', 'Nutrition']
NET_VOL_PATTERN = re.compile(r'\b\d+[\.,]?\d*\s*(?:mL|ml|g|kg|L|oz|fl\.?\s*oz)\b')
BLEED_MM = 2.0  # 四角出血位


def identify_zones(elements: List[FusedElement],
                   pw_mm: float, ph_mm: float,
                   ai_path: str = None,
                   **kwargs) -> List[Zone]:
    """基于融合后的 FusedElement 列表，用统一规则识别区域。
    自动检测单栏 / 双栏布局。"""
    zones = []
    used = set()
    net_zone = None  # 在规则 5 之前可能被规则 4 引用

    # ================================================================
    # 规则 0: 布局检测 — 如果 table 在右半侧 → 双栏
    # ================================================================
    table_elem = None
    table_idx = None
    for i, e in enumerate(elements):
        if e.semantic_label == 'table':
            table_elem = e
            table_idx = i
            break

    is_dual_col = (table_elem is not None and table_elem.x1_mm > pw_mm * 0.35)
    col_divider = table_elem.x1_mm if is_dual_col else pw_mm

    if is_dual_col:
        print(f"  📐 布局检测: 双栏 (分列线 x={col_divider:.1f}mm)")
    else:
        print(f"  📐 布局检测: 单栏")

    # ================================================================
    # 规则 1: Logo — image 标签 + 右上角
    # ================================================================
    logo_zone = None
    for i, e in enumerate(elements):
        if e.semantic_label == 'image' and e.y1_mm < ph_mm * 0.15 and e.x1_mm > pw_mm * 0.4:
            logo_zone = Zone(
                id='logo', type='static',
                y_mm=e.y1_mm, h_mm=e.h_mm,
                x_mm=e.x1_mm, w_mm=e.w_mm,
                blocks=[e],
                meta={'position': 'top_right'},
            )
            zones.append(logo_zone)
            used.add(i)
            break

    # ================================================================
    # 规则 2: 营养表 — table 标签
    # ================================================================
    nutrition_zone = None
    if table_elem is not None:
        nutrition_zone = Zone(
            id='nutrition', type='table',
            y_mm=table_elem.y1_mm, h_mm=table_elem.h_mm,
            blocks=[table_elem],
            meta={'format': 'eu', 'from_bottom_mm': round(ph_mm - table_elem.y2_mm, 1)},
        )
        # 双栏模式：营养表有明确 x/w，且与 net_volume 共存
        if is_dual_col:
            nutrition_zone.x_mm = table_elem.x1_mm
            nutrition_zone.w_mm = table_elem.w_mm
            nutrition_zone.avoid_zone = 'net_volume'  # net_volume 嵌在右上角
        zones.append(nutrition_zone)
        used.add(table_idx)

    # ================================================================
    # 规则 2b: 双栏模式 — 营养表上下的右列文字也归入 nutrition
    # ================================================================
    if is_dual_col and nutrition_zone:
        right_text_indices = []
        for i, e in enumerate(elements):
            if i in used or e.semantic_label != 'text':
                continue
            # 右列文字: x 中心在分列线右侧
            if e.cx_mm > col_divider:
                right_text_indices.append(i)
        if right_text_indices:
            # 扩展 nutrition zone 覆盖所有右列内容
            all_right_y1 = min(elements[i].y1_mm for i in right_text_indices)
            all_right_y2 = max(elements[i].y2_mm for i in right_text_indices)
            nut_y1 = min(nutrition_zone.y_mm, all_right_y1)
            nut_y2 = max(nutrition_zone.y_mm + nutrition_zone.h_mm, all_right_y2)
            nutrition_zone.y_mm = round(nut_y1, 1)
            nutrition_zone.h_mm = round(nut_y2 - nut_y1, 1)
            for i in right_text_indices:
                used.add(i)

    # ================================================================
    # 规则 3: 环保标识 — image/footer_image + 底部 85%
    # ================================================================
    eco_indices = []
    for i, e in enumerate(elements):
        if i in used:
            continue
        if e.semantic_label in ('image', 'footer_image') and e.y1_mm > ph_mm * 0.85:
            eco_indices.append(i)
    if eco_indices:
        y_top = min(elements[i].y1_mm for i in eco_indices)
        y_bot = max(elements[i].y2_mm for i in eco_indices)
        zones.append(Zone(
            id='eco_icons', type='static',
            y_mm=round(y_top, 1), h_mm=round(y_bot - y_top, 1),
            blocks=[elements[i] for i in eco_indices],
            meta={'from_bottom_mm': round(ph_mm - y_bot, 1)},
        ))
        for i in eco_indices:
            used.add(i)

    # ================================================================
    # 规则 4: 标题 — Bold 文字行 + 顶部区域
    # ================================================================
    title_lines = []
    for i, e in enumerate(elements):
        if i in used or e.semantic_label != 'text':
            continue
        if any(kw in e.text for kw in TITLE_STOP_KW + CONTENT_KW):
            break
        if e.is_bold and e.font_size_pt > 3.0:
            title_lines.append(i)
            used.add(i)
        elif title_lines:
            break

    if title_lines:
        y_top = min(elements[i].y1_mm for i in title_lines)
        y_bot = max(elements[i].y2_mm for i in title_lines)

        # L 型检测：logo 或 net_volume 在标题右上角（仅单栏模式）
        avoid_id = ''
        if not is_dual_col:
            for candidate_id, candidate_zone in [
                ('logo', logo_zone),
                ('net_volume', net_zone),
            ]:
                if candidate_zone and candidate_zone.x_mm > pw_mm * 0.4:
                    if (candidate_zone.y_mm < y_bot + 1
                            and candidate_zone.y_mm >= y_top - 1):
                        avoid_id = candidate_id
                        break

        title_zone = Zone(
            id='title', type='title',
            y_mm=round(y_top, 1), h_mm=round(y_bot - y_top, 1),
            shape='L' if avoid_id else 'rect',
            avoid_zone=avoid_id,
            blocks=[elements[i] for i in title_lines],
            meta={'text_blocks': len(title_lines)},
        )
        # 双栏模式: title 限制在左列宽度
        if is_dual_col:
            title_zone.w_mm = round(col_divider - BLEED_MM, 1)
        zones.append(title_zone)

    # ================================================================
    # 规则 5: 净含量 — 大字号(>8pt) + mL/g/kg 关键词
    # ================================================================
    net_zone = None
    if ai_path and os.path.exists(ai_path):
        nv_info = _extract_net_volume_span(ai_path, NET_VOL_PATTERN)
        if nv_info:
            net_zone = Zone(
                id='net_volume', type='value',
                y_mm=nv_info['y_mm'], h_mm=nv_info['h_mm'],
                x_mm=nv_info['x_mm'], w_mm=nv_info['w_mm'],
                font_pt=nv_info['font_pt'],
                blocks=[],
                meta={
                    'value': nv_info['value'],
                    'align': 'right' if nv_info['x_mm'] > pw_mm * 0.5 else 'left',
                },
            )
            zones.append(net_zone)
            for i, e in enumerate(elements):
                if i not in used and e.semantic_label == 'text':
                    if (e.y1_mm >= net_zone.y_mm - 1
                            and e.y2_mm <= net_zone.y_mm + net_zone.h_mm + 1
                            and e.font_size_pt > 8):
                        used.add(i)

            # 单栏模式：回头更新 title → L 型 avoid net_volume
            if not is_dual_col:
                title_zone_ref = next((z for z in zones if z.id == 'title'), None)
                if (title_zone_ref and not title_zone_ref.avoid_zone
                        and net_zone.x_mm > pw_mm * 0.4
                        and net_zone.y_mm < title_zone_ref.y_mm + title_zone_ref.h_mm + 1):
                    title_zone_ref.shape = 'L'
                    title_zone_ref.avoid_zone = 'net_volume'

    # ================================================================
    # 规则 6: 剩余 text → content (flow)
    # ================================================================
    flow_indices = []
    for i, e in enumerate(elements):
        if i in used:
            continue
        if e.semantic_label == 'text':
            flow_indices.append(i)
            used.add(i)

    if flow_indices:
        y_top = min(elements[i].y1_mm for i in flow_indices)
        y_bot = max(elements[i].y2_mm for i in flow_indices)

        # content 紧跟 title 底部
        title_zone = next((z for z in zones if z.id == 'title'), None)
        if title_zone:
            title_bot = title_zone.y_mm + title_zone.h_mm
            if title_bot < y_top:
                y_top = title_bot

        content_zone = Zone(
            id='content', type='flow',
            y_mm=round(y_top, 1),
            h_mm=round(y_bot - y_top, 1),
            blocks=[elements[i] for i in flow_indices],
            meta={'field_count': _detect_content_fields(
                ' '.join(elements[i].text for i in flow_indices))},
        )

        if is_dual_col:
            # 双栏: content 限制在左列
            content_zone.w_mm = round(col_divider - BLEED_MM, 1)
        else:
            # 单栏: 倒 L 型判断
            has_inv_l = (net_zone is not None
                         and net_zone.y_mm >= y_top
                         and net_zone.x_mm > pw_mm * 0.4)
            if has_inv_l:
                y_bot = max(y_bot, net_zone.y_mm + net_zone.h_mm)
                content_zone.h_mm = round(y_bot - y_top, 1)
                content_zone.shape = 'inverted_L'
                content_zone.avoid_zone = 'net_volume'

            # 防止与 nutrition 重叠
            if nutrition_zone and (content_zone.y_mm + content_zone.h_mm) > nutrition_zone.y_mm:
                content_zone.h_mm = round(nutrition_zone.y_mm - content_zone.y_mm, 1)

        zones.append(content_zone)

    zones.sort(key=lambda z: z.y_mm)

    # ================================================================
    # 后处理: 出血位 + 不重叠
    # ================================================================
    zones = _enforce_bleed_and_non_overlap(zones, pw_mm, ph_mm)

    return zones


def _enforce_bleed_and_non_overlap(zones: List[Zone],
                                    pw_mm: float, ph_mm: float) -> List[Zone]:
    """
    后处理保证：
    1. 所有 zone 在 BLEED_MM 安全区域内
    2. 相邻 zone 按 y 排序后不重叠（后一个的 y_top >= 前一个的 y_bot）
    """
    safe_top = BLEED_MM
    safe_bot = ph_mm - BLEED_MM
    safe_left = BLEED_MM
    safe_right = pw_mm - BLEED_MM

    for z in zones:
        y_top = z.y_mm
        y_bot = z.y_mm + z.h_mm

        # 1a. 上下出血位裁剪
        if y_top < safe_top:
            y_top = safe_top
        if y_bot > safe_bot:
            y_bot = safe_bot

        z.y_mm = round(y_top, 1)
        z.h_mm = round(max(y_bot - y_top, 0.1), 1)

        # 1b. 左右出血位裁剪（仅对有明确 x/w 的 zone）
        if z.x_mm > 0 or z.w_mm > 0:
            x_left = z.x_mm
            x_right = z.x_mm + z.w_mm
            if x_left < safe_left:
                x_left = safe_left
            if x_right > safe_right:
                x_right = safe_right
            z.x_mm = round(x_left, 1)
            z.w_mm = round(max(x_right - x_left, 0.1), 1)

    # 2. 不重叠: 按 y 排序后，检查真正重叠（y 和 x 都重叠）
    #    例外：avoid_zone 关系（L 型并排），不同列的 zone
    zones.sort(key=lambda z: z.y_mm)
    for idx in range(1, len(zones)):
        prev = zones[idx - 1]
        curr = zones[idx]
        # 跳过 avoid_zone 关系（L 型并排）
        if prev.avoid_zone == curr.id or curr.avoid_zone == prev.id:
            continue
        # 检查 x 轴是否重叠（全宽 zone 的 x 范围 = [0, pw]）
        prev_x1 = prev.x_mm if (prev.x_mm > 0 or prev.w_mm > 0) else 0
        prev_x2 = (prev.x_mm + prev.w_mm) if (prev.x_mm > 0 or prev.w_mm > 0) else pw_mm
        curr_x1 = curr.x_mm if (curr.x_mm > 0 or curr.w_mm > 0) else 0
        curr_x2 = (curr.x_mm + curr.w_mm) if (curr.x_mm > 0 or curr.w_mm > 0) else pw_mm
        x_overlap = prev_x1 < curr_x2 and curr_x1 < prev_x2
        if not x_overlap:
            continue  # 不同列 → 不算重叠
        prev_bot = prev.y_mm + prev.h_mm
        if curr.y_mm < prev_bot:
            overlap = prev_bot - curr.y_mm
            prev.h_mm = round(max(prev.h_mm - overlap, 0.1), 1)

    return zones


# =====================================================================
# 辅助函数
# =====================================================================

def _detect_content_fields(text: str) -> dict:
    """检测流式内容中包含的字段类型"""
    fields = {}
    if 'Ingredients' in text or 'Ingrediënten' in text:
        fields['ingredients'] = True
    if 'Store' in text or 'Bewaren' in text:
        fields['storage'] = True
    if 'Good for' in text or 'dipping' in text:
        fields['usage'] = True
    if 'Best before' in text or 'houdbaar' in text:
        fields['best_before'] = True
    if 'Product of' in text or 'Product uit' in text:
        fields['product_of'] = True
    if 'Importer' in text or 'Importeur' in text:
        fields['importer'] = True
    return fields


def zones_to_yaml(zones: List[Zone], pw_mm: float, ph_mm: float,
                  template_id: str = "auto_parsed") -> dict:
    """将区域列表转为 YAML 配置字典"""
    config = {
        'template_id': template_id,
        'display_name': f'Auto-parsed ({pw_mm:.0f}×{ph_mm:.0f}mm)',
        'label_size': {
            'width_mm': round(pw_mm, 1),
            'height_mm': round(ph_mm, 1),
            'margin_mm': 2.0,
        },
        'zones': [],
    }

    for zone in zones:
        z = {
            'id': zone.id,
            'type': zone.type,
            'y_mm': zone.y_mm,
            'h_mm': zone.h_mm,
        }
        if zone.x_mm > 0 or zone.w_mm > 0:
            z['x_mm'] = zone.x_mm
            z['w_mm'] = zone.w_mm
        if zone.font_pt > 0:
            z['font_pt'] = zone.font_pt
        if zone.shape != 'rect':
            z['shape'] = zone.shape
            z['avoid_zone'] = zone.avoid_zone

        if zone.type == 'title':
            z['text_blocks'] = zone.meta.get('text_blocks', 1)
        elif zone.type == 'table':
            z['format'] = zone.meta.get('format', 'eu')
            z['from_bottom_mm'] = zone.meta.get('from_bottom_mm', 0)
        elif zone.type == 'static':
            if 'position' in zone.meta:
                z['position'] = zone.meta['position']
            z['from_bottom_mm'] = zone.meta.get('from_bottom_mm', 0)
        elif zone.type == 'value':
            z['align'] = zone.meta.get('align', 'right')
            z['sample_value'] = zone.meta.get('value', '')
        elif zone.type == 'flow':
            z['detected_fields'] = list(zone.meta.get('field_count', {}).keys())

        config['zones'].append(z)

    return config


# =====================================================================
# 预览渲染
# =====================================================================

def _mm_to_pt(mm_val):
    return mm_val / 25.4 * 72


def _get_zone_by_id(zones: List[Zone], zone_id: str) -> Optional[Zone]:
    for z in zones:
        if z.id == zone_id:
            return z
    return None


def render_preview(ai_path: str, zones: List[Zone],
                   output_path: str = None) -> str:
    """在设计稿上叠加区域框，生成预览图（支持 L 型异形）"""
    doc = fitz.open(ai_path)
    page = doc[0]
    pw = page.rect.width
    ph = page.rect.height
    bleed_pt = _mm_to_pt(BLEED_MM)  # 出血位安全边界 (pt)
    x_safe_left = bleed_pt
    x_safe_right = pw - bleed_pt

    colors = {
        'title':      (1, 0, 0),        # 红
        'logo':       (0, 0.5, 0),      # 绿
        'content':    (0, 0, 1),        # 蓝
        'nutrition':  (0.5, 0, 0.5),    # 紫
        'eco_icons':  (0, 0.3, 0.3),    # 青
        'net_volume': (0, 0.6, 0),      # 深绿
    }

    for z in zones:
        color = colors.get(z.id, (0.5, 0.5, 0.5))
        y_top = _mm_to_pt(z.y_mm)
        h = _mm_to_pt(z.h_mm)

        rects = []
        if z.shape == 'L' and z.avoid_zone:
            avoided = _get_zone_by_id(zones, z.avoid_zone)
            if avoided:
                av_x = _mm_to_pt(avoided.x_mm)
                av_h = _mm_to_pt(avoided.h_mm)
                rects.append(fitz.Rect(x_safe_left, y_top, av_x, y_top + av_h))
                if h > av_h:
                    rects.append(fitz.Rect(x_safe_left, y_top + av_h, x_safe_right, y_top + h))
            else:
                rects.append(fitz.Rect(x_safe_left, y_top, x_safe_right, y_top + h))

        elif z.shape == 'inverted_L' and z.avoid_zone:
            avoided = _get_zone_by_id(zones, z.avoid_zone)
            if avoided:
                av_y = _mm_to_pt(avoided.y_mm)
                av_x = _mm_to_pt(avoided.x_mm)
                if av_y > y_top:
                    rects.append(fitz.Rect(x_safe_left, y_top, x_safe_right, av_y))
                rects.append(fitz.Rect(x_safe_left, av_y, av_x, y_top + h))
            else:
                rects.append(fitz.Rect(x_safe_left, y_top, x_safe_right, y_top + h))

        elif z.x_mm > 0 or z.w_mm > 0:
            x = _mm_to_pt(z.x_mm)
            w = _mm_to_pt(z.w_mm) if z.w_mm > 0 else x_safe_right - x
            rects.append(fitz.Rect(x, y_top, x + w, y_top + h))

        else:
            rects.append(fitz.Rect(x_safe_left, y_top, x_safe_right, y_top + h))

        for rect in rects:
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(color=color, fill=color, fill_opacity=0.15, width=1.5)
            shape.commit()

        if rects:
            shape_label = f" [{z.shape}]" if z.shape != 'rect' else ""
            label = f"{z.id} ({z.type}){shape_label}"
            text_point = fitz.Point(rects[0].x0 + 2, rects[0].y0 + 8)
            page.insert_text(text_point, label, fontsize=5, color=color)

    if output_path is None:
        output_path = ai_path.rsplit('.', 1)[0] + '_zones.png'

    pix = page.get_pixmap(dpi=300)
    pix.save(output_path)
    doc.close()
    print(f"🖼️  预览图: {output_path}")
    return output_path


# =====================================================================
# 主流程
# =====================================================================

def parse_and_report(ai_path: str, output_yaml: str = None,
                     preview: bool = False, save_raw: bool = False) -> dict:
    """完整解析流程 — 双引擎融合版"""
    print(f"📂 解析: {os.path.basename(ai_path)}")
    print("=" * 60)

    # Stage 0: .ai → PNG（供 PaddleOCR 用）
    print("  📸 栅格化 .ai → PNG (300 DPI)...")
    png_path, pw_mm, ph_mm, px_w, px_h = ai_to_png(ai_path)
    print(f"  📐 页面: {pw_mm:.1f} × {ph_mm:.1f} mm ({px_w}×{px_h} px)")

    # Stage 1a: PaddleOCR 提取
    paddle_data = call_paddle_ocr(png_path)
    if save_raw:
        raw_path = ai_path.rsplit('.', 1)[0] + '_paddle_raw.json'
        with open(raw_path, 'w', encoding='utf-8') as f:
            json.dump(paddle_data, f, ensure_ascii=False, indent=2)
        print(f"  📋 原始响应: {raw_path}")

    paddle_elems = extract_paddle_elements(paddle_data, pw_mm, ph_mm)
    print(f"  🅿️  PaddleOCR: {len(paddle_elems)} 个区块")

    # Stage 1b: PyMuPDF 提取
    pymupdf_elems = extract_pymupdf_spans(ai_path)
    print(f"  📄 PyMuPDF:   {len(pymupdf_elems)} 行 span")

    # Stage 2: 融合
    fused = fuse_elements(paddle_elems, pymupdf_elems, pw_mm, ph_mm)
    print(f"  🔗 融合后:    {len(fused)} 个元素")

    # 调试输出
    for e in fused:
        src_icon = '🖼️' if e.semantic_label in ('image', 'table', 'footer_image') else '📝'
        bold_mark = '𝐁' if e.is_bold else ' '
        font_info = f"{e.font_size_pt:4.1f}pt" if e.font_size_pt > 0 else "      "
        text_preview = e.text[:50].replace('\n', '↵') if e.text else ''
        print(f"    {src_icon} [{e.semantic_label:12s}] ({e.x1_mm:5.1f},{e.y1_mm:5.1f})→"
              f"({e.x2_mm:5.1f},{e.y2_mm:5.1f}) {bold_mark} {font_info} \"{text_preview}\"")

    # Stage 3: Zone 识别
    zones = identify_zones(fused, pw_mm, ph_mm, ai_path=ai_path)
    print(f"\n🔍 识别区域: {len(zones)} 个")
    print("-" * 60)

    for z in zones:
        shape_info = f"  shape={z.shape}" if z.shape != 'rect' else ""
        avoid_info = f" avoid={z.avoid_zone}" if z.avoid_zone else ""
        xy_info = f" x={z.x_mm:.1f} w={z.w_mm:.1f}" if z.x_mm > 0 or z.w_mm > 0 else ""
        print(f"  [{z.type:6s}] {z.id:<12s} y={z.y_mm:5.1f}mm  h={z.h_mm:5.1f}mm"
              f"{xy_info}{shape_info}{avoid_info}")

    # YAML
    config = zones_to_yaml(zones, pw_mm, ph_mm,
                           template_id=os.path.splitext(os.path.basename(ai_path))[0][:30])

    yaml_str = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"\n📝 YAML 配置:")
    print("-" * 60)
    print(yaml_str)

    if output_yaml:
        with open(output_yaml, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"✅ 已保存: {output_yaml}")

    # 预览图
    if preview:
        render_preview(ai_path, zones)

    # 清理临时 PNG
    if os.path.exists(png_path):
        os.remove(png_path)

    return config


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='解析 .ai 设计文件 — 双引擎融合版 (PaddleOCR + PyMuPDF)')
    parser.add_argument('ai_file', help='.ai 文件路径')
    parser.add_argument('--output', '-o', help='输出 YAML 路径')
    parser.add_argument('--preview', '-p', action='store_true', help='生成区域预览图')
    parser.add_argument('--save-raw', '-r', action='store_true', help='保存 PaddleOCR 原始响应')
    args = parser.parse_args()

    parse_and_report(args.ai_file, args.output, args.preview, args.save_raw)
