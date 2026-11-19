"""覆盖说明：能重算的范围、不能重算的缺口、既有汇总与可比性注记。

覆盖说明是新考核的独立组成，不与旧考核互作替代。
"""
from __future__ import annotations

from .measures import NEW_RULE, OLD_RULE, STATUS_GAP, STATUS_NA, STATUS_OK
from .metrics import legacy_cells
from .source import fmt_moment

COMPARABILITY_NOTES = [
    "旧季完成率按窗口旧口径解释保留；居民新口径自规则生效日起考核，两者起止定义与排除区间不同",
    "只知道责任窗口超时率，不能还原居民超时率；居民等待以联办件为单位另行计算",
    "比较差异可能来自事项组合、适用日期或口径，不得把全部变动归为办得更快了",
    "旧汇总批次的等待与今年新口径测到的等待不是同一回事，不为让曲线连得上而并轨",
]


def _gap_entry(it):
    return {"item": it.item, "reasons": it.reasons, "missing": it.missing}


def build_coverage(source, cutoff, period, district, old_items, new_items) -> dict:
    """一个收件方范围（district 或全市）一个期间的覆盖说明。"""
    cases = {r["case"]: r for r in source.rows("cases")}

    def in_scope(case):
        row = cases[case]
        if district != "全市" and row["district"] != district:
            return False
        return row["period"] == period

    old_scope = [it for it in old_items if in_scope(it.case)]
    new_scope = [it for it in new_items if in_scope(it.case)]
    case_ids = [c for c, r in cases.items() if in_scope(c)]
    no_links = [
        it.case for it in new_scope
        if it.status == STATUS_GAP and "缺少有效联办关系" in it.reasons
    ]
    revoked = [
        {
            "case": r["case"], "component": r["component"], "window": r["window"],
            "received": r["received"],
            "note": "该联办关系已撤销，撤销后口径截点下不计入任何窗口责任",
        }
        for r in source.revoked_links(cutoff)
        if r["case"] in case_ids
    ]
    revisions = []
    for row in source.rows("calendar_revisions"):
        revisions.append({
            "calendar": row["calendar"], "applies_to": row["applies_to"],
            "affected": row["affected"], "state": row["state"], "recorded": row["recorded"],
        })
    revisions.sort(key=lambda r: (r["affected"], r["recorded"]))
    return {
        "part": "覆盖说明",
        "facts_at": fmt_moment(cutoff) if not isinstance(cutoff, str) else cutoff,
        "period": period,
        "district": district,
        "rules": [source.rule(OLD_RULE), source.rule(NEW_RULE)],
        "旧口径": {
            "应到组件": len(old_scope),
            "已重算": sum(1 for it in old_scope if it.status == STATUS_OK),
            "未能重算": [_gap_entry(it) for it in old_scope if it.status == STATUS_GAP],
        },
        "新口径": {
            "应到案件": len(new_scope),
            "已重算": sum(1 for it in new_scope if it.status == STATUS_OK),
            "未能重算": [_gap_entry(it) for it in new_scope if it.status == STATUS_GAP],
            "不适用": [_gap_entry(it) for it in new_scope if it.status == STATUS_NA],
        },
        "无联办关系案件": no_links,
        "已撤销联办关系": revoked,
        "日历更正": revisions,
        "既有汇总": legacy_cells(source, district),
        "可比性注记": list(COMPARABILITY_NOTES),
    }
