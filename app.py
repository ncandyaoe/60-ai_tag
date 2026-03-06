import re
import json
import os

import requests
import streamlit as st
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from country_config import (
    get_country_config,
    get_country_choices,
    validate_font_compliance,
    COUNTRY_REGISTRY,
)
from label_renderer import generate_label_preview_html, generate_label_pdf

# 加载 .env 文件
load_dotenv()

# ==========================================
# Jinja2 模板环境：加载 templates/ 目录下的模板
# ==========================================
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_JINJA_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape([])  # 关闭 HTML 自动转义，JS 数据由 json.dumps 保证安全
)

# ==========================================
# 1. 基础配置与 AI 初始化
# ==========================================
# 从环境变量读取 MiniMax API Key
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
# MiniMax API 基础地址
BASE_URL = "https://api.minimax.chat/v1"

# MiniMax M2.5 模型名称
MODEL_NAME = "abab6.5s-chat"

st.set_page_config(page_title="智能出口标签生成器 (含营养表)", layout="wide")

# ==========================================
# 2. 核心逻辑：AI 提取与结构化数据 (新增营养表字段)
# ==========================================
def extract_label_data(raw_text):
    prompt = f"""
    You are a professional food export compliance expert. Extract product information from the following text and output in strict JSON format.

    IMPORTANT rules:
    - product_name_en: English product name
    - product_name_cn: Chinese product name (keep Chinese, e.g., "海天招牌拌饭酱")
    - allergens: English only (e.g., "Soybeans, Wheat")
    - origin: English only (e.g., "China")
    - manufacturer: English only
    - importer_info: English only
    - nutrition table: English only (e.g., "Energy", "Protein", "Fat", etc.)

    Please output JSON only, no other text.
    注意：营养成分表的 table_data 最多只包含 6 行，不要超过。
    If information is not mentioned, fill with empty string "".

    需要提取的字段包括：
    - product_name_en (英文品名)
    - product_name_cn (中文品名)
    - ingredients (英文配料表)
    - allergens (过敏原提示)
    - net_weight (净重，如 300 g)
    - drained_weight (沥干重，如 ≥165 g)
    - origin (原产国)
    - manufacturer (生产商名称与地址)
    - importer_info (进口商名称与地址)
    - is_halal (是否有清真认证，true 或 false)
    - nutrition (营养成分表对象，包含以下结构：
        - servings_per_package (每包份数，数字或字符串)
        - serving_size (每份大小，如 15g)
        - table_data (一个数组，每个元素包含: name(项目名), per_100g(每100克含量), per_serving(每份含量))
      )

    原始文本：
    {raw_text}
    """

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 4096
    }

    response = requests.post(
        f"{BASE_URL}/text/chatcompletion_v2",
        headers=headers,
        json=data
    )

    # 检查响应状态
    if response.status_code != 200:
        raise Exception(f"API 请求失败: {response.status_code} - {response.text}")

    result = response.json()

    # 调试：打印完整响应
    # print(result)

    # MiniMax 返回的内容在 choices[0].message.content 中
    choices = result.get("choices")
    if not choices or not choices[0].get("message"):
        raise Exception(f"API 响应格式异常: {result}")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise Exception(f"API 返回内容为空: {result}")

    # 尝试提取 JSON 部分（处理 AI 返回可能包含的额外文本）

    # 方法1：直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 方法2：尝试找到 JSON 代码块
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 方法3：找到所有 { } 对，尝试找到完整的 JSON
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            raise Exception(f"JSON 解析失败: {e}\n内容: {content[:500]}")

    raise Exception(f"无法解析 JSON 格式，内容: {content[:300]}")

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
        preview_html, pdf_bytes = generate_label_preview_html(data, country_cfg)
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