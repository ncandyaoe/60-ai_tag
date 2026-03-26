"""
Streamlit 页面：模板管理

上传 .ai 文件、预检区域检测结果、保存为模板、管理已有模板。
"""

import io
import json
import os
import tempfile

import streamlit as st
import fitz  # PyMuPDF

from country_config import get_country_config, get_country_choices, COUNTRY_REGISTRY
from template_extractor import extract_template_regions
from template_store import save_template, list_templates, delete_template, get_template_path
from render_pipeline import render_label
from label_renderer import pdf_to_png_base64

# ---- 页面配置 ----
st.set_page_config(page_title="模板管理", layout="wide")
st.title("📐 模板管理")

# ---- PLM 示例数据（用于预览） ----
_PREVIEW_DATA = {
    "product_name_en": "Light Soy Sauce (Classic Version)",
    "product_name_cn": "生抽酱油(经典版)",
    "net_weight": "500 mL",
    "ingredients": "Water, Soybeans, Salt, Wheat(Gluten), Flavour Enhancer(Monosodium Glutamate), Wheat Flour(Gluten), Colour(Caramel I), Preservative(Potassium Sorbate).",
    "allergens": "Soybeans, Wheat(Gluten)",
    "storage": "Keep in a cool and dry place. Refrigerate after opening.",
    "production_date": "See The Package",
    "best_before": "See The Package",
    "origin": "China",
    "manufacturer": "Foshan Haitian Flavouring & Food Co., Ltd.",
    "manufacturer_address": "Cangjiang Industrial Park, Gaoming District, Foshan, Guangdong, China",
    "importer_info": "Import Co. Ltd.",
    "nutrition": {
        "serving_size": "100mL",
        "table_data": [
            {"name": "Energy", "per_serving": "56 kJ"},
            {"name": "Fat", "per_serving": "0 g"},
            {"name": "Carbohydrate", "per_serving": "2.5 g"},
            {"name": "Protein", "per_serving": "0.8 g"},
            {"name": "Salt", "per_serving": "18.8 g"},
        ],
    },
}

# ==============================================================
# 上传区
# ==============================================================
st.subheader("📤 上传新模板")

col_upload, col_config = st.columns([1, 1])

with col_upload:
    uploaded_file = st.file_uploader(
        "选择 .ai 模板文件",
        type=["ai"],
        help="设计师需在 .ai 文件中用指定颜色色块标注区域：🔴红=标题 🔵蓝=内容 🟢绿=净含量 🟣紫=营养表 🩵青=环保标 🟡黄=Logo",
    )

with col_config:
    template_name = st.text_input("模板名称", placeholder="如：荷兰草菇老抽 50x120mm")

    country_choices = get_country_choices()
    country_labels = [label for _, label in country_choices]
    country_codes = [code for code, _ in country_choices]
    selected_country_label = st.selectbox("绑定国家", country_labels)
    selected_country_code = country_codes[country_labels.index(selected_country_label)]

    _NUT_TYPES = {"3列标准版 (单语言)": "standard_3col", "多语言双列版 (宽标题)": "multilingual_2col"}
    selected_nut_label = st.selectbox("营养表排版类型", list(_NUT_TYPES.keys()))
    selected_nut_type = _NUT_TYPES[selected_nut_label]

    _CONTENT_TYPES = {"单语言 (带前缀自动加粗)": "standard_single", "多语言 (去除前缀，原味输出)": "multilingual"}
    selected_content_label = st.selectbox("正文排版格式", list(_CONTENT_TYPES.keys()))
    selected_content_type = _CONTENT_TYPES[selected_content_label]

# ---- 预检 + 预览 ----
if uploaded_file is not None:
    st.markdown("---")
    st.subheader("🔍 预检结果")

    # 写入临时文件以供 PyMuPDF 读取
    with tempfile.NamedTemporaryFile(suffix=".ai", delete=False) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        cfg = extract_template_regions(tmp_path)

        # ── 检测结果 ──
        col_info, col_preview = st.columns([1, 1])

        with col_info:
            page_w_mm = round(cfg.page_width / 72 * 25.4, 1)
            page_h_mm = round(cfg.page_height / 72 * 25.4, 1)
            st.metric("页面尺寸", f"{page_w_mm} × {page_h_mm} mm")

            region_checks = {
                "🔴 标题 (Title)": bool(cfg.title_rects),
                "🔵 内容 (Content)": bool(cfg.content_rects),
                "🟣 营养表 (Nutrition)": cfg.nut_table is not None,
                "🟢 净含量 (Net Volume)": cfg.net_volume is not None,
                "🟡 Logo": cfg.logo is not None,
                "🩵 环保标 (Eco Icons)": bool(cfg.eco_icon_rects),
            }

            for label, detected in region_checks.items():
                icon = "✅" if detected else "⬜"
                st.write(f"{icon} {label}")

            detected_count = sum(1 for v in region_checks.values() if v)
            total = len(region_checks)

            if detected_count == 0:
                st.error("⚠️ 未检测到任何区域！请确认 .ai 文件中包含约定颜色的色块。")
            elif detected_count < 3:
                st.warning(f"检测到 {detected_count}/{total} 个区域，部分区域可能缺失。")
            else:
                st.success(f"检测到 {detected_count}/{total} 个区域。")

        with col_preview:
            # 使用选定国家配置 + 选择的营养表格式 + 示例数据生成预览
            try:
                cfg.nut_table_type = selected_nut_type
                cfg.content_type = selected_content_type
                country_cfg = get_country_config(selected_country_code)
                pdf_bytes = render_label(cfg, _PREVIEW_DATA, country_cfg, show_regions=True)
                png_b64 = pdf_to_png_base64(pdf_bytes, dpi=216)

                preview_html = f"""<!DOCTYPE html>
                <html><head><meta charset="UTF-8"><style>
                body {{ margin:0; padding:0; background:#4a4a4a; display:flex; justify-content:center; padding: 16px; }}
                img {{ max-width:100%; box-shadow:0 4px 16px rgba(0,0,0,0.5); }}
                </style></head><body>
                <img src="data:image/png;base64,{png_b64}" alt="Template Preview" />
                </body></html>"""

                st.caption("📎 预览（示例数据 + 区域叠加）")
                st.components.v1.html(preview_html, height=600, scrolling=True)
            except Exception as e:
                st.error(f"预览生成失败: {e}")

        # ── 保存按钮 ──
        st.markdown("---")
        if not template_name.strip():
            st.warning("请输入模板名称后再保存。")
        else:
            if st.button("💾 确认保存模板", type="primary"):
                entry = save_template(
                    ai_bytes=uploaded_file.getvalue(),
                    name=template_name.strip(),
                    country_code=selected_country_code,
                    nut_table_type=selected_nut_type,
                )
                st.success(f"✅ 模板「{entry['name']}」已保存！（ID: {entry['id']}）")
                st.rerun()

    except Exception as e:
        st.error(f"❌ .ai 文件解析失败: {e}")
    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ==============================================================
# 已有模板列表
# ==============================================================
st.markdown("---")
st.subheader("📋 已注册模板")

templates = list_templates()

if not templates:
    st.info("暂无已注册模板。请通过上方上传 .ai 文件创建新模板。")
else:
    for tmpl in templates:
        with st.expander(
            f"**{tmpl['name']}** — {tmpl['country_code']} · {tmpl['dimensions_mm']}",
            expanded=False,
        ):
            col_detail, col_action = st.columns([3, 1])

            with col_detail:
                st.write(f"**ID:** `{tmpl['id']}`")
                st.write(f"**国家:** {tmpl['country_code']}")
                
                ntt_val = tmpl.get('nut_table_type', 'standard_3col')
                ntt_display = "3列标准版 (单语言)" if ntt_val == "standard_3col" else "多语言双列版 (宽标题)"
                st.write(f"**营养表类型:** {ntt_display}")

                ctt_val = tmpl.get('content_type', 'standard_single')
                ctt_display = "单语言 (带前缀自动加粗)" if ctt_val == "standard_single" else "多语言 (去除前缀，原味输出)"
                st.write(f"**正文排版格式:** {ctt_display}")
                st.write(f"**尺寸:** {tmpl['dimensions_mm']}")
                st.write(f"**创建时间:** {tmpl['created_at']}")

                regions = tmpl.get("regions_detected", [])
                if regions:
                    st.write(f"**检测到的区域:** {', '.join(regions)}")
                else:
                    st.write("**检测到的区域:** (无)")

            with col_action:
                # 预览按钮
                ai_path = get_template_path(tmpl["id"])
                if ai_path and os.path.isfile(ai_path):
                    if st.button("👁 预览", key=f"preview_{tmpl['id']}"):
                        try:
                            cfg = extract_template_regions(ai_path)
                            cfg.nut_table_type = tmpl.get("nut_table_type", "standard_3col")
                            cfg.content_type = tmpl.get("content_type", "standard_single")
                            country_cfg = get_country_config(tmpl["country_code"])
                            pdf_bytes = render_label(cfg, _PREVIEW_DATA, country_cfg, show_regions=True)
                            png_b64 = pdf_to_png_base64(pdf_bytes, dpi=216)
                            preview_html = f"""<!DOCTYPE html>
                            <html><head><meta charset="UTF-8"><style>
                            body {{ margin:0; padding:0; background:#4a4a4a; display:flex; justify-content:center; padding: 16px; }}
                            img {{ max-width:100%; box-shadow:0 4px 16px rgba(0,0,0,0.5); }}
                            </style></head><body>
                            <img src="data:image/png;base64,{png_b64}" alt="Preview" />
                            </body></html>"""
                            st.components.v1.html(preview_html, height=500, scrolling=True)
                        except Exception as e:
                            st.error(f"预览失败: {e}")

                # 删除按钮
                if st.button("🗑 删除", key=f"delete_{tmpl['id']}", type="secondary"):
                    delete_template(tmpl["id"])
                    st.success(f"已删除模板「{tmpl['name']}」")
                    st.rerun()
                            <html><head><meta charset="UTF-8"><style>
                            body {{ margin:0; padding:0; background:#4a4a4a; display:flex; justify-content:center; padding: 16px; }}
                            img {{ max-width:100%; box-shadow:0 4px 16px rgba(0,0,0,0.5); }}
                            </style></head><body>
                            <img src="data:image/png;base64,{png_b64}" alt="Preview" />
                            </body></html>"""
                            st.components.v1.html(preview_html, height=500, scrolling=True)
                        except Exception as e:
                            st.error(f"预览失败: {e}")

                # 删除按钮
                if st.button("🗑 删除", key=f"delete_{tmpl['id']}", type="secondary"):
                    delete_template(tmpl["id"])
                    st.success(f"已删除模板「{tmpl['name']}」")
                    st.rerun()
