"""P6 测试：真实赛果记录到 R1 偏差数据库（scripts/record_live_result.py）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.record_live_result import record, _auto_pred_hit


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
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


def test_record_writes_row():
    db = _tmp_db()
    try:
        n = record(_rec(), db)
        from data_stores.bias_store import BiasStore
        s = BiasStore(db)
        assert n == 1
        rows = s.query(season="2627")
        assert len(rows) == 1
        r = rows[0]
        assert r["match_id"] == "BUN_2627_R08_Union Berlin_FC Koln_LIVE"
        assert r["actual_ftr"] == "D"
        assert r["pred_home"] == 0.442
        assert r["divergence"] == 1
    finally:
        os.remove(db)


def test_missing_probs_stay_null():
    """诚实原则：未提供的概率（pred_draw/pred_away）必须为 NULL，不虚构。"""
    db = _tmp_db()
    try:
        record(_rec(), db)
        from data_stores.bias_store import BiasStore
        r = BiasStore(db).query(season="2627")[0]
        assert r["pred_draw"] is None
        assert r["pred_away"] is None
    finally:
        os.remove(db)


def test_pred_hit_auto():
    """pred_hit 由 pred_pick 与实际结果自动判定。"""
    assert _auto_pred_hit("home_win", "D") == 0  # 预测主胜、实际平局 → 未中
    assert _auto_pred_hit("home_win", "H") == 1
    assert _auto_pred_hit("draw", "D") == 1
    assert _auto_pred_hit("away_win", "A") == 1
    assert _auto_pred_hit(None, "D") == 0
    assert _auto_pred_hit("home_win", None) == 0


def test_record_does_not_touch_backtest_rows():
    """新增真实记录不得破坏/覆盖既有回测 918 条（不同赛季号隔离）。"""
    db = _tmp_db()
    try:
        # 模拟一条既有回测记录
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
        record(_rec(), db)
        assert s.count() == before + 1
        # 回测那条仍在
        assert s.query(season="2526", round_no=8)[0]["match_id"] == \
            "BUN_2526_R08_Werder Bremen_Union Berlin"
    finally:
        os.remove(db)
