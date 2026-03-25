# flow_layout.py — 自适应渲染函数说明

## 数据模型

### FlowRect — 文字可流入的矩形区域

```python
@dataclass
class FlowRect:
    x: float        # 左边 x
    y: float        # 顶部 y（PDF 坐标系，y 向上增长）
    width: float    # 可用宽度
    height: float   # 可用高度（向下延伸）
    seamless: bool = False  # True = 与上一个区域无缝衔接（保持 leading 节奏）
```

多个 `FlowRect` 按顺序排列，可构成 **倒L型**、**正L型**、**全宽** 等复合区域。

### TextBlock — 文本块

```python
@dataclass
class TextBlock:
    text: str              # 正文内容
    bold_prefix: str = ""  # 粗体前缀（如 "Ingredients: "）
```

渲染时 `bold_prefix + text` 拼接为完整文本，`bold_prefix` 部分用粗体字体绘制。

### FontConfig — 字体配置

```python
@dataclass
class FontConfig:
    font_name: str          # 正文字体
    font_name_bold: str     # 粗体字体
    font_size: float        # 字号 (pt)
    leading_ratio: float    # 行间隔 = font_size × leading_ratio
    h_scale: float          # 横向压缩比（1.0 = 无压缩）
    descent_ratio: float    # descender 深度 / font_size
```

---

## Content 渲染函数

### `layout_flow_content()` — 流式排版引擎

```python
def layout_flow_content(
    blocks: List[TextBlock],       # 文本块列表（按显示顺序）
    flow_regions: List[FlowRect],  # 有序矩形序列（文字依次流经）
    font_config: FontConfig,       # 字体配置（字号、行距、横向压缩等）
    canvas=None,                   # ReportLab canvas（传入则渲染，不传则仅估算）
) -> LayoutResult
```

**功能**：将文本沿 `flow_regions` 序列从上到下流动排版。当一个矩形排满后，自动溢出到下一个矩形。

**返回值** `LayoutResult`：
- `overflow: bool` — 是否溢出
- `lines: List[LinePlacement]` — 每行的位置、文本、字体信息
- `region_usage: List[float]` — 各区域使用高度
- `total_lines: int` — 总行数

### `find_best_font_size()` — 三阶段自适应字号搜索

```python
def find_best_font_size(
    blocks: List[TextBlock],       # 文本块列表
    flow_regions: List[FlowRect],  # 区域列表
    font_name: str,                # 正文字体
    font_name_bold: str,           # 粗体字体
    leading_ratio: float = 1.15,   # 行间隔比例
    h_scale: float = 1.0,         # 初始横向比例（固定 1.0）
    min_size: float = 4.0,        # 最小字号（法规约束）
    max_size: float = 16.0,       # 最大字号
    min_h_scale: float = 0.35,    # 最小横向压缩比
    iterations: int = 20,         # 二分搜索迭代次数
) -> tuple[float, float]          # 返回 (font_size, h_scale)
```

**三阶段搜索策略**：
1. **字号搜索**：二分搜索 `max_size → min_size`，`h_scale=1.0`
2. **横向压缩**：若 `min_size` 仍溢出，固定字号，搜索 `h_scale`（`1.0 → 0.35`）
3. **极限降号**：若 `min_h_scale` 仍溢出，固定 `h_scale=0.35`，继续降字号到 4pt

### `plm_to_blocks()` — PLM 数据转 TextBlock

```python
def plm_to_blocks(data: dict) -> List[TextBlock]
```

将 PLM JSON 数据（ingredients, allergens, storage, production_date, origin, manufacturer, address, importer_info）转换为带粗体前缀的 TextBlock 列表。

---

## 标题渲染函数

### `layout_title()` — 标题自适应布局

```python
def layout_title(
    text_en: str,                  # 英文产品名
    text_cn: str,                  # 中文产品名（可为空）
    flow_regions: List[FlowRect],  # 标题区域（矩形或 L 型）
    content_font_size: float,      # content 区域的自适应字号
    font_name: str,                # 正文字体
    font_name_bold: str,           # 粗体字体
    title_ratio: float = 1.1,     # 标题最小字号 = content_font_size × 1.1
    max_size: float = 24.0,       # 标题最大字号
    leading_ratio: float = 1.15,  # 行间隔 = font_size × 1.15（固定 15% 间距）
    canvas=None,                   # ReportLab canvas（传入则渲染，不传则仅计算）
) -> tuple[float, float, LayoutResult]  # (font_size, h_scale, LayoutResult)
```

**功能**：将英文名 + 中文名作为全粗体文本块，流入标题区域，自适应三种场景：

| 场景 | 说明 |
|------|------|
| 双行 | EN 一行 + CN 一行 |
| 同行 | EN + CN 放在同一行（标题短时） |
| 多行 | EN 过长自动换行 + CN 跟在最后 |

**约束**：
- 标题尽可能填满区域（字号尽量大）
- 最小字号 = `content_font_size × title_ratio`（默认 1.1）
- 横向压缩仅在区域过窄时触发

**调用顺序**（在 `render_pipeline.py` 中）：
1. 先调用 `render_content(canvas=None)` 得到 content 字号（dry-run）
2. 再调用 `render_title(content_font_size=...)` 渲染标题
3. 再调用 `render_content(canvas=c)` 渲染 content
4. 最后渲染 nutrition / net_volume / logo / eco_icons（互不依赖）

---

## 解耦式渲染框架

### 整体流程

```
.ai 模板文件
  → template_extractor.extract_template_regions()
  → TemplateConfig
  → render_pipeline.render_label(template, data, country_cfg)
  → PDF 字节
```

---

## template_extractor.py — 模板区域提取

### TemplateRegion — 单个区域位置

```python
@dataclass
class TemplateRegion:
    x: float       # 左边 x（PDF 坐标）
    y: float       # 顶边 y（PDF 坐标，y 向上增长）
    width: float
    height: float
```

### TemplateConfig — 完整模板配置

```python
@dataclass
class TemplateConfig:
    page_width: float
    page_height: float
    source_file: str = ""

    title: Optional[TemplateRegion] = None        # 标题区域
    content: Optional[TemplateRegion] = None       # 内容区域
    nut_table: Optional[TemplateRegion] = None     # 营养表区域
    net_volume: Optional[TemplateRegion] = None    # 净含量区域
    logo: Optional[TemplateRegion] = None          # Logo 区域
    eco_icons: Optional[TemplateRegion] = None     # 环保标区域（可选）
```

所有区域均为 Optional，模板中未标注的区域为 None。

### `extract_template_regions()` — 从 .ai 提取区域

```python
def extract_template_regions(ai_path: str) -> TemplateConfig
```

**识别方式**：设计师在 .ai 文件中用彩色填充矩形标注区域。

| 颜色 | HEX | 区域名 |
|------|------|--------|
| 🔴 红 | `#ce2d27` | title |
| 🔵 蓝 | `#1667a7` | content |
| 🟢 绿 | `#289349` | net_volume |
| 🟣 紫 | `#762e82` | nut_table |
| 🩵 青 | `#0f9a9a` | eco_icons |
| 🟡 黄 | `#f0ce29` | logo |

**坐标转换**：.ai 坐标 y=0 在顶部（y↓），自动转为 PDF 坐标 y=0 在底部（y↑）。

---

## region_renderers.py — 区域渲染器

### `render_content()` — 内容区域

```python
def render_content(
    canvas: Optional[Canvas],       # None = dry-run（仅计算字号）
    regions: List[FlowRect],        # content 的 FlowRect 列表
    data: dict,                     # PLM 数据
    country_cfg: dict,              # 国家法规配置
) -> Tuple[float, float]            # (font_size, h_scale)
```

### `render_title()` — 标题区域

```python
def render_title(
    canvas: Optional[Canvas],
    regions: List[FlowRect],        # 标题的 FlowRect 列表（可能 L 型）
    data: dict,
    content_font_size: float,       # 来自 render_content 的输出
    title_ratio: float = 1.1,
) -> Tuple[float, float]            # (font_size, h_scale)
```

### `render_nutrition()` — 营养表

```python
def render_nutrition(
    canvas: Canvas,
    region: TemplateRegion,         # 营养表矩形区域
    data: dict,
    country_cfg: dict,
) -> float                          # table_bottom_y
```

### `render_net_volume()` — 净含量

```python
def render_net_volume(
    canvas: Canvas,
    region: TemplateRegion,
    data: dict,                     # 需含 net_weight
)
```

### `render_logo()` — Logo

```python
def render_logo(
    canvas: Canvas,
    region: TemplateRegion,
    logo_path: str,
)
```

### `render_eco_icons()` — 环保标

```python
def render_eco_icons(
    canvas: Canvas,
    region: TemplateRegion,
    data: dict,                     # 可含 eco_icons 列表
)
```

---

## render_pipeline.py — 渲染管线

### `build_flow_regions()` — 构建 FlowRect

```python
def build_flow_regions(template: TemplateConfig) -> dict
```

从 `TemplateConfig` 构建各渲染器需要的 `FlowRect` 列表。处理：
- **标题 L 型**：扣除 logo 区域，生成窄行 + 全宽行
- **Content 倒 L 型**：全宽区域 + 营养表旁左栏（扣除 net_volume 预留）

返回 `{"title": [FlowRect, ...], "content": [FlowRect, ...]}`，仅包含模板中存在的区域。

### `render_label()` — 渲染入口

```python
def render_label(
    template_or_path,               # TemplateConfig 或 .ai 文件路径
    data: dict,                     # PLM 产品数据
    country_cfg: Optional[dict],    # 国家法规配置
) -> bytes                          # PDF 字节
```

**执行顺序**（依赖驱动）：

| 阶段 | 渲染器 | 依赖 |
|------|--------|------|
| 1 | `render_content(dry-run)` | 区域 |
| 2 | `render_logo` | logo 区域 |
| 3 | `render_title` | content_font_size |
| 4 | `render_content` | 区域 |
| 5 | `render_nutrition` | nut_table 区域 |
| 6 | `render_net_volume` | net_volume 区域 |
| 7 | `render_eco_icons` | eco_icons 区域（可选） |

各区域按需渲染，模板中不存在的区域自动跳过。

