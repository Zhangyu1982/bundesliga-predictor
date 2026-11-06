"""
按小时重标定疲劳系数（无泄漏，可复现）
======================================
背景：旧口径用"天"（min_rest_days=3, 判定 rest_days < 3），而周四欧联→周日德甲
      恰好 3 天，永远不触发 → 欧联参赛队的疲劳恒为 0。本脚本改用小时精度重标定。

方法（三季 23/24 + 24/25 + 25/26，德甲 918 场）：
  1. 时间轴：德甲(football-data 精确开球) + 欧战(ESPN 缓存 CL/EL/UECL) 合并到队级时间线
  2. 每场德甲 → 该队"上一场任何赛事"与本场的小时差 hours
  3. 期望进球：该队**本场之前**同场地场均进球（≥3 场样本；不足用联赛均值）→ 无未来泄漏
  4. 残差 residual = 实际进球 - 期望进球
  5. 分桶比较残差均值（相对 hours>=144 或无上一场 的基线），并做 hours 线性回归
  6. 换算 xG 折扣 = 残差差 / 期望进球均值；per_hour = -斜率 / 期望进球均值
  7. 判定 calibrated：短间隔(欧战后)样本量 ≥ 40 且 |z| > 1.96 才置 True，否则保持 False 打黄牌

运行：python scripts/calibrate_fatigue_hours.py
输出：data/fatigue_calibration.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from data.historical_data import load_seasons
from data.euro_fetch import (
    EURO_LEAGUES, EURO_NAMES_ZH, espn_team_to_code, history_matches_for_season,
)

# CSV 英文队名 → code（含三季历史降级队）
CSV_ENG2CODE = {
    "Augsburg": "FCA", "Bayern Munich": "BAY", "Bochum": "BOC", "Darmstadt": "DAR",
    "Dortmund": "DOR", "Ein Frankfurt": "SGE", "FC Koln": "KOE", "Freiburg": "SCF",
    "Hamburg": "HSV", "Heidenheim": "HDH", "Hoffenheim": "TSG", "Holstein Kiel": "KIE",
    "Leverkusen": "LEV", "M'gladbach": "BMG", "Mainz": "MAI", "RB Leipzig": "RBL",
    "St Pauli": "STP", "Stuttgart": "STU", "Union Berlin": "FCU",
    "Werder Bremen": "SVW", "Wolfsburg": "WOB",
}
CODE2CSV = {v: k for k, v in CSV_ENG2CODE.items()}

KICKOFF_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "bl1_kickoff_cache.json")
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "fatigue_calibration.json")


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


# ---------------------------------------------------------------- 开球时间
def load_bl1_kickoffs(token: str | None = None, seasons: list[int] | None = None) -> dict:
    """拉 football-data BL1 三季赛程 → {(date, home_code, away_code): kickoff_dt}，带本地缓存。"""
    if os.path.exists(KICKOFF_CACHE):
        try:
            with open(KICKOFF_CACHE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {(k[0], k[1], k[2]): _parse_dt(v) for k, v in raw.items() if _parse_dt(v)}
        except Exception:
            pass

    token = token or os.environ.get("FOOTBALL_DATA_TOKEN", "")
    if not token:
        return {}
    import requests
    from data.api_client import fdo_team_to_code

    out: dict[str, str] = {}
    for season in (seasons or [2023, 2024, 2025]):
        try:
            r = requests.get(
                "https://api.football-data.org/v4/competitions/BL1/matches",
                headers={"X-Auth-Token": token}, params={"season": season}, timeout=30)
            if r.status_code != 200:
                continue
            for m in r.json().get("matches", []) or []:
                hc = fdo_team_to_code((m.get("homeTeam") or {}).get("name", ""))
                ac = fdo_team_to_code((m.get("awayTeam") or {}).get("name", ""))
                kick = (m.get("utcDate") or "")[:10]
                if hc and ac and kick:
                    out[f"{kick}|{hc}|{ac}"] = m.get("utcDate", "")
        except Exception:
            continue
    try:
        with open(KICKOFF_CACHE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass
    return {tuple(k.split("|")): _parse_dt(v) for k, v in out.items() if _parse_dt(v)}


# ---------------------------------------------------------------- 时间轴
def build_timeline(df, kickoffs: dict) -> dict:
    """{code: [(dt, competition, is_home), ...]} 按时间升序（德甲 + 欧战）。"""
    timeline: dict[str, list] = {}

    # 德甲（CSV 有比分/结果，开球时间来自 football-data；缺失时用 15:30 近似并标记）
    fallback = 0
    for row in df.itertuples():
        hc = CSV_ENG2CODE.get(row.HomeTeam, row.HomeTeam)
        ac = CSV_ENG2CODE.get(row.AwayTeam, row.AwayTeam)
        key = (row.Date.strftime("%Y-%m-%d"), hc, ac)
        dt = kickoffs.get(key)
        if dt is None:
            fallback += 1
            dt = datetime(row.Date.year, row.Date.month, row.Date.day, 15, 30)
        timeline.setdefault(hc, []).append((dt, "BL1", True))
        timeline.setdefault(ac, []).append((dt, "BL1", False))

    # 欧战（ESPN 缓存）
    seasons = sorted(set(df["season"]))
    added = 0
    for s in seasons:
        for m in history_matches_for_season(s):
            dt = _parse_dt(m.get("kickoff"))
            if dt is None:
                continue
            hc, ac = m.get("home_code"), m.get("away_code")
            comp = m.get("competition", "?")
            # 只保留德甲队一侧：对手可能是非德甲队（code=None），不能因此丢掉整场
            if hc:
                timeline.setdefault(hc, []).append((dt, comp, True))
                added += 1
            if ac:
                timeline.setdefault(ac, []).append((dt, comp, False))
                added += 1

    for lst in timeline.values():
        lst.sort(key=lambda x: x[0])
    return timeline, {"bl1_fallback_kickoff": fallback, "euro_rows_used": added}


def hours_since_prev(timeline: dict, code: str, kick: datetime) -> tuple[float | None, str | None]:
    """该队上一场（任何赛事，严格早于本场）与本场的小时差 + 上一场赛事。"""
    prev = None
    for (dt, comp, _is_home) in timeline.get(code, []):
        if dt >= kick:
            break
        prev = (dt, comp)
    if not prev:
        return None, None
    return (kick - prev[0]).total_seconds() / 3600.0, prev[1]


# ---------------------------------------------------------------- 标定
def calibrate(df, kickoffs: dict) -> dict:
    timeline, meta = build_timeline(df, kickoffs)

    # 联赛基线：按赛季、按场地的每队场均进球（用于样本不足兜底）
    league_mu = {}
    for row in df.itertuples():
        s = row.season
        league_mu.setdefault(s, {"h": [], "a": []})
        league_mu[s]["h"].append(row.FTHG)
        league_mu[s]["a"].append(row.FTAG)
    base_mu = {s: {"h": float(np.mean(v["h"])), "a": float(np.mean(v["a"]))}
               for s, v in league_mu.items()}
    global_mu = {"h": float(df["FTHG"].mean()), "a": float(df["FTAG"].mean()),
                 "all": float((df["FTHG"] + df["FTAG"]).mean() / 2)}

    # 逐场：本场之前该队同场地场均进球（滚动，无未来泄漏）
    hist = {}  # (season, code, venue) -> [goals]
    samples = []
    for row in df.itertuples():
        hc = CSV_ENG2CODE.get(row.HomeTeam, row.HomeTeam)
        ac = CSV_ENG2CODE.get(row.AwayTeam, row.AwayTeam)
        s = row.season
        kick = kickoffs.get((row.Date.strftime("%Y-%m-%d"), hc, ac))
        if kick is None:
            kick = datetime(row.Date.year, row.Date.month, row.Date.day, 15, 30)

        for code, venue, goals, is_home in ((hc, "h", row.FTHG, True), (ac, "a", row.FTAG, False)):
            h_prev, comp_prev = hours_since_prev(timeline, code, kick)
            past = hist.setdefault((s, code, venue), [])
            if len(past) >= 3:
                expected = float(np.mean(past))
            else:
                expected = base_mu.get(s, {}).get(venue, global_mu[venue])
            resid = float(goals) - expected
            samples.append({
                "season": s, "code": code, "venue": venue, "is_home": is_home,
                "hours": h_prev, "prev_comp": comp_prev,
                "goals": int(goals), "expected": expected, "residual": resid,
            })

        hist.setdefault((s, hc, "h"), []).append(row.FTHG)
        hist.setdefault((s, ac, "a"), []).append(row.FTAG)

    # 分桶（以"上一场是否为欧战"和小时数划分）
    euro_prev = [x for x in samples if x["hours"] is not None
                 and str(x["prev_comp"]).upper() in ("CL", "EL", "UECL")]

    def _stat(group: list) -> dict:
        if not group:
            return {"n": 0, "residual_mean": None, "expected_mean": None,
                    "goals_mean": None, "se": None}
        r = np.array([x["residual"] for x in group], dtype=float)
        e = np.array([x["expected"] for x in group], dtype=float)
        g = np.array([x["goals"] for x in group], dtype=float)
        return {
            "n": len(group),
            "residual_mean": round(float(r.mean()), 4),
            "expected_mean": round(float(e.mean()), 4),
            "goals_mean": round(float(g.mean()), 4),
            "se": round(float(r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0.0, 4),
        }

    # 基线：**正常一周一赛**（上一场是德甲且间隔 >=120h），才是"无欧战干扰"的对照
    normal = [x for x in samples if x["hours"] is not None
              and str(x["prev_comp"]).upper() == "BL1" and x["hours"] >= 120]
    baseline = _stat(normal)

    buckets = []
    for name, lo, hi in (("<=72h", 0, 72), ("72-96h", 72, 96),
                         ("96-120h", 96, 120), (">=120h", 120, 10**6)):
        grp = [x for x in euro_prev if lo <= x["hours"] < hi]
        st = _stat(grp)
        if st["n"]:
            st["delta_vs_baseline"] = round((baseline["residual_mean"] or 0.0) - st["residual_mean"], 4)
            exp = st["expected_mean"] or 1.0
            st["implied_discount"] = round(max(0.0, st["delta_vs_baseline"]) / max(exp, 0.1), 4)
        buckets.append({"bucket": name, "hours": [lo, None if hi > 10**5 else hi], **st})

    # 全样本（不限欧战）按小时分桶，作为交叉验证
    all_with_prev = [x for x in samples if x["hours"] is not None and x["hours"] <= 200]
    buckets_all = []
    for name, lo, hi in (("<=72h", 0, 72), ("72-96h", 72, 96),
                         ("96-120h", 96, 120), (">=120h", 120, 10**6)):
        grp = [x for x in all_with_prev if lo <= x["hours"] < hi]
        buckets_all.append({"bucket": name, **(_stat(grp))})

    # 线性回归（欧战上一场，96h 以上为参照；仅用 hours<=168）
    lin = {"n": 0, "slope_per_hour": None, "discount_per_hour": None, "note": ""}
    reg = [x for x in euro_prev if x["hours"] <= 168]
    if len(reg) >= 40:
        hs = np.array([x["hours"] for x in reg], dtype=float)
        rs = np.array([x["residual"] for x in reg], dtype=float)
        es = np.array([x["expected"] for x in reg], dtype=float)
        slope, intercept = np.polyfit(hs, rs, 1)
        mean_lam = float(es.mean())
        lin = {
            "n": len(reg),
            "slope_per_hour": round(float(slope), 6),
            "intercept": round(float(intercept), 4),
            "mean_expected": round(mean_lam, 4),
            # 残差随 hours 上升（slope>0）说明休息越少进球越少 → 折扣 = -slope/lam
            "discount_per_hour": round(max(0.0, -float(slope)) / max(mean_lam, 0.1), 6),
            "note": "residual ~ hours 线性拟合（欧战上一场、间隔≤168h），仅作量级参考",
        }

    # 短间隔组（欧战后 <96h）
    short = [x for x in euro_prev if x["hours"] < 96]
    short_stat = _stat(short)
    z = None
    if short and baseline["n"] and short_stat["se"] and baseline["se"]:
        diff = (baseline["residual_mean"] or 0.0) - (short_stat["residual_mean"] or 0.0)
        se_diff = (short_stat["se"] ** 2 + baseline["se"] ** 2) ** 0.5
        z = round(diff / se_diff, 3) if se_diff > 0 else None

    calibrated = bool(short_stat["n"] >= 40 and z is not None and z > 1.96)
    best = {
        "min_rest_hours": 96,
        "discount_per_hour": lin.get("discount_per_hour") or 0.0,
        "implied_short_discount": (buckets[0].get("implied_discount")
                                   if buckets and buckets[0]["n"] else 0.0),
        "calibrated": calibrated,
        "z_score": z,
        "short_n": short_stat["n"],
    }

    return {
        "method": "三季(2324/2425/2526)德甲918场 + 欧战(CL/EL/UECL)真实赛程；"
                  "按小时计算上一场间隔；期望进球用该队本场之前同场地滚动均值（无未来泄漏）",
        "samples": {"total": len(samples), "euro_prev": len(euro_prev),
                    "short_lt96h": short_stat["n"], "baseline_n": baseline["n"]},
        "meta": meta,
        "baseline": baseline,
        "buckets_euro_prev": buckets,
        "buckets_all_prev": buckets_all,
        "linear": lin,
        "short_vs_baseline_z": z,
        "best": best,
        "conclusion": "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _conclusion(rep: dict) -> str:
    b = rep["best"]
    bl = rep["baseline"]
    if b["calibrated"]:
        return (f"欧战后 <96h 样本 {b['short_n']} 例，残差显著低于正常休息基线"
                f"（基线 n={bl['n']}, z={b['z_score']}），按小时折扣 "
                f"{b['discount_per_hour']:.4f}/h（≈{b['discount_per_hour']*24*100:.1f}%/天）可标定（calibrated=True）")
    return (f"欧战后 <96h 样本 {b['short_n']} 例（对照基线 n={bl['n']}），"
            f"与正常一周一赛的残差差异 z={b['z_score']}，未达显著（阈值 z>1.96 且 n≥40）→ "
            f"真实数据显示欧战短间隔对该队进球无统计显著拖累；按既定口径维持 calibrated=False 打黄牌，"
            f"折扣只取经验量级 {b['discount_per_hour']:.4f}/h（≈{b['discount_per_hour']*24*100:.1f}%/天）并如实提示未标定")


if __name__ == "__main__":
    df = load_seasons()
    token = os.environ.get("FOOTBALL_DATA_TOKEN", "")
    kickoffs = load_bl1_kickoffs(token)
    rep = calibrate(df, kickoffs)
    rep["conclusion"] = _conclusion(rep)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: rep[k] for k in ("samples", "baseline", "linear", "best", "conclusion")},
                     ensure_ascii=False, indent=2))
    print("\nbuckets(euro_prev):")
    for b in rep["buckets_euro_prev"]:
        print(" ", b)
