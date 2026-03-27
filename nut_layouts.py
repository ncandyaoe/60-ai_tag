"""
营养表数据模型与各国布局配置

定义了各国营养表的列结构、标题区属性、行高、线边框以及多语言等详细版式信息。
通过实现这一层，render_nutrition() 可以变成一个纯渲染循环，完全无需包含 if/elif 分支。
"""
from typing import List, Dict, Optional, Union
from dataclasses import dataclass

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
class NutritionLayout:
    name: str                                
    columns: List[NutColumn]                 
    header_rows: List[NutHeaderRow]          
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
    "CA": NutritionLayout(
        name="Canada 2-Column",
        columns=[
            NutColumn("name",        0.75, "left"),
            NutColumn("nrv",         0.25, "right"),
        ],
        data_row_line_padding=2.0,  # 内部横线统一左右退让
        header_rows=[
            NutHeaderRow(
                cells=["Nutrition Facts\nValeur nutritive"], 
                bold=True, span_full=True, 
                align="left",
                draw_line_below=False,
                multi_line=True,
                height_ratio=1.7, font_ratio=2.0,
                independent_tz=True, horizontal_padding=2.0,
                font_override="AliPuHuiTi-Heavy"
            ),
            NutHeaderRow(
                cells=["Per {serving_size}"], 
                template=True, span_full=True, 
                align="left",
                draw_line_below=False,
                independent_tz=True, horizontal_padding=2.0,
                height_ratio=0.8,
                margin_top=-2.9
            ),
            # 硬编码法语 serving_size 仅供展示用，后续可替换为占位符
            NutHeaderRow(
                cells=["pour 1 cuillère à soupe (18 g)"], 
                span_full=True, 
                align="left",
                draw_line_below=True,
                line_width_below=-1.0,  # 使用标准细线（不加粗）
                line_padding=4.0,       # 左右不顶到线，留出悬空内缩
                independent_tz=True, horizontal_padding=2.0,
                height_ratio=0.8,
            ),
            NutHeaderRow(
                cells=["Calories 25", "% Daily Value *\n% valeur quotidienne *"],
                bold=True, 
                multi_line=True,
                draw_line_below=True,
                line_width_below=-3.0, # 负数代表外框线宽的倍数 (此处为3倍)
                line_left_padding=2.0, # 左侧单独向内退让 2.0，不挤满边框
                line_span_col=0,
                valign="ca_header",
                height_ratio=0.75,  
                margin_below=0.0,
                font_ratios=[1.1, 0.6],
                col_width_ratios=[0.4, 0.6],
                font_override=["AliPuHuiTi-Heavy", "AliPuHuiTi-Bold"]
            ),
        ],
        draw_data_row_lines=True,
        draw_col_sep=False,        # CA不画竖线
        thick_line_width=1.5,      # 加粗分割线宽度
        bold_main_items=True,
        col_padding=2.0,
        sub_indent=10.0,
        line_height_ratio=1.45,    # 增加行内垂直内边框留白，拉开文字与横线的呼吸空间
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
        return NUT_LAYOUT_REGISTRY["EU_MULTI"]
        
    return NUT_LAYOUT_REGISTRY.get(country_code, NUT_LAYOUT_REGISTRY["DEFAULT"])
