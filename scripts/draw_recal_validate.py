"""平局输出层校准验证：CV 选锚点（2324↔2425），2526 只做留出。

三项验收：
1) 留出 Brier、LogLoss 都下降；
2) 方向命中率下降不超过 1pp；
3) 留出集按“原分档 cohort”重算，两端 |z| < 1。
z 口径：(实际平局数 − 定价平局质量) / sqrt(定价平局质量)。
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402

from data.historical_data import load_seasons  # noqa: E402
from data.draw_map import recalibrate_draw, load_map  # noqa: E402
from scripts.backtest import _fit_dc  # noqa: E402
from scripts.draw_calibration import _probs  # noqa: E402

FIT = ("2324", "2425")
HOLDOUT = "2526"
ROWS_PATH = os.path.join("data", "walkforward_rows.json")


def season_label(d) -> str:
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y % 100}{(y + 1) % 100}"


def build_rows() -> list[dict]:
    if os.path.exists(ROWS_PATH):
        with open(ROWS_PATH, encoding="utf-8") as f:
            return json.load(f)
    df = load_seasons()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["Season"] = [season_label(d) for d in df["Date"]]
    df["round_key"] = df["Date"].dt.to_period("W")
    rows = []
    for rk in sorted(df["round_key"].unique()):
        past = df[df["round_key"] < rk]
        cur = df[df["round_key"] == rk]
        if len(past) < 100 or cur.empty:
            continue
        dc = _fit_dc(past)
        for row in cur.itertuples():
            pr = _probs(dc, row.HomeTeam, row.AwayTeam)
            if not pr:
                continue
            rows.append({"season": row.Season, "pred_draw": pr["draw"],
                         "pred_home": pr["home_win"], "pred_away": pr["away_win"],
                         "actual_draw": int(row.FTR == "D"), "ftr": row.FTR})
    with open(ROWS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return rows


def prep(rows: list[dict], knots) -> list[dict]:
    for r in rows:
        r["actual_home"] = int(r["ftr"] == "H")
        r["actual_away"] = int(r["ftr"] == "A")
        r["after"] = recalibrate_draw({"home_win": r["pred_home"], "draw": r["pred_draw"],
                                       "away_win": r["pred_away"]}, knots)
    return rows


def metrics(rows: list[dict], after: bool = False) -> dict:
    b = ll = 0.0
    ok = 0
    for r in rows:
        p = r["after"] if after else {"home_win": r["pred_home"], "draw": r["pred_draw"],
                                      "away_win": r["pred_away"]}
        b += ((p["home_win"] - r["actual_home"]) ** 2 + (p["draw"] - r["actual_draw"]) ** 2 +
              (p["away_win"] - r["actual_away"]) ** 2)
        pk = max(p, key=p.get)
        act = "home_win" if r["actual_home"] else ("draw" if r["actual_draw"] else "away_win")
        ok += int(pk == act)
        ll += -math.log(max(p[act], 1e-12))
    n = len(rows)
    return {"brier": round(b / n, 4), "logloss": round(ll / n, 4), "hit": round(ok / n, 4)}


def cohort(rows: list[dict], after: bool) -> list[dict]:
    segs = [("≤0.15（强队场）", lambda p: p <= 0.15),
            ("0.15–0.30（均势偏）", lambda p: 0.15 < p <= 0.30),
            ("≥0.30（均势）", lambda p: p > 0.30)]
    out = []
    for name, fn in segs:
        sel = [r for r in rows if fn(r["pred_draw"])]   # 按“原分档”固定 cohort
        mass = sum((r["after"]["draw"] if after else r["pred_draw"]) for r in sel)
        act = sum(r["actual_draw"] for r in sel)
        out.append({"segment": name, "n": len(sel), "pred_mass": round(mass, 2),
                    "actual_draws": act,
                    "z": round((act - mass) / math.sqrt(max(mass, 1e-9)), 2)})
    return out


def bins(rows: list[dict], after: bool) -> list[dict]:
    out = []
    for i in range(13):
        lo = round(i * 0.05, 2)
        sel = [r for r in rows
               if lo <= (r["after"]["draw"] if after else r["pred_draw"]) < lo + 0.05 - 1e-9]
        if not sel:
            continue
        mass = sum((r["after"]["draw"] if after else r["pred_draw"]) for r in sel)
        act = sum(r["actual_draw"] for r in sel)
        out.append({"bin": f"{lo:.2f}-{lo + 0.05:.2f}", "n": len(sel),
                    "pred_mass": round(mass, 2), "actual_draws": act,
                    "z": round((act - mass) / math.sqrt(max(mass, 1e-9)), 2)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("data", "draw_recal_report.json"))
    a = ap.parse_args()
    rows = build_rows()
    knots = load_map()

    fit_rows = prep([r for r in rows if r["season"] in FIT], knots)
    hold_rows = prep([r for r in rows if r["season"] == HOLDOUT], knots)

    rep = {
        "map": knots,
        "fit_seasons": list(FIT), "fit_n": len(fit_rows),
        "holdout": HOLDOUT, "holdout_n": len(hold_rows),
        "holdout_metrics": {"before": metrics(hold_rows), "after": metrics(hold_rows, True)},
        "segments_before": cohort(hold_rows, False),
        "segments_after": cohort(hold_rows, True),
        "bins_before": bins(hold_rows, False),
        "bins_after": bins(hold_rows, True),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    print(f"锚点 {knots}｜拟合 {FIT}（{len(fit_rows)} 场）→ 留出 {HOLDOUT}（{len(hold_rows)} 场）")
    for k in ("brier", "logloss", "hit"):
        before = rep["holdout_metrics"]["before"][k]
        after = rep["holdout_metrics"]["after"][k]
        print(f"{k:<8}{before:.4f} → {after:.4f}（Δ {after - before:+.4f}）")
    for tag in ("segments_before", "segments_after"):
        print(tag)
        for s in rep[tag]:
            print(f"  {s['segment']:<18}n={s['n']:>4} 定价{s['pred_mass']:>7.2f} "
                  f"兑现{s['actual_draws']:>3} z={s['z']:+.2f}")
    print("已写入", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
