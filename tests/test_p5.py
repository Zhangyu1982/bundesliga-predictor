"""
P5 优化 · 针对性测试（P1 本地JSON兜底 / P2 injury_shift敏感性标定报告）
覆盖：
  - P1：load_backup/save_backup 本地 JSON 往返；fetch_with_fallback 降级（ESPN失败→读本地缓存+used_backup=True）。
  - P2：calibration_report.json 存在、calibrated=False（诚实）、含网格结果与结论。
"""
from __future__ import annotations
import os, sys, json, importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.injury_fetch as injf


# ---------- P1：本地 JSON 持久化兜底 ----------

def test_save_load_backup_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(injf, "BACKUP_PATH", str(tmp_path / "injuries_backup.json"))
    payload = {"candidates": [{"player": "Kane", "team_code": "BAY"}],
               "manual_injuries": "拜仁: 凯恩[核心]", "updated_at": "2026-11-01T00:00:00"}
    injf.save_backup(payload)
    back = injf.load_backup()
    assert back["candidates"][0]["player"] == "Kane"
    assert back["manual_injuries"].startswith("拜仁")


def test_load_backup_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(injf, "BACKUP_PATH", str(tmp_path / "nope.json"))
    assert injf.load_backup() == {}


def test_fetch_with_fallback_uses_backup_when_espn_down(tmp_path, monkeypatch):
    monkeypatch.setattr(injf, "BACKUP_PATH", str(tmp_path / "injuries_backup.json"))
    # 先写入一份"历史缓存"
    injf.save_backup({"candidates": [{"player": "Alonso", "team_code": "LEV"}],
                      "note": "ESPN官方伤病 1 条", "updated_at": "2026-11-01T00:00:00"})
    # ESPN 实时不可达
    monkeypatch.setattr(injf, "_espn_reachable", lambda: False)
    res = injf.fetch_with_fallback()
    assert res["used_backup"] is True
    assert res["candidates"][0]["player"] == "Alonso"
    assert "本地缓存数据" in res["note"] or "本地" in res["note"]


def test_fetch_with_fallback_saves_backup_when_espn_up(tmp_path, monkeypatch):
    monkeypatch.setattr(injf, "BACKUP_PATH", str(tmp_path / "injuries_backup.json"))
    monkeypatch.setattr(injf, "_espn_reachable", lambda: True)
    monkeypatch.setattr(injf, "fetch_all_candidates",
                        lambda: {"candidates": [{"player": "Kimmich", "team_code": "BAY"}],
                                 "note": "ESPN官方伤病 1 条",
                                 "espn_injuries_count": 1, "news_candidates_count": 0})
    res = injf.fetch_with_fallback()
    assert res["used_backup"] is False
    back = injf.load_backup()
    assert back["candidates"][0]["player"] == "Kimmich"   # 成功时已写回本地 JSON


# ---------- P2：injury_shift 敏感性标定报告（诚实未标定） ----------

def test_calibration_report_exists_and_honest():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "calibration_report.json")
    assert os.path.exists(p), "calibration_report.json 缺失，需先运行 scripts/calibrate_injury.py"
    rep = json.load(open(p, encoding="utf-8"))
    assert rep["kind"] == "injury_shift_sensitivity"
    assert rep["calibrated"] is False          # 诚实：无真实伤病数据 → 不翻 calibrated
    assert rep.get("games", 0) > 100           # 覆盖历史场次
    assert len(rep.get("results", [])) > 1     # 网格结果
    assert "data_source_note" in rep           # 数据源死路如实说明


def test_calibration_report_baseline_consistent():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "calibration_report.json")
    rep = json.load(open(p, encoding="utf-8"))
    bl = rep["baseline"]
    assert bl["key_out_shift"] == 0.10 and bl["doubtful_shift"] == 0.05   # 与 config 经验值一致
    assert 0.9 < bl["logloss"] < 1.3                                        # Log Loss 合理区间
