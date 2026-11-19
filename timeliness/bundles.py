"""上报包：一个收件方、一个期间、一个口径截点、按订阅组成。

一致性规则：
- 同一上报包的旧考核、新考核、覆盖说明必须出自同一口径截点；
- 组成之间不能互作替代（旧考核不能顶替覆盖说明，反之亦然）；
- 送出前按同一套有效事实重核整包；任一组对不上，整包不得发送，
  不允许只更新能算的一张就发送整包。
"""
from __future__ import annotations

import hashlib
import json

from .coverage import build_coverage
from .measures import collect_items
from .metrics import NEW_DENOMINATOR, OLD_DENOMINATOR, new_cells, new_cells_by_window, old_cells
from .source import as_moment, fmt_moment

PART_NAMES = ("旧考核", "新考核", "覆盖说明")


def _digest(parts: dict) -> str:
    """内容摘要：口径截点本身是元数据（由 check_consistency 单独核对），
    摘要只覆盖由有效事实决定的计算内容，用于送出前重核与版本比对。"""
    content = {
        name: {k: v for k, v in part.items() if k != "facts_at"}
        for name, part in parts.items()
    }
    blob = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _part_old(source, cutoff, period, district, old_items) -> dict:
    cases = {r["case"]: r for r in source.rows("cases")}
    case_gaps = []
    linked_cases = {it.case for it in old_items}
    for row in source.rows("cases"):
        if row["period"] != period:
            continue
        if district != "全市" and row["district"] != district:
            continue
        if row["case"] not in linked_cases:
            case_gaps.append({
                "case": row["case"],
                "reasons": ["缺少有效联办关系"],
                "missing": [f"{row['case']} 在口径截点没有有效的联办关系（case_links）"],
            })
    return {
        "part": "旧考核",
        "facts_at": fmt_moment(cutoff),
        "period": period,
        "district": district,
        "convention": "窗口旧口径",
        "rule": source.rule("窗口旧口径"),
        "denominator": OLD_DENOMINATOR,
        "cells": old_cells(source, old_items, district, period),
        "items": [it.to_dict() for it in old_items
                  if cases[it.case]["period"] == period
                  and (district == "全市" or cases[it.case]["district"] == district)],
        "case_gaps": case_gaps,
    }


def _part_new(source, cutoff, period, district, new_items) -> dict:
    cases = {r["case"]: r for r in source.rows("cases")}
    return {
        "part": "新考核",
        "facts_at": fmt_moment(cutoff),
        "period": period,
        "district": district,
        "convention": "居民新口径",
        "rule": source.rule("居民新口径"),
        "denominator": NEW_DENOMINATOR,
        "cells": new_cells(source, new_items, district, period),
        "by_window": new_cells_by_window(source, new_items, cutoff, district, period),
        "items": [it.to_dict() for it in new_items
                  if cases[it.case]["period"] == period
                  and (district == "全市" or cases[it.case]["district"] == district)],
    }


def build_bundle(source, recipient: dict, period: str, cutoff, bundle_id: str) -> dict:
    """按收件方订阅组成构建上报包；所有组成共用同一口径截点。"""
    cutoff = as_moment(cutoff)
    district = recipient["district"]
    old_items, new_items = collect_items(source, cutoff)
    parts = {}
    for name in recipient["parts"]:
        if name == "旧考核":
            parts[name] = _part_old(source, cutoff, period, district, old_items)
        elif name == "新考核":
            parts[name] = _part_new(source, cutoff, period, district, new_items)
        elif name == "覆盖说明":
            parts[name] = build_coverage(source, cutoff, period, district, old_items, new_items)
        else:
            raise ValueError(f"未知包组成 {name}")
    bundle = {
        "bundle": bundle_id,
        "recipient": recipient["recipient"],
        "district": district,
        "period": period,
        "facts_at": fmt_moment(cutoff),
        "due": recipient["due"],
        "parts": parts,
    }
    bundle["digest"] = _digest(parts)
    return bundle


def check_consistency(bundle: dict, subscription: list) -> list:
    """组成本身的一致性：齐全、不互替、同一口径截点。返回问题列表。"""
    problems = []
    if set(bundle["parts"]) != set(subscription):
        problems.append(
            f"包组成 {sorted(bundle['parts'])} 与订阅 {sorted(subscription)} 不一致"
        )
    for name, part in bundle["parts"].items():
        if part.get("part") != name:
            problems.append(f"组成 {name} 被其他内容替代（实际为 {part.get('part')}）")
        if part.get("facts_at") != bundle["facts_at"]:
            problems.append(
                f"组成 {name} 的口径截点 {part.get('facts_at')} 与包截点 {bundle['facts_at']} 不一致"
            )
    return problems


def verify_at_send(source, bundle: dict, send_at):
    """送出前按送出时点的同一套有效事实重核整包。

    返回 (是否一致, 重核后的包)。不一致时整包不得发送。
    """
    recipient = {
        "recipient": bundle["recipient"],
        "district": bundle["district"],
        "parts": list(bundle["parts"]),
        "due": bundle["due"],
    }
    fresh = build_bundle(source, recipient, bundle["period"], as_moment(send_at), bundle["bundle"])
    return fresh["digest"] == bundle["digest"], fresh


def _diff_items(was_items, now_items, part_name):
    changes = []
    was = {it["item"]: it for it in was_items}
    now = {it["item"]: it for it in now_items}
    for iid in sorted(was.keys() - now.keys()):
        changes.append({"part": part_name, "scale": "项", "item": iid, "change": "移出",
                        "was": _brief(was[iid]), "now": None})
    for iid in sorted(now.keys() - was.keys()):
        changes.append({"part": part_name, "scale": "项", "item": iid, "change": "新增",
                        "was": None, "now": _brief(now[iid])})
    for iid in sorted(was.keys() & now.keys()):
        a, b = was[iid], now[iid]
        for field in ("status", "days", "timely", "excluded_days", "start", "end"):
            if a.get(field) != b.get(field):
                changes.append({
                    "part": part_name, "scale": "项", "item": iid, "change": f"{field} 变化",
                    "was": a.get(field), "now": b.get(field),
                })
    return changes


def _brief(item):
    if item is None:
        return None
    return {k: item.get(k) for k in ("status", "days", "timely", "window") if item.get(k) is not None}


def _diff_cells(was_cells, now_cells, part_name, keys):
    changes = []
    was = {tuple(c[k] for k in keys): c for c in was_cells}
    now = {tuple(c[k] for k in keys): c for c in now_cells}
    for key in sorted(was.keys() | now.keys()):
        a, b = was.get(key), now.get(key)
        if a is None:
            changes.append({"part": part_name, "scale": "单元", "cell": "/".join(key),
                            "change": "新增", "was": None,
                            "now": {"total": b["total"], "timely": b["timely"]}})
        elif b is None:
            changes.append({"part": part_name, "scale": "单元", "cell": "/".join(key),
                            "change": "移出", "was": {"total": a["total"], "timely": a["timely"]},
                            "now": None})
        elif (a["total"], a["timely"]) != (b["total"], b["timely"]):
            changes.append({
                "part": part_name, "scale": "单元", "cell": "/".join(key),
                "change": "数值变化",
                "was": {"total": a["total"], "timely": a["timely"]},
                "now": {"total": b["total"], "timely": b["timely"]},
            })
    return changes


def diff_bundles(was: dict, now: dict) -> list:
    """两版同一上报包的差异（用于本期更正）。覆盖说明由其他组成派生，不单独比对。"""
    changes = []
    for name in was["parts"]:
        if name not in now["parts"]:
            changes.append({"part": name, "scale": "包", "change": "新版缺少该组成"})
            continue
        a, b = was["parts"][name], now["parts"][name]
        if name == "旧考核":
            changes += _diff_items(a["items"], b["items"], name)
            changes += _diff_cells(a["cells"], b["cells"], name, ("period", "district", "service", "window"))
        elif name == "新考核":
            changes += _diff_items(a["items"], b["items"], name)
            changes += _diff_cells(a["cells"], b["cells"], name, ("period", "district", "service"))
    return changes
