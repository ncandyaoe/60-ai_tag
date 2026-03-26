"""
模板存储管理器

使用 templates/ 目录存储 .ai 文件，
用 templates/registry.json 作为元数据注册表。

用法:
    from template_store import save_template, list_templates, delete_template

    # 保存新模板
    tmpl = save_template(ai_bytes, "荷兰草菇老抽", "NL")

    # 列出所有模板
    for t in list_templates():
        print(t["name"], t["country_code"])

    # 删除模板
    delete_template(tmpl["id"])
"""

import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Optional

from template_extractor import extract_template_regions

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_THIS_DIR, "templates")
_REGISTRY_FILE = os.path.join(_TEMPLATES_DIR, "registry.json")


def _ensure_dir():
    """确保 templates/ 目录存在。"""
    os.makedirs(_TEMPLATES_DIR, exist_ok=True)


def _load_registry() -> list[dict]:
    """从 registry.json 加载模板注册表。"""
    if not os.path.isfile(_REGISTRY_FILE):
        return []
    try:
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("templates", [])
    except (json.JSONDecodeError, IOError):
        return []


def _save_registry(templates: list[dict]):
    """保存模板注册表到 registry.json。"""
    _ensure_dir()
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump({"templates": templates}, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def save_template(
    ai_bytes: bytes,
    name: str,
    country_code: str,
    nut_table_type: str = "standard_3col",
    content_type: str = "standard_single",
) -> dict:
    """
    保存 .ai 文件并注册为新模板。

    Args:
        ai_bytes:     .ai 文件的二进制内容
        name:         用户自定义模板名称
        country_code: 绑定的国家代码（如 "NL", "AU"）

    Returns:
        注册的模板元数据 dict
    """
    _ensure_dir()

    template_id = str(uuid.uuid4())[:8]
    ai_filename = f"{template_id}.ai"
    ai_path = os.path.join(_TEMPLATES_DIR, ai_filename)

    # 写入 .ai 文件
    with open(ai_path, "wb") as f:
        f.write(ai_bytes)

    # 提取区域信息
    try:
        cfg = extract_template_regions(ai_path)
        page_w_mm = round(cfg.page_width / 72 * 25.4, 1)
        page_h_mm = round(cfg.page_height / 72 * 25.4, 1)
        dimensions_mm = f"{page_w_mm}×{page_h_mm}"

        regions_detected = []
        if cfg.title_rects:
            regions_detected.append("title")
        if cfg.content_rects:
            regions_detected.append("content")
        if cfg.nut_table:
            regions_detected.append("nut_table")
        if cfg.net_volume:
            regions_detected.append("net_volume")
        if cfg.logo:
            regions_detected.append("logo")
        if cfg.eco_icon_rects:
            regions_detected.append("eco_icons")
    except Exception:
        dimensions_mm = "未知"
        regions_detected = []

    entry = {
        "id": template_id,
        "name": name,
        "country_code": country_code,
        "nut_table_type": nut_table_type,
        "content_type": content_type,
        "ai_filename": ai_filename,
        "dimensions_mm": dimensions_mm,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "regions_detected": regions_detected,
    }

    templates = _load_registry()
    templates.append(entry)
    _save_registry(templates)

    return entry


def list_templates() -> list[dict]:
    """列出所有已注册模板。"""
    return _load_registry()


def get_template(template_id: str) -> Optional[dict]:
    """根据 ID 获取单个模板信息。"""
    for t in _load_registry():
        if t["id"] == template_id:
            return t
    return None


def get_template_path(template_id: str) -> Optional[str]:
    """根据 ID 返回 .ai 文件的绝对路径。"""
    t = get_template(template_id)
    if t is None:
        return None
    return os.path.join(_TEMPLATES_DIR, t["ai_filename"])


def delete_template(template_id: str) -> bool:
    """
    删除模板（移除注册表条目 + 删除 .ai 文件）。

    Returns:
        True 如果删除成功，False 如果模板不存在
    """
    templates = _load_registry()
    target = None
    for t in templates:
        if t["id"] == template_id:
            target = t
            break

    if target is None:
        return False

    # 删除 .ai 文件
    ai_path = os.path.join(_TEMPLATES_DIR, target["ai_filename"])
    if os.path.isfile(ai_path):
        os.remove(ai_path)

    # 从注册表移除
    templates = [t for t in templates if t["id"] != template_id]
    _save_registry(templates)
    return True
