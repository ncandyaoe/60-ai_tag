"""
国家配置注册表 & 字高合规校验

每个出口国定义：
- min_font_height_mm: 法规要求的最低字符物理高度 (mm)
- nutrition_title:    营养成分表标题文案
- nutrition_header:   营养表列头 [空, 第二列, 第三列]
- icons:              需渲染的图标标识 (如 halal, eac)
- eco_icons:          环保标图标文件名列表 (从 static/eco_icons/ 加载)
- show_magnifier:     是否在营养表旁显示放大镜图标 (加拿大要求)
- lang:               标签主语言标识
"""

# EU 通用环保标组合
_EU_ECO_ICONS = ["es_reciclaje.png", "fr_triman.png"]

# --------------------------------------------------
# 国家注册表
# --------------------------------------------------
COUNTRY_REGISTRY = {
    "CL": {
        "name": "智利",
        "lang": "es",
        "min_font_height_mm": 2.0,    # 最严格
        "nutrition_title": "Información Nutricional",
        "nutrition_header": ["", "Por 100g", "Por Porción"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "ZA": {
        "name": "非洲 (南非标准)",
        "lang": "en",
        "min_font_height_mm": 1.6,
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per serving", "NRV%"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "CA": {
        "name": "加拿大",  # 加拿大含英法双语，但属于特殊单行排版，后续可能单独调整，目前归为 False
        "lang": "en-fr",
        "min_font_height_mm": 1.6,
        "nutrition_title": "Nutrition Facts / Valeur nutritive",
        "nutrition_header": ["", "Amount", "% Daily Value"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": True,
        "is_multilingual": False,
    },
    "AU": {
        "name": "澳大利亚",
        "lang": "en",
        "min_font_height_mm": 1.8,    # 亚太原则
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "NZ": {
        "name": "新西兰",
        "lang": "en",
        "min_font_height_mm": 1.8,    # 亚太原则
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "SG": {
        "name": "新加坡",
        "lang": "en",
        "min_font_height_mm": 1.5,
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "TH": {
        "name": "泰国",
        "lang": "th-en",
        "min_font_height_mm": 1.5,
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "MY": {
        "name": "马来西亚",
        "lang": "ms-en",
        "min_font_height_mm": 1.5,
        "nutrition_title": "Nutrition Information",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": ["halal"],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    "RU": {
        "name": "俄罗斯",
        "lang": "ru",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Пищевая ценность",
        "nutrition_header": ["", "На 100г", "На порцию"],
        "icons": ["eac"],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
    # ── EU 国家 ──
    "NL": {
        "name": "荷兰",
        "lang": "nl-en",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Nutrition Declaration",
        "nutrition_header": ["", "Per 100mL", "NRV%"],
        "icons": [],
        "eco_icons": _EU_ECO_ICONS,
        "show_magnifier": False,
        "is_multilingual": True,
    },
    "FR": {
        "name": "法国",
        "lang": "fr",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Déclaration nutritionnelle",
        "nutrition_header": ["", "Pour 100mL", "% AR"],
        "icons": [],
        "eco_icons": _EU_ECO_ICONS,
        "show_magnifier": False,
        "is_multilingual": True,
    },
    "ES": {
        "name": "西班牙",
        "lang": "es",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Información Nutricional",
        "nutrition_header": ["", "Por 100mL", "% VRN"],
        "icons": [],
        "eco_icons": _EU_ECO_ICONS,
        "show_magnifier": False,
        "is_multilingual": True,
    },
    "DE": {
        "name": "德国",
        "lang": "de",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Nährwertdeklaration",
        "nutrition_header": ["", "Pro 100mL", "% NRV"],
        "icons": [],
        "eco_icons": _EU_ECO_ICONS,
        "show_magnifier": False,
        "is_multilingual": True,
    },
    "EU": {
        "name": "欧盟（通用）",
        "lang": "en",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Nutrition Declaration",
        "nutrition_header": ["", "Per 100mL", "NRV%"],
        "icons": [],
        "eco_icons": _EU_ECO_ICONS,
        "show_magnifier": False,
        "is_multilingual": True,
    },
    "DEFAULT": {
        "name": "默认（通用出口）",
        "lang": "en",
        "min_font_height_mm": 1.2,
        "nutrition_title": "Nutrition Facts",
        "nutrition_header": ["", "Per 100g", "Per Serving"],
        "icons": [],
        "eco_icons": [],
        "show_magnifier": False,
        "is_multilingual": False,
    },
}


def get_country_config(country_code: str) -> dict:
    """获取国家配置，未找到则返回 DEFAULT。"""
    return COUNTRY_REGISTRY.get(country_code, COUNTRY_REGISTRY["DEFAULT"])


def get_country_choices() -> list[tuple[str, str]]:
    """返回 (code, display_name) 列表，用于前端下拉框。"""
    return [
        (code, f"{cfg['name']} ({code})")
        for code, cfg in COUNTRY_REGISTRY.items()
        if code != "DEFAULT"
    ] + [("DEFAULT", "默认（通用出口）")]


# --------------------------------------------------
# 字高合规校验
# --------------------------------------------------
# 换算常量: 1pt = 1/72 inch = 25.4/72 mm ≈ 0.3528 mm
PT_TO_MM = 25.4 / 72.0


def validate_font_compliance(
    font_size_pt: float,
    country_code: str = "DEFAULT",
) -> dict:
    """
    校验指定字号是否满足目的国法规的最低字高要求。

    Args:
        font_size_pt: 当前最小字号 (pt)
        country_code: 出口国代号

    Returns:
        dict with keys:
            - ok:       bool, 是否合规
            - actual_mm: float, 当前字号对应的物理高度 (mm)
            - min_mm:   float, 法规要求的最低高度 (mm)
            - level:    "pass" | "warn" | "fail"
            - message:  str, 人类可读信息
    """
    cfg = get_country_config(country_code)
    min_mm = cfg["min_font_height_mm"]
    # 实际 x-height 高度 = font_pt × PT_TO_MM × x_height_ratio
    _X_HEIGHT_RATIO = 0.54  # AliPuHuiTi sxHeight/UPM
    actual_mm = round(font_size_pt * PT_TO_MM * _X_HEIGHT_RATIO, 2)

    # 临界警告线：高出最低线 20% 以内视为 warn
    warn_threshold = min_mm * 1.2

    if actual_mm < min_mm:
        return {
            "ok": False,
            "actual_mm": actual_mm,
            "min_mm": min_mm,
            "level": "fail",
            "message": (
                f"❌ 不合规：当前最小字号 {font_size_pt}pt "
                f"(物理高度 {actual_mm}mm) 低于 "
                f"{cfg['name']} 法规要求的最低 {min_mm}mm"
            ),
        }
    elif actual_mm < warn_threshold:
        return {
            "ok": True,
            "actual_mm": actual_mm,
            "min_mm": min_mm,
            "level": "warn",
            "message": (
                f"⚠️ 临界：当前最小字号 {font_size_pt}pt "
                f"(物理高度 {actual_mm}mm) 接近 "
                f"{cfg['name']} 法规底线 {min_mm}mm，建议留余量"
            ),
        }
    else:
        return {
            "ok": True,
            "actual_mm": actual_mm,
            "min_mm": min_mm,
            "level": "pass",
            "message": (
                f"✅ 合规：字号 {font_size_pt}pt "
                f"(物理高度 {actual_mm}mm) ≥ "
                f"{cfg['name']} 最低要求 {min_mm}mm"
            ),
        }
