"""
营养表数据模型与各国布局配置

定义了各国营养表的列结构、标题区属性、行高、线边框以及多语言等详细版式信息。
通过实现这一层，render_nutrition() 可以变成一个纯渲染循环，完全无需包含 if/elif 分支。
"""
from typing import List, Dict, Optional, Union
from dataclasses import dataclass, field

@dataclass
class NutColumn:
    key: str
    width_ratio: float
    align: str = "center"

@dataclass
class NutHeaderRow:
    cells: List[str]               
    bold: bool = False             
    span_full: bool = False        
    template: bool = False         
    multi_line: bool = False       
    draw_line_below: bool = True   
    line_width_below: float = 0.0  
    line_span_col: Optional[int] = None
    line_padding: float = 0.0      # 整体左右两端向内缩进
    line_left_padding: float = 0.0 # 覆盖全局，针对左侧缩进距离
    line_right_padding: float = 0.0# 覆盖全局，针对右侧缩进距离
    fill_color: Optional[tuple] = None   
    text_color: tuple = (0, 0, 0)        
    align: str = "center"          
    valign: str = "center"         # 垂直对齐，可选 "center", "ca_header"
    height_ratio: float = 1.0      
    font_ratio: float = 1.0        # 标题行全局字体缩放比
    font_ratios: Optional[List[float]] = None # 局部覆盖列字体缩放比（如 [2.5, 1.0]）
    col_sep_here: bool = False     # True = 这一行之后才开始画竖分隔线
    font_override: Optional[Union[str, List[str]]] = None # 独立字体，如阿里巴巴普惠体 Heavy，也可按列传 List
    independent_tz: bool = False   # True = 单独计算横向压缩占比
    horizontal_padding: Optional[float] = None # 左右横向预留距离
    col_width_ratios: Optional[List[float]] = None # 局部覆盖列宽占比（如 [0.4, 0.6]）
    margin_below: float = 0.0      # 行边框下方边缘与下一行顶部的极小安全留白
    margin_top: float = 0.0        # 行边框上方边缘对上一行的距离（负值则能发生视觉向上侵入）


@dataclass
class NutFooterRow:
    """营养表底部行 — 满行布局 + 自动换行"""
    text: str                       # 原始文本（长句），引擎自动折行
    bold: bool = False
    font_ratio: float = 0.8         # 脚注通常比正文小
    height_ratio: float = 1.6       # 行高倍率（应能容纳折行后的多行文字）
    draw_line_below: bool = True
    thick_line_below: bool = False
    margin_top: float = 0.0
    align: str = "left"


@dataclass
class NutritionLayout:
    name: str                                
    columns: List[NutColumn]                 
    header_rows: List[NutHeaderRow]          
    footer_rows: List[NutFooterRow] = field(default_factory=list)  # 底部行（满行 + 自动换行）
    draw_data_row_lines: bool = True         
    data_row_line_width: float = 0.3
    data_row_line_padding: float = 0.0 # 数据排版区横线的左右边距         
    header_line_width: float = 0.5           
    border_line_width: float = 0.5           
    outer_border_line_width: Optional[float] = None
    thick_line_width: float = 1.5            # 强化的数据行分割线粗细（如加拿大特定分组）
    sub_indent: float = 10.0                 
    draw_col_sep: bool = True                # 全局开关：是否绘制列与列之间的垂直分割线
    col_sep_in_data: bool = True             # False = 竖线仅在列头行，不延伸进数据行
    col_padding: float = 1.5                 # 数据列内容与左右外框/竖线的距离
    line_height_ratio: float = 1.15          # 行高 = 字号 x 此值 (越大行间距越宽)
    bold_main_items: bool = False            # True = 非 sub 项的数据行全部加粗
    reference_font_size: float = 0.0         # 参考字号(pt)。>0时所有绝对pt值按 actual_fs/ref_fs 缩放
    name_mapping: Optional[Dict[str, str]] = None  

    @property
    def n_header_rows(self) -> float:
        n = 0.0
        for row in self.header_rows:
            n += 2 * row.height_ratio if row.multi_line else row.height_ratio
        return n

NUT_LAYOUT_REGISTRY: Dict[str, NutritionLayout] = {
    # ── 澳大利亚 ──
    "AU": NutritionLayout(
        name="Australian 3-Column",
        columns=[
            NutColumn("name",        0.334, "left"),
            NutColumn("per_serving", 0.333, "center"),
            NutColumn("per_100g",    0.333, "center"),
        ],
        header_rows=[
            NutHeaderRow(
                cells=["NUTRITION INFORMATION"],
                bold=True, span_full=True,
                fill_color=None,
                text_color=(0, 0, 0),
                align="center",
                draw_line_below=False,
                height_ratio=1.0,
                font_ratio=1.0,
                font_override="AlibabaPuHuiTi-3-105-Heavy",
                independent_tz=True,                       # 独立压缩占比铺满外框
            ),
            NutHeaderRow(
                cells=["Servings per package: {servings_per_package}"],
                span_full=True, template=True,
                align="left",
                draw_line_below=False,
                height_ratio=0.85,
            ),
            NutHeaderRow(
                cells=["Serving size: {serving_size}"],
                span_full=True, template=True,
                align="left",
                draw_line_below=True,
                line_width_below=0.5,
                height_ratio=0.85,
            ),
            NutHeaderRow(
                cells=["", "Average Quantity\nPer serving", "Average Quantity\nPer 100g"],
                multi_line=True,
                draw_line_below=True,
                line_width_below=0.5,
                col_sep_here=False,
            ),
        ],
        draw_data_row_lines=False,
        draw_col_sep=False,
        col_sep_in_data=False,
        col_padding=1.5,           # 极限压缩左右留白，只要不黏边框即可
        header_line_width=0.5,
        border_line_width=0.5,
        sub_indent=8.0,
    ),

    # ── 俄罗斯 ──
    "RU": NutritionLayout(
        name="Russian 2-Column",
        columns=[NutColumn("name", 0.50, "left"), NutColumn("per_100g", 0.50, "center")],
        header_rows=[
            NutHeaderRow(cells=["Пищевая ценность"], bold=True, span_full=True),
            NutHeaderRow(cells=["100 г продукта содержит"], span_full=True),
        ],
        draw_data_row_lines=True,
        sub_indent=10.0,
    ),

    # ── 智利 ──
    "CL": NutritionLayout(
        name="Chile 3-Column",
        columns=[NutColumn("name", 0.40, "left"), NutColumn("per_100g", 0.30, "center"), NutColumn("vd", 0.30, "center")],
        header_rows=[
            NutHeaderRow(cells=["INFORMACIÓN NUTRICIONAL"], bold=True, span_full=True),
            NutHeaderRow(cells=["Porción: {serving_size}"], template=True, span_full=True),
            NutHeaderRow(cells=["", "Cantidad por\nporción", "%VD*"], multi_line=True),
        ],
        draw_data_row_lines=True,
        sub_indent=10.0,
    ),

    # ── 非洲 (南非) ──
    "ZA": NutritionLayout(
        name="ZA Standard 3-Column",
        columns=[NutColumn("name", 0.48, "left"), NutColumn("per_serving", 0.30, "center"), NutColumn("nrv", 0.22, "center")],
        header_rows=[
            NutHeaderRow(cells=["Nutrition Information"], bold=True, span_full=True, height_ratio=1.4, font_ratio=1.4, independent_tz=True),
            NutHeaderRow(cells=["", "Per serving\n({serving_size})", "NRV%"], template=True, multi_line=True),
        ],
        draw_data_row_lines=True,
        sub_indent=10.0,
        bold_main_items=True,
        data_row_line_width=0.5,
        border_line_width=0.5,
        outer_border_line_width=0.65,
    ),

    # ── 欧盟 (多语言) ──
    "EU_MULTI": NutritionLayout(
        name="EU Multilingual 2-Column",
        columns=[NutColumn("name", 0.78, "left"), NutColumn("per_serving", 0.22, "center")],
        header_rows=[
            NutHeaderRow(cells=["Nutrition declaration / Voedingswaardevermelding / Información nutricional / Nährwertdeklaration / Déclaration nutritionnelle"], bold=True, span_full=True),
            NutHeaderRow(cells=["Nutrition facts per / Voedingswaarde per / Valor nutricional por / Nährwerte pro / Valeur nutritive pour 100mL"], span_full=True),
        ],
        draw_data_row_lines=True,
        col_padding=1.5,
        line_height_ratio=1.1,
        sub_indent=10.0,
        name_mapping={
            'energy': 'Energy / Energie / Valor energético / Energie / Énergie', 
            'fat': 'Fat / Vetten / Grasas / Fett / Matières grasses', 
            'of which': '  of which / waarvan / de las cuales / davon / dont', 
            'saturates': '  -Saturates / Verzadigde vetzuren / Saturadas / gesättigte Fettsäuren / Acides gras saturés', 
            'carbohydrate': 'Carbohydrate / Koolhydraten / Hidratos de carbono / Kohlenhydrate / Glucides', 
            'of which sugars': '  of which / waarvan / de los cuales / davon / dont', 
            'sugars': '  -Sugars / Suikers / Azúcares / Zucker / Sucres', 
            'protein': 'Protein / Eiwitten / Proteínas / Eiweiß / Protéines', 
            'salt': 'Salt / Zout / Sal / Salz / Sel'
        }
    ),

    # ── 新加坡 ──
    "SG": NutritionLayout(
        name="Singapore 3-Column",
        columns=[NutColumn("name", 0.48, "left"), NutColumn("per_serving", 0.30, "center"), NutColumn("per_100g", 0.22, "center")],
        header_rows=[
            NutHeaderRow(cells=["Nutrition Information"], bold=True, span_full=True),
            NutHeaderRow(cells=["Items", "Per serving\n({serving_size})", "NRV%"], template=True, multi_line=True),
        ],
        draw_data_row_lines=True,
        sub_indent=10.0,
    ),

    # ── 马来西亚 ──
    "MY": NutritionLayout(
        name="Malaysia 3-Column",
        columns=[
            NutColumn("name",        0.38, "left"),
            NutColumn("per_100g",    0.31, "center"),
            NutColumn("per_serving", 0.31, "center"),
        ],
        header_rows=[
            NutHeaderRow(
                cells=["NUTRITION INFORMATION"], 
                bold=True, span_full=True, 
                align="center",
                draw_line_below=True,
                height_ratio=1.3, font_ratio=1.4,
                independent_tz=True,
                horizontal_padding=8.0,
            ),
            NutHeaderRow(cells=[""], span_full=True, draw_line_below=False, height_ratio=0.2),
            NutHeaderRow(
                cells=["Serving size: {serving_size}"], 
                bold=True, span_full=True, template=True, 
                align="center",
                draw_line_below=False,
                height_ratio=0.75,
            ),
            NutHeaderRow(
                cells=["Servings per package: {servings_per_package}"], 
                bold=True, span_full=True, template=True, 
                align="center",
                draw_line_below=False,
                height_ratio=0.75,
            ),
            NutHeaderRow(cells=[""], span_full=True, draw_line_below=True, height_ratio=0.15),
            NutHeaderRow(
                cells=["", "Per 100 g", "Per serving ({serving_size})"], 
                template=True, multi_line=False,
                draw_line_below=True,
            ),
        ],
        draw_data_row_lines=True,
        draw_col_sep=True,
        col_sep_in_data=True,
        bold_main_items=True,
        col_padding=2.0,
        sub_indent=10.0,
    ),

    # ── 加拿大 ──
    # 参数来源: nut_auto_parser.py 逆向提取设计师原稿 (base_fs=6.04, base_lw=0.559)
    "CA": NutritionLayout(
        name="Canada 2-Column",
        columns=[
            NutColumn("name",        0.85, "left"),
            NutColumn("nrv",         0.15, "right"),
        ],
        data_row_line_padding=2.0,  # 内部横线统一左右退让
        header_rows=[
            # Row 1: "Nutrition Facts"
            # 原稿: 8.85pt Heavy
            NutHeaderRow(
                cells=["Nutrition Facts"], 
                bold=True, span_full=True, 
                align="left",
                draw_line_below=False,
                height_ratio=1.47, font_ratio=1.47,      # 8.85 / 6.04 = 1.47
                independent_tz=True, horizontal_padding=2.0,
                font_override="AliPuHuiTi-Heavy"
            ),
            # Row 2: "Valeur nutritive"
            # 原稿: 8.85pt Heavy, gap=-1.94 (紧贴)
            NutHeaderRow(
                cells=["Valeur nutritive"], 
                bold=True, span_full=True, 
                align="left",
                draw_line_below=False,
                height_ratio=1.0, font_ratio=1.47,       # 同字号
                margin_top=-2.5,                          # 紧贴上行（原稿 gap=-1.94）
                independent_tz=True, horizontal_padding=2.0,
                font_override="AliPuHuiTi-Heavy"
            ),
            # Row 3: "Per 1 tbsp (18 g)"
            # 原稿: 4.45pt Regular, gap=-1.61 (紧贴上方大字)
            NutHeaderRow(
                cells=["Per {serving_size}"], 
                template=True, span_full=True, 
                align="left",
                draw_line_below=False,
                independent_tz=True, horizontal_padding=2.0,
                font_ratio=0.74,                         # 4.45 / 6.04 = 0.74
                height_ratio=0.74,
                margin_top=-1.0                          # gap=-1.61，补偿大字底部空余
            ),
            # Row 4: 法语 serving size（模板化）
            # 原稿: 4.45pt Regular, gap=-0.41 (紧贴)
            NutHeaderRow(
                cells=["pour {serving_size_fr}"], 
                template=True,
                span_full=True, 
                align="left",
                draw_line_below=True,
                line_width_below=-1.0,
                independent_tz=True, horizontal_padding=2.0,
                font_ratio=0.74,                         # 4.45 / 6.04 = 0.74
                height_ratio=0.74,
                margin_top=-1.5                           # 紧贴 Per 行（原稿 gap=-0.41）
            ),
            # Row 5-6: "Calories 25" | "% Daily Value *\n% valeur quotidienne *"
            # 原稿: Calories=7.96pt Heavy(1.32x), %DV=4.67pt Bold(0.77x)
            # Calories半宽粗线: 1.372pt = 2.45x base_lw
            # gap to Fat: -0.28 (紧贴)
            NutHeaderRow(
                cells=["Calories 25", "% Daily Value *\n% valeur quotidienne *"],
                bold=True, 
                multi_line=True,
                draw_line_below=True,
                line_width_below=-2.5,                   # 1.372/0.559 = 2.45 ≈ 2.5x
                line_left_padding=2.0,
                line_span_col=0,
                valign="ca_header",
                height_ratio=0.75,  
                margin_below=1.5,
                font_ratios=[1.5, 0.77],                # 7.96/6.04, 4.67/6.04
                col_width_ratios=[0.4, 0.6],
                font_override=["AliPuHuiTi-Heavy", "AliPuHuiTi-Bold"]
            ),
        ],
        draw_data_row_lines=True,
        draw_col_sep=False,
        thick_line_width=1.5,
        bold_main_items=False,      # CA 由数据行的 heavy 标记精确控制
        col_padding=2.0,
        sub_indent=10.0,
        line_height_ratio=1.45,
        reference_font_size=8.4,    # 80×70mm标签下的参考字号
    ),

    # ── 新西兰 ──
    "NZ": NutritionLayout(
        name="New Zealand 3-Column",
        columns=[NutColumn("name", 0.48, "left"), NutColumn("per_serving", 0.30, "center"), NutColumn("per_100g", 0.22, "center")],
        header_rows=[
            NutHeaderRow(cells=["NUTRITION INFORMATION"], bold=True, span_full=True),
            NutHeaderRow(cells=["Servings per package: {servings}"], template=True, span_full=True),
            NutHeaderRow(cells=["Serving size: {serving_size}"], template=True, span_full=True),
            NutHeaderRow(cells=["", "Average Quantity\nPer serving", "Average Quantity\nPer 100g"], multi_line=True),
        ],
        draw_data_row_lines=False,
        sub_indent=10.0,
    ),

    # ── 默认兜底 ──
    "DEFAULT": NutritionLayout(
        name="Default 3-Column",
        columns=[NutColumn("name", 0.48, "left"), NutColumn("per_serving", 0.30, "center"), NutColumn("nrv", 0.22, "center")],
        header_rows=[
            NutHeaderRow(cells=["Nutrition Information"], bold=True, span_full=True),
            NutHeaderRow(cells=["Items", "Per serving", "NRV%"], draw_line_below=True),
        ],
        draw_data_row_lines=True,
        sub_indent=10.0,
    ),
}

def get_nut_layout(country_code: str, override_type: Optional[str] = None) -> NutritionLayout:
    if override_type and override_type in NUT_LAYOUT_REGISTRY:
        return NUT_LAYOUT_REGISTRY[override_type]
    
    # 欧洲多语言共享相同的两列式（含多语言表头）设计
    if country_code in ("NL", "FR", "DE", "ES", "EU"):
        return _active_registry.get("EU_MULTI", NUT_LAYOUT_REGISTRY["EU_MULTI"])
        
    return _active_registry.get(country_code, _active_registry.get("DEFAULT", NUT_LAYOUT_REGISTRY["DEFAULT"]))


# ══════════════════════════════════════════════════════════
# Excel 配置优先加载（可选）
# ══════════════════════════════════════════════════════════
import os as _os

_EXCEL_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "nut_config.xlsx")
_active_registry = NUT_LAYOUT_REGISTRY  # 默认使用代码内配置
_data_templates: Dict[str, list] = {}   # 数据行模板（按国家代码索引）

def load_excel_config(force=False):
    """如果 nut_config.xlsx 存在，从中加载配置覆盖代码内默认值"""
    global _active_registry, _data_templates
    if _os.path.exists(_EXCEL_PATH):
        try:
            from nut_config_excel import load_from_excel
            _active_registry, _data_templates = load_from_excel(_EXCEL_PATH)
        except Exception as e:
            print(f"⚠️ Excel 加载失败，使用代码内配置: {e}")
            _active_registry = NUT_LAYOUT_REGISTRY
            _data_templates = {}
    else:
        _active_registry = NUT_LAYOUT_REGISTRY
        _data_templates = {}


def get_data_row_template(country_code: str) -> Optional[list]:
    """获取指定国家的数据行模板（从 Excel 加载），无则返回 None"""
    return _data_templates.get(country_code)


# 启动时自动尝试加载
load_excel_config()
