"""赛果记录自证字段：kickoff / season / matchday / fixture_source。
运行：python -m pytest tests/test_record_selfcert.py -q
"""
import json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from scripts.record_live_result import (
    parse_kickoff, season_label, derive_matchday, fixture_source, record,
)


def test_season_label_derives_from_kickoff():
    # 跨年赛季：7 月起算
    assert season_label(datetime(2026, 11, 7, 22, 30, tzinfo=timezone(timedelta(hours=8)))) == "2627"
    assert season_label(datetime(2027, 5, 20, 18, 30, tzinfo=timezone(timedelta(hours=8)))) == "2627"
    assert season_label(datetime(2027, 8, 20, 18, 30, tzinfo=timezone(timedelta(hours=8)))) == "2728"


def test_parse_kickoff_requires_timezone():
    ok = parse_kickoff("2026-11-07T22:30:00+08:00")
    assert ok.tzinfo is not None
    with pytest.raises(ValueError):
        parse_kickoff("2026-11-07T22:30:00")      # 无时区 → 无法自证，必须拒绝


def test_derive_matchday_matches_fixture_by_teams_and_date():
    fixtures = [
        {"matchday": 9, "kickoff": "2026-11-07T22:30:00+08:00", "home": "Mainz", "away": "Bayern Munich"},
        {"matchday": 10, "kickoff": "2026-11-21T22:30:00+08:00", "home": "Bayern Munich", "away": "Leverkusen"},
    ]
    k = parse_kickoff("2026-11-07T22:30:00+08:00")
    assert derive_matchday(k, "Mainz", "Bayern Munich", fixtures) == 9
    assert derive_matchday(k, "Bayern Munich", "Leverkusen", fixtures) is None   # 日期不符


def test_fixture_source_self_describes_origin():
    fs = fixture_source(endpoint="/v4/competitions/BL1/matches?season=2026&matchday=9",
                        season="2627", matchday=9, fetched_at="2026-11-06T23:30:07+08:00")
    for k in ("endpoint", "season", "matchday", "fetched_at"):
        assert k in fs
    assert fs["matchday"] == 9 and fs["season"] == "2627"


def test_record_persists_selfcert_fields(tmp_path):
    live = tmp_path / "live.json"
    live.write_text("[]", encoding="utf-8")
    db = tmp_path / "bias.db"
    rec = {
        "season": "2627", "round_no": 10, "match_id": "T_1",
        "home": "Mainz", "away": "Bayern Munich", "actual_hg": 1, "actual_ag": 2,
        "actual_ftr": "A", "pred_pick": "away_win",
        "kickoff": "2026-11-07T22:30:00+08:00",
        "fixture_source": fixture_source("/v4/competitions/BL1/matches?matchday=9", "2627", 9,
                                         "2026-11-06T23:30:07+08:00"),
    }
    record(rec, db_path=str(db), live_path=str(live))
    from data.live_results import load_live_results
    saved = load_live_results(str(live))
    assert len(saved) == 1
    assert saved[0]["kickoff"] == "2026-11-07T22:30:00+08:00"
    assert saved[0]["fixture_source"]["endpoint"].startswith("/v4/competitions/BL1")
    assert saved[0]["fixture_source"]["matchday"] == 9
    assert saved[0]["season"] == "2627"
