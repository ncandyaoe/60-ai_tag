"""
区域渲染器

每个函数负责一个独立的语义区域，输入为 TemplateRegion/FlowRect + canvas + data。
函数之间通过返回值传递依赖（如 content_font_size → title min_size）。
"""

import os
from typing import List, Optional, Tuple

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from flow_layout import (
    FlowRect, FontConfig, TextBlock,
    layout_flow_content, find_best_font_size,
    plm_to_blocks, layout_title,
)
from template_extractor import TemplateRegion

# ---------------------------------------------------------------------------
# 字体常量（与 label_renderer.py 保持一致）
# ---------------------------------------------------------------------------
_FONT_NAME = "AliPuHuiTi"
_FONT_NAME_BOLD = "AliPuHuiTi-Bold"
_CAP_H_RATIO = 0.735
_X_HEIGHT_RATIO = 0.54

# 固定字号
_NUT_ROW_H = 7.94        # 营养表行高 2.8mm → 7.94pt
_NUT_FONT = _NUT_ROW_H - 2  # 营养表字号 ≈ 5.94pt
_NET_FONT = 21             # Net Volume 固定 21pt


# ---------------------------------------------------------------------------
# render_content — 内容区域
# ---------------------------------------------------------------------------

def render_content(
    canvas: Optional[Canvas],
    regions: List[FlowRect],
    data: dict,
    country_cfg: dict,
) -> Tuple[float, float]:
    """
    内容区域渲染器。

    Args:
        canvas:      ReportLab Canvas （None = dry-run，仅计算字号）
        regions:     Content 的 FlowRect 列表（倒 L 型等）
        data:        PLM 数据
        country_cfg: 国家法规配置

    Returns:
        (font_size, h_scale) — content 的自适应字号和横向压缩比
    """
    # 查找对应的 country_code 进行传递
    from country_config import COUNTRY_REGISTRY
    target_country = "DEFAULT"
    for code, cfg in COUNTRY_REGISTRY.items():
        if cfg == country_cfg:
            target_country = code
            break
            
    blocks = plm_to_blocks(data, target_country=target_country)

    # 法规最小字号
    min_mm = country_cfg.get("min_font_height_mm", 1.2)
    min_font_pt = min_mm / (0.3528 * _X_HEIGHT_RATIO)

    font_size, h_scale = find_best_font_size(
        blocks, regions,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        min_size=min_font_pt,
        max_size=16.0,
    )

    if canvas is not None:
        fc = FontConfig(
            font_name=_FONT_NAME,
            font_name_bold=_FONT_NAME_BOLD,
            font_size=font_size,
            h_scale=h_scale,
        )
        layout_flow_content(blocks, regions, fc, canvas=canvas)

    return font_size, h_scale


# ---------------------------------------------------------------------------
# render_title — 标题区域
# ---------------------------------------------------------------------------

def render_title(
    canvas: Optional[Canvas],
    regions: List[FlowRect],
    data: dict,
    country_cfg: Optional[dict] = None,
) -> Tuple[float, float, object]:
    """
    标题区域渲染器（已与 content 完全解耦）。

    Args:
        canvas:             ReportLab Canvas（None = 仅计算）
        regions:            标题的 FlowRect 列表（可能 L 型）
        data:               PLM 数据（需含 product_name_en / product_name_cn）
        country_cfg:        国家法规配置

    Returns:
        (font_size, h_scale, LayoutResult) — 标题的自适应结果及布局详情
    """
    en_name = data.get("product_name_en", "PRODUCT NAME")
    cn_name = data.get("product_name_cn", "")
    country_code = (country_cfg or {}).get("code", "DEFAULT")

    font_size, h_scale, result = layout_title(
        text_en=en_name,
        text_cn=cn_name,
        flow_regions=regions,
        font_name=_FONT_NAME,
        font_name_bold=_FONT_NAME_BOLD,
        country_code=country_code,
        canvas=canvas,
    )

    return font_size, h_scale, result


# ---------------------------------------------------------------------------
# render_nutrition — 营养表
# ---------------------------------------------------------------------------

def render_nutrition(
    canvas: Canvas,
    region: TemplateRegion,
    data: dict,
    country_cfg: dict,
) -> float:
    """
    营养表渲染器。

    在 region 指定的矩形内绘制营养信息表格。

    Args:
        canvas:      ReportLab Canvas
        region:      营养表的 TemplateRegion
        data:        PLM 数据（需含 nutrition.table_data）
        country_cfg: 国家配置（含 nutrition_title）

    Returns:
        table_bottom_y — 表格底部 y 坐标
    """
    c = canvas
    x = region.x
    y = region.y  # 顶部（PDF 坐标）
    width = region.width
    font_size = _NUT_FONT

    nutrition = data.get("nutrition") or {}
    nut_title = country_cfg.get("nutrition_title", "Nutrition Information")
    table_data_raw = nutrition.get("table_data") or []
    serving_size = nutrition.get("serving_size", "")

    row_h = font_size + 2
    pad = (row_h - font_size) / 2

    # 列宽
    col1_w = width * 0.48
    col2_w = width * 0.30
    col3_w = width * 0.22

    # 标题行
    title_fs = font_size + 2
    title_row_h = title_fs + 4
    table_top = y
    c.setLineWidth(1.0)

    cap_h_title = title_fs * _CAP_H_RATIO
    title_text_y = y - title_row_h + (title_row_h - cap_h_title) / 2
    c.setFont(_FONT_NAME_BOLD, title_fs)
    c.drawCentredString(x + width / 2, title_text_y, nut_title)
    y -= title_row_h

    c.setLineWidth(1.0)
    c.line(x, y, x + width, y)

    # 列标题行
    col_hdr_h = row_h * 2
    hdr_font_size = font_size - 0.5
    hdr_cap_h = hdr_font_size * _CAP_H_RATIO
    line1_y = y - row_h + (row_h - hdr_cap_h) / 2
    line2_y = y - row_h * 2 + (row_h - hdr_cap_h) / 2
    nrv_y = y - col_hdr_h + (col_hdr_h - hdr_cap_h) / 2

    c.setFont(_FONT_NAME, hdr_font_size)
    c.drawCentredString(x + col1_w + col2_w / 2, line1_y, "Per serving")
    if serving_size:
        c.drawCentredString(x + col1_w + col2_w / 2, line2_y, f"({serving_size})")
    c.drawCentredString(x + col1_w + col2_w + col3_w / 2, nrv_y, "NRV%")

    y -= col_hdr_h
    c.setLineWidth(0.5)
    c.line(x, y, x + width, y)

    # 数据行
    for item in table_data_raw:
        name = item.get("name", "")
        per_serving = str(item.get("per_serving", ""))
        nrv = str(item.get("nrv", ""))
        is_sub = item.get("is_sub", False)

        cap_h = font_size * _CAP_H_RATIO
        text_y = y - row_h + (row_h - cap_h) / 2

        name_x = x + 10 if is_sub else x + 2
        name_font = _FONT_NAME if is_sub else _FONT_NAME_BOLD

        c.setFont(name_font, font_size)
        display_name = name
        max_name_w = col1_w - (name_x - x) - 2
        while pdfmetrics.stringWidth(display_name, name_font, font_size) > max_name_w and len(display_name) > 3:
            display_name = display_name[:-1]
        c.drawString(name_x, text_y, display_name)

        c.setFont(_FONT_NAME, font_size)
        c.drawCentredString(x + col1_w + col2_w / 2, text_y, per_serving)
        if nrv:
            c.drawCentredString(x + col1_w + col2_w + col3_w / 2, text_y, nrv)

        y -= row_h
        c.setLineWidth(0.3)
        c.line(x, y, x + width, y)

    table_bottom = y

    # 外框
    c.setLineWidth(1.0)
    c.line(x, table_top, x, table_bottom)
    c.line(x + width, table_top, x + width, table_bottom)
    c.line(x, table_top, x + width, table_top)
    c.line(x, table_bottom, x + width, table_bottom)

    col_hdr_top = table_top - title_row_h
    c.setLineWidth(0.5)
    c.line(x + col1_w, col_hdr_top, x + col1_w, table_bottom)
    c.line(x + col1_w + col2_w, col_hdr_top, x + col1_w + col2_w, table_bottom)

    return table_bottom


# ---------------------------------------------------------------------------
# render_net_volume — 净含量
# ---------------------------------------------------------------------------

def render_net_volume(
    canvas: Canvas,
    region: TemplateRegion,
    data: dict,
):
    """
    Net Volume 渲染器（自适应字号）。

    在 region 指定的矩形内渲染净含量文字。
    字号自动适配区域高度，确保：
      - 字形完全在区域内（ascender 不超顶、descender 不超底）
      - 横向超宽时自动压缩（Tz 缩放）

    Args:
        canvas: ReportLab Canvas
        region: Net Volume 的 TemplateRegion
        data:   PLM 数据（需含 net_weight）
    """
    net_weight = data.get("net_weight", "")
    if not net_weight:
        return

    c = canvas

    # ── 自适应字号：基于真实字体度量填满区域高度 ──
    # Helvetica-Bold 实际度量: ascent=718, descent=-207 (per 1000 em)
    # 字形可见高度 = ascent - descent = 925/1000 em（仅占 em 方块的 92.5%）
    # 若用 font_size = region.height，会留 7.5% 的空隙
    # 修正：font_size = region.height × 1000 / 925，让可见高度 = region.height
    face = pdfmetrics.getFont(_FONT_NAME_BOLD).face
    real_ascent = face.ascent                    # 718
    real_descent = abs(face.descent)             # 207
    visible_h = real_ascent + real_descent        # 925

    font_size = region.height * 1000.0 / visible_h  # 13.7 → 14.8pt

    # baseline 定位：让 descender 底部恰好对齐区域底边
    descent_pt = font_size * real_descent / 1000.0   # 实际 descent (pt)
    y = region.bottom + descent_pt
    x = region.x

    # ── 横向压缩 ──
    text_w = pdfmetrics.stringWidth(net_weight, _FONT_NAME_BOLD, font_size)
    tz = min(100, int(region.width / text_w * 100)) if text_w > 0 else 100

    t = c.beginText(x, y)
    t.setFont(_FONT_NAME_BOLD, font_size)
    t._code.append(f'{tz} Tz')
    t.textOut(net_weight)
    t._code.append('100 Tz')
    c.drawText(t)


# ---------------------------------------------------------------------------
# render_logo — Logo
# ---------------------------------------------------------------------------

def render_logo(
    canvas: Canvas,
    region: TemplateRegion,
    logo_path: str,
):
    """
    Logo 渲染器。

    在 region 指定的矩形内绘制 logo 图片。

    Args:
        canvas:    ReportLab Canvas
        region:    Logo 的 TemplateRegion
        logo_path: Logo 文件路径
    """
    if not logo_path or not os.path.isfile(logo_path):
        return

    try:
        # region.y = 顶部（PDF坐标），region.bottom = 底部
        canvas.drawImage(
            logo_path,
            region.x, region.bottom,
            width=region.width, height=region.height,
            preserveAspectRatio=True, mask='auto',
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# render_eco_icons — 环保标
# ---------------------------------------------------------------------------

def render_eco_icons(
    canvas: Canvas,
    slots: list,
    data: dict,
    country_cfg: Optional[dict] = None,
):
    """
    环保标渲染器 —— 每个槽位独立渲染一个 PNG 图标。

    slots[i] 与 country_cfg["eco_icons"][i] 一一配对。
    如果槽位数 > 图标数，多余的槽位留空。
    如果图标数 > 槽位数，多余的图标被忽略。

    Args:
        canvas:      ReportLab Canvas
        slots:       List[TemplateRegion]，按从左到右排序
        data:        PLM 数据（预留给产品级别的环保标选择）
        country_cfg: 国家法规配置（含 eco_icons 文件名列表）
    """
    eco_icon_names = (country_cfg or {}).get("eco_icons", [])
    if not eco_icon_names or not slots:
        return

    # 定位 static/eco_icons/ 目录
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    eco_dir = os.path.join(_this_dir, "static", "eco_icons")

    # 逐槽渲染
    for i, slot in enumerate(slots):
        if i >= len(eco_icon_names):
            break  # 没有更多图标了

        path = os.path.join(eco_dir, eco_icon_names[i])
        if not os.path.exists(path):
            continue

        # 读取图标原始尺寸
        from PIL import Image as PILImage
        img = PILImage.open(path)
        img_w, img_h = img.width, img.height
        img.close()

        # 等比缩放到槽位内（fit 模式），移除 padding 以最大化显示
        avail_w = slot.width
        avail_h = slot.height
        if avail_w <= 0 or avail_h <= 0:
            continue

        aspect = img_w / img_h
        # 先尝试按高度填满
        draw_h = avail_h
        draw_w = draw_h * aspect
        # 如果宽度超出，改按宽度填满
        if draw_w > avail_w:
            draw_w = avail_w
            draw_h = draw_w / aspect

        # 在槽位中居中
        slot_bottom = slot.y - slot.height
        x = slot.x + (slot.width - draw_w) / 2
        y = slot_bottom + (slot.height - draw_h) / 2

        canvas.drawImage(
            path, x, y,
            width=draw_w, height=draw_h,
            preserveAspectRatio=True,
            mask='auto',
        )
