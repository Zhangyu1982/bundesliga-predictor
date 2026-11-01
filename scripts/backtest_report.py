"""
P4·优先级二③ 历史回测看板生成器（独立脚本，本地跑完 → 静态 data/backtest_report.json）
========================================================================================
用近 3 赛季 918 场 walk-forward（复用 scripts/backtest.run_backtest）计算：
  方向命中率、爆冷率、按轮次/按赛季命中曲线、Elo/DC 分歧效应、R1 偏差惯性、R4 分歧阈值。
输出静态 JSON 供 Streamlit 页面直接读取渲染 —— 回测计算不在主应用里跑，避免每次交互拖慢。

运行：python3 scripts/backtest_report.py   （生成 data/backtest_report.json）
"""
from __future__ import annotations
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.backtest import run_backtest, calibrate_r1, calibrate_r4


def build_report() -> dict:
    rep = run_backtest()
    rec = rep["records"]
    n = len(rec)
    hits = sum(r["pred_hit"] for r in rec)
    upsets = sum(1 for r in rec if
                 (r["elo_dir"] == "home_win" and r["actual_ftr"] == "A") or
                 (r["elo_dir"] == "away_win" and r["actual_ftr"] == "H"))

    # 按轮次方向命中率
    by_round: dict[int, list[int]] = {}
    for r in rec:
        by_round.setdefault(r["round_no"], [0, 0])
        by_round[r["round_no"]][0] += r["pred_hit"]
        by_round[r["round_no"]][1] += 1
    round_curve = [{"round": rn, "acc": round(w / tot, 4), "n": tot}
                   for rn, (w, tot) in sorted(by_round.items())]

    # 按赛季
    by_season: dict[str, list[int]] = {}
    for r in rec:
        by_season.setdefault(r["season"], [0, 0, 0])
        by_season[r["season"]][0] += r["pred_hit"]
        by_season[r["season"]][1] += 1
        if (r["elo_dir"] == "home_win" and r["actual_ftr"] == "A") or \
           (r["elo_dir"] == "away_win" and r["actual_ftr"] == "H"):
            by_season[r["season"]][2] += 1
    season_summary = [{"season": s, "n": v[1], "acc": round(v[0] / v[1], 4),
                       "upset": round(v[2] / v[1], 4)}
                      for s, v in sorted(by_season.items())]

    # 平均推荐置信度（Elo 主/客胜 max，draw=0）
    avg_conf = round(float(sum(max(r["pred_home"], r["pred_away"]) for r in rec)) / n, 4) if n else 0.0

    div = rep["r4_div"]
    div_upset = div["div"][0] / div["div"][1] if div["div"][1] else 0.0
    nodiv_upset = div["no_div"][0] / div["no_div"][1] if div["no_div"][1] else 0.0

    r1 = calibrate_r1(rep["r1"])
    r4 = calibrate_r4(rep["r4"], rep["r4_div"])

    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "games": n,
        "seasons": sorted(set(r["season"] for r in rec)),
        "direction_accuracy": round(hits / n, 4) if n else 0.0,
        "upset_rate": round(upsets / n, 4) if n else 0.0,
        "avg_confidence": avg_conf,
        "round_curve": round_curve,
        "season_summary": season_summary,
        "divergence": {"div_upset": round(div_upset, 4),
                       "no_div_upset": round(nodiv_upset, 4)},
        "r1": r1,
        "r4": r4,
    }


def main() -> None:
    report = build_report()
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "backtest_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"✅ 已生成 {out_path}")
    print(f"   games={report['games']} 方向命中率={report['direction_accuracy']:.1%} "
          f"爆冷率={report['upset_rate']:.1%} 平均置信度={report['avg_confidence']}")
    print("   按赛季 " + ", ".join(f"{s['season']}:{s['acc']:.1%}(n={s['n']})" for s in report['season_summary']))
    print(f"   分歧爆冷 {report['divergence']['div_upset']:.1%} vs 一致 {report['divergence']['no_div_upset']:.1%}")
    print(f"   R1 基线错率 {report['r1']['baseline_wrong_rate']:.1%}；R4 分歧阈值 {report['r4']['threshold']}")


if __name__ == "__main__":
    main()
