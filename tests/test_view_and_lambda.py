"""两个可判定指标：① "无观点场"看 margin（top1−top2），不看最大概率；② λ 滚动 z。

用户口径：0.351/0.328/0.320 间距只有 0.023，是真无观点；0.40/0.35/0.25 最大值也低但排序有信息。
"""
import json
import os
import tempfile

from pipeline import view_margin
from data import lambda_track


def test_view_margin_flat_triplet_is_no_view():
    m, flag = view_margin({"home_win": 0.351, "draw": 0.328, "away_win": 0.320})
    assert round(m, 3) == 0.023
    assert flag is True


def test_view_margin_low_max_but_clear_order_is_a_view():
    m, flag = view_margin({"home_win": 0.40, "draw": 0.35, "away_win": 0.25})
    assert round(m, 3) == 0.05
    assert flag is False  # 0.05 是边界，>=0.05 视为有方向


def test_view_margin_confident():
    m, flag = view_margin({"home_win": 0.763, "draw": 0.104, "away_win": 0.133})
    assert flag is False and m > 0.6


def test_view_margin_handles_missing():
    assert view_margin({}) == (0.0, True)
    assert view_margin(None) == (0.0, True)


def _clear_log(path):
    d = {"records": []}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


def test_rolling_z_uses_poisson_sd():
    p = os.path.join(tempfile.mkdtemp(), "lambda_log.json")
    _clear_log(p)
    # 预测 3.94 -> 实际 2 球：diff=1.94, sd=sqrt(Σλ)
    lambda_track.append([{"match_id": "m1", "lam_pred": 3.94, "actual_goals": 2}], path=p)
    z, n = lambda_track.rolling_z(path=p, window=20)
    assert n == 1
    assert round(z, 3) == round((2 - 3.94) / (3.94 ** 0.5), 3)


def test_rolling_z_only_uses_last_n():
    p = os.path.join(tempfile.mkdtemp(), "lambda_log.json")
    _clear_log(p)
    recs = [{"match_id": f"old{i}", "lam_pred": 5.0, "actual_goals": 0} for i in range(30)]
    recs.append({"match_id": "new1", "lam_pred": 2.6, "actual_goals": 3})
    lambda_track.append(recs, path=p)
    z, n = lambda_track.rolling_z(path=p, window=1)
    assert n == 1 and round(z, 3) == round((3 - 2.6) / (2.6 ** 0.5), 3)


def test_rolling_z_empty():
    p = os.path.join(tempfile.mkdtemp(), "lambda_log.json")
    _clear_log(p)
    z, n = lambda_track.rolling_z(path=p, window=20)
    assert n == 0 and z == 0.0
