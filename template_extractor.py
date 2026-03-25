"""
.ai 模板区域提取器

从设计师标注了色块的 .ai 文件中提取各语义区域的位置。
色块颜色与区域的映射关系固定，设计师按约定上色即可。

支持矩形和 L 型多边形区域。L 型会被分解为多个 FlowRect。

用法:
    cfg = extract_template_regions("template.ai")
    print(cfg.title, cfg.content, cfg.nut_table, ...)
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class TemplateRegion:
    """模板中一个语义区域的位置（PDF 坐标系，y=0在底部，y 向上增长）"""
    x: float       # 左边 x
    y: float       # 顶边 y（PDF 坐标）
    width: float
    height: float

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """底边 y（PDF 坐标）"""
        return self.y - self.height

    def __repr__(self):
        return (f"TemplateRegion(x={self.x:.1f}, y={self.y:.1f}, "
                f"w={self.width:.1f}, h={self.height:.1f})")


@dataclass
class TemplateConfig:
    """从 .ai 文件提取的完整模板配置"""
    page_width: float
    page_height: float
    source_file: str = ""

    # 简单矩形区域用 TemplateRegion
    nut_table: Optional[TemplateRegion] = None
    net_volume: Optional[TemplateRegion] = None
    logo: Optional[TemplateRegion] = None

    # L 型/多矩形区域用 List[TemplateRegion]（按 y 从上到下排序）
    title_rects: List[TemplateRegion] = field(default_factory=list)
    content_rects: List[TemplateRegion] = field(default_factory=list)
    # 环保标：每个青色矩形 = 一个图标槽位，按从左到右排序
    eco_icon_rects: List[TemplateRegion] = field(default_factory=list)

    @property
    def title(self) -> Optional[TemplateRegion]:
        """兼容旧接口：返回标题的 bounding box"""
        if not self.title_rects:
            return None
        return _bounding_box(self.title_rects)

    @property
    def content(self) -> Optional[TemplateRegion]:
        """兼容旧接口：返回内容的 bounding box"""
        if not self.content_rects:
            return None
        return _bounding_box(self.content_rects)

    @property
    def eco_icons(self) -> Optional[TemplateRegion]:
        """兼容旧接口：返回所有环保标槽位的 bounding box"""
        if not self.eco_icon_rects:
            return None
        return _bounding_box(self.eco_icon_rects)

    def summary(self) -> str:
        """输出各区域的概要信息"""
        lines = [
            f"模板: {self.source_file}",
            f"页面: {self.page_width:.1f} × {self.page_height:.1f} pt "
            f"({self.page_width/72*25.4:.1f} × {self.page_height/72*25.4:.1f} mm)",
        ]
        # 多矩形区域
        for name in ("title_rects", "content_rects", "eco_icon_rects"):
            rects = getattr(self, name)
            if rects:
                lines.append(f"  {name:16s}: {len(rects)} 个矩形")
                for i, r in enumerate(rects):
                    lines.append(f"    R{i+1}: {r}")
            else:
                lines.append(f"  {name:16s}: (未检测到)")
        # 简单矩形区域
        for name in ("nut_table", "net_volume", "logo"):
            region = getattr(self, name)
            if region:
                lines.append(f"  {name:16s}: {region}")
            else:
                lines.append(f"  {name:16s}: (未检测到)")
        return "\n".join(lines)


def _bounding_box(rects: List[TemplateRegion]) -> TemplateRegion:
    """计算多个矩形的 bounding box"""
    min_x = min(r.x for r in rects)
    max_x = max(r.x2 for r in rects)
    max_y = max(r.y for r in rects)
    min_y = min(r.bottom for r in rects)
    return TemplateRegion(
        x=min_x, y=max_y,
        width=max_x - min_x, height=max_y - min_y,
    )


# ---------------------------------------------------------------------------
# 色块颜色映射
# ---------------------------------------------------------------------------

# 色块 RGB (0-1) → 区域名称
# 使用欧氏距离匹配，容差 0.15
_COLOR_MAP: Dict[str, Tuple[float, float, float]] = {
    "title":      (0.81, 0.18, 0.15),   # 🔴 红色 #ce2d27
    "content":    (0.09, 0.41, 0.66),   # 🔵 蓝色 #1667a7
    "net_volume": (0.16, 0.58, 0.29),   # 🟢 绿色 #289349
    "nut_table":  (0.47, 0.18, 0.51),   # 🟣 紫色 #762e82
    "eco_icons":  (0.06, 0.60, 0.61),   # 🩵 青色 #0f9a9a
    "logo":       (0.94, 0.81, 0.16),   # 🟡 黄色 #f0ce29
}

_COLOR_TOLERANCE = 0.15


def _match_color(fill: Tuple[float, ...]) -> Optional[str]:
    """将填充色与已知色块匹配，返回区域名称或 None"""
    if len(fill) < 3:
        return None
    r, g, b = fill[0], fill[1], fill[2]
    best_name = None
    best_dist = float("inf")
    for name, (cr, cg, cb) in _COLOR_MAP.items():
        dist = math.sqrt((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    if best_dist <= _COLOR_TOLERANCE:
        return best_name
    return None


# ---------------------------------------------------------------------------
# 多边形 → 矩形分解
# ---------------------------------------------------------------------------

def _polygon_to_rects(
    items: list,
    page_height: float,
) -> List[TemplateRegion]:
    """
    将绘图对象的路径段分解为矩形列表。

    支持：
      - 单个 `re` 矩形
      - 由 `l` (line) 组成的轴对齐多边形（如 L 型 6 顶点）

    通过水平切割线将多边形分解为多个水平条带矩形。

    Args:
        items: 绘图对象的 items 列表
        page_height: 页面高度（用于 y 坐标转换）

    Returns:
        List[TemplateRegion]: 分解后的矩形列表（PDF 坐标系，y 从上到下排序）
    """
    # Case 1: 单个矩形
    if len(items) == 1 and items[0][0] == "re":
        ai_rect = items[0][1]
        return [TemplateRegion(
            x=ai_rect.x0,
            y=page_height - ai_rect.y0,
            width=ai_rect.width,
            height=ai_rect.height,
        )]

    # Case 2: 多边形 → 提取顶点
    vertices = []
    for item in items:
        if item[0] == "l":
            p1 = item[1]  # Point
            if not vertices or (abs(p1.x - vertices[-1][0]) > 0.1
                                or abs(p1.y - vertices[-1][1]) > 0.1):
                vertices.append((p1.x, p1.y))
            p2 = item[2]
            vertices.append((p2.x, p2.y))
        elif item[0] == "re":
            ai_rect = item[1]
            return [TemplateRegion(
                x=ai_rect.x0,
                y=page_height - ai_rect.y0,
                width=ai_rect.width,
                height=ai_rect.height,
            )]

    if len(vertices) < 4:
        return []

    # 去重（闭合多边形的首尾可能重复）
    if (abs(vertices[0][0] - vertices[-1][0]) < 0.5
            and abs(vertices[0][1] - vertices[-1][1]) < 0.5):
        vertices = vertices[:-1]

    # 收集所有 y 坐标并合并相近值（容差 1.5pt，处理浮点噪声）
    raw_ys = sorted(set(round(v[1], 1) for v in vertices))
    y_vals = []
    for y in raw_ys:
        if y_vals and abs(y - y_vals[-1]) < 1.5:
            # 合并到已有值（取平均）
            y_vals[-1] = (y_vals[-1] + y) / 2
        else:
            y_vals.append(y)

    if len(y_vals) < 2:
        return []

    # 对每个水平条带 [y_vals[i], y_vals[i+1]]，
    # 计算多边形在该条带内的水平范围（x_min, x_max）
    rects = []
    for i in range(len(y_vals) - 1):
        band_top = y_vals[i]      # .ai 坐标（y↓）
        band_bottom = y_vals[i + 1]
        band_mid = (band_top + band_bottom) / 2

        # 用射线法找该 y 高度上多边形的 x 边界
        # 仅使用非水平边（水平边不贡献 x 边界信息）
        x_intersections = []
        n = len(vertices)
        for j in range(n):
            v1 = vertices[j]
            v2 = vertices[(j + 1) % n]
            y1, y2 = v1[1], v2[1]

            # 跳过水平线段（不贡献交点）
            if abs(y1 - y2) < 0.1:
                continue

            # 检查这条边是否跨越 band_mid
            if (min(y1, y2) - 0.1) <= band_mid <= (max(y1, y2) + 0.1):
                t = (band_mid - y1) / (y2 - y1)
                x_cross = v1[0] + t * (v2[0] - v1[0])
                x_intersections.append(x_cross)

        if not x_intersections:
            continue

        x_min = min(x_intersections)
        x_max = max(x_intersections)
        band_h = band_bottom - band_top

        if x_max - x_min < 1 or band_h < 0.5:
            continue

        # .ai 坐标 → PDF 坐标
        pdf_y_top = page_height - band_top
        rects.append(TemplateRegion(
            x=round(x_min, 1),
            y=round(pdf_y_top, 1),
            width=round(x_max - x_min, 1),
            height=round(band_h, 1),
        ))

    # 按 y 从上到下排序（PDF 坐标系 y 越大越高）
    rects.sort(key=lambda r: -r.y)
    return rects


# ---------------------------------------------------------------------------
# 提取函数
# ---------------------------------------------------------------------------

def extract_template_regions(ai_path: str) -> TemplateConfig:
    """
    从 .ai 文件提取各区域位置。

    设计师需在 .ai 中用指定颜色的填充色块标注各区域：
      🔴 红 = 标题, 🔵 蓝 = 内容, 🟢 绿 = Net Volume,
      🟣 紫 = 营养表, 🩵 青 = 环保标, 🟡 黄 = Logo

    L 型色块会被自动分解为多个矩形（如标题避让 logo、content 避让 net_volume）。

    Args:
        ai_path: .ai 文件路径

    Returns:
        TemplateConfig: 包含各区域位置的配置对象（PDF 坐标系）
    """
    doc = fitz.open(ai_path)
    page = doc[0]
    W = page.rect.width
    H = page.rect.height

    config = TemplateConfig(
        page_width=W,
        page_height=H,
        source_file=ai_path,
    )

    # 多矩形区域（L 型支持）—— 同色取面积最大的一组
    multi_rect_names = {"title", "content"}
    # 累积多矩形区域 —— 同色的每个矩形单独保留
    accum_rect_names = {"eco_icons"}
    # 简单矩形区域
    simple_rect_names = {"nut_table", "net_volume", "logo"}

    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue

        region_name = _match_color(fill)
        if region_name is None:
            continue

        rect = d["rect"]
        if rect.width < 5 or rect.height < 5:
            continue

        if region_name in multi_rect_names:
            # 分解多边形为矩形列表（取面积最大的一组）
            rects = _polygon_to_rects(d["items"], H)
            attr = f"{region_name}_rects"
            existing = getattr(config, attr)
            new_area = sum(r.width * r.height for r in rects)
            old_area = sum(r.width * r.height for r in existing)
            if new_area > old_area:
                setattr(config, attr, rects)
        elif region_name in accum_rect_names:
            # 累积：每个同色矩形单独保留（如多个环保标槽位）
            rects = _polygon_to_rects(d["items"], H)
            config.eco_icon_rects.extend(rects)
        else:
            # 简单矩形
            rects = _polygon_to_rects(d["items"], H)
            if rects:
                region = rects[0]  # 取第一个（应该只有一个）
                existing = getattr(config, region_name)
                if existing is None or (region.width * region.height
                                        > existing.width * existing.height):
                    setattr(config, region_name, region)

    doc.close()

    # 环保标槽位按从左到右排序（x 坐标升序）
    config.eco_icon_rects.sort(key=lambda r: r.x)

    return config


# ---------------------------------------------------------------------------
# CLI 测试
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python template_extractor.py <path_to.ai>")
        sys.exit(1)
    cfg = extract_template_regions(sys.argv[1])
    print(cfg.summary())
