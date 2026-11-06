"""P5.1 · 转会窗容错回归测试。

覆盖用户三条要求：
1. 大名单校验：已离队的基线球员不触发扣分；名单抓不到时**绝不能**把全队当离队清空。
2. 洗牌检测：基线 XI 离队比例超阈值 → 判定 stale / need_rebuild。
3. 新援识别：本场首发但基线窗口内从未首发 → newcomers 提示，且不额外加减分。
外加：_id_key 的姓名兜底。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import LINEUP_SHIFT
from data.baseline import _id_key, prune_by_roster, roster_drift
from lineup_shift import describe_newcomers, describe_shift, starter_deviation


def _entry(xi, seen=None, available=True):
    return {"available": available, "n_matches": 8,
            "xi": [dict(p) for p in xi],
            "core": [],
            "seen": [dict(p) for p in (seen if seen is not None else xi)]}


def _lineup(starters, bench=(), formation="4-2-3-1"):
    return {"starters": [dict(p) for p in starters],
            "bench": [dict(p) for p in bench], "formation": formation}


XI = [{"name": f"P{i}", "id": str(1000 + i), "rate": 1.0, "pos": "M"} for i in range(1, 12)]


# ---------------- 1. 大名单校验 ----------------

def test_departed_player_not_penalised():
    """凯恩式场景：人已转会，仍留在基线里 → 不能算缺阵扣分。"""
    ros = {"ids": [str(1000 + i) for i in range(2, 12)]}       # 缺 P1
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(2, 12)] + \
               [{"name": "New", "id": "9999"}]
    dev = starter_deviation(_entry(XI), _lineup(starters), roster=ros)
    assert dev["available"] is True
    assert dev["shift"] == 0.0                     # P1 已离队，不该扣
    assert dev["absent"] == []
    assert [d["name"] for d in dev["departed"]] == ["P1"]
    assert dev["roster_check"] == "ok"


def test_roster_unavailable_never_wipes_baseline():
    """名单抓不到时原样保留——空名单≠全队走光。"""
    xi = [dict(p) for p in XI]
    kept, core, departed, status = prune_by_roster(xi, [], {"ids": []}, LINEUP_SHIFT)
    assert len(kept) == 11 and departed == [] and status == "roster_unavailable"
    kept2, _, dep2, st2 = prune_by_roster(xi, [], {}, LINEUP_SHIFT)
    assert len(kept2) == 11 and dep2 == [] and st2 == "roster_unavailable"
    # 计算层同样不能把空名单当全队离队
    dev = starter_deviation(_entry(XI), _lineup(XI), roster={"ids": []})
    assert dev["roster_check"] == "roster_unavailable"
    assert dev["shift"] == 0.0 and dev["departed"] == []


def test_roster_check_can_be_disabled():
    kept, _, dep, status = prune_by_roster([dict(p) for p in XI], [],
                                          {"ids": ["1"]},
                                          {**LINEUP_SHIFT, "roster_check": False})
    assert status == "disabled" and len(kept) == 11 and dep == []


def test_departed_isolated_when_others_absent():
    """离队的人不扣，真正缺阵的人要照样扣——两者不能混。"""
    ros = {"ids": [str(1000 + i) for i in range(2, 12)]}       # P1 离队
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(3, 12)] + \
               [{"name": "New", "id": "9999"}]
    dev = starter_deviation(_entry(XI), _lineup(starters), roster=ros)
    # 只剩 P2 缺席（P1 已离队被剔除）
    assert [a["name"] for a in dev["absent"]] == ["P2"]
    assert abs(dev["shift"] - 0.10) < 1e-6


# ---------------- 2. 洗牌检测 ----------------

def test_roster_drift_flags_shakeup():
    ros = {"ids": [str(1000 + i) for i in range(5, 12)]}       # 11 人里走了 4 个（36%）
    d = roster_drift({"xi": XI}, ros)
    assert d["available"] is True
    assert d["departed_ratio"] > LINEUP_SHIFT["drift_warn_ratio"]
    assert d["drift"] is True
    assert len(d["departed"]) == 4


def test_roster_drift_stable_roster():
    ros = {"ids": [str(1000 + i) for i in range(1, 12)]}
    d = roster_drift({"xi": XI}, ros)
    assert d["drift"] is False and d["departed_ratio"] == 0.0


def test_roster_drift_needs_both_sides():
    assert roster_drift({"xi": XI}, {"ids": []})["available"] is False
    assert roster_drift({}, {"ids": ["1"]})["available"] is False


# ---------------- 3. 新援识别 ----------------

def test_newcomer_detected_from_never_started():
    """本场首发、但基线 8 场里从未首发 → 新援/奇兵提示，且不额外加分。"""
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(2, 12)] + \
               [{"name": "Wunderkind", "id": "8888", "pos": "F"}]
    dev = starter_deviation(_entry(XI, seen=XI), _lineup(starters),
                            roster={"ids": [str(1000 + i) for i in range(2, 12)] + ["8888"]})
    assert [x["name"] for x in dev["newcomers"]] == ["Wunderkind"]
    assert dev["newcomers"][0]["pos"] == "F"
    # 新援不参与扣分：P1 离队剔除后，其余 10 人都在首发 → 0
    assert dev["shift"] == 0.0


def test_rotation_player_is_not_newcomer():
    """轮换老队员（窗口内曾首发过）不算新援，避免误报。"""
    seen = XI + [{"name": "Rot", "id": "7777", "rate": 0.25, "pos": "M"}]
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(1, 11)] + \
               [{"name": "Rot", "id": "7777"}]
    dev = starter_deviation(_entry(XI, seen=seen), _lineup(starters))
    assert dev["newcomers"] == []
    assert [u["name"] for u in dev["unknown_starters"]] == ["Rot"]


def test_newcomer_hint_line_text():
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(1, 11)] + \
               [{"name": "Fresh", "id": "5555", "pos": "D"}]
    dev = starter_deviation(_entry(XI, seen=XI), _lineup(starters))
    txt = describe_newcomers("汉堡", dev)
    assert txt and "新面孔首发" in txt and "Fresh" in txt
    assert "不额外加分" in txt
    assert describe_newcomers("汉堡", starter_deviation(_entry(XI), _lineup(XI))) is None


def test_legacy_baseline_without_seen_is_flagged_estimated():
    """旧版基线没有 seen 字段 → 保守标"疑似"，而不是直接当成确定新援。"""
    entry = {"available": True, "n_matches": 8, "xi": [dict(p) for p in XI], "core": []}
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(1, 11)] + \
               [{"name": "Fresh", "id": "5555"}]
    dev = starter_deviation(entry, _lineup(starters))
    assert dev["newcomers"] and dev["newcomers"][0].get("estimated") is True
    assert "疑似" in describe_newcomers("汉堡", dev)


# ---------------- 4. 身份键与文案 ----------------

def test_id_key_prefers_id_and_falls_back_to_name():
    assert _id_key({"id": "84774", "name": "Manuel Neuer"}) == "84774"
    assert _id_key({"id": "", "name": "Manuel Neuer"}) == "name::Manuel Neuer"
    assert _id_key({"name": "X"}) != _id_key({"name": "Y"})


def test_describe_mentions_departed_and_unchecked():
    ros = {"ids": [str(1000 + i) for i in range(2, 12)]}
    starters = [{"name": f"P{i}", "id": str(1000 + i)} for i in range(2, 12)] + \
               [{"name": "New", "id": "9999"}]
    dev = starter_deviation(_entry(XI), _lineup(starters), roster=ros)
    txt = describe_shift("拜仁", dev, base_lambda=1.5)
    assert "离队" in txt and "P1" in txt
