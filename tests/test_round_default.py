"""轮次选择器默认值改造验收：第一选项自动跟到最新轮次。
运行：python -m pytest tests/test_round_default.py -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import current_round_from_matches, latest_round, round_options


def _m(md, status, kickoff="2026-11-01T14:30:00Z"):
    return {"matchday": md, "status": status, "kickoff": kickoff}


def test_current_round_is_earliest_unfinished():
    fixtures = [
        _m(8, "FINISHED"), _m(8, "FINISHED"),
        _m(9, "FINISHED"), _m(9, "SCHEDULED"),
        _m(10, "TIMED"), _m(10, "TIMED"),
    ]
    assert current_round_from_matches(fixtures) == 9


def test_all_finished_returns_next_round():
    fixtures = [_m(9, "FINISHED"), _m(9, "FINISHED"), _m(10, "FINISHED")]
    # 全部打完 → 指向下一轮（10 是最大轮次，则给 11）
    assert current_round_from_matches(fixtures) == 11


def test_ignores_rows_without_matchday():
    fixtures = [{"status": "FINISHED"}, _m(11, "IN_PLAY")]
    assert current_round_from_matches(fixtures) == 11


def test_no_matchday_returns_none():
    assert current_round_from_matches([]) is None
    assert current_round_from_matches([{"status": "FINISHED"}]) is None


def test_latest_round_falls_back_to_recorded_plus_one():
    # 网络/赛程拿不到时，用已录入赛果的最大轮次 +1（录入的都是已打完的轮次）
    assert latest_round([], [8, 9, 9]) == 10
    # 赛程优先于本地记录
    assert latest_round([_m(9, "FINISHED"), _m(10, "TIMED")], [9]) == 10
    # 两者都没有 → None（由调用方兜底）
    assert latest_round([], []) is None


def test_round_options_puts_latest_first_and_covers_all():
    rounds = [9, 7, 3]
    opts = round_options(10)
    assert opts[0] == 10, "第一选项必须是最新轮次"
    assert opts[1] == 9 and opts[2] == 8, "其后按轮次倒序，方便回看历史轮"
    assert all(r in opts for r in rounds)
    assert len(set(opts)) == len(opts), "不应有重复轮次"
    assert max(opts) <= 34 and min(opts) >= 1


def test_round_options_default_when_latest_unknown():
    opts = round_options(None)
    assert opts[0] >= 1 and max(opts) <= 34 and len(set(opts)) == len(opts)
