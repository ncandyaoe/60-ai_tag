"""
eval_title.py — 标题渲染评估脚本 (AutoResearch 评估器)

5 维度评分（总分 100）：
  - 不溢出 (30分)：overflow=False 且文字不超出 FlowRect 边界
  - 填充率 (25分)：文字占标题区域面积的比例
  - 均匀分布 (20分)：各行宽度利用率方差越小越好
  - 压缩合理 (15分)：h_scale 越接近 1.0 越好
  - 字号合规 (10分)：font_size >= 法规最小字号 (aoe 字高)

运行方式:
    python eval_title.py
"""

import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from reportlab.pdfbase.pdfmetrics import stringWidth

from flow_layout import (
    FlowRect, FontConfig, TextBlock, LayoutResult,
    layout_flow_content, find_best_font_size, layout_title,
)

# ---------------------------------------------------------------------------
# 测试用例定义
# ---------------------------------------------------------------------------

@dataclass
class TitleTestCase:
    """一个标题测试用例"""
    id: str
    description: str
    text_en: str
    text_cn: str
    flow_regions: List[FlowRect]
    content_font_size: float = 7.0  # 仅用于向后兼容，评分时已不使用
    country_code: str = "DEFAULT"  # 国家代码（用于法规最小字号）
    difficulty: str = "medium"  # easy / medium / hard / extreme


def _get_test_cases() -> List[TitleTestCase]:
    """8 个测试用例，覆盖 short/medium/long/extreme × rect/L-shape"""

    # --- 区域定义 ---
    # 实际草菇老抽模板的 L 型区域
    lshape_regions = [
        FlowRect(x=5.9, y=334.3, width=105.8, height=16.3, seamless=False),  # R1 窄（logo 旁）
        FlowRect(x=5.8, y=318.0, width=130.3, height=14.8, seamless=True),   # R2 全宽
    ]

    # 普通矩形区域 (130 × 30pt)
    rect_region = [
        FlowRect(x=5.0, y=340.0, width=130.0, height=30.0, seamless=False),
    ]

    # 窄矩形区域 (80 × 25pt)
    narrow_rect = [
        FlowRect(x=5.0, y=340.0, width=80.0, height=25.0, seamless=False),
    ]

    # 大矩形区域 (180 × 40pt)
    wide_rect = [
        FlowRect(x=5.0, y=340.0, width=180.0, height=40.0, seamless=False),
    ]

    return [
        TitleTestCase(
            id="T1", description="短标题 矩形",
            text_en="SOY SAUCE",
            text_cn="酱油",
            flow_regions=rect_region,
            content_font_size=7.0,
            difficulty="easy",
        ),
        TitleTestCase(
            id="T2", description="中等标题 矩形",
            text_en="PREMIUM DARK SOY SAUCE / SALSA DE SOYA OSCURA",
            text_cn="老抽酱油",
            flow_regions=rect_region,
            content_font_size=7.0,
            difficulty="medium",
        ),
        TitleTestCase(
            id="T3", description="长标题 L型",
            text_en="[EN] MUSHROOM DARK SOY SAUCE / [NL] CHAMPIGNON DONKERE SOJASAUS / [ES] SALSA DE SOYA",
            text_cn="草菇老抽",
            flow_regions=lshape_regions,
            content_font_size=6.5,
            difficulty="medium",
        ),
        TitleTestCase(
            id="T4", description="超长标题 L型 (草菇老抽实际)",
            text_en="[EN] 0 MUSHROOM DARK SOY SAUCE / [NL] 0 CHAMPIGNON DONKERE SOJASAUS / [ES] 0 SALSA DE SOYA OSCURA DE SETA DE PAJA / [DE] 0 SOJASAUCE MIT PILZGESCHMACK / [FR] 0 SAUCE DE SOJA AU CHAMPIGNON",
            text_cn="0草菇老抽",
            flow_regions=lshape_regions,
            content_font_size=6.3,
            difficulty="hard",
        ),
        TitleTestCase(
            id="T5", description="仅英文 L型",
            text_en="PREMIUM ORGANIC EXTRA VIRGIN SESAME OIL / HUILE DE SÉSAME VIERGE",
            text_cn="",
            flow_regions=lshape_regions,
            content_font_size=6.5,
            difficulty="medium",
        ),
        TitleTestCase(
            id="T6", description="超短标题 L型 (填满度)",
            text_en="OIL",
            text_cn="油",
            flow_regions=lshape_regions,
            content_font_size=6.5,
            difficulty="easy",
        ),
        TitleTestCase(
            id="T7", description="极长标题 窄矩形",
            text_en="[EN] MUSHROOM DARK SOY SAUCE / [NL] CHAMPIGNON DONKERE SOJASAUS / [ES] SALSA DE SOYA OSCURA / [DE] SOJASAUCE MIT PILZGESCHMACK / [FR] SAUCE DE SOJA / [IT] SALSA DI SOIA SCURA",
            text_cn="草菇老抽",
            flow_regions=narrow_rect,
            content_font_size=5.5,
            difficulty="extreme",
        ),
        TitleTestCase(
            id="T8", description="正常标题 大矩形 (基准)",
            text_en="SESAME OIL / HUILE DE SÉSAME",
            text_cn="芝麻油",
            flow_regions=wide_rect,
            content_font_size=7.0,
            difficulty="easy",
        ),
    ]


# ---------------------------------------------------------------------------
# 评分函数
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    """单个测试用例的评分结果"""
    test_id: str
    total: float
    no_overflow: float     # 30 分
    fill_rate: float       # 25 分
    uniformity: float      # 20 分
    compression: float     # 15 分
    font_compliance: float # 10 分
    details: Dict = field(default_factory=dict)


def score_title(tc: TitleTestCase) -> ScoreResult:
    """
    对一个测试用例调用 layout_title 并按 5 维度评分。

    Returns:
        ScoreResult 对象
    """
    font_size, h_scale, result = layout_title(
        text_en=tc.text_en,
        text_cn=tc.text_cn,
        flow_regions=tc.flow_regions,
        country_code=tc.country_code,
    )

    from flow_layout import get_min_font_pt
    min_required = get_min_font_pt(tc.country_code)
    total_area = sum(r.width * r.height for r in tc.flow_regions)
    details = {
        "font_size": round(font_size, 2),
        "h_scale": round(h_scale, 3),
        "overflow": result.overflow,
        "total_lines": result.total_lines,
        "min_required_fs": round(min_required, 2),
    }

    # ── 1. 不溢出 (30 分) ──
    if result.overflow:
        s_overflow = 0.0
    else:
        # 检查每行是否超出 FlowRect 边界
        overflow_lines = 0
        for line in result.lines:
            ri = line.region_idx
            if ri >= len(tc.flow_regions):
                overflow_lines += 1
                continue
            region = tc.flow_regions[ri]
            raw_w = stringWidth(line.text, line.font_name, line.font_size)
            # 检查是否有粗体混排
            if line.bold_end > 0:
                bold_w = stringWidth(line.text[:line.bold_end], "AliPuHuiTi-Bold", line.font_size)
                reg_w = stringWidth(line.text[line.bold_end:], "AliPuHuiTi", line.font_size)
                raw_w = bold_w + reg_w
            phys_w = raw_w * h_scale
            right_edge = line.x + phys_w
            region_right = region.x + region.width
            if right_edge > region_right + 1.0:  # 1pt 容差
                overflow_lines += 1

        if overflow_lines == 0:
            s_overflow = 30.0
        else:
            s_overflow = max(0, 30.0 - overflow_lines * 10)

    details["overflow_lines"] = overflow_lines if not result.overflow else "N/A"

    # ── 2. 填充率 (25 分) ──
    if result.total_lines == 0 or total_area == 0:
        s_fill = 0.0
    else:
        # 使用的区域面积 = 各区域的 usage 高度 × 对应宽度
        used_area = 0.0
        for ri, usage_h in enumerate(result.region_usage):
            if ri < len(tc.flow_regions):
                used_area += min(usage_h, tc.flow_regions[ri].height) * tc.flow_regions[ri].width
        fill_ratio = min(used_area / total_area, 1.0)
        # 0.6 以下低分，0.9+ 满分
        s_fill = min(25.0, 25.0 * (fill_ratio / 0.85))
        details["fill_ratio"] = round(fill_ratio, 3)

    # ── 3. 均匀分布 (20 分) ──
    if result.total_lines <= 1:
        s_uniform = 20.0  # 单行无需均匀性评估
    else:
        # 每行的宽度利用率 = 物理文字宽度 / 该 FlowRect 宽度
        utilizations = []
        for line in result.lines:
            ri = line.region_idx
            if ri >= len(tc.flow_regions):
                continue
            region = tc.flow_regions[ri]
            raw_w = stringWidth(line.text, line.font_name, line.font_size)
            if line.bold_end > 0:
                bold_w = stringWidth(line.text[:line.bold_end], "AliPuHuiTi-Bold", line.font_size)
                reg_w = stringWidth(line.text[line.bold_end:], "AliPuHuiTi", line.font_size)
                raw_w = bold_w + reg_w
            phys_w = raw_w * h_scale
            util = phys_w / region.width if region.width > 0 else 0
            utilizations.append(min(util, 1.0))

        if len(utilizations) >= 2:
            # 最后一行通常较短，排除
            main_utils = utilizations[:-1] if len(utilizations) > 2 else utilizations
            std_dev = statistics.stdev(main_utils) if len(main_utils) > 1 else 0
            # std_dev 越小越好: 0 → 20分, 0.3+ → 0分
            s_uniform = max(0, 20.0 * (1 - std_dev / 0.3))
        else:
            s_uniform = 15.0
        details["utilizations"] = [round(u, 3) for u in utilizations]

    # ── 4. 压缩合理 (15 分) ──
    # h_scale=1.0 → 15分, h_scale=0.5 → 7.5分, h_scale=0.1 → ~1.5分
    s_compression = 15.0 * h_scale
    details["h_scale_score"] = round(s_compression, 1)

    # ── 5. 字号合规 (10 分) ──
    if font_size >= min_required - 0.01:  # 微小浮点容差
        s_font = 10.0
    else:
        # 字号低于要求，按比例扣分
        ratio = font_size / min_required if min_required > 0 else 0
        s_font = 10.0 * ratio

    total = s_overflow + s_fill + s_uniform + s_compression + s_font

    return ScoreResult(
        test_id=tc.id,
        total=round(total, 1),
        no_overflow=round(s_overflow, 1),
        fill_rate=round(s_fill, 1),
        uniformity=round(s_uniform, 1),
        compression=round(s_compression, 1),
        font_compliance=round(s_font, 1),
        details=details,
    )


# ---------------------------------------------------------------------------
# 运行全部测试
# ---------------------------------------------------------------------------

def run_all() -> dict:
    """运行全部测试用例并返回汇总结果"""
    cases = _get_test_cases()
    results = []

    for tc in cases:
        sr = score_title(tc)
        results.append(sr)

    avg_score = sum(r.total for r in results) / len(results)

    return {
        "average_score": round(avg_score, 1),
        "results": results,
        "passed": sum(1 for r in results if r.total >= 80),
        "total_cases": len(results),
    }


def print_report(summary: dict):
    """打印格式化报告"""
    print("=" * 72)
    print(f"  🧪 标题渲染评估报告    平均分: {summary['average_score']:.1f}/100")
    print(f"  通过 (≥80): {summary['passed']}/{summary['total_cases']}")
    print("=" * 72)

    header = f"{'ID':>4} {'描述':<24} {'总分':>5} {'溢出':>4} {'填充':>4} {'均匀':>4} {'压缩':>4} {'字号':>4}"
    print(header)
    print("-" * 72)

    cases = _get_test_cases()
    case_map = {tc.id: tc for tc in cases}

    for r in summary["results"]:
        tc = case_map.get(r.test_id)
        desc = tc.description if tc else r.test_id
        status = "✅" if r.total >= 80 else "⚠️" if r.total >= 60 else "❌"
        print(f"{r.test_id:>4} {desc:<24} {r.total:>5.1f} {r.no_overflow:>4.0f} {r.fill_rate:>4.0f} "
              f"{r.uniformity:>4.0f} {r.compression:>4.0f} {r.font_compliance:>4.0f}  {status}")

    print("-" * 72)
    print()

    # 详情
    for r in summary["results"]:
        if r.total < 80:
            print(f"  ⚠️ {r.test_id}: {r.details}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    summary = run_all()
    print_report(summary)
