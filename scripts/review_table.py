"""复盘表：把 shift(主/客) 与 λ 合计并列打出来，先看分布，不写结论。

用法：
  python -m scripts.review_table --round 10          # 某轮
  python -m scripts.review_table --round 10 --season 2627
  python -m scripts.review_table --last              # 最近一轮有赛果的

口径说明：
  - λ 主/客：脚本用当前训练产物（bundle.dc_lambda）**复算**的期望进球，不是预测当时的快照；
    团队没有历史参数时按联赛平均（1.0），该场 λ 仅供参考。
  - λ 合计 = λ主 + λ客（期望进球），**不要和 Top1 比分求和混用**（后者是"最可能单一结果"，必然偏低）。
  - shift 主/客：来自赛果记录的 shift_home / shift_away（2026-11-18 起录入时写入）；
    老记录没有该字段 → 显示 —，不补 0。
  - 本表只做分布呈现；命中率、Brier 等结论另行输出。
"""
from __future__ import annotations

import argparse
import math
import sys

from data.live_results import load_live_results


def _eng_name(name: str) -> str:
    """赛果里的 football-data 英文名 → 模型训练用的英文名（同名时原样返回）。"""
    try:
        from data.api_client import fdo_team_to_code
        from pipeline import CODE2ENG
        code = fdo_team_to_code(name)
        if code and code in CODE2ENG:
            return CODE2ENG[code]
    except Exception:
        pass
    return name


def _lam(bundle, home: str, away: str) -> tuple[float, float]:
    """复算 λ（与 app 同一条口径：先归一到模型英文名再算）；升班马按联赛平均，值仍可用。"""
    try:
        lam, mu = bundle.dc_lambda(_eng_name(home), _eng_name(away))
        return float(lam), float(mu)
    except Exception:
        return 1.74, 1.46      # 联赛平均（dc_mu_home / dc_mu_away）


def build_rows(records, bundle) -> list[dict]:
    rows = []
    for r in records or []:
        lam_h, lam_a = _lam(bundle, r.get("home", ""), r.get("away", ""))
        hg, ag = r.get("actual_hg"), r.get("actual_ag")
        actual = "" if hg is None or ag is None else f"{hg}:{ag}"
        rows.append({
            "home": r.get("home", ""), "away": r.get("away", ""),
            "actual": actual,
            "lam_home": round(lam_h, 2), "lam_away": round(lam_a, 2),
            "lam_total": round(lam_h + lam_a, 2),
            "shift_home": r.get("shift_home"),     # None = 老记录没这列，展示为 —
            "shift_away": r.get("shift_away"),
            "pick": r.get("pred_pick") or "—",
            "hit": r.get("pred_hit"),
        })
    return rows


def summarize(rows) -> dict:
    """只汇总数字，不下结论。z 用泊松方差（sd = sqrt(实际总进球)）。"""
    lam_total = round(sum(r["lam_total"] for r in rows), 1)
    actual_total = 0
    for r in rows:
        if r.get("actual"):
            hg, ag = (int(x) for x in r["actual"].split(":"))
            actual_total += hg + ag
    diff = round(lam_total - actual_total, 1)
    z = round(diff / math.sqrt(actual_total), 2) if actual_total > 0 else None
    hits = sum(1 for r in rows if r.get("hit") == 1)
    return {"count": len(rows), "lam_total": lam_total, "actual_total": actual_total,
            "diff": diff, "z": z, "hits": hits,
            "hit_rate": round(hits / len(rows), 3) if rows else None}


def _fmt_shift(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def format_table(rows) -> str:
    head = ("| 场次 | 实际 | λ主 | λ客 | λ合计 | shift主 | shift客 | 推荐 | 命中 |\n"
            "|---|---|---|---|---|---|---|---|---|")
    body = []
    for i, r in enumerate(rows, 1):
        body.append("| {n}. {h} vs {a} | {act} | {lh} | {la} | {lt} | {sh} | {sa} | {pick} | {hit} |".format(
            n=i, h=r["home"], a=r["away"], act=r.get("actual") or "—",
            lh=r["lam_home"], la=r["lam_away"], lt=r["lam_total"],
            sh=_fmt_shift(r.get("shift_home")), sa=_fmt_shift(r.get("shift_away")),
            pick=r.get("pick") or "—",
            hit="✓" if r.get("hit") == 1 else ("✗" if r.get("hit") == 0 else "—")))
    return head + "\n" + "\n".join(body)


def _pick_round(records, round_no=None, last=False) -> list[dict]:
    rounds = sorted({r.get("round_no") for r in records if r.get("round_no")})
    if last:
        round_no = rounds[-1] if rounds else None
    if round_no is None:
        return []
    return [r for r in records if r.get("round_no") == round_no]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="复盘表：shift + λ 合计并列")
    ap.add_argument("--round", type=int, default=None, help="轮次")
    ap.add_argument("--last", action="store_true", help="取最近一轮有赛果的")
    ap.add_argument("--season", default=None, help="赛季，如 2627（默认不过滤）")
    args = ap.parse_args(argv)

    from pipeline import PredictionPipeline
    pipe = PredictionPipeline()

    records = load_live_results()
    if args.season:
        records = [r for r in records if str(r.get("season")) == str(args.season)]
    rows = build_rows(_pick_round(records, args.round, args.last), pipe._bundle)
    if not rows:
        print("没有该轮赛果记录")
        return 1

    print(format_table(rows))
    s = summarize(rows)
    print()
    print(f"λ 合计 {s['lam_total']} · 实际 {s['actual_total']} · 差 {s['diff']} · z={s['z']}"
          f"（泊松口径，只做分布呈现，不作结论）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
