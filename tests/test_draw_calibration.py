from scripts.draw_calibration import bin_table, _probs


def test_bin_table_uses_5pp_buckets_and_gap():
    rows = [{"pred_draw": 0.11, "actual_draw": 1}, {"pred_draw": 0.14, "actual_draw": 0},
            {"pred_draw": 0.23, "actual_draw": 0}]
    t = bin_table(rows)
    assert [x["bin"] for x in t] == ["0.10-0.15", "0.20-0.25"]
    assert t[0]["n"] == 2 and t[0]["actual_draw_rate"] == 0.5
    assert round(t[0]["gap_rate_minus_pred"], 4) == round(0.5 - 0.125, 4)
    assert t[1]["n"] == 1 and t[1]["gap_rate_minus_pred"] < 0


def test_probs_sum_to_one_and_draw_reasonable():
    dc = {"attack": {"A": 1.5, "B": 0.8}, "defence": {"A": 0.8, "B": 1.4},
          "mu_home": 1.6, "mu_away": 1.2}
    p = _probs(dc, "A", "B")
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p["home_win"] > p["away_win"] > 0


def test_probs_none_for_unknown_team():
    dc = {"attack": {"A": 1.0}, "defence": {"A": 1.0}, "mu_home": 1.5, "mu_away": 1.2}
    assert _probs(dc, "A", "Z") is None
