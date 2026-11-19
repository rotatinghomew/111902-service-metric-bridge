"""回执流转与更正说明：生成/送出/签收分明，已签收不追改，只影响受波及收件方。"""

import tempfile
import unittest
from pathlib import Path

from timeliness import corrections
from timeliness.outbox import Outbox
from timeliness.source import Source
from timeliness.trace import trace_item

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-11-04T18:00"


class OutboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source.load(ROOT / "data" / "supplies.json")

    def fresh_box(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return Outbox.load(Path(self.tmp.name) / "outbox.json", self.source)

    def test_seed_distinguishes_states(self):
        box = self.fresh_box()
        self.assertEqual(box.find("R01")["state"], "签收")   # 已有对方签收回执
        self.assertEqual(box.find("R02")["state"], "生成")   # 未发送 != 已发送 != 已签收

    def test_signed_bundle_immutable(self):
        box = self.fresh_box()
        with self.assertRaises(ValueError):
            box.transition("R01", "作废", NOW)
        with self.assertRaises(ValueError):
            box.regenerate("R01", NOW, NOW)

    def test_return_requires_receipt(self):
        box = self.fresh_box()
        box.transition("R02", "送出", "2026-11-05T08:00")
        box.transition("R02", "退回", "2026-11-05T09:30", note="退回比较项：new:K2")
        self.assertEqual(box.find("R02")["state"], "退回")
        self.assertEqual(box.find("R02")["history"][-1]["state"], "退回")

    def test_correction_only_for_affected_signed(self):
        box = self.fresh_box()
        notices = corrections.generate_corrections(self.source, box, NOW, self.tmp.name)
        # 只有东城窗口的 R01 受影响；北城窗口无已签收包，市主管 R02 未送出
        self.assertEqual(len(notices), 1)
        notice = notices[0]
        self.assertEqual(notice["recipient"], "东城窗口")
        self.assertEqual(notice["about"], "R01")
        # 本期更正：A2 天数、B2 移出、W2 单元
        items = {c.get("item") for c in notice["本期更正"]}
        self.assertIn("old:A2", items)
        self.assertIn("old:B2", items)
        # 不再成立的对比区间指向签收版基准
        self.assertTrue(any("签收版" in x for x in notice["不再成立的对比区间"]))
        # 新事实依据：日历更正 + 联办关系撤销
        tables = {f["table"] for f in notice["新事实依据"]}
        self.assertEqual(tables, {"calendar_revisions", "case_links"})
        # 归因精确：B2 移出只与联办关系撤销有关，与 W1 日历更正无关
        b2 = [c for c in notice["本期更正"] if c.get("item") == "old:B2"][0]
        self.assertTrue(any("撤销" in x for x in b2["causes"]))
        self.assertFalse(any("calendar_revisions" in x for x in b2["causes"]))
        a2 = [c for c in notice["本期更正"] if c.get("item") == "old:A2"][0]
        self.assertTrue(any("calendar_revisions" in x for x in a2["causes"]))
        self.assertFalse(any("撤销" in x for x in a2["causes"]))  # 同案他件撤销与 A2 无关
        # 更正说明本身也走本地回执：生成后可送出
        self.assertEqual(box.find(notice["notice"])["state"], "生成")
        box.transition(notice["notice"], "送出", "2026-11-04T18:30")
        self.assertEqual(box.find(notice["notice"])["state"], "送出")

    def test_no_correction_when_facts_unchanged(self):
        box = self.fresh_box()
        notices = corrections.generate_corrections(
            self.source, box, "2026-11-02T10:00", self.tmp.name
        )
        self.assertEqual(notices, [])

    def test_trace_returned_item(self):
        # 经办人从退回比较项查到不支持它的历史材料
        result = trace_item(self.source, "new:K2", NOW)
        self.assertEqual(result["状态"], "未能重算")
        self.assertTrue(any("材料齐备" in m for m in result["明细"]["missing"]))
        self.assertTrue(any("联合结案" in m for m in result["明细"]["missing"]))
        # 口径不支持的追溯：B2 在当前截点不属于任何联办件
        result = trace_item(self.source, "old:B2", NOW)
        self.assertTrue(any("撤销" in e for e in result["明细"]["evidence"]))
        # 汇总批次：只能给出可用范围与缺口
        result = trace_item(self.source, "legacy:2026-09/北城/居住联办/W3", NOW)
        self.assertEqual(result["状态"], "仅汇总资料")
        self.assertTrue(any("逐件回执" in m for m in result["明细"]["missing"]))


if __name__ == "__main__":
    unittest.main()
