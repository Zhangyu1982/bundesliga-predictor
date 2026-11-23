"""平局定价 vs 兑现：只出数字，不下结论。

口径（用户定）：把历史所有预测的 draw 概率按 5pp 分档，统计每档实际平局率。
- 若每档都偏低 3–5pp → 全局压缩；
- 若只有 0.10–0.15 档偏低 → 极端强队场次的先验问题。
两种修法不同，本脚本**只给表**，判定留给人。

历史口径：对 3 赛季做 walk-forward（每轮只用该轮之前的比赛拟合 DC），
概率口径与生产一致（attack/defence 归一到均值 1.0，λ 用 rho 修正的 DC 泊松）。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd  # noqa: E402
from data.historical_data import load_seasons  # noqa: E402
from config import DC_RHO  # noqa: E402
from scripts.backtest import _fit_dc  # noqa: E402

BIN = 0.05


def _tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def _probs(dc: dict, home: str, away: str, rho: float = DC_RHO, max_goals: int = 8) -> dict | None:
    att, dfn = dc.get("attack") or {}, dc.get("defence") or {}
    if home not in att or away not in att:
        return None
    # 与生产一致：归一到「平均队 = 1.0」
    ma = sum(att.values()) / len(att)
    if ma <= 0:
        return None
    a_h, a_a = att[home] / ma, att[away] / ma
    d_h, d_a = dfn[home] * ma, dfn[away] * ma
    lam = dc["mu_home"] * a_h * d_a
    mu = dc["mu_away"] * a_a * d_h
    p = [math.exp(-lam) * lam ** i / math.factorial(i) for i in range(max_goals + 1)]
    q = [math.exp(-mu) * mu ** j / math.factorial(j) for j in range(max_goals + 1)]
    hw = dr = aw = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            v = p[i] * q[j] * _tau(i, j, lam, mu, rho)
            if v < 0:
                v = 0.0
            if i > j:
                hw += v
            elif i == j:
                dr += v
            else:
                aw += v
    tot = hw + dr + aw
    if tot <= 0:
        return None
    return {"home_win": hw / tot, "draw": dr / tot, "away_win": aw / tot}


def walk_forward(seasons: list[str] | None = None) -> list[dict]:
    """逐轮 walk-forward，产出每场 (pred_draw, actual_draw)。"""
    df = load_seasons(seasons)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["round_key"] = df["Date"].dt.to_period("W")
    out = []
    rounds = list(df["round_key"].unique())
    for rk in rounds:
        past = df[df["round_key"] < rk]
        cur = df[df["round_key"] == rk]
        if len(past) < 100 or cur.empty:
            continue
        dc = _fit_dc(past)
        for row in cur.itertuples():
            pr = _probs(dc, row.HomeTeam, row.AwayTeam)
            if not pr:
                continue
            out.append({"pred_draw": pr["draw"], "pred_home": pr["home_win"],
                        "pred_away": pr["away_win"],
                        "actual_draw": int(row.FTR == "D"), "source": "walkforward"})
    return out


def bin_table(rows: list[dict]) -> list[dict]:
    bins: dict[float, list[dict]] = {}
    for r in rows:
        p = float(r["pred_draw"])
        lo = math.floor(p / BIN) * BIN
        lo = round(max(0.0, lo), 2)
        bins.setdefault(lo, []).append(r)
    table = []
    for lo in sorted(bins):
        rs = bins[lo]
        n = len(rs)
        mean_p = sum(r["pred_draw"] for r in rs) / n
        rate = sum(r["actual_draw"] for r in rs) / n
        table.append({"bin": f"{lo:.2f}-{lo + BIN:.2f}", "bin_lo": lo, "n": n,
                      "mean_pred_draw": round(mean_p, 4),
                      "actual_draw_rate": round(rate, 4),
                      "gap_rate_minus_pred": round(rate - mean_p, 4)})
    return table


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("data", "draw_calibration.json"))
    ap.add_argument("--seasons", nargs="*", default=None)
    a = ap.parse_args()
    rows = walk_forward(a.seasons)

    # 叠加真实赛季已入库样本（只有带 pred_draw 的记录可用）
    live_path = os.path.join("data", "live_results.json")
    live_rows = []
    try:
        with open(live_path, encoding="utf-8") as f:
            for r in json.load(f).get("records", []):
                if r.get("pred_draw") is None or r.get("actual_ftr") is None:
                    continue
                live_rows.append({"pred_draw": float(r["pred_draw"]),
                                  "pred_home": r.get("pred_home"),
                                  "pred_away": r.get("pred_away"),
                                  "actual_draw": int(r["actual_ftr"] == "D"),
                                  "source": "live"})
    except (OSError, ValueError):
        live_rows = []

    allrows = rows + live_rows
    table = bin_table(allrows)
    overall = {"n": len(allrows),
               "actual_draw_rate": round(sum(r["actual_draw"] for r in allrows) / len(allrows), 4),
               "mean_pred_draw": round(sum(r["pred_draw"] for r in allrows) / len(allrows), 4)}
    res = {"bin_width": BIN, "overall": overall,
           "walkforward_n": len(rows), "live_n": len(live_rows), "table": table}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"平局定价 vs 兑现（5pp 分档）｜历史 walk-forward {len(rows)} 场 + 真实赛季 {len(live_rows)} 场")
    print(f"{'区间':<12}{'n':>6}{'平均定价':>10}{'实际平局率':>12}{'差(pp)':>9}")
    for t in table:
        print(f"{t['bin']:<12}{t['n']:>6}{t['mean_pred_draw']:>10.3f}"
              f"{t['actual_draw_rate']:>12.3f}{t['gap_rate_minus_pred'] * 100:>9.1f}")
    print(f"{'合计':<12}{overall['n']:>6}{overall['mean_pred_draw']:>10.3f}"
          f"{overall['actual_draw_rate']:>12.3f}"
          f"{(overall['actual_draw_rate'] - overall['mean_pred_draw']) * 100:>9.1f}")
    print(f"已写入 {a.out}（只给数字，不在此下结论）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
