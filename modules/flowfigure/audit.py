# -*- coding: utf-8 -*-
"""Figure 1 的 fail-closed n 审计（canonical spec v3，按块校验）。

只校验已给出的数字；对不上就抛 :class:`AuditError`，调用方不得落盘。
本模块不猜数、不补数、不截断。``layout`` 只是来源标记，审计不按 layout 分派。
"""
from __future__ import annotations

from modules.flowfigure import spec as spec_mod


class AuditError(ValueError):
    """n 审计失败；调用方不得写任何项目文件。"""


def _n(value) -> int | None:
    return spec_mod.parse_n(value)


def _audit_node(node: dict, label: str, problems: list[str]) -> None:
    n = _n(node.get("n"))
    pp = _n(node.get("pp"))
    non_pp = _n(node.get("non_pp"))
    if n is not None and pp is not None and non_pp is not None and n != pp + non_pp:
        problems.append(f"{label}: n ({n}) != pp ({pp}) + non_pp ({non_pp})")


def audit(spec: dict) -> list[str]:
    """按块审计 canonical v3；失败抛 AuditError，可恢复的问题以 warning 列表返回。"""
    problems: list[str] = []
    warnings: list[str] = []
    screened = spec.get("screened") or {}
    included = spec.get("included") or {}
    analyzed = spec.get("analyzed") or {}
    criteria = spec.get("criteria") or {}
    exclusions = criteria.get("exclusion") or []
    presplit = spec.get("presplit") or []
    cohorts = spec.get("cohorts") or []
    downsample = spec.get("downsample") or []
    external = spec.get("external") or []

    # -- exclusion 块：只有给了排除项才查 screened − Σexcl --
    if exclusions:
        missing = [i + 1 for i, item in enumerate(exclusions) if _n(item.get("n")) is None]
        if missing:
            warnings.append(
                "exclusion 缺 n（第 " + "、".join(map(str, missing))
                + " 条），跳过 screened−Σexclusion 校验（不猜数）"
            )
        else:
            grouped = _n(screened.get("n"))
            target = _n(included.get("n"))
            if target is None:
                target = _n(analyzed.get("n"))
            if grouped is not None and target is not None:
                excl_sum = sum(_n(item.get("n")) or 0 for item in exclusions)
                if grouped - excl_sum != target:
                    problems.append(
                        f"screened ({grouped}) − Σ exclusion n ({excl_sum}) = "
                        f"{grouped - excl_sum}, not {target}"
                    )

    # -- presplit 块：有臂才查臂合计与降采样算术 --
    arm_ns = [_n(arm.get("n")) for arm in presplit]
    if presplit:
        for index, arm in enumerate(presplit, 1):
            _audit_node(arm, arm.get("label") or f"arm {index}", problems)
            if arm_ns[index - 1] is None:
                problems.append(f"{arm.get('label') or 'arm ' + str(index)}: cannot parse n")
        screened_n = _n(screened.get("n"))
        if screened_n is not None and None not in arm_ns:
            if screened_n != sum(arm_ns):
                problems.append(
                    f"screened n ({screened_n}) != sum of pre-split arm n ({sum(arm_ns)})"
                )
        if cohorts and len(cohorts) == len(presplit):
            downs = {str(entry.get("arm")): entry for entry in downsample}
            for index, arm in enumerate(presplit):
                arm_n = arm_ns[index]
                cohort_n = _n(cohorts[index].get("n"))
                if arm_n is None or cohort_n is None:
                    continue
                value = downs.get(str(arm.get("id")))
                if value is not None:
                    dropped = _n(value.get("n"))
                    if dropped is None:
                        problems.append(f"{arm.get('label') or 'arm'}: cannot parse downsample n")
                    elif arm_n - dropped != cohort_n:
                        problems.append(
                            f"{arm.get('label') or 'arm'}: arm n ({arm_n}) - "
                            f"downsample n ({dropped}) = {arm_n - dropped}, "
                            f"not cohort n ({cohort_n})"
                        )
                elif arm_n != cohort_n:
                    problems.append(
                        f"{arm.get('label') or 'arm'}: arm n ({arm_n}) != "
                        f"cohort n ({cohort_n}) with no downsample"
                    )

    # -- cohort 块 --
    for index, cohort in enumerate(cohorts, 1):
        _audit_node(cohort, cohort.get("label") or f"cohort {index}", problems)
    cohort_ns = [_n(cohort.get("n")) for cohort in cohorts]
    cohort_target = _n(analyzed.get("n"))
    if cohort_target is None:
        cohort_target = _n(included.get("n"))
    if cohorts and cohort_target is not None and None not in cohort_ns:
        if sum(cohort_ns) != cohort_target:
            problems.append(
                f"sum of cohort n ({sum(cohort_ns)}) != analyzed n ({cohort_target})"
            )
    pos_cohorts = [_n(cohort.get("pp")) for cohort in cohorts]
    neg_cohorts = [_n(cohort.get("non_pp")) for cohort in cohorts]
    pos_arms = [_n(arm.get("pp")) for arm in presplit]
    neg_arms = [_n(arm.get("non_pp")) for arm in presplit]
    if cohorts and None not in pos_cohorts:
        target_pp = _n(analyzed.get("pp"))
        if target_pp is None:
            target_pp = _n(included.get("pp"))
        if target_pp is not None and sum(pos_cohorts) != target_pp:
            problems.append(
                f"sum of cohort pp ({sum(pos_cohorts)}) != pp target ({target_pp})"
            )
    if cohorts and None not in neg_cohorts:
        target_non = _n(analyzed.get("non_pp"))
        if target_non is None:
            target_non = _n(included.get("non_pp"))
        if target_non is not None and sum(neg_cohorts) != target_non:
            problems.append(
                f"sum of cohort non_pp ({sum(neg_cohorts)}) != non_pp target ({target_non})"
            )
    if presplit and cohorts and len(presplit) == len(cohorts):
        dropped_pos = sum(
            _n(entry.get("n")) or 0
            for entry in downsample
            if str(entry.get("class") or "non_pp") == "pp"
        )
        dropped_neg = sum(
            _n(entry.get("n")) or 0
            for entry in downsample
            if str(entry.get("class") or "non_pp") != "pp"
        )
        if None not in pos_arms and None not in pos_cohorts:
            if sum(pos_cohorts) != sum(pos_arms) - dropped_pos:
                problems.append(
                    f"sum of cohort pp ({sum(pos_cohorts)}) != sum of arm pp "
                    f"({sum(pos_arms)}) - downsampled pp ({dropped_pos})"
                )
        if None not in neg_arms and None not in neg_cohorts:
            if sum(neg_cohorts) != sum(neg_arms) - dropped_neg:
                problems.append(
                    f"sum of cohort non_pp ({sum(neg_cohorts)}) != sum of arm non_pp "
                    f"({sum(neg_arms)}) - downsampled non_pp ({dropped_neg})"
                )

    # -- included / analyzed / external 自身一致性 --
    _audit_node(included, "included", problems)
    _audit_node(analyzed, "analyzed", problems)
    for index, entry in enumerate(external, 1):
        _audit_node(entry, entry.get("label") or f"external {index}", problems)

    # -- pipeline 白名单 --
    pipeline = spec.get("pipeline") or {}
    balance = pipeline.get("balance")
    if balance not in (None, "", *spec_mod.BALANCE_KEYS):
        problems.append("balance must be downsample, smote, or omitted")
    if balance in spec_mod.BALANCE_KEYS and pipeline.get("balance_n") not in (None, ""):
        if _n(pipeline.get("balance_n")) is None:
            problems.append("balance_n must be an integer when set")
    for step in pipeline.get("steps") or []:
        kind = str(step.get("kind") or "")
        if kind not in spec_mod.STEP_KINDS:
            problems.append(f"unknown pipeline step kind: {kind}")
        items = [str(item) for item in (step.get("items") or [])]
        if kind == "models":
            unknown = [item for item in items if item not in spec_mod.MODEL_KEYS]
            if unknown:
                problems.append("unknown models items: " + ", ".join(unknown))
        if kind == "explain":
            unknown = [item for item in items if item not in spec_mod.EXPLAIN_KEYS]
            if unknown:
                problems.append("unknown explain items: " + ", ".join(unknown))

    if problems:
        raise AuditError("n-audit failed: " + "; ".join(problems) + ". Do not invent n.")
    return warnings
