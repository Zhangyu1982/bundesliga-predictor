"""复盘表：shift(主/客) 与 λ 合计并列，只看分布不下结论。
运行：python -m pytest tests/test_review_table.py -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from scripts.review_table import build_rows, summarize, format_table


class _StubBundle:
    """替身：只为验证表格逻辑，不碰真实模型。"""
    dc_attack = {"A": 1.0, "B": 1.0}

    def dc_lambda(self, home, away):
        return 1.6, 1.2


REC_BOTH = {
    "round_no": 10, "home": "A", "away": "B",
    "actual_hg": 3, "actual_ag": 1, "actual_ftr": "H",
    "pred_pick": "home_win", "pred_hit": 1,
    "shift_home": 0.12, "shift_away": 0.0,
}
REC_NO_SHIFT_FIELD = {
    "round_no": 10, "home": "B", "away": "A",
    "actual_hg": 1, "actual_ag": 1, "actual_ftr": "D",
    "pred_pick": "draw", "pred_hit": 0,
}


def test_build_rows_carries_lambda_and_shift_columns():
    rows = build_rows([REC_BOTH, REC_NO_SHIFT_FIELD], _StubBundle())
    assert len(rows) == 2
    r = rows[0]
    for k in ("home", "away", "actual", "lam_home", "lam_away", "lam_total",
              "shift_home", "shift_away", "pick", "hit"):
        assert k in r, f"复盘表缺列 {k}"
    assert abs(r["lam_total"] - (r["lam_home"] + r["lam_away"])) < 0.02
    assert r["shift_home"] == 0.12 and r["shift_away"] == 0.0


def test_missing_shift_field_is_rendered_as_dash():
    rows = build_rows([REC_NO_SHIFT_FIELD], _StubBundle())
    table = format_table(rows)
    assert "—" in table, "没有 shift 字段的场次（改造前入库的）必须显示为 —，不能编造 0"


def test_summary_uses_lambda_total_vs_actual_total():
    rows = build_rows([REC_BOTH, REC_NO_SHIFT_FIELD], _StubBundle())
    s = summarize(rows)
    # λ 合计 = (1.6+1.2) * 2 = 5.6；实际 = 4+2 = 6
    assert abs(s["lam_total"] - 5.6) < 0.1
    assert s["actual_total"] == 6
    assert abs(s["diff"] - (s["lam_total"] - s["actual_total"])) < 1e-9
    # z 用泊松方差（sd = sqrt(实际总进球)）；保留两位小数，故允许 0.01 误差
    assert abs(s["z"] - s["diff"] / (s["actual_total"] ** 0.5)) < 0.01


def test_format_table_has_both_groups_side_by_side():
    rows = build_rows([REC_BOTH], _StubBundle())
    table = format_table(rows)
    for col in ("场次", "实际", "λ主", "λ客", "λ合计", "shift主", "shift客", "推荐", "命中"):
        assert col in table, f"表头缺 {col}"
    assert summarize(rows)["lam_total"] >= 0
