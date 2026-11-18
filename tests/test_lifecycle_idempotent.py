"""降级队回归衰减必须是幂等的：重复 apply / pickle 往返都不能二次衰减。

背景 bug：train() 的落盘路径先 apply_provisional（内含 apply_lifecycle）再 pickle.dump，
于是磁盘上的参数已经是「衰减过」的；下次进程启动再 apply 一次，就在已衰减值上再乘 0.85，
每次重启都多衰减一层，参数被静默吃掉。
"""
import os, pickle, tempfile

import pytest

from trainer import ModelBundle, apply_lifecycle


def _bundle():
    b = ModelBundle()
    b.dc_attack = {"Wolfsburg": 1.40, "Bayern": 2.05, "Hamburg": 0.88}
    b.dc_defence = {"Wolfsburg": 1.10, "Bayern": 0.93, "Hamburg": 1.25}
    return b


@pytest.fixture
def fake_roster(monkeypatch):
    """伪造一份含 WOB 的本季名单（真实 2627 名单里没有降级队回归，衰减路径是空转的）。"""
    import data.season_roster as sr

    monkeypatch.setattr(sr, "load_roster", lambda season=None: {
        "WOB": "Wolfsburg", "BAY": "Bayern", "HSV": "Hamburg"}, raising=True)
    return sr


def test_repeated_apply_does_not_double_decay(fake_roster):
    b = _bundle()
    apply_lifecycle(b, season="2627")
    att1, dfn1 = b.dc_attack["Wolfsburg"], b.dc_defence["Wolfsburg"]
    assert b.carryover == ["WOB"]
    # 0.85 因子：1 + (1.40-1)*0.85 = 1.34
    assert round(att1, 4) == round(1 + 0.40 * 0.85, 4)

    apply_lifecycle(b, season="2627")
    assert (b.dc_attack["Wolfsburg"], b.dc_defence["Wolfsburg"]) == (att1, dfn1)


def test_pickle_roundtrip_does_not_double_decay(fake_roster):
    b = _bundle()
    apply_lifecycle(b, season="2627")
    p = os.path.join(tempfile.mkdtemp(), "models.pkl")
    with open(p, "wb") as f:
        pickle.dump(b, f)
    with open(p, "rb") as f:
        b2 = pickle.load(f)
    apply_lifecycle(b2, season="2627")
    assert round(b2.dc_attack["Wolfsburg"], 6) == round(b.dc_attack["Wolfsburg"], 6)


def test_next_season_decays_from_base_not_from_decayed(fake_roster):
    b = _bundle()
    apply_lifecycle(b, season="2627")          # k=1 → 0.85
    apply_lifecycle(b, season="2829")          # k=3 → 0.85^3，且基于原始 1.40
    assert round(b.dc_attack["Wolfsburg"], 4) == round(1 + 0.40 * 0.85 ** 3, 4)


def test_untouched_teams_keep_values(fake_roster):
    b = _bundle()
    apply_lifecycle(b, season="2627")
    assert b.dc_attack["Bayern"] == 2.05 and b.dc_defence["Bayern"] == 0.93
