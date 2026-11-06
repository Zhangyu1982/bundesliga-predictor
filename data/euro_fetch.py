"""
欧战数据接入（ESPN 直连，国内可达，无需 token）
=================================================
补齐 football-data.org 免费层的两个空洞：
  - EL 欧联 / UECL 欧会：football-data 分别 403 / 404，ESPN 有真实赛程与结果
  - 阵容：football-data 免费层无 lineup，ESPN summary 提供首发/替补/换人

用途：
  1. FatigueEngine 的"上一场欧战"判定 —— 覆盖周二/周三（欧冠）与周四（欧联/欧会）
  2. 本周德甲总览：展示每队周中欧战、休息小时数、轮换风险
  3. 历史缓存：供 scripts/calibrate_fatigue_hours.py 做按小时疲劳标定

诚实边界：
  - ESPN keyEvents 只有进球/黄牌/换人，**不标注赛中伤病**，赛中伤情无法从本模块获得
  - 阵容仅覆盖 ESPN 有 summary 的场次（欧战与德甲主流场次基本都有），拿不到时 available=False
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import requests

ESPN_SOCCER_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 内部赛事 key -> ESPN league code
EURO_LEAGUES = {
    "CL": "uefa.champions",
    "EL": "uefa.europa",
    "UECL": "uefa.europa.conf",
}
EURO_NAMES_ZH = {"CL": "欧冠", "EL": "欧联", "UECL": "欧会"}

HISTORY_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "euro_history.json")
LINEUP_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lineup_cache.json")

# ESPN displayName 关键字 -> 本项目 code（含历史赛季降级队，供标定使用）
# 用"最长关键字优先"匹配，避免 Union St.-Gilloise 被当作 Union Berlin 之类的误判
EURO_TEAM2CODE = {
    "bayern munich": "BAY", "bayern": "BAY",
    "bayer leverkusen": "LEV", "leverkusen": "LEV",
    "borussia dortmund": "DOR", "dortmund": "DOR",
    "rb leipzig": "RBL", "leipzig": "RBL",
    "vfb stuttgart": "STU", "stuttgart": "STU",
    "eintracht frankfurt": "SGE", "frankfurt": "SGE",
    "sc freiburg": "SCF", "freiburg": "SCF",
    "1. fsv mainz 05": "MAI", "mainz": "MAI",
    "borussia mönchengladbach": "BMG", "mönchengladbach": "BMG", "gladbach": "BMG",
    "vfl wolfsburg": "WOB", "wolfsburg": "WOB",
    "werder bremen": "SVW", "bremen": "SVW",
    "fc augsburg": "FCA", "augsburg": "FCA",
    "tsg hoffenheim": "TSG", "hoffenheim": "TSG",
    "1. fc union berlin": "FCU", "union berlin": "FCU",
    "vfl bochum": "BOC", "bochum": "BOC",
    "fc st. pauli": "STP", "st. pauli": "STP", "st pauli": "STP",
    "1. fc heidenheim 1846": "HDH", "heidenheim": "HDH",
    "hamburg sv": "HSV", "hamburger sv": "HSV", "hamburg": "HSV",
    "fc cologne": "KOE", "1. fc köln": "KOE", "koln": "KOE",
    "schalke 04": "SCH", "schalke": "SCH",
    "sc paderborn 07": "PAD", "paderborn": "PAD",
    "sv elversberg": "ELV", "elversberg": "ELV",
    "holstein kiel": "KIE", "kiel": "KIE",
    "sv darmstadt 98": "DAR", "darmstadt": "DAR",
    "hertha": "BSC",
}
# 按关键字长度降序，保证最长匹配优先
_TEAM_KEYS = sorted(EURO_TEAM2CODE.items(), key=lambda kv: -len(kv[0]))


def espn_team_to_code(name: str = "", abbr: str = "") -> str | None:
    """ESPN 队名 → 本项目 code；非德甲队返回 None。"""
    n = (name or "").strip().lower()
    if n:
        for kw, code in _TEAM_KEYS:
            if kw in n:
                return code
    if abbr:
        a = abbr.strip().upper()
        if a in EURO_TEAM2CODE:
            return EURO_TEAM2CODE[a]
    return None


# ---------------------------------------------------------------- 基础请求
def _get_json(url: str, timeout: int = 20) -> dict | None:
    try:
        r = requests.get(url, headers=ESPN_UA, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _is_finished(status_obj: dict) -> bool:
    t = (status_obj or {}).get("type") or {}
    name = str(t.get("name") or "").upper()
    return name in ("STATUS_FULL_TIME", "STATUS_END_PERIOD", "STATUS_FINAL", "STATUS_END_OF_EXTRATIME")


def _is_extra_time(status_obj: dict) -> bool:
    t = (status_obj or {}).get("type") or {}
    detail = f"{t.get('detail') or ''} {t.get('shortDetail') or ''}".lower()
    return ("extra time" in detail) or ("penalt" in detail) or ("aet" in detail)


def _score_of(comp: dict) -> dict:
    """ESPN competition → 项目内部 score 结构（fullTime / duration）。"""
    ft = {}
    for c in comp.get("competitors") or []:
        side = c.get("homeAway")
        try:
            ft[side] = int(c.get("score"))
        except (TypeError, ValueError):
            pass
    if not ft:
        return {}
    return {
        "fullTime": {"home": ft.get("home"), "away": ft.get("away")},
        "duration": "EXTRA_TIME" if _is_extra_time(comp) else "REGULAR",
    }


def _normalize_event(ev: dict, comp_key: str) -> dict | None:
    """ESPN event → 项目 matches 结构（可被 FatigueEngine 直接消费）。"""
    leagues = (ev.get("competitions") or [{}])[0]
    comp = leagues
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    home = away = None
    for c in competitors:
        team = c.get("team") or {}
        code = espn_team_to_code(team.get("displayName") or team.get("name") or "",
                                 team.get("abbreviation") or "")
        if (c.get("homeAway") or "").lower() == "home":
            home = (code, team.get("displayName") or "")
        else:
            away = (code, team.get("displayName") or "")
    kick = ev.get("date") or ""
    return {
        "match_id": f"ESPN_{ev.get('id')}",
        "espn_id": str(ev.get("id") or ""),   # 纯数字 event id（fetch_lineup 需要）
        "competition": comp_key,
        "home_code": home[0] if home else None,
        "away_code": away[0] if away else None,
        "home_raw": home[1] if home else "",
        "away_raw": away[1] if away else "",
        "kickoff": kick,
        "status": "FINISHED" if _is_finished(ev.get("status")) else "TIMED",
        "score": _score_of(comp),
        "extra_time": _is_extra_time(comp),
        "source": "ESPN",
    }


def fetch_scoreboard(comp_key: str, dates: str = "") -> list[dict]:
    """拉某赛事赛程。dates: YYYYMMDD（单日）或 YYYYMM（整月）；空=当期。"""
    league = EURO_LEAGUES.get(comp_key)
    if not league:
        return []
    url = f"{ESPN_SOCCER_BASE}/{league}/scoreboard"
    if dates:
        url += f"?dates={dates}"
    j = _get_json(url)
    if not j:
        return []
    out = []
    for ev in j.get("events") or []:
        rec = _normalize_event(ev, comp_key)
        if rec:
            out.append(rec)
    return out


_CURRENT_CACHE: dict = {"ts": 0.0, "data": None}
CURRENT_TTL = 900   # 15 分钟进程内缓存（欧战赛程日内变化很小）


def fetch_current_euro(days_back: int = 8, days_fwd: int = 2, use_cache: bool = True) -> dict:
    """当前窗口的欧战赛程（含 EL/UECL），用于"本周德甲总览"与疲劳引擎。

    实现要点：ESPN 支持 dates=YYYYMM 整月查询 → 每月每赛事 1 次请求（通常 6 次），
    再在本地按窗口过滤，避免逐日 20+ 次请求把页面拖慢。
    返回 {competition: [matches]}；任一赛事失败返回空列表（不阻塞页面）。
    """
    if use_cache and _CURRENT_CACHE["data"] is not None \
            and time.time() - _CURRENT_CACHE["ts"] < CURRENT_TTL:
        return _CURRENT_CACHE["data"]

    today = datetime.now()
    lo = today - timedelta(days=days_back)
    hi = today + timedelta(days=days_fwd)
    months = {today.strftime("%Y%m"), lo.strftime("%Y%m"), hi.strftime("%Y%m")}

    out: dict[str, list] = {}
    for key in EURO_LEAGUES:
        got: list[dict] = []
        for ym in sorted(months):
            got.extend(fetch_scoreboard(key, ym))
        seen, uniq = set(), []
        for m in got:
            dt = _parse_iso(m.get("kickoff") or "")
            if dt is None or not (lo <= dt <= hi + timedelta(days=1)):
                continue
            if m["match_id"] in seen:
                continue
            seen.add(m["match_id"])
            uniq.append(m)
        uniq.sort(key=lambda m: m.get("kickoff") or "")
        out[key] = uniq

    if any(out.values()):
        _CURRENT_CACHE["data"] = out
        _CURRENT_CACHE["ts"] = time.time()
    return out


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def recent_euro_for_team(code: str, days_back: int = 10,
                         data: dict | None = None) -> list[dict]:
    """某德甲队近期欧战场次（用于总览/轮换提示）。"""
    data = data if data is not None else fetch_current_euro(days_back=days_back)
    out = []
    for key, lst in (data or {}).items():
        for m in lst:
            if code and (m.get("home_code") == code or m.get("away_code") == code):
                out.append(m)
    out.sort(key=lambda m: m.get("kickoff") or "")
    return out


# ---------------------------------------------------------------- 历史缓存
def load_history_cache() -> dict:
    try:
        with open(HISTORY_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history_cache(data: dict) -> None:
    try:
        with open(HISTORY_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def build_history_cache(seasons: list[str] | None = None, verbose: bool = False) -> dict:
    """抓取历史赛季欧战赛程并落盘。seasons: ['2324','2425','2526'] 形式。"""
    seasons = seasons or ["2324", "2425", "2526"]
    cache = load_history_cache()
    for s in seasons:
        start_year = 2000 + int(s[:2])
        months = [f"{start_year}{m:02d}" for m in (9, 10, 11, 12)] + \
                 [f"{start_year + 1}{m:02d}" for m in (1, 2, 3, 4, 5)]
        for key in EURO_LEAGUES:
            bucket = cache.setdefault(s, {}).setdefault(key, [])
            have = {m["match_id"] for m in bucket}
            for ym in months:
                for m in fetch_scoreboard(key, ym):
                    if m["match_id"] not in have:
                        bucket.append(m)
                        have.add(m["match_id"])
                if verbose:
                    print(f"  {s} {key} {ym} -> {len(bucket)}")
        for key in cache[s]:
            cache[s][key].sort(key=lambda m: m.get("kickoff") or "")
        save_history_cache(cache)
    return cache


def history_matches_for_season(season: str) -> list[dict]:
    """返回某赛季三赛事合并的欧战赛程（读缓存，无则空）。"""
    cache = load_history_cache()
    out = []
    for key in EURO_LEAGUES:
        out.extend((cache.get(season) or {}).get(key) or [])
    return out


# ---------------------------------------------------------------- 阵容
def load_lineup_cache() -> dict:
    try:
        with open(LINEUP_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_lineup_cache(data: dict) -> None:
    try:
        with open(LINEUP_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_lineup(event_id: str, comp_key: str, use_cache: bool = True) -> dict:
    """抓某场欧战/联赛的阵容与换人。

    返回 {available, home:{starters,bench,code}, away:{...}, substitutions:[{minute,in,out,team}], source}
    拿不到时 available=False（不抛异常）。
    """
    empty = {"available": False, "home": {}, "away": {}, "substitutions": [], "source": "ESPN summary"}
    league = EURO_LEAGUES.get(comp_key)
    if not league:
        return empty
    cache = load_lineup_cache()
    if use_cache and event_id in cache:
        return cache[event_id]
    j = _get_json(f"{ESPN_SOCCER_BASE}/{league}/summary?event={event_id}", timeout=25)
    if not j:
        return empty

    out = {"available": False, "home": {}, "away": {}, "substitutions": [], "source": "ESPN summary"}
    for r in j.get("rosters") or []:
        team = r.get("team") or {}
        side = "home" if (r.get("homeAway") or "").lower() == "home" else "away"
        players = r.get("roster") or []
        if not players:
            continue
        starters, bench = [], []
        for p in players:
            ath = p.get("athlete") or {}
            name = ath.get("displayName") or ""
            if not name:
                continue
            item = {
                "player": name,
                "pos": ((p.get("position") or {}).get("abbreviation") or ""),
                "starter": bool(p.get("starter")),
                "subbed_in": bool(p.get("subbedIn")),
                "subbed_out": bool(p.get("subbedOut")),
            }
            (starters if item["starter"] else bench).append(item)
        out[side] = {
            "code": espn_team_to_code(team.get("displayName") or "", team.get("abbreviation") or ""),
            "team": team.get("displayName") or "",
            "starters": starters,
            "bench": bench,
        }
        if len(starters) >= 11:
            out["available"] = True

    for ev in j.get("keyEvents") or []:
        tx = (ev.get("text") or "")
        if "Substitution" not in tx:
            continue
        minutes = ((ev.get("clock") or {}).get("displayValue") or "")
        out["substitutions"].append({"minute": minutes, "text": tx})

    if out["available"] or out["substitutions"]:
        cache[event_id] = out
        save_lineup_cache(cache)
    return out


def lineup_rotation_summary(lineup: dict, side: str = "home") -> dict:
    """从阵容/换人推断该队轮换与消耗情况（**仅供展示，不进模型**）。

    诚实边界：ESPN 不提供逐人出场分钟，也不提供赛中伤病事件（实测 injury-ish events = 0），
    所以只能给出「首发名单 / 被换下 / 替补登场」这些弱信号，
    不能据此断言"某人受伤"或"某人打满 90 分钟"。
    """
    if not lineup or not lineup.get("available"):
        return {"available": False, "note": "ESPN 未提供该场阵容"}
    t = lineup.get(side) or {}
    starters = t.get("starters") or []
    if not starters:
        return {"available": False, "note": "ESPN 未提供该队首发"}
    subbed_out = [p for p in starters if p.get("subbed_out")]
    bench_used = [p for p in (t.get("bench") or []) if p.get("subbed_in")]
    return {
        "available": True,
        "team": t.get("team", ""),
        "code": t.get("code"),
        "starter_count": len(starters),
        "starters": [f"{p['player']}({p['pos']})" if p.get("pos") else p["player"]
                     for p in starters],
        "subbed_out": [p["player"] for p in subbed_out],
        "bench_used": [p["player"] for p in bench_used],
        "bench_size": len(t.get("bench") or []),
        "sub_events": len(lineup.get("substitutions") or []),
        "note": "ESPN 不提供逐人出场分钟与赛中伤情；「被换下」只作消耗/疑似不适的弱信号",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build-cache":
        seasons = sys.argv[2].split(",") if len(sys.argv) > 2 else None
        t0 = time.time()
        data = build_history_cache(seasons, verbose=True)
        print("done in %.1fs" % (time.time() - t0))
        for s, v in data.items():
            print(s, {k: len(x) for k, x in v.items()})
    else:
        print("usage: python -m data.euro_fetch build-cache [2324,2425,2526]")
