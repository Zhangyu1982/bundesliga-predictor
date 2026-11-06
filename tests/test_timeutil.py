"""时区口径回归（修复「一屏两个时钟」）。

规则：存储一律 UTC 带 +00:00；展示一律转北京时间；历史无标记值按本地 +08:00 解析。
"""
from datetime import datetime, timezone

import pytest

from data.timeutil import (LOCAL_TZ, age_hours, fmt_local, parse_any,
                           to_local, utc_now_iso)


def test_utc_now_iso_has_offset():
    s = utc_now_iso()
    d = datetime.fromisoformat(s)
    assert d.tzinfo is not None and d.utcoffset() == timezone.utc.utcoffset(d)


def test_naive_historic_value_is_treated_as_local():
    """历史文件里的 "2026-11-06 22:26" 是开发机本地时间，不能当 UTC。"""
    d = parse_any("2026-11-06 22:26")
    assert d.utcoffset() == to_local("2026-11-06 22:26").utcoffset()
    loc = to_local("2026-11-06 22:26")
    assert loc.hour == 22 and loc.minute == 26      # 原样保留，不漂移 8 小时


def test_utc_value_shows_as_beijing():
    """API 的 UTC 时间戳（15:46:03Z）展示应变成 23:46:03。"""
    loc = to_local("2026-11-06T15:46:03+00:00")
    assert (loc.hour, loc.minute, loc.second) == (23, 46, 3)
    assert fmt_local("2026-11-06T15:46:03+00:00").startswith("2026-11-06 23:46:03")
    assert "北京时间" in fmt_local("2026-11-06T15:46:03+00:00")


def test_z_suffix_supported():
    assert to_local("2026-11-06T15:46:03Z").hour == 23


def test_two_clocks_now_agree():
    """同一时刻：UTC 存储值转本地，与本地写入值应落在同一秒附近。"""
    u = utc_now_iso()
    a = to_local(u)
    h = age_hours(u)
    assert h is not None and -1 < h < 1


def test_garbage_returns_none():
    assert parse_any("垃圾时间") is None
    assert age_hours("垃圾时间") is None
    assert fmt_local("垃圾时间") == "垃圾时间"
    assert fmt_local(None) == "—"


def test_baseline_age_compatible_with_old_and_new():
    """baseline_staleness 对旧（本地无标记）/新（UTC 带标记）两种格式都能算天数。"""
    from data.baseline import baseline_staleness
    from data.timeutil import utc_now_iso as _now

    old_bl = {"teams": {"X": {}}, "updated_at": "2026-11-06 22:26",
              "built_round": 9, "window_matches": 6, "core_rate": 0.7}
    new_bl = {"teams": {"X": {}}, "updated_at": _now(), "updated_at_iso": _now(),
              "built_round": 9, "window_matches": 6, "core_rate": 0.7}
    assert baseline_staleness(old_bl, current_round=9)["age_days"] is not None
    age_new = baseline_staleness(new_bl, current_round=9)["age_days"]
    assert age_new is not None and abs(age_new) < 0.01
