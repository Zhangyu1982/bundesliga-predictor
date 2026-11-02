"""P6 测试：真实赛果记录到 R1 偏差数据库（bias.db）+ 入库 JSON（live_results.json）合并。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.record_live_result import record, _auto_pred_hit  # noqa: E402
from data.live_results import (  # noqa: E402
    load_live_results, save_live_results, merge_with_bias,
)


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _tmp_json():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # 让 load 走"文件不存在 → 空"路径
    return path


def _rec():
    return {
        "season": "2627", "round_no": 8,
        "match_id": "BUN_2627_R08_Union Berlin_FC Koln_LIVE",
        "home": "Union Berlin", "away": "FC Koln",
        "actual_hg": 1, "actual_ag": 1, "actual_ftr": "D",
        "pred_home": 0.442,
        "pred_pick": "home_win", "elo_dir": "home_win", "dc_dir": "away_win",
        "divergence": 1,
    }


def test_record_writes_both_db_and_json():
    """record() 同时写入 bias.db 与 live_results.json。"""
    db, jp = _tmp_db(), _tmp_json()
    try:
        n = record(_rec(), db, jp)
        from data_stores.bias_store import BiasStore
        assert n == 1
        assert len(BiasStore(db).query(season="2627")) == 1
        lr = load_live_results(jp)
        assert len(lr) == 1
        assert lr[0]["match_id"] == "BUN_2627_R08_Union Berlin_FC Koln_LIVE"
    finally:
        os.remove(db); os.path.exists(jp) and os.remove(jp)


def test_missing_probs_stay_null():
    """诚实原则：未提供的概率（pred_draw/pred_away）必须为 NULL，不虚构。"""
    db, jp = _tmp_db(), _tmp_json()
    try:
        record(_rec(), db, jp)
        from data_stores.bias_store import BiasStore
        r = BiasStore(db).query(season="2627")[0]
        assert r["pred_draw"] is None and r["pred_away"] is None
        lr = load_live_results(jp)
        assert lr[0]["pred_draw"] is None and lr[0]["pred_away"] is None
    finally:
        os.remove(db); os.path.exists(jp) and os.remove(jp)


def test_pred_hit_auto():
    assert _auto_pred_hit("home_win", "D") == 0
    assert _auto_pred_hit("home_win", "H") == 1
    assert _auto_pred_hit("draw", "D") == 1
    assert _auto_pred_hit("away_win", "A") == 1
    assert _auto_pred_hit(None, "D") == 0
    assert _auto_pred_hit("home_win", None) == 0


def test_record_does_not_touch_backtest_rows():
    """新增真实记录不得破坏/覆盖既有回测 918 条（不同赛季号隔离）。"""
    db, jp = _tmp_db(), _tmp_json()
    try:
        from data_stores.bias_store import BiasStore
        s = BiasStore(db)
        s.write_records([{**{
            "season": "2526", "round_no": 8,
            "match_id": "BUN_2526_R08_Werder Bremen_Union Berlin",
            "home": "Werder Bremen", "away": "Union Berlin",
            "actual_hg": 1, "actual_ag": 0, "actual_ftr": "H",
            "pred_home": 0.676, "pred_draw": 0.0, "pred_away": 0.324,
            "pred_pick": "Werder Bremen", "pred_hit": 1,
            "elo_dir": "home_win", "dc_dir": "away_win",
            "divergence": 1, "bias_streak": 1,
        }}])
        before = s.count()
        record(_rec(), db, jp)
        assert s.count() == before + 1
        assert s.query(season="2526", round_no=8)[0]["match_id"] == \
            "BUN_2526_R08_Werder Bremen_Union Berlin"
    finally:
        os.remove(db); os.path.exists(jp) and os.remove(jp)


def test_merge_dedup_live_first():
    """merge_with_bias：同名 match_id 真实赛果优先，bias.db 历史补全。"""
    db, jp = _tmp_db(), _tmp_json()
    try:
        from data_stores.bias_store import BiasStore
        BiasStore(db).write_records([{**_rec(), "pred_draw": None, "pred_away": None,
                                      "pred_hit": 0, "bias_streak": 0,
                                      "actual_hg": 9, "actual_ag": 9}])  # bias 同名旧值
        BiasStore(db).write_records([{**_rec(), "pred_draw": None, "pred_away": None,
                                      "pred_hit": 0, "bias_streak": 0,
                                      "match_id": "BUN_2627_R08_Other_Match",
                                      "home": "Other"}])  # bias 另场
        save_live_results([_rec()], jp)
        merged = merge_with_bias(load_live_results(jp), db)
        by_id = {r["match_id"]: r for r in merged}
        # 同名：live（1-1）优先于 bias（9-9）
        assert by_id["BUN_2627_R08_Union Berlin_FC Koln_LIVE"]["actual_hg"] == 1
        # bias 另场保留
        assert "BUN_2627_R08_Other_Match" in by_id
        assert len(merged) == 2
    finally:
        os.remove(db); os.path.exists(jp) and os.remove(jp)


def test_load_missing_returns_empty():
    assert load_live_results(_tmp_json()) == []


def test_no_predict_hit_is_null():
    """诚实：未提供赛前预测时不误判为'未命中'，pred_hit 应为 NULL。"""
    db, jp = _tmp_db(), _tmp_json()
    try:
        rec = _rec()
        rec["pred_pick"] = None
        rec["pred_home"] = None
        record(rec, db, jp)
        from data_stores.bias_store import BiasStore
        r = BiasStore(db).query(season="2627")[0]
        assert r["pred_pick"] is None
        assert r["pred_hit"] is None
        assert load_live_results(jp)[0]["pred_hit"] is None
    finally:
        os.remove(db); os.path.exists(jp) and os.remove(jp)
