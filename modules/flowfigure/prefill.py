# -*- coding: utf-8 -*-
"""手稿 Methods -> Figure 1 canonical spec 草稿（只填 Methods 写到的字段）。

解析器复用 ``modules.stats.flow_spec_text``；本模块只负责把它映射成 canonical
v2，并原样带回 ``missing`` / ``evidence``。草稿不写项目文件；缺的字段留空，
绝不猜数。审计问题以 ``problems`` 返回，由界面显示。
"""
from __future__ import annotations

from pathlib import Path

from modules.flowfigure import audit as audit_mod
from modules.flowfigure import spec as spec_mod


def from_spec(study_spec: dict) -> dict:
    """study shape（flow_spec_text 的 ``spec``）-> canonical 草稿 + problems。"""
    if not isinstance(study_spec, dict):
        raise ValueError("手稿解析结果必须是对象")
    canonical = spec_mod.normalize(study_spec)
    problems: list[str] = []
    try:
        spec_mod.validate(canonical)
        audit_mod.audit(canonical)
    except (ValueError, audit_mod.AuditError) as err:  # 草稿可以暂时不合格，问题照样报
        problems.append(str(err))
    return {"spec": canonical, "problems": problems}


def from_docx(path) -> dict:
    """docx -> canonical 草稿；Methods 缺失时抛 AuditError（由调用方展示）。"""
    from modules.stats.flow_spec_text import (
        docx_paragraphs,
        methods_section,
        spec_from_text,
    )

    path = Path(path)
    if path.suffix.lower() != ".docx":
        raise ValueError(f"仅支持 .docx 手稿，请先另存为 .docx：{path.name}")
    parsed = spec_from_text(methods_section(docx_paragraphs(path)))
    out = from_spec(parsed["spec"])
    out["missing"] = list(parsed.get("missing") or [])
    out["evidence"] = dict(parsed.get("evidence") or {})
    out["file"] = path.name
    return out
