"""本地接收回执（outbox）：区分生成、送出、签收、退回、作废、受阻。

- signed 表是历史签收的独立输入回执，用于播种初始状态，不是现时计算的结论；
- 已签收的包不能追改，只能出具更正说明；
- 接收回退也必须有回执（退回状态同样落账）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .source import as_moment, fmt_moment

STATES = ("生成", "送出", "签收", "退回", "作废", "受阻")

FLOW = {
    "生成": {"送出", "作废", "受阻"},
    "受阻": {"生成", "作废"},
    "送出": {"签收", "退回"},
    "退回": {"生成"},
    "作废": set(),
    "签收": set(),  # 已签收不能追改
}


class Outbox:
    def __init__(self, path):
        self.path = Path(path)
        self.receipts: list[dict] = []

    # ---- 持久化 ----
    @classmethod
    def load(cls, path, source=None) -> "Outbox":
        box = cls(path)
        if box.path.exists():
            box.receipts = json.loads(box.path.read_text(encoding="utf-8"))["receipts"]
        elif source is not None:
            box.seed_from_signed(source)
        return box

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"receipts": self.receipts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def seed_from_signed(self, source):
        """用 signed 表播种历史回执：已签收的记签收，未发送的记生成。"""
        for row in source.rows("signed"):
            if row["result"] == "签收" and row["ack_at"]:
                history = [
                    {"at": row["facts_at"], "state": "生成", "note": "signed 表播种"},
                    {"at": row["ack_at"], "state": "签收", "note": "对方签收回执（signed 表）"},
                ]
                state = "签收"
            else:
                history = [{"at": row["facts_at"], "state": "生成", "note": "signed 表播种（未发送）"}]
                state = "生成"
            self.receipts.append({
                "bundle": row["bundle"],
                "kind": "上报包",
                "recipient": row["recipient"],
                "period": row["period"],
                "facts_at": row["facts_at"],
                "state": state,
                "about": None,
                "history": history,
            })

    # ---- 查询 ----
    def find(self, bundle_id: str):
        for r in self.receipts:
            if r["bundle"] == bundle_id:
                return r
        return None

    def find_bundle_for(self, recipient: str, period: str, kind="上报包"):
        for r in self.receipts:
            if r["kind"] == kind and r["recipient"] == recipient and r["period"] == period:
                if r["state"] not in ("作废",):
                    return r
        return None

    def next_bundle_id(self) -> str:
        nums = [
            int(r["bundle"][1:]) for r in self.receipts
            if r["bundle"].startswith("R") and r["bundle"][1:].isdigit()
        ]
        return f"R{(max(nums) + 1) if nums else 1:02d}"

    def next_notice_id(self, about: str) -> str:
        nums = [
            int(r["bundle"].rsplit("-", 1)[1]) for r in self.receipts
            if r["kind"] == "更正说明" and r["about"] == about
        ]
        return f"CN-{about}-{(max(nums) + 1) if nums else 1}"

    # ---- 流转 ----
    def add(self, bundle_id, kind, recipient, period, facts_at, at, note="", about=None):
        if self.find(bundle_id):
            raise ValueError(f"回执 {bundle_id} 已存在")
        receipt = {
            "bundle": bundle_id,
            "kind": kind,
            "recipient": recipient,
            "period": period,
            "facts_at": facts_at,
            "state": "生成",
            "about": about,
            "history": [{"at": fmt_moment(as_moment(at)), "state": "生成", "note": note}],
        }
        self.receipts.append(receipt)
        return receipt

    def transition(self, bundle_id: str, state: str, at, note=""):
        receipt = self.find(bundle_id)
        if receipt is None:
            raise KeyError(f"没有回执 {bundle_id}")
        current = receipt["state"]
        if state not in FLOW.get(current, set()):
            raise ValueError(f"{bundle_id} 不允许从 {current} 转到 {state}")
        receipt["state"] = state
        receipt["history"].append({
            "at": fmt_moment(as_moment(at)), "state": state, "note": note,
        })
        return receipt

    def regenerate(self, bundle_id: str, facts_at: str, at, note=""):
        """未送出的包按新口径截点重出：旧生成作废，新生成落账。"""
        receipt = self.find(bundle_id)
        if receipt is None:
            raise KeyError(f"没有回执 {bundle_id}")
        if receipt["state"] not in ("生成", "受阻", "退回"):
            raise ValueError(f"{bundle_id} 状态为 {receipt['state']}，不能重出")
        receipt["history"].append({
            "at": fmt_moment(as_moment(at)), "state": "作废",
            "note": f"原生成（facts_at {receipt['facts_at']}）作废：{note}",
        })
        receipt["state"] = "生成"
        receipt["facts_at"] = facts_at
        receipt["history"].append({
            "at": fmt_moment(as_moment(at)), "state": "生成", "note": note,
        })
        return receipt
