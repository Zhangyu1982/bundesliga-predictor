"""出卡前四条核验：规则本身必须被测试钉住，否则闸门会"看着在跑、其实判错"。"""
from scripts.round_checklist import all_pass, check_round
from pipeline import fixture_fingerprint

ROSTER = {"BAY", "KOE", "FCU", "RBL", "LEV", "PAD", "BMG", "DOR", "TSG", "SGE",
          "SCF", "ELV", "STU", "SCH", "MAI", "FCA", "HSV", "SVW"}
PAIRS = [("BAY", "KOE"), ("FCU", "RBL"), ("LEV", "PAD"), ("BMG", "DOR"), ("SGE", "TSG"),
         ("ELV", "SCF"), ("SCH", "STU"), ("FCA", "MAI"), ("SVW", "HSV")]


def _fx(pairs=PAIRS, kickoff="2026-11-21T14:30:00Z", season="2627"):
    return [{"match_id": f"{season}-{i}", "home_code": h, "away_code": a, "kickoff": kickoff}
            for i, (h, a) in enumerate(pairs)]


def _meta(fx, fetched_at="2026-11-18T13:11:42+00:00"):
    return {"freshness": {"fetched_at": fetched_at},
            "fixture_snapshot": fixture_fingerprint(fx, fetched_at=fetched_at)}


def test_all_four_pass_on_real_round_10():
    fx = _fx()
    # 真实首场是周五 11-20 19:30Z：窗口必须覆盖到它
    fx[0]["kickoff"] = "2026-11-20T19:30:00Z"
    fx[-1]["kickoff"] = "2026-11-22T16:30:00Z"
    res = check_round(fx, _meta(fx), ROSTER, ("2026-11-20", "2026-11-22"))
    assert len(res) == 4 and all_pass(res), [r for r in res if not r["ok"]]


def test_check1_catches_wrong_batch_teams():
    """跑错批次（上一季 4 队）必须被第 1 条抓住，并指明两边各缺谁。"""
    pairs = [("BAY", "BMG"), ("DOR", "BOC"), ("STP", "HDH"), ("WOB", "HSV"),
             ("LEV", "FCA"), ("SCF", "STU"), ("SGE", "SVW"), ("FCU", "TSG"), ("MAI", "RBL")]
    res = check_round(_fx(pairs, season="2526"), _meta(_fx(pairs)), ROSTER,
                      ("2026-11-20", "2026-11-22"))
    r1 = res[0]
    assert r1["ok"] is False
    assert "名单外" in r1["detail"] and "BOC" in r1["detail"]
    assert "名单缺" in r1["detail"] and "ELV" in r1["detail"]


def test_check1_ignores_teams_model_covers_but_not_in_round():
    """模型覆盖 22 队（含已降级 4 队）不能导致第 1 条误杀——基准是赛季名单，不是覆盖集。"""
    cover = ROSTER | {"BOC", "HDH", "STP", "WOB"}
    res = check_round(_fx(), _meta(_fx()), cover, ("2026-11-20", "2026-11-22"))
    assert res[0]["ok"] is True


def test_check2_uses_utc_not_beijing():
    """周日 16:30Z 的比赛在北京是次日凌晨；按 UTC 窗口应判在界内。"""
    res = check_round(_fx(), _meta(_fx()), ROSTER, ("2026-11-20", "2026-11-22"))
    assert res[1]["ok"] is True
    # 而只给 11-21~11-22 的 UTC 窗口（用户原写法）会漏掉周五开场场次
    fx = _fx()
    fx[0]["kickoff"] = "2026-11-20T19:30:00Z"
    res = check_round(fx, _meta(fx), ROSTER, ("2026-11-21", "2026-11-22"))
    assert res[1]["ok"] is False and "BAY-KOE" in res[1]["detail"]


def test_check3_catches_duplicate_team():
    pairs = PAIRS[:-1] + [("SVW", "BAY")]
    res = check_round(_fx(pairs), _meta(_fx(pairs)), ROSTER, ("2026-11-20", "2026-11-22"))
    # SVW-HSV 被改成 SVW-BAY 后 BAY 出场两次 —— 第 3 条必须报重复队
    assert res[2]["ok"] is False and "BAY" in res[2]["detail"]


def test_check4_catches_fetched_at_mismatch():
    fx = _fx()
    meta = _meta(fx)
    meta["fixture_snapshot"] = fixture_fingerprint(fx, fetched_at="2026-11-17T09:00:00+00:00")
    res = check_round(fx, meta, ROSTER, ("2026-11-20", "2026-11-22"))
    assert res[3]["ok"] is False


def test_check4_catches_missing_snapshot():
    fx = _fx()
    res = check_round(fx, {"freshness": {}}, ROSTER, ("2026-11-20", "2026-11-22"))
    assert res[3]["ok"] is False
