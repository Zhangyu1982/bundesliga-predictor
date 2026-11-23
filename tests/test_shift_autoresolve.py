"""shift 三态自动判定：终极版 + 首发可用才算 computed，--shift-computed 不能绕过。"""
import json
import os

from scripts.record_live_result import resolve_shift


def _write(dirp, season, rnd, cards):
    os.makedirs(dirp, exist_ok=True)
    p = os.path.join(dirp, "{}_R{:02d}.json".format(season, rnd))
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"season": season, "round": rnd, "cards": cards}, f)


def test_computed_only_for_final_stage_with_lineup(tmp_path):
    _write(str(tmp_path), "2627", 11, [
        {"match_id": "M1", "stage": "终极版", "lineup_available": True, "injury_shift": {"home": 0.0, "away": 0.1}},
        {"match_id": "M2", "stage": "初版", "lineup_available": True, "injury_shift": {"home": 0.1, "away": 0.0}},
        {"match_id": "M3", "stage": "终极版", "lineup_available": False, "injury_shift": {"home": 0.0}},
    ])
    r1 = resolve_shift("2627", 11, "M1", snap_dir=str(tmp_path))
    assert r1["basis"] == "computed" and r1["shift_away"] == 0.1
    r2 = resolve_shift("2627", 11, "M2", 0.2, None, snap_dir=str(tmp_path))
    assert r2["basis"] == "unverified" and "初版" in r2["reason"]
    # 首发不可用 = 没算 → null（未计算），不是 0.00
    r3 = resolve_shift("2627", 11, "M3", snap_dir=str(tmp_path))
    assert r3["basis"] is None and "未计算" in r3["reason"]


def test_no_snapshot_means_unverified(tmp_path):
    r = resolve_shift("2627", 12, "MX", 0.0, 0.0, snap_dir=str(tmp_path))
    assert r["basis"] == "unverified" and "无预测快照" in r["reason"]


def test_no_shift_values_means_null(tmp_path):
    _write(str(tmp_path), "2627", 11, [{"match_id": "M4", "stage": "终极版", "lineup_available": True}])
    r = resolve_shift("2627", 11, "M4", snap_dir=str(tmp_path))
    assert r["basis"] is None and r["shift_home"] is None
