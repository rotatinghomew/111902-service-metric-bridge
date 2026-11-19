"""汇总口径与比较注记。

分母规则：
- 旧口径（窗口责任）：分母按组件计，一个联办件的每个责任窗口各计一件（原约定）；
  组件按窗口划分，跨窗口合计不重复。
- 新口径（居民等待）：分母按联办件计，同一联办件只算一次；
  按涉及窗口切片时各片互相重叠，不允许勾选两个窗口后把两片按行相加。
比较规则：差异可能来自事项组合、适用日期或口径，不得把全部变动归为办得更快了。
"""
from __future__ import annotations

from .measures import STATUS_OK

OLD_DENOMINATOR = "窗口责任分母按组件计：一个联办件的每个责任窗口各计一件（遵从原约定）"
NEW_DENOMINATOR = "居民等待分母按联办件计：同一联办件只算一次"
SLICE_NOTE = "居民等待按涉及窗口切片，各片互相重叠，不可按行相加；合计按联办件去重"


def _case_index(source):
    return {r["case"]: r for r in source.rows("cases")}


def _in_scope(case_row, district, period):
    if district and district != "全市" and case_row["district"] != district:
        return False
    if period and case_row["period"] != period:
        return False
    return True


def old_cells(source, old_items, district="全市", period=None) -> list:
    """旧口径单元：(period, district, service, window)。未能重算的不入分母，单列为缺口。"""
    cases = _case_index(source)
    cells: dict[tuple, dict] = {}
    for it in old_items:
        case_row = cases[it.case]
        if not _in_scope(case_row, district, period):
            continue
        key = (case_row["period"], case_row["district"], case_row["service"], it.window)
        cell = cells.setdefault(key, {
            "period": key[0], "district": key[1], "service": key[2], "window": key[3],
            "total": 0, "timely": 0, "items": [], "gaps": [],
        })
        if it.status == STATUS_OK:
            cell["total"] += 1
            cell["timely"] += 1 if it.timely else 0
            cell["items"].append(it.item)
        else:
            cell["gaps"].append(it.item)
    return [cells[k] for k in sorted(cells)]


def new_cells(source, new_items, district="全市", period=None) -> list:
    """新口径单元：(period, district, service)。分母为去重后的联办件。"""
    cases = _case_index(source)
    cells: dict[tuple, dict] = {}
    for it in new_items:
        case_row = cases[it.case]
        if not _in_scope(case_row, district, period):
            continue
        key = (case_row["period"], case_row["district"], case_row["service"])
        cell = cells.setdefault(key, {
            "period": key[0], "district": key[1], "service": key[2],
            "total": 0, "timely": 0, "items": [], "gaps": [],
        })
        if it.status == STATUS_OK:
            cell["total"] += 1
            cell["timely"] += 1 if it.timely else 0
            cell["items"].append(it.item)
        else:
            cell["gaps"].append(it.item)
    return [cells[k] for k in sorted(cells)]


def new_cells_by_window(source, new_items, cutoff, district="全市", period=None) -> dict:
    """居民等待按涉及窗口切片。切片仅供拆开查看，合计必须按联办件去重。"""
    cases = _case_index(source)
    links = source.effective_links(cutoff)
    windows_of: dict[str, set] = {}
    for (case, _comp), link in links.items():
        windows_of.setdefault(case, set()).add(link["window"])
    slices: dict[str, dict] = {}
    for it in new_items:
        if it.status != STATUS_OK:
            continue
        case_row = cases[it.case]
        if not _in_scope(case_row, district, period):
            continue
        for w in sorted(windows_of.get(it.case, [])):
            sl = slices.setdefault(w, {"window": w, "total": 0, "timely": 0, "items": []})
            sl["total"] += 1
            sl["timely"] += 1 if it.timely else 0
            sl["items"].append(it.item)
    combined_items = sorted({iid for sl in slices.values() for iid in sl["items"]})
    by_id = {it.item: it for it in new_items}
    combined = {
        "total": len(combined_items),
        "timely": sum(1 for i in combined_items if by_id[i].timely),
        "items": combined_items,
    }
    return {"slices": {w: slices[w] for w in sorted(slices)}, "combined": combined, "note": SLICE_NOTE}


def legacy_cells(source, district="全市") -> list:
    """只报了窗口月度汇总的旧批次：保持有效，但不推断到单个联办件。"""
    rows = []
    for row in source.rows("legacy_cells"):
        if district and district != "全市" and row["district"] != district:
            continue
        rows.append({
            "id": f"legacy:{row['period']}/{row['district']}/{row['service']}/{row['window']}",
            **row,
            "note": "既有旧汇总仍有效；仅月度汇总，不能推断至单个联办件，也不能当作居民新口径",
        })
    return rows


def compare_items(a: dict, b: dict) -> dict:
    """比较两个比较项：只标注差异来源，不把变动归结为"办得更快了"。

    a、b 需带 id / kind / convention / period / facts_at 等元数据。
    """
    factors = []
    if a.get("kind") != b.get("kind"):
        factors.append("资料粒度不同：逐件重算与月度汇总不能互推")
    if a.get("convention") != b.get("convention"):
        factors.append(
            f"口径不同：{a.get('convention')} 与 {b.get('convention')} 的起止定义、排除区间、日历不同"
        )
    if a.get("calendar") and b.get("calendar") and a["calendar"] != b["calendar"]:
        factors.append(f"适用日历不同：{a['calendar']} 与 {b['calendar']}")
    if a.get("period") and b.get("period") and a["period"] != b["period"]:
        factors.append("适用日期不同：期间不同，事项组合可能不同")
    if a.get("facts_at") and b.get("facts_at") and a["facts_at"] != b["facts_at"]:
        factors.append("事实版本不同：其间可能有日历更正或联办关系撤销，见本期更正")
    blocking = any(("口径不同" in f) or ("粒度不同" in f) for f in factors)
    if blocking:
        verdict = "不可直接比较"
    elif factors:
        verdict = "可并列，需注记差异来源"
    else:
        verdict = "可比较"
    return {
        "a": a["id"], "b": b["id"], "verdict": verdict, "factors": factors,
        "note": "差异可能来自事项组合、适用日期或口径；不得把全部变动归为办得更快了",
    }
