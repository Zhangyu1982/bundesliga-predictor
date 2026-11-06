"""P5 回归测试：赛前首发反推（终极刷新）。

1. 常规主力全部首发 → 零扣减
2. 常规主力在替补席 → 半额（bench_factor=0.5）
3. 常规主力不在名单 → 全额，且按历史首发率加权
4. 单队累计扣减必须被 max_total_shift 截断
5. 无基线 / 无首发 → available=False，绝不当成全主力在阵（避免"没数据=满分"）
6. describe_shift 必须点明"替补席"
7. baseline._pick_xi 门将位优先，凑满 11 人
8. baseline._aggregate 必须逐场判断主客侧（同一支队既有主场又有客场）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import LINEUP_SHIFT
from lineup_shift import describe_shift, starter_deviation, xg_equivalent
from data.baseline import _aggregate, _pick_xi, _side_of


def _baseline(names, rate=1.0):
    return {"available": True, "n_matches": 8,
            "xi": [{"name": n, "rate": rate, "pos": ""} for n in names],
            "core": []}


def _lineup(starters, bench):
    return {"starters": [{"name": n} for n in starters],
            "bench": [{"name": n} for n in bench],
            "formation": "4-2-3-1"}


XI11 = [f"P{i}" for i in range(1, 12)]


def test_full_strength_no_shift():
    dev = starter_deviation(_baseline(XI11), _lineup(XI11, ["S1", "S2"]))
    assert dev["available"] is True
    assert dev["shift"] == 0.0
    assert dev["matched"] == 11
    assert dev["absent"] == [] and dev["benched"] == []


def test_bench_half_penalty():
    # P1 在替补席：0.10 * rate(1.0) * 0.5 = 0.05
    starters = [p for p in XI11 if p != "P1"] + ["X1"]
    dev = starter_deviation(_baseline(XI11), _lineup(starters, ["P1"]))
    assert abs(dev["shift"] - 0.05) < 1e-6
    assert len(dev["benched"]) == 1 and dev["benched"][0]["name"] == "P1"
    assert dev["absent"] == []


def test_absent_full_penalty_weighted_by_rate():
    starters = [p for p in XI11 if p != "P1"] + ["X1"]
    dev = starter_deviation(_baseline(XI11, rate=0.5), _lineup(starters, []))
    # 0.10 * 0.5 = 0.05
    assert abs(dev["shift"] - 0.05) < 1e-6
    assert len(dev["absent"]) == 1
    assert dev["absent"][0]["name"] == "P1"


def test_shift_capped_by_max_total():
    # 6 人缺阵 * 0.10 = 0.60 > 上限 0.30
    starters = [p for p in XI11 if p not in ("P1", "P2", "P3", "P4", "P5", "P6")] + ["X1", "X2"]
    dev = starter_deviation(_baseline(XI11), _lineup(starters, []))
    assert dev["shift"] == LINEUP_SHIFT["max_total_shift"] == 0.30


def test_no_baseline_or_no_lineup_is_unavailable():
    assert starter_deviation(None, _lineup(XI11, []))["available"] is False
    assert starter_deviation(_baseline(XI11), None)["available"] is False
    # 名单为空（ESPN 赛前未公布）→ 也必须标不可用，不能默认满分
    assert starter_deviation(_baseline(XI11), {"starters": [], "bench": []})["available"] is False


def test_describe_mentions_bench_and_xg():
    starters = [p for p in XI11 if p != "P1"] + ["X1"]
    dev = starter_deviation(_baseline(XI11), _lineup(starters, ["P1"]))
    txt = describe_shift("汉堡", dev, base_lambda=1.5)
    assert "汉堡" in txt and "替补席" in txt
    assert xg_equivalent(dev["shift"], 1.5) > 0


def test_pick_xi_prefers_goalkeeper():
    players = {f"F{i}": {"name": f"F{i}", "pos": "CD", "rate": 1.0, "starts": 8}
               for i in range(13)}
    players["GK"] = {"name": "GK", "pos": "G", "rate": 0.6, "starts": 5}
    xi, rows = _pick_xi(players)
    assert len(xi) == 11
    assert xi[0]["name"] == "GK"          # 门将必须进且排第一
    assert "GK" in [r["name"] for r in rows]


def test_aggregate_handles_home_and_away():
    code = "HSV"
    lu_home = {"home": {"code": code, "starters": [{"name": "A"}, {"name": "B"}] + [{"name": f"H{i}"} for i in range(9)]},
               "away": {"code": "BMG", "starters": [{"name": "Z"}] * 11}}
    lu_away = {"home": {"code": "BMG", "starters": [{"name": "Z"}] * 11},
               "away": {"code": code, "starters": [{"name": "A"}, {"name": "C"}] + [{"name": f"W{i}"} for i in range(9)]}}
    assert _side_of(lu_home, code) == "home"
    assert _side_of(lu_away, code) == "away"
    agg = _aggregate(code, [lu_home, lu_away])
    assert agg["n_matches"] == 2
    assert agg["players"]["A"]["starts"] == 2 and agg["players"]["A"]["rate"] == 1.0
    assert agg["players"]["B"]["starts"] == 1
    assert "Z" not in agg["players"]      # 对手球员绝不能混进来
