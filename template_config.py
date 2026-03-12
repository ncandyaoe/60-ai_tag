"""
模板配置系统

将标签布局参数从硬编码提取为可配置的 dataclass，
为多模板适配做准备。
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from reportlab.lib.units import mm


@dataclass
class LogoConfig:
    """Logo 区域配置"""
    width_pt: float = 40           # logo 宽度 (pt)
    height_pt: float = 5.4 * mm    # logo 高度 5.4mm
    padding_pt: float = 2          # logo 与文字的间距 (pt)
    enabled: bool = True           # 是否显示 logo

    @property
    def reserve_w(self) -> float:
        """logo 占用的宽度（含 padding）"""
        return (self.width_pt + self.padding_pt) if self.enabled else 0

    @property
    def zone_h(self) -> float:
        """logo 影响的高度范围"""
        return (self.height_pt + self.padding_pt) if self.enabled else 0


@dataclass
class NutritionConfig:
    """营养表配置"""
    right_col_ratio: float = 0.62   # 营养表占标签宽度的比例
    col_gap_pt: float = 4           # 左右栏间距 (pt)
    row_height_mm: float = 2.8      # 营养表行高 (mm)
    font_padding_pt: float = 2      # 行高 - 字号的 padding (pt)
    format: str = "australia"       # 营养表格式: australia | canada_bilingual | eu

    @property
    def row_height_pt(self) -> float:
        return self.row_height_mm * mm

    @property
    def font_size_pt(self) -> float:
        return self.row_height_pt - self.font_padding_pt

    @property
    def left_col_ratio(self) -> float:
        return 1 - self.right_col_ratio


@dataclass
class FixedSizes:
    """固定字号（不参与自适应缩放）"""
    title_pt: float = 8.0    # 英文品名
    cn_pt: float = 9.8       # 中文品名
    net_pt: float = 21.0     # Net Volume


@dataclass
class AdaptiveRange:
    """自适应字号的最小/最大范围"""
    body_min_pt: float = 4.0
    body_max_pt: float = 16.0
    ingr_min_pt: float = 4.0
    ingr_max_pt: float = 14.0


@dataclass
class TemplateConfig:
    """标签模板配置

    将 label_renderer.py 中分散的硬编码参数统一管理。
    每个模板实例代表一种标签布局（尺寸、字号、障碍物位置等）。
    """
    # --- 模板标识 ---
    template_id: str = "au_70x69"
    display_name: str = "澳洲小标签 (70×69mm)"

    # --- 标签物理尺寸 ---
    label_width_mm: float = 70.0
    label_height_mm: float = 69.0
    margin_mm: float = 2.0

    # --- 字号配置 ---
    fixed_sizes: FixedSizes = field(default_factory=FixedSizes)
    adaptive_range: AdaptiveRange = field(default_factory=AdaptiveRange)

    # --- 布局子配置 ---
    logo: LogoConfig = field(default_factory=LogoConfig)
    nutrition: NutritionConfig = field(default_factory=NutritionConfig)

    # --- 字体度量（字体相关，通常固定） ---
    cap_height_ratio: float = 0.735   # cap height / em
    x_height_ratio: float = 0.54      # x-height / em

    # ===========================
    # 计算属性（pt 单位，供渲染器使用）
    # ===========================
    @property
    def label_w(self) -> float:
        """标签宽度 (pt)"""
        return self.label_width_mm * mm

    @property
    def label_h(self) -> float:
        """标签高度 (pt)"""
        return self.label_height_mm * mm

    @property
    def margin(self) -> float:
        """出血位 (pt)"""
        return self.margin_mm * mm

    @property
    def content_w(self) -> float:
        """内容区域宽度 (pt)"""
        return self.label_w - 2 * self.margin

    @property
    def content_h(self) -> float:
        """内容区域高度 (pt) = available_h"""
        return self.label_h - 2 * self.margin

    @property
    def left_col_w(self) -> float:
        """左栏宽度 (pt)"""
        return self.content_w * self.nutrition.left_col_ratio

    def size_max(self) -> Dict[str, float]:
        """自适应字号上限字典（兼容原 _SIZE_MAX 格式）"""
        return {
            "title": self.fixed_sizes.title_pt,
            "cn": self.fixed_sizes.cn_pt,
            "body": self.adaptive_range.body_max_pt,
            "ingr": self.adaptive_range.ingr_max_pt,
            "nut": self.nutrition.font_size_pt,
            "net": self.fixed_sizes.net_pt,
        }

    def size_min(self) -> Dict[str, float]:
        """自适应字号下限字典（兼容原 _SIZE_MIN 格式）"""
        return {
            "title": self.fixed_sizes.title_pt,
            "cn": self.fixed_sizes.cn_pt,
            "body": self.adaptive_range.body_min_pt,
            "ingr": self.adaptive_range.ingr_min_pt,
            "nut": self.nutrition.font_size_pt,
            "net": self.fixed_sizes.net_pt,
        }

    def net_reserve(self, has_net_weight: bool) -> float:
        """Net Volume 预留高度 (pt)"""
        if not has_net_weight:
            return 0
        return self.fixed_sizes.net_pt * self.cap_height_ratio


# ===========================
# 模板注册表
# ===========================
_TEMPLATE_REGISTRY: Dict[str, TemplateConfig] = {}


def register_template(config: TemplateConfig):
    """注册一个模板"""
    _TEMPLATE_REGISTRY[config.template_id] = config


def get_template(template_id: str) -> TemplateConfig:
    """获取模板配置，不存在时返回默认模板"""
    return _TEMPLATE_REGISTRY.get(template_id, get_default_template())


def get_default_template() -> TemplateConfig:
    """返回默认模板（当前 AU 70×69mm）"""
    return _TEMPLATE_REGISTRY.get("au_70x69", TemplateConfig())


def list_templates() -> Dict[str, str]:
    """返回所有可用模板 {id: display_name}"""
    return {tid: t.display_name for tid, t in _TEMPLATE_REGISTRY.items()}


# ===========================
# 注册默认模板
# ===========================
register_template(TemplateConfig(
    template_id="au_70x69",
    display_name="澳洲小标签 (70×69mm)",
    label_width_mm=70.0,
    label_height_mm=69.0,
    margin_mm=2.0,
))
