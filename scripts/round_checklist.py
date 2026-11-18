"""出卡前 4 条核验（可脚本化，任一不过即拒绝出卡）。

用法：
    python scripts/round_checklist.py --round 10
    python scripts/round_checklist.py --round 10 --window 2026-11-20:2026-11-22

四条：
 1) 18 个 code 与 CODE2ENG/基线完全一致（差集为空）
 2) 每场 kickoff 落在给定日期窗口内（中线 = 本轮官方日程）
 3) 9 场共 18 支、无重复、无缺
 4) fixture_snapshot 已写入，且 fetched_at 与本次抓取一致
任何一条不过 → 退出码 1，并打印哪一条、差什么。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import fixture_fingerprint                     # noqa: E402
from data.season_roster import SEASON_ROSTER_2627            # noqa: E402

CST = timezone(timedelta(hours=8))
EXPECTED_TEAMS = 18
EXPECTED_MATCHES = 9


def _parse_iso(s: str) -> datetime:
    """支持 2026-11-20T19:30Z / +08:00；无时区按 UTC 解释（football-data 口径）。"""
    s = (s or "").strip()
    if not s:
        raise ValueError("kickoff 为空")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_round(fixtures, meta, cover_codes, window, expected_matches=EXPECTED_MATCHES,
                expected_teams=EXPECTED_TEAMS, roster=None) -> list[dict]:
    """返回 4 条核验结果；window=(start,end) 为本地日期字符串。"""
    fx = fixtures or []
    results = []

    # ---- 1) 队集合与本季基线名单完全一致（双向差集为空），且模型有参数
    codes = set()
    for m in fx:
        for k in ("home_code", "away_code"):
            if m.get(k):
                codes.add(m[k])
    roster = set(roster if roster is not None else SEASON_ROSTER_2627)
    missing = sorted(roster - codes)          # 名单里有、本轮没出现
    extra = sorted(codes - roster)            # 本轮有、名单外（= 跑错批次/串季的直接证据）
    no_param = sorted(codes - set(cover_codes))
    ok1 = not missing and not extra and not no_param
    results.append({
        "no": 1, "name": "18 队与本季名单一致（双向差集为空）", "ok": ok1,
        "detail": (f"本轮 {len(codes)} 队 / 名单 {len(roster)} 队；名单缺 {missing or '无'}；"
                   f"名单外 {extra or '无'}；无模型参数 {no_param or '无'}"
                   if not ok1 else
                   f"{len(codes)} 队与名单完全匹配，且全部有模型参数"),
    })

    # ---- 2) kickoff 全部落在窗口内（窗口按 **UTC** 计，与赛程源同口径）
    #     注意：德甲周日 17:30 CET 的比赛在北京时间是**次日凌晨**，所以不能拿北京时间去卡日期。
    start = datetime.fromisoformat(window[0]).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(window[1]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    bad, span = [], []
    for m in fx:
        try:
            dt = _parse_iso(m.get("kickoff") or m.get("utcDate", ""))
        except Exception as e:
            bad.append(f"{m.get('match_id')}: kickoff 无法解析（{e}）")
            continue
        span.append(dt)
        if not (start <= dt < end):
            bad.append(f"{m.get('home_code')}-{m.get('away_code')} 在 "
                       f"{dt:%m-%d %H:%M}Z（北京时间 {dt.astimezone(CST):%m-%d %H:%M}）")
    _span = (f"实到 {min(span).astimezone(CST):%m-%d %H:%M} ~ {max(span).astimezone(CST):%m-%d %H:%M}（北京）"
             if span else "无 kickoff")
    results.append({
        "no": 2, "name": f"kickoff 落在 {window[0]} ~ {window[1]}（UTC）", "ok": bool(span) and not bad,
        "detail": (f"全部在窗口内；{_span}" if not bad and span else "越界：" + "；".join(bad)),
    })

    # ---- 3) 场次数 / 队数 / 无重复
    dup_teams, dup_matches = [], []
    seen_mid = set()
    home = [m.get("home_code") for m in fx]
    away = [m.get("away_code") for m in fx]
    for t in set(home) | set(away):
        if home.count(t) + away.count(t) > 1:
            dup_teams.append(t)
    for m in fx:
        mid = m.get("match_id")
        if mid in seen_mid:
            dup_matches.append(mid)
        seen_mid.add(mid)
    ok3 = (len(fx) == expected_matches and len(codes) == expected_teams
           and not dup_teams and not dup_matches)
    results.append({
        "no": 3, "name": f"{expected_matches} 场 / {expected_teams} 队 · 无重复无缺", "ok": ok3,
        "detail": (f"场次 {len(fx)} / 队 {len(codes)}，无重复"
                   if ok3 else f"场次 {len(fx)} / 队 {len(codes)}；重复队 {dup_teams or '无'}；"
                               f"重复 match_id {dup_matches or '无'}"),
    })

    # ---- 4) 指纹已写入且 fetched_at 与本次抓取一致
    fetched_at = (meta.get("freshness") or {}).get("fetched_at")
    snap_from_meta = meta.get("fixture_snapshot")
    snap_now = fixture_fingerprint(fx, fetched_at=fetched_at)
    snap_used = snap_from_meta or snap_now
    ok4 = bool(snap_used) and snap_used.get("fetched_at") == fetched_at and bool(fetched_at)
    results.append({
        "no": 4, "name": "fixture_snapshot 已写入且 fetched_at 一致", "ok": ok4,
        "detail": (f"hash={snap_used.get('payload_hash')} · fetched_at={fetched_at}"
                   if ok4 else f"snapshot={'有' if snap_used else '无'}，"
                               f"fetched_at(meta)={fetched_at} vs snapshot={snap_used.get('fetched_at')}"),
    })
    return results


def all_pass(results) -> bool:
    return all(r["ok"] for r in results)


def _load_round(round_no: int):
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    fx, meta = p.fetch_round(round_no)
    cover = {code for code, eng in __import__("data.historical_data", fromlist=["CODE2ENG"]).CODE2ENG.items()
             if eng in (p._bundle.dc_attack or {})}
    return fx, meta, cover


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--window", default=None, help="本地日期窗口，如 2026-11-20:2026-11-22")
    a = ap.parse_args()
    fx, meta, cover = _load_round(a.round)
    if a.window:
        window = tuple(a.window.split(":"))
    else:
        # 默认按 UTC 取本轮首末比赛日（与赛程源同口径）
        dts = [_parse_iso(m.get("kickoff", "")) for m in fx if m.get("kickoff")]
        window = (min(dts).strftime("%Y-%m-%d"), max(dts).strftime("%Y-%m-%d"))
    meta.setdefault("fixture_snapshot", fixture_fingerprint(
        fx, fetched_at=(meta.get("freshness") or {}).get("fetched_at")))
    print(f"第 {a.round} 轮 · 赛程来源 = {meta.get('fixtures_source')} · "
          f"窗口 = {window[0]} ~ {window[1]}")
    res = check_round(fx, meta, cover, window)
    for r in res:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['no']}. {r['name']} —— {r['detail']}")
    print("结论：", "四条全过，可出卡" if all_pass(res) else "有未过项，拒绝出卡")
    return 0 if all_pass(res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
