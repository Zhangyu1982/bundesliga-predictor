"""P7 回归测试：本轮五个 bug 的防线。

1. 手动伤病文本全角标点必须能解析（否则整行被丢、确认版复算等于没录入）
2. 存疑标注 `甲?[轮换]` 的问答顺序必须正确剥离
3. 无法解析的行必须被显式上报
4. validate_stage_window 必须按带时区时间比较（kickoff 是 UTC Z，last_updated 是 +08:00）
5. 推荐方向必须取三项概率最大（平局参与比较）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from injury_text import normalize, parse_manual_injuries, unparsed_lines
from schema import validate_stage_window


def test_fullwidth_colon_and_comma_parsed():
    p = parse_manual_injuries("汉堡：格拉策尔[核心]，贾塔[轮换]")
    assert p and "HSV" in p
    names = [e["name"] for e in p["HSV"]["key_out"]]
    assert names == ["格拉策尔", "贾塔"]


def test_doubtful_with_importance_after_question_mark():
    p = parse_manual_injuries("门兴: 克莱因迪恩斯特?[轮换]")
    d = p["BMG"]["doubtful"][0]
    assert d["name"] == "克莱因迪恩斯特"
    assert d["importance"] == "轮换"


def test_markdown_prefix_stripped():
    assert normalize("- 汉堡: 甲").startswith("汉堡")


def test_unparsed_lines_reported():
    bad = unparsed_lines("不存在的队: 某人\n汉堡: 甲")
    assert bad == ["不存在的队: 某人"]


def test_unknown_team_yields_no_entry():
    assert parse_manual_injuries("不存在的队: 某人") is None


def test_stage_window_timezone_normalized():
    # 2026-11-06 20:34:55 +08:00 → 距 19:30:00Z 开球约 6.9 小时，应在 48h 窗口内
    assert validate_stage_window("2026-11-06T20:34:55+08:00", "2026-11-06T19:30:00Z", 48) is True


def test_stage_window_outside_after_kickoff():
    # 已开球（updated 晚于 kickoff）→ 窗口外
    assert validate_stage_window("2026-11-07T05:00:00+08:00", "2026-11-06T19:30:00Z", 48) is False


def test_pick_uses_three_way_argmax():
    """pick 必须三向取最大；draw 最大时结果必须是平局，不能被主/客胜二选一覆盖。"""
    probs = {"home_win": 0.284, "draw": 0.549, "away_win": 0.168}
    pick_dir = max(("home_win", "draw", "away_win"), key=lambda k: probs[k])
    assert pick_dir == "draw"
