"""
经典 70×69mm 方形标签的模板配置构造器。

无 .ai 文件，使用代码从设计师固定常量生成 TemplateConfig，
使经典标签也能走 render_pipeline.py 新引擎。

布局：
  ┌─────────────────────────────────┐
  │  Title (L型)            [LOGO]  │
  ├─────────────────────────────────┤
  │                    │            │
  │  Content           │  Nut Table │
  │  (全宽 → 左栏)     │            │
  │                    │            │
  │ [NetVol]           │            │
  └─────────────────────────────────┘
"""

from reportlab.lib.units import mm
from template_extractor import TemplateConfig, TemplateRegion


# ---------------------------------------------------------------------------
# 设计师固定常量（与 label_renderer.py 保持一致）
# ---------------------------------------------------------------------------
LABEL_W = 70 * mm      # ≈ 198.4pt
LABEL_H = 69 * mm      # ≈ 195.6pt
MARGIN  = 2 * mm        # ≈ 5.67pt

LOGO_W = 40             # pt
LOGO_H = 5.4 * mm       # ≈ 15.3pt

_NUT_FONT = 5.94        # 营养表字号
_NUT_ROW_H = _NUT_FONT + 2   # 行高
_NUT_TITLE_FS = _NUT_FONT + 2
_NUT_TITLE_ROW_H = _NUT_TITLE_FS + 4

RIGHT_COL_RATIO = 0.62
COL_GAP = 4             # 内容与营养表间距

TITLE_ZONE_H_DEFAULT = 35  # 默认标题预留高度（足够 3 行 11pt）
TITLE_LEADING = 1.15           # 标题行距比例


# ---------------------------------------------------------------------------
# 营养表高度计算
# ---------------------------------------------------------------------------
def _calc_nut_height(data: dict) -> float:
    """计算营养成分表的总高度 (pt)。"""
    nut = data.get("nutrition") or {}
    table_data = nut.get("table_data") or []
    h = _NUT_TITLE_ROW_H                    # 标题行
    h += _NUT_ROW_H * 2                     # 列标题行（2行高）
    h += len(table_data) * _NUT_ROW_H       # 数据行
    return h


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_classic_config(
    data: dict,
) -> TemplateConfig:
    """
    根据 PLM 数据动态构建经典 70×69mm 模板的 TemplateConfig。

    营养表高度取决于数据行数，因此 nut_table / content 区域需要动态计算。
    标题区域已与 content 完全解耦，使用固定预留高度。
    """
    TITLE_ZONE_H = TITLE_ZONE_H_DEFAULT
    left   = MARGIN
    right  = LABEL_W - MARGIN
    top    = LABEL_H - MARGIN
    bottom = MARGIN
    content_w = right - left

    # ── 营养表 ──
    left_col_w = content_w * (1 - RIGHT_COL_RATIO)
    right_col_w = content_w * RIGHT_COL_RATIO
    right_col_x = left + left_col_w + COL_GAP
    actual_right_w = right_col_w - COL_GAP

    nut_total_h = _calc_nut_height(data)
    nut_top_y = bottom + nut_total_h

    nut_table = TemplateRegion(
        x=right_col_x,
        y=nut_top_y,
        width=actual_right_w,
        height=nut_total_h,
    )

    # ── Net Volume ──
    net_weight = data.get("net_weight", "")
    # 高度 = net_volume 字体的可见高度（从真实度量推算）
    # 这里预留与老引擎一致的空间
    net_reserve_h = 21 * 0.735 + 2 if net_weight else 0  # cap_h + 2pt
    net_volume = TemplateRegion(
        x=left,
        y=bottom + net_reserve_h,
        width=left_col_w,
        height=net_reserve_h,
    ) if net_weight else None

    # ── Logo ──
    logo = TemplateRegion(
        x=right - LOGO_W,
        y=top,
        width=LOGO_W,
        height=LOGO_H,
    )

    # ── 标题 ── L 型（扣除 logo 区域）
    title_top = top
    title_bottom = top - TITLE_ZONE_H
    title_narrow_w = content_w - LOGO_W - 2
    logo_row_h = LOGO_H

    title_rects = []
    # R1: logo 旁的窄行
    if logo_row_h > 0:
        title_rects.append(TemplateRegion(
            x=left, y=title_top,
            width=title_narrow_w,
            height=min(logo_row_h, TITLE_ZONE_H),
        ))
    # R2: logo 下方全宽行
    remaining_h = TITLE_ZONE_H - logo_row_h
    if remaining_h > 0:
        title_rects.append(TemplateRegion(
            x=left, y=title_top - logo_row_h,
            width=content_w,
            height=remaining_h,
        ))

    # ── Content ── 倒 L 型（全宽 + 左栏）
    content_top = title_bottom
    r1_h = content_top - nut_top_y    # 全宽区域（标题底部 → 营养表顶部）
    r2_h = nut_top_y - (bottom + net_reserve_h)  # 左栏（营养表旁 → net_volume 上方）

    content_rects = []
    if r1_h > 0:
        content_rects.append(TemplateRegion(
            x=left, y=content_top,
            width=content_w,
            height=r1_h,
        ))
    if r2_h > 0:
        content_rects.append(TemplateRegion(
            x=left, y=nut_top_y,
            width=left_col_w,
            height=r2_h,
        ))

    return TemplateConfig(
        page_width=LABEL_W,
        page_height=LABEL_H,
        source_file="(classic 70x69mm, code-generated)",
        title_rects=title_rects,
        content_rects=content_rects,
        nut_table=nut_table,
        net_volume=net_volume,
        logo=logo,
    )
