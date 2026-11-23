import json
import pytest
from scripts.record_live_result import _shift_basis
from data import shift_track


def test_shift_basis_three_states():
    assert _shift_basis(None, None, False) is None       # 未计算
    assert _shift_basis(0.0, 0.0, True) == "computed"    # 算了且为零
    assert _shift_basis(0.1, None, False) == "unverified"


def test_shift_out_of_range_rejected():
    with pytest.raises(SystemExit):
        _shift_basis(0.35, None, True)


def test_only_computed_enters_stats(tmp_path):
    p = tmp_path / "live.json"
    p.write_text(json.dumps({"records": [
        {"match_id": "A", "shift_home": None, "shift_away": None},                  # 未计算
        {"match_id": "B", "shift_home": 0.0, "shift_away": 0.1, "shift_basis": "computed"},
        {"match_id": "C", "shift_home": 0.2, "shift_away": None, "shift_basis": "unverified"},
        {"match_id": "D", "shift_home": 0.3, "shift_away": 0.3, "shift_basis": "computed"},
    ]}), encoding="utf-8")
    s = shift_track.summary(str(p))
    assert s["n_valid"] == 2 and s["unverified"] == 1 and s["missing"] == 1
    assert abs(s["mean"] - (0.0 + 0.1 + 0.3 + 0.3) / 4) < 1e-9
    assert s["top_rate"] == 0.5
