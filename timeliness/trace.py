"""追溯：经办人从任一个退回的比较项，查到不支持它的历史材料或适用口径。"""
from __future__ import annotations

from .measures import STATUS_OK, collect_items, measure_case, measure_component
from .metrics import legacy_cells


def trace_item(source, item_id: str, cutoff) -> dict:
    """返回一个比较项的支撑/缺口说明。"""
    if item_id.startswith("old:"):
        return _trace_old(source, item_id[4:], cutoff)
    if item_id.startswith("new:"):
        return _trace_new(source, item_id[4:], cutoff)
    if item_id.startswith("legacy:"):
        return _trace_legacy(source, item_id)
    raise ValueError(f"无法识别的比较项 {item_id}")


def _fmt_item(it) -> dict:
    d = it.to_dict()
    d["结论"] = (
        f"{it.days} 工作日（时限 {it.limit}，{'及时' if it.timely else '超时'}）"
        if it.status == STATUS_OK else "；".join(it.reasons)
    )
    return d


def _trace_old(source, component: str, cutoff) -> dict:
    links = source.effective_links(cutoff)
    for (case, comp), link in links.items():
        if comp == component:
            item = measure_component(source, link, cutoff)
            return {"item": item.item, "状态": item.status, "明细": _fmt_item(item)}
    rows = source.link_rows(component=component)
    if rows:
        last = max(rows, key=lambda r: r["received"])
        return {
            "item": f"old:{component}",
            "状态": "不适用",
            "明细": {
                "reasons": [f"联办关系在口径截点无效（最后操作 {last['operation']}，received {last['received']}）"],
                "missing": [f"{component} 在当前口径截点不属于任何联办件，不进入窗口责任分母"],
                "evidence": [
                    f"case_links: {r['case']}/{r['component']} {r['operation']}（received {r['received']}）"
                    for r in rows
                ],
            },
        }
    return {
        "item": f"old:{component}",
        "状态": "未能重算",
        "明细": {"reasons": ["缺少联办关系"], "missing": [f"{component} 没有任何 case_links 记录"]},
    }


def _trace_new(source, case: str, cutoff) -> dict:
    links = source.effective_links(cutoff)
    case_links = [l for (c, _comp), l in links.items() if c == case]
    item = measure_case(source, source.case_row(case), case_links, cutoff)
    return {"item": item.item, "状态": item.status, "明细": _fmt_item(item)}


def _trace_legacy(source, item_id: str) -> dict:
    for row in legacy_cells(source, "全市"):
        if row["id"] == item_id:
            return {
                "item": item_id,
                "状态": "仅汇总资料",
                "明细": {
                    **row,
                    "missing": ["该批次只有窗口月度汇总，缺少逐件回执"],
                    "reasons": [
                        "不能推断至单个联办件",
                        "不能当作居民新口径与今年测量值并轨",
                    ],
                },
            }
    raise KeyError(f"没有汇总批次 {item_id}")
