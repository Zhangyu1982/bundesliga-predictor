"""卡片必须能看见「降级队回归、参数已衰减」：不展示等于静默改参数。

用纯函数直接验证展示链路，不依赖网络赛程（云主机配额耗尽时整轮会被名单闸门拦住）。
"""
import pytest

from pipeline import mark_param_flags


class _Bundle:
    def __init__(self, provisional, carryover):
        self.provisional = provisional
        self.carryover = carryover


def _cards():
    return [
        {"match_id": "m1", "home_team": "Hamburg", "away_team": "Bayern"},
        {"match_id": "m2", "home_team": "Koln", "away_team": "Elversberg"},
        {"match_id": "m3", "home_team": "Mainz", "away_team": "Freiburg"},
    ]


def _fixtures():
    return [
        {"match_id": "m1", "home_code": "HSV", "away_code": "BAY", "kickoff": "2026-11-21T14:30:00Z"},
        {"match_id": "m2", "home_code": "KOE", "away_code": "ELV", "kickoff": "2026-11-21T14:30:00Z"},
        {"match_id": "m3", "home_code": "MAI", "away_code": "SCF", "kickoff": "2026-11-21T14:30:00Z"},
    ]


def _eng(code: str) -> str:
    from data.historical_data import CODE2ENG
    return CODE2ENG.get(code, code)


def test_carryover_and_provisional_marked_per_match():
    b = _Bundle(provisional=["Elversberg"], carryover=["HSV"])
    cards = mark_param_flags(_cards(), _fixtures(), b, eng=_eng)
    assert cards[0]["carryover_teams"] == ["Hamburg"]
    assert cards[0]["provisional_teams"] == []
    assert cards[1]["provisional_teams"] == ["Elversberg"]
    assert cards[1]["carryover_teams"] == []
    assert cards[2]["carryover_teams"] == [] and cards[2]["provisional_teams"] == []


def test_all_cards_have_fields_even_when_nothing_marked():
    b = _Bundle(provisional=[], carryover=[])
    cards = mark_param_flags(_cards(), _fixtures(), b, eng=_eng)
    for c in cards:
        assert c["carryover_teams"] == [] and c["provisional_teams"] == []


def test_unknown_fixture_does_not_break():
    b = _Bundle(provisional=["Elversberg"], carryover=["HSV"])
    cards = mark_param_flags([{"match_id": "nope"}], _fixtures(), b, eng=_eng)
    assert cards[0]["carryover_teams"] == [] and cards[0]["provisional_teams"] == []
