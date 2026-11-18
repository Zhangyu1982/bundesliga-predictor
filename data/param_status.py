"""临时参数（升班马）的到期状态：能自动发现"临时了太久还没转正"。"""
from __future__ import annotations

from data.historical_data import CODE2ENG


def rounds_played(code_eng: str, season: str, results: dict | None = None) -> int:
    """本季已踢轮次（按赛果记录里该队出场次数计）。"""
    if results is None:
        import json, os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_results.json")
        try:
            with open(path, encoding="utf-8") as f:
                results = json.load(f)
        except (OSError, ValueError):
            results = {"records": []}
    n = 0
    for r in results.get("records", []) or []:
        if str(r.get("season")) != str(season):
            continue
        if code_eng in (r.get("home"), r.get("away")):
            n += 1
    return n


def status(team_eng: str, season: str, results=None, expires_after_rounds: int = 10) -> dict:
    played = rounds_played(team_eng, season, results)
    # fresh：还没踢或只踢过 1 场；aging：样本在长肉，但仍不足；expired：踢满 N 轮该转正了
    if played < 2:
        st = "fresh"
    elif played < expires_after_rounds:
        st = "aging"
    else:
        st = "expired"
    return {"team": team_eng, "played": played,
            "expires_after_rounds": expires_after_rounds, "status": st}


def code_to_eng(code: str) -> str:
    return CODE2ENG.get(code, code)


def load_expires() -> int:
    """临时参数的到期轮次（读 provisional_teams.json 的 _meta，缺省 10）。"""
    import json, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisional_teams.json")
    try:
        with open(path, encoding="utf-8") as f:
            return int((json.load(f).get("_meta") or {}).get("expires_after_rounds", 10))
    except (OSError, ValueError, TypeError):
        return 10
