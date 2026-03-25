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

def render_label(
    template_or_path,
    data: dict,
    country_cfg: Optional[dict] = None,
) -> bytes:
    """
    解耦式标签渲染管线。

    所有区域严格遵循 .ai 色块约束，不超出模板边界。
    L 型区域由 template_extractor 自动分解为多个矩形，
    直接作为 FlowRect 传入渲染器。

    渲染顺序（依赖驱动）：
      1. content dry-run → 得到 content_font_size
      2. logo
      3. title（依赖 content_font_size）
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

    # 标题（依赖 content_font_size）
    if title_flowrects:
        render_title(
            canvas=c,
            regions=title_flowrects,
            data=data,
            content_font_size=content_font_size,
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
    if template.eco_icons:
        render_eco_icons(c, template.eco_icons, data)

    # HALAL 标识
    if data.get("is_halal") and template.content:
        c.setFont("AliPuHuiTi-Bold", 5)
        c.drawString(template.content.x, template.content.bottom, "☪ HALAL")

    c.save()
    return buf.getvalue()
