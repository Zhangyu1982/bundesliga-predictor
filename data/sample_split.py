"""统计层：把「全样本 / 仅稳定参数 / 含临时参数 / 含衰减参数」切开看。

参数口径不同的比赛，命中率不可直接相加：升班马用德乙换算的临时参数、降级回归用
衰减参数，混在一起报一个总命中率，既掩盖问题也让改进看不出效果。这个模块只做口径
切分与计数，不改模型，也不剔除任何一场——每场都留在"全样本"里。
"""
from __future__ import annotations

import json
import os

CLASSES = ("all", "stable", "provisional", "carryover")


def classify_record(record: dict, provisional_teams, carryover_teams) -> str:
    """给一条赛果记录归口径。临时参数优先于衰减（两者都涉及就取更弱的一方）。"""
    home, away = record.get("home"), record.get("away")
    prov = set(provisional_teams or ())
    carry = set(carryover_teams or ())
    if home in prov or away in prov:
        return "provisional"
    if home in carry or away in carry:
        return "carryover"
    return "stable"


def _stat(rows, min_n: int) -> dict:
    n = len(rows)
    hits = sum(int(bool(r.get("pred_hit"))) for r in rows)
    return {
        "n": n,
        "hits": hits,
        "hit_rate": (hits / n) if n else 0.0,
        "insufficient": n < min_n,
        "min_n": min_n,
    }


def split_stats(records, provisional_teams, carryover_teams, min_n: int = 10,
                season: str | None = None) -> dict:
    """按口径切分样本。season 传入时只统计该赛季。"""
    rows = [r for r in (records or []) if season is None or str(r.get("season")) == str(season)]
    buckets = {k: [] for k in CLASSES}
    for r in rows:
        buckets["all"].append(r)
        buckets[classify_record(r, provisional_teams, carryover_teams)].append(r)
    return {k: _stat(buckets[k], min_n) for k in CLASSES}


def load_carryover_names(season: str) -> set[str]:
    """该赛季里「曾降级、现在又在名单内」的队（英文名）。"""
    try:
        from data.historical_data import CODE2ENG
        from data.season_roster import load_roster
        from data.team_lifecycle import seasons_absent
    except Exception:
        return set()
    roster = load_roster(season) or {}
    return {CODE2ENG.get(c, c) for c in roster if seasons_absent(c, season) > 0}


def load_provisional_names() -> set[str]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisional_teams.json")
    try:
        with open(path, encoding="utf-8") as f:
            return set((json.load(f).get("teams") or {}).keys())
    except (OSError, ValueError):
        return set()


def report(season: str | None = None, min_n: int = 10) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_results.json")
    with open(path, encoding="utf-8") as f:
        records = json.load(f).get("records", [])
    season = season or (records[-1].get("season") if records else None)
    return {
        "season": season,
        "provisional_teams": sorted(load_provisional_names()),
        "carryover_teams": sorted(load_carryover_names(season)) if season else [],
        "stats": split_stats(records, load_provisional_names(),
                             load_carryover_names(season) if season else set(),
                             min_n=min_n, season=season),
    }
