import json
import os

import streamlit as st
from jinja2 import Environment, FileSystemLoader, select_autoescape

from country_config import (
    get_country_config,
    get_country_choices,
    validate_font_compliance,
    COUNTRY_REGISTRY,
)
from label_renderer import generate_label_preview_html, generate_label_pdf
from template_config import list_templates, get_template

# ==========================================
# Jinja2 模板环境：加载 templates/ 目录下的模板
# ==========================================
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_JINJA_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape([])  # 关闭 HTML 自动转义，JS 数据由 json.dumps 保证安全
)

st.set_page_config(page_title="智能出口标签生成器 (含营养表)", layout="wide")

# ==========================================
# 3. 核心逻辑：Jinja2 模板渲染
# ==========================================

# 最大营养表行数（合规上限）
MAX_NUTRITION_ROWS = 6

# --------------------------------------------------
# 动态字号计算（与 label.html 中的 JS 保持同步）
# 用于 Python 侧的合规校验
# --------------------------------------------------
def calc_min_font_size_pt(data: dict, country_code: str = "AU") -> float:
    """调用 label_renderer 的自适应算法获取实际最小字号。"""
    from label_renderer import _calc_font_sizes
    from country_config import get_country_config
    cfg = get_country_config(country_code)
    sizes, h_scale, _ = _calc_font_sizes(data, cfg)
    return min(sizes["ingr"], sizes["body"])


def generate_label_html(data: dict, country_code: str = "DEFAULT") -> tuple[str, dict]:
    """
    使用 Jinja2 渲染标签 HTML，并进行字高合规校验。

    Args:
        data:         PLM 结构化产品数据
        country_code: 目的国代码（如 "US", "AU", "CA"）

    Returns:
        (html_string, compliance_result)
    """
    # 预处理：营养表行数截断
    nutrition = data.get("nutrition") or {}
    table_data = nutrition.get("table_data") or []
    if len(table_data) > MAX_NUTRITION_ROWS:
        nutrition = {**nutrition, "table_data": table_data[:MAX_NUTRITION_ROWS]}
        data = {**data, "nutrition": nutrition}

    # --------------------------------------------------
    # [改造项3] 字高合规校验
    # --------------------------------------------------
    min_font_pt = calc_min_font_size_pt(data, country_code)
    compliance = validate_font_compliance(min_font_pt, country_code)

    # --------------------------------------------------
    # [改造项4] 目的国配置
    # --------------------------------------------------
    country_cfg = get_country_config(country_code)

    # --------------------------------------------------
    # 字体配置：优先阿里巴巴普惠体（需放入 static/ 目录），
    # 否则降级到 PDFMake 内置 Roboto（秒开，不支持中文）
    # --------------------------------------------------
    _STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    alibaba_regular = "AlibabaPuHuiTi-Regular.ttf"
    alibaba_bold    = "AlibabaPuHuiTi-Bold.ttf"
    has_alibaba = (
        os.path.exists(os.path.join(_STATIC_DIR, alibaba_regular)) and
        os.path.getsize(os.path.join(_STATIC_DIR, alibaba_regular)) > 100_000
    )

    if has_alibaba:
        font_config = {
            "family": "AlibabaPuHuiTi",
            "files":  [alibaba_regular, alibaba_bold],
            "variants": {
                "normal":      alibaba_regular,
                "bold":        alibaba_bold,
                "italics":     alibaba_regular,
                "bolditalics": alibaba_bold,
            }
        }
    else:
        # 降级到 NISC18030（GB18030 中文字体，6.8MB，加载几秒）
        fallback = "NISC18030.ttf"
        font_config = {
            "family": "NISC18030",
            "files":  [fallback],
            "variants": {
                "normal":      fallback,
                "bold":        fallback,
                "italics":     fallback,
                "bolditalics": fallback,
            }
        }

    template = _JINJA_ENV.get_template("label.html")
    html = template.render(
        label_data_json=json.dumps(data, ensure_ascii=False),
        font_config_json=json.dumps(font_config, ensure_ascii=False),
        country_config_json=json.dumps(country_cfg, ensure_ascii=False),
        compliance_json=json.dumps(compliance, ensure_ascii=False),
    )
    return html, compliance

# ==========================================
# 4. Streamlit 网页前端界面
# ==========================================

# --------------------------------------------------
# PLM 示例数据（来自小标签AI生成场景.xlsx）
# --------------------------------------------------

# 场景1：同一标签，不同经销商（生抽酱油 - Wonderful Foods）
PLM_EXAMPLE_1 = {
    "product_name_en": "Light Soy Sauce (Classic Version)",
    "product_name_cn": "生抽酱油(经典版)",
    "net_weight": "Net Volume: 1.9 L",
    "drained_weight": "",
    "ingredients": "Water, Soybeans, Salt, Wheat(Gluten), Flavour Enhancer(Monosodium Glutamate), Wheat Flour(Gluten), Fructose-glucose Syrup, Colour(Caramel I), Flavour Enhancer(Disodium 5'-Ribonucleotide), Flavour Enhancer(Disodium 5'-Inosinate), Preservative(Potassium Sorbate).",
    "allergens": "Soybeans, Wheat(Gluten)",
    "storage": "Please keep it in a cool and dry place. Tightly close lid after use and keep refrigerated.",
    "production_date": "See The Package",
    "best_before": "See The Package",
    "origin": "China",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park (No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "Wonderful Food Co. Ltd.",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "AU",
    "nutrition": {
        "serving_size": "15 mL",
        "servings_per_package": "",
        "table_data": [
            {"name": "Energy",              "per_serving": "25 kJ",    "nrv": "0%"},
            {"name": "Protein",             "per_serving": "0.8 g",    "nrv": "2%"},
            {"name": "Carbohydrate",        "per_serving": "0.6 g",    "nrv": "0%"},
            {"name": "of which total sugars","per_serving": "0 g",     "nrv": "",   "is_sub": True},
            {"name": "Total fat",           "per_serving": "0 g",      "nrv": "0%"},
            {"name": "of which saturated fat","per_serving": "0 g",    "nrv": "0%", "is_sub": True},
            {"name": "Sodium",              "per_serving": "1072 mg",  "nrv": "54%"}
        ]
    }
}

# 场景2：同一标签，老抽酱油（不同产品，营养成分不同）
PLM_EXAMPLE_2 = {
    "product_name_en": "Dark Soy Sauce(Classic Version)",
    "product_name_cn": "老抽酱油(经典版)",
    "net_weight": "Net Volume: 1.9 L",
    "drained_weight": "",
    "ingredients": "Water, Soybeans, Salt, Colour(Caramel I, Caramel IV), Wheat(Gluten), Flavour Enhancer(Monosodium Glutamate), Wheat Flour(Gluten), Flavour Enhancers(Disodium 5'-Ribonucleotide, Disodium 5'-Inosinate), Preservative(Potassium Sorbate).",
    "allergens": "Soybeans, Wheat (Gluten), Sulfite.",
    "storage": "Please keep it in a cool and dry place. Tightly close lid after use and keep refrigerated.",
    "production_date": "See The Package",
    "best_before": "See The Package",
    "origin": "China",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park (No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "GUANGZHOUBAOLAIXING",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "AU",
    "nutrition": {
        "serving_size": "15 mL",
        "servings_per_package": "",
        "table_data": [
            {"name": "Energy",              "per_serving": "56 kJ",    "nrv": "0.01%"},
            {"name": "Protein",             "per_serving": "0.8 g",    "nrv": "2%"},
            {"name": "Carbohydrate",        "per_serving": "2.5 g",    "nrv": "0.01%"},
            {"name": "of which total sugars","per_serving": "0 g",     "nrv": "",      "is_sub": True},
            {"name": "Total fat",           "per_serving": "0 g",      "nrv": "0%"},
            {"name": "of which saturated fat","per_serving": "0 g",    "nrv": "0%",    "is_sub": True},
            {"name": "Sodium",              "per_serving": "1095 mg",  "nrv": "55%"}
        ]
    }
}

PLM_EXAMPLES = {
    "场景1 - 生抽酱油 (Wonderful Foods 进口)": PLM_EXAMPLE_1,
    "场景2 - 老抽酱油 (广州宝来星 进口)":     PLM_EXAMPLE_2,
}

# 场景3：荷兰京东国际 草菇老抽（50×120mm，5 语言）
PLM_EXAMPLE_NL = {
    "product_name_en": "【EN】0 MUSHROOM DARK SOY SAUCE /  【NL】0 CHAMPIGNON DONKERE SOJASAUS /  【ES】0 SALSA DE SOYA OSCURA DE SETA DE PAJA /  【DE】0 SOJASAUCE MIT PILZGESCHMACK /  【FR】0 SAUCE DE SOJA AU CHAMPIGNON\n\n0草菇老抽",
    "product_name_cn": "",
    "net_weight": "500mL",
    "ingredients": "[EN] Ingredients: Water, <u>Soybeans</u> (23%), Sugar, Salt, <u>Wheat(Gluten)</u>(11%), Mushroom Extract(0.002%). / [NL] Ingrediënten: Water, <u>Sojabonen</u> (23%), Suiker, Zout, <u>Tarwe(Gluten)</u>(11%), Paddenstoelenextract (0.002%). / [ES]Ingredientes: Agua, <u>Soja</u> (23%), Azúcar, Sal, <u>Trigo(Gluten)</u>(11%), Jugo de Seta de Paja(0.002%). / [DE] Zutaten: Wasser, <u>Sojabohnen</u> (23%), Zucker, Salz, <u>Weizen(Gluten)</u>(11%), Hefeextrakt (0.002%). / [FR] Ingrédients:Eau, <u>Soja</u> (23%), Sucre, Sel, <u>Blé(Gluten)</u>(11%), Extrait de Champignon (0.002%).",
    "allergens": "",
    "storage": "Store in a cool, dry place.Please keep in refrigerator after opening and consume as soon as possible. / Bewaren op een koele, droge plaats. Na opening in de koelkast bewaren en zo snel mogelijk consumeren. / Conservar en un lugar fresco y seco. Conservar en el frigorífico una vez abierto y consumir lo antes posible. / Kühl und trocken lagern. Nach dem Öffnen bitte im Kühlschrank aufbewahren und schnellstmöglich verbrauchen. / Conserver dans un endroit frais et sec.Veuillez conserver au réfrigérateur après ouverture et consommer dès que possible.",
    "usage": "Good for dipping, cold-mixing, stir-frying and braising for coloring./ Goed voor onderdompelen, koud mengen, roerbakken en te smoren voor kleur./ Servir para mojar, mezclar en frío, saltear y estofar para dar color. / Gut zum Dippen, Kaltmischen, Braten und Schmoren zum Färben./ Bon pour tremper, mélanger à froid, faire sauter et les plats mijotés pour ajouter de la couleur.",
    "production_date": "",
    "best_before": "Best before / Ten minste houdbaar tot / Consumir preferentemente antes del / Mindestens haltbar bis / Á consommer de préférence avant le: See the package / Zie verpakking / Ver envase / Siehe verpackung / Voir emballage (DD/MM/YYYY).",
    "product_of": "Product of China / Product uit China / Producto de China / Produkt aus China / Produit de Chine",
    "origin": "China",
    "manufacturer": "",
    "manufacturer_address": "",
    "importer_info": "JINGDONG RETAIL (NETHERLANDS) B.V.",
    "importer_address": "Da Vincistraat 5, 2652XE, Berkel en Rodenrijs, The Netherlands",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "NL",
    "nutrition": {
        "serving_size": "100mL",
        "title": "Nutrition declaration / Voedingswaardevermelding / Información nutricional / Nährwertdeklaration / Déclaration nutritionnelle",
        "per_label": "Nutrition facts per / Voedingswaarde per / Valor nutricional por / Nährwerte pro / Valeur nutritive pour  100mL",
        "table_data": [
            {"name": "Energy / Energie / Valor energético / Energie / Énergie", "per_serving": "706 kJ / 167 kcal"},
            {"name": "Fat / Vetten / Grasas / Fett / Matières grasses", "per_serving": "0 g"},
            {"name": "of which / waarvan / de las cuales / davon / dont", "per_serving": "", "is_sub": True},
            {"name": "-Saturates / Verzadigde vetzuren / Saturadas / gesättigte Fettsäuren / Acides gras saturés", "per_serving": "0 g", "is_sub": True},
            {"name": "Carbohydrate / Koolhydraten / Hidratos de carbono / Kohlenhydrate / Glucides", "per_serving": "32 g"},
            {"name": "of which / waarvan / de los cuales / davon / dont", "per_serving": "", "is_sub": True},
            {"name": "-Sugars / Suikers / Azúcares / Zucker / Sucres", "per_serving": "7.1 g", "is_sub": True},
            {"name": "Protein / Eiwitten / Proteínas / Eiweiß / Protéines", "per_serving": "11 g"},
            {"name": "Salt / Zout / Sal / Salz / Sel", "per_serving": "18.8 g"}
        ]
    }
}

PLM_EXAMPLES["场景3 - 荷兰草菇老抽 (京东国际 50×120mm)"] = PLM_EXAMPLE_NL

# --------------------------------------------------
# UI
# --------------------------------------------------
st.title("🏷️ 小标签生成系统 · PLM 直连版 (3×3 方形标签)")

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. PLM 产品数据 (JSON)")

    # 示例选择器
    example_key = st.selectbox("选择 PLM 示例场景：", list(PLM_EXAMPLES.keys()))

    # 目的国选择器
    country_choices = get_country_choices()
    country_labels = [label for _, label in country_choices]
    country_codes  = [code  for code, _ in country_choices]
    # 从当前示例 JSON 的 target_country 推断默认选中项
    _default_cc = PLM_EXAMPLES[example_key].get("target_country", "DEFAULT")
    _default_idx = country_codes.index(_default_cc) if _default_cc in country_codes else len(country_codes) - 1
    selected_country_label = st.selectbox(
        "🌍 目的国（驱动合规校验 & 条件渲染）：",
        country_labels,
        index=_default_idx,
    )
    selected_country_code = country_codes[country_labels.index(selected_country_label)]

    # 模板选择器
    tpl_dict = list_templates()
    tpl_ids = list(tpl_dict.keys())
    tpl_names = list(tpl_dict.values())
    selected_tpl_name = st.selectbox(
        "📐 标签模板（尺寸 & 布局）：",
        tpl_names,
        index=0,
    )
    selected_tpl_id = tpl_ids[tpl_names.index(selected_tpl_name)]

    # JSON 编辑器
    json_input = st.text_area(
        "PLM JSON 数据（可直接粘贴或编辑）",
        value=json.dumps(PLM_EXAMPLES[example_key], ensure_ascii=False, indent=2),
        height=340,
        help="将 PLM 系统导出的 JSON 粘贴到此处，点击「生成标签」即可渲染。"
    )

    if st.button("✅ 生成标签", type="primary"):
        try:
            structured_data = json.loads(json_input)
            st.session_state['label_data'] = structured_data
            st.session_state['country_code'] = selected_country_code
            st.session_state['template_id'] = selected_tpl_id
            st.success("JSON 解析成功，标签已生成！")
        except json.JSONDecodeError as e:
            st.error(f"JSON 格式错误，请检查输入：{e}")

with col2:
    st.subheader("2. 物理 PDF 合规排版 (3×3 · 76mm)")
    if 'label_data' in st.session_state:
        cc = st.session_state.get('country_code', 'DEFAULT')
        data = st.session_state['label_data']

        # --------------------------------------------------
        # [改造项3] 合规校验
        # --------------------------------------------------
        from country_config import validate_font_compliance
        min_font_pt = calc_min_font_size_pt(data, cc)
        compliance = validate_font_compliance(min_font_pt, cc)

        if compliance["level"] == "fail":
            st.error(compliance["message"])
        elif compliance["level"] == "warn":
            st.warning(compliance["message"])
        else:
            st.success(compliance["message"])

        # --------------------------------------------------
        # 服务端生成 PDF + PNG 预览
        # --------------------------------------------------
        country_cfg = get_country_config(cc)
        tpl_id = st.session_state.get('template_id', 'au_70x69')
        tpl = get_template(tpl_id)
        preview_html, pdf_bytes = generate_label_preview_html(data, country_cfg, tpl=tpl)
        st.components.v1.html(preview_html, height=600, scrolling=True)

        # 下载按钮
        if compliance["level"] != "fail":
            filename = (data.get('product_name_en', 'label').replace(' ', '_') + '_Compliance.pdf')
            st.download_button(
                label="⬇ 下载 PDF（送厂印刷）",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
                type="primary",
            )
        else:
            st.button("⬇ 下载 PDF（送厂印刷）", disabled=True, help="字高不合规，禁止下载")

        with st.expander("查看当前 JSON 结构"):
            st.json(data)
    else:
        st.info("请在左侧选择或粘贴 PLM JSON 数据，点击「生成标签」查看排版预览。")