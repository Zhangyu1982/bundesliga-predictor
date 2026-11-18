"""球队生命周期：降级保留参数、回归按缺席季数衰减；升班队临时参数到期要能被发现。"""
import json
import os

import pytest

import data.team_lifecycle as tl
import data.param_status as ps


# ---------- 生命周期 ----------
def test_lifecycle_file_shape():
    raw = json.load(open(os.path.join(os.path.dirname(tl.__file__), "team_lifecycle.json"), encoding="utf-8"))
    assert raw["decay_per_season"] == pytest.approx(0.85)
    assert set(raw["relegated"]) == {"BOC", "HDH", "STP", "WOB"}


def test_seasons_absent_counts_seasons_not_years():
    assert tl.seasons_absent("WOB", "2627") == 1      # 2526 赛季末降级，2627 是缺席第 1 季
    assert tl.seasons_absent("WOB", "2728") == 2
    assert tl.seasons_absent("BAY", "2627") == 0      # 没降级过


def test_decay_factor_and_apply():
    assert tl.decay_factor("WOB", "2627") == pytest.approx(0.85)
    assert tl.decay_factor("WOB", "2829") == pytest.approx(0.85 ** 3)
    att, dfn = tl.decayed(2.0, 0.6, 0.85)
    assert att == pytest.approx(1 + 1.0 * 0.85)       # 偏离部分衰减，1.0 是联赛基准
    assert dfn == pytest.approx(1 - 0.4 * 0.85)


def test_carryover_only_touches_returning_teams():
    """当前在德甲的 18 队里没人被降过级 → 不衰减；把 WOB 放回名单才衰减。"""
    params = {"BAY": (2.0, 0.9), "WOB": (1.6, 1.2)}
    out, touched = tl.carryover_params(params, roster_codes={"BAY", "WOB"}, season="2627")
    assert touched == ["WOB"]
    assert out["BAY"] == (2.0, 0.9)
    assert out["WOB"][0] < 1.6


# ---------- 临时参数到期 ----------
def _results():
    return {"records": [
        {"season": "2627", "home": "Elversberg", "away": "Bayern Munich"},
        {"season": "2627", "home": "Bayern Munich", "away": "Elversberg"},
        {"season": "2627", "home": "Paderborn", "away": "Hamburg"},
        {"season": "2526", "home": "Elversberg", "away": "Hamburg"},   # 上一季不算
    ]}


def test_rounds_played_counts_current_season_only():
    assert ps.rounds_played("Elversberg", "2627", _results()) == 2
    assert ps.rounds_played("Paderborn", "2627", _results()) == 1
    assert ps.rounds_played("Schalke 04", "2627", _results()) == 0


def test_status_fresh_aging_expired():
    # 口径：踢满 N 轮（played >= expires）即视为到期，需要重估
    assert ps.status("Paderborn", "2627", _results(), expires_after_rounds=10)["status"] == "fresh"
    assert ps.status("Elversberg", "2627", _results(), expires_after_rounds=3)["status"] == "aging"
    assert ps.status("Elversberg", "2627", _results(), expires_after_rounds=2)["status"] == "expired"


def test_trainer_apply_lifecycle_is_noop_for_current_roster():
    """本季 18 队里没有降级回归队 → 参数一点都不能动。"""
    from trainer import apply_lifecycle
    from data.historical_data import CODE2ENG

    class B:
        pass
    b = B()
    b.dc_attack = {v: 1.3 for v in CODE2ENG.values()}
    b.dc_defence = {v: 0.9 for v in CODE2ENG.values()}
    before = dict(b.dc_attack)
    apply_lifecycle(b, season="2627")
    assert b.dc_attack == before
    assert b.carryover == []


def test_trainer_apply_lifecycle_decays_returning_relegated_team(monkeypatch):
    """把赛季名单临时改成含 WOB，验证衰减真的落到参数上。"""
    from trainer import apply_lifecycle
    import data.season_roster as sr

    monkeypatch.setattr(sr, "load_roster", lambda season=None: {"WOB": "沃夫斯堡", "BAY": "拜仁慕尼黑"})

    class B:
        pass
    b = B()
    b.dc_attack = {"Wolfsburg": 1.6, "Bayern Munich": 2.0}
    b.dc_defence = {"Wolfsburg": 1.2, "Bayern Munich": 0.9}
    apply_lifecycle(b, season="2627")
    assert b.carryover == ["WOB"]
    assert b.dc_attack["Wolfsburg"] == pytest.approx(1 + 0.6 * 0.85)
    assert b.dc_attack["Bayern Munich"] == 2.0
