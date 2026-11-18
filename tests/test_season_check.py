"""三方对账：赛程 / 本季名单 / 参数库。"""
from scripts.season_check import reconcile

ROSTER = {"BAY", "KOE", "FCU", "RBL", "LEV", "PAD", "BMG", "DOR", "TSG", "SGE",
          "SCF", "ELV", "STU", "SCH", "MAI", "FCA", "HSV", "SVW"}


def test_all_match_is_ok():
    res = reconcile(ROSTER, ROSTER, ROSTER | {"BOC", "HDH", "STP", "WOB"}, {"ELV", "PAD", "SCH"})
    assert res["ok"] is True and res["problems"] == []
    assert res["provisional"] == ["ELV", "PAD", "SCH"]


def test_wrong_batch_is_reported():
    old = ROSTER - {"ELV", "KOE", "PAD", "SCH"} | {"BOC", "HDH", "STP", "WOB"}
    res = reconcile(old, ROSTER, ROSTER | {"BOC"}, set())
    assert res["ok"] is False
    assert "BOC" in "/".join(res["outside"])
    assert "ELV" in "/".join(res["absent"])


def test_missing_param_is_reported():
    res = reconcile(ROSTER, ROSTER, ROSTER - {"SCH"}, {"ELV", "PAD"})
    assert res["ok"] is False and res["no_param"] == ["SCH"]
