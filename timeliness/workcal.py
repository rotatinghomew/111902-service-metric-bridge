"""工作日日历：默认周一至周五，日历更正按登记时点生效。

区间一律左闭右开；同一时钟上重叠的等待区间只计一次（取并集）。
"""
from __future__ import annotations

from datetime import date, timedelta


class WorkCalendar:
    def __init__(self, source, calendar: str, scope: str, cutoff):
        self.source = source
        self.calendar = calendar
        self.scope = scope
        self.cutoff = cutoff

    def is_working(self, day: date) -> bool:
        return self.source.calendar_state(self.calendar, self.scope, day, self.cutoff) == "工作"

    def workdays_between(self, start: date, end: date) -> int:
        """[start, end) 内的工作日数。"""
        days = 0
        d = start
        while d < end:
            if self.is_working(d):
                days += 1
            d += timedelta(days=1)
        return days

    def workdays_in_intervals(self, intervals) -> int:
        """多个区间并集内的工作日数：同一时钟上重叠等待不重复扣除。"""
        days = set()
        for start, end in intervals:
            d = start
            while d < end:
                days.add(d)
                d += timedelta(days=1)
        return sum(1 for d in days if self.is_working(d))

    def applied_revisions(self, start: date, end: date) -> list:
        """区间内实际生效且改变默认状态的更正行（用于证据链）。"""
        rows = []
        for row in self.source.effective_revisions(self.calendar, self.scope, self.cutoff):
            affected = date.fromisoformat(row["affected"])
            if not (start <= affected < end):
                continue
            default = "工作" if affected.weekday() < 5 else "休息"
            if row["state"] != default:
                rows.append(row)
        return rows
