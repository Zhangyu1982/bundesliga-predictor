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


def test_gate_blocks_wrong_batch_from_seed_fallback():
    """示例/串季赛程的 18 队与本赛季基线不一致 → 必须被拦住，不出卡。"""
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    bad = [{"match_id": f"s{i}", "home_code": h, "away_code": a, "kickoff": "2026-11-22T14:30:00Z"}
           for i, (h, a) in enumerate([("BAY", "BMG"), ("DOR", "BOC"), ("STP", "HDH"), ("WOB", "HSV"),
                                       ("LEV", "FCA"), ("SCF", "STU"), ("SGE", "SVW"), ("FCU", "TSG"),
                                       ("MAI", "RBL")])]
    p.fetch_round = lambda r: (bad, {"freshness": {}, "realtime_injury_available": False,
                                     "round_has_data": True, "round_no": 10})
    res = p.predict_round(10, stage="初版")
    assert res["cards"] == []
    assert "本季基线不一致" in res["coverage_error"]["message"]
    assert set(res["coverage_error"]["roster_outside"]) == {"BOC", "STP", "HDH", "WOB"}


def test_gate_passes_real_round_10():
    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    from data.api_client import team_zh
    fx = [{"match_id": f"r{i}", "home_code": h, "away_code": a, "kickoff": "2026-11-21T14:30:00Z",
           "home_team": team_zh(h), "away_team": team_zh(a)}
          for i, (h, a) in enumerate([("BAY", "KOE"), ("FCU", "RBL"), ("LEV", "PAD"), ("BMG", "DOR"),
                                      ("SGE", "TSG"), ("ELV", "SCF"), ("SCH", "STU"), ("FCA", "MAI"),
                                      ("SVW", "HSV")])]
    p.fetch_round = lambda r: (fx, {"freshness": {}, "realtime_injury_available": False,
                                    "round_has_data": True, "round_no": 10})
    res = p.predict_round(10, stage="初版")
    assert res.get("coverage_error") is None
    assert len(res["cards"]) == 9
