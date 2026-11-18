"""统计层切分：含临时参数/衰减参数的场次必须能从业绩里单独切出来看。

问题：升班马用德乙换算参数、降级回归用衰减参数，这些场的命中率与"纯稳定参数"场不同口径，
混在一个命中率里对外报数，等于用不可比样本互相稀释。
"""
from data.sample_split import classify_record, split_stats


PROV = {"Elversberg", "Paderborn", "Schalke 04"}
CARRY = {"Hamburg"}


def _rec(home, away, hit, rnd=10, season="2627"):
    return {"season": season, "round_no": rnd, "home": home, "away": away, "pred_hit": hit}


def test_classify_record():
    assert classify_record(_rec("Bayern", "Mainz", 1), PROV, CARRY) == "stable"
    assert classify_record(_rec("Koln", "Elversberg", 0), PROV, CARRY) == "provisional"
    assert classify_record(_rec("Hamburg", "Bayern", 1), PROV, CARRY) == "carryover"
    # 双方一个是临时一个是衰减 → 归入最需要标注的口径（临时优先）
    assert classify_record(_rec("Elversberg", "Hamburg", 1), PROV, CARRY) == "provisional"


def test_split_stats_counts_and_rate():
    rows = [
        _rec("Bayern", "Mainz", 1),
        _rec("Dortmund", "Leipzig", 0),
        _rec("Koln", "Elversberg", 0),
        _rec("Hamburg", "Bayern", 1),
    ]
    res = split_stats(rows, PROV, CARRY)
    assert res["all"]["n"] == 4 and res["all"]["hits"] == 2
    assert res["stable"]["n"] == 2 and res["stable"]["hits"] == 1
    assert res["provisional"]["n"] == 1 and res["provisional"]["hits"] == 0
    assert res["carryover"]["n"] == 1 and res["carryover"]["hits"] == 1
    assert round(res["all"]["hit_rate"], 3) == 0.5


def test_small_sample_is_flagged_not_concluded():
    res = split_stats([_rec("Bayern", "Mainz", 1)], PROV, CARRY, min_n=5)
    assert res["stable"]["insufficient"] is True
    assert res["all"]["insufficient"] is True


def test_empty_input():
    res = split_stats([], PROV, CARRY)
    for k in ("all", "stable", "provisional", "carryover"):
        assert res[k]["n"] == 0 and res[k]["hit_rate"] == 0.0
