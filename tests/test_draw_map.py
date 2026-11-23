from data.draw_map import DEFAULT_MAP, _interp, load_map, recalibrate_draw


def test_map_is_monotone_and_anchored():
    knots = load_map()
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    assert xs == sorted(xs)
    assert all(b >= a for a, b in zip(ys, ys[1:])), "校准映射必须单调，否则会制造新的倒挂"


def test_low_tail_lifted_mid_untouched_high_compressed():
    assert 0.155 <= _interp(DEFAULT_MAP, 0.02) <= 0.165   # 2% → 16% 附近
    assert abs(_interp(DEFAULT_MAP, 0.25) - 0.25) < 0.02         # 中段基本不动
    assert 0.275 <= _interp(DEFAULT_MAP, 0.36) <= 0.29   # 高档压到 28% 附近


def test_recalibrate_sums_to_one_and_scales_sides():
    p = {"home_win": 0.7, "draw": 0.1, "away_win": 0.2}
    out = recalibrate_draw(p, DEFAULT_MAP)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["draw"] > p["draw"]
    # 主客比例保持不变
    assert abs(out["home_win"] / out["away_win"] - 0.7 / 0.2) < 1e-9


def test_recalibrate_keeps_extremes():
    for d in (0.0, 1.0):
        p = {"home_win": 1.0 - d, "draw": d, "away_win": 0.0}
        assert recalibrate_draw(p, DEFAULT_MAP)["draw"] == d


def test_pipeline_toggle_is_identity_when_off(monkeypatch):
    import pipeline
    p = {"home_win": 0.7, "draw": 0.1, "away_win": 0.2}
    monkeypatch.setattr(pipeline, "DRAW_RECAL", False)
    assert pipeline.PredictionPipeline._apply_draw_map(p) == p
    monkeypatch.setattr(pipeline, "DRAW_RECAL", True)
    out = pipeline.PredictionPipeline._apply_draw_map(p)
    assert out["draw"] > p["draw"] and abs(sum(out.values()) - 1.0) < 1e-9


def test_map_does_not_extrapolate_beyond_support():
    from data.draw_map import _interp
    knots = load_map()
    assert _interp(knots, 0.95) == knots[-1][1] == 0.30   # >0.45 不再外推


def test_shadow_log_written_without_touching_probs(tmp_path, monkeypatch):
    import json
    import os
    import pipeline
    monkeypatch.chdir(tmp_path)
    p = {"home_win": 0.7, "draw": 0.1, "away_win": 0.2}
    pipeline.PredictionPipeline._shadow_draw("BUN_TEST", p, "2627")
    rec = json.loads(open(os.path.join("logs", "draw_shadow.jsonl"), encoding="utf-8").read().splitlines()[0])
    assert rec["base"] == {"home_win": 0.7, "draw": 0.1, "away_win": 0.2}
    assert rec["recal"]["draw"] > rec["base"]["draw"]
