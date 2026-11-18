"""赛程完整性三件套：出卡前校验队集合 / 卡片盖赛程指纹 / 刷新比对指纹。
运行：python -m pytest tests/test_fixture_integrity.py -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pipeline import (
    round_coverage, fixture_fingerprint, snapshot_changed, CODE2ENG,
)


def _fx(home_code, away_code, kickoff, mid=None):
    return {"match_id": mid or f"BUN_R_{home_code}_{away_code}",
            "home_code": home_code, "away_code": away_code,
            "home_team": home_code, "away_team": away_code, "kickoff": kickoff}


FIXTURES_OK = [
    _fx("BAY", "LEV", "2026-11-21T22:30:00+08:00"),
    _fx("DOR", "RBL", "2026-11-22T03:30:00+08:00"),
]
FIXTURES_BAD = [
    _fx("BAY", "LEV", "2026-11-21T22:30:00+08:00"),
    _fx("WOB", "STP", "2026-11-22T03:30:00+08:00"),   # 模型不覆盖
]


def test_coverage_passes_when_all_codes_known():
    cover = set(CODE2ENG)
    c = round_coverage(FIXTURES_OK, cover)
    assert c["ok"] is True and c["missing"] == []


def test_coverage_reports_missing_codes_and_blocks_cards():
    cover = set(CODE2ENG) - {"WOB", "STP"}
    c = round_coverage(FIXTURES_BAD, cover)
    assert c["ok"] is False
    assert c["missing"] == ["STP", "WOB"]
    assert "WOB" in c["message"] and "STP" in c["message"]
    assert c["message"].startswith("赛程名单与模型覆盖不一致（缺 ")


def test_fingerprint_is_stable_and_sensitive_to_change():
    fp1 = fixture_fingerprint(FIXTURES_OK, fetched_at="2026-11-18T20:00:00+08:00")
    fp2 = fixture_fingerprint(FIXTURES_OK, fetched_at="2026-11-19T10:00:00+08:00")
    assert fp1["payload_hash"] == fp2["payload_hash"], "哈希只看赛程内容，不看拉取时间"
    assert fp1["fetched_at"] != fp2["fetched_at"]
    assert sorted(fp1["codes"]) == ["BAY", "DOR", "LEV", "RBL"]
    assert len(fp1["event_ids"]) == 2

    changed = [dict(FIXTURES_OK[0], kickoff="2026-11-22T22:30:00+08:00"), FIXTURES_OK[1]]
    fp3 = fixture_fingerprint(changed)
    assert fp3["payload_hash"] != fp1["payload_hash"], "开赛时间变了必须换指纹"


def test_snapshot_changed_detects_upstream_revision():
    old = fixture_fingerprint(FIXTURES_OK)
    same = fixture_fingerprint(FIXTURES_OK)
    new = fixture_fingerprint(FIXTURES_BAD)
    assert snapshot_changed(old, same) is False
    assert snapshot_changed(old, new) is True
    assert snapshot_changed(None, new) is True      # 没有旧快照 → 视为需要重算


def test_predict_round_blocks_cards_when_coverage_missing(monkeypatch=None):
    """队集合校验不过 → cards 必须为空，并给出缺项提示（不许照常出 9 张）。"""
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    bad = [_fx("BAY", "ZZZ", "2026-11-21T22:30:00+08:00")]
    meta = {"freshness": {"cached": False, "fetched_at": "2026-11-18T20:00:00+08:00"},
            "realtime_injury_available": False, "round_has_data": True, "round_no": 10}
    p.fetch_round = lambda r: (bad, dict(meta))     # 阻断网络
    res = p.predict_round(10, stage="初版")
    assert res["cards"] == [], "覆盖不一致时不得出卡"
    assert res["coverage_error"]["missing"] == ["ZZZ"]
    assert "赛程名单与模型覆盖不一致" in res["coverage_error"]["message"]


def test_fixtures_origin_marks_seed_fallback():
    """实时源失败时用的示例赛程必须被标记，不能冒充实时赛程。"""
    from data.api_client import LiveFootballDataProvider
    from data.seed_data import get_round_fixtures

    prov = LiveFootballDataProvider(token="dummy")
    prov._last_round_source = "live"
    assert prov._fallback_fixtures(9, "x")                          # 走兜底
    assert prov._last_round_source == "seed_fallback"


def test_pipeline_meta_carries_fixtures_source(monkeypatch=None):
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    p.provider._last_round_source = "seed_fallback"
    fx, meta = p.fetch_round(10)
    assert meta.get("fixtures_source") == "seed_fallback"
