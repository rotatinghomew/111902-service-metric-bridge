"""逐件重算：旧约定（窗口责任时钟）与新版（居民等待区间）。

- 旧口径：窗口受理 -> 窗口完成的工作日之差，排除窗口有效等待补件（窗口暂停/恢复）区间；
  日历用规则指定的旧历，适用对象为责任窗口。
- 新口径：联办主体首次完整材料（材料齐备）-> 最终结案（联合结案）的工作日之差，
  只排除整件获准暂停（全件暂停/恢复）区间；窗口暂停事件本身不能充当全件暂停凭据；
  日历用居民历，适用对象为联办。
- 完结或暂停事实不能建立时标为未能重算，并列出缺口证据。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .source import parse_day
from .workcal import WorkCalendar

OLD_RULE = "窗口旧口径"
NEW_RULE = "居民新口径"

ACCEPT = "窗口受理"
READY = "材料齐备"
W_PAUSE = "窗口暂停"
W_RESUME = "窗口恢复"
W_DONE = "窗口完成"
CLOSED = "联合结案"
C_PAUSE = "全件暂停"
C_RESUME = "全件恢复"

STATUS_OK = "已重算"
STATUS_GAP = "未能重算"
STATUS_NA = "不适用"


@dataclass
class Item:
    """一个比较项：已重算的数值，或未能重算的原因与缺口。证据链随项携带。"""

    item: str
    kind: str  # old / new
    status: str
    convention: str = ""
    case: str = ""
    component: str = ""
    window: str = ""
    calendar: str = ""
    scope: str = ""
    limit: int | None = None
    start: str = ""
    end: str = ""
    days: int | None = None
    excluded_days: int = 0
    timely: bool | None = None
    pauses: list = field(default_factory=list)
    reasons: list = field(default_factory=list)   # 未能重算/不适用原因
    missing: list = field(default_factory=list)   # 缺口：缺少的历史材料
    evidence: list = field(default_factory=list)  # 支撑证据（来源行）
    notes: list = field(default_factory=list)     # 口径注记

    def to_dict(self) -> dict:
        return asdict(self)


def _pair_pauses(events, pause_type: str, resume_type: str):
    """按时间配对暂停/恢复；返回 (区间列表, 是否破碎)。区间左闭右开。"""
    pauses, open_at, broken = [], None, False
    for e in events:
        kind = e["event"]
        if kind == pause_type:
            if open_at is not None:
                broken = True
                break
            open_at = parse_day(e["at"])
        elif kind == resume_type:
            if open_at is None:
                broken = True
                break
            pauses.append((open_at, parse_day(e["at"])))
            open_at = None
    if open_at is not None:
        broken = True
    return pauses, broken


def measure_component(source, link: dict, cutoff) -> Item:
    """旧口径：一个有效联办关系下的组件在责任窗口的用时。"""
    component, window, case = link["component"], link["window"], link["case"]
    rule = source.rule(OLD_RULE)
    item = Item(
        item=f"old:{component}", kind="old", status=STATUS_OK,
        convention=OLD_RULE, case=case, component=component, window=window,
        calendar=rule["calendar"], scope=window, limit=rule["limit_days"],
    )
    item.evidence.append(
        f"case_links: {case}/{component} 关联 {window}（received {link['received']}）"
    )
    events = source.events_of(component)
    accepts = [e for e in events if e["event"] == ACCEPT]
    dones = [e for e in events if e["event"] == W_DONE]
    if not accepts:
        item.reasons.append("受理事实不能建立")
        item.missing.append(f"{component} 缺少窗口受理回执")
    if not dones:
        item.reasons.append("完结事实不能建立")
        item.missing.append(f"{component} 缺少窗口完成回执")
    if item.reasons:
        item.status = STATUS_GAP
        return item
    start = parse_day(min(e["at"] for e in accepts))
    end = parse_day(min(e["at"] for e in dones))
    item.evidence.append(f"events: {component} 窗口受理 {start.isoformat()}")
    item.evidence.append(f"events: {component} 窗口完成 {end.isoformat()}")
    pauses, broken = _pair_pauses(events, W_PAUSE, W_RESUME)
    if broken:
        item.status = STATUS_GAP
        item.reasons.append("暂停事实不能建立")
        item.missing.append(f"{component} 的窗口暂停与窗口恢复回执不成对")
        return item
    if end < start:
        item.status = STATUS_GAP
        item.reasons.append("时序矛盾")
        item.missing.append(f"{component} 窗口完成 {end} 早于窗口受理 {start}")
        return item
    cal = WorkCalendar(source, rule["calendar"], window, cutoff)
    clipped = []
    for s, e in pauses:
        s2, e2 = max(s, start), min(e, end)
        if s2 < e2:
            clipped.append((s2, e2))
            item.evidence.append(f"events: {component} 窗口暂停 {s.isoformat()} -> 恢复 {e.isoformat()}")
    total = cal.workdays_between(start, end)
    excluded = cal.workdays_in_intervals(clipped)
    item.start, item.end = start.isoformat(), end.isoformat()
    item.pauses = [[s.isoformat(), e.isoformat()] for s, e in clipped]
    item.days = total - excluded
    item.excluded_days = excluded
    item.timely = item.days <= rule["limit_days"]
    for row in cal.applied_revisions(start, end):
        item.evidence.append(
            "calendar_revisions: "
            f"{row['calendar']}/{row['applies_to']} {row['affected']}={row['state']}"
            f"（recorded {row['recorded']}）"
        )
    return item


def measure_case(source, case_row: dict, links: list, cutoff) -> Item:
    """新口径：一个联办件的居民等待区间（首次完整材料 -> 最终结案）。"""
    case = case_row["case"]
    rule = source.rule(NEW_RULE)
    item = Item(
        item=f"new:{case}", kind="new", status=STATUS_OK,
        convention=NEW_RULE, case=case,
        calendar=rule["calendar"], scope="联办", limit=rule["limit_days"],
    )
    if not links:
        item.status = STATUS_GAP
        item.reasons.append("缺少有效联办关系")
        item.missing.append(f"{case} 在口径截点没有有效的联办关系（case_links）")
        return item
    windows = sorted({l["window"] for l in links})
    item.window = "/".join(windows)
    for l in links:
        item.evidence.append(
            f"case_links: {case}/{l['component']} 关联 {l['window']}（received {l['received']}）"
        )
    readies, closed, case_pause_events = [], [], []
    for l in links:
        comp = l["component"]
        for e in source.events_of(comp):
            kind = e["event"]
            if kind == READY:
                readies.append((comp, e))
            elif kind == CLOSED:
                closed.append((comp, e))
            elif kind in (C_PAUSE, C_RESUME):
                case_pause_events.append(e)
            elif kind in (W_PAUSE, W_RESUME):
                item.notes.append(
                    f"{comp} 的{kind}（{e['at']}）是窗口事件，不能充当全件暂停凭据"
                )
    if not readies:
        item.reasons.append("完整申请事实不能建立")
        item.missing.append(f"{case} 各组件均缺少材料齐备回执")
    if not closed:
        item.reasons.append("完结事实不能建立")
        item.missing.append(f"{case} 缺少联合结案回执")
    if item.reasons:
        item.status = STATUS_GAP
        return item
    first_ready = min(readies, key=lambda x: x[1]["at"])
    first_close = min(closed, key=lambda x: x[1]["at"])
    start = parse_day(first_ready[1]["at"])
    end = parse_day(first_close[1]["at"])
    item.evidence.append(f"events: 首次材料齐备 {start.isoformat()}（{first_ready[0]}）")
    item.evidence.append(f"events: 联合结案 {end.isoformat()}（{first_close[0]}）")
    pauses, broken = _pair_pauses(sorted(case_pause_events, key=lambda e: e["at"]), C_PAUSE, C_RESUME)
    if broken:
        item.status = STATUS_GAP
        item.reasons.append("暂停事实不能建立")
        item.missing.append(f"{case} 的全件暂停与全件恢复回执不成对")
        return item
    if end < start:
        item.status = STATUS_GAP
        item.reasons.append("时序矛盾")
        item.missing.append(f"{case} 联合结案 {end} 早于首次材料齐备 {start}")
        return item
    effective = parse_day(rule["effective"])
    if end < effective:
        item.status = STATUS_NA
        item.reasons.append(
            f"结案日 {end.isoformat()} 早于{NEW_RULE}生效日 {rule['effective']}，新口径不适用"
        )
        return item
    cal = WorkCalendar(source, rule["calendar"], "联办", cutoff)
    clipped = []
    for s, e in pauses:
        s2, e2 = max(s, start), min(e, end)
        if s2 < e2:
            clipped.append((s2, e2))
            item.evidence.append(f"events: 全件暂停 {s.isoformat()} -> 恢复 {e.isoformat()}")
    total = cal.workdays_between(start, end)
    excluded = cal.workdays_in_intervals(clipped)
    item.start, item.end = start.isoformat(), end.isoformat()
    item.pauses = [[s.isoformat(), e.isoformat()] for s, e in clipped]
    item.days = total - excluded
    item.excluded_days = excluded
    item.timely = item.days <= rule["limit_days"]
    for row in cal.applied_revisions(start, end):
        item.evidence.append(
            "calendar_revisions: "
            f"{row['calendar']}/{row['applies_to']} {row['affected']}={row['state']}"
            f"（recorded {row['recorded']}）"
        )
    return item


def collect_items(source, cutoff):
    """全部有效组件的旧口径项 + 全部案件的新口径项。返回 (old_items, new_items)。"""
    links = source.effective_links(cutoff)
    by_case: dict[str, list] = {}
    for (case, _comp), link in links.items():
        by_case.setdefault(case, []).append(link)
    old_items = [measure_component(source, link, cutoff) for link in links.values()]
    new_items = [
        measure_case(source, case_row, by_case.get(case_row["case"], []), cutoff)
        for case_row in source.rows("cases")
    ]
    return old_items, new_items
