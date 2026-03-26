import base64
from country_config import get_country_config
from render_pipeline import render_label
from label_renderer import pdf_to_png_base64

PLM_EXAMPLE_3 = {
    "product_name_en": "[EN] 0 MUSHROOM DARK SOY SAUCE / [NL] 0 CHAMPIGNON DONKERE SOJASAUS / [ES] 0 SALSA DE SOYA OSCURA DE SETA DE PAJA / [DE] 0 SOJASAUCE MIT PILZGESCHMACK / [FR] 0 SAUCE DE SOJA AU CHAMPIGNON",
    "product_name_cn": "0草菇老抽",
    "net_weight": "500 mL",
    "ingredients": "**[EN] Ingredients:** Water, **__Soybeans__** (23%), Sugar, Salt, **__Wheat(Gluten)__**(11%), Mushroom Extract(0.002%). / **[NL] Ingrediënten:** Water, **__Sojabonen__** (23%), Suiker, Zout, **__Tarwe(Gluten)__**(11%), Paddenstoelenextract (0.002%). / **[ES] Ingredientes:** Agua, **__Soja__** (23%), Azúcar, Sal, **__Trigo(Gluten)__**(11%), Jugo de Seta de Paja(0.002%). / **[DE] Zutaten:** Wasser, **__Sojabohnen__** (23%), Zucker, Salz, **__Weizen(Gluten)__**(11%), Hefeextrakt (0.002%). / **[FR] Ingrédients:** Eau, **__Soja__** (23%), Sucre, Sel, **__Blé(Gluten)__**(11%), Extrait de Champignon (0.002%).",
    "allergens": "",
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

def main():
    try:
        # Generate PDF
        ai_path = "0-需求文档/1-竖版标签/荷兰0草老/25500015414 500mL荷兰京东国际0草菇老抽小标签(50x120mm) 202510-02.ai"
        pdf_bytes = render_label(ai_path, PLM_EXAMPLE_3, get_country_config("NL"))
        # Convert to PNG Base64
        b64_str = pdf_to_png_base64(pdf_bytes, dpi=216)
        if b64_str.startswith("data:image/png;base64,"):
            b64_str = b64_str.split(",", 1)[1]
        
        # Decode and save to file
        png_data = base64.b64decode(b64_str)
        out_path = "/Users/mulele/.gemini/antigravity/brain/8709239d-2b92-4f83-9229-79c449a52fce/preview_multi.png"
        with open(out_path, "wb") as f:
            f.write(png_data)
        print(f"Successfully created {out_path}")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
