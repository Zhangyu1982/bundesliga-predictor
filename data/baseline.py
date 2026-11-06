"""
P5 · 常规主力库（Baseline XI）
==============================
用 ESPN 的历史首发名单统计「谁经常首发」，得到每队的常规主力与首发率。

数据来源（已实测）：
  - `/teams`              → 18 支队 id
  - `/teams/{id}/schedule`→ 该队近期已完成/待打的 event id
  - `/summary?event=`     → 该场 11 首发 + 9 替补（见 data/lineup.py）

口径：
  - 只统计**该队实际出场且首发名单已公布**的场次（含联赛+杯赛，ESPN 同一 league code 内）
  - 首发率 rate = 首发次数 / 参与统计的场次数，取值 0–1
  - baseline XI = 门将位取首发最多的 1 人 + 其余按 rate 取 10 人
  - 常规主力集合 core = rate >= cfg.core_rate（默认 0.6）
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from data.euro_fetch import espn_team_to_code
from data.lineup import (_get_json, ESPN_SOCCER_BASE, LINEUP_LEAGUES,
                         fetch_match_lineup, scoreboard_events)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_FILE = os.path.join(DATA_DIR, "baseline_xi.json")

# 项目 code -> BL1 18 队（用于确认 baseline 覆盖到哪些队）
BL1_CODES = ["BAY", "BMG", "DOR", "ELV", "FCA", "FCU", "HSV", "KOE", "LEV",
             "MAI", "PAD", "RBL", "SCF", "SCH", "SGE", "STU", "SVW", "TSG"]


def load_baseline() -> dict:
    try:
        with open(BASELINE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_baseline(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(BASELINE_FILE), exist_ok=True)
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_team_ids(league_key: str = "BL1", use_cache: bool = True) -> dict[str, str]:
    """ESPN team id 表：{code: espn_team_id}。"""
    lg = LINEUP_LEAGUES.get(league_key)
    if not lg:
        return {}
    from data.lineup import SCOREBOARD_CACHE, _load, _save
    cache = _load(SCOREBOARD_CACHE)
    tkey = f"teams:{lg}"
    if use_cache and tkey in cache:
        return cache[tkey]

    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/teams")
    if not j:
        return {}
    out = {}
    try:
        tlist = ((j.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    except Exception:
        tlist = []
    for t in tlist:
        team = t.get("team") or {}
        code = espn_team_to_code(team.get("displayName") or "", team.get("abbreviation") or "")
        if code:
            out[code] = str(team.get("id"))
    cache[tkey] = out
    _save(SCOREBOARD_CACHE, cache)
    return out


def fetch_team_events(team_id: str, league_key: str = "BL1") -> list[dict]:
    """该队近期赛事（含已结束），返回 [{event_id, date}]。"""
    lg = LINEUP_LEAGUES.get(league_key)
    if not lg or not team_id:
        return []
    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/teams/{team_id}/schedule")
    if not j:
        return []
    out = []
    for e in j.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        state = ((comp.get("status") or {}).get("type") or {}).get("state") or ""
        out.append({"event_id": str(e.get("id")), "date": e.get("date") or "",
                    "name": e.get("name") or "", "state": state})
    return out


def _side_of(lu: dict, code: str) -> str:
    """判断该队在这场是主还是客。"""
    if ((lu.get("home") or {}).get("code")) == code:
        return "home"
    if ((lu.get("away") or {}).get("code")) == code:
        return "away"
    return ""


def _aggregate(team_code: str, lineups: list[dict]) -> dict:
    """把多场首发汇总成 {球员名: 统计}，逐场按该队实际的主/客位置取名单。"""
    stat: dict[str, dict] = {}
    n = 0
    for lu in lineups:
        side = _side_of(lu, team_code)
        if not side:
            continue
        sd = lu.get(side) or {}
        starters = sd.get("starters") or []
        if len(starters) < 11:
            continue
        n += 1
        for p in starters:
            name = p.get("name") or ""
            if not name:
                continue
            row = stat.setdefault(name, {"name": name, "id": p.get("id", ""),
                                         "pos": p.get("pos", ""), "starts": 0})
            row["starts"] += 1
            if p.get("pos") and not row["pos"]:
                row["pos"] = p["pos"]
    for r in stat.values():
        r["rate"] = round(r["starts"] / n, 3) if n else 0.0
    return {"n_matches": n, "players": stat}


def _pick_xi(players: dict) -> tuple[list[dict], list[dict]]:
    """从统计里挑 baseline XI（含门将 1 人）与常规主力集合。"""
    rows = sorted(players.values(), key=lambda r: (-r["rate"], -r["starts"], r["name"]))
    gks = [r for r in rows if (r.get("pos") or "").upper().startswith("G")]
    outfield = [r for r in rows if r not in gks]
    xi = ((gks[:1] if gks else []) + outfield)[:11]
    return xi, rows


def build_baseline(window_matches: int = 8, league_key: str = "BL1",
                   codes: list[str] | None = None, core_rate: float = 0.6,
                   verbose: bool = False) -> dict:
    """构建/刷新常规主力库并落盘。

    一次构建约 18 次 schedule + 若干次 summary 请求（summary 有本地缓存，重复跑很快）。
    """
    team_ids = fetch_team_ids(league_key)
    target_codes = codes or [c for c in BL1_CODES if c in team_ids]

    # 1) 收集每队近期 event（去重后统一下载阵容）
    team_events: dict[str, list[str]] = {}
    for code in target_codes:
        tid = team_ids.get(code)
        if not tid:
            continue
        evs = [e for e in fetch_team_events(tid, league_key) if e.get("event_id")]
        team_events[code] = [e["event_id"] for e in evs[:window_matches + 4]]

    all_events = sorted({eid for evs in team_events.values() for eid in evs})
    if verbose:
        print(f"[baseline] teams={len(team_events)} events={len(all_events)}")

    lineups = {}
    for eid in all_events:
        lu = fetch_match_lineup(eid, league_key)
        if lu.get("available"):
            lineups[eid] = lu

    # 2) 汇总
    teams_out = {}
    for code, evs in team_events.items():
        rows = []
        for eid in evs:
            lu = lineups.get(eid)
            if not lu:
                continue
            if not _side_of(lu, code):
                continue
            rows.append(lu)
            if len(rows) >= window_matches:
                break
        if not rows:
            teams_out[code] = {"n_matches": 0, "xi": [], "core": [],
                               "available": False, "note": "ESPN 未提供该队近期首发"}
            continue
        agg = _aggregate(code, rows)
        xi, all_rows = _pick_xi(agg["players"])
        core = [r for r in all_rows if r["rate"] >= core_rate and r["starts"] > 0]
        side_first = _side_of(rows[0], code)
        teams_out[code] = {
            "available": True,
            "n_matches": agg["n_matches"],
            "formation_hint": (rows[0].get(side_first) or {}).get("formation", ""),
            "xi": [{"name": r["name"], "id": r.get("id", ""), "pos": r.get("pos", ""),
                    "rate": r["rate"], "starts": r["starts"]} for r in xi],
            "core": [{"name": r["name"], "rate": r["rate"], "starts": r["starts"],
                      "pos": r.get("pos", "")} for r in core],
            "note": f"近 {agg['n_matches']} 场首发统计",
        }
        if verbose:
            print(f"[baseline] {code}: matches={agg['n_matches']} xi={len(xi)} core={len(core)}")

    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "league": LINEUP_LEAGUES.get(league_key, ""),
        "window_matches": window_matches,
        "core_rate": core_rate,
        "teams": teams_out,
    }
    save_baseline(data)
    return data


if __name__ == "__main__":
    import sys
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    d = build_baseline(window_matches=w, verbose=True)
    n_ok = sum(1 for v in d["teams"].values() if v.get("available"))
    print(f"done: {n_ok}/{len(d['teams'])} teams with baseline")
