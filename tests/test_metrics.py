"""汇总分母规则与比较注记。"""

import unittest
from pathlib import Path

from timeliness.measures import collect_items
from timeliness.metrics import (
    compare_items,
    legacy_cells,
    new_cells,
    new_cells_by_window,
    old_cells,
)
from timeliness.source import Source

ROOT = Path(__file__).resolve().parents[1]
C1 = "2026-11-04T18:00"


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source.load(ROOT / "data" / "supplies.json")
        cls.old_items, cls.new_items = collect_items(cls.source, C1)

    def test_old_cells_by_window(self):
        cells = {(c["district"], c["service"], c["window"]): c
                 for c in old_cells(self.source, self.old_items, "全市", "2026-10")}
        self.assertEqual(cells[("东城", "登记联办", "W1")]["total"], 2)   # A1、A2
        self.assertEqual(cells[("东城", "登记联办", "W2")]["total"], 1)   # 仅 B1（B2 已撤销）
        self.assertTrue(all(c["timely"] == c["total"] for c in cells.values()))

    def test_new_cells_case_counted_once(self):
        cells = new_cells(self.source, self.new_items, "全市", "2026-10")
        east = [c for c in cells if c["district"] == "东城"][0]
        self.assertEqual(east["total"], 1)          # 只有 K1 可重算
        self.assertEqual(east["items"], ["new:K1"])
        self.assertEqual(east["gaps"], ["new:K2"])  # K2 未能重算，单列为缺口

    def test_window_slices_not_additive(self):
        bw = new_cells_by_window(self.source, self.new_items, C1, "东城", "2026-10")
        # K1 同时出现在 W1、W2 两片
        self.assertEqual(bw["slices"]["W1"]["items"], ["new:K1"])
        self.assertEqual(bw["slices"]["W2"]["items"], ["new:K1"])
        # 但合计按联办件去重，不允许按行相加
        self.assertEqual(bw["combined"]["total"], 1)
        self.assertNotEqual(
            bw["combined"]["total"],
            sum(sl["total"] for sl in bw["slices"].values()),
        )
        self.assertIn("不可按行相加", bw["note"])

    def test_legacy_kept_but_not_bridged(self):
        rows = legacy_cells(self.source, "全市")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["total"], rows[0]["timely"]), (12, 10))
        self.assertIn("不能推断至单个联办件", rows[0]["note"])
        # 北城窗口只能看到自己范围的汇总
        self.assertEqual(legacy_cells(self.source, "东城"), [])

    def test_compare_blocks_cross_convention(self):
        verdict = compare_items(
            {"id": "legacy:2026-09/北城/居住联办/W3", "kind": "legacy",
             "convention": "窗口旧口径（月度汇总）", "period": "2026-09", "calendar": "旧历"},
            {"id": "new:K1", "kind": "new", "convention": "居民新口径",
             "period": "2026-10", "calendar": "居民历"},
        )
        self.assertEqual(verdict["verdict"], "不可直接比较")
        self.assertTrue(any("口径不同" in f for f in verdict["factors"]))
        self.assertIn("办得更快", verdict["note"])

    def test_compare_flags_fact_version(self):
        verdict = compare_items(
            {"id": "old:A2", "kind": "old", "convention": "窗口旧口径",
             "period": "2026-10", "calendar": "旧历", "facts_at": "2026-11-02T09:00"},
            {"id": "old:A2", "kind": "old", "convention": "窗口旧口径",
             "period": "2026-10", "calendar": "旧历", "facts_at": "2026-11-04T18:00"},
        )
        self.assertEqual(verdict["verdict"], "可并列，需注记差异来源")
        self.assertTrue(any("事实版本不同" in f for f in verdict["factors"]))


if __name__ == "__main__":
    unittest.main()
