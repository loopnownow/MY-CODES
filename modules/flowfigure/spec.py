# -*- coding: utf-8 -*-
"""Figure 1 spec v3：canonical 结构、旧 spec 映射、校验与 JSON 写回。

canonical 结构::

    {
      "schema_version": 3,
      "layout": "<来源标记，渲染忽略>",
      "screened": {"text": str, "n": int | None},
      "criteria": {"inclusion": [str],
                   "exclusion": [{"reason": str, "n": int | None}]},
      "included": {"text": str, "n": int | None, "pp": int | None, "non_pp": int | None},
      "presplit": [{"id": "arm-0", "label": str, "n": ..., "pp": ..., "non_pp": ...}],
      "downsample": [{"arm": "arm-0", "n": ..., "class": "pp" | "non_pp", "reason": str}],
      "cohorts": [{"id": "cohort-0", "label": str, "n": ..., "pp": ..., "non_pp": ...}],
      "external": [{"id": "external-0", "label": str, "n": ..., "pp": ..., "non_pp": ...}],
      "analyzed": {"text": str, "label": str, "n": ..., "pp": ..., "non_pp": ...},
      "pipeline": {"balance": ..., "balance_n": ...,
                   "steps": [{"kind": str, "enabled": bool,
                              "label": str?, "items": [str]?}]},
      "pos_name": str,
      "notes": str,
      "overrides": {"boxes": {}, "edges": {}, "hidden": []},
      "font_size": 12.0
    }

``layout`` 只是来源标记，版式由字段决定（单一渲染器）。v1/v2 旧 spec 自动归一到
v3：旧 pipeline 布尔键 -> 默认 steps；study 的 included_n -> included；analyzed 照读。
未知顶级键原样保留；``normalize`` 幂等；``validate`` 显式报错，不做静默回退。
"""
from __future__ import annotations

import copy
import json
import re

# -- CONFIG -------------------------------------------------------------------
SCHEMA_VERSION = 3
TARGET_WIDTH_CM = 16.0
MAX_WIDTH_CM = 17.2
DEFAULT_FONT_SIZE_PT = 12.0
MARGIN_IN = 0.12
CM_PER_INCH = 2.54
DEFAULT_POS_NAME = "positive"
DEFAULT_LAYOUT = "expanded-split-downsample"
# 历史来源标记（layout 只作标记，渲染忽略；旧 CLI 包装仍用它做兼容检查）
LAYOUT_EXPANDED = "expanded-split-downsample"
LAYOUT_STUDY = "study"
LAYOUT_PUBLISHED = "published-a"
LAYOUTS = (LAYOUT_EXPANDED, LAYOUT_STUDY, LAYOUT_PUBLISHED)

DEFAULT_STEPS = (
    "lasso_logistic", "nomogram", "calibration", "roc", "nri_idi", "dca",
)
STEP_KINDS = DEFAULT_STEPS + ("models", "explain")
STEP_LABELS = {
    "lasso_logistic": "LASSO logistic",
    "nomogram": "Nomogram",
    "calibration": "Calibration",
    "roc": "ROC AUC",
    "nri_idi": "NRI / IDI",
    "dca": "DCA",
    "models": "Models",
    "explain": "DAG / Mediation",
}
MODEL_KEYS = ("clinical", "radiomics", "nomogram")
EXPLAIN_KEYS = ("dag", "mediation")
BALANCE_KEYS = ("downsample", "smote")

_N_EQ = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
_LEADING_COUNT = re.compile(
    r"^\s*(\d+)\s+(?:patients?|subjects?|cases?|women|men)\b", re.I
)

_REPLACED_KEYS = {
    "schema_version", "layout", "screened", "analyzed", "inclusion", "exclusion",
    "presplit", "arms", "splits", "cohorts", "downsample", "pos_name", "notes",
    "overrides", "font_size", "criteria", "screened_n", "included_n",
    "training", "test", "validation", "included_positive", "included_negative",
    "included_positive_label", "included_negative_label", "balance", "balance_n",
    "lasso", "models", "metrics", "explain", "included", "external",
}
_ENTRY_REBUILT = {
    "id", "label", "n", "pp", "non_pp", "n_n", "pp_n", "non_pp_n",
    "positive", "negative", "positive_label", "negative_label",
    "arm", "class", "reason",
}


def parse_n(value) -> int | None:
    """解析计数：优先 ``n = N``，其次 ``N patients``；解析不出返回 None，绝不猜。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        if value.get("n") not in (None, ""):
            return parse_n(value.get("n"))
        for key in ("text", "label", "reason", "screened", "analyzed"):
            if key in value:
                found = parse_n(value.get(key))
                if found is not None:
                    return found
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _N_EQ.search(text)
    if match:
        return int(match.group(1))
    match = _LEADING_COUNT.match(text)
    if match:
        return int(match.group(1))
    return None


_PAIR_IN_PARENS = re.compile(r"\(([^)]*)\)")
_PAIR_PP = re.compile(
    r"(\d+)\s*(?:patients?\s*)?(?:with\s*)?(?:pseudo[-\s]?progression|pp)\b", re.I
)
_PAIR_NON = re.compile(
    r"(\d+)\s*(?:patients?\s*)?(?:with(?:out)?\s*)?"
    r"(?:non[-\s]?pseudo[-\s]?progression|non[-\s]?pp)\b",
    re.I,
)


def pair_from_text(text, n: int | None = None) -> tuple[int | None, int | None]:
    """从 ``… (53 pseudoprogression vs 196 non-pseudoprogression)`` 解析 PP/non-PP。

    只接受两数之和等于 n（n 已知时）的组合；否则返回 ``(None, None)``，绝不猜。
    """
    text = str(text or "")
    if not text:
        return None, None
    for group in _PAIR_IN_PARENS.findall(text):
        numbers = [int(item) for item in re.findall(r"\d+", group)]
        if len(numbers) == 2 and (n is None or numbers[0] + numbers[1] == n):
            return numbers[0], numbers[1]
    left = _PAIR_PP.search(text)
    right = _PAIR_NON.search(text)
    if left and right:
        pp, non_pp = int(left.group(1)), int(right.group(1))
        if n is None or pp + non_pp == n:
            return pp, non_pp
    return None, None


def unwrap(raw) -> dict:
    """取出真正的 spec：兼容旧的 ``{"spec": {...}, "meta": ...}`` 包装。"""
    if not isinstance(raw, dict):
        raise ValueError("Figure 1 spec 必须是 JSON 对象")
    if isinstance(raw.get("spec"), dict) and "layout" not in raw:
        return raw["spec"]
    return raw


def _infer_layout(raw: dict) -> str:
    if "presplit" in raw or "arms" in raw:
        return "expanded-split-downsample"
    if raw.get("kind") == "study" or any(
        key in raw for key in (
            "screened_n", "included_n", "training", "test", "validation",
            "pipeline", "balance", "models", "metrics", "explain",
        )
    ):
        return "study"
    if "splits" in raw:
        return "published-a"
    return DEFAULT_LAYOUT


# -- field mapping ------------------------------------------------------------
def _screened(src: dict) -> dict:
    raw = src.get("screened")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        return {"text": text, "n": parse_n(raw)}
    if raw not in (None, ""):
        text = str(raw).strip()
        return {"text": text, "n": parse_n(text)}
    n = parse_n(src.get("screened_n"))
    period = str(src.get("period") or "").strip()
    if n is None and not period:
        return {"text": "", "n": None}
    top = f"{n} patients screened" if n is not None else "Patients screened"
    return {"text": f"{period}\n{top}" if period else top, "n": n}


def _criteria(src: dict) -> dict:
    raw = src.get("criteria") if isinstance(src.get("criteria"), dict) else {}
    inclusion_raw = raw.get("inclusion") if "inclusion" in raw else src.get("inclusion")
    inclusion = [
        str(item).strip() for item in (inclusion_raw or []) if str(item).strip()
    ]
    exclusion_raw = raw.get("exclusion") if "exclusion" in raw else src.get("exclusion")
    exclusion = []
    for entry in exclusion_raw or []:
        if isinstance(entry, dict):
            reason = str(entry.get("reason") or entry.get("text") or "").strip()
            n = parse_n(entry.get("n"))
            if n is None:
                n = parse_n(reason)
        else:
            reason = str(entry).strip()
            n = parse_n(reason)
        item = _entry_extras(entry) if isinstance(entry, dict) else {}
        item["reason"] = reason
        item["n"] = n
        exclusion.append(item)
    return {"inclusion": inclusion, "exclusion": exclusion}


def _entry_extras(entry: dict) -> dict:
    return {
        key: copy.deepcopy(val)
        for key, val in entry.items()
        if key not in _ENTRY_REBUILT
    }


def _included(src: dict) -> dict:
    raw = src.get("included")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        n = parse_n(raw.get("n"))
        pp = parse_n(raw.get("pp", raw.get("positive")))
        non_pp = parse_n(raw.get("non_pp", raw.get("negative")))
        if pp is None and non_pp is None:
            pp, non_pp = pair_from_text(text, n)
        return {"text": text, "n": n, "pp": pp, "non_pp": non_pp}
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        n = parse_n(text)
        pp, non_pp = pair_from_text(text, n)
        return {"text": text, "n": n, "pp": pp, "non_pp": non_pp}
    n = parse_n(src.get("included_n"))
    pp = parse_n(src.get("included_positive"))
    non_pp = parse_n(src.get("included_negative"))
    text = str(src.get("included_text") or "").strip()
    return {"text": text, "n": n, "pp": pp, "non_pp": non_pp}


def _presplit(src: dict) -> list[dict]:
    raw = src.get("presplit")
    if raw is None:
        raw = src.get("arms")
    if raw is None or raw == "":
        return []
    if isinstance(raw, dict):
        items = []
        for key, val in raw.items():
            entry = dict(val or {}) if isinstance(val, dict) else {"n": val}
            entry.setdefault("label", str(key).replace("_", " ").title())
            items.append(entry)
    elif isinstance(raw, (list, tuple)):
        items = [
            dict(val) if isinstance(val, dict) else {"label": str(val)}
            for val in raw
        ]
    else:
        raise ValueError("presplit 必须是对象或数组")

    out = []
    for index, entry in enumerate(items):
        item = _entry_extras(entry)
        item["id"] = str(entry.get("id") or f"arm-{index}")
        item["label"] = str(entry.get("label") or f"Arm {index + 1}").strip()
        item["n"] = parse_n(entry.get("n", entry.get("n_n")))
        item["pp"] = parse_n(entry.get("pp", entry.get("pp_n")))
        item["non_pp"] = parse_n(entry.get("non_pp", entry.get("non_pp_n")))
        out.append(item)
    return out


def _cohort_entries(raw) -> list[dict]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, dict):
        items = [
            dict(val or {}) if isinstance(val, dict) else {"n": val}
            for val in raw.values()
        ]
    elif isinstance(raw, (list, tuple)):
        items = [
            dict(val) if isinstance(val, dict) else {"label": str(val)}
            for val in raw
        ]
    else:
        raise ValueError("cohorts/splits 必须是对象或数组")
    out = []
    for index, entry in enumerate(items):
        item = _entry_extras(entry)
        label = str(entry.get("label") or f"Cohort {index + 1}").strip()
        if "development" in label.lower():
            label = "Training Cohort"
        item["id"] = str(entry.get("id") or f"cohort-{index}")
        item["label"] = label
        item["n"] = parse_n(entry.get("n"))
        item["pp"] = parse_n(entry.get("pp", entry.get("positive")))
        item["non_pp"] = parse_n(entry.get("non_pp", entry.get("negative")))
        out.append(item)
    return out


def _cohorts(src: dict) -> list[dict]:
    raw = src.get("cohorts")
    if raw not in (None, "", [], {}):
        return _cohort_entries(raw)
    raw = src.get("splits")
    if raw not in (None, "", [], {}):
        return _cohort_entries(raw)
    out = []
    for key, label, box_id in (
        ("training", "Training set", "cohort-training"),
        ("test", "Test set", "cohort-test"),
        ("validation", "Validation set", "cohort-validation"),
    ):
        raw_entry = src.get(key)
        if not raw_entry:
            continue
        if not isinstance(raw_entry, dict):
            raise ValueError(f"{key} 必须是对象（n/positive/negative）")
        out.append({
            "id": box_id,
            "label": str(raw_entry.get("label") or label).strip(),
            "n": parse_n(raw_entry.get("n")),
            "pp": parse_n(raw_entry.get("positive")),
            "non_pp": parse_n(raw_entry.get("negative")),
        })
    return out


def _downsample(src: dict, presplit: list[dict]) -> list[dict]:
    raw = src.get("downsample")
    if raw in (None, "", [], {}):
        return []
    if not presplit:
        raise ValueError("spec 有 downsample 但没有 presplit/arms，无法挂到哪个臂")
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    ids = [arm["id"] for arm in presplit]
    labels = [arm["label"] for arm in presplit]
    out = []
    for entry in items:
        entry = dict(entry or {})
        target = entry.get("arm")
        arm_id = ids[0]
        if isinstance(target, int) and not isinstance(target, bool):
            if not 0 <= target < len(ids):
                raise ValueError(f"downsample.arm {target!r} 越界")
            arm_id = ids[target]
        elif isinstance(target, str) and target:
            if target in ids:
                arm_id = target
            elif target in labels:
                arm_id = ids[labels.index(target)]
            else:
                raise ValueError(f"downsample.arm {target!r} 不在 presplit 里")
        klass = str(entry.get("class") or "non_pp")
        if klass not in ("pp", "non_pp"):
            raise ValueError(
                f"downsample.class {klass!r} 必须是 'pp' 或 'non_pp'（默认 non_pp）"
            )
        item = _entry_extras(entry)
        item["arm"] = arm_id
        item["n"] = parse_n(entry.get("n"))
        item["class"] = klass
        item["reason"] = str(entry.get("reason") or "").strip()
        out.append(item)
    return out


def _external(src: dict) -> list[dict]:
    raw = src.get("external")
    if raw in (None, "", [], {}):
        return []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        raise ValueError("external 必须是数组")
    out = []
    for index, entry in enumerate(raw):
        entry = dict(entry or {}) if isinstance(entry, dict) else {"label": str(entry)}
        item = _entry_extras(entry)
        item["id"] = str(entry.get("id") or f"external-{index}")
        item["label"] = str(entry.get("label") or f"External {index + 1}").strip()
        item["n"] = parse_n(entry.get("n"))
        item["pp"] = parse_n(entry.get("pp", entry.get("positive")))
        item["non_pp"] = parse_n(entry.get("non_pp", entry.get("negative")))
        out.append(item)
    return out


def _analyzed(src: dict) -> dict:
    raw = src.get("analyzed")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        label = str(raw.get("label") or "").strip()
        n = parse_n(raw.get("n"))
        if n is None:
            n = parse_n(text)
        pp = parse_n(raw.get("pp", raw.get("positive")))
        non_pp = parse_n(raw.get("non_pp", raw.get("negative")))
        if pp is None and non_pp is None:
            pp, non_pp = pair_from_text(text, n)
        return {
            "text": text,
            "label": label,
            "n": n,
            "pp": pp,
            "non_pp": non_pp,
        }
    if raw not in (None, ""):
        text = str(raw).strip()
        n = parse_n(text)
        pp, non_pp = pair_from_text(text, n)
        return {"text": text, "label": "", "n": n, "pp": pp, "non_pp": non_pp}
    return {"text": "", "label": "", "n": None, "pp": None, "non_pp": None}


def _default_steps() -> list[dict]:
    return [{"kind": kind, "enabled": False} for kind in DEFAULT_STEPS]


def _steps_from_legacy(src: dict, pipeline: dict) -> list[dict]:
    models = [str(x) for x in (pipeline.get("models", src.get("models")) or [])]
    metrics = [str(x) for x in (pipeline.get("metrics", src.get("metrics")) or [])]
    explain = [str(x) for x in (pipeline.get("explain", src.get("explain")) or [])]
    lasso = bool(pipeline.get("lasso", src.get("lasso")))
    enabled = {
        "lasso_logistic": lasso,
        "nomogram": "nomogram" in models,
        "calibration": "calibration" in metrics,
        "roc": "roc" in metrics,
        "nri_idi": "nri" in metrics or "idi" in metrics,
        "dca": "dca" in metrics,
    }
    steps = [{"kind": kind, "enabled": enabled.get(kind, False)} for kind in DEFAULT_STEPS]
    model_items = [item for item in models if item in ("clinical", "radiomics")]
    if model_items:
        steps.append({"kind": "models", "enabled": True, "items": model_items})
    if explain:
        steps.append({"kind": "explain", "enabled": True, "items": explain})
    return steps


def _steps(src: dict, pipeline: dict) -> list[dict]:
    raw = pipeline.get("steps")
    if isinstance(raw, list):
        out = []
        for entry in raw:
            entry = dict(entry or {}) if isinstance(entry, dict) else {"kind": str(entry)}
            kind = str(entry.get("kind") or "").strip()
            item = _entry_extras(entry)
            item["kind"] = kind
            item["enabled"] = bool(entry.get("enabled"))
            label = str(entry.get("label") or "").strip()
            if label:
                item["label"] = label
            items = entry.get("items")
            if items:
                item["items"] = [str(x) for x in items]
            out.append(item)
        return out
    legacy_keys = ("lasso", "models", "metrics", "explain", "balance")
    has_legacy = any(
        key in pipeline or key in src for key in legacy_keys
    )
    return _steps_from_legacy(src, pipeline) if has_legacy else _default_steps()


def _pipeline(src: dict) -> dict:
    raw = src.get("pipeline") if isinstance(src.get("pipeline"), dict) else {}
    balance = raw.get("balance", src.get("balance"))
    balance = str(balance).strip() if balance not in (None, "") else None
    return {
        "balance": balance,
        "balance_n": parse_n(raw.get("balance_n", src.get("balance_n"))),
        "steps": _steps(src, raw),
    }


def normalize(raw) -> dict:
    """旧 spec / study spec -> canonical v3；未知键保留，结果幂等。"""
    src = copy.deepcopy(unwrap(raw))
    out = {
        key: copy.deepcopy(val)
        for key, val in src.items()
        if key not in _REPLACED_KEYS and key != "pipeline"
    }
    out["schema_version"] = SCHEMA_VERSION
    out["layout"] = str(src.get("layout") or _infer_layout(src))
    out["screened"] = _screened(src)
    out["criteria"] = _criteria(src)
    out["included"] = _included(src)
    out["presplit"] = _presplit(src)
    out["downsample"] = _downsample(src, out["presplit"])
    out["cohorts"] = _cohorts(src)
    out["external"] = _external(src)
    out["analyzed"] = _analyzed(src)
    out["pipeline"] = _pipeline(src)
    out["pos_name"] = str(
        src.get("pos_name") or src.get("included_positive_label") or DEFAULT_POS_NAME
    ).strip()
    out["notes"] = str(src.get("notes") or "")
    out["overrides"] = copy.deepcopy(
        src.get("overrides") or {"boxes": {}, "edges": {}, "hidden": []}
    )
    out["font_size"] = float(src.get("font_size") or DEFAULT_FONT_SIZE_PT)
    return out


def _has_content(spec: dict) -> bool:
    if (spec.get("screened") or {}).get("text") or (spec.get("screened") or {}).get("n"):
        return True
    if (spec.get("included") or {}).get("n") is not None:
        return True
    for key in ("presplit", "cohorts", "external"):
        if spec.get(key):
            return True
    return False


def validate(spec: dict) -> None:
    """canonical 结构校验；不合法就显式报错（不补字段、不回退）。"""
    if not isinstance(spec, dict):
        raise ValueError("Figure 1 spec 必须是 JSON 对象")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"spec.schema_version={spec.get('schema_version')!r}，需要 {SCHEMA_VERSION}；"
            "先走 spec.normalize"
        )
    if not str(spec.get("layout") or "").strip():
        raise ValueError("spec.layout 必须是非空字符串（来源标记）")
    if not _has_content(spec):
        raise ValueError("spec 没有任何可画的节点（screened/included/presplit/cohorts/external）")

    for key in ("presplit", "cohorts", "external"):
        entries = spec.get(key) or []
        ids = [str(entry.get("id") or "") for entry in entries]
        if len(set(ids)) != len(ids) or "" in ids:
            raise ValueError(f"{key} 的 id 必须唯一且非空")
    downsample = spec.get("downsample") or []
    arm_ids = {str(arm.get("id")) for arm in (spec.get("presplit") or [])}
    for entry in downsample:
        if arm_ids and str(entry.get("arm")) not in arm_ids:
            raise ValueError(f"downsample.arm={entry.get('arm')!r} 不在 presplit 里")

    pipeline = spec.get("pipeline") or {}
    balance = pipeline.get("balance")
    if balance not in (None, "", *BALANCE_KEYS):
        raise ValueError("pipeline.balance 必须是 downsample/smote 或留空")
    seen_kinds = []
    for step in pipeline.get("steps") or []:
        kind = str(step.get("kind") or "")
        if kind not in STEP_KINDS:
            raise ValueError(f"pipeline.steps kind={kind!r} 不在白名单 {STEP_KINDS}")
        seen_kinds.append(kind)
        items = [str(item) for item in (step.get("items") or [])]
        if kind == "models":
            unknown = [item for item in items if item not in MODEL_KEYS]
            if unknown:
                raise ValueError(f"models items 非法：{unknown}")
        if kind == "explain":
            unknown = [item for item in items if item not in EXPLAIN_KEYS]
            if unknown:
                raise ValueError(f"explain items 非法：{unknown}")


def to_json_text(spec: dict) -> str:
    """canonical spec -> 落盘 JSON 文本（保持 layout/overrides，不排序键）。"""
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
