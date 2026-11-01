"""
P0 验收测试 —— 4 项验收条件（headless，不依赖浏览器）
运行：python3 -m pytest tests/ -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from config import uncalibrated_thresholds
from data.api_client import resolve_provider
from pipeline import PredictionPipeline


@pytest.fixture(scope="module")
def pipe():
    return PredictionPipeline()


# ---------- 验收条件 #1：数据时效性提示 ----------
def test_freshness_label_marks_cache():
    """零密钥种子源必须标注"数据最后拉取时间 + 缓存"。"""
    from data.api_client import ZeroKeySeedProvider
    seed = ZeroKeySeedProvider()
    f = seed.freshness()
    assert f.get("cached") is True, "零密钥源应标记为缓存"
    fixtures, meta = seed.fetch_round(9), {"freshness": f, "realtime_injury_available": False}
    label = PredictionPipeline().data_freshness_label(meta)
    assert "数据最后拉取时间" in label
    assert "（缓存）" in label
    assert meta["freshness"]["fetched_at"]


# ---------- 验收条件 #2：未标定阈值透传 ----------
def test_uncalibrated_thresholds_exposed(pipe):
    """未标定阈值清单必须透传（含 injury_shift 伤病偏移量）。"""
    uncal = pipe.round_global_summary(9)["uncalibrated_thresholds"]
    assert "injury_shift" in uncal, "伤病偏移量应列为未标定"
    cfg_uncal = uncalibrated_thresholds()
    assert "injury_shift" in cfg_uncal


# ---------- 验收条件 #3：初版/确认版拦截 ----------
def test_confirm_blocked_without_realtime_injury(pipe):
    """零密钥源无实时伤病时，请求确认版必须被拦截并保留为初版。"""
    assert pipe.provider.realtime_injury_available is False, "零密钥源不应具备实时伤病"
    match = {"match_id": "BUN_2026_27_R09_MAI_BAY", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
             "kickoff": "2026-11-07T22:30:00+08:00", "last_updated": "2026-11-07T12:00:00+08:00",
             "injuries": {"home_key_out": [], "away_key_out": ["Musiala"], "home_doubtful": [], "away_doubtful": []},
             "confidence": "中"}
    card = pipe.predict_match(match, 9, stage="确认版", realtime_injury_available=False)
    assert card["confirm_blocked"] is True
    assert card["stage"] == "初版", "无实时伤病时必须拦截为初版"


# ---------- 验收条件 #4：未来无数据轮次降级容错 ----------
def test_future_round_no_data_falls_back(pipe):
    """未来已排期但无伤病数据的轮次：输出基准概率，不抛异常、概率求和=1。"""
    # round 12：已排期未开赛、无伤病
    res12 = pipe.predict_round(12, stage="初版")
    assert res12["meta"]["round_has_data"] is True
    for c in res12["cards"]:
        assert c["data_availability"] == "无伤病数据", "该轮应为无伤病数据"
        s = round(sum(c["probabilities"].values()), 3)
        assert abs(s - 1.0) <= 0.002, f"概率求和应≈1，实际{s}"


def test_no_fixture_round_baseline(pipe):
    """完全无数据的轮次（超出赛季排期，真实源亦无）→ GBM→DC 兜底输出基准概率，不抛 500。"""
    res = pipe.predict_round(40, stage="初版")  # 40 轮超出 34 轮赛季，种子与真实源均无
    assert res["meta"]["round_has_data"] is False
    card = res["cards"][0]
    assert "兜底基准" in card["risk_note"]
    assert card["data_availability"] == "无数据"
    s = round(sum(card["probabilities"].values()), 3)
    assert abs(s - 1.0) <= 0.002, f"基准概率求和应≈1，实际{s}"
    assert card["score_top2"], "基准输出应有比分 Top2"
