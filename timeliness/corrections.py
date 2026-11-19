"""更正说明：已签收但不能追改的包，在事实更正后给签收方的说明。

- 受影响的历史照原签收版可查（签收版快照随说明保存）；
- 说明区分本期更正与不再成立的对比区间；
- 只有受影响的收件方才收到更正说明，其他收件方不重复收包。
"""
from __future__ import annotations

import json
from pathlib import Path

from .bundles import build_bundle, diff_bundles
from .source import as_moment, fmt_moment


def _recipient_row(source, name):
    return source.recipient(name)


def _causes(source, signed_facts_at, now, change):
    """把一条差异归因到 (signed_facts_at, now] 内登记、且与该差异相关的事实行。

    相关性：日历更正按适用对象（窗口/联办）匹配，联办关系按案件或组件匹配。
    """
    facts = source.new_facts_between(signed_facts_at, now)
    item = change.get("item", "") or ""
    cell = change.get("cell", "") or ""
    windows, cases = set(), set()
    if item.startswith("old:"):
        comp = item[4:]
        for l in source.rows("case_links"):
            if l["component"] == comp:
                windows.add(l["window"])
                cases.add(l["case"])
    elif item.startswith("new:"):
        cases.add(item[4:])
        windows.add("联办")
    elif cell:
        parts = cell.split("/")
        if change.get("part") == "旧考核" and len(parts) == 4:
            windows.add(parts[3])
        if change.get("part") == "新考核":
            windows.add("联办")
        for row in source.rows("cases"):
            if (row["period"], row["district"], row["service"]) == tuple(parts[:3]):
                cases.add(row["case"])
    related = []
    for fact in facts:
        if fact["table"] == "case_links":
            if item.startswith("old:"):
                # 旧口径组件项只受自身联办关系影响
                hit = fact["component"] == item[4:]
            else:
                hit = fact["case"] in cases
            if hit:
                related.append(
                    f"case_links {fact['case']}/{fact['component']} {fact['operation']}"
                    f"（received {fact['received']}）"
                )
        elif fact["table"] == "calendar_revisions" and fact["applies_to"] in windows:
            related.append(
                f"calendar_revisions {fact['calendar']}/{fact['applies_to']} "
                f"{fact['affected']}={fact['state']}（recorded {fact['recorded']}）"
            )
    return related


def build_correction(source, outbox, receipt, now, results_dir):
    """为一条已签收回执生成更正说明；无影响时返回 None。"""
    now = as_moment(now)
    recipient = _recipient_row(source, receipt["recipient"])
    signed_facts = receipt["facts_at"]
    signed_bundle = build_bundle(source, recipient, receipt["period"], signed_facts, receipt["bundle"])
    current_bundle = build_bundle(source, recipient, receipt["period"], now, receipt["bundle"])
    changes = diff_bundles(signed_bundle, current_bundle)
    if not changes:
        return None

    for change in changes:
        change["causes"] = _causes(source, signed_facts, now, change)

    invalid = []
    for change in changes:
        if change.get("scale") == "单元":
            invalid.append(
                f"{change['part']} {change['cell']}：以签收版（{signed_facts}）为基准的对比不再成立"
            )
    item_parts = sorted({c["part"] for c in changes if c.get("scale") == "项"})
    for part in item_parts:
        invalid.append(f"{part} 逐件明细在签收版与现行事实之间发生变化，跨两版的趋势对比不再成立")
    invalid.append(
        f"凡引用签收版（{signed_facts}）数值、且落在上述单元/明细的对比区间均不再成立"
    )
    # 去重保序
    invalid = list(dict.fromkeys(invalid))

    notice_id = outbox.next_notice_id(receipt["bundle"])
    signed_dir = Path(results_dir) / "bundles"
    signed_dir.mkdir(parents=True, exist_ok=True)
    snapshot = signed_dir / f"{receipt['bundle']}.signed.json"
    if not snapshot.exists():
        snapshot.write_text(
            json.dumps(signed_bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    notice = {
        "notice": notice_id,
        "about": receipt["bundle"],
        "recipient": receipt["recipient"],
        "period": receipt["period"],
        "signed_facts_at": signed_facts,
        "generated_at": fmt_moment(now),
        "本期更正": changes,
        "不再成立的对比区间": invalid,
        "新事实依据": source.new_facts_between(signed_facts, now),
        "签收版快照": str(snapshot),
        "note": "已签收包不能追改；本说明供签收方区分本期更正与不再成立的对比区间",
    }
    outbox.add(
        notice_id, "更正说明", receipt["recipient"], receipt["period"],
        fmt_moment(now), now, note=f"针对 {receipt['bundle']} 的本期更正", about=receipt["bundle"],
    )
    notice_dir = Path(results_dir) / "corrections"
    notice_dir.mkdir(parents=True, exist_ok=True)
    (notice_dir / f"{notice_id}.json").write_text(
        json.dumps(notice, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (notice_dir / f"{notice_id}.md").write_text(render_notice_md(notice), encoding="utf-8")
    return notice


def generate_corrections(source, outbox, now, results_dir):
    """扫描全部已签收回执，为受影响者生成更正说明。返回说明列表。"""
    notices = []
    for receipt in list(outbox.receipts):
        if receipt["kind"] != "上报包" or receipt["state"] != "签收":
            continue
        notice = build_correction(source, outbox, receipt, now, results_dir)
        if notice is not None:
            notices.append(notice)
    return notices


def render_notice_md(notice: dict) -> str:
    lines = [
        f"# 更正说明 {notice['notice']}",
        "",
        f"- 针对上报包：{notice['about']}（签收口径截点 {notice['signed_facts_at']}）",
        f"- 收件方：{notice['recipient']}　期间：{notice['period']}",
        f"- 生成时点：{notice['generated_at']}",
        f"- 签收版快照：{notice['签收版快照']}（照原签收版可查）",
        "",
        "## 本期更正",
    ]
    for c in notice["本期更正"]:
        head = f"- [{c['part']}] {c.get('item') or c.get('cell') or c['scale']}：{c['change']}"
        if c.get("was") is not None or c.get("now") is not None:
            head += f"（{c.get('was')} -> {c.get('now')}）"
        lines.append(head)
        for cause in c.get("causes", []):
            lines.append(f"  - 依据：{cause}")
    lines += ["", "## 不再成立的对比区间"]
    lines += [f"- {x}" for x in notice["不再成立的对比区间"]]
    lines += ["", "## 新事实依据"]
    for f in notice["新事实依据"]:
        if f["table"] == "case_links":
            lines.append(
                f"- case_links {f['case']}/{f['component']} {f['operation']}（received {f['received']}）"
            )
        else:
            lines.append(
                f"- calendar_revisions {f['calendar']}/{f['applies_to']} "
                f"{f['affected']}={f['state']}（recorded {f['recorded']}）"
            )
    lines += ["", notice["note"], ""]
    return "\n".join(lines)
