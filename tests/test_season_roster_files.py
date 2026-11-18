"""赛季名单按 season 读取：缺该季名单必须拒绝出卡，不能用上一季名单顶替。"""
import json
import os

import pytest

import data.season_roster as sr


ROUND10 = [("BAY", "KOE"), ("FCU", "RBL"), ("LEV", "PAD"), ("BMG", "DOR"), ("SGE", "TSG"),
           ("ELV", "SCF"), ("SCH", "STU"), ("FCA", "MAI"), ("SVW", "HSV")]


def test_roster_2627_file_has_18_and_promoted_three():
    roster = sr.load_roster("2627")
    assert len(roster) == 18
    assert {"ELV", "PAD", "SCH", "KOE"} <= set(roster)
    assert roster["ELV"] == "埃尔弗斯贝格"


def test_default_roster_follows_current_season():
    from data.timeutil import season_label
    assert set(sr.load_roster()) == set(sr.load_roster(season_label()))


def test_unknown_season_returns_none_not_last_season():
    assert sr.load_roster("9900") is None
    assert sr.roster_codes("9900") == set()


def test_file_matches_python_constant():
    """2627.json 与旧常量必须一致，迁移期两条路径不能漂。"""
    roster = sr.load_roster("2627")
    assert roster == sr.SEASON_ROSTER_2627


def _pipeline_with(fx_meta):
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    p.fetch_round = lambda r: fx_meta
    return p


def _fixtures():
    from data.api_client import team_zh
    return [{"match_id": f"x{i}", "home_code": h, "away_code": a,
             "home_team": team_zh(h), "away_team": team_zh(a),
             "kickoff": "2026-11-21T14:30:00Z"} for i, (h, a) in enumerate(ROUND10)]


def test_gate_blocks_when_season_roster_missing():
    """赛程是 2728 季（名单未登记）→ 必须拦，且理由写明"名单未登记"，不能说别的。"""
    p = _pipeline_with((_fixtures(), {"freshness": {}, "realtime_injury_available": False,
                                      "round_has_data": True, "round_no": 10, "season": "2728"}))
    res = p.predict_round(10, stage="初版")
    assert res["cards"] == []
    assert "名单未登记" in res["coverage_error"]["message"]


def test_gate_passes_with_registered_season():
    p = _pipeline_with((_fixtures(), {"freshness": {}, "realtime_injury_available": False,
                                      "round_has_data": True, "round_no": 10, "season": "2627"}))
    res = p.predict_round(10, stage="初版")
    assert res.get("coverage_error") is None and len(res["cards"]) == 9
