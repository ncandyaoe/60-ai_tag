"""
Streamlit 页面：流式矩形布局引擎测试

左侧：PLM JSON 编辑器 + 生成按钮
右侧：4 种布局自适应渲染结果（字号由二分搜索自动决定）
"""

import io
import json
import streamlit as st
import fitz
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import Color

from flow_layout import (
    FlowRect, TextBlock, FontConfig,
    layout_flow_content, find_best_font_size, plm_to_blocks,
    get_min_font_pt, _MIN_HEIGHT_MM,
)

# ---- 页面配置 ----
st.set_page_config(page_title="Flow Layout 测试", layout="wide")
st.title("🔲 流式矩形布局引擎测试")

# ---- 画布尺寸 ----
CW, CH = 200, 200
M = 6
CONTENT_W = CW - 2 * M
TITLE_H = 20
NET_H = 20
NUT_H = 70
NUT_W = round(CONTENT_W * 0.58)
LEFT_W = CONTENT_W - NUT_W
TEXT_TOP = CH - M - TITLE_H
NET_TOP = M + NET_H
BANNER_H = 20
R1_H_SPLIT = 50

# ---- 色板 ----
R1_FILL   = Color(0.20, 0.50, 1.00, alpha=0.08)
R1_STROKE = Color(0.20, 0.50, 1.00, alpha=0.6)
R2_FILL   = Color(0.20, 0.80, 0.30, alpha=0.08)
R2_STROKE = Color(0.20, 0.80, 0.30, alpha=0.6)
FIXED_FILL   = Color(0.90, 0.90, 0.90, alpha=1.0)
FIXED_STROKE = Color(0.70, 0.70, 0.70)

# ---- PLM 示例数据 ----
PLM_EXAMPLE_LONG = {
    "product_name_en": "Light Soy Sauce (Classic Version)",
    "product_name_cn": "生抽酱油(经典版)",
    "net_weight": "Net Volume: 1.9 L",
    "ingredients": "Water, Soybeans, Salt, Wheat(Gluten), Flavour Enhancer(Monosodium Glutamate), Wheat Flour(Gluten), Fructose-glucose Syrup, Colour(Caramel I), Flavour Enhancer(Disodium 5'-Ribonucleotide), Flavour Enhancer(Disodium 5'-Inosinate), Preservative(Potassium Sorbate).",
    "allergens": "Soybeans, Wheat(Gluten)",
    "storage": "Please keep it in a cool and dry place. Tightly close lid after use and keep refrigerated.",
    "production_date": "See The Package",
    "best_before": "See The Package",
    "origin": "China",
    "manufacturer": "Foshan Haitian (Gaoming) Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Eastern Park (No.889 Gaoming Road), Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "Wonderful Food Co. Ltd.",
}

PLM_EXAMPLE_SHORT = {
    "product_name_en": "Sesame Oil",
    "product_name_cn": "芝麻油",
    "net_weight": "500 mL",
    "ingredients": "Sesame oil (100%).",
    "allergens": "Sesame",
    "storage": "Store in a cool, dry place.",
    "production_date": "See packaging",
    "best_before": "See packaging",
    "origin": "China",
    "manufacturer": "Guangzhou Baolaixing Food Co., Ltd.",
    "manufacturer_address": "",
    "importer_info": "ABC Trading Pty Ltd",
}

PLM_EXAMPLES = {
    "生抽酱油（多字版本）": PLM_EXAMPLE_LONG,
    "芝麻油（少字版本）":   PLM_EXAMPLE_SHORT,
}

# ---- 4 种布局 ----
LAYOUTS = {
    "倒L型": {
        "desc": "全宽 → 右下营养表避让",
        "regions": [
            FlowRect(x=M, y=TEXT_TOP, width=CONTENT_W, height=TEXT_TOP - (M+NET_H+NUT_H)),
            FlowRect(x=M, y=M+NET_H+NUT_H, width=LEFT_W, height=NUT_H, seamless=True),
        ],
        "fixed": [
            {"label": "Title",     "x": M,        "y": CH-M-TITLE_H, "w": CONTENT_W, "h": TITLE_H},
            {"label": "Nut Table", "x": M+LEFT_W,  "y": M+NET_H,      "w": NUT_W,     "h": NUT_H},
            {"label": "Net Vol",   "x": M,         "y": M,             "w": CONTENT_W, "h": NET_H},
        ],
    },
    "正L型": {
        "desc": "左上营养表避让 → 右栏 → 全宽",
        "regions": [
            FlowRect(x=M+NUT_W, y=TEXT_TOP, width=LEFT_W, height=NUT_H),
            FlowRect(x=M, y=TEXT_TOP-NUT_H, width=CONTENT_W, height=(TEXT_TOP-NUT_H)-NET_TOP),
        ],
        "fixed": [
            {"label": "Title",     "x": M, "y": CH-M-TITLE_H,  "w": CONTENT_W, "h": TITLE_H},
            {"label": "Nut Table", "x": M, "y": TEXT_TOP-NUT_H, "w": NUT_W,     "h": NUT_H},
            {"label": "Net Vol",   "x": M, "y": M,              "w": CONTENT_W, "h": NET_H},
        ],
    },
    "全宽": {
        "desc": "无营养表障碍，单矩形排列",
        "regions": [
            FlowRect(x=M, y=TEXT_TOP, width=CONTENT_W, height=TEXT_TOP-NET_TOP),
        ],
        "fixed": [
            {"label": "Title",   "x": M, "y": CH-M-TITLE_H, "w": CONTENT_W, "h": TITLE_H},
            {"label": "Net Vol", "x": M, "y": M,             "w": CONTENT_W, "h": NET_H},
        ],
    },
    "不连续": {
        "desc": "中间被横幅分隔，文字分两段",
        "regions": [
            FlowRect(x=M, y=TEXT_TOP, width=CONTENT_W, height=R1_H_SPLIT),
            FlowRect(x=M, y=TEXT_TOP-R1_H_SPLIT-BANNER_H, width=CONTENT_W,
                     height=(TEXT_TOP-R1_H_SPLIT-BANNER_H)-NET_TOP),
        ],
        "fixed": [
            {"label": "Title",   "x": M, "y": CH-M-TITLE_H,                "w": CONTENT_W, "h": TITLE_H},
            {"label": "Banner",  "x": M, "y": TEXT_TOP-R1_H_SPLIT-BANNER_H, "w": CONTENT_W, "h": BANNER_H},
            {"label": "Net Vol", "x": M, "y": M,                            "w": CONTENT_W, "h": NET_H},
        ],
    },
}


# ---- 渲染函数 ----
def render_layout(layout_cfg: dict, blocks: list[TextBlock], min_size: float = 4.0) -> tuple[bytes, float, float, int, bool]:
    """渲染一种布局，返回 (PNG, 自适应字号, h_scale, 行数, 是否溢出)"""
    regions = layout_cfg["regions"]
    best_size, best_hs = find_best_font_size(blocks, regions, min_size=min_size, max_size=16.0)

    fc = FontConfig(font_size=best_size, h_scale=best_hs)
    est = layout_flow_content(blocks, regions, fc)

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(CW, CH))
    c.setFillColor(Color(1, 1, 1))
    c.rect(0, 0, CW, CH, stroke=0, fill=1)
    c.setStrokeColor(Color(0, 0, 0, alpha=0.3))
    c.setLineWidth(0.5)
    c.rect(0, 0, CW, CH, stroke=1, fill=0)

    # 固定元素
    for f in layout_cfg["fixed"]:
        c.setFillColor(FIXED_FILL)
        c.setStrokeColor(FIXED_STROKE)
        c.setLineWidth(0.5)
        c.rect(f["x"], f["y"], f["w"], f["h"], stroke=1, fill=1)
        c.setFillColor(Color(0.5, 0.5, 0.5))
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(f["x"]+f["w"]/2, f["y"]+f["h"]/2-3, f["label"])

    # 流式矩形边框
    styles = [(R1_FILL, R1_STROKE), (R2_FILL, R2_STROKE)]
    for i, r in enumerate(regions):
        fill, stroke = styles[i % 2]
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(1.2)
        c.setDash(4, 3)
        c.rect(r.x, r.bottom, r.width, r.height, stroke=1, fill=1)
        c.setDash()
        c.setFillColor(stroke)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(r.x + 2, r.bottom + 2, f"R{i+1}")

    # 排版 + 绘制
    layout_flow_content(blocks, regions, fc, canvas=c)

    c.save()
    buf.seek(0)

    doc = fitz.open(stream=buf.read(), filetype="pdf")
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    png = pix.tobytes("png")
    doc.close()
    return png, best_size, best_hs, est.total_lines, est.overflow


# ---- Streamlit UI ----

# 左侧：PLM JSON 编辑器（用 st.form 保证编辑内容不丢失）
st.sidebar.subheader("📋 PLM 产品数据")
example_key = st.sidebar.selectbox("选择示例场景", list(PLM_EXAMPLES.keys()))

# 目的国选择器
COUNTRY_OPTIONS = {
    f"{code} ({mm}mm → {get_min_font_pt(code):.1f}pt)": code
    for code, mm in _MIN_HEIGHT_MM.items()
}
selected_label = st.sidebar.selectbox("🌍 目的国（法规最小字高）", list(COUNTRY_OPTIONS.keys()), index=3)  # AU
selected_country = COUNTRY_OPTIONS[selected_label]
min_font_size = get_min_font_pt(selected_country)

# 当切换示例场景时更新默认 JSON
default_json = json.dumps(PLM_EXAMPLES[example_key], ensure_ascii=False, indent=2)

with st.sidebar.form("plm_form"):
    json_input = st.text_area(
        "JSON（可直接编辑文字量）",
        value=default_json,
        height=320,
    )
    submitted = st.form_submit_button("✅ 生成布局预览", type="primary")

# 解析 JSON
if submitted:
    try:
        st.session_state["plm_data"] = json.loads(json_input)
        st.session_state["parse_ok"] = True
    except json.JSONDecodeError as e:
        st.sidebar.error(f"JSON 格式错误：{e}")
        st.session_state["parse_ok"] = False

# 首次加载也解析一次
if "plm_data" not in st.session_state:
    try:
        st.session_state["plm_data"] = json.loads(default_json)
        st.session_state["parse_ok"] = True
    except json.JSONDecodeError:
        st.session_state["parse_ok"] = False

# 主区域渲染
if st.session_state.get("parse_ok"):
    blocks = plm_to_blocks(st.session_state["plm_data"])
    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"**{len(blocks)} 个文本块** · "
        f"最小字号 {min_font_size:.1f}pt ({selected_country})"
    )

    cols = st.columns(2)
    for i, (name, cfg) in enumerate(LAYOUTS.items()):
        with cols[i % 2]:
            png, font_size, h_scale, n_lines, overflow = render_layout(cfg, blocks, min_size=min_font_size)
            if overflow:
                status = "⚠️ OVERFLOW"
            elif h_scale < 1.0:
                status = f"⚠️ {font_size:.1f}pt · h_scale={int(h_scale*100)}% · {n_lines} lines"
            else:
                status = f"✅ {font_size:.1f}pt · {n_lines} lines"
            st.subheader(name)
            st.caption(f"{cfg['desc']} — {status}")
            st.image(png, use_column_width=True)
else:
    st.info("请在左侧编辑 JSON 后点击「✅ 生成布局预览」。")

