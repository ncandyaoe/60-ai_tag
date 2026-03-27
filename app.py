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
from label_renderer import generate_label_preview_html, generate_label_pdf, pdf_to_png_base64
from template_extractor import extract_template_regions
from render_pipeline import render_label
from flow_layout import FlowRect
from template_store import list_templates, get_template_path

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
# 场景3：竖版标签（50×120mm），多语言 Markdown 测试
PLM_EXAMPLE_3 = {
    "product_name_en": "[EN] 0 MUSHROOM DARK SOY SAUCE / [NL] 0 CHAMPIGNON DONKERE SOJASAUS / [ES] 0 SALSA DE SOYA OSCURA DE SETA DE PAJA / [DE] 0 SOJASAUCE MIT PILZGESCHMACK / [FR] 0 SAUCE DE SOJA AU CHAMPIGNON",
    "product_name_cn": "0草菇老抽",
    "net_weight": "500 mL",
    "ingredients": "[EN] Ingredients: Water, Soybeans (23%), Sugar, Salt, Wheat(Gluten)(11%), Mushroom Extract(0.002%). / [NL] Ingrediënten: Water, Sojabonen (23%), Suiker, Zout, Tarwe(Gluten)(11%), Paddenstoelenextract (0.002%). / [ES] Ingredientes: Agua, Soja (23%), Azúcar, Sal, Trigo(Gluten)(11%), Jugo de Seta de Paja(0.002%). / [DE] Zutaten: Wasser, Sojabohnen (23%), Zucker, Salz, Weizen(Gluten)(11%), Hefeextrakt (0.002%). / [FR] Ingrédients: Eau, Soja (23%), Sucre, Sel, Blé(Gluten)(11%), Extrait de Champignon (0.002%).",
    "allergens": "Soybeans, Wheat(Gluten), Sojabonen, Tarwe(Gluten), Soja, Trigo(Gluten), Sojabohnen, Weizen(Gluten), Blé(Gluten)", 
    "storage": "**Storage / Bewaren / Conservar / Lagerung / Stockage:** Store in a cool, dry place.Please keep in refrigerator after opening and consume as soon as possible. / Bewaren op een koele, droge plaats. Na opening in de koelkast bewaren en zo snel mogelijk consumeren. / Conservar en un lugar fresco y seco. Conservar en el frigorifico una vez abierto y consumir lo antes posible. / Kühl und trocken lagern. Nach dem Öffnen bitte im Kühlschrank aufbewahren und schnellstmöglich verbrauchen. / Conserver dans un endroit frais et sec.Veuillez conserver au réfrigérateur après ouverture et consommer dès que possible.",
    "usage": "", 
    "production_date": "",
    "best_before": "**Best before / Ten minste houdbaar tot / Consumir preferentemente antes del / Mindestens haltbar bis / À consommer de préférence avant le:** See the package / Zie verpakking / Ver envase / Siehe verpackung / Voir emballage (DD/MM/YYYY).",
    "origin": "**Origin:** Product of China / Product uit China / Producto de China / Produkt aus China / Produit de Chine",
    "manufacturer": "", 
    "manufacturer_address": "",
    "importer_info": "**Importer / Importeur / Importador / Importateur:** JINGDONG RETAIL (NETHERLANDS) B.V.\nDa Vincistraat 5, 2652XE, Berkel en Rodenrijs, The Netherlands",
    "target_country": "NL",
    "nutrition": {
        "serving_size": "100mL",
        "nut_title": "Nutrition declaration / Voedingswaardevermelding / Información nutricional / Nährwertdeklaration / Déclaration nutritionnelle",
        "nut_subtitle": "Nutrition facts per / Voedingswaarde per / Valor nutricional por / Nährwerte pro / Valeur nutritive pour 100mL",
        "table_data": [
            {"name": "Energy / Energie / Valor energético / Energie / Énergie", "per_serving": "706 kJ / 167 kcal"},
            {"name": "Fat / Vetten / Grasas / Fett / Matières grasses", "per_serving": "0 g"},
            {"name": "of which / waarvan / de las cuales / davon / dont", "per_serving": "", "is_sub": True},
            {"name": "-Saturates / Verzadigde vetzuren / Saturadas / gesättigte Fettsäuren / Acides gras saturés", "per_serving": "0 g", "is_sub": True},
            {"name": "Carbohydrate / Koolhydraten / Hidratos de carbono / Kohlenhydrate / Glucides", "per_serving": "32 g"},
            {"name": "of which / waarvan / de las cuales / davon / dont", "per_serving": "", "is_sub": True},
            {"name": "-Sugars / Suikers / Azúcares / Zucker / Sucres", "per_serving": "7.1 g", "is_sub": True},
            {"name": "Protein / Eiwitten / Proteínas / Eiweiß / Protéines", "per_serving": "11 g"},
            {"name": "Salt / Zout / Sal / Salz / Sel", "per_serving": "18.8 g"},
        ],
    },
}

# 场景4：竖版标签，耗油（德国，测试中等标题长度+极长配料表）
PLM_EXAMPLE_4 = {
    "product_name_en": "[EN] PREMIUM OYSTER FLAVOURED SAUCE / [NL] PREMIUM OESTERSAUS / [ES] SALSA CON SABOR A OSTRA PREMIUM / [DE] PREMIUM-AUSTERN-SAUCE / [FR] SAUCE SAVEUR HUITRE PREMIUM",
    "product_name_cn": "特级黄豆蚝油",
    "net_weight": "725 g",
    "ingredients": "[EN] Ingredients: Water, Sugar, Oyster Extract(11%) (Oyster, Water, Salt), Salt, Modified Corn Starch, Flavour Enhancer (Monosodium Glutamate), Wheat Flour, Colour (Caramel I). / [NL] Ingrediënten: Water, Suiker, Oesterextract (11%) (Oester, Water, Zout), Zout, Gemodificeerd maïszetmeel, Smaakversterker (Mononatriumglutamaat), Tarwebloem, Kleurstof (Karamel I). / [ES] Ingredientes: Agua, Azúcar, Extracto de ostra (11%) (Ostra, Agua, Sal), Sal, Almidón de maíz modificado, Potenciador del sabor (Glutamato monosódico), Harina de trigo, Colorante (Caramelo I). / [DE] Zutaten: Wasser, Zucker, Austernextrakt (11%) (Auster, Wasser, Salz), Salz, Modifizierte Maisstärke, Geschmacksverstärker (Mononatriumglutamat), Weizenmehl, Farbstoff (Karamell I). / [FR] Ingrédients: Eau, Sucre, Extrait d'huître (11%) (Huître, Eau, Sel), Sel, Amidon de maïs modifié, Exhausteur de goût (Glutamate monosodique), Farine de blé, Colorant (Caramel I).",
    "allergens": "Oyster, Wheat, Oester, Tarwe, Ostra, Trigo, Auster, Weizen, Huître, Blé", 
    "storage": "Store in a cool, dry place. Please keep in refrigerator after opening.",
    "usage": "Use as a dip, marinade or stir-fry sauce.", 
    "production_date": "",
    "best_before": "Best before: See the package (DD/MM/YYYY).",
    "origin": "Product of China",
    "manufacturer": "", 
    "manufacturer_address": "",
    "importer_info": "Importer: EUROPE TRADING CO., LTD.\nMain Street 100, 10115 Berlin, Germany",
    "target_country": "DE",
    "nutrition": {"serving_size": "100g", "table_data": [
        {"name": "Energy", "per_serving": "406 kJ / 95 kcal"},
        {"name": "Fat", "per_serving": "0.1 g"},
        {"name": "  -Saturates", "per_serving": "0 g"},
        {"name": "Carbohydrate", "per_serving": "21.5 g"},
        {"name": "  -Sugars", "per_serving": "18.3 g"},
        {"name": "Protein", "per_serving": "2.1 g"},
        {"name": "Salt", "per_serving": "11.5 g"},
    ]},
}

# 场景5：竖版标签，芝麻油（法国，测试双标题同行+长提示语情况）
PLM_EXAMPLE_5 = {
    "product_name_en": "[EN] 100% PURE ROASTED SESAME OIL / [FR] HUILE DE SÉSAME GRILLÉ 100% PURE",
    "product_name_cn": "100%纯正芝麻香油",
    "net_weight": "250 mL",
    "ingredients": "[EN] Ingredients: 100% Roasted Sesame Seed Oil. / [FR] Ingrédients: Huile de graines de sésame grillées à 100%.",
    "allergens": "Contains Sesame.", 
    "storage": "Keep away from direct sunlight. Cloudiness and/or sediment may naturally occur, this does not affect the quality.",
    "usage": "Add a few drops to soup, salad or noodles just before serving to enhance flavour.", 
    "production_date": "",
    "best_before": "A consommer de preference avant le: Voir emballage.",
    "origin": "Product of China / Produit de Chine",
    "manufacturer": "", 
    "manufacturer_address": "",
    "importer_info": "Importateur: PARIS FOODS S.A.S.\n15 Rue de Rivoli, 75004 Paris, France",
    "target_country": "FR",
    "nutrition": {"serving_size": "100mL", "table_data": [
        {"name": "Energy", "per_serving": "3425 kJ / 833 kcal"},
        {"name": "Fat", "per_serving": "92.5 g"},
        {"name": "  -Saturates", "per_serving": "12.8 g"},
        {"name": "Carbohydrate", "per_serving": "0 g"},
        {"name": "  -Sugars", "per_serving": "0 g"},
        {"name": "Protein", "per_serving": "0 g"},
        {"name": "Salt", "per_serving": "0 g"},
    ]},
}

# 场景6：澳大利亚横版标签（海天上等蚝油，双列营养表 + 长配料表）
PLM_EXAMPLE_6 = {
    "product_name_en": "Haday Superior Oyster Sauce (Classic Version)",
    "product_name_cn": "海天上等蚝油(经典版)",
    "net_weight": "NET: 590 g",
    "drained_weight": "",
    "ingredients": "Water, Oyster Extractives (10%)(Oyster (Mollusc), Water, Salt), Sugar, Salt, Flavour Enhancer (Monosodium Glutamate (621)), Thickener (Hydroxypropyl Distarch Phosphate (1442)), Fructose-Glucose Syrup, Colour (Caramel I (150a)), Wheat Flour, Flavour Enhancer (Disodium 5'-Ribonucleotide (635)), Thickener (Xanthan Gum (415)), Acidity Regulator (Citric Acid (330)), Preservative (Potassium Sorbate (202)).",
    "allergens": "Wheat, Gluten, Mollusc",
    "storage": "Please keep it in a cool and dry place. Tightly close lid after use and keep refrigerated. Serve with stir-fry cooking.",
    "production_date": "See Packing",
    "best_before": "See Packing",
    "origin": "China",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park (No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "MING FA TRADING CO PTY LTD\nSYDNEY: 8 Ormsby Place, Wetherill Park, NSW 2164\nMELBOURNE: 1 Kingston Park Court, Knoxfield, VIC 3180\nBRISBANE: 60 Computer Road, Yatala, QLD 4207\nhotline: 03 97610778  email: support@mingfa.com.au",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "AU",
    "nutrition": {
        "serving_size": "15 g",
        "servings_per_package": "about 39",
        "table_data": [
            {"name": "Energy",              "per_serving": "47 kJ",         "per_100g": "311 kJ"},
            {"name": "Protein",             "per_serving": "Less than 1 g", "per_100g": "3.1 g"},
            {"name": "Fat, total",          "per_serving": "Less than 1 g", "per_100g": "Less than 1 g"},
            {"name": "-saturated",          "per_serving": "Less than 1 g", "per_100g": "Less than 1 g", "is_sub": True},
            {"name": "Carbohydrate",        "per_serving": "2.3 g",         "per_100g": "15.3 g"},
            {"name": "-sugars",             "per_serving": "1.5 g",         "per_100g": "10.0 g", "is_sub": True},
            {"name": "Sodium",              "per_serving": "637 mg",        "per_100g": "4240 mg"},
        ]
    }
}

# 场景7：马来西亚（双列全网格，无缩进子项，Per 100g 居中居左）
PLM_EXAMPLE_7 = {
    "product_name_en": "MUSHROOM VEGETARIAN OYSTER FLAVOURED SAUCE",
    "product_name_cn": "香菇素蚝油",
    "net_weight": "Net Weight: 615 g",
    "drained_weight": "",
    "ingredients": "Water, Brewed Soy Sauce(Water, Soybeans, Salt, Wheat (Gluten)), Sugar, Salt, Flavour Enhancer(Monosodium Glutamate), Modified Starch(Hydroxypropyl Distarch Phosphate), Brewed Vinegar, Shiitake Mushroom (1%), Boletus, King Trumpet Mushroom, Flavour Enhancer(Disodium 5'-Ribonucleotides), Flavour Enhancer(Yeast Extract), Acidity Regulator (Citric Acid).",
    "allergens": "Soybeans, Wheat(Gluten).",
    "storage": "Please keep it in cool and dry place. Tightly close lid after use and keep refrigerated. / Serve with stir-fry cooking.",
    "production_date": "See on package",
    "best_before": "See on package",
    "origin": "Product of China",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park(No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "Imported by: HADAY MALAYSIA SDN. BHD.\nAddress: LOT 5, JALAN CJ 1/7 TAMAN CHERAS JAYA 43200 CHERAS SELANGOR MALAYSIA\nTel: 03-90821838",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "MY",
    "nutrition": {
        "serving_size": "15 g",
        "servings_per_package": "41",
        "table_data": [
            {"name": "Energy",       "per_100g": "105 kcal/442 kJ", "per_serving": "16 kcal/66 kJ"},
            {"name": "Carbohydrate", "per_100g": "21.1 g",          "per_serving": "3.2 g"},
            {"name": "Total sugars", "per_100g": "15.7 g",          "per_serving": "2.4 g"},
            {"name": "Protein",      "per_100g": "4.4 g",           "per_serving": "0.7 g"},
            {"name": "Fat",          "per_100g": "0.2 g",           "per_serving": "0 g"},
            {"name": "Sodium",       "per_100g": "4502 mg",         "per_serving": "675 mg"}
        ]
    }
}

# 场景8：加拿大（双语，特殊粗实线分组，附带脚注）
PLM_EXAMPLE_8 = {
    "product_name_en": "DELICIOUS LIGHT SOY SAUCE",
    "product_name_cn": "鲜味生抽",
    "net_weight": "500 mL",
    "drained_weight": "",
    "ingredients": "Water, Soybeans, Salt, Wheat, Monosodium glutamate, Wheat flour, Glucose-fructose, Caramel, Rice, Disodium 5'-inosinate, Disodium 5'-ribonucleotide, Potassium sorbate, Sucralose.\nContains: Soybeans, Wheat.",
    "allergens": "Eau, Soja, Sel, Blé, Glutamate monosodique, Farine de blé, Fructose-glucose, Couleur caramel, Riz, 5'-inosinate disodique, 5'-ribonucléotide disodique, Sorbate de potassium, Sucralose.\nContient: Soja, Blé.",
    "storage": "Best before: see the package/Meilleur avant: voir emballage\nPlease keep it in cool and dry place. Tightly close lid after use and keep refrigerated.\nVeuillez le conserver dans un endroit frais et sec. Bien fermer le couvercle après utilisation et conserver au réfrigérateur.",
    "production_date": "",
    "best_before": "see the package/voir emballage",
    "origin": "Product of China / Produit de Chine",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park(No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "Imported by / Importé par:\nFive Continents International LTD\nAddress/Adresse:\n1880 Birchmount Road,Scarborough,\nON M1P 2J7",
    "brand_logo": "",
    "is_halal": False,
    "target_country": "CA",
    "nutrition": {
        "serving_size": "1 tbsp (18 g)",
        "servings_per_package": "",
        "table_data": [
            {"name": "Fat / Lipides 1 g", "nrv": "1 %", "heavy": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Saturated / saturées 0 g", "nrv": "0 %", "is_sub": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "+ Trans / trans 0 g", "nrv": "", "is_sub": True, "padded_line_below": True, "height_ratio": 0.8},
            {"name": "Carbohydrate / Glucides 3 g", "nrv": "", "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Fibre / Fibres 1 g", "nrv": "4 %", "is_sub": True, "hide_line_below": True, "height_ratio": 0.8},
            {"name": "Sugars / Sucres 2 g", "nrv": "2 %", "is_sub": True, "padded_line_below": True, "height_ratio": 0.8},
            {"name": "Protein / Protéines 2 g", "nrv": "", "heavy": True, "padded_line_below": True},
            {"name": "Cholesterol / Cholestérol 0 mg", "nrv": "", "heavy": True, "padded_line_below": True},
            {"name": "Sodium 890 mg", "nrv": "39 %", "heavy": True},
            {"name": "Potassium 100 mg", "nrv": "3 %"},
            {"name": "Calcium 10 mg", "nrv": "1 %"},
            {"name": "Iron / Fer 0.2 mg", "nrv": "1 %", "thick_line_below": True},
            {"name": "* 5% or less is a little, 15% or more is a lot", "bold": False, "hide_line_below": True, "height_ratio": 0.8, "margin_top": 2.0},
            {"name": "* 5% ou moins c'est peu, 15% ou plus c'est beaucoup", "bold": False, "hide_line_below": True, "height_ratio": 0.8}
        ]
    }
}

PLM_EXAMPLES = {
    "场景1 - 生抽酱油 (Wonderful Foods 进口)": PLM_EXAMPLE_1,
    "场景2 - 老抽酱油 (广州宝来星 进口)":     PLM_EXAMPLE_2,
    "场景3 - 草菇老抽 (京东国际竖版 荷兰)":   PLM_EXAMPLE_3,
    "场景4 - 特级蚝油 (含过敏原与极长配料 德国)": PLM_EXAMPLE_4,
    "场景5 - 纯正芝麻油 (短配料长提示语 法国)": PLM_EXAMPLE_5,
    "场景6 - 上等蚝油 (海天经典版 澳大利亚)": PLM_EXAMPLE_6,
    "场景7 - 香菇素蚝油 (全网格表头 马来西亚)": PLM_EXAMPLE_7,
    "场景8 - 鲜味生抽 (特殊分组表头 加拿大)": PLM_EXAMPLE_8,
}

# --------------------------------------------------
# UI
# --------------------------------------------------
st.title("🏷️ 智能合规标签排版系统")

def _build_template_options():
    """动态构建模板选项：内置 + 用户上传。"""
    opts = {
        "默认 3×3 方形标签 (70×69mm)": ("classic", None),
        "竖版标签 (50×120mm)": ("vertical_50_120", None)
    }
    for tmpl in list_templates():
        label = f"{tmpl['name']} ({tmpl['dimensions_mm']})"
        opts[label] = ("ai_template", tmpl["id"])
    return opts

TEMPLATE_OPTIONS = _build_template_options()

selected_template_name = st.selectbox("📏 选择物理标签模板：", list(TEMPLATE_OPTIONS.keys()))
selected_template_type, selected_template_id = TEMPLATE_OPTIONS[selected_template_name]
show_regions = st.checkbox("🔍 显示区域图层", value=False, help="叠加彩色区域边界，用于调试布局")

st.markdown("---")

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
            # 强行覆盖目标国家，使用户在下拉框的选择优先级高于单纯的文本拷贝
            structured_data["target_country"] = selected_country_code
            st.session_state['label_data'] = structured_data
            st.session_state['country_code'] = selected_country_code
            st.success("JSON 解析成功，标签已生成！")
        except json.JSONDecodeError as e:
            st.error(f"JSON 格式错误，请检查输入：{e}")

with col2:
    if selected_template_type == "classic":
        st.subheader("2. 物理 PDF 合规排版 (3×3 · 76mm)")
    else:
        st.subheader(f"2. 物理 PDF 合规排版 ({selected_template_name})")

    if 'label_data' in st.session_state:
        cc = st.session_state.get('country_code', 'DEFAULT')
        data = st.session_state['label_data']

        if selected_template_type == "classic":
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
            # 使用新引擎渲染经典标签（标题与内容完全解耦）
            # --------------------------------------------------
            from template_classic import build_classic_config
            country_cfg = get_country_config(cc)

            # 构建经典模板（标题与内容已完全解耦，单次构建即可）
            classic_tmpl = build_classic_config(data)
            pdf_bytes = render_label(classic_tmpl, data, country_cfg, show_regions=show_regions)

            # PNG 预览
            png_b64 = pdf_to_png_base64(pdf_bytes, dpi=216)
            preview_html = f"""<!DOCTYPE html>
            <html><head><meta charset="UTF-8"><style>
            body {{ margin:0; padding:0; background:#4a4a4a; display:flex; flex-direction:column; align-items:center; min-height:100vh; }}
            img {{ max-width:100%; background:white; box-shadow:0 4px 16px rgba(0,0,0,0.5); margin:16px; }}
            </style></head><body>
            <img src="data:image/png;base64,{png_b64}" alt="Label Preview" />
            </body></html>"""
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

        elif selected_template_type == "vertical_50_120":
            st.info("💡 竖版标签使用智能自适应布局引擎，内置防溢出规则并自动保证合规字高（无需额外校验检查）。")
            country_cfg = get_country_config(cc)
            
            # 使用固定提供给设计师的那个竖版老抽模板
            ai_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "0-需求文档", "1-竖版标签", "荷兰0草老", "多环保标-25500015414 500mL荷兰京东国际0草菇老抽小标签(50x120mm) 202510-02.ai")
            
            if not os.path.exists(ai_path):
                st.error(f"找不到内置模板文件：{ai_path}")
            else:
                cfg_tmpl = extract_template_regions(ai_path)
                # 取消固定模板赋值，交由内部自适应布局判断
                # cfg_tmpl.nut_table_type = "multilingual_2col"
                cfg_tmpl.content_type = "multilingual"         # 内置示例固定为多语言单表
                pdf_bytes = render_label(cfg_tmpl, data, country_cfg, show_regions=show_regions)

                # 生成 PNG 预览 (高分屏 216 DPI)
                png_b64 = pdf_to_png_base64(pdf_bytes, dpi=216)

                preview_html = f"""<!DOCTYPE html>
                <html><head><meta charset="UTF-8"><style>
                body {{ margin:0; padding:0; background:#4a4a4a; display:flex; justify-content:center; padding: 20px; }}
                img {{ max-width:100%; box-shadow:0 4px 16px rgba(0,0,0,0.5); }}
                </style></head><body>
                <img src="data:image/png;base64,{png_b64}" alt="Label Preview" />
                </body></html>"""

                st.components.v1.html(preview_html, height=800, scrolling=True)

                filename = (data.get('product_name_en', 'label').replace(' ', '_') + '_Vertical.pdf')
                st.download_button(
                    label="⬇ 下载 PDF（送厂印刷）",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                    type="primary",
                )

        elif selected_template_type == "ai_template" and selected_template_id:
            st.info("💡 自定义模板使用智能自适应布局引擎，内置防溢出规则并自动保证合规字高。")
            country_cfg = get_country_config(cc)

            ai_path = get_template_path(selected_template_id)
            if not ai_path or not os.path.exists(ai_path):
                st.error("找不到模板文件，可能已被删除。请到 📐 模板管理 页面重新上传。")
            else:
                cfg_tmpl = extract_template_regions(ai_path)
                from template_store import get_template
                t_info = get_template(selected_template_id)
                if t_info:
                    cfg_tmpl.nut_table_type = t_info.get("nut_table_type", "standard_3col")
                    cfg_tmpl.content_type = t_info.get("content_type", "standard_single")
                pdf_bytes = render_label(cfg_tmpl, data, country_cfg, show_regions=show_regions)

                # 生成 PNG 预览 (高分屏 216 DPI)
                png_b64 = pdf_to_png_base64(pdf_bytes, dpi=216)

                preview_html = f"""<!DOCTYPE html>
                <html><head><meta charset="UTF-8"><style>
                body {{ margin:0; padding:0; background:#4a4a4a; display:flex; justify-content:center; padding: 20px; }}
                img {{ max-width:100%; box-shadow:0 4px 16px rgba(0,0,0,0.5); }}
                </style></head><body>
                <img src="data:image/png;base64,{png_b64}" alt="Label Preview" />
                </body></html>"""

                st.components.v1.html(preview_html, height=800, scrolling=True)

                filename = (data.get('product_name_en', 'label').replace(' ', '_') + '_Custom.pdf')
                st.download_button(
                    label="⬇ 下载 PDF（送厂印刷）",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                    type="primary",
                )

        with st.expander("查看当前 JSON 结构"):
            st.json(data)
    else:
        st.info("请在左侧选择或粘贴 PLM JSON 数据，点击「生成标签」查看排版预览。")
