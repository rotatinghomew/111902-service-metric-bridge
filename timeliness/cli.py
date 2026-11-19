"""业务操作入口：重算、组包、送出、更正、追溯、状态。

用法示例：
    python3 -m timeliness.cli compute --cutoff 2026-11-04T18:00
    python3 -m timeliness.cli correct --now 2026-11-04T18:00
    python3 -m timeliness.cli bundle --all --period 2026-10 --cutoff 2026-11-04T18:00
    python3 -m timeliness.cli send --bundle R02 --at 2026-11-05T08:00
    python3 -m timeliness.cli trace --item new:K2
    python3 -m timeliness.cli status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import corrections
from .bundles import build_bundle, check_consistency, verify_at_send
from .coverage import COMPARABILITY_NOTES, build_coverage
from .measures import STATUS_OK, collect_items
from .metrics import (
    compare_items,
    legacy_cells,
    new_cells,
    new_cells_by_window,
    old_cells,
)
from .outbox import Outbox
from .source import Source, as_moment, fmt_moment
from .trace import trace_item


def _load(args):
    source = Source.load(args.data)
    outbox = Outbox.load(Path(args.results) / "outbox.json", source)
    return source, outbox


def _save_outbox(args, outbox):
    outbox.save()


def _slug(text: str) -> str:
    return text.replace(":", "").replace("/", "-")


# ---------- compute ----------
def cmd_compute(args):
    source, _ = _load(args)
    cutoff = as_moment(args.cutoff)
    old_items, new_items = collect_items(source, cutoff)
    periods = sorted({r["period"] for r in source.rows("cases")})
    analysis = {
        "cutoff": fmt_moment(cutoff),
        "rules": source.rows("rules"),
        "periods": {},
        "legacy": legacy_cells(source, "全市"),
        "comparability_notes": list(COMPARABILITY_NOTES),
    }
    for period in periods:
        analysis["periods"][period] = {
            "old_items": [it.to_dict() for it in old_items
                          if source.case_row(it.case)["period"] == period],
            "new_items": [it.to_dict() for it in new_items
                          if source.case_row(it.case)["period"] == period],
            "old_cells": old_cells(source, old_items, "全市", period),
            "new_cells": new_cells(source, new_items, "全市", period),
            "new_by_window": new_cells_by_window(source, new_items, cutoff, "全市", period),
            "coverage": build_coverage(source, cutoff, period, "全市", old_items, new_items),
        }
    out_dir = Path(args.results)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"analysis_{_slug(fmt_moment(cutoff))}"
    stem.with_suffix(".json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(_render_analysis_md(analysis), encoding="utf-8")
    print(f"分析结果已写出：{stem.with_suffix('.json')} / {stem.with_suffix('.md')}")
    _print_analysis_summary(analysis)


def _print_analysis_summary(analysis):
    for period, sec in analysis["periods"].items():
        print(f"\n== 期间 {period}（口径截点 {analysis['cutoff']}）==")
        for it in sec["old_items"]:
            _print_item(it)
        for it in sec["new_items"]:
            _print_item(it)
        for cell in sec["old_cells"]:
            print(f"  旧考核 {cell['district']}/{cell['service']}/{cell['window']}: "
                  f"{cell['timely']}/{cell['total']} 及时")
        for cell in sec["new_cells"]:
            print(f"  新考核 {cell['district']}/{cell['service']}: "
                  f"{cell['timely']}/{cell['total']} 及时")


def _print_item(it):
    if it["status"] == STATUS_OK:
        print(f"  {it['item']} [{it['convention']}] {it['start']}~{it['end']} "
              f"{it['days']} 工作日（时限 {it['limit']}，{'及时' if it['timely'] else '超时'}）")
    else:
        print(f"  {it['item']} [{it['status']}] {'；'.join(it['reasons'])}")


def _render_analysis_md(analysis) -> str:
    lines = [f"# 联办件时限重算（口径截点 {analysis['cutoff']}）", ""]
    lines.append("## 适用规则")
    for r in analysis["rules"]:
        lines.append(f"- {r['version']}：生效 {r['effective']}，日历 {r['calendar']}，"
                     f"时限 {r['limit_days']} 工作日")
    for period, sec in analysis["periods"].items():
        lines += ["", f"## 期间 {period}", "", "### 逐件结果"]
        for it in sec["old_items"] + sec["new_items"]:
            if it["status"] == STATUS_OK:
                lines.append(
                    f"- `{it['item']}`（{it['convention']}）{it['start']} ~ {it['end']}："
                    f"**{it['days']} 工作日**，时限 {it['limit']}，"
                    f"{'及时' if it['timely'] else '超时'}"
                    + (f"；排除等待 {it['excluded_days']} 天" if it["excluded_days"] else "")
                )
            else:
                lines.append(f"- `{it['item']}`（{it['status']}）：{'；'.join(it['reasons'])}")
                for m in it["missing"]:
                    lines.append(f"  - 缺口：{m}")
        lines += ["", "### 旧考核汇总（窗口责任，分母按组件计）"]
        for c in sec["old_cells"]:
            lines.append(f"- {c['district']}/{c['service']}/{c['window']}："
                         f"{c['timely']}/{c['total']} 及时")
        lines += ["", "### 新考核汇总（居民等待，分母按联办件计）"]
        for c in sec["new_cells"]:
            lines.append(f"- {c['district']}/{c['service']}：{c['timely']}/{c['total']} 及时")
        bw = sec["new_by_window"]
        if bw["slices"]:
            lines += ["", f"涉及窗口切片（{bw['note']}）："]
            for w, sl in bw["slices"].items():
                lines.append(f"- {w}：{sl['timely']}/{sl['total']}（{', '.join(sl['items'])}）")
            lines.append(f"- 合计（按联办件去重）：{bw['combined']['timely']}/{bw['combined']['total']}")
    if analysis["legacy"]:
        lines += ["", "## 既有汇总（仅月度汇总，保持有效，不可推至联办件）"]
        for row in analysis["legacy"]:
            lines.append(f"- {row['period']} {row['district']}/{row['service']}/{row['window']}："
                         f"{row['timely']}/{row['total']} 及时")
    lines += ["", "## 可比性注记"] + [f"- {n}" for n in analysis["comparability_notes"]] + [""]
    return "\n".join(lines)


# ---------- bundle ----------
def cmd_bundle(args):
    source, outbox = _load(args)
    cutoff = as_moment(args.cutoff)
    names = [r["recipient"] for r in source.rows("recipients")] if args.all else [args.recipient]
    for name in names:
        recipient = source.recipient(name)
        existing = outbox.find_bundle_for(name, args.period)
        if existing and existing["state"] in ("签收", "送出"):
            print(f"{name} 的 {args.period} 包已{existing['state']}（{existing['bundle']}），"
                  f"不重复生成；事实更正请用 correct")
            continue
        bundle_id = existing["bundle"] if existing else outbox.next_bundle_id()
        bundle = build_bundle(source, recipient, args.period, cutoff, bundle_id)
        problems = check_consistency(bundle, recipient["parts"])
        bundle_dir = Path(args.results) / "bundles"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / f"{bundle_id}.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if problems:
            if existing:
                outbox.regenerate(bundle_id, bundle["facts_at"], cutoff, note="按新口径截点重出")
            else:
                outbox.add(bundle_id, "上报包", name, args.period, bundle["facts_at"], cutoff,
                           note="生成")
            outbox.transition(bundle_id, "受阻", cutoff, note="；".join(problems))
            print(f"{bundle_id}（{name}）受阻：{'；'.join(problems)}")
            continue
        if existing:
            outbox.regenerate(bundle_id, bundle["facts_at"], cutoff,
                              note="按同一套有效事实重核后重出")
            print(f"{bundle_id}（{name}）已按口径截点 {bundle['facts_at']} 重出，原生成作废")
        else:
            outbox.add(bundle_id, "上报包", name, args.period, bundle["facts_at"], cutoff,
                       note="生成")
            print(f"{bundle_id}（{name}）已生成，口径截点 {bundle['facts_at']}，"
                  f"组成：{'、'.join(bundle['parts'])}")
    _save_outbox(args, outbox)


# ---------- send ----------
def cmd_send(args):
    source, outbox = _load(args)
    send_at = as_moment(args.at)
    receipt = outbox.find(args.bundle)
    if receipt is None:
        sys.exit(f"没有回执 {args.bundle}")
    if receipt["kind"] == "更正说明":
        outbox.transition(args.bundle, "送出", send_at, note="更正说明送出")
        _save_outbox(args, outbox)
        print(f"{args.bundle}（更正说明 -> {receipt['recipient']}）已送出")
        return
    bundle_path = Path(args.results) / "bundles" / f"{args.bundle}.json"
    if not bundle_path.exists():
        sys.exit(f"缺少包文件 {bundle_path}，请先用 bundle 生成")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    recipient = source.recipient(receipt["recipient"])
    problems = check_consistency(bundle, recipient["parts"])
    if problems:
        outbox.transition(args.bundle, "受阻", send_at, note="；".join(problems))
        _save_outbox(args, outbox)
        sys.exit(f"{args.bundle} 受阻：{'；'.join(problems)}")
    ok, fresh = verify_at_send(source, bundle, send_at)
    if not ok:
        outbox.transition(args.bundle, "受阻", send_at,
                          note="送出时重核不一致：事实已变化，整包须按同一套有效事实重出，"
                               "不允许只更新能算的一张就发送整包")
        _save_outbox(args, outbox)
        sys.exit(f"{args.bundle} 送出被拦截：重核不一致，须整包重出")
    due = as_moment(bundle["due"])
    timing = "按期" if send_at <= due else "逾期"
    outbox.transition(args.bundle, "送出", send_at,
                      note=f"{timing}送出（限 {bundle['due']}），重核一致 {fresh['digest']}")
    _save_outbox(args, outbox)
    print(f"{args.bundle}（{receipt['recipient']}）已{timing}送出，重核一致")


# ---------- correct ----------
def cmd_correct(args):
    source, outbox = _load(args)
    notices = corrections.generate_corrections(source, outbox, args.now, args.results)
    _save_outbox(args, outbox)
    if not notices:
        print("没有受影响的已签收包，无需更正说明；其他收件方不重复收包")
        return
    for n in notices:
        print(f"{n['notice']}（针对 {n['about']} -> {n['recipient']}）已生成："
              f"本期更正 {len(n['本期更正'])} 条，失效对比区间 {len(n['不再成立的对比区间'])} 条")


# ---------- ack / return ----------
def cmd_ack(args):
    source, outbox = _load(args)
    outbox.transition(args.bundle, "签收", args.at, note=args.note or "对方签收回执")
    _save_outbox(args, outbox)
    print(f"{args.bundle} 已签收")


def cmd_return(args):
    source, outbox = _load(args)
    note = "退回比较项：" + "、".join(args.items) if args.items else (args.note or "退回")
    outbox.transition(args.bundle, "退回", args.at, note=note)
    _save_outbox(args, outbox)
    print(f"{args.bundle} 已退回；可用 trace --item <比较项> 查不支持的历史材料或适用口径")


# ---------- trace ----------
def cmd_trace(args):
    source, _ = _load(args)
    cutoff = as_moment(args.cutoff) if args.cutoff else max(source.fact_moments())
    result = trace_item(source, args.item, cutoff)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------- compare ----------
def cmd_compare(args):
    source, _ = _load(args)
    cutoff = as_moment(args.cutoff) if args.cutoff else max(source.fact_moments())
    a = _resolve(source, args.a, cutoff)
    b = _resolve(source, args.b, cutoff)
    print(json.dumps(compare_items(a, b), ensure_ascii=False, indent=2))


def _resolve(source, item_id, cutoff):
    if item_id.startswith("legacy:"):
        for row in legacy_cells(source, "全市"):
            if row["id"] == item_id:
                return {"id": item_id, "kind": "legacy", "convention": "窗口旧口径（月度汇总）",
                        "period": row["period"], "calendar": "旧历", "facts_at": None}
        raise KeyError(item_id)
    result = trace_item(source, item_id, cutoff)
    detail = result["明细"]
    kind = "old" if item_id.startswith("old:") else "new"
    case = detail.get("case")
    period = source.case_row(case)["period"] if case else None
    return {"id": item_id, "kind": kind, "convention": detail.get("convention"),
            "period": period, "calendar": detail.get("calendar"),
            "facts_at": fmt_moment(cutoff)}


# ---------- status ----------
def cmd_status(args):
    source, outbox = _load(args)
    if not outbox.receipts:
        print("（无回执）")
        return
    for r in outbox.receipts:
        last = r["history"][-1]
        about = f"（针对 {r['about']}）" if r["about"] else ""
        print(f"{r['bundle']:<10} {r['kind']} {r['recipient']:<6} {r['period']} "
              f"状态={r['state']} 截点={r['facts_at']} 最近={last['at']} {about}")
        for h in r["history"]:
            print(f"    {h['at']} {h['state']} {h['note']}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="timeliness", description="联办件时限重算与报送")
    parser.add_argument("--data", default="data/supplies.json", help="业务来源资料")
    parser.add_argument("--results", default="results", help="结果与回执目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("compute", help="按口径截点重算全部指标")
    p.add_argument("--cutoff", required=True)
    p.set_defaults(fn=cmd_compute)

    p = sub.add_parser("bundle", help="生成上报包（未交付的按同一套有效事实重核）")
    p.add_argument("--recipient")
    p.add_argument("--all", action="store_true")
    p.add_argument("--period", required=True)
    p.add_argument("--cutoff", required=True)
    p.set_defaults(fn=cmd_bundle)

    p = sub.add_parser("send", help="送出前重核整包，一致才发送")
    p.add_argument("--bundle", required=True)
    p.add_argument("--at", required=True)
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("correct", help="为已签收但受事实更正影响的包生成更正说明")
    p.add_argument("--now", required=True)
    p.set_defaults(fn=cmd_correct)

    p = sub.add_parser("ack", help="登记签收回执")
    p.add_argument("--bundle", required=True)
    p.add_argument("--at", required=True)
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_ack)

    p = sub.add_parser("return", help="登记退回回执（接收回退也必须有回执）")
    p.add_argument("--bundle", required=True)
    p.add_argument("--at", required=True)
    p.add_argument("--items", nargs="*", default=[])
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_return)

    p = sub.add_parser("trace", help="查比较项由哪段历史材料或哪种口径支持/不支持")
    p.add_argument("--item", required=True)
    p.add_argument("--cutoff", default=None)
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("compare", help="并列两个比较项并标注差异来源")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--cutoff", default=None)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("status", help="查看本地接收回执")
    p.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    if getattr(args, "all", False) is False and getattr(args, "recipient", None) is None \
            and args.cmd == "bundle":
        parser.error("bundle 需要 --recipient 或 --all")
    args.fn(args)


if __name__ == "__main__":
    main()
