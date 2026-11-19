"""逐件重算：旧口径、新口径、未能重算、事实更正的影响。"""

import unittest
from pathlib import Path

from timeliness.measures import STATUS_GAP, STATUS_OK, collect_items
from timeliness.source import Source

ROOT = Path(__file__).resolve().parents[1]
C0 = "2026-11-02T09:00"
C1 = "2026-11-04T18:00"


class MeasureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source.load(ROOT / "data" / "supplies.json")

    def items(self, cutoff):
        old, new = collect_items(self.source, cutoff)
        return {it.item: it for it in old}, {it.item: it for it in new}

    def test_old_a1_pause_absorbs_calendar_change(self):
        # A1 暂停 [10-14,10-16) 覆盖了被更正的 10-15，两种事实版本下都是 4 天
        old0, _ = self.items(C0)
        old1, _ = self.items(C1)
        self.assertEqual(old0["old:A1"].days, 4)
        self.assertEqual(old1["old:A1"].days, 4)
        self.assertTrue(old1["old:A1"].timely)

    def test_old_a2_calendar_correction(self):
        old0, _ = self.items(C0)
        old1, _ = self.items(C1)
        self.assertEqual(old0["old:A2"].days, 4)   # 10-15 曾按工作
        self.assertEqual(old1["old:A2"].days, 3)   # 更正为休息后少一天
        self.assertTrue(old1["old:A2"].timely)

    def test_old_b1_with_pause(self):
        old1, _ = self.items(C1)
        b1 = old1["old:B1"]
        self.assertEqual(b1.days, 4)
        self.assertEqual(b1.excluded_days, 2)
        self.assertTrue(b1.timely)

    def test_b2_removed_after_revocation(self):
        old0, _ = self.items(C0)
        old1, _ = self.items(C1)
        self.assertIn("old:B2", old0)        # 签收时 B2 仍计入 W2
        self.assertNotIn("old:B2", old1)     # 11-04 撤销后不再属于任何联办件

    def test_new_k1_resident_wait(self):
        _, new1 = self.items(C1)
        k1 = new1["new:K1"]
        self.assertEqual(k1.status, STATUS_OK)
        self.assertEqual((k1.start, k1.end), ("2026-10-13", "2026-10-21"))
        self.assertEqual(k1.days, 5)         # 居民历 10-16 休息
        self.assertEqual(k1.excluded_days, 0)  # 窗口暂停不能充当全件暂停
        self.assertTrue(k1.timely)
        self.assertTrue(any("不能充当全件暂停凭据" in n for n in k1.notes))

    def test_new_k2_gap(self):
        _, new1 = self.items(C1)
        k2 = new1["new:K2"]
        self.assertEqual(k2.status, STATUS_GAP)
        self.assertIn("完整申请事实不能建立", k2.reasons)
        self.assertIn("完结事实不能建立", k2.reasons)
        self.assertTrue(any("材料齐备" in m for m in k2.missing))
        self.assertTrue(any("联合结案" in m for m in k2.missing))

    def test_new_k3_no_links(self):
        _, new1 = self.items(C1)
        k3 = new1["new:K3"]
        self.assertEqual(k3.status, STATUS_GAP)
        self.assertIn("缺少有效联办关系", k3.reasons)

    def test_window_pause_not_case_pause(self):
        # K1 组件上有窗口暂停，但新口径不得据此扣除
        _, new1 = self.items(C1)
        self.assertEqual(new1["new:K1"].pauses, [])


if __name__ == "__main__":
    unittest.main()
