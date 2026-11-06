"""P5.2 · 基线自动重建 + 核心口径收紧（0.7 / 6 场）单测。

原则：全部离线，不碰真实 BASELINE_FILE / 锁文件（monkeypatch 到 tmp_path）。
"""
import json
import os
import time
from datetime import datetime, timedelta

import pytest

import data.baseline as B


@pytest.fixture()
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "BASELINE_FILE", str(tmp_path / "baseline_xi.json"))
    monkeypatch.setattr(B, "REBUILD_LOCK", str(tmp_path / ".lock"))
    monkeypatch.setattr(B, "REBUILD_STATE", str(tmp_path / ".state.json"))
    return tmp_path


def _bl(updated_at, built_round=None, teams_ok=18):
    teams = {f"T{i}": {"available": True, "xi": []} for i in range(teams_ok)}
    return {"updated_at": updated_at.strftime("%Y-%m-%d %H:%M"),
            "updated_at_iso": updated_at.isoformat(timespec="seconds"),
            "built_round": built_round, "window_matches": 6, "core_rate": 0.7,
            "teams": teams}


# ---------- 陈旧判定 ----------

def test_stale_by_round_gap():
    bl = _bl(datetime.now(), built_round=5)
    assert B.baseline_staleness(bl, current_round=8)["stale"] is True   # 差 3 → 触发
    assert B.baseline_staleness(bl, current_round=7)["stale"] is False  # 差 2 → 不触发


def test_stale_by_age():
    bl = _bl(datetime.now() - timedelta(days=15))
    info = B.baseline_staleness(bl, current_round=9)
    assert info["stale"] is True and "天未更新" in info["reason"]


def test_fresh_when_recent_and_same_round():
    bl = _bl(datetime.now() - timedelta(days=1), built_round=9)
    info = B.baseline_staleness(bl, current_round=9)
    assert info["stale"] is False and info["reason"] == ""


def test_missing_built_round_only_uses_date():
    """老基线没有 built_round：绝不能因此判定陈旧（否则每次启动都会重建）。"""
    assert B.baseline_staleness(_bl(datetime.now(), built_round=None), 9)["stale"] is False
    assert B.baseline_staleness(_bl(datetime.now() - timedelta(days=20), None), 9)["stale"] is True


def test_missing_round_number_only_uses_date():
    bl = _bl(datetime.now(), built_round=3)
    assert B.baseline_staleness(bl, None)["stale"] is False


def test_no_baseline_counts_as_stale():
    assert B.baseline_staleness({}, 9)["reason"] == "no_baseline"


def test_bad_timestamp_does_not_crash():
    bl = _bl(datetime.now(), built_round=9)
    bl["updated_at"] = "垃圾时间"
    bl["updated_at_iso"] = "also-bad"
    assert B.baseline_staleness(bl, 9)["stale"] is False


# ---------- 落盘守卫（重建失败不能毁掉旧基线） ----------

def test_save_rejects_low_quality_result(tmp_paths):
    good = _bl(datetime.now(), 9, teams_ok=18)
    assert B.save_baseline(good) is True
    before = open(B.BASELINE_FILE, encoding="utf-8").read()

    bad = _bl(datetime.now(), 9, teams_ok=2)          # 只有 2 队可用 < 阈值 9
    assert B.save_baseline(bad) is False
    assert open(B.BASELINE_FILE, encoding="utf-8").read() == before  # 旧文件原样
    json.loads(before)  # 仍是合法 JSON


def test_atomic_write_leaves_no_tmp(tmp_paths):
    assert B.save_baseline(_bl(datetime.now(), 9)) is True
    left = [p for p in os.listdir(str(tmp_paths)) if ".tmp." in p]
    assert left == []


def test_guard_disabled_allows_partial(tmp_paths):
    assert B.save_baseline(_bl(datetime.now(), 9, teams_ok=1), guard=False) is True


# ---------- 自动触发 ----------

def test_auto_rebuild_skipped_when_fresh(tmp_paths, monkeypatch):
    B.save_baseline(_bl(datetime.now(), 9))
    called = []
    monkeypatch.setattr(B, "build_baseline", lambda **k: called.append(1) or {})
    res = B.maybe_auto_rebuild(current_round=9)
    assert res == {**res, "started": False, "reason": "fresh"}
    time.sleep(0.2)
    assert called == []


def test_auto_rebuild_disabled(tmp_paths, monkeypatch):
    B.save_baseline(_bl(datetime.now() - timedelta(days=30)))
    monkeypatch.setitem(B.LINEUP_CFG, "auto_rebuild", False)
    res = B.maybe_auto_rebuild(current_round=20)
    assert res["started"] is False and res["reason"] == "disabled"
    # 显式 force（用户手动点按钮那条路）不受开关限制
    monkeypatch.setattr(B, "build_baseline", lambda **k: {})
    assert B.maybe_auto_rebuild(current_round=20, force=True)["started"] is True


def test_auto_rebuild_starts_thread_and_writes_state(tmp_paths, monkeypatch):
    B.save_baseline(_bl(datetime.now() - timedelta(days=30), built_round=2))

    def fake_build(**k):
        # 模拟真实构建：写入新基线与轮次
        d = _bl(datetime.now(), built_round=k.get("round_hint"))
        B.save_baseline(d)
        return {**d, "saved": True, "updated_at": "2026-11-06 23:00", "window_matches": 6}

    monkeypatch.setattr(B, "build_baseline", fake_build)
    res = B.maybe_auto_rebuild(current_round=9)
    assert res["started"] is True
    for _ in range(60):
        if B.rebuild_status().get("status") in ("done", "failed"):
            break
        time.sleep(0.05)
    st = B.rebuild_status()
    assert st["status"] == "done" and st["teams_ok"] == 18
    assert not os.path.exists(B.REBUILD_LOCK)          # 锁已释放
    assert B.maybe_auto_rebuild(current_round=9)["reason"] == "fresh"  # 重建后不再触发


def test_auto_rebuild_busy_when_locked(tmp_paths, monkeypatch):
    B.save_baseline(_bl(datetime.now() - timedelta(days=30)))
    with open(B.REBUILD_LOCK, "w") as f:
        f.write("999")
    monkeypatch.setattr(B, "build_baseline", lambda **k: {})
    assert B.maybe_auto_rebuild(current_round=9)["reason"] == "busy"


def test_zzz_stale_lock_is_reclaimed(tmp_paths):
    B.save_baseline(_bl(datetime.now() - timedelta(days=30)))
    with open(B.REBUILD_LOCK, "w") as f:
        f.write("999")
    old = time.time() - 3600 * 3                     # 3 小时前的僵尸锁
    os.utime(B.REBUILD_LOCK, (old, old))
    assert B._lock_acquire(ttl_minutes=15) is True


def test_auto_rebuild_failure_keeps_old_baseline(tmp_paths, monkeypatch):
    B.save_baseline(_bl(datetime.now() - timedelta(days=30), built_round=2))
    before = open(B.BASELINE_FILE, encoding="utf-8").read()

    def boom(**k):
        raise RuntimeError("network down")
    monkeypatch.setattr(B, "build_baseline", boom)
    res = B.maybe_auto_rebuild(current_round=9)
    assert res["started"] is True
    for _ in range(60):
        if B.rebuild_status().get("status") == "failed":
            break
        time.sleep(0.05)
    assert "network down" in B.rebuild_status()["error"]
    assert open(B.BASELINE_FILE, encoding="utf-8").read() == before


def test_rebuild_now_busy(tmp_paths):
    with open(B.REBUILD_LOCK, "w") as f:
        f.write("999")
    r = B.rebuild_now(current_round=9)
    assert r["ok"] is False and r["reason"] == "busy"


# ---------- 核心口径：0.7 × 6 场 ----------

def _rows(starts_list, n=6):
    return [{"name": f"P{s}", "starts": s, "rate": round(s / n, 3)} for s in starts_list]


def test_core_requires_five_of_six():
    rows = _rows([6, 5, 4, 3, 1])
    core = B.select_core(rows, 0.7)
    assert [r["starts"] for r in core] == [6, 5]     # 4/6=0.667 被排除
    assert all(r["rate"] >= 0.7 for r in core)


def test_core_excludes_zero_starts_even_if_rate_missing():
    assert B.select_core([{"name": "X", "starts": 0, "rate": 1.0}], 0.7) == []


def test_old_wide_core_would_have_included_rotation():
    """对照：同样的 4/6 球员在旧口径 0.6 下会被当核心（说明收紧确实生效）。"""
    rows = _rows([4])
    assert B.select_core(rows, 0.6) != []
    assert B.select_core(rows, 0.7) == []


def test_config_now_uses_six_and_seventy():
    from config import LINEUP_SHIFT as C
    assert C["window_matches"] == 6
    assert C["core_rate"] == 0.7
    assert C["auto_rebuild"] is True
    assert C["rebuild_round_gap"] == 3
    assert C["rebuild_max_age_days"] == 14
