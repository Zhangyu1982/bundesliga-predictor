"""
P2 阶段 · 验收测试：真实 token 数据链路 + 动态限流 + 队名归一化 + 确认版伤病复算
运行：python3 -m pytest tests/ -v
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.api_client import fdo_team_to_code, team_zh
from data_stores.cache import Cache
from config import uncalibrated_thresholds
from data.injury_fetch import espn_team_to_code, _is_injury_context, fetch_espn_injuries
from data.cross_competition import CrossElo, FatigueEngine
from config import COMPETITIONS, ELO_K_MULT, FATIGUE_RULES
from app import _merge_injuries


# ---------- P2 #1：动态剩余额度识别（防 403） ----------
def test_cache_dynamic_quota_from_headers():
    c = Cache(":memory:")
    c.update_quota_from_headers("fdo:r9", {
        "x-requests-available-minute": "0", "X-RequestCounter-Reset": "60"})
    rem, reset = c.dynamic_quota("fdo:r9")
    assert rem == 0 and reset is not None
    # 窗口未重置且剩余 0 → 阻止调用（防 403）
    assert c.can_call_dynamic("fdo:r9", 100) is False
    # 窗口已重置 → 放行
    c.conn.execute("UPDATE quota_state SET reset_at=? WHERE resource=?",
                   (time.time() - 10, "fdo:r9"))
    c.conn.commit()
    assert c.can_call_dynamic("fdo:r9", 100) is True
    # 每日配额兜底
    assert c.can_call_dynamic("fdo:r9", 0) is False


def test_cache_dynamic_quota_no_header_allows():
    c = Cache(":memory:")
    assert c.can_call_dynamic("fdo:new", 100) is True   # 无头信息由每日配额兜底


# ---------- P2 #2：队名归一化映射层（真实 API 名 → code/中文） ----------
def test_fdo_team_normalization():
    assert fdo_team_to_code("1. FSV Mainz 05") == "MAI"
    assert fdo_team_to_code("FC Bayern München") == "BAY"
    assert fdo_team_to_code("Borussia Mönchengladbach") == "BMG"
    assert fdo_team_to_code("Hamburger SV") == "HSV"
    assert team_zh("BAY", "FC Bayern München") == "拜仁慕尼黑"
    assert team_zh("ELV", "SV 07 Elversberg") == "埃尔弗斯贝格"   # 升班马中文名


# ---------- P2 #3：确认版伤病复算（injury_shift 折入 DC） ----------
def test_confirm_stage_injury_recalc():
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    match = {
        "match_id": "565851", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
        "home_code": "MAI", "away_code": "BAY",
        "kickoff": "2026-11-07T22:30:00+08:00",
        "last_updated": "2026-11-01T15:00:00+08:00",
        "standings": {}, "form_last3": {}, "confidence": "中",
        "injuries": {"home_key_out": ["X"], "away_key_out": ["Y", "Z"],
                     "home_doubtful": [], "away_doubtful": ["W"]},
    }
    card = p.predict_match(match, 9, stage="确认版", realtime_injury_available=True)
    assert card["stage"] == "确认版"
    assert card["confirm_probs"] is not None
    assert card["injury_shift"]["home"] == 0.10            # 1 缺阵
    assert card["injury_shift"]["away"] == 0.25            # 2缺阵*0.1 + 1存疑*0.05
    assert card["confirm_probs"]["away_win"] < card["base_probs"]["away_win"]  # 复算下调拜仁
    assert "复算" in card["risk_note"]


def test_confirm_blocked_without_injury_source():
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    match = {
        "match_id": "565851", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
        "home_code": "MAI", "away_code": "BAY",
        "kickoff": "2026-11-07T22:30:00+08:00", "last_updated": "2026-11-01T15:00:00+08:00",
        "standings": {}, "form_last3": {}, "confidence": "中",
        "injuries": {"home_key_out": [], "away_key_out": [], "home_doubtful": [], "away_doubtful": []},
    }
    card = p.predict_match(match, 9, stage="确认版", realtime_injury_available=False)
    assert card["stage"] == "初版" and card["confirm_blocked"] is True
    assert card["confirm_probs"] is None


# ---------- P2 #4：未标定阈值透传（R2/R3/injury_shift 保留；R1/R4 已标定） ----------
def test_uncalibrated_kept_honest():
    un = uncalibrated_thresholds()
    assert "R2_injury_upgrade" in un and "R3_national_window" in un and "injury_shift" in un
    assert "R1_bias_record" not in un and "R4_divergence_flag" not in un


# ---------- P2 #5：ESPN 自动抓取（方案三，国内直连） ----------
def test_espn_team_mapping():
    assert espn_team_to_code("Bayern Munich") == "BAY"
    assert espn_team_to_code("Mainz") == "MAI"
    assert espn_team_to_code("Borussia Mönchengladbach") == "BMG"
    assert espn_team_to_code("", "M05") == "MAI"   # abbreviation 兜底


def test_injury_context_negative_filter():
    assert _is_injury_context("Harry Kane misses penalty as Bayern", 20) is False
    assert _is_injury_context("Bayern forward is injured and out for three weeks", 40) is True


def test_espn_injuries_structured():
    res = fetch_espn_injuries()   # 当前空 → 结构可用、不抛异常
    assert isinstance(res, list)


def test_merge_injuries_from_candidates():
    merged = _merge_injuries(
        {"MAI": {"key_out": ["布卡特"], "doubtful": []}},
        [{"player": "Kane", "team_code": "BAY", "status": "key_out"},
         {"player": "Gnabry", "team_code": "BAY", "status": "doubtful"}])
    assert merged["BAY"]["key_out"] == ["Kane"]
    assert merged["BAY"]["doubtful"] == ["Gnabry"]
    assert merged["MAI"]["key_out"] == ["布卡特"]   # 原有保留
    assert _merge_injuries(None, []) is None


# ---------- P2 #6：跨赛事分析 ----------
def test_cross_competition_availability():
    assert COMPETITIONS["BL1"]["available"] is True
    assert COMPETITIONS["CL"]["available"] is True
    assert COMPETITIONS["EL"]["available"] is False
    assert COMPETITIONS["DFB"]["available"] is False
    assert COMPETITIONS["UECL"]["available"] is False


def test_cross_elo_rating_priority():
    ce = CrossElo({"BAY": 1900.0})
    assert ce.rating_for("BAY", "拜仁慕尼黑") == 1900.0          # 模型 Elo 优先
    assert ce.rating_for("X", "Real Madrid") == 1780.0           # 精选豪门
    assert ce.rating_for("X", "Manchester City FC") == 1750.0    # 精选豪门(子串)
    assert ce.rating_for("X", "Some Unknown Club") == 1500.0     # 联赛平均兜底
    assert ce.k_multiplier("CL") == 1.25
    assert ce.k_multiplier("BL1") == 1.0


def test_fatigue_engine_rest_days():
    # 拜仁 11-01 有场欧冠(FINISHED)，11-07 德甲 → rest_days=6
    cl = [{"match_id": "c1", "competition": "CL", "home_code": "BAY", "away_code": "X",
           "kickoff": "2026-11-01T20:00:00Z", "status": "FINISHED", "home_team": "拜仁", "away_team": "x"}]
    bl1 = [{"match_id": "b1", "competition": "BL1", "home_code": "BAY", "away_code": "Y",
            "kickoff": "2026-11-07T14:30:00Z", "status": "FINISHED", "home_team": "拜仁", "away_team": "y"}]
    fe = FatigueEngine(bl1, cl)
    from datetime import datetime
    d = datetime.fromisoformat("2026-11-07T14:30:00+00:00")
    assert fe.rest_days("BAY", d, exclude_id="b1") == 5


# ---------- P2 #7：疲劳系数标定（calibrated=True，轮换折扣折入） ----------
def test_fatigue_calibrated_honest():
    assert FATIGUE_RULES["calibrated"] is True
    assert 0.0 < FATIGUE_RULES["xG_discount_per_day"] <= 0.05      # 实测 1.4%
    assert 0.0 < FATIGUE_RULES["xG_discount_max"] <= 0.10          # 实测 4%
    assert FATIGUE_RULES.get("cl_rotation_discount", 0.0) > 0.0    # 轮换折扣已标定


def test_fatigue_cl_preceding_applies_rotation_discount():
    # 未来 7 天该队有欧冠 → 折入 cl_rotation_discount
    cl_fut = [{"match_id": "c1", "competition": "CL", "home_code": "BAY", "away_code": "X",
               "kickoff": "2026-11-04T20:00:00Z", "status": "TIMED", "home_team": "拜仁", "away_team": "x"}]
    bl1 = [{"match_id": "b1", "competition": "BL1", "home_code": "BAY", "away_code": "Y",
            "kickoff": "2026-10-31T14:30:00Z", "status": "FINISHED", "home_team": "拜仁", "away_team": "y"}]
    fe = FatigueEngine(bl1, [])
    amap = fe.analyze(bl1, cl_fut)
    assert amap["b1"]["home"]["cl_preceding"] is True
    assert amap["b1"]["home"]["fatigue_discount"] >= FATIGUE_RULES["cl_rotation_discount"]
