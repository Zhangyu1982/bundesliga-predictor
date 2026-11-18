"""缓存 key 的 season 迁移：翻季时同一轮次必须 miss，旧 key 只做一次回退读取。"""
import logging

import pytest

from data.api_client import LiveFootballDataProvider
from data.timeutil import season_label
from data_stores.cache import Cache


@pytest.fixture()
def provider(tmp_path, monkeypatch):
    p = LiveFootballDataProvider(token="dummy")
    p.cache = Cache(str(tmp_path / "cache.db"))
    return p


def test_resource_key_contains_season():
    assert LiveFootballDataProvider._round_resource("BL1", 10, "2627") == "round:2627:BL1:10"


def test_season_label_rolls_over_in_july():
    from datetime import datetime, timezone
    assert season_label(datetime(2026, 11, 18, tzinfo=timezone.utc)) == "2627"
    assert season_label(datetime(2026, 5, 20, tzinfo=timezone.utc)) == "2526"
    assert season_label(datetime(2026, 7, 1, tzinfo=timezone.utc)) == "2627"


def test_same_round_different_season_must_miss(provider):
    """核心用例：season 变化时同一 round 必须不命中。"""
    provider.cache.set_daily("fdo:round:2627:BL1:10", {"matches": [1, 2, 3]})
    assert provider._get_cached("round:2627:BL1:10") == {"matches": [1, 2, 3]}
    assert provider._get_cached("round:2728:BL1:10") is None


def test_legacy_key_read_once_then_migrated(provider, caplog):
    """旧键（无 season）仍能读到一次，同时写到新键并打 deprecation。"""
    provider.cache.set_daily("fdo:round:BL1:10", {"matches": [{"id": 1}]})
    with caplog.at_level(logging.WARNING):
        hit = provider._cached_round("BL1", 10, "2627")
    assert hit == {"matches": [{"id": 1}], "cached": True, "legacy_key": True}
    assert any("deprecation" in r.message.lower() or "deprecation" in str(r.msg).lower()
               for r in caplog.records)
    # 迁移后新键可独立命中，且不再带 legacy 标记
    again = provider._cached_round("BL1", 10, "2627")
    assert again == {"matches": [{"id": 1}], "cached": True}


def test_new_key_wins_over_legacy(provider):
    provider.cache.set_daily("fdo:round:BL1:10", {"matches": ["old"]})
    provider.cache.set_daily("fdo:round:2627:BL1:10", {"matches": ["new"]})
    hit = provider._cached_round("BL1", 10, "2627")
    assert hit["matches"] == ["new"]
    assert "legacy_key" not in hit
