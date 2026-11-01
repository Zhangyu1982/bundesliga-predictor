"""
P3 闭环基础 · 验收测试：R1/R4 历史实测标定 + R5 记录库
运行：python3 -m pytest tests/ -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from config import RULES, uncalibrated_thresholds
from data_stores.bias_store import BiasStore
from rules.iteration_rules import IterationRules


# ---------- R1 阈值实测标定 ----------
def test_r1_calibrated_from_backtest():
    r1 = RULES["R1_bias_record"]
    assert r1["calibrated"] is True, "R1 应已标定"
    assert r1["consecutive_rounds"] == 3, "实测取连续3轮同方向偏差触发重校准"
    assert "64.4%" in r1["calibration_note"], "标定注记应含实测提升数据"
    assert "R1_bias_record" not in uncalibrated_thresholds()


# ---------- R4 分歧阈值实测标定（实数） ----------
def test_r4_threshold_is_real_calibrated():
    r4 = RULES["R4_divergence_flag"]
    assert r4["calibrated"] is True, "R4 应已标定"
    assert isinstance(r4["submodel_disagreement_threshold"], (int, float))
    assert r4["submodel_disagreement_threshold"] == 0.10, "实测取 Δp≥0.10 为分歧强度阈值"
    assert "R4_divergence_flag" not in uncalibrated_thresholds()


def test_mark_divergence_uses_numeric_threshold():
    """R4 代码应实际使用 0.10 阈值：Δp<0.10 同方向一致不标，Δp≥0.10 方向不一致才降档。"""
    rules = IterationRules()
    # 方向一致 → 不标（无论 Δp）
    d0 = rules.mark_divergence("home_win", "home_win", "home_win", "T", 0.30, 0.10)
    assert d0["flagged"] is False
    # 方向不一致但 Δp 很小 → 不标
    d1 = rules.mark_divergence("home_win", "away_win", "home_win", "T", 0.42, 0.41)
    assert d1["flagged"] is False, f"Δp<0.10 不应标分歧，实际{d1}"
    # 方向不一致且 Δp≥0.10 → 标分歧并降一档
    d2 = rules.mark_divergence("home_win", "away_win", "home_win", "T", 0.60, 0.20)
    assert d2["flagged"] is True and d2["confidence_downgrade_steps"] == 1


# ---------- R5 记录库：历史结果 vs 预测结果比对 ----------
def test_bias_store_has_backtest_records():
    store = BiasStore()
    assert store.count() >= 900, f"记录库应有 900+ 条历史比对，实际{store.count()}"
    stats = store.calibration_stats()
    assert "prediction_hit_rate" in stats and 0 <= stats["prediction_hit_rate"] <= 1


def test_bias_store_prev_round_summary():
    store = BiasStore()
    summary = store.prev_round_bias_summary(9)
    assert summary["records"] >= 0
    assert "hit_rate" in summary


def test_bias_record_fields_complete():
    store = BiasStore()
    rec = store.query(round_no=9)[0]
    for field in ("season", "round_no", "home", "away", "actual_ftr",
                  "pred_pick", "pred_hit", "elo_dir", "dc_dir", "divergence", "bias_streak"):
        assert field in rec, f"缺字段 {field}"
