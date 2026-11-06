"""
P5 · 赛前首发反推（Lineup Reversal）
=====================================
ESPN 足球侧没有结构化伤病接口（P7 已实测确认），但 **summary 里有逐人首发/替补名单**：
  - 已结束比赛：每队 20 人（11 首发 + 9 替补），带 starter 布尔、athlete.id、位置、球衣号
  - 赛前：rosters 存在但 athletes=0（只在本场开球前约 60 分钟才填）

因此本模块做两件事：
  1. 抓「本场首发」（find_event_id + fetch_match_lineup）
  2. 抓「历史首发」用于构建常规主力库（见 data/baseline.py）

诚实边界：
  - ESPN 不保证首发提前多久公布；实测赛前 6.5h 为空。代码按"有就立刻用、没有就回退"处理，不猜测。
  - 阵容是英文名（`Mark Flekken` 这类），不翻译；队名统一映射成项目 code。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

ESPN_SOCCER_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 项目内部赛事 key -> ESPN league code（BL1/BL2 供 P5 使用；欧战沿用 euro_fetch 的映射）
LINEUP_LEAGUES = {
    "BL1": "ger.1",
    "BL2": "ger.2",
    "CL": "uefa.champions",
    "EL": "uefa.europa",
    "UECL": "uefa.europa.conf",
}

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LINEUP_CACHE = os.path.join(DATA_DIR, "lineup_p5_cache.json")
SCOREBOARD_CACHE = os.path.join(DATA_DIR, "scoreboard_p5_cache.json")


def _get_json(url: str, timeout: int = 25) -> dict | None:
    try:
        r = requests.get(url, headers=ESPN_UA, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------- 赛程 → event id
def scoreboard_events(league_key: str, dates: str = "", use_cache: bool = True) -> list[dict]:
    """取某联赛某天（UTC 日期 YYYYMMDD）的赛程，返回精简事件列表。

    ESPN 的 dates 参数按 UTC 日期分片；空则返回当前默认窗口。
    """
    lg = LINEUP_LEAGUES.get(league_key)
    if not lg:
        return []
    cache = _load(SCOREBOARD_CACHE)
    key = f"{lg}:{dates}"
    if use_cache and key in cache:
        return cache[key]

    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/scoreboard?dates={dates}" if dates
                  else f"{ESPN_SOCCER_BASE}/{lg}/scoreboard")
    if not j:
        return []
    out = []
    for ev in j.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        t_home = (home.get("team") or {})
        t_away = (away.get("team") or {})
        out.append({
            "event_id": str(ev.get("id")),
            "name": ev.get("name") or "",
            "kickoff": ev.get("date") or comp.get("date") or "",
            "state": ((comp.get("status") or {}).get("type") or {}).get("state") or "",
            "home_name": t_home.get("displayName") or "",
            "away_name": t_away.get("displayName") or "",
            "home_abbr": t_home.get("abbreviation") or "",
            "away_abbr": t_away.get("abbreviation") or "",
        })
    cache[key] = out
    _save(SCOREBOARD_CACHE, cache)
    return out


def _utc_dates_around(kickoff_iso: str) -> list[str]:
    """开球时刻前后各一天的 UTC 日期串（ESPN dates 按 UTC 分片，夜间场会跨日）。"""
    try:
        dt = datetime.fromisoformat((kickoff_iso or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    except Exception:
        return []
    return [(dt + timedelta(days=d)).strftime("%Y%m%d") for d in (-1, 0, 1)]


def find_event_id(home_code: str, away_code: str, kickoff_iso: str,
                  league_key: str = "BL1") -> str | None:
    """用「开球日期 + 主客队 code」在 ESPN scoreboard 里定位 event id。

    不依赖 football-data 的 match_id（两套 id 体系不通用）。
    """
    from data.euro_fetch import espn_team_to_code

    for d in _utc_dates_around(kickoff_iso):
        for ev in scoreboard_events(league_key, d):
            if (espn_team_to_code(ev["home_name"], ev["home_abbr"]) == home_code
                    and espn_team_to_code(ev["away_name"], ev["away_abbr"]) == away_code):
                return ev["event_id"]
    return None


# ---------------------------------------------------------------- 单场阵容
def _parse_side(roster_team: dict) -> dict:
    from data.euro_fetch import espn_team_to_code

    team = roster_team.get("team") or {}
    players = roster_team.get("roster") or []
    starters, bench = [], []
    for p in players:
        ath = p.get("athlete") or {}
        name = ath.get("displayName") or p.get("displayName") or ""
        if not name:
            continue
        item = {
            "name": name,
            "id": str(ath.get("id") or ""),
            "pos": ((p.get("position") or {}).get("abbreviation") or ""),
            "jersey": p.get("jersey"),
            "subbed_in": bool(p.get("subbedIn")),
            "subbed_out": bool(p.get("subbedOut")),
        }
        (starters if p.get("starter") else bench).append(item)
    return {
        "code": espn_team_to_code(team.get("displayName") or "", team.get("abbreviation") or ""),
        "team": team.get("displayName") or "",
        "formation": roster_team.get("formation") or "",
        "starters": starters,
        "bench": bench,
    }


def fetch_match_lineup(event_id: str, league_key: str = "BL1",
                       use_cache: bool = True) -> dict:
    """抓单场阵容。返回 {available, ready_at, home:{...}, away:{...}, source}。

    available=False 表示"官方首发还没出"（赛前 rosters 为 0 人），调用方应回退。
    """
    lg = LINEUP_LEAGUES.get(league_key) or LINEUP_LEAGUES["BL1"]
    empty = {"available": False, "home": {}, "away": {}, "source": "ESPN summary",
             "note": "ESPN 未返回首发名单（赛前 rosters 为空）"}
    if not event_id:
        empty["note"] = "未匹配到 ESPN event id"
        return empty

    cache = _load(LINEUP_CACHE)
    ck = f"{lg}:{event_id}"
    if use_cache and ck in cache and (cache[ck] or {}).get("available"):
        return cache[ck]

    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/summary?event={event_id}")
    if not j:
        return empty

    out = {"available": False, "home": {}, "away": {}, "source": "ESPN summary",
           "event_id": event_id}
    for rt in j.get("rosters") or []:
        side = "home" if (rt.get("homeAway") or "").lower() == "home" else "away"
        side_data = _parse_side(rt)
        if side_data["starters"]:
            out[side] = side_data

    if len((out.get("home") or {}).get("starters") or []) >= 11 \
            and len((out.get("away") or {}).get("starters") or []) >= 11:
        out["available"] = True
        try:
            out["ready_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        cache[ck] = out
        _save(LINEUP_CACHE, cache)
    else:
        out["note"] = "两队首发未齐（赛前尚未公布或数据不全），按不可用处理"
    return out


def minutes_to_kickoff(kickoff_iso: str) -> float | None:
    """距开球还有多少分钟（负数=已开球）。用于 UI 判断"是否进入首发窗口"。"""
    try:
        dt = datetime.fromisoformat((kickoff_iso or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        return None


def final_refresh_window(kickoff_iso: str, minutes: int = 90) -> dict:
    """判断是否进入"赛前终极刷新"窗口（默认开球前 90 分钟以内）。"""
    m = minutes_to_kickoff(kickoff_iso)
    if m is None:
        return {"in_window": False, "minutes_to_kickoff": None, "note": "开球时间不可解析"}
    return {
        "in_window": -5 <= m <= minutes,
        "minutes_to_kickoff": round(m, 1),
        "note": ("已进入首发窗口" if -5 <= m <= minutes
                 else ("已开球" if m < -5 else f"距开球还有 {m/60:.1f} 小时")),
    }
