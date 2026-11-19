"""工作日日历：默认、更正生效时点、重叠等待不重复扣除。"""

import unittest
from datetime import date
from pathlib import Path

from timeliness.source import Source
from timeliness.workcal import WorkCalendar

ROOT = Path(__file__).resolve().parents[1]
C0 = "2026-11-02T09:00"   # 签收口径截点
C1 = "2026-11-04T18:00"   # 日历更正与撤销生效后


class WorkCalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source.load(ROOT / "data" / "supplies.json")

    def test_default_weekday(self):
        cal = WorkCalendar(self.source, "旧历", "W1", C1)
        self.assertTrue(cal.is_working(date(2026, 10, 12)))   # 周一
        self.assertFalse(cal.is_working(date(2026, 10, 17)))  # 周六

    def test_revision_effective_by_recorded(self):
        day = date(2026, 10, 15)
        old = WorkCalendar(self.source, "旧历", "W1", C0)
        new = WorkCalendar(self.source, "旧历", "W1", C1)
        self.assertTrue(old.is_working(day))    # 签收时 10-15 仍按工作
        self.assertFalse(new.is_working(day))   # 11-04 更正为休息后生效

    def test_revision_scoped_to_window(self):
        day = date(2026, 10, 13)
        w1 = WorkCalendar(self.source, "旧历", "W1", C1)
        w2 = WorkCalendar(self.source, "旧历", "W2", C1)
        self.assertTrue(w1.is_working(day))    # W1 的 10-13 无更正
        self.assertFalse(w2.is_working(day))   # W2 的 10-13 休息

    def test_overlapping_waits_counted_once(self):
        cal = WorkCalendar(self.source, "旧历", "W1", C1)
        # 同一时钟上两段重叠等待 [12,16) 与 [14,18)：并集为 12,13,14,15,16,17
        n = cal.workdays_in_intervals([(date(2026, 10, 12), date(2026, 10, 16)),
                                       (date(2026, 10, 14), date(2026, 10, 18))])
        # 工作日：12,13,14（15 休息）,16（17 周六）= 4
        self.assertEqual(n, 4)

    def test_workdays_between_left_closed_right_open(self):
        cal = WorkCalendar(self.source, "居民历", "联办", C1)
        # [10-13, 10-21)：13,14,15,19,20（16 休息，17/18 周末）= 5
        self.assertEqual(cal.workdays_between(date(2026, 10, 13), date(2026, 10, 21)), 5)


if __name__ == "__main__":
    unittest.main()
