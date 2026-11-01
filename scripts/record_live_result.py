#!/usr/bin/env python3
"""记录真实比赛结果到 R1 偏差数据库（data_stores/bias.db）。

用于把真实踢完的比赛结果 + 赛前初版预测比对写入记录库（与 scripts/backtest.py
的 walk-forward 回测记录同表，但用真实赛季号，避免与回测模拟轮次混淆）。

用法示例（记录一场真实赛果）：
  python3 scripts/record_live_result.py \\
    --season 2627 --round 8 \\
    --match-id "BUN_2627_R08_Union Berlin_FC Koln_LIVE" \\
    --home "Union Berlin" --away "FC Koln" \\
    --hg 1 --ag 1 --ftr D \\
    --pred-home 0.442 --pick home_win --elo home_win --dc away_win --divergence 1

诚实原则：只记录确知字段；未提供的概率（如 pred-draw / pred-away）保留 NULL，
绝不虚构缺失值。pred_hit 由 pred_pick 与实际结果自动判定。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_stores.bias_store import BiasStore, DB_PATH  # noqa: E402


def _auto_pred_hit(pick: str | None, ftr: str | None) -> int:
    """按 pred_pick 与实际结果判定是否命中。"""
    if not pick or not ftr:
        return 0
    mapping = {"home_win": "H", "draw": "D", "away_win": "A"}
    return 1 if mapping.get(pick) == ftr else 0


def record(rec: dict, db_path: str = DB_PATH) -> int:
    """写一条真实比对记录；缺省字段补齐默认，缺失概率保留 NULL。"""
    rec.setdefault("pred_draw", None)
    rec.setdefault("pred_away", None)
    rec.setdefault("bias_streak", 0)
    rec.setdefault("elo_dir", None)
    rec.setdefault("dc_dir", None)
    rec.setdefault("divergence", 0)
    rec.setdefault("pred_hit", _auto_pred_hit(rec.get("pred_pick"), rec.get("actual_ftr")))
    return BiasStore(db_path).write_records([rec])


def main() -> None:
    ap = argparse.ArgumentParser(description="记录一场真实比赛结果到 R1 偏差数据库")
    ap.add_argument("--season", required=True, help="真实赛季，如 2627")
    ap.add_argument("--round", type=int, required=True, help="赛季内轮次")
    ap.add_argument("--match-id", required=True)
    ap.add_argument("--home", required=True)
    ap.add_argument("--away", required=True)
    ap.add_argument("--hg", type=int, default=None)
    ap.add_argument("--ag", type=int, default=None)
    ap.add_argument("--ftr", required=True, help="H/D/A")
    ap.add_argument("--pred-home", type=float, default=None)
    ap.add_argument("--pred-draw", type=float, default=None)
    ap.add_argument("--pred-away", type=float, default=None)
    ap.add_argument("--pick", default=None, help="home_win / draw / away_win")
    ap.add_argument("--elo", default=None)
    ap.add_argument("--dc", default=None)
    ap.add_argument("--divergence", type=int, default=0)
    args = ap.parse_args()

    rec = {
        "season": args.season,
        "round_no": args.round,
        "match_id": args.match_id,
        "home": args.home,
        "away": args.away,
        "actual_hg": args.hg,
        "actual_ag": args.ag,
        "actual_ftr": args.ftr,
        "pred_home": args.pred_home,
        "pred_draw": args.pred_draw,
        "pred_away": args.pred_away,
        "pred_pick": args.pick,
        "elo_dir": args.elo,
        "dc_dir": args.dc,
        "divergence": args.divergence,
    }
    n = record(rec)
    print(f"已写入 {n} 条：{args.season} R{args.round} {args.home} vs {args.away} "
          f"实际 {args.hg}-{args.ag} ({args.ftr}) · 预测首选 {args.pick} · 命中 "
          f"{_auto_pred_hit(args.pick, args.ftr)}")


if __name__ == "__main__":
    main()
