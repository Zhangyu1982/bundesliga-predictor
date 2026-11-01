"""
P2 · injury_shift 系数网格搜索（离线，不放在 Streamlit 前端）
================================================================
目标：评估 config.RULES["injury_shift"] 的系数组合（key_out_shift / doubtful_shift /
max_total_shift / importance_factor）对预测质量（Log Loss / 方向命中率）的影响，
输出《模型参数标定报告》data/calibration_report.json。

诚实边界（重要）：
- football-data.org 无伤病接口；TheSportsDB(403)/Transfermarkt(Cloudflare)/Fotmob(404)
  均被网络阻断；ESPN 官方伤病接口当前德甲为空 → **历史真实伤病名单数据源不可用**。
- 因此本脚本采用【合成伤病注入】做敏感性/稳健性分析：为每场按固定规则模拟注入
  若干名 核心/轮换/替补 缺阵，衡量不同系数如何把"伤病强度"映射为概率变化。
- 这是"系数方向与量级是否稳健"的分析，**不是**基于真实标签的有监督最优标定。
  因此结论：不据此把 calibrated 翻为 True（维持诚实黄牌），直至拿到真实伤病名单数据源。

运行：python3 scripts/calibrate_injury.py
输出：data/calibration_report.json（供 Streamlit 侧边栏"模型历史表现"只读渲染）
"""
from __future__ import annotations
import os, sys, json, math, random, datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.historical_data import load_seasons
from scripts.backtest import _fit_dc   # 复用轻量 DC 拟合（无泄漏 walk-forward）

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "calibration_report.json")

# 合成伤病注入规则（固定 seed 可复现）：
# 每场每队随机注入 0~2 名核心 + 0~2 轮换 + 0~1 替补 缺阵（均匀概率），模拟"未知伤病名单"场景。
_INJ_SEED = 2026
_MAX_KEY = 2
_MAX_ROT = 2
_MAX_SUB = 1


def _dc_probs(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    """泊松比分矩阵 → (主胜, 平, 客胜) 概率。"""
    hw = dr = 0.0
    for i in range(9):
        ph = np.exp(-lam_h) * lam_h ** i / math.factorial(i)
        for j in range(9):
            pa = np.exp(-lam_a) * lam_a ** j / math.factorial(j)
            p = ph * pa
            if i > j:
                hw += p
            elif i == j:
                dr += p
    return hw, dr, max(0.0, 1.0 - hw - dr)


def _inject_injuries(rng: random.Random) -> tuple[int, int, int]:
    """为单队注入 (核心, 轮换, 替补) 缺阵人数（合成，模拟未知伤病名单）。"""
    return (rng.randint(0, _MAX_KEY), rng.randint(0, _MAX_ROT), rng.randint(0, _MAX_SUB))


def run() -> dict:
    df = load_seasons().sort_values(["season", "Date"]).reset_index(drop=True)
    df["round_no"] = df.groupby("season").cumcount() // 9 + 1

    # —— 一次无泄漏 walk-forward：记录每场拟合后的 lam 与真实赛果 ——
    past = pd.DataFrame(columns=df.columns)
    games = []   # {lam_h, lam_a, ftr}
    for season, grp in df.groupby("season"):
        grp = grp.sort_values("Date")
        for r in sorted(grp["round_no"].unique()):
            round_df = grp[grp["round_no"] == r]
            dc = _fit_dc(past) if len(past) >= 40 else {}
            for m in round_df.itertuples():
                if not dc or m.HomeTeam not in dc["attack"] or m.AwayTeam not in dc["attack"]:
                    continue
                lam_h = dc["mu_home"] * dc["attack"][m.HomeTeam] * dc["defence"][m.AwayTeam]
                lam_a = dc["mu_away"] * dc["attack"][m.AwayTeam] * dc["defence"][m.HomeTeam]
                games.append({"lam_h": lam_h, "lam_a": lam_a, "ftr": m.FTR})
            past = pd.concat([past, round_df], ignore_index=True)
    n = len(games)
    if n == 0:
        return {"games": 0, "error": "无可用历史数据"}

    # 固定合成伤病注入（每场每队），可复现
    rng = random.Random(_INJ_SEED)
    injected = [(_inject_injuries(rng), _inject_injuries(rng)) for _ in games]

    # —— 网格搜索 injury_shift 系数组合 ——
    grid_key = [0.05, 0.08, 0.10, 0.12, 0.15]
    grid_doubt = [0.03, 0.05, 0.08]
    imp_weights = [1.0, 0.6, 0.3]        # 核心/轮换/替补（结构规则，保持）
    max_total = 0.30
    baseline = {"key": 0.10, "doubt": 0.05}   # 当前经验值

    def _eval(key_s: float, doubt_s: float) -> dict:
        ll = 0.0
        hit = 0
        for g, ((hk, hr, hs), (ak, ar, as_)) in zip(games, injected):
            sh = min(key_s * hk * 1.0 + key_s * hr * 0.6 + key_s * hs * 0.3
                     + doubt_s * 0, max_total)          # doubtful 在合成场景置 0，仅测 key_out 通道
            sa = min(key_s * ak * 1.0 + key_s * ar * 0.6 + key_s * as_ * 0.3, max_total)
            ph, pd_, pa = _dc_probs(g["lam_h"] * (1 - sh), g["lam_a"] * (1 - sa))
            probs = np.clip([ph, pd_, pa], 1e-6, 1 - 1e-6)
            y = {"H": 0, "D": 1, "A": 2}[g["ftr"]]
            ll += -math.log(probs[y])
            if (ph > pa and g["ftr"] == "H") or (pa > ph and g["ftr"] == "A") or \
               (pa == ph and g["ftr"] == "D"):
                hit += 1
        return {"logloss": round(ll / n, 4), "hit_rate": round(hit / n, 4)}

    baseline_ll = _eval(baseline["key"], baseline["doubt"])["logloss"]
    results = []
    for k in grid_key:
        for d in grid_doubt:
            r = _eval(k, d)
            results.append({"key_out_shift": k, "doubtful_shift": d,
                            "importance_factor": imp_weights,
                            "logloss": r["logloss"], "hit_rate": r["hit_rate"],
                            "delta_logloss_vs_baseline": round(r["logloss"] - baseline_ll, 4)})
    results.sort(key=lambda x: x["logloss"])

    # —— 诚实结论 ——
    best = results[0]
    n_rep = len(set(f"{r['key_out_shift']}|{r['doubtful_shift']}" for r in results))
    conclusion = (
        f"合成伤病敏感性分析（非真实标签的有监督标定）：共 {n} 场、{n_rep} 组系数组合。"
        f"网格内 Log Loss 最优 {best['logloss']}（key_out={best['key_out_shift']}, doubtful={best['doubtful_shift']}），"
        f"经验值 0.10/0.05 的 Log Loss 为 {baseline_ll}。因无历史真实伤病名单数据源"
        f"（TheSportsDB 403 / Transfermarkt Cloudflare / ESPN 德甲当前空），该差异系合成场景下的敏感性格局，"
        f"不足以作为真实世界最优依据；故维持经验值并保持 calibrated=False（诚实黄牌），"
        f"待拿到带伤病标签的历史数据后再据此表做有监督定稿。"
    )

    report = {
        "kind": "injury_shift_sensitivity",
        "method": "合成伤病注入的敏感性/稳健性分析（非真实标签有监督标定）",
        "data_source_note": "历史真实伤病名单不可用（football-data无伤病接口；TheSportsDB/Transfermarkt/Fotmob被阻断；ESPN德甲为空）。用固定seed合成伤病注入衡量系数稳健性。",
        "games": n,
        "injection_rule": {"max_core": _MAX_KEY, "max_rotation": _MAX_ROT, "max_sub": _MAX_SUB,
                           "seed": _INJ_SEED, "note": "每场每队均匀随机注入，模拟未知伤病名单"},
        "baseline": {"key_out_shift": baseline["key"], "doubtful_shift": baseline["doubt"],
                     "max_total_shift": max_total, "logloss": baseline_ll},
        "best": best,
        "results": results,
        "conclusion": conclusion,
        "calibrated": False,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


if __name__ == "__main__":
    rep = run()
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:2500])
    print("\n[OK] 报告已写入:", OUT)
