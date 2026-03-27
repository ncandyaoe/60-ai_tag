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
_FONT_NAME_HEAVY = "Helvetica-Bold"

def _register_font():
    """注册阿里巴巴普惠体，降级到 Helvetica"""
    global _FONT_REGISTERED, _FONT_NAME, _FONT_NAME_BOLD
    if _FONT_REGISTERED:
        return
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    alibaba_r = os.path.join(static_dir, "Alibaba-PuHuiTi-Regular.ttf")
    alibaba_b = os.path.join(static_dir, "Alibaba-PuHuiTi-Bold.ttf")
    alibaba_h = os.path.join(static_dir, "AlibabaPuHuiTi-3-105-Heavy.ttf")
    if os.path.isfile(alibaba_r) and os.path.getsize(alibaba_r) > 100_000:
        pdfmetrics.registerFont(TTFont("AliPuHuiTi", alibaba_r))
        
        if os.path.isfile(alibaba_b) and os.path.getsize(alibaba_b) > 100_000:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_b))
        else:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Bold", alibaba_r))
            
        if os.path.isfile(alibaba_h) and os.path.getsize(alibaba_h) > 100_000:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Heavy", alibaba_h))
        else:
            pdfmetrics.registerFont(TTFont("AliPuHuiTi-Heavy", alibaba_b if os.path.isfile(alibaba_b) else alibaba_r))
            
        _FONT_NAME = "AliPuHuiTi"
        _FONT_NAME_BOLD = "AliPuHuiTi-Bold"
        global _FONT_NAME_HEAVY
        _FONT_NAME_HEAVY = "AliPuHuiTi-Heavy"
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
class TextSpan:
    """一个文本切片（带样式信息）"""
    text: str
    bold: bool = False
    underline: bool = False

@dataclass
class TextBlock:
    """一个文本块（由多个可能带样式的切片组成）"""
    spans: List[TextSpan]
    
    # 兼容老接口
    def __init__(self, text: str = "", bold_prefix: str = ""):
        self.spans = []
        if bold_prefix:
            self.spans.append(TextSpan(text=bold_prefix, bold=True))
        if text:
            # 解析 text 中的 markdown 标记
            self.spans.extend(parse_markdown_text(text))

    @property
    def full_text(self) -> str:
        """获取无格式纯文本，用于分词和断行计算"""
        return "".join(s.text for s in self.spans)


def parse_markdown_text(text: str) -> List[TextSpan]:
    """
    解析内联 Markdown 样式，支持：
    **加粗** (bold)
    __下划线__ (underline)
    **__加粗加下划线__**
    返回 TextSpan 列表。
    """
    import re
    spans = []
    
    # 匹配 **bold**, __underline__, **__both__** 或 __**both**__
    # 正则解释:
    # (?P<bold underline>\*\*__(.*?)__\*\*) -> **__text__**
    # (?P<underline bold>__\*\*(.*?)\*\*__) -> __**text**__
    # (?P<bold>\*\*(.*?)\*\*) -> **text**
    # (?P<underline>__(.*?)__) -> __text__
    pattern = re.compile(
        r'(?P<bu>\*\*__(.*?)__\*\*)|'
        r'(?P<ub>__\*\*(.*?)\*\*__)|'
        r'(?P<b>\*\*(.*?)\*\*)|'
        r'(?P<u>__(.*?)__)'
    )
    
    last_end = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        # 1. 添加匹配点之前的普通文本
        if start > last_end:
            spans.append(TextSpan(text=text[last_end:start]))
            
        # 2. 添加匹配的格式化文本
        if match.group('bu'):
            spans.append(TextSpan(text=match.group(2), bold=True, underline=True))
        elif match.group('ub'):
            spans.append(TextSpan(text=match.group(4), bold=True, underline=True))
        elif match.group('b'):
            spans.append(TextSpan(text=match.group(6), bold=True))
        elif match.group('u'):
            spans.append(TextSpan(text=match.group(8), underline=True))
            
        last_end = end
        
    # 3. 添加剩余普通文本
    if last_end < len(text):
        spans.append(TextSpan(text=text[last_end:]))
        
    # 如果完全没有匹配到或者本身为空，确保至少有一个 span
    if not spans and text:
        spans.append(TextSpan(text=text))
        
    return spans


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


def _get_char_style(spans: List[TextSpan], char_index: int) -> tuple[bool, bool]:
    """根据全局字符索引，找出该字符是否加粗、是否下划线"""
    curr_len = 0
    for span in spans:
        if curr_len <= char_index < curr_len + len(span.text):
            return span.bold, span.underline
        curr_len += len(span.text)
    return False, False


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
    # 新增对多级 spans 的支持
    spans: List[TextSpan] = field(default_factory=list)


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
    canvas=None,
    new_line_per_block: bool = False,
) -> LayoutResult:
    """
    通用流式矩形布局引擎。
    """
    if not flow_regions or not blocks:
        return LayoutResult(region_usage=[0.0] * len(flow_regions))

    fc = font_config
    leading = fc.leading
    eff_h_scale = fc.h_scale
    descent = fc.font_size * fc.descent_ratio  # g/y/p 等字母下沉深度

    result = LayoutResult(region_usage=[0.0] * len(flow_regions))

    ri = 0                          # 当前矩形索引
    # baseline = 矩形顶边 - 字高（让首行视觉顶部贴齐矩形顶边，主体字母高度约 0.8 * font_size）
    cur_y = flow_regions[0].y - fc.font_size * 0.8

    for bi, block in enumerate(blocks):
        full_text = block.full_text
        if not full_text:
            continue

        # 每个 TextBlock 独占新行（标题用：中英文各自分行）
        if new_line_per_block and bi > 0 and result.total_lines > 0:
            pass  # cur_y 已经被推到新行位置

        total_len = len(full_text)
        ptr = 0                     # 当前字符指针

        while ptr < total_len:
            # 检查是否所有矩形已用完
            if ri >= len(flow_regions):
                result.overflow = True
                return result

            region = flow_regions[ri]

            # ── 预检查：当前行是否能在当前区域内放下 ──
            while cur_y - descent < region.bottom:
                if ri + 1 >= len(flow_regions):
                    result.overflow = True
                    return result
                next_r = flow_regions[ri + 1]

                if next_r.seamless and next_r.width > region.width and cur_y + fc.font_size * 0.8 > region.bottom:
                    # ── 延迟切换 ──
                    break
                else:
                    ri += 1
                    region = next_r
                    if not region.seamless:
                        cur_y = region.y - fc.font_size * 0.8

            # 横向压缩时等效宽度更大
            avail_w = (region.width / eff_h_scale if eff_h_scale < 1.0 else region.width) - 1.0

            line_start = ptr

            # 优化：优先在上一个可断行位置断行 (避免单词截断)
            line_w = 0.0
            last_break_ptr = line_start   # 上一个可断行位置

            while ptr < total_len:
                ch = full_text[ptr]
                is_bold, _ = _get_char_style(block.spans, ptr)
                ch_font = fc.font_name_bold if is_bold else fc.font_name
                ch_w = stringWidth(ch, ch_font, fc.font_size)

                if line_w + ch_w > avail_w and ptr > line_start:
                    if last_break_ptr > line_start:
                        ptr = last_break_ptr
                        break
                    else:
                        # 单个超长单词，字符级截断
                        break

                line_w += ch_w
                ptr += 1

                # 标记可断行位置
                if ch in ('(', '（'):
                    last_break_ptr = ptr - 1 if ptr - 1 > line_start else ptr
                elif ch in (' ', '-', ')', '）', ',', '，', '/', '、'):
                    last_break_ptr = ptr

            line_text = full_text[line_start:ptr]
            if not line_text:
                break  # 安全退出

            # 提取这一行的 spans
            line_spans = []
            curr_text_built = ""
            curr_bold = None
            curr_underline = None
            
            for i in range(line_start, ptr):
                char = full_text[i]
                bold, under = _get_char_style(block.spans, i)
                if curr_bold is None:
                    curr_bold = bold
                    curr_underline = under
                
                if bold != curr_bold or under != curr_underline:
                    if curr_text_built:
                        line_spans.append(TextSpan(curr_text_built, curr_bold, curr_underline))
                    curr_text_built = char
                    curr_bold = bold
                    curr_underline = under
                else:
                    curr_text_built += char
            
            if curr_text_built:
                line_spans.append(TextSpan(curr_text_built, curr_bold, curr_underline))

            placement = LinePlacement(
                text=line_text,
                x=region.x,
                y=cur_y,
                font_name=fc.font_name,
                font_size=fc.font_size,
                h_scale=eff_h_scale,
                region_idx=ri,
                spans=line_spans
            )
            result.lines.append(placement)
            result.total_lines += 1

            if canvas:
                _render_line(canvas, placement, fc)

            cur_y -= leading
            result.region_usage[ri] = region.y - cur_y

            # 检查下一行是否超出当前矩形（含 descender 深度）
            if cur_y - descent < region.bottom:
                if ri + 1 >= len(flow_regions):
                    pass
                else:
                    next_r = flow_regions[ri + 1]
                    if next_r.seamless and next_r.width > region.width and cur_y + fc.font_size > region.bottom:
                        pass
                    elif not next_r.seamless:
                        ri += 1
                        region = next_r
                        cur_y = region.y - fc.font_size
                    else:
                        ri += 1
                        region = next_r

    return result


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _render_line(canvas, line: LinePlacement, fc: FontConfig):
    """在 canvas 上绘制一行文字（支持多种字体混合与下划线）"""
    c = canvas
    x, y = line.x, line.y

    if line.h_scale < 1.0:
        c.saveState()
        c.transform(line.h_scale, 0, 0, 1, x * (1 - line.h_scale), 0)

    curr_x = x
    for span in line.spans:
        font_name = fc.font_name_bold if span.bold else fc.font_name
        c.setFont(font_name, fc.font_size)
        
        c.drawString(curr_x, y, span.text)
        span_w = stringWidth(span.text, font_name, fc.font_size)
        
        if span.underline:
            # 简单计算下划线位置，设定在 baseline 之下大约 descender 的 1/2 处
            # 线宽根据字号适当调整
            line_y = y - fc.font_size * 0.1
            c.setLineWidth(fc.font_size * 0.05)
            c.line(curr_x, line_y, curr_x + span_w, line_y)
            
        curr_x += span_w

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
    optimize_fill: bool = False,
) -> tuple:
    """
    自适应搜索（优先不压缩）：

    第一阶段：二分搜索字号（max_size → hard_min=4pt），h_scale=1.0
             优先找到最大的不压缩字号。如果结果 < min_size，标签会合规警告，
             但文字依然保持正常宽度，不会被压扁。
    第 1.5 阶段（optimize_fill=True 时启用）：纵向填满优化
             当纵向利用率 < 92% 时，尝试更大字号 + 适度横向压缩（h_scale ≥ 0.65）
             来填满纵向空间，减少底部留白。
    第二阶段：若到 4pt 仍溢出（极端场景），固定字号=min_size，
             二分搜索 h_scale（1.0 → min_h_scale）
    第三阶段：若 min_h_scale 仍溢出，固定 h_scale=min_h_scale，
             继续降低字号到 4pt 确保信息完整

    Returns:
        (font_size, h_scale)
    """
    import math

    hard_min = 4.0  # 绝对底线

    # ---- 第一阶段：搜索字号（优先不压缩） ----
    # 搜索范围扩展到 [hard_min, max_size]，而不是 [min_size, max_size]
    # 这样即使法规最小字号放不下，也会继续缩小字号而不是跳到横向压缩
    lo, hi = hard_min, max_size
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

    # 检查 hard_min 在 h_scale=1.0 下是否仍溢出
    fc_min = FontConfig(
        font_name=font_name, font_name_bold=font_name_bold,
        font_size=hard_min, leading_ratio=leading_ratio, h_scale=1.0,
    )
    result_min = layout_flow_content(blocks, flow_regions, fc_min)

    if not result_min.overflow:
        # 第一阶段就够了，不需要横向压缩

        # ---- 第 1.5 阶段：纵向填满优化 ----
        # 当 optimize_fill=True 时，检查纵向利用率。
        # 如果底部还有大量留白，尝试用更大字号 + 适度横向压缩来填满。
        if optimize_fill:
            fc_check = FontConfig(
                font_name=font_name, font_name_bold=font_name_bold,
                font_size=best_size, leading_ratio=leading_ratio, h_scale=1.0,
            )
            result_check = layout_flow_content(blocks, flow_regions, fc_check)
            total_region_h = sum(r.height for r in flow_regions)
            used_h = sum(result_check.region_usage)
            fill_ratio = used_h / total_region_h if total_region_h > 0 else 1.0

            if fill_ratio < 0.92:
                # 纵向利用率不足，尝试更大字号 + 适度压缩
                fill_hs_floor = 0.65  # 填满优化时的 h_scale 下限（保持可读性）
                opt_best_size = best_size
                opt_best_hs = 1.0
                opt_best_fill = fill_ratio

                # 从当前 best_size 往上以 0.5pt 步长搜索
                candidate = best_size + 0.5
                while candidate <= max_size:
                    # 先检查 h_scale=1.0 是否溢出
                    fc_try = FontConfig(
                        font_name=font_name, font_name_bold=font_name_bold,
                        font_size=candidate, leading_ratio=leading_ratio, h_scale=1.0,
                    )
                    r_try = layout_flow_content(blocks, flow_regions, fc_try)
                    if not r_try.overflow:
                        # 不溢出 → h_scale=1.0 就行，计算填充率
                        used = sum(r_try.region_usage)
                        fr = used / total_region_h if total_region_h > 0 else 1.0
                        if fr > opt_best_fill + 0.03:
                            opt_best_size = candidate
                            opt_best_hs = 1.0
                            opt_best_fill = fr
                    else:
                        # 溢出 → 二分搜索 h_scale（从 1.0 到 fill_hs_floor）
                        hs_lo_f, hs_hi_f = fill_hs_floor, 1.0
                        found_hs = None
                        for _ in range(iterations):
                            hs_mid_f = (hs_lo_f + hs_hi_f) / 2
                            fc_f = FontConfig(
                                font_name=font_name, font_name_bold=font_name_bold,
                                font_size=candidate, leading_ratio=leading_ratio,
                                h_scale=hs_mid_f,
                            )
                            r_f = layout_flow_content(blocks, flow_regions, fc_f)
                            if not r_f.overflow:
                                found_hs = hs_mid_f
                                hs_lo_f = hs_mid_f
                            else:
                                hs_hi_f = hs_mid_f

                        if found_hs is not None:
                            found_hs = math.floor(found_hs * 100) / 100
                            fc_eval = FontConfig(
                                font_name=font_name, font_name_bold=font_name_bold,
                                font_size=candidate, leading_ratio=leading_ratio,
                                h_scale=found_hs,
                            )
                            r_eval = layout_flow_content(blocks, flow_regions, fc_eval)
                            used = sum(r_eval.region_usage)
                            fr = used / total_region_h if total_region_h > 0 else 1.0
                            if fr > opt_best_fill + 0.03:
                                opt_best_size = candidate
                                opt_best_hs = found_hs
                                opt_best_fill = fr

                    candidate += 0.5

                opt_best_size = math.floor(opt_best_size * 100) / 100
                return opt_best_size, opt_best_hs

        return best_size, 1.0

    # ---- 第二阶段：固定 min_size，搜索 h_scale ----
    # 只有当 4pt 在 h_scale=1.0 下都放不下时才会到这里（极端场景）
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

import re

def _auto_format_ingredients(ingr_text: str, allergens_text: str) -> str:
    """自动将纯文本配料表转换为 Markdown 富文本：自动前缀加粗 + 自动过敏原下划线"""
    if not ingr_text:
        return ""
    text = ingr_text
    
    # 1. 自动前缀加粗 (例如: "[EN] Ingredients:")
    pattern_prefix1 = r'(\[[A-Z]{2}\]\s*[A-Za-z\u00C0-\u017F]+:)'
    text = re.sub(pattern_prefix1, r'**\1**', text)
    
    # 支持无括号或在 / 之后的前缀 (例如: "Zutaten:" 或 "/ Ingredients:")
    pattern_prefix2 = r'(^|/\s*)([A-Z][a-z\u00C0-\u017F]+:)'
    text = re.sub(pattern_prefix2, r'\1**\2**', text)
    
    # 2. 自动过敏原标红/下划线
    if allergens_text:
        # 清洗拆分
        tokens = [t.strip() for t in re.split(r'[,;]', allergens_text) if t.strip()]
        if tokens:
            # 按长度倒序，防止短词（如 Soy）错误覆盖长词（如 Soybeans）
            tokens.sort(key=len, reverse=True)
            escaped = [re.escape(t) for t in tokens]
            # 强化边界：左右不能是字母（防止把包含该字符串的单词也替换了）
            pattern_allergens = r'(?<![a-zA-Z\u00C0-\u017F])(' + '|'.join(escaped) + r')(?![a-zA-Z\u00C0-\u017F])'
            text = re.sub(pattern_allergens, lambda m: f"**__{m.group(1)}__**", text, flags=re.IGNORECASE)
            
    return text

def plm_to_blocks(data: dict, target_country: str = "DEFAULT", content_type: str = "standard_single") -> List[TextBlock]:
    """
    将 PLM JSON 数据转换为 TextBlock 列表。
    根据 content_type 决定使用单语言（带强前缀）或是多语言（读取原始格式）。
    由于采用了明确分类处理，未来如需添加其他正文版式风格，只需在下方追加判断分支即可。
    """
    from country_config import get_country_config
    blocks = []
    
    cfg = get_country_config(target_country)
    is_multi = (content_type == "multilingual")

    if is_multi:
        # ------- 多语言模式 -------
        ingr = data.get("ingredients", "").strip()
        allergens = data.get("allergens", "").strip()
        if ingr:
            ingr = _auto_format_ingredients(ingr, allergens)
            blocks.append(TextBlock(text=ingr))
            
        # 多语言模式下，过敏原通常内置在 ingredients 中并用 Markdown 加粗标记了
        # 所以跳过 allergens 的单独渲染
        
        storage = data.get("storage", "").strip()
        if storage:
            blocks.append(TextBlock(text=storage))
            
        prod_date = data.get("production_date", "").strip()
        best_before = data.get("best_before", "").strip()
        # 假设在多语言版里，长长的前缀已经在 string 内部写好了（如 **Best Before / ...:**）
        if prod_date or best_before:
            date_str = f"{prod_date}  {best_before}" if (prod_date and best_before) else (prod_date or best_before)
            blocks.append(TextBlock(text=date_str.strip()))
            
        origin = data.get("origin", "").strip()
        if origin:
            blocks.append(TextBlock(text=origin))
            
        mfr = data.get("manufacturer", "").strip()
        if mfr:
            blocks.append(TextBlock(text=mfr))
            
        addr = data.get("manufacturer_address", "").strip()
        if addr:
            blocks.append(TextBlock(text=addr))
            
        imp = data.get("importer_info", "").strip()
        if imp:
            blocks.append(TextBlock(text=imp))
            
    else:
        # ------- 单语言模式 (兼容老的加粗前缀) -------
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


# ---------------------------------------------------------------------------
# 标题自适应布局（最少压缩策略）
# ---------------------------------------------------------------------------

def layout_title(
    text_en: str,
    text_cn: str,
    flow_regions: List[FlowRect],
    font_name: str = _FONT_NAME,
    font_name_bold: str = _FONT_NAME_BOLD,
    max_size: float = 24.0,
    leading_ratio: float = 1.15,
    country_code: str = "DEFAULT",
    canvas=None,
) -> tuple:
    """
    标题自适应布局引擎（已与 content 完全解耦）。

    核心策略：基于模板容量的启发式 A/B 竞争。
      - 变体 A (显式分行)：强制中英文各占一行，优先保层级感。
      - 变体 B (流式拼接)：中英文融为一段，优先填满空间。

      标题字号的唯一硬约束 = 各国法规 aoe 最低字高（与 content 一致）。
      利用模板预留的高度计算预期最大行容量（expected_max_lines）。

    Args:
        text_en / text_cn:  产品名
        flow_regions:       标题区域（矩形或 L 型）
        country_code:       国家代码（用于法规最小字号）
        canvas:             ReportLab canvas

    Returns:
        (font_size, h_scale, LayoutResult)
    """
    import math

    if not text_en and not text_cn:
        return 0.0, 1.0, LayoutResult(region_usage=[0.0] * len(flow_regions))

    regulatory_min = get_min_font_pt(country_code)

    total_height = sum(r.height for r in flow_regions)
    ideal_line_h = regulatory_min * leading_ratio
    expected_max_lines = total_height / ideal_line_h if ideal_line_h > 0 else 0

    # ── 变体 A: 显式双行 ──
    variant_a = []
    if text_en: variant_a.append(TextBlock(text="", bold_prefix=text_en))
    if text_cn: variant_a.append(TextBlock(text="", bold_prefix=text_cn))

    # ── 变体 B: 连续流式拼接 ──
    variant_b = []
    if text_en and text_cn:
        merged = text_en + "  " + text_cn
        variant_b.append(TextBlock(text="", bold_prefix=merged))
    elif text_en:
        variant_b.append(TextBlock(text="", bold_prefix=text_en))
    elif text_cn:
        variant_b.append(TextBlock(text="", bold_prefix=text_cn))

    def _search_variant(blocks, min_sz: float, min_hs: float, new_line: bool):
        """扫描所有字号 [max_size → min_sz]，找纵向使用最多的方案。"""
        b_fs, b_hs = 0.0, 0.0
        b_res = None
        b_used_h = 0.0
        b_score = 0.0

        sizes = []
        s = max_size
        while s >= min_sz - 0.01:
            sizes.append(round(s, 1))
            s -= 0.5
        if round(min_sz, 1) not in sizes:
            sizes.append(round(min_sz, 1))
        sizes.sort(reverse=True)

        for fs in sizes:
            if fs < min_sz - 0.01:
                continue
            leading = fs * leading_ratio
            if leading <= 0 or total_height / leading < 1:
                continue

            fc = FontConfig(
                font_name=font_name, font_name_bold=font_name_bold,
                font_size=fs, leading_ratio=leading_ratio, h_scale=1.0,
            )
            result = layout_flow_content(blocks, flow_regions, fc, new_line_per_block=new_line)
            if not result.overflow:
                hs = 1.0
            else:
                hs_lo, hs_hi = min_hs, 1.0
                hs = None
                for _ in range(20):
                    mid = (hs_lo + hs_hi) / 2
                    fc2 = FontConfig(
                        font_name=font_name, font_name_bold=font_name_bold,
                        font_size=fs, leading_ratio=leading_ratio, h_scale=mid,
                    )
                    r2 = layout_flow_content(blocks, flow_regions, fc2, new_line_per_block=new_line)
                    if not r2.overflow:
                        hs = mid
                        hs_lo = mid
                    else:
                        hs_hi = mid
                if hs is None:
                    continue
                hs = math.floor(hs * 100) / 100

            # 评估最终方案
            fc_final = FontConfig(
                font_name=font_name, font_name_bold=font_name_bold,
                font_size=fs, leading_ratio=leading_ratio, h_scale=hs,
            )
            r_final = layout_flow_content(blocks, flow_regions, fc_final, new_line_per_block=new_line)
            actual_lines = r_final.total_lines
            used_h = actual_lines * (fs * leading_ratio)

            # 硬约束：使用高度不得超过区域总高度（红线：不可突破边界）
            if used_h > total_height + 0.5:
                continue

            # 核心评分：艺术感纵向填满优先 (Artistic Vertical Fill)
            # 用户倾向于：“把字高调大，然后执行一部分横向压缩，以把纵向空间用满”。
            # 通过 fs ** 1.5 提高字高的权重，使得算法宁愿牺牲一定 hs (变扁) 也要把字标大，
            # 从而完美填满预留的 vertical bounding box。
            score = (fs ** 1.5) * hs * actual_lines

            if (score > b_score + 0.1) or \
               (abs(score - b_score) <= 0.1 and fs > b_fs):
                b_fs, b_hs = fs, hs
                b_score = score
                b_res = r_final

        return b_fs, b_hs, b_res

    # ── 智能调度策略 ──
    best_fs, best_hs, best_blocks, best_nl = 0.0, 1.0, variant_a, True
    
    # 阶段一：尝试理想双行布局 (Variant A)
    # 优先级最高。允许 hs 最低压缩到 0.6，配合 fs**1.5 算法，自动寻找将字号撑到最大且能放下的方案。
    fs_a, hs_a, res_a = _search_variant(variant_a, regulatory_min, min_hs=0.60, new_line=True)
    if fs_a > 0 and res_a is not None:
        # 检查英文是否已经换行（占了 2+ 行）
        # 如果英文本身已经折行，强制分行就失去了视觉层级意义 → 切换到流式拼接
        en_lines = sum(1 for lp in res_a.lines if lp.region_idx == 0 or (len(variant_a) > 1 and lp.text and not any(c in lp.text for c in '的了是在有不')))
        # 更精确的方法：如果总行数 > 2（意味着英文占了 2+ 行），直接切 Variant B
        if text_cn and res_a.total_lines > 2:
            # 英文已换行 → 中文没必要独占一行，让它紧跟英文尾巴
            fs_b, hs_b, res_b = _search_variant(variant_b, regulatory_min, min_hs=0.60, new_line=False)
            if fs_b > 0:
                best_fs, best_hs, best_blocks, best_nl = fs_b, hs_b, variant_b, False
            else:
                best_fs, best_hs, best_blocks, best_nl = fs_a, hs_a, variant_a, True
        else:
            best_fs, best_hs, best_blocks, best_nl = fs_a, hs_a, variant_a, True
    else:
        # 阶段二：基于模板线索的降级抉择
        if expected_max_lines < 2.8:
            # 短标题预期：即使需要极限压缩，优先死保双行层级
            fs_a2, hs_a2, res_a2 = _search_variant(variant_a, regulatory_min, min_hs=0.45, new_line=True)
            if fs_a2 > 0:
                best_fs, best_hs, best_blocks, best_nl = fs_a2, hs_a2, variant_a, True
            else:
                # 极端破防（可能标题奇长），回退到流式拼接
                fs_b, hs_b, res_b = _search_variant(variant_b, regulatory_min, min_hs=0.35, new_line=False)
                best_fs, best_hs, best_blocks, best_nl = fs_b, hs_b, variant_b, False
        else:
            # 多语言长标题预期（本来就留了很多行）：果断切连续合并模式
            fs_b, hs_b, res_b = _search_variant(variant_b, regulatory_min, min_hs=0.7, new_line=False)
            if fs_b > 0:
                best_fs, best_hs, best_blocks, best_nl = fs_b, hs_b, variant_b, False
            else:
                # 放宽压缩底线继续尝试
                fs_b2, hs_b2, res_b2 = _search_variant(variant_b, regulatory_min, min_hs=0.35, new_line=False)
                best_fs, best_hs, best_blocks, best_nl = fs_b2, hs_b2, variant_b, False

    # 阶段三：绝对兜底 (Hard Fallback) —— 如果真的全炸了，强行塞 4pt 流式
    if best_fs == 0.0:
        hard_min = 4.0
        fs_b3, hs_b3, res_b3 = _search_variant(variant_b, hard_min, min_hs=0.3, new_line=False)
        if fs_b3 > 0:
            best_fs, best_hs, best_blocks, best_nl = fs_b3, hs_b3, variant_b, False
        else:
            # 最惨情况：连硬切 4pt 0.3缩放 都放不下，直接画
            best_fs, best_hs, best_blocks, best_nl = hard_min, 0.3, variant_b, False

    # ── 最终排版与微调 ──
    fc = FontConfig(
        font_name=font_name, font_name_bold=font_name_bold,
        font_size=best_fs, leading_ratio=leading_ratio, h_scale=best_hs,
    )
    # 最后一次排版不传 canvas，以便应用 y 轴偏移微调
    result = layout_flow_content(best_blocks, flow_regions, fc,
                                 canvas=None, new_line_per_block=best_nl)

    # -----------------------------------------------------------------------
    # 纵向居中 (Vertical Centering) 
    # 消除顶部贴边而在底部留下空荡荡的视觉违和感
    # -----------------------------------------------------------------------
    if result.total_lines > 0:
        actual_lines = result.total_lines
        used_h = actual_lines * (best_fs * leading_ratio)
        total_height = sum(r.height for r in flow_regions)
        gap = total_height - used_h
        if gap > 1.0:
            offset = gap / 2.0
            for placement in result.lines:
                placement.y -= offset
                
    # 手动绘制平移后的文字
    if canvas:
        for placement in result.lines:
            _render_line(canvas, placement, fc)

    return best_fs, best_hs, result

