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
        {"MAI": {"key_out": [{"name": "布卡特", "importance": "核心"}], "doubtful": []}},
        [{"player": "Kane", "team_code": "BAY", "status": "key_out", "importance": "轮换"},
         {"player": "Gnabry", "team_code": "BAY", "status": "doubtful", "importance": "替补"}])
    assert merged["BAY"]["key_out"] == [{"name": "Kane", "importance": "轮换"}]
    assert merged["BAY"]["doubtful"] == [{"name": "Gnabry", "importance": "替补"}]
    assert merged["MAI"]["key_out"] == [{"name": "布卡特", "importance": "核心"}]   # 原有保留
    assert _merge_injuries(None, []) is None


# ---------- P2 #8：球员重要性 → injury_shift 加权重算 ----------
def test_injury_shift_by_importance():
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    # 3 名缺阵：核心+轮换+替补 → 0.10 + 0.10*0.6 + 0.10*0.3 = 0.19
    match = {
        "match_id": "565851", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
        "home_code": "MAI", "away_code": "BAY",
        "kickoff": "2026-11-07T22:30:00+08:00", "last_updated": "2026-11-01T15:00:00+08:00",
        "standings": {}, "form_last3": {}, "confidence": "中",
        "injuries": {
            "home_key_out": [{"name": "布卡特", "importance": "核心"}],
            "away_key_out": [{"name": "Kane", "importance": "核心"},
                              {"name": "Musiala", "importance": "轮换"},
                              {"name": "Gnabry", "importance": "替补"}],
            "home_doubtful": [], "away_doubtful": []},
    }
    card = p.predict_match(match, 9, stage="确认版", realtime_injury_available=True)
    assert card["injury_shift"]["home"] == 0.10
    assert card["injury_shift"]["away"] == round(0.10 + 0.06 + 0.03, 4)   # 0.19
    # 全核心（无重要性标注，默认核心）→ 与旧口径一致（0.30 封顶前）
    match2 = dict(match, injuries={
        "home_key_out": ["A", "B", "C"], "away_key_out": [],
        "home_doubtful": [], "away_doubtful": []})
    card2 = p.predict_match(match2, 9, stage="确认版", realtime_injury_available=True)
    assert card2["injury_shift"]["home"] == 0.30   # 3*0.1=0.30


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


# ---------- P2 #7 / P6：疲劳系数标定（按小时口径，实测不显著 → 诚实 calibrated=False） ----------
def test_fatigue_calibration_honest():
    assert FATIGUE_RULES["min_rest_hours"] == 96                    # 按小时口径（覆盖周四欧联→周日）
    buckets = FATIGUE_RULES["hour_bucket_discounts"]
    assert buckets and all(0.0 <= b["discount"] <= FATIGUE_RULES["xG_discount_max"] for b in buckets)
    assert 0.0 < FATIGUE_RULES["xG_discount_max"] <= 0.10           # 封顶 4%
    assert FATIGUE_RULES.get("cl_rotation_discount", 0.0) > 0.0
    # 实测 z=1.03 不显著 → 必须如实打黄牌，不得置 True
    assert FATIGUE_RULES["calibrated"] is False
    assert FATIGUE_RULES.get("calibration_note")
    # 已删除的旧字段不得复活（否则旧口径会偷偷生效）
    assert "xG_discount_per_day" not in FATIGUE_RULES
    assert set(FATIGUE_RULES["euro_competitions"]) == {"CL", "EL", "UECL"}


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


# ---------- P2 #9：加时赛+120分钟极端疲劳惩罚 ----------
def test_fatigue_extra_time_extreme_penalty():
    # 拜仁 11-01 打满120分钟(欧冠加时 FINISHED, duration=EXTRA_TIME)，11-05 德甲(休息4天≤3? no→但加时窗口3天外)
    # 休息4天超出 extra_time_recovery_days(3) → 无额外惩罚；换 11-03(休息2天) → 触发额外惩罚
    cl = [{"match_id": "c1", "competition": "CL", "home_code": "BAY", "away_code": "X",
           "kickoff": "2026-11-01T20:00:00Z", "status": "FINISHED",
           "score": {"duration": "EXTRA_TIME"}, "home_team": "拜仁", "away_team": "x"}]
    bl1 = [{"match_id": "b1", "competition": "BL1", "home_code": "BAY", "away_code": "Y",
            "kickoff": "2026-11-03T14:30:00Z", "status": "TIMED", "home_team": "拜仁", "away_team": "y"}]
    fe = FatigueEngine(bl1, cl)
    amap = fe.analyze(bl1, [])
    h = amap["b1"]["home"]
    assert h["prev_extra_time"] is True
    # 极端惩罚：额外折扣≥extra_time_discount（且超过常规 xG_discount_max 封顶部分由加时赛解锁）
    assert h["fatigue_discount"] >= FATIGUE_RULES["extra_time_discount"]
    assert h["fatigue_discount"] > FATIGUE_RULES["xG_discount_max"]
    # 休息3天+加时 → 恢复窗口边界(3天内含) → 仍触发
    cl2 = [dict(cl[0], kickoff="2026-11-02T20:00:00Z")]
    bl12 = [dict(bl1[0], kickoff="2026-11-05T14:30:00Z")]   # 休息3天
    fe2 = FatigueEngine(bl12, cl2)
    assert fe2.analyze(bl12, [])["b1"]["home"]["fatigue_discount"] >= FATIGUE_RULES["extra_time_discount"]
    # 无加时(常规赛) → 不触发额外惩罚
    cl3 = [dict(cl[0], score={"duration": "REGULAR"})]
    fe3 = FatigueEngine(bl1, cl3)
    assert fe3.analyze(bl1, [])["b1"]["home"]["prev_extra_time"] is False


# ---------- P4：预测解释（Explainable AI）构建 ----------
def test_build_explanation():
    from app import _build_explanation
    card = {
        "pick": "拜仁慕尼黑胜", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
        "probabilities": {"home_win": 0.11, "draw": 0.17, "away_win": 0.72},
        "score_top2": [{"score": "1:3", "prob": 0.077}, {"score": "1:4", "prob": 0.066}],
        "injuries": {"home_key_out": [{"name": "布卡特", "importance": "核心"}], "away_key_out": [],
                     "home_doubtful": [], "away_doubtful": []},
        "injury_shift": {"home": 0.10, "away": 0.0},
        "fatigue": {"home_prev_extra_time": True, "away_prev_extra_time": False,
                    "home_rest_days": 3, "away_rest_days": 6,
                    "cl_preceding_home": True, "cl_preceding_away": False},
    }
    txt = _build_explanation(card, "BL1")
    assert "拜仁慕尼黑胜" in txt and "客队占优明显" in txt
    assert "美因茨" in txt and "缺阵" in txt and "伤病偏移" in txt
    assert "加时" in txt and "欧冠前置" in txt and "最可能比分 1:3" in txt
    # 基准卡兜底
    base = {"pick": "基准"}
    assert "基准概率" in _build_explanation(base, "BL1")


# ---------- P4·优先级二①：运气回归（触底反弹）特征 ----------
def test_luck_map_shape():
    from data.historical_data import luck_map
    m = luck_map()
    assert "BAY" in m and "MAI" in m and "HSV" in m
    assert all(set(v) == {"roll_hg", "roll_hst", "gap"} for v in m.values())
    # 2526 赛季已开赛 → 有真实近5场数据
    assert m["BAY"]["roll_hst"] > 0


def test_luck_rebound_detected_and_explained():
    from pipeline import PredictionPipeline
    from app import _build_explanation
    p = PredictionPipeline()
    match = {
        "match_id": "565851", "home_team": "美因茨", "away_team": "拜仁慕尼黑",
        "home_code": "MAI", "away_code": "BAY",
        "kickoff": "2026-11-07T22:30:00+08:00", "last_updated": "2026-11-01T15:00:00+08:00",
        "standings": {}, "form_last3": {}, "confidence": "中",
        "injuries": {"home_key_out": [], "away_key_out": [], "home_doubtful": [], "away_doubtful": []},
    }
    # MAI 近5场射正均值高(机会强)但进球少 → 触底反弹；BAY 正常（需至少3队算相对基线）
    lm = {"MAI": {"roll_hg": 0.8, "roll_hst": 4.2, "gap": 3.4},
          "BAY": {"roll_hg": 2.2, "roll_hst": 2.5, "gap": 0.3},
          "DOR": {"roll_hg": 2.0, "roll_hst": 3.0, "gap": 1.0}}
    card = p.predict_match(match, 9, stage="初版", luck_map=lm)
    assert card["luck"]["home_rebound"] is True
    assert card["luck"]["away_rebound"] is False
    assert card["luck"]["home_gap"] == 3.4
    txt = _build_explanation(card, "BL1")
    assert "触底反弹" in txt and "📈" in txt


# ---------- P4·优先级二②：战意规则（欧冠前3天联赛 + 对手弱旅 → 硬扣0.15xG） ----------
def test_motivation_penalty_when_cl_preceding_and_weak_opponent():
    from data.cross_competition import FatigueEngine
    from config import MOTIVATION_RULES
    fe = FatigueEngine([], [])
    bl1 = [{"match_id": "m1", "competition": "BL1", "home_team": "A", "away_team": "B",
            "home_code": "AAA", "away_code": "BBB", "kickoff": "2026-10-01T18:00:00Z",
            "status": "TIMED", "score": {},
            "standings": {"home": {"rank": 3, "pts": 20}, "away": {"rank": 10, "pts": 8}}}]
    cl = [{"match_id": "c1", "competition": "CL", "home_team": "A", "away_team": "C",
           "home_code": "AAA", "away_code": "CCC", "kickoff": "2026-10-03T19:00:00Z",
           "status": "TIMED", "score": {}}]
    amap = fe.analyze(bl1, cl)
    h = amap["m1"]["home"]
    assert h["cl_motivation_penalty"] == MOTIVATION_RULES["xg_deduction"]  # 对手排名低7名
    assert amap["m1"]["away"].get("cl_motivation_penalty", 0.0) == 0.0


def test_motivation_penalty_not_when_rank_close():
    from data.cross_competition import FatigueEngine
    fe = FatigueEngine([], [])
    bl1 = [{"match_id": "m1", "competition": "BL1", "home_team": "A", "away_team": "B",
            "home_code": "AAA", "away_code": "BBB", "kickoff": "2026-10-01T18:00:00Z",
            "status": "TIMED", "score": {},
            "standings": {"home": {"rank": 3, "pts": 20}, "away": {"rank": 5, "pts": 14}}}]
    cl = [{"match_id": "c1", "competition": "CL", "home_team": "A", "away_team": "C",
           "home_code": "AAA", "away_code": "CCC", "kickoff": "2026-10-03T19:00:00Z",
           "status": "TIMED", "score": {}}]
    amap = fe.analyze(bl1, cl)
    h = amap["m1"]["home"]
    assert h.get("cl_motivation_penalty", 0.0) == 0.0   # 对手只低2名 → 不触发
    assert h["cl_preceding"] is True                     # 仍处轮换窗口（7天内）
    # 超出3天窗口 → 也不触发战意（只保留轮换标记）
    cl2 = [dict(cl[0], kickoff="2026-10-05T19:00:00Z")]  # 差4天
    bl1b = [dict(bl1[0], standings={"home": {"rank": 3, "pts": 20}, "away": {"rank": 10, "pts": 8}})]
    amap2 = fe.analyze(bl1b, cl2)
    assert amap2["m1"]["home"].get("cl_motivation_penalty", 0.0) == 0.0
    assert amap2["m1"]["home"]["cl_preceding"] is True


# ---------- P4·优先级二③：历史回测看板静态报告 ----------
def test_backtest_report_file():
    import json, os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "backtest_report.json")
    assert os.path.exists(p), "请先运行 python3 scripts/backtest_report.py"
    rep = json.load(open(p, encoding="utf-8"))
    assert rep["games"] == 918
    assert 0.4 < rep["direction_accuracy"] < 0.7
    assert rep["r4"]["threshold"] is not None
    assert rep["r1"]["baseline_wrong_rate"] > 0
