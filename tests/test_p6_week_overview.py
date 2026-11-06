"""P6 测试：本周德甲总览 + 按小时疲劳口径 + 欧联/欧会接入（全部离线，不联网）。

覆盖本轮改造的关键风险点：
  1. 旧口径用「天」判定（rest_days<3 才触发）→ 周四欧联→周日德甲恰好 3 天，永远不触发；
     新口径必须按小时识别出 ≈66h 的短间隔。
  2. 欧联(EL)/欧会(UECL)此前被免费层挡在门外（403/404），必须能被引擎识别为欧战消耗。
  3. 欧战对手是非德甲队（code=None）时，不能把德甲队这一侧整场丢掉。
  4. 折扣分桶与封顶必须与 config 一致，且不得用已删除的旧字段。
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FATIGUE_RULES  # noqa: E402
from data.cross_competition import FatigueEngine  # noqa: E402


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _bl1_lev_vs_koe():
    return [{
        "match_id": "b1", "competition": "BL1",
        "home_code": "KOE", "away_code": "LEV",
        "kickoff": "2026-11-08T14:30:00Z", "status": "TIMED",
        "standings": {"home": {"rank": 12}, "away": {"rank": 5}},
    }]


def test_thursday_europa_to_sunday_is_short_rest():
    """周四欧联 20:00 → 周日德甲 14:30 = 66.5h，新口径必须识别（旧口径按天=3 永不触发）。"""
    euro = [{
        "match_id": "ESPN_1", "competition": "EL",
        "home_code": "LEV", "away_code": None,
        "home_raw": "Bayer Leverkusen", "away_raw": "Marseille",
        "kickoff": "2026-11-05T20:00:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 2, "away": 1}}, "extra_time": False,
    }]
    fe = FatigueEngine(_bl1_lev_vs_koe(), euro)
    h = fe.hours_since_prev("LEV", _dt("2026-11-08T14:30:00Z"), exclude_id="b1")
    assert h is not None and abs(h - 66.5) < 0.05, h
    prev = fe._prev("LEV", _dt("2026-11-08T14:30:00Z"), exclude_id="b1")
    assert prev["competition"] == "EL"
    assert prev["is_euro"] is True
    # 旧口径下 66.5h // 24 == 2 天 < min_rest_days，但旧代码用 .days 得 3 不回退 → 这里只验证小时口径
    assert int(h // 24) == 2


def test_uecl_recognised_as_euro():
    """欧会(UECL)也必须算欧战消耗（弗赖堡周四客场特拉布宗）。"""
    euro = [{
        "match_id": "ESPN_2", "competition": "UECL",
        "home_code": None, "away_code": "SCF",
        "home_raw": "Trabzonspor", "away_raw": "SC Freiburg",
        "kickoff": "2026-11-05T17:45:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 1, "away": 1}}, "extra_time": False,
    }]
    fe = FatigueEngine([], euro)
    assert "SCF" in fe.history
    comp = fe.history["SCF"][0][1]
    assert comp == "UECL"
    assert fe.history["SCF"][0][5] is True          # is_euro 标志
    assert "UECL" in FATIGUE_RULES["euro_competitions"]


def test_non_bundesliga_opponent_keeps_bundesliga_side():
    """欧战对手非德甲队（code=None）时，德甲队一侧必须仍进时间轴（不整场丢弃）。"""
    euro = [{
        "match_id": "ESPN_3", "competition": "CL",
        "home_code": None, "away_code": "BAY",
        "home_raw": "Atlético Madrid", "away_raw": "Bayern Munich",
        "kickoff": "2026-11-03T20:00:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 2, "away": 1}}, "extra_time": False,
    }]
    fe = FatigueEngine([], euro)
    assert "BAY" in fe.history and len(fe.history["BAY"]) == 1
    assert fe.history["BAY"][0][2] is True          # was_away


def test_bucket_discount_table_matches_config():
    """分桶折扣必须与 config 一致，且 >=120h 不折扣；无纪元不折扣。"""
    assert FatigueEngine.bucket_discount(None) == (0.0, "")
    assert FatigueEngine.bucket_discount(60.0)[0] == 0.0            # <=72h：实测无拖累
    assert FatigueEngine.bucket_discount(80.0)[0] == 0.04           # 72-96h：触顶 4%
    assert FatigueEngine.bucket_discount(110.0)[0] == 0.02          # 96-120h
    assert FatigueEngine.bucket_discount(150.0)[0] == 0.0           # >=120h
    mx = FATIGUE_RULES["xG_discount_max"]
    for b in FATIGUE_RULES["hour_bucket_discounts"]:
        assert 0.0 <= b["discount"] <= mx


def test_away_euro_adds_intensity_bonus():
    """上一场欧战**客场** + 短间隔 → 在分桶折扣之上加 cl_away_intensity_bonus。"""
    euro = [{
        "match_id": "ESPN_4", "competition": "UECL",
        "home_code": None, "away_code": "SCF",   # 弗赖堡客场（特拉布宗主场）
        "home_raw": "Trabzonspor", "away_raw": "SC Freiburg",
        "kickoff": "2026-11-05T17:45:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 1, "away": 1}}, "extra_time": False,
    }]
    bl1 = [{
        "match_id": "b2", "competition": "BL1",
        "home_code": "SCF", "away_code": "FCU",
        "kickoff": "2026-11-08T16:30:00Z", "status": "TIMED",
    }]
    fe = FatigueEngine(bl1, euro)
    amap = fe.analyze(bl1, [])
    d = amap["b2"]["home"]["fatigue_discount"]
    assert d >= FATIGUE_RULES["cl_away_intensity_bonus"], d
    assert d <= FATIGUE_RULES["xG_discount_max"] + 1e-9


def test_fatigue_never_exceeds_cap():
    """常规情况折扣不得超过 xG_discount_max（加时另计，此处构造无加时）。"""
    euro = [{
        "match_id": "ESPN_5", "competition": "EL",
        "home_code": "X_EL", "away_code": "TSG",
        "home_raw": "Crystal Palace", "away_raw": "TSG Hoffenheim",
        "kickoff": "2026-11-05T20:00:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 2, "away": 0}}, "extra_time": False,
    }]
    bl1 = [{
        "match_id": "b3", "competition": "BL1",
        "home_code": "TSG", "away_code": "SCH",
        "kickoff": "2026-11-08T18:30:00Z", "status": "TIMED",
    }]
    fe = FatigueEngine(bl1, euro)
    amap = fe.analyze(bl1, [])
    f = amap["b3"]["home"]
    assert f["fatigue_discount"] <= FATIGUE_RULES["xG_discount_max"] + 1e-9
    assert f["prev_competition"] == "EL"
    assert f["prev_competition_zh"] == "欧联"


def test_cl_preceding_rotation_discount():
    """未来 7 天内有欧冠 → 折入 cl_rotation_discount（欧冠前置轮换）。"""
    cl_fut = [{"match_id": "c1", "competition": "CL", "home_code": "BAY",
               "away_code": "X", "kickoff": "2026-11-11T20:00:00Z", "status": "TIMED"}]
    bl1 = [{"match_id": "b4", "competition": "BL1", "home_code": "BAY",
            "away_code": "MAI", "kickoff": "2026-11-07T14:30:00Z", "status": "TIMED"}]
    fe = FatigueEngine(bl1, [])
    amap = fe.analyze(bl1, cl_fut)
    assert amap["b4"]["home"]["cl_preceding"] is True
    assert amap["b4"]["home"]["fatigue_discount"] >= FATIGUE_RULES["cl_rotation_discount"]


def test_rest_days_backward_compatible():
    """旧接口 rest_days 仍可用（= hours//24），不破坏既有调用。"""
    cl = [{"match_id": "c2", "competition": "CL", "home_code": "BAY", "away_code": "Y",
           "kickoff": "2026-11-02T20:00:00Z", "status": "FINISHED"}]
    bl1 = [{"match_id": "b5", "competition": "BL1", "home_code": "BAY", "away_code": "Z",
            "kickoff": "2026-11-07T14:30:00Z", "status": "TIMED"}]
    fe = FatigueEngine(bl1, cl)
    # 11-02 20:00Z → 11-07 14:30Z = 114.5h → 向下取整 4 天（旧接口语义）
    assert fe.rest_days("BAY", _dt("2026-11-07T14:30:00Z"), exclude_id="b5") == 4
