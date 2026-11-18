"""产物一致性：同一份数据在两个环境重训后，参数指纹必须相同。

XGBoost 的 pkl 不能跨平台搬，所以"一致性"不能靠复制文件保证，只能靠参数指纹核对。
"""
import pickle
import tempfile
import os

from data.artifact_check import fingerprint, summary, check


class _B:
    def __init__(self, att, dfn, elo):
        self.dc_attack = att
        self.dc_defence = dfn
        self.elo_ratings = elo
        self.provisional = []
        self.dc_mu_home = 1.738562
        self.dc_mu_away = 1.457516
        self.dc_rho = -0.1
        self.elo_k = 20
        self.elo_home_adv = 60
        self.metrics = {"elo_brier": 0.15911439445862163, "dc_nll": 2774.377725191573}


def test_fingerprint_stable_and_order_independent():
    a = _B({"Bayern": 2.0503, "Mainz": 1.02}, {"Bayern": 0.9264, "Mainz": 1.31},
           {"Bayern": 1900.0, "Mainz": 1500.0})
    b = _B({"Mainz": 1.02, "Bayern": 2.0503}, {"Mainz": 1.31, "Bayern": 0.9264},
           {"Mainz": 1500.0, "Bayern": 1900.0})
    assert fingerprint(a) == fingerprint(b)
    assert len(fingerprint(a)) == 16


def test_fingerprint_changes_with_param():
    a = _B({"Bayern": 2.05}, {"Bayern": 0.93}, {})
    c = _B({"Bayern": 2.06}, {"Bayern": 0.93}, {})
    assert fingerprint(a) != fingerprint(c)


def test_summary_core_mean_excludes_provisional():
    b = _B({"Bayern": 2.0, "Paderborn": 0.5}, {"Bayern": 1.0, "Paderborn": 2.0}, {})
    b.provisional = ["Paderborn"]
    assert summary(b)["core_mean_attack"] == 2.0


def test_check_expect_mismatch_reports_false():
    b = _B({"Bayern": 2.0}, {"Bayern": 1.0}, {})
    p = os.path.join(tempfile.mkdtemp(), "m.pkl")
    with open(p, "wb") as f:
        pickle.dump(b, f)
    ok, s = check(expect=fingerprint(b), path=p)
    assert ok is True and s["fingerprint"] == fingerprint(b)
    ok2, _ = check(expect="deadbeefdeadbeef", path=p)
    assert ok2 is False
