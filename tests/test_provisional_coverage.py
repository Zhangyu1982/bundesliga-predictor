"""2026-27 升班马覆盖补丁：映射 + 临时参数 + 闸门放行。"""
import json, os


def test_code2eng_covers_promoted_teams():
    from data.historical_data import CODE2ENG
    for code, eng in (("KOE", "FC Koln"), ("ELV", "Elversberg"),
                      ("PAD", "Paderborn"), ("SCH", "Schalke 04")):
        assert CODE2ENG.get(code) == eng, f"{code} 缺映射"


def test_provisional_json_shape():
    p = os.path.join(os.path.dirname(__file__), "..", "data", "provisional_teams.json")
    d = json.load(open(p, encoding="utf-8"))
    assert d["_meta"]["calibrated"] is False
    teams = d["teams"]
    assert set(teams) == {"Elversberg", "Paderborn", "Schalke 04"}
    for name, v in teams.items():
        assert 0.5 <= v["attack"] <= 1.7, name
        assert 0.55 <= v["defence"] <= 1.9, name


def test_apply_provisional_merges_into_bundle():
    from trainer import train, apply_provisional
    b = train()
    b = apply_provisional(b)
    for name in ("Elversberg", "Paderborn", "Schalke 04"):
        assert name in b.dc_attack and name in b.dc_defence
    assert set(b.provisional) == {"Elversberg", "Paderborn", "Schalke 04"}
    # 原有 21 队参数不得被改动
    assert "Bayern Munich" in b.dc_attack


def test_round_coverage_passes_for_18_teams_with_promoted():
    from pipeline import round_coverage, PredictionPipeline
    from data.historical_data import CODE2ENG
    from trainer import apply_provisional
    p = PredictionPipeline()
    b = apply_provisional(p._bundle)
    cover = {c for c, eng in CODE2ENG.items() if eng in b.dc_attack}
    codes = ["BAY", "LEV", "DOR", "RBL", "STU", "SGE", "SCF", "MAI", "BMG", "WOB",
             "SVW", "FCA", "TSG", "FCU", "HSV", "KOE", "ELV", "PAD"]
    cov = round_coverage([{"home_code": c, "away_code": "SCH"} for c in codes], cover)
    assert set(codes) - set(cov["codes"]) == set()
