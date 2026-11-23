#!/usr/bin/env python3
"""记录真实比赛结果到 R1 偏差数据库（data_stores/bias.db）+ 入库 JSON（data/live_results.json）。

用于把真实踢完的比赛结果 + 赛前初版预测比对写入记录库（与 scripts/backtest.py
的 walk-forward 回测记录同表，但用真实赛季号，避免与回测模拟轮次混淆）。
bias.db 为运行时库（*.db 不入库，云端重建为空）；同时写入 data/live_results.json
（静态、入库），保证真实赛果在公网端持久可见并可合并。

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
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_stores.bias_store import BiasStore, DB_PATH  # noqa: E402
from data.live_results import load_live_results, save_live_results  # noqa: E402


def parse_kickoff(text: str) -> datetime:
    """解析开赛时间：必须带时区，否则无法自证"哪天几点、哪个赛季"。"""
    dt = datetime.fromisoformat(text.strip())
    if dt.tzinfo is None:
        raise ValueError("--kickoff 必须带时区（如 2026-11-07T22:30:00+08:00）")
    return dt


def season_label(dt: datetime) -> str:
    """按开赛时间推赛季号，跨年赛季从 7 月起算：2026-11 → 2627，2027-05 → 2627。"""
    y = dt.year
    start = y if dt.month >= 7 else y - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def derive_matchday(kickoff: datetime, home: str, away: str, fixtures) -> int | None:
    """在赛程里按"对阵 + 开赛日期"反查轮次，不写死轮号。"""
    for f in fixtures or []:
        try:
            fk = parse_kickoff(str(f.get("kickoff")))
        except Exception:
            continue
        if not fk:
            continue
        if (f.get("home") or "").strip() != home.strip():
            continue
        if (f.get("away") or "").strip() != away.strip():
            continue
        if abs((fk - kickoff).total_seconds()) <= 36 * 3600:
            return f.get("matchday")
    return None


def fixture_source(endpoint: str, season: str, matchday: int | None, fetched_at: str) -> dict:
    """一条记录自证"从哪个端点、哪一季、哪一轮、什么时候取的"。"""
    return {"endpoint": endpoint, "season": season,
            "matchday": matchday, "fetched_at": fetched_at}


def _auto_pred_hit(pick: str | None, ftr: str | None) -> int:
    """按 pred_pick 与实际结果判定是否命中。"""
    if not pick or not ftr:
        return 0
    mapping = {"home_win": "H", "draw": "D", "away_win": "A"}
    return 1 if mapping.get(pick) == ftr else 0


def record(rec: dict, db_path: str = DB_PATH, live_path: str | None = None) -> int:
    """写一条真实比对记录；同时写入 bias.db 与 live_results.json（入库）。"""
    rec.setdefault("pred_home", None)
    rec.setdefault("pred_draw", None)
    rec.setdefault("pred_away", None)
    rec.setdefault("bias_streak", 0)
    rec.setdefault("elo_dir", None)
    rec.setdefault("dc_dir", None)
    rec.setdefault("divergence", 0)
    _pick = rec.get("pred_pick")
    # 诚实：无赛前预测时 pred_hit 为 NULL（未预测），不误判为"未命中"
    rec.setdefault("pred_hit", None if not _pick else _auto_pred_hit(_pick, rec.get("actual_ftr")))
    n = BiasStore(db_path).write_records([rec])
    # 同步写入库 JSON：按 match_id 去重替换，保留既有真实赛果
    existing = [r for r in load_live_results(live_path) if r.get("match_id") != rec.get("match_id")]
    save_live_results(existing + [rec], path=live_path)
    return n


_STAGE_ORDER = ("终极版", "确认版", "初版")


def _load_snapshot(season, round_no, snap_dir: str = "data/predictions") -> dict | None:
    """按 stage 优先取快照文件：终极版 > 确认版 > 初版 > 旧的无 stage 文件。"""
    for tag in _STAGE_ORDER:
        path = os.path.join(snap_dir, "{}_R{:02d}_{}.json".format(season, int(round_no), tag))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                continue
    path = os.path.join(snap_dir, "{}_R{:02d}.json".format(season, int(round_no)))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def resolve_shift(season, round_no, match_id, shift_home=None, shift_away=None,
                  snap_dir: str = "data/predictions") -> dict:
    """自动判定 shift 三态：只有"终极版 + 有首发"的快照才算 computed。

    传了 --shift-computed 也不能绕过；不符合就降级为 unverified 并给出原因。
    """
    snap = _load_snapshot(season, round_no, snap_dir)
    if not snap:
        basis, reason = "unverified", "无预测快照（无法回查终极版/首发）"
    else:
        card = next((c for c in snap.get("cards", []) if c.get("match_id") == match_id), None)
        if not card:
            basis, reason = "unverified", "快照中无此场"
        elif card.get("stage") != "终极版":
            basis, reason = "unverified", "快照 stage={}".format(card.get("stage"))
        elif not card.get("lineup_available"):
            basis, reason = "unverified", "快照 lineup.available=False"
        else:
            basis, reason = "computed", "终极版 + 首发可用"
            card_shift = card.get("injury_shift") or {}
            if shift_home is None:
                shift_home = card_shift.get("home")
            if shift_away is None:
                shift_away = card_shift.get("away")
    if shift_home is None and shift_away is None:
        basis, reason = None, "无 shift 值（未计算）"
    return {"shift_home": shift_home, "shift_away": shift_away,
            "basis": basis, "reason": reason}


def _shift_basis(home, away, computed: bool):
    """三态：None=未计算；computed=算了且可信；unverified=有值但没声明来源。"""
    if home is None and away is None:
        return None
    for v in (home, away):
        if v is not None and not (0.0 <= float(v) <= 0.30):
            raise SystemExit(f"shift 越界：{v}（允许 0.00~0.30；未计算请留空）")
    return "computed" if computed else "unverified"


def main() -> None:
    ap = argparse.ArgumentParser(description="记录一场真实比赛结果到 R1 偏差数据库")
    ap.add_argument("--kickoff", required=True,
                    help="开赛时间（ISO 带时区，如 2026-11-21T22:30:00+08:00）；必填，用于自证赛季与轮次")
    ap.add_argument("--season", default=None, help="赛季，如 2627；缺省由 --kickoff 派生")
    ap.add_argument("--round", type=int, default=None, help="轮次；缺省按 --kickoff + 对阵反查赛程")
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
    ap.add_argument("--lam-pred", type=float, default=None,
                    help="赛前 λ 合计（期望总进球），用于 λ 滚动账")
    ap.add_argument("--divergence", type=int, default=0)
    ap.add_argument("--shift-home", type=float, default=None,
                    help="本场预测时的伤病偏移（主队，DC 攻击折扣口径）；用于 P0-1 有效性回测")
    ap.add_argument("--shift-away", type=float, default=None,
                    help="本场预测时的伤病偏移（客队）")
    ap.add_argument("--shift-computed", action="store_true",
                    help="声明本场确实按首发/伤病名单算过偏移（用于区分 null=未计算 与 0.0=算了且为零）")
    args = ap.parse_args()

    kickoff_dt = parse_kickoff(args.kickoff)
    season = args.season or season_label(kickoff_dt)
    round_no = args.round
    source = None
    if round_no is None:
        try:
            from pipeline import PredictionPipeline
            pipe = PredictionPipeline()
            fixtures = []
            for r in range(1, 35):
                try:
                    fx, _ = pipe.fetch_round(r)
                except Exception:
                    continue
                for m in fx:
                    fixtures.append({"matchday": r, "kickoff": m.get("kickoff"),
                                     "home": m.get("home_team"), "away": m.get("away_team")})
            round_no = derive_matchday(kickoff_dt, args.home, args.away, fixtures)
        except Exception:
            round_no = None
    if round_no is None:
        raise SystemExit("无法从赛程反查轮次：请显式传 --round（今日配额可能已耗尽）")
    source = fixture_source("/v4/competitions/BL1/matches?matchday={}".format(round_no),
                            season, round_no,
                            kickoff_dt.isoformat())

    sh = resolve_shift(season, round_no, args.match_id, args.shift_home, args.shift_away)
    if args.shift_computed and sh["basis"] != "computed":
        print(f"[shift] --shift-computed 被拒：{sh['reason']} → unverified")
    if sh["basis"] == "unverified":
        print(f"[shift] 本场 shift 记为 unverified：{sh['reason']}")
    rec = {
        "season": season,
        "round_no": round_no,
        "kickoff": kickoff_dt.isoformat(),
        "fixture_source": source,
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
        "shift_home": sh["shift_home"],
        "shift_away": sh["shift_away"],
        "shift_basis": sh["basis"],
        "shift_reason": sh["reason"],
    }
    n = record(rec)
    # λ 滚动账：有 --lam-pred 就记一笔，用于"最近 N 场 Σλ vs Σ实际"的 z
    if args.lam_pred is not None:
        try:
            from data import lambda_track
            lambda_track.append([{
                "match_id": args.match_id, "season": season, "round_no": round_no,
                "lam_pred": args.lam_pred,
                "actual_goals": (args.hg + args.ag) if (args.hg is not None and args.ag is not None) else None,
            }])
        except Exception as _e:
            print(f"[lambda_track] 记账失败：{type(_e).__name__}")
    print(f"已写入 {n} 条：{season} R{round_no} {args.home} vs {args.away} "
          f"实际 {args.hg}-{args.ag} ({args.ftr}) · 预测首选 {args.pick} · 命中 "
          f"{_auto_pred_hit(args.pick, args.ftr)} · shift {rec['shift_basis'] or '未计算'}")


if __name__ == "__main__":
    main()
