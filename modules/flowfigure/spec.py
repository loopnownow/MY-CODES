# -*- coding: utf-8 -*-
"""Figure 1 spec v2：canonical 结构、旧 spec 映射、校验与 JSON 写回。

单一源是 ``<project>/ref/pipeline/figure1_strobe.json``；本模块只做纯数据变换，
不读项目文件、不画图。canonical 结构::

    {
      "schema_version": 2,
      "layout": "expanded-split-downsample" | "study" | "published-a",
      "screened": {"text": str, "n": int | None},
      "criteria": {"inclusion": [str],
                   "exclusion": [{"reason": str, "n": int | None}]},
      "presplit": [{"id": "arm-0", "label": str,
                    "n": int | None, "pp": int | None, "non_pp": int | None}],
      "downsample": [{"arm": "arm-0", "n": int | None,
                      "class": "pp" | "non_pp", "reason": str}],
      "cohorts": [{"id": "cohort-0", "label": str,
                   "n": int | None, "pp": int | None, "non_pp": int | None}],
      "analyzed": {"text": str, "n": int | None},
      "pos_name": str,
      "notes": str,
      "overrides": {"boxes": {}, "edges": {}, "hidden": []},
      "font_size": 12.0
    }

旧 spec（没有 ``schema_version``）由字段形态自动映射：
``presplit``/``splits`` 形态 -> expanded；``splits`` -> published-a；
``kind == "study"`` / ``screened_n`` / ``included_n`` -> study。

未知顶级键原样保留；``normalize`` 幂等；``validate`` 显式报错，不做静默回退。
"""
from __future__ import annotations

import copy
import json
import re

# -- CONFIG -------------------------------------------------------------------
SCHEMA_VERSION = 2
TARGET_WIDTH_CM = 16.0
MAX_WIDTH_CM = 17.2
DEFAULT_FONT_SIZE_PT = 12.0
MARGIN_IN = 0.12
CM_PER_INCH = 2.54
DEFAULT_POS_NAME = "positive"

LAYOUT_EXPANDED = "expanded-split-downsample"
LAYOUT_STUDY = "study"
LAYOUT_PUBLISHED = "published-a"
LAYOUTS = (LAYOUT_EXPANDED, LAYOUT_STUDY, LAYOUT_PUBLISHED)

MODEL_KEYS = ("clinical", "radiomics", "plaque", "nomogram")
METRIC_KEYS = ("roc", "calibration", "dca", "nri", "idi")
EXPLAIN_KEYS = ("dag", "mediation")
BALANCE_KEYS = ("downsample", "smote")

_N_EQ = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
_LEADING_COUNT = re.compile(
    r"^\s*(\d+)\s+(?:patients?|subjects?|cases?|women|men)\b", re.I
)

# 旧 shape 里由 canonical 重建的键；其余键视为未知键并保留。
_REPLACED_KEYS = {
    "schema_version", "layout", "screened", "analyzed", "inclusion", "exclusion",
    "presplit", "arms", "splits", "cohorts", "downsample", "pos_name", "notes",
    "overrides", "font_size", "criteria", "screened_n", "included_n",
    "training", "test", "validation", "included_positive", "included_negative",
    "included_positive_label", "included_negative_label", "balance", "balance_n",
    "lasso", "models", "metrics", "explain",
}
# 条目内部由 canonical 重建的键；其余条目键保留。
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


def unwrap(raw) -> dict:
    """取出真正的 spec：兼容旧的 ``{"spec": {...}, "meta": ...}`` 包装。"""
    if not isinstance(raw, dict):
        raise ValueError("Figure 1 spec 必须是 JSON 对象")
    if isinstance(raw.get("spec"), dict) and "layout" not in raw:
        return raw["spec"]
    return raw


def infer_layout(raw: dict) -> str:
    """按字段形态推断 layout；显式 layout 非法时直接报错。"""
    layout = raw.get("layout")
    if layout is not None and layout != "":
        if layout not in LAYOUTS:
            raise ValueError(
                f"未知 spec.layout={layout!r}；只支持 {', '.join(LAYOUTS)}"
            )
        return str(layout)
    if "presplit" in raw or "arms" in raw:
        return LAYOUT_EXPANDED
    if raw.get("kind") == "study" or any(
        key in raw for key in (
            "screened_n", "included_n", "training", "test", "validation",
            "pipeline", "balance", "models", "metrics", "explain",
        )
    ):
        return LAYOUT_STUDY
    if "splits" in raw:
        return LAYOUT_PUBLISHED
    raise ValueError(
        "spec 缺少 layout，且无法从字段推断（presplit/splits/study 三选一）"
    )


# -- field mapping ------------------------------------------------------------
def _screened(src: dict) -> dict:
    raw = src.get("screened")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        n = parse_n(raw)
        return {"text": text, "n": n}
    if raw not in (None, ""):
        text = str(raw).strip()
        return {"text": text, "n": parse_n(text)}
    n = parse_n(src.get("screened_n"))
    period = str(src.get("period") or "").strip()
    if n is None and not period:
        return {"text": "", "n": None}
    top = f"{n} patients screened" if n is not None else "Patients screened"
    return {"text": f"{period}\n{top}" if period else top, "n": n}


def _analyzed(src: dict, layout: str) -> dict:
    raw = src.get("analyzed")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("label") or "").strip()
        return {"text": text, "n": parse_n(raw)}
    if raw not in (None, ""):
        text = str(raw).strip()
        return {"text": text, "n": parse_n(text)}
    if layout == LAYOUT_STUDY:
        n = parse_n(src.get("included_n"))
        text = f"Patients included\n(n = {n})" if n is not None else "Patients included"
        return {"text": text, "n": n}
    return {"text": "", "n": None}


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
        item = {key: copy.deepcopy(val) for key, val in (entry.items() if isinstance(entry, dict) else [])
                if key not in _ENTRY_REBUILT}
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


def _cohorts(src: dict, layout: str) -> list[dict]:
    if layout == LAYOUT_STUDY:
        raw = src.get("cohorts")
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
    raw = src.get("splits")
    if raw is None:
        raw = src.get("cohorts")
    return _cohort_entries(raw)


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
    for index, entry in enumerate(items):
        entry = dict(entry or {})
        target = entry.get("arm")
        arm_id = ids[0]
        if isinstance(target, int) and not isinstance(target, bool):
            pos = target
            if not 0 <= pos < len(ids):
                raise ValueError(f"downsample.arm {target!r} 越界")
            arm_id = ids[pos]
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


def _pipeline(src: dict) -> dict:
    raw = src.get("pipeline") if isinstance(src.get("pipeline"), dict) else {}
    balance = raw.get("balance", src.get("balance"))
    balance = str(balance).strip() if balance not in (None, "") else None
    return {
        "balance": balance,
        "balance_n": parse_n(raw.get("balance_n", src.get("balance_n"))),
        "lasso": bool(raw.get("lasso", src.get("lasso"))),
        "models": [str(key) for key in (raw.get("models", src.get("models")) or [])],
        "metrics": [str(key) for key in (raw.get("metrics", src.get("metrics")) or [])],
        "explain": [str(key) for key in (raw.get("explain", src.get("explain")) or [])],
    }


def normalize(raw) -> dict:
    """旧 spec / study spec -> canonical v2；未知键保留，结果幂等。"""
    src = copy.deepcopy(unwrap(raw))
    layout = infer_layout(src)

    out = {
        key: copy.deepcopy(val)
        for key, val in src.items()
        if key not in _REPLACED_KEYS and key != "pipeline"
    }
    if layout != LAYOUT_STUDY and "pipeline" in src:
        # 非 study 版式不画 pipeline；保留下来交给 validate 显式报错。
        out["pipeline"] = copy.deepcopy(src["pipeline"])
    out["schema_version"] = SCHEMA_VERSION
    out["layout"] = layout
    out["screened"] = _screened(src)
    out["criteria"] = _criteria(src)
    presplit = _presplit(src) if layout == LAYOUT_EXPANDED else []
    out["presplit"] = presplit
    out["downsample"] = _downsample(src, presplit) if layout == LAYOUT_EXPANDED else []
    out["cohorts"] = _cohorts(src, layout)
    out["analyzed"] = _analyzed(src, layout)
    out["pos_name"] = str(
        src.get("pos_name") or src.get("included_positive_label") or DEFAULT_POS_NAME
    ).strip()
    out["notes"] = str(src.get("notes") or "")
    out["overrides"] = copy.deepcopy(src.get("overrides") or {"boxes": {}, "edges": {}, "hidden": []})
    out["font_size"] = float(src.get("font_size") or DEFAULT_FONT_SIZE_PT)
    if layout == LAYOUT_STUDY:
        out["pipeline"] = _pipeline(src)
    return out


def validate(spec: dict) -> None:
    """canonical 结构校验；不合法就显式报错（不补字段、不回退）。"""
    if not isinstance(spec, dict):
        raise ValueError("Figure 1 spec 必须是 JSON 对象")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"spec.schema_version={spec.get('schema_version')!r}，需要 {SCHEMA_VERSION}；"
            "先走 spec.normalize"
        )
    layout = spec.get("layout")
    if layout not in LAYOUTS:
        raise ValueError(f"未知 spec.layout={layout!r}")
    if layout != LAYOUT_STUDY and spec.get("pipeline"):
        raise ValueError(f"layout {layout!r} 不画 pipeline 行；改用 study 或删掉该键")

    arms = spec.get("presplit") or []
    cohorts = spec.get("cohorts") or []
    for index, arm in enumerate(arms):
        if not isinstance(arm, dict) or not arm.get("id"):
            raise ValueError(f"presplit[{index}] 缺少 id")
    for index, cohort in enumerate(cohorts):
        if not isinstance(cohort, dict) or not cohort.get("id"):
            raise ValueError(f"cohorts[{index}] 缺少 id")
    arm_ids = [arm["id"] for arm in arms]
    if len(set(arm_ids)) != len(arm_ids):
        raise ValueError("presplit 的 id 不唯一")
    cohort_ids = [cohort["id"] for cohort in cohorts]
    if len(set(cohort_ids)) != len(cohort_ids):
        raise ValueError("cohorts 的 id 不唯一")

    if layout == LAYOUT_EXPANDED:
        if not arms:
            raise ValueError("expanded 版式至少需要 1 个 presplit 臂")
        if not cohorts:
            raise ValueError("expanded 版式至少需要 1 个 cohort")
        if len(arms) != len(cohorts):
            raise ValueError(
                f"{len(arms)} 个 presplit 臂 vs {len(cohorts)} 个 cohort；按索引配对，数量必须一致"
            )
        known = set(arm_ids)
        for entry in spec.get("downsample") or []:
            if entry.get("arm") not in known:
                raise ValueError(f"downsample.arm={entry.get('arm')!r} 不在 presplit 里")
    elif layout == LAYOUT_PUBLISHED:
        if not (spec.get("screened") or {}).get("text") and not (spec.get("analyzed") or {}).get("text"):
            raise ValueError("published-a 版式至少需要 screened 或 analyzed 文本")
    else:  # study
        if not cohorts and spec.get("analyzed", {}).get("n") is None:
            raise ValueError("study 版式至少需要 cohorts 或 analyzed.n")


def to_json_text(spec: dict) -> str:
    """canonical spec -> 落盘 JSON 文本（保持 layout/overrides，不排序键）。"""
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
