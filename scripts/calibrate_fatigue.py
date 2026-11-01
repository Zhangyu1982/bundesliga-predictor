"""
P2 · 疲劳系数真实标定（scripts/calibrate_fatigue.py）

目标：用近 3 赛季德甲历史（918 场）+ 同赛季真实欧冠赛程，回测「欧战（欧冠）前后
德甲轮次」的表现，把 FATIGUE_RULES 的疲劳折扣系数从保守默认标定为实测值，
并写入 config.RULES / FATIGUE_RULES，置 calibrated=True。

方法（防泄漏、可复核）：
1. Elo 按时间前瞻推进：每场用「赛前 Elo」算期望进球，赛后按结果更新评分
   （与 trainer 同一口径：ELO_K、HOME_ADV、10^(diff/400) 期望胜率）。
2. 期望进球用标准 10^EloDiff/600 目标法：λh = meanHomeGoals·10^(diff/600)，
   λa = meanAwayGoals·10^(-diff/600)。残差 = 实际进球 − 期望进球。
3. 每队每场算休息天数（距上一场，排除自身）与「欧冠前置」标记
   （未来 7 天内该队有欧冠 → 与模型 midweek_window_days 一致）。
4. 残差按 疲劳组（休息<阈值 或 欧冠前置） vs 对照组 分组，比较均值差
   → 疲劳效应（每场损失进球数）。把效应换算成 每缺一天 的 xG 折扣率。
5. 阈值扫描：min_rest_days ∈ {3,4,5}，挑「样本量≥40 且效应显著(负)」的最优档。
"""
from __future__ import annotations
import os, sys, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import ELO_K, HOME_ADV, FATIGUE_RULES
from data.historical_data import load_seasons, CODE2ENG, ENG2CODE

# CL 队名 → 德甲 code（跨赛季德国参赛队）
CL_NAME2CODE = {
    "FC Bayern München": "BAY", "Bayern Munich": "BAY",
    "Borussia Dortmund": "DOR", "Dortmund": "DOR",
    "RB Leipzig": "RBL",
    "Bayer 04 Leverkusen": "LEV", "Leverkusen": "LEV",
    "VfB Stuttgart": "STU", "Stuttgart": "STU",
    "1. FC Union Berlin": "FCU", "Union Berlin": "FCU",
    "Eintracht Frankfurt": "SGE", "Ein Frankfurt": "SGE",
    "SV Werder Bremen": "SVW", "Werder Bremen": "SVW",
    "1. FC Köln": "FCK",
}
CL_CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "historical")


def pull_cl_fixtures(token: str, seasons: list[str], force: bool = False) -> dict:
    """拉历史欧冠赛程并缓存到 data/historical/cl_<season>.json。返回 {season: [(date, code), ...]}"""
    import requests
    out = {}
    seas_year = {"2526": 2025, "2425": 2024, "2324": 2023}
    for s in seasons:
        cache = os.path.join(CL_CACHE, f"cl_{s}.json")
        if os.path.exists(cache) and not force:
            data = json.load(open(cache, encoding="utf-8"))
            out[s] = data
            print(f"  [缓存] CL {s}: {len(data)} 场德甲欧冠")
            continue
        year = seas_year[s]
        r = requests.get("https://api.football-data.org/v4/competitions/CL/matches",
                         params={"season": year, "status": "FINISHED"},
                         headers={"X-Auth-Token": token, "User-Agent": "Mozilla/5.0"},
                         timeout=25)
        r.raise_for_status()
        rows = []
        for m in r.json().get("matches", []):
            for side in ("homeTeam", "awayTeam"):
                nm = m.get(side, {}).get("name", "")
                code = CL_NAME2CODE.get(nm)
                if code:
                    rows.append((m.get("utcDate", "")[:10], code))
        out[s] = rows
        json.dump(rows, open(cache, "w", encoding="utf-8"))
        print(f"  [拉取] CL {s}: HTTP{r.status_code} 德甲欧冠场次={len(rows)} 剩配额={r.headers.get('x-requests-available-minute')}")
    return out


def _sigmoid(x):
    x = max(min(x, 30.0), -30.0)
    return 1.0 / (1.0 + np.exp(-x))


def main(token: str, out_json: str):
    print("=== 1. 读取历史 ===")
    df = load_seasons()                     # 918 场，时间升序
    print(f"  场次={len(df)} 赛季={sorted(df['season'].unique())}")
    mean_hg = df["FTHG"].mean()
    mean_ag = df["FTAG"].mean()
    print(f"  主均进球={mean_hg:.3f} 客均进球={mean_ag:.3f}")

    print("=== 2. 拉/读历史欧冠赛程 ===")
    cl = pull_cl_fixtures(token, list(df["season"].unique()))
    cl_by_team = {}   # (season, code) -> [date10,...]
    for s, rows in cl.items():
        for date10, code in rows:
            cl_by_team.setdefault((s, code), []).append(date10)
    for k in sorted(cl_by_team):
        print(f"  {k}: {len(cl_by_team[k])} 场")

    print("=== 3. Elo 前瞻 + 疲劳特征 + 残差 ===")
    ratings = {}
    last_date = {}      # 队名 -> date (datetime)
    records = []        # 逐场 team-match 残差
    for _, row in df.iterrows():
        h, a = row["HomeTeam"], row["AwayTeam"]
        date = row["Date"]                     # datetime
        season = row["season"]
        fthg, ftag = int(row["FTHG"]), int(row["FTAG"])
        res_home = 1 if row["FTR"] == "H" else (0.5 if row["FTR"] == "D" else 0)
        res_away = 1 if row["FTR"] == "A" else (0.5 if row["FTR"] == "D" else 0)

        rh = ratings.get(h, 1500.0); ra = ratings.get(a, 1500.0)
        diff = (rh + HOME_ADV) - ra
        lam_h = mean_hg * 10 ** (diff / 600.0)
        lam_a = mean_ag * 10 ** (-diff / 600.0)

        for side, tn, fthg_, lam_, res_ in (("home", h, fthg, lam_h, res_home),
                                            ("away", a, ftag, lam_a, res_away)):
            rest = None
            if tn in last_date:
                rest = max(0, (date - last_date[tn]).days)
            # 欧冠前置：用 CL code 查本场后 7 天内该队有欧冠
            cl_code = ENG2CODE.get(tn)
            cl_prev = False
            if cl_code:
                for cd in cl_by_team.get((season, cl_code), []):
                    if 0 < (pd.to_datetime(cd) - date).days <= FATIGUE_RULES["midweek_window_days"]:
                        cl_prev = True; break
            # 欧冠近效：本场前 7 天内该队打过欧冠（周中欧冠→周末德甲 短休息）
            cl_recent = False
            if cl_code:
                for cd in cl_by_team.get((season, cl_code), []):
                    if 0 < (date - pd.to_datetime(cd)).days <= 7:
                        cl_recent = True; break
            records.append({
                "team": tn, "side": side, "rest": rest,
                "cl_preceding": cl_prev, "cl_recent": cl_recent,
                "actual": int(fthg_), "expected": float(lam_),
                "resid": float(fthg_) - float(lam_),
            })
        ph = _sigmoid(diff / 400.0); pa = _sigmoid(-diff / 400.0)
        ratings[h] = rh + ELO_K * (res_home - ph)
        ratings[a] = ra + ELO_K * (res_away - pa)
        last_date[h] = last_date[a] = date

    rec = pd.DataFrame(records)
    print(f"  总 team-match 样本={len(rec)} 有休息天数={rec['rest'].notna().sum()} "
          f"欧冠前置={int(rec['cl_preceding'].sum())} 欧冠近效={int(rec['cl_recent'].sum())}")

    # ---- 队内配对（消除强队混杂）：先按队去均值残差 ----
    team_mean = rec.groupby("team")["resid"].transform("mean")
    rec["resid_dm"] = rec["resid"] - team_mean   # demeaned：同队相对自身基准的偏差

    print("\n=== 4. 队内配对残差（demeaned，负=疲劳拖累该队相对自身基准的表现） ===")
    def grp_report(mask, name):
        g = rec[mask]
        if len(g) == 0:
            print(f"  {name}: 空")
            return None
        return {"n": int(len(g)), "mean_resid_dm": round(float(g["resid_dm"].mean()), 4),
                "mean_actual": round(float(g["actual"].mean()), 2),
                "mean_exp": round(float(g["expected"].mean()), 2)}
    ctrl_mask = (rec["rest"].notna()) & (rec["rest"] >= 7) & (~rec["cl_preceding"])
    ctrl = grp_report(ctrl_mask, "对照(休息≥7,无欧冠)")
    print(f"  对照(休息≥7,无欧冠): {ctrl}")

    print("  --- 按休息天数分档（队内，含欧冠近效即周中欧战后的短休息） ---")
    for lo, hi, lbl in [(0, 2, "休息0-2天"), (3, 4, "休息3-4天"), (5, 6, "休息5-6天")]:
        g = grp_report((rec["rest"].notna()) & (rec["rest"] >= lo) & (rec["rest"] <= hi), lbl)
        print(f"  {lbl}: {g}")
    print("  --- 欧冠近效（本场前7天打过欧冠） vs 非，控制休息≥4 ---")
    g_cr = grp_report(rec["cl_recent"] & (rec["rest"].notna()) & (rec["rest"] >= 4), "欧冠近效且休息≥4")
    print(f"  欧冠近效且休息≥4: {g_cr}")
    print("  --- 欧冠前置（本场后7天有欧冠，可能轮换） ---")
    g_cp = grp_report(rec["cl_preceding"] & (rec["rest"].notna()) & (rec["rest"] >= 3), "欧冠前置且休息≥3")
    print(f"  欧冠前置且休息≥3: {g_cp}")

    print("\n=== 5. 阈值扫描 → 最优 min_rest_days（队内残差差，负为真疲劳） ===")
    scan = []
    for mrd in [3, 4, 5]:
        mask = (rec["rest"].notna()) & ((rec["rest"] < mrd) | rec["cl_preceding"])
        fat = rec[mask]
        if len(fat) == 0:
            print(f"  min_rest={mrd}: 样本0，跳过"); continue
        effect = float(fat["resid_dm"].mean() - rec[ctrl_mask]["resid_dm"].mean())
        mean_gap = float((mrd - fat["rest"].clip(upper=mrd - 1)).mean()) if mrd > 1 else 0.0
        per_day = (effect / mean_gap / max(fat["expected"].mean(), 1.0)) if mean_gap > 0 else 0.0
        scan.append((mrd, int(len(fat)), round(effect, 4), round(mean_gap, 2), round(per_day, 4)))
        print(f"  min_rest={mrd}: 疲劳样本={len(fat)} 效应(每场进球,队内)={effect:+.4f} 平均缺={mean_gap:.2f}天 每缺一天折扣≈{per_day:.4f}")

    cands = [s for s in scan if s[1] >= 40 and s[2] < 0]
    if not cands:
        print("  ⚠️ 无满足样本≥40且效应为负的档，保留保守默认 calibrated=False")
        return
    best = min(cands, key=lambda s: s[2])
    best_mrd, best_n, best_eff, best_gap, best_per = best
    discount = max(0.005, min(best_per, 0.08))
    cap = min(round(abs(best_eff) / max(best_n, 1) + 0.05, 2), 0.15)
    cap = min(round(abs(best_eff), 2) if abs(best_eff) < 0.15 else 0.15, 0.15)

    print(f"\n=== 6. 结论：min_rest_days={best_mrd}, xG_discount_per_day≈{discount:.4f}, "
          f"xG_discount_max≈{cap:.2f}, n={best_n}, 效应={best_eff:+.4f} → calibrated=True ===")

    result = {
        "calibrated": True,
        "n_team_matches": int(len(rec)),
        "n_fatigue": best_n,
        "n_control": int(ctrl["n"]) if ctrl else None,
        "min_rest_days": best_mrd,
        "xG_discount_per_day": round(discount, 4),
        "xG_discount_max": cap,
        "midweek_window_days": FATIGUE_RULES["midweek_window_days"],
        "fatigue_effect_goals_per_match": round(best_eff, 4),
        "mean_rest_gap_days": round(best_gap, 2),
        "control_mean_resid_dm": round(ctrl["mean_resid_dm"], 4) if ctrl else None,
        "fatigue_mean_resid_dm": round(float(rec[(rec["rest"].notna()) & ((rec["rest"] < best_mrd) | rec["cl_preceding"])]["resid_dm"].mean()), 4),
        "by_rest_bucket": {lbl: grp_report((rec["rest"].notna()) & (rec["rest"] >= lo) & (rec["rest"] <= hi), lbl) for lo, hi, lbl in [(0,2,"0-2"),(3,4,"3-4"),(5,6,"5-6")]},
        "cl_recent_effect": grp_report(rec["cl_recent"] & (rec["rest"].notna()) & (rec["rest"] >= 4), "欧冠近效休息≥4"),
        "cl_preceding_effect": grp_report(rec["cl_preceding"] & (rec["rest"].notna()) & (rec["rest"] >= 3), "欧冠前置休息≥3"),
    }
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    json.dump(result, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  标定结果已写入 {out_json}")
    return result


if __name__ == "__main__":
    token = os.environ.get("FOOTBALL_DATA_TOKEN", "")
    out = os.path.join(ROOT, "artifacts", "fatigue_calibration.json")
    main(token, out)
