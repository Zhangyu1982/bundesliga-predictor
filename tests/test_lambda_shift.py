"""展示层补 λ 期望 + shift 两列；λ 诊断改合计口径。
运行：python -m pytest tests/test_lambda_shift.py -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pipeline import PredictionPipeline, lambda_total_of


@pytest.fixture(scope="module")
def pipe():
    return PredictionPipeline()


def test_probs_for_exposes_lambda_and_consistent_with_matrix(pipe):
    res = pipe._probs_for("MUN", "KOE")
    lam = res.get("lam") or {}
    assert set(lam) >= {"home", "away", "total"}, "必须导出 λ 期望（主/客/合计）"
    assert abs(lam["total"] - (lam["home"] + lam["away"])) < 0.02, "合计 = 主 + 客"
    mat = pipe._bundle.dc_score_matrix(pipe._eng("MUN"), pipe._eng("KOE"), 8)
    tot = sum(mat.values())
    exp_h = sum(i * p for (i, _), p in mat.items()) / tot
    assert abs(lam["home"] - exp_h) < 0.06, "λ 必须与展示矩阵的期望进球一致"


def test_cards_carry_lambda_and_shift_columns(pipe):
    res = pipe.predict_round(9, stage="初版")
    cards = res.get("cards") or []
    if not cards:
        pytest.skip("免费层限流导致本轮无实时对阵")
    for c in cards:
        lam = c.get("lam") or {}
        assert lam.get("total") is not None, "卡片必须带 λ 期望合计"
        sh = c.get("injury_shift") or {}
        assert "home" in sh and "away" in sh, "卡片必须带 shift 两列（主/客）"
        for k in ("home", "away"):
            assert isinstance(sh[k], (int, float)), f"shift[{k}] 必须是数值，便于回测"


def test_lambda_total_uses_sum_not_top1_scoreline(pipe):
    """合计口径：λ 求和，不是把 Top1 比分相加（后者会系统性偏低）。"""
    res = pipe.predict_round(9, stage="初版")
    cards = res.get("cards") or []
    if not cards:
        pytest.skip("免费层限流导致本轮无实时对阵")
    lam_sum = lambda_total_of(cards)
    top1_sum = sum(sum(int(x) for x in c["score_top2"][0]["score"].split(":")) for c in cards)
    assert lam_sum > 0
    # 第9轮实测 λ 合计 ~31，Top1 比分求和 ~22：两者量纲不同，必须区分
    assert lam_sum > top1_sum, "合计 λ 应高于 Top1 比分求和（λ 才是期望进球）"


def test_lambda_total_handles_empty_and_missing():
    assert lambda_total_of([]) == 0.0
    assert lambda_total_of([{"score_top2": []}]) == 0.0
