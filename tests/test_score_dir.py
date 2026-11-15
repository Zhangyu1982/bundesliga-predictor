"""比分口径改造验收：方向内最可能 + 全场最高 并列显示（不动模型概率）。
运行：python -m pytest tests/test_score_dir.py -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pipeline import direction_top_scores, score_line_for


def _dir_of(score):
    g1, g2 = (int(x) for x in score.split(":"))
    return "home_win" if g1 > g2 else ("away_win" if g1 < g2 else "draw")


def test_direction_top_scores_picks_best_cell_per_direction():
    matrix = {
        (2, 1): 0.090,
        (1, 0): 0.120,
        (1, 1): 0.131,
        (0, 1): 0.050,
        (0, 2): 0.030,
        (3, 3): 0.010,
    }
    out = direction_top_scores(matrix)
    assert set(out) == {"home_win", "draw", "away_win"}
    assert out["home_win"]["score"] == "1:0"
    assert out["draw"]["score"] == "1:1"
    assert out["away_win"]["score"] == "0:1"
    assert abs(out["draw"]["prob"] - 0.131) < 1e-9
    for d, v in out.items():
        assert _dir_of(v["score"]) == d


def test_score_line_shows_both_when_global_top_is_other_direction():
    card = {
        "pick_dir": "home_win",
        "pick": "汉堡胜",
        "score_top2": [{"score": "1:1", "prob": 0.131}, {"score": "1:0", "prob": 0.114}],
        "score_by_dir": {
            "home_win": {"score": "1:0", "prob": 0.114},
            "draw": {"score": "1:1", "prob": 0.131},
            "away_win": {"score": "0:1", "prob": 0.053},
        },
    }
    line = score_line_for(card)
    assert "1:1" in line and "1:0" in line, "两个比分必须并列出现"
    assert "全场最高" in line and "汉堡胜" in line
    assert "1:1" in line.split("全场最高")[1] or "全场最高" in line


def test_score_line_single_when_direction_top_is_global_top():
    card = {
        "pick_dir": "away_win",
        "pick": "拜仁慕尼黑胜",
        "score_top2": [{"score": "0:3", "prob": 0.092}, {"score": "1:3", "prob": 0.080}],
        "score_by_dir": {
            "home_win": {"score": "1:0", "prob": 0.030},
            "draw": {"score": "1:1", "prob": 0.060},
            "away_win": {"score": "0:3", "prob": 0.092},
        },
    }
    line = score_line_for(card)
    assert line.count(":") == 1, f"同分时只显示一个比分，实际 {line}"
    assert "0:3" in line


def test_score_line_tolerates_missing_field():
    card = {"pick_dir": "home_win", "pick": "主胜",
            "score_top2": [{"score": "2:1", "prob": 0.10}]}
    assert "2:1" in score_line_for(card)


def test_live_card_has_score_by_dir_consistent_with_pick():
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    res = p.predict_round(9, stage="初版")
    cards = res.get("cards") or []
    if not cards:
        pytest.skip("免费层限流导致本轮无实时对阵")
    for c in cards:
        sbd = c.get("score_by_dir")
        assert sbd and set(sbd) == {"home_win", "draw", "away_win"}, "卡片应带方向内最可能比分"
        assert _dir_of(sbd[c["pick_dir"]]["score"]) == c["pick_dir"], "方向内比分必须落在推荐方向"
        top1 = c["score_top2"][0]
        assert sbd[_dir_of(top1["score"])]["prob"] >= top1["prob"] - 1e-9, "各方向最高值不应低于全场 Top1"
