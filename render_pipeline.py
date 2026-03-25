"""
渲染管线

从 .ai 模板提取区域 → 按需调度各区域渲染器 → 输出 PDF。

所有区域严格遵循 .ai 色块的尺寸约束，不可超出。
L 型区域由 template_extractor 自动分解为多个矩形。

用法:
    from render_pipeline import render_label
    pdf = render_label("template.ai", plm_data, country_cfg)
"""

import io
import os
from typing import List, Optional

from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from flow_layout import FlowRect
from template_extractor import TemplateConfig, TemplateRegion, extract_template_regions
from region_renderers import (
    render_content, render_title,
    render_nutrition, render_net_volume,
    render_logo, render_eco_icons,
)


# ---------------------------------------------------------------------------
# 字体注册
# ---------------------------------------------------------------------------
_FONT_REGISTERED = False


def _register_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    alibaba_r = os.path.join(static_dir, "Alibaba-PuHuiTi-Regular.ttf")
    alibaba_b = os.path.join(static_dir, "Alibaba-PuHuiTi-Bold.ttf")

    if os.path.isfile(alibaba_r) and os.path.getsize(alibaba_r) > 100_000:
        pdfmetrics.registerFont(TTFont("AliPuHuiTi", alibaba_r))
        if os.path.isfile(alibaba_b) and os.path.getsize(alibaba_b) > 100_000:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_b))

    _FONT_REGISTERED = True


# ---------------------------------------------------------------------------
# TemplateRegion → FlowRect 转换
# ---------------------------------------------------------------------------

def _regions_to_flowrects(regions: List[TemplateRegion]) -> List[FlowRect]:
    """
    将 TemplateRegion 列表转换为 FlowRect 列表。

    第一个矩形为独立区域，后续矩形标记为 seamless（与前一个无缝衔接）。
    """
    rects = []
    for i, r in enumerate(regions):
        rects.append(FlowRect(
            x=r.x, y=r.y,
            width=r.width, height=r.height,
            seamless=(i > 0),
        ))
    return rects


# ---------------------------------------------------------------------------
# 渲染管线
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 区域叠加层（调试用）
# ---------------------------------------------------------------------------

_REGION_COLORS = {
    "title":      (Color(0.81, 0.18, 0.15, alpha=0.20), Color(0.81, 0.18, 0.15)),  # 红
    "content":    (Color(0.09, 0.41, 0.66, alpha=0.20), Color(0.09, 0.41, 0.66)),  # 蓝
    "nut_table":  (Color(0.47, 0.18, 0.51, alpha=0.20), Color(0.47, 0.18, 0.51)),  # 紫
    "net_volume": (Color(0.16, 0.58, 0.29, alpha=0.20), Color(0.16, 0.58, 0.29)),  # 绿
    "logo":       (Color(0.94, 0.81, 0.16, alpha=0.20), Color(0.94, 0.81, 0.16)),  # 黄
    "eco_icons":  (Color(0.06, 0.60, 0.60, alpha=0.20), Color(0.06, 0.60, 0.60)),  # 青
}

_REGION_LABELS = {
    "title": "Title", "content": "Content",
    "nut_table": "Nutrition", "net_volume": "NetVol",
    "logo": "Logo", "eco_icons": "EcoIcons",
}


def _draw_region_overlay(c, template: TemplateConfig):
    """在 Canvas 上绘制所有区域的彩色叠加层。"""
    c.saveState()

    items = []
    # 多矩形区域
    for i, r in enumerate(template.title_rects):
        suffix = f" R{i+1}" if len(template.title_rects) > 1 else ""
        items.append((f"title", f"Title{suffix}", r))
    for i, r in enumerate(template.content_rects):
        suffix = f" R{i+1}" if len(template.content_rects) > 1 else ""
        items.append((f"content", f"Content{suffix}", r))
    for i, r in enumerate(template.eco_icon_rects):
        suffix = f" {i+1}" if len(template.eco_icon_rects) > 1 else ""
        items.append(("eco_icons", f"Eco{suffix}", r))
    # 单矩形区域
    for key in ("nut_table", "net_volume", "logo"):
        region = getattr(template, key, None)
        if region:
            items.append((key, _REGION_LABELS[key], region))

    for color_key, label, r in items:
        fill_c, stroke_c = _REGION_COLORS.get(color_key, _REGION_COLORS["content"])
        bottom = r.y - r.height

        # 半透明填充
        c.setFillColor(fill_c)
        c.rect(r.x, bottom, r.width, r.height, stroke=0, fill=1)

        # 边框
        c.setStrokeColor(stroke_c)
        c.setLineWidth(0.8)
        c.rect(r.x, bottom, r.width, r.height, stroke=1, fill=0)

        # 标签文字
        c.setFillColor(stroke_c)
        label_fs = min(6, r.height * 0.3)
        if label_fs >= 3:
            c.setFont("Helvetica-Bold", label_fs)
            c.drawString(r.x + 1, r.y - label_fs - 1, label)
            c.setFont("Helvetica", max(3, label_fs - 1.5))
            c.drawString(r.x + 1, r.y - label_fs * 2 - 1,
                         f"{r.width:.0f}×{r.height:.0f}")

    c.restoreState()


def render_label(
    template_or_path,
    data: dict,
    country_cfg: Optional[dict] = None,
    show_regions: bool = False,
) -> bytes:
    """
    解耦式标签渲染管线。

    所有区域严格遵循 .ai 色块约束，不超出模板边界。
    L 型区域由 template_extractor 自动分解为多个矩形，
    直接作为 FlowRect 传入渲染器。

    渲染顺序（依赖驱动）：
      1. content dry-run → 得到 content_font_size
      2. logo
      3. title（独立自适应，仅受法规最小字号约束）
      4. content
      5. nutrition / net_volume / eco_icons

    Args:
        template_or_path: TemplateConfig 对象或 .ai 文件路径
        data:             PLM 产品数据
        country_cfg:      国家法规配置

    Returns:
        PDF 字节
    """
    _register_font()
    country_cfg = country_cfg or {}

    # 1. 获取模板配置
    if isinstance(template_or_path, str):
        template = extract_template_regions(template_or_path)
    else:
        template = template_or_path

    # 2. 构建 FlowRect（直接从提取器的 L 型分解结果转换）
    title_flowrects = _regions_to_flowrects(template.title_rects)
    content_flowrects = _regions_to_flowrects(template.content_rects)

    # 3. Logo 路径
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    logo_path = data.get("brand_logo", "")
    if not logo_path or not os.path.isfile(logo_path):
        logo_path = os.path.join(static_dir, "logo_placeholder.png")

    # ================================================================
    # 阶段 1: content 字号 (dry-run)
    # ================================================================
    content_font_size = 8.0
    content_h_scale = 1.0
    if content_flowrects:
        content_font_size, content_h_scale = render_content(
            canvas=None,
            regions=content_flowrects,
            data=data,
            country_cfg=country_cfg,
        )

    # ================================================================
    # 阶段 2: 创建 Canvas & 渲染
    # ================================================================
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(template.page_width, template.page_height))

    # Logo
    if template.logo:
        render_logo(c, template.logo, logo_path)

    # 标题（已与 content 解耦，独立自适应）
    if title_flowrects:
        render_title(
            canvas=c,
            regions=title_flowrects,
            data=data,
            country_cfg=country_cfg,
        )

    # Content
    if content_flowrects:
        render_content(
            canvas=c,
            regions=content_flowrects,
            data=data,
            country_cfg=country_cfg,
        )

    # 营养表
    if template.nut_table:
        render_nutrition(c, template.nut_table, data, country_cfg)

    # Net Volume
    if template.net_volume:
        render_net_volume(c, template.net_volume, data)

    # 环保标
    if template.eco_icon_rects:
        render_eco_icons(c, template.eco_icon_rects, data, country_cfg=country_cfg)

    # HALAL 标识
    if data.get("is_halal") and template.content:
        c.setFont("AliPuHuiTi-Bold", 5)
        c.drawString(template.content.x, template.content.bottom, "☪ HALAL")

    # 区域叠加层（调试用，最后绘制确保在最上层）
    if show_regions:
        _draw_region_overlay(c, template)

    c.save()
    return buf.getvalue()
