"""业务来源资料读取与"口径截点"下的有效事实查询。

事实生效规则：
- case_links 的 received、calendar_revisions 的 recorded 是事实登记时点；
- 给定截点 T，只采纳登记时点 <= T 的事实；同一键取最后登记的一条；
- events 是已发生的回执事实，本身不携带登记时点，按表内原样信任。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path


def parse_day(text: str) -> date:
    return date.fromisoformat(text)


def parse_moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def as_moment(value) -> datetime:
    """把日期或日期时间统一成时点，用于截点比较。日期视为当日 00:00。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value)
    if "T" in text:
        return datetime.fromisoformat(text)
    return datetime.combine(date.fromisoformat(text), time.min)


def fmt_moment(moment: datetime) -> str:
    return moment.isoformat(timespec="minutes")


@dataclass(frozen=True)
class Source:
    tables: dict
    path: str = ""

    @classmethod
    def load(cls, path) -> "Source":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not data.get("synthetic"):
            raise ValueError("资料缺少 synthetic 标记，拒绝当作业务来源")
        return cls(tables=data["tables"], path=str(path))

    def rows(self, table: str) -> list:
        return self.tables.get(table, [])

    def rule(self, version: str) -> dict:
        for row in self.rows("rules"):
            if row["version"] == version:
                return row
        raise KeyError(f"未找到规则 {version}")

    def recipient(self, name: str) -> dict:
        for row in self.rows("recipients"):
            if row["recipient"] == name:
                return row
        raise KeyError(f"未找到收件方 {name}")

    def case_row(self, case: str) -> dict:
        for row in self.rows("cases"):
            if row["case"] == case:
                return row
        raise KeyError(f"未找到联办件 {case}")

    def events_of(self, component: str) -> list:
        evs = [r for r in self.rows("events") if r["component"] == component]
        return sorted(evs, key=lambda r: r["at"])

    def link_rows(self, case=None, component=None) -> list:
        return [
            r for r in self.rows("case_links")
            if (case is None or r["case"] == case)
            and (component is None or r["component"] == component)
        ]

    def effective_links(self, cutoff) -> dict:
        """截点下有效的联办关系：{(case, component): 关联行}。

        同一 (case, component) 取 received <= 截点的最后一条操作；
        最后一条是"撤销"则该关系在截点下无效。
        """
        cut = as_moment(cutoff)
        latest = {}
        for row in self.rows("case_links"):
            rec = as_moment(row["received"])
            if rec > cut:
                continue
            key = (row["case"], row["component"])
            if key not in latest or rec > as_moment(latest[key]["received"]):
                latest[key] = row
        return {k: v for k, v in latest.items() if v["operation"] == "关联"}

    def revoked_links(self, cutoff) -> list:
        """截点下已被撤销的联办关系（最后操作为撤销）。"""
        cut = as_moment(cutoff)
        latest = {}
        for row in self.rows("case_links"):
            rec = as_moment(row["received"])
            if rec > cut:
                continue
            key = (row["case"], row["component"])
            if key not in latest or rec > as_moment(latest[key]["received"]):
                latest[key] = row
        return [v for v in latest.values() if v["operation"] == "撤销"]

    def calendar_state(self, calendar: str, scope: str, day: date, cutoff) -> str:
        """某日历某适用对象在某日期的有效状态（默认周一至周五工作）。"""
        cut = as_moment(cutoff)
        state = "工作" if day.weekday() < 5 else "休息"
        best = None
        for row in self.rows("calendar_revisions"):
            if row["calendar"] != calendar or row["applies_to"] != scope:
                continue
            if parse_day(row["affected"]) != day:
                continue
            rec = as_moment(row["recorded"])
            if rec > cut:
                continue
            if best is None or rec > as_moment(best["recorded"]):
                best = row
        if best is not None:
            state = best["state"]
        return state

    def effective_revisions(self, calendar: str, scope: str, cutoff) -> list:
        """截点下某日历某对象全部生效的更正（每日期取最后登记）。"""
        cut = as_moment(cutoff)
        latest = {}
        for row in self.rows("calendar_revisions"):
            if row["calendar"] != calendar or row["applies_to"] != scope:
                continue
            rec = as_moment(row["recorded"])
            if rec > cut:
                continue
            key = row["affected"]
            if key not in latest or rec > as_moment(latest[key]["recorded"]):
                latest[key] = row
        return sorted(latest.values(), key=lambda r: r["affected"])

    def new_facts_between(self, start, end) -> list:
        """登记时点落在 (start, end] 内的事实行（日历更正、联办关系操作）。"""
        lo, hi = as_moment(start), as_moment(end)
        found = []
        for row in self.rows("calendar_revisions"):
            rec = as_moment(row["recorded"])
            if lo < rec <= hi:
                found.append({"table": "calendar_revisions", "recorded": row["recorded"], **row})
        for row in self.rows("case_links"):
            rec = as_moment(row["received"])
            if lo < rec <= hi:
                found.append({"table": "case_links", "received": row["received"], **row})
        return sorted(found, key=lambda r: r.get("recorded") or r.get("received"))

    def fact_moments(self) -> list:
        moments = [as_moment(r["received"]) for r in self.rows("case_links")]
        moments += [as_moment(r["recorded"]) for r in self.rows("calendar_revisions")]
        return moments
