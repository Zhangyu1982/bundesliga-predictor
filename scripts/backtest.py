"""
P3 闭环基础 · 历史偏差回溯（walk-forward，无未来泄漏）
========================================================
用近 3 赛季德甲历史 CSV：
  1. Elo 逐轮滚动 + DC 每轮重拟合，预测每一场（只用该场之前的数据）。
  2. 写入 R5 SQLite 记录库（历史结果 vs 预测结果比对）。
  3. R1 实测：连续同方向偏差轮数 T 的"偏差惯性"（P_T vs 基线），确定触发重校准的最优轮数。
  4. R4 实测：Elo 与 DC 分歧强度(Δp)分桶下的爆冷率，确定 submodel_disagreement_threshold。
输出 calibration report，用于把实测值填入 config.RULES 并置 calibrated=True。

运行：python3 scripts/backtest.py
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.historical_data import load_seasons
from data_stores.bias_store import BiasStore
from config import DC_RHO, DC_DECAY


# ---------------- 轻量 DC 拟合（回测用，速度快，不做 rho 网格） ----------------
def _fit_dc(past: pd.DataFrame) -> dict:
    past = past.copy()
    past["Date"] = pd.to_datetime(past["Date"])   # 兼容新版 pandas：dt 访问器要求真 datetime
    teams = sorted(set(past["HomeTeam"]) | set(past["AwayTeam"]))
    if not teams:
        return {}
    mu_home = float(past["FTHG"].mean())
    mu_away = float(past["FTAG"].mean())
    dates = past["Date"]
    span = max((dates.max() - dates.min()).days, 1)
    wts = np.exp(-DC_DECAY * (dates.max() - dates).dt.days / span)
    attack = {t: 1.0 for t in teams}
    defence = {t: 1.0 for t in teams}
    for _ in range(6):
        for t in teams:
            hm = [(r.FTHG, wts[i], defence[r.AwayTeam])
                  for i, r in enumerate(past.itertuples()) if r.HomeTeam == t]
            num = sum(g * w for g, w, _ in hm); den = sum(w * mu_home * d for _, w, d in hm)
            attack[t] = max(num / den if den > 0 else 1.0, 0.2)
        for t in teams:
            am = [(r.FTAG, wts[i], attack[r.AwayTeam])
                  for i, r in enumerate(past.itertuples()) if r.AwayTeam == t]
            num = sum(g * w for g, w, _ in am); den = sum(w * mu_away * a for _, w, a in am)
            defence[t] = max(num / den if den > 0 else 1.0, 0.2)
    return {"attack": attack, "defence": defence, "mu_home": mu_home, "mu_away": mu_away}


def _dc_home_prob(dc: dict, home: str, away: str) -> float:
    if not dc or home not in dc["attack"] or away not in dc["attack"]:
        return 0.4
    lam = dc["mu_home"] * dc["attack"][home] * dc["defence"][away]
    mu = dc["mu_away"] * dc["attack"][away] * dc["defence"][home]
    hw = 0.0
    for i in range(9):
        for j in range(9):
            if i > j:
                hw += (np.exp(-lam) * lam ** i / math.factorial(i)
                       * np.exp(-mu) * mu ** j / math.factorial(j))
    return hw


def _elo_home_prob(ratings: dict, home: str, away: str, home_adv: float = 50.0,
                   scale: float = 400.0) -> float:
    dr = (ratings.get(home, 1500) + home_adv) - ratings.get(away, 1500)
    return 1.0 / (1.0 + 10 ** (-dr / scale))


def run_backtest() -> dict:
    df = load_seasons()
    df = df.sort_values(["season", "Date"]).reset_index(drop=True)
    # 每赛季按时间分轮（每轮 9 场）
    df["round_no"] = df.groupby("season").cumcount() // 9 + 1

    ratings = {}
    streak: dict[str, int] = {}
    past = pd.DataFrame(columns=df.columns)
    records = []
    r1 = {}   # streak_before -> (wrong, total)  偏差惯性
    r4 = {}   # Δp bucket -> (upset, total)  爆冷
    r4_div = {"div": [0, 0], "no_div": [0, 0]}

    for season, grp in df.groupby("season"):
        grp = grp.sort_values("Date")
        for r in sorted(grp["round_no"].unique()):
            round_df = grp[grp["round_no"] == r]
            dc = _fit_dc(past) if len(past) >= 40 else {}
            # —— 预测本轮的每场（只用本轮之前数据）——
            for m in round_df.itertuples():
                eh = _elo_home_prob(ratings, m.HomeTeam, m.AwayTeam)
                dc_h = _dc_home_prob(dc, m.HomeTeam, m.AwayTeam)
                elo_dir = "home_win" if eh > 0.5 else "away_win"
                dc_dir = "home_win" if dc_h > 0.5 else "away_win"
                favored = m.HomeTeam if elo_dir == "home_win" else m.AwayTeam
                dir_hit = (elo_dir == "home_win" and m.FTR == "H") or \
                          (elo_dir == "away_win" and m.FTR == "A")
                # 爆冷 = Elo 被看好方直接落败
                upset = (elo_dir == "home_win" and m.FTR == "A") or \
                        (elo_dir == "away_win" and m.FTR == "H")
                streak_before = streak.get(favored, 0)
                dp = abs(eh - dc_h)
                divergence = int(elo_dir != dc_dir)

                r1[streak_before] = r1.get(streak_before, [0, 0])
                r1[streak_before][0] += int(not dir_hit)   # 下一场也错
                r1[streak_before][1] += 1

                bucket = min(int(dp / 0.05), 6)
                r4[bucket] = r4.get(bucket, [0, 0])
                r4[bucket][0] += int(upset); r4[bucket][1] += 1
                if divergence:
                    r4_div["div"][0] += int(upset); r4_div["div"][1] += 1
                else:
                    r4_div["no_div"][0] += int(upset); r4_div["no_div"][1] += 1

                pick = m.HomeTeam if elo_dir == "home_win" else m.AwayTeam
                records.append({
                    "season": season, "round_no": int(r),
                    "match_id": f"BUN_{season}_R{int(r):02d}_{m.HomeTeam}_{m.AwayTeam}",
                    "home": m.HomeTeam, "away": m.AwayTeam,
                    "actual_hg": int(m.FTHG), "actual_ag": int(m.FTAG), "actual_ftr": m.FTR,
                    "pred_home": round(eh, 3), "pred_draw": 0.0, "pred_away": round(1 - eh, 3),
                    "pred_pick": pick, "pred_hit": int(dir_hit),
                    "elo_dir": elo_dir, "dc_dir": dc_dir,
                    "divergence": divergence, "bias_streak": streak_before,
                })
            # —— 本轮结束：更新 Elo 评分与偏差连续计数 ——
            for m in round_df.itertuples():
                eh = _elo_home_prob(ratings, m.HomeTeam, m.AwayTeam)
                favored = m.HomeTeam if eh > 0.5 else m.AwayTeam
                dir_hit = (eh > 0.5 and m.FTR == "H") or (eh <= 0.5 and m.FTR == "A")
                goal_margin = abs(m.FTHG - m.FTAG)
                w = 1.0 if goal_margin <= 1 else (1.5 if goal_margin <= 2 else 2.0)
                k = 20.0
                actual = 1.0 if m.FTR == "H" else (0.5 if m.FTR == "D" else 0.0)
                ratings[m.HomeTeam] = ratings.get(m.HomeTeam, 1500) + k * (actual - eh) * w
                ratings[m.AwayTeam] = ratings.get(m.AwayTeam, 1500) - k * (actual - eh) * w
                if dir_hit:
                    streak[favored] = 0
                else:
                    streak[favored] = streak.get(favored, 0) + 1
            past = pd.concat([past, round_df], ignore_index=True)

    store = BiasStore()
    store.write_records(records)
    return {"records": records, "r1": r1, "r4": r4, "r4_div": r4_div,
            "stats": store.calibration_stats()}


def calibrate_r1(r1: dict) -> dict:
    """R1 标定：找偏差惯性 P_T - 基线 最大的 T（样本量足够）。"""
    baseline_total = sum(v[1] for v in r1.values())
    baseline_wrong = sum(v[0] for v in r1.values())
    baseline = baseline_wrong / baseline_total if baseline_total else 0.0
    rows = []
    for t in range(1, 7):
        w, n = r1.get(t, [0, 0])
        if n < 10:
            continue
        p = w / n
        rows.append({"T": t, "n": n, "P_T": round(p, 3),
                     "lift": round(p - baseline, 3)})
    rows.sort(key=lambda x: (-x["lift"], -x["n"]))
    return {"baseline_wrong_rate": round(baseline, 3), "candidates": rows}


def calibrate_r4(r4: dict, r4_div: dict) -> dict:
    """R4 标定：Δp 分桶爆冷率；返回分歧强度阈值（首个明显高于基线的桶下界）。"""
    base_div = r4_div["div"][0] / r4_div["div"][1] if r4_div["div"][1] else 0.0
    base_nodiv = r4_div["no_div"][0] / r4_div["no_div"][1] if r4_div["no_div"][1] else 0.0
    buckets = []
    for b in range(7):
        w, n = r4.get(b, [0, 0])
        buckets.append({"bucket": f"{b*0.05:.2f}-{(b+1)*0.05:.2f}",
                        "n": n, "upset_rate": round(w / n, 3) if n else None})
    # 总爆冷基线
    tot = sum(v[1] for v in r4.values())
    totw = sum(v[0] for v in r4.values())
    baseline = totw / tot if tot else 0.0
    # 选阈值：第一个样本量>=20 且爆冷率 > baseline + 0.06 的桶下界
    threshold = None
    for b in range(7):
        w, n = r4.get(b, [0, 0])
        if n >= 20 and (w / n if n else 0) > baseline + 0.06:
            threshold = round(b * 0.05, 2)
            break
    if threshold is None:
        threshold = 0.10
    return {"baseline_upset": round(baseline, 3),
            "upset_divergence": round(base_div, 3),
            "upset_no_divergence": round(base_nodiv, 3),
            "buckets": buckets, "threshold": threshold}


if __name__ == "__main__":
    rep = run_backtest()
    print("=== R5 记录库 ===")
    print(rep["stats"])
    print("\n=== R1 标定（偏差惯性）===")
    print(calibrate_r1(rep["r1"]))
    print("\n=== R4 标定（Δp 分桶爆冷率）===")
    print(calibrate_r4(rep["r4"], rep["r4_div"]))
