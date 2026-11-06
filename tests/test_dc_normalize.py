"""DC 尺度归一化回归（修复「默认 1.0 ≠ 联赛平均」）。

背景：λ = mu * attack[主] * defence[客]，而 unsee n 球队走 .get(x, 1.0) 默认值。
拟合出的 attack 均值 ≈0.614、defence 均值 ≈1.591，所以"默认"曾经等于"攻强守弱"。
_normalize_dc 把 attack 除以均值、defence 乘同一个数（λ 不变），使 1.0 = 平均队。
"""
import pytest

import trainer

ART_OK = True


@pytest.fixture(scope="module")
def bundle():
    return trainer.train(verbose=False)


def test_mean_attack_is_one(bundle):
    ma = sum(bundle.dc_attack.values()) / len(bundle.dc_attack)
    assert abs(ma - 1.0) < 1e-6, f"mean(dc_attack)={ma}，归一化没生效"


def test_normalize_is_idempotent(bundle):
    import copy
    b = copy.deepcopy(bundle)
    before = dict(b.dc_attack)
    trainer._normalize_dc(b)
    for k, v in before.items():
        assert abs(b.dc_attack[k] - v) < 1e-9, f"{k} 二次归一化后变了"


def test_known_teams_unaffected(bundle):
    """已见球队的 λ 与概率不应因归一化改变（attack*defence 不变）。"""
    import copy
    b = copy.deepcopy(bundle)
    ma = sum(b.dc_attack.values()) / len(b.dc_attack)
    # 还原到修复前的尺度
    b.dc_attack = {k: v * ma for k, v in b.dc_attack.items()}
    b.dc_defence = {k: v / ma for k, v in b.dc_defence.items()}
    for h, a in [("Bayern Munich", "Augsburg"), ("Dortmund", "RB Leipzig"),
                 ("Leverkusen", "Mainz")]:
        lam0, mu0 = b.dc_lambda(h, a)
        lam1, mu1 = bundle.dc_lambda(h, a)
        assert abs(lam0 - lam1) < 1e-9 and abs(mu0 - mu1) < 1e-9


def test_unknown_team_lambda_close_to_average(bundle):
    """两支都没有 DC 参数的队（用 code 直接传），λ 应≈「平均队打平均队」。"""
    ma = sum(bundle.dc_attack.values()) / len(bundle.dc_attack)
    md = sum(bundle.dc_defence.values()) / len(bundle.dc_defence)
    lam_unk, mu_unk = bundle.dc_lambda("ELV", "PAD")
    lam_avg = bundle.dc_mu_home * ma * md
    mu_avg = bundle.dc_mu_away * ma * md
    assert abs(lam_unk / lam_avg - 1) < 0.05, f"升班马进攻 λ 偏离平均队 {(lam_unk/lam_avg-1)*100:.1f}%"
    assert abs(mu_unk / mu_avg - 1) < 0.05


def test_promoted_team_not_favoured_at_bayern(bundle):
    """回归：拜仁主场打无历史数据的升班马，必须明显占优（曾经是五五开）。"""
    p = bundle.dc_prob("Bayern Munich", "ELV")
    assert p["home_win"] > 0.65, f"拜仁 vs 升班马 DC 主胜只有 {p['home_win']:.2f}"
    assert p["home_win"] > p["away_win"] * 3


def test_dortmund_home_vs_promoted_is_home_pick(bundle):
    """回归：多特主场打升班马，pick 应为多特胜（曾经是客胜）。"""
    p = bundle.dc_prob("Dortmund", "ELV")
    assert max(p, key=p.get) == "home_win", f"pick 仍有误：{ {k: round(v,3) for k, v in p.items()} }"
