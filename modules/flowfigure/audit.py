# -*- coding: utf-8 -*-
"""Figure 1 的 fail-closed n 审计（canonical spec v2）。

规则与旧 ``draw_strobe_flow_expanded.audit`` / ``figure_strobe_flow.audit_study``
一致：只校验已给出的数字；对不上就抛 :class:`AuditError`，调用方不得落盘。
本模块不猜数、不补数、不截断。
"""
from __future__ import annotations

from modules.flowfigure import spec as spec_mod


class AuditError(ValueError):
    """n 审计失败；调用方不得写任何项目文件。"""


def _n(value) -> int | None:
    return spec_mod.parse_n(value)


def _downsample_by_arm(spec: dict) -> dict[str, dict]:
    arms = spec.get("presplit") or []
    arm_ids = [arm["id"] for arm in arms]
    mapped: dict[str, dict] = {}
    for entry in spec.get("downsample") or []:
        arm_id = entry.get("arm")
        if arm_id not in arm_ids:
            raise AuditError(f"n-audit failed: downsample.arm={arm_id!r} 不在 presplit 里")
        mapped[arm_id] = entry
    return mapped


def audit(spec: dict) -> None:
    """按 layout 分派审计；失败抛 AuditError（消息含 ``n-audit failed``）。"""
    layout = spec.get("layout")
    if layout == spec_mod.LAYOUT_EXPANDED:
        _audit_expanded(spec)
    elif layout == spec_mod.LAYOUT_PUBLISHED:
        _audit_published(spec)
    elif layout == spec_mod.LAYOUT_STUDY:
        _audit_study(spec)
    else:
        raise AuditError(f"n-audit failed: 未知 layout={layout!r}")


# -- expanded（A'） -----------------------------------------------------------
def _audit_expanded(spec: dict) -> None:
    arms = spec.get("presplit") or []
    cohorts = spec.get("cohorts") or []
    downs = _downsample_by_arm(spec)
    problems: list[str] = []

    for index, arm in enumerate(arms, 1):
        label = arm.get("label") or f"arm {index}"
        arm_n = _n(arm.get("n"))
        if arm_n is None:
            problems.append(f"{label}: cannot parse n")
            continue
        pp = _n(arm.get("pp"))
        non_pp = _n(arm.get("non_pp"))
        if pp is not None and non_pp is not None and arm_n != pp + non_pp:
            problems.append(
                f"{label}: n ({arm_n}) != pp ({pp}) + non_pp ({non_pp})"
            )
        if index - 1 >= len(cohorts):
            continue
        cohort = cohorts[index - 1]
        cohort_n = _n(cohort.get("n"))
        if cohort_n is None:
            problems.append(f"{label}: final cohort n unparseable")
            continue
        down = downs.get(arm["id"])
        if down is not None:
            dropped = _n(down.get("n"))
            if dropped is None:
                problems.append(f"{label}: cannot parse downsample n")
            elif arm_n - dropped != cohort_n:
                problems.append(
                    f"{label}: arm n ({arm_n}) - downsample n ({dropped}) "
                    f"= {arm_n - dropped}, not cohort n ({cohort_n})"
                )
        elif arm_n != cohort_n:
            problems.append(
                f"{label}: arm n ({arm_n}) != cohort n ({cohort_n}) with no downsample"
            )

    for index, cohort in enumerate(cohorts, 1):
        label = cohort.get("label") or f"cohort {index}"
        cohort_n = _n(cohort.get("n"))
        if cohort_n is None:
            continue
        pp = _n(cohort.get("pp"))
        non_pp = _n(cohort.get("non_pp"))
        if pp is not None and non_pp is not None and cohort_n != pp + non_pp:
            problems.append(
                f"{label}: n ({cohort_n}) != pp ({pp}) + non_pp ({non_pp})"
            )

    screened_n = _n((spec.get("screened") or {}).get("n"))
    analyzed_n = _n((spec.get("analyzed") or {}).get("n"))
    arm_ns = [_n(arm.get("n")) for arm in arms]
    if screened_n is not None and None not in arm_ns:
        arm_total = sum(arm_ns)
        if screened_n != arm_total:
            problems.append(
                f"screened n ({screened_n}) != sum of pre-split arm n ({arm_total})"
            )
    cohort_ns = [_n(cohort.get("n")) for cohort in cohorts]
    if analyzed_n is not None and None not in cohort_ns:
        cohort_total = sum(cohort_ns)
        if cohort_total != analyzed_n:
            problems.append(
                f"sum of final cohort n ({cohort_total}) != analyzed n ({analyzed_n})"
            )

    pos_arms = [_n(arm.get("pp")) for arm in arms]
    pos_cohorts = [_n(cohort.get("pp")) for cohort in cohorts]
    if None not in pos_arms and None not in pos_cohorts:
        dropped_pos = sum(
            _n(entry.get("n")) or 0
            for entry in downs.values()
            if str(entry.get("class") or "non_pp") == "pp"
        )
        if sum(pos_cohorts) != sum(pos_arms) - dropped_pos:
            problems.append(
                f"sum of cohort pp ({sum(pos_cohorts)}) != sum of arm pp "
                f"({sum(pos_arms)}) - downsampled pp ({dropped_pos})"
            )
    neg_arms = [_n(arm.get("non_pp")) for arm in arms]
    neg_cohorts = [_n(cohort.get("non_pp")) for cohort in cohorts]
    if None not in neg_arms and None not in neg_cohorts:
        dropped_neg = sum(
            _n(entry.get("n")) or 0
            for entry in downs.values()
            if str(entry.get("class") or "non_pp") != "pp"
        )
        if sum(neg_cohorts) != sum(neg_arms) - dropped_neg:
            problems.append(
                f"sum of cohort non_pp ({sum(neg_cohorts)}) != sum of arm non_pp "
                f"({sum(neg_arms)}) - downsampled non_pp ({dropped_neg})"
            )

    if problems:
        raise AuditError(
            "n-audit failed — do not invent n:\n  " + "\n  ".join(problems)
        )


# -- published-a（A） ---------------------------------------------------------
def _audit_published(spec: dict) -> None:
    problems: list[str] = []
    screened_n = _n((spec.get("screened") or {}).get("n"))
    analyzed_n = _n((spec.get("analyzed") or {}).get("n"))
    if (spec.get("screened") or {}).get("text") and screened_n is None:
        problems.append("cannot parse n from screened")
    if (spec.get("analyzed") or {}).get("text") and analyzed_n is None:
        problems.append("cannot parse n from analyzed")

    exclusion = (spec.get("criteria") or {}).get("exclusion") or []
    missing = [i + 1 for i, item in enumerate(exclusion) if _n(item.get("n")) is None]
    if missing:
        problems.append("cannot parse n from exclusion item(s) " + ", ".join(map(str, missing)))
    if problems:
        raise AuditError("n-audit failed: " + "; ".join(problems) + ". Do not invent n.")

    if screened_n is not None and analyzed_n is not None:
        excl_sum = sum(_n(item.get("n")) or 0 for item in exclusion)
        if screened_n - excl_sum != analyzed_n:
            raise AuditError(
                f"n-audit failed: screened ({screened_n}) − Σ exclusion n ({excl_sum}) "
                f"= {screened_n - excl_sum}, not analyzed ({analyzed_n}). Do not invent n."
            )
    cohorts = spec.get("cohorts") or []
    cohort_ns = [_n(cohort.get("n")) for cohort in cohorts]
    if analyzed_n is not None and cohorts and None not in cohort_ns:
        total = sum(cohort_ns)
        if total != analyzed_n:
            raise AuditError(
                f"n-audit failed: Σ splits n ({total}) != analyzed ({analyzed_n}). "
                "Do not invent n."
            )


# -- study（含 Methods 行） ---------------------------------------------------
def _audit_study(spec: dict) -> None:
    problems: list[str] = []
    screened_n = _n((spec.get("screened") or {}).get("n"))
    analyzed_n = _n((spec.get("analyzed") or {}).get("n"))
    exclusion = (spec.get("criteria") or {}).get("exclusion") or []
    exclusions = [(item.get("reason") or "", _n(item.get("n"))) for item in exclusion]
    if exclusions:
        missing = [i + 1 for i, (_, n) in enumerate(exclusions) if n is None]
        if missing:
            problems.append(
                "cannot parse n from exclusion item(s) " + ", ".join(map(str, missing))
            )
        elif screened_n is not None and analyzed_n is not None:
            excl_sum = sum(n for _, n in exclusions if n is not None)
            if screened_n - excl_sum != analyzed_n:
                problems.append(
                    f"screened ({screened_n}) − Σ exclusion n ({excl_sum}) "
                    f"!= included_n ({analyzed_n})"
                )

    cohorts = spec.get("cohorts") or []
    cohort_ns = [_n(cohort.get("n")) for cohort in cohorts]
    if cohorts and None not in cohort_ns and analyzed_n is not None:
        total = sum(cohort_ns)
        if total != analyzed_n:
            problems.append(f"cohort n sum ({total}) != included_n ({analyzed_n})")
        pos = [_n(cohort.get("pp")) for cohort in cohorts]
        neg = [_n(cohort.get("non_pp")) for cohort in cohorts]
        if None not in pos and None not in neg:
            # 只在与 included 的阳性/阴性数同时存在时比较；canonical 不在 analyzed 里存这对数，
            # 因此这里只校验队列自身 n = pp + non_pp。
            for cohort in cohorts:
                n = _n(cohort.get("n"))
                pp = _n(cohort.get("pp"))
                non_pp = _n(cohort.get("non_pp"))
                if None not in (n, pp, non_pp) and n != pp + non_pp:
                    problems.append(
                        f"{cohort.get('label') or cohort.get('id')}: "
                        f"n ({n}) != pp ({pp}) + non_pp ({non_pp})"
                    )

    pipeline = spec.get("pipeline") or {}
    balance = pipeline.get("balance")
    if balance not in (None, "", *spec_mod.BALANCE_KEYS):
        problems.append("balance must be downsample, smote, or omitted")
    if balance in spec_mod.BALANCE_KEYS and pipeline.get("balance_n") not in (None, ""):
        if _n(pipeline.get("balance_n")) is None:
            problems.append("balance_n must be an integer when set")
    for key, allowed in (
        ("models", spec_mod.MODEL_KEYS),
        ("metrics", spec_mod.METRIC_KEYS),
        ("explain", spec_mod.EXPLAIN_KEYS),
    ):
        unknown = [item for item in (pipeline.get(key) or []) if item not in allowed]
        if unknown:
            problems.append(f"unknown {key}: " + ", ".join(map(str, unknown)))

    if problems:
        raise AuditError("n-audit failed: " + "; ".join(problems) + ". Do not invent n.")
