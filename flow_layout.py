"""
flow_layout.py — 通用流式矩形布局引擎

文本沿有序矩形序列（FlowRect）从上到下流动排版。
当一个矩形排满后，自动溢出到下一个矩形继续。
支持任意 L 型、倒 L 型、全宽、多矩形不连续布局。

渲染模式：传入 canvas 直接绘制
估算模式：不传 canvas，仅返回布局结果（供二分搜索用）
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ---------------------------------------------------------------------------
# 字体注册
# ---------------------------------------------------------------------------

_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"
_FONT_NAME_BOLD = "Helvetica-Bold"

def _register_font():
    """注册阿里巴巴普惠体，降级到 Helvetica"""
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

_register_font()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class FlowRect:
    """文字可流入的矩形区域（PDF 坐标系，y 向上增长）"""
    x: float       # 左边 x
    y: float       # 顶部 y（文字从此处 baseline 开始向下排）
    width: float   # 可用宽度
    height: float  # 可用高度（向下延伸）
    seamless: bool = False  # True = 从上一个区域无缝衔接（保持 leading 节奏）

    @property
    def bottom(self) -> float:
        """矩形底边 y 坐标"""
        return self.y - self.height


@dataclass
class TextBlock:
    """一个文本块（粗体前缀 + 正文）"""
    text: str
    bold_prefix: str = ""


@dataclass
class FontConfig:
    """字体配置"""
    font_name: str = _FONT_NAME
    font_name_bold: str = _FONT_NAME_BOLD
    font_size: float = 8.0
    leading_ratio: float = 1.15
    h_scale: float = 1.0
    descent_ratio: float = 0.25    # descender 深度 / font_size

    @property
    def leading(self) -> float:
        """基线到基线的距离"""
        return self.font_size * self.leading_ratio


@dataclass
class LinePlacement:
    """一行文字的放置信息"""
    text: str
    x: float
    y: float
    font_name: str
    font_size: float
    h_scale: float = 1.0
    region_idx: int = 0
    bold_end: int = 0      # text[:bold_end] 用粗体绘制


@dataclass
class LayoutResult:
    """布局计算结果"""
    overflow: bool = False
    lines: List[LinePlacement] = field(default_factory=list)
    region_usage: List[float] = field(default_factory=list)
    total_lines: int = 0


# ---------------------------------------------------------------------------
# 核心引擎
# ---------------------------------------------------------------------------

def layout_flow_content(
    blocks: List[TextBlock],
    flow_regions: List[FlowRect],
    font_config: FontConfig,
    canvas=None
) -> LayoutResult:
    """
    通用流式矩形布局引擎。

    文本沿 flow_regions 序列从上到下流动排版。
    当一个矩形排满后，自动溢出到下一个矩形继续。

    Args:
        blocks:       文本块列表（按显示顺序）
        flow_regions: 有序矩形序列（文字依次流经）
        font_config:  字体配置
        canvas:       ReportLab canvas（传入则渲染，不传则仅估算）

    Returns:
        LayoutResult: overflow, lines, region_usage, total_lines
    """
    if not flow_regions or not blocks:
        return LayoutResult(region_usage=[0.0] * len(flow_regions))

    fc = font_config
    leading = fc.leading
    eff_h_scale = fc.h_scale
    descent = fc.font_size * fc.descent_ratio  # g/y/p 等字母下沉深度

    result = LayoutResult(region_usage=[0.0] * len(flow_regions))

    ri = 0                          # 当前矩形索引
    # baseline = 矩形顶边 - 字号（让字符顶部贴齐矩形顶边）
    cur_y = flow_regions[0].y - fc.font_size

    for block in blocks:
        text = block.text
        prefix = block.bold_prefix
        if not text and not prefix:
            continue

        full_text = prefix + text
        prefix_len = len(prefix)
        total_len = len(full_text)
        ptr = 0                     # 当前字符指针

        while ptr < total_len:
            # 检查是否所有矩形已用完
            if ri >= len(flow_regions):
                result.overflow = True
                return result

            region = flow_regions[ri]
            # 横向压缩时等效宽度更大（每行容纳更多字符）
            # 减 1pt 安全余量：stringWidth 逐字累加与实际渲染的字距有微小差异
            avail_w = (region.width / eff_h_scale if eff_h_scale < 1.0 else region.width) - 1.0

            line_start = ptr

            # 优化：先检查剩余文字能否一行放完
            remaining = full_text[ptr:]
            rem_w = _measure_segment(remaining, ptr, prefix_len, fc)

            if rem_w <= avail_w:
                # 整行放完
                ptr = total_len
            else:
                # 单词级断行：遇到放不下的完整单词，整个移到下一行
                # 可断行位置：空格、连字符、括号后、逗号后
                line_w = 0.0
                last_break_ptr = line_start   # 上一个可断行位置

                while ptr < total_len:
                    ch = full_text[ptr]
                    ch_font = fc.font_name_bold if ptr < prefix_len else fc.font_name
                    ch_w = stringWidth(ch, ch_font, fc.font_size)

                    if line_w + ch_w > avail_w and ptr > line_start:
                        # 溢出：优先在上一个单词边界断行
                        if last_break_ptr > line_start:
                            ptr = last_break_ptr
                        # else: 单个超长单词，逼不得已字符级截断
                        break

                    line_w += ch_w
                    ptr += 1

                    # 标记可断行位置（在这些字符之后可以换行）
                    if ch in (' ', '-', '(', ')', ',', '/'):
                        last_break_ptr = ptr

            line_text = full_text[line_start:ptr]
            if not line_text:
                break  # 安全退出（避免死循环）

            # 计算本行中粗体字符数
            bold_end = max(0, min(prefix_len - line_start, len(line_text)))

            placement = LinePlacement(
                text=line_text,
                x=region.x,
                y=cur_y,
                font_name=fc.font_name,
                font_size=fc.font_size,
                h_scale=eff_h_scale,
                region_idx=ri,
                bold_end=bold_end
            )
            result.lines.append(placement)
            result.total_lines += 1

            if canvas:
                _render_line(canvas, placement, fc)

            cur_y -= leading
            result.region_usage[ri] = region.y - cur_y

            # 检查下一行是否超出当前矩形（含 descender 深度）
            # cur_y 是下一行 baseline，其 descender 延伸到 cur_y - descent
            if cur_y - descent < region.bottom:
                ri += 1
                if ri < len(flow_regions):
                    new_r = flow_regions[ri]
                    if not new_r.seamless:
                        # 默认行为：重置到新区域顶边
                        cur_y = new_r.y - fc.font_size
                    # else: seamless=True → 保持 cur_y 连贯（倒L型）

    return result


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _measure_segment(text: str, start_in_full: int, prefix_len: int,
                     fc: FontConfig) -> float:
    """测量一段文字的宽度（自动处理粗体/正体混排）"""
    w = 0.0
    for i, ch in enumerate(text):
        idx = start_in_full + i
        font = fc.font_name_bold if idx < prefix_len else fc.font_name
        w += stringWidth(ch, font, fc.font_size)
    return w


def _render_line(canvas, line: LinePlacement, fc: FontConfig):
    """在 canvas 上绘制一行文字（支持粗体前缀混排 + 横向压缩）"""
    c = canvas
    x, y = line.x, line.y

    if line.h_scale < 1.0:
        c.saveState()
        c.transform(line.h_scale, 0, 0, 1, x * (1 - line.h_scale), 0)

    if line.bold_end > 0:
        bold_part = line.text[:line.bold_end]
        regular_part = line.text[line.bold_end:]

        c.setFont(fc.font_name_bold, fc.font_size)
        c.drawString(x, y, bold_part)

        if regular_part:
            bold_w = stringWidth(bold_part, fc.font_name_bold, fc.font_size)
            c.setFont(fc.font_name, fc.font_size)
            c.drawString(x + bold_w, y, regular_part)
    else:
        c.setFont(fc.font_name, fc.font_size)
        c.drawString(x, y, line.text)

    if line.h_scale < 1.0:
        c.restoreState()


# ---------------------------------------------------------------------------
# 各国最小字号（基于字高法规）
# ---------------------------------------------------------------------------

# 字高(mm) → 字号(pt) 换算: min_pt = min_mm / (PT_TO_MM × x_height_ratio)
_PT_TO_MM = 25.4 / 72.0        # 1pt = 0.3528mm
_X_HEIGHT_RATIO = 0.54          # AliPuHuiTi sxHeight/UPM

# 各国最小字高 (mm) — 与 country_config.py 保持同步
_MIN_HEIGHT_MM = {
    "CL": 2.0,     # 智利（最严格）
    "AU": 1.8,     # 澳大利亚
    "NZ": 1.8,     # 新西兰
    "US": 1.6,     # 美国
    "CA": 1.6,     # 加拿大
    "SG": 1.5,     # 新加坡
    "TH": 1.5,     # 泰国
    "MY": 1.5,     # 马来西亚
    "DEFAULT": 1.2, # 默认
}

def get_min_font_pt(country_code: str = "DEFAULT") -> float:
    """根据目标国法规字高要求，返回最小字号 (pt)"""
    import math
    min_mm = _MIN_HEIGHT_MM.get(country_code, _MIN_HEIGHT_MM["DEFAULT"])
    return math.ceil(min_mm / (_PT_TO_MM * _X_HEIGHT_RATIO) * 10) / 10  # 向上取整到 0.1pt


# ---------------------------------------------------------------------------
# 二分搜索：自适应字号
# ---------------------------------------------------------------------------

def find_best_font_size(
    blocks: List[TextBlock],
    flow_regions: List[FlowRect],
    font_name: str = _FONT_NAME,
    font_name_bold: str = _FONT_NAME_BOLD,
    leading_ratio: float = 1.15,
    h_scale: float = 1.0,
    min_size: float = 4.0,
    max_size: float = 16.0,
    min_h_scale: float = 0.35,
    iterations: int = 20,
) -> tuple:
    """
    三阶段自适应搜索：

    第一阶段：二分搜索字号（max_size → min_size），h_scale=1.0
    第二阶段：若到 min_size 仍溢出，固定字号=min_size，
             二分搜索 h_scale（1.0 → min_h_scale）
    第三阶段：若 min_h_scale 仍溢出，固定 h_scale=min_h_scale，
             继续降低字号到 hard_min（4pt）确保信息完整

    Returns:
        (font_size, h_scale)
    """
    import math

    # ---- 第一阶段：搜索字号 ----
    lo, hi = min_size, max_size
    best_size = lo

    for _ in range(iterations):
        mid = (lo + hi) / 2
        fc = FontConfig(
            font_name=font_name,
            font_name_bold=font_name_bold,
            font_size=mid,
            leading_ratio=leading_ratio,
            h_scale=1.0,
        )
        result = layout_flow_content(blocks, flow_regions, fc)
        if not result.overflow:
            best_size = mid
            lo = mid
        else:
            hi = mid

    best_size = math.floor(best_size * 100) / 100

    # 检查最小字号是否仍然溢出
    fc_min = FontConfig(
        font_name=font_name, font_name_bold=font_name_bold,
        font_size=min_size, leading_ratio=leading_ratio, h_scale=1.0,
    )
    result_min = layout_flow_content(blocks, flow_regions, fc_min)

    if not result_min.overflow:
        # 第一阶段就够了，不需要横向压缩
        return best_size, 1.0

    # ---- 第二阶段：固定 min_size，搜索 h_scale ----
    hs_lo, hs_hi = min_h_scale, 1.0
    best_hs = hs_lo

    for _ in range(iterations):
        hs_mid = (hs_lo + hs_hi) / 2
        fc = FontConfig(
            font_name=font_name, font_name_bold=font_name_bold,
            font_size=min_size, leading_ratio=leading_ratio,
            h_scale=hs_mid,
        )
        result = layout_flow_content(blocks, flow_regions, fc)
        if not result.overflow:
            best_hs = hs_mid
            hs_lo = hs_mid   # 尝试更大 h_scale（更少压缩）
        else:
            hs_hi = hs_mid   # 需要更小 h_scale（更多压缩）

    best_hs = math.floor(best_hs * 100) / 100

    # 检查 min_h_scale 是否仍然溢出
    fc_hs_min = FontConfig(
        font_name=font_name, font_name_bold=font_name_bold,
        font_size=min_size, leading_ratio=leading_ratio, h_scale=min_h_scale,
    )
    result_hs_min = layout_flow_content(blocks, flow_regions, fc_hs_min)

    if not result_hs_min.overflow:
        return min_size, best_hs

    # ---- 第三阶段：固定 h_scale=min_h_scale，继续缩小字号 ----
    # 信息完整性 > 法规字号（标签会显示 warning）
    hard_min = 4.0
    lo3, hi3 = hard_min, min_size
    best3 = lo3

    for _ in range(iterations):
        mid = (lo3 + hi3) / 2
        fc = FontConfig(
            font_name=font_name, font_name_bold=font_name_bold,
            font_size=mid, leading_ratio=leading_ratio,
            h_scale=min_h_scale,
        )
        result = layout_flow_content(blocks, flow_regions, fc)
        if not result.overflow:
            best3 = mid
            lo3 = mid
        else:
            hi3 = mid

    best3 = math.floor(best3 * 100) / 100
    return best3, min_h_scale


# ---------------------------------------------------------------------------
# PLM JSON → TextBlock 转换
# ---------------------------------------------------------------------------

def plm_to_blocks(data: dict) -> List[TextBlock]:
    """将 PLM JSON 数据转换为 TextBlock 列表"""
    blocks = []

    ingr = data.get("ingredients", "").strip()
    if ingr:
        blocks.append(TextBlock(text=ingr, bold_prefix="Ingredients: "))

    allergens = data.get("allergens", "").strip()
    if allergens:
        blocks.append(TextBlock(text=allergens, bold_prefix="Contains: "))

    storage = data.get("storage", "").strip()
    if storage:
        blocks.append(TextBlock(text=storage))

    prod_date = data.get("production_date", "").strip()
    best_before = data.get("best_before", "").strip()
    if prod_date or best_before:
        date_str = f"{prod_date} / Best Before: {best_before}" if best_before else prod_date
        blocks.append(TextBlock(text=date_str, bold_prefix="Production date: "))

    origin = data.get("origin", "").strip()
    if origin:
        blocks.append(TextBlock(text=origin, bold_prefix="Product of "))

    mfr = data.get("manufacturer", "").strip()
    if mfr:
        blocks.append(TextBlock(text=mfr, bold_prefix="Manufacturer: "))

    addr = data.get("manufacturer_address", "").strip()
    if addr:
        blocks.append(TextBlock(text=addr, bold_prefix="Address: "))

    imp = data.get("importer_info", "").strip()
    if imp:
        blocks.append(TextBlock(text=imp, bold_prefix="Imported by: "))

    return blocks
