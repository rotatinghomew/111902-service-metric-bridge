"""上报包：组成一致、同一口径截点、送出重核、整包拦截。"""

import unittest
from pathlib import Path

from timeliness.bundles import build_bundle, check_consistency, diff_bundles, verify_at_send
from timeliness.source import Source

ROOT = Path(__file__).resolve().parents[1]
C0 = "2026-11-02T09:00"
C1 = "2026-11-04T18:00"
SEND = "2026-11-05T08:00"


class BundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Source.load(ROOT / "data" / "supplies.json")

    def build(self, name, cutoff):
        return build_bundle(self.source, self.source.recipient(name), "2026-10", cutoff, "RX")

    def test_parts_follow_subscription(self):
        bundle = self.build("北城窗口", C1)
        self.assertEqual(set(bundle["parts"]), {"旧考核"})
        bundle = self.build("市主管", C1)
        self.assertEqual(set(bundle["parts"]), {"旧考核", "新考核", "覆盖说明"})

    def test_single_cutoff_per_bundle(self):
        bundle = self.build("市主管", C1)
        self.assertEqual(check_consistency(bundle, ["旧考核", "新考核", "覆盖说明"]), [])
        for part in bundle["parts"].values():
            self.assertEqual(part["facts_at"], bundle["facts_at"])

    def test_part_substitution_rejected(self):
        bundle = self.build("市主管", C1)
        # 用旧考核顶替覆盖说明：两份不能互作替代
        bundle["parts"]["覆盖说明"] = dict(bundle["parts"]["旧考核"])
        problems = check_consistency(bundle, ["旧考核", "新考核", "覆盖说明"])
        self.assertTrue(any("替代" in p for p in problems))

    def test_verify_at_send_ok_when_facts_stable(self):
        bundle = self.build("市主管", C1)
        ok, fresh = verify_at_send(self.source, bundle, SEND)
        self.assertTrue(ok)
        self.assertEqual(fresh["digest"], bundle["digest"])

    def test_verify_at_send_blocks_stale_bundle(self):
        # 按签收日事实生成的包，在 11-04 更正后送出时必须被拦下
        stale = self.build("市主管", C0)
        ok, _ = verify_at_send(self.source, stale, SEND)
        self.assertFalse(ok)

    def test_diff_signed_vs_current(self):
        was = self.build("东城窗口", C0)
        now = self.build("东城窗口", C1)
        changes = diff_bundles(was, now)
        # B2 移出、A2 天数 4->3、W2 单元 2->1
        self.assertTrue(any(c.get("item") == "old:B2" and c["change"] == "移出" for c in changes))
        self.assertTrue(any(
            c.get("item") == "old:A2" and c["change"] == "days 变化"
            and c["was"] == 4 and c["now"] == 3 for c in changes
        ))
        self.assertTrue(any(
            c.get("scale") == "单元" and "W2" in c.get("cell", "")
            and c["was"]["total"] == 2 and c["now"]["total"] == 1 for c in changes
        ))

    def test_beicheng_bundle_reports_gap(self):
        bundle = self.build("北城窗口", C1)
        part = bundle["parts"]["旧考核"]
        self.assertEqual(part["cells"], [])
        self.assertEqual(part["case_gaps"][0]["case"], "K3")
        self.assertIn("缺少有效联办关系", part["case_gaps"][0]["reasons"])

    def test_coverage_part_contents(self):
        bundle = self.build("市主管", C1)
        cov = bundle["parts"]["覆盖说明"]
        self.assertEqual(cov["新口径"]["已重算"], 1)          # K1
        self.assertEqual(len(cov["新口径"]["未能重算"]), 2)   # K2、K3
        self.assertEqual(cov["无联办关系案件"], ["K3"])
        self.assertEqual(len(cov["已撤销联办关系"]), 1)       # B2
        self.assertEqual(len(cov["既有汇总"]), 1)             # 2026-09 北城 W3
        self.assertTrue(any("不能还原居民超时率" in n for n in cov["可比性注记"]))


if __name__ == "__main__":
    unittest.main()
