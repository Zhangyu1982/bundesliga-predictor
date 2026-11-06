"""P0/P1 修复的回归测试（不依赖 ESPN / 模型，纯函数级，可直接跑）。

覆盖：
  1. 只对 core 计扣分（P0：core_rate 之前空转）
  2. 正常轮换（rate 0.33~0.50，不在 core）不再被当成"主力缺失"
  3. 按 athlete id 匹配首发/替补（姓名漂移不再造成假扣分）
  4. 已离队球员不扣分
  5. roster 残缺（<20 人）→ roster_suspect，整体跳过校验
  6. "过半离队" → 同样 roster_suspect，不把整队清空
  7. 无 roster → 保持老口径（roster_unavailable），不误剔除

放置位置：项目根目录 `tests/`，运行 `python -m pytest tests/test_p5_shift_fixes.py -q`
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from config import LINEUP_SHIFT, RULES              # noqa: E402
from lineup_shift import starter_deviation, describe_shift   # noqa: E402
from rules.iteration_rules import IterationRules     # noqa: E402

UNIT = LINEUP_SHIFT["unit_shift"]
BENCH = LINEUP_SHIFT["bench_factor"]

CORE = [("C%d" % i, "c%d" % i) for i in range(8)]
ROT = [("R%d" % i, "r%d" % i) for i in range(3)]
ALL_IDS = [i for _, i in CORE + ROT]


def _p(name, pid, rate, pos=""):
    return {"name": name, "id": pid, "pos": pos, "rate": rate}


def _baseline():
    xi = [_p(n, i, 0.83) for n, i in CORE] + [_p(n, i, 0.33) for n, i in ROT]
    core = [_p(n, i, 0.83) for n, i in CORE]
    return {"available": True, "n_matches": 6, "xi": xi, "core": core,
            "seen": [{"name": n, "id": i} for n, i in CORE + ROT]}


def _lineup(starters, bench=()):
    return {"starters": [{"name": n, "id": i} for n, i in starters],
            "bench": [{"name": n, "id": i} for n, i in bench],
            "formation": "4-2-3-1", "code": "TST"}


def _full_roster():
    return {"ids": list(ALL_IDS)}


def test_only_core_is_penalised():
    starters = [(n, i) for n, i in CORE[:7]] + ROT      # C7 缺阵
    dev = starter_deviation(_baseline(), _lineup(starters), roster=_full_roster())
    assert dev["available"] is True
    assert [a["name"] for a in dev["absent"]] == ["C7"]
    assert abs(dev["shift"] - round(UNIT * 0.83, 4)) < 1e-6, dev["shift"]


def test_rotation_absence_is_free():
    dev = starter_deviation(_baseline(), _lineup(CORE), roster=_full_roster())
    assert dev["shift"] == 0.0
    assert dev["absent"] == []


def test_bench_counts_half():
    starters = [(n, i) for n, i in CORE[:7]] + ROT
    dev = starter_deviation(_baseline(), _lineup(starters, bench=[CORE[7]]),
                            roster=_full_roster())
    assert abs(dev["shift"] - round(UNIT * 0.83 * BENCH, 4)) < 1e-6
    assert dev["benched"][0]["name"] == CORE[7][0]


def test_id_match_survives_name_drift():
    # c0 在基线里叫 "C0"，出场名单里叫别的名字（同一 id）
    starters = [("Cristiano Ronaldo", "c0")] + [(n, i) for n, i in CORE[1:]] + ROT
    dev = starter_deviation(_baseline(), _lineup(starters), roster=_full_roster())
    assert dev["shift"] == 0.0, dev
    assert all(a["name"] != "C0" for a in dev["absent"])


def test_departed_not_penalised():
    # 真实名单 20+ 人：C7 不在其中 → 判离队，不扣分
    roster = {"ids": [f"x{k}" for k in range(25)] + [i for i in ALL_IDS if i != "c7"]}
    starters = [(n, i) for n, i in CORE[:7]] + ROT
    dev = starter_deviation(_baseline(), _lineup(starters), roster=roster)
    assert dev["shift"] == 0.0
    assert [d["name"] for d in dev["departed"]] == ["C7"]
    assert dev["roster_check"] == "ok"


def test_small_roster_is_suspect_and_skipped():
    starters = [(n, i) for n, i in CORE[:7]] + ROT
    dev = starter_deviation(_baseline(), _lineup(starters), roster={"ids": ALL_IDS[:6]})
    assert dev["roster_check"] == "roster_suspect"
    assert dev["departed"] == []
    assert abs(dev["shift"] - round(UNIT * 0.83, 4)) < 1e-6


def test_mass_departure_is_suspect():
    starters = [(n, i) for n, i in CORE[:7]] + ROT
    roster = {"ids": [f"x{k}" for k in range(25)]}      # 名字对不上且超过 20 人
    dev = starter_deviation(_baseline(), _lineup(starters), roster=roster)
    assert dev["roster_check"] == "roster_suspect"
    assert dev["departed"] == []


def test_no_roster_keeps_legacy():
    starters = [(n, i) for n, i in CORE[:7]] + ROT
    dev = starter_deviation(_baseline(), _lineup(starters), roster={})
    assert dev["roster_check"] == "roster_unavailable"
    assert dev["departed"] == []
    assert dev["shift"] > 0


# ---------------- 2026-11-06 追加：口径按 roster 过滤的 core ----------------

def test_core_outside_xi_still_penalised():
    """扣分范围 = core（按 roster 过滤），不要求必须落在 xi。

    core 是"近 6 场首发率≥0.7"的判定，xi 只是基线快照；核心没进 xi 也照样扣。
    """
    base = _baseline()
    extra = _p("C8", "c8", 0.9)
    base["core"] = base["core"] + [extra]                 # 核心，但不在 xi 里
    base["seen"] = base["seen"] + [{"name": "C8", "id": "c8"}]
    roster = {"ids": list(ALL_IDS) + ["c8"] + [f"x{k}" for k in range(20)]}
    dev = starter_deviation(base, _lineup(CORE + ROT), roster=roster)
    assert dev["roster_check"] == "ok"
    assert [a["name"] for a in dev["absent"]] == ["C8"]
    assert abs(dev["shift"] - round(UNIT * 0.9, 4)) < 1e-6, dev["shift"]


def test_departed_core_is_removed_from_scope():
    """离队核心不扣分（scope 按 roster 过滤时同样生效）。"""
    base = _baseline()
    extra = _p("C8", "c8", 0.9)
    base["core"] = base["core"] + [extra]
    roster = {"ids": list(ALL_IDS) + [f"x{k}" for k in range(20)]}   # c8 已离队
    dev = starter_deviation(base, _lineup(CORE + ROT), roster=roster)
    assert dev["shift"] == 0.0
    assert [d["name"] for d in dev["departed"]] == ["C8"]


def test_mass_departure_warns_rebuild_but_still_filters():
    """离队占比 ≥ departed_warn_ratio(0.6)：剔除照常生效 + 提示重建，不回退成"不校验"。"""
    roster = {"ids": list(ALL_IDS[:4]) + [f"y{k}" for k in range(20)]}   # 11 人里走 7 人
    dev = starter_deviation(_baseline(), _lineup(CORE[:4]), roster=roster)
    assert dev["roster_check"] == "ok"
    assert dev["rebuild_recommended"] is True
    assert dev["departed_ratio"] >= LINEUP_SHIFT["departed_warn_ratio"]
    assert len(dev["departed"]) == 7
    # 已离队的人不在扣分对象里，所以 shift 只来自仍在册的 4 名核心（且全部首发）
    assert dev["shift"] == 0.0
    txt = describe_shift("测试队", dev)
    assert "更新主力基线" in txt and "过期" in txt


def test_departure_below_threshold_has_no_loud_warning():
    roster = {"ids": list(ALL_IDS[:9]) + [f"z{k}" for k in range(20)]}   # 走 2 人，比例 0.18
    dev = starter_deviation(_baseline(), _lineup(CORE[:5]), roster=roster)
    assert dev["rebuild_recommended"] is False
    assert dev["departed_ratio"] < LINEUP_SHIFT["departed_warn_ratio"]


# ---------------- 2026-11-06 追加：R1 before 必须取 bundle ----------------

class _FakeBundle:
    elo_k = 20
    elo_home_adv = 60


def test_r1_before_comes_from_bundle(tmp_path):
    """R1 触发 elo_recalibrate 时，日志里的"改前参数"要取训练产物的标定值，不读 config。"""
    rules = IterationRules(log_root=str(tmp_path))
    rules.attach_elo_params(bundle=_FakeBundle())
    n = RULES["R1_bias_record"]["consecutive_rounds"]
    for r in range(n):
        rules.record_deviation("m1", "TST", "主胜", "客胜", [], "0-1", "中", round_no=r + 1)
    rows = [x for x in rules.logger.read("R1_bias_record") if x["event"] == "elo_recalibrate"]
    assert rows, "连续偏差未触发 elo_recalibrate"
    assert rows[-1]["payload"]["before"] == {"K": 20, "home_advantage": 60}
    assert rows[-1]["payload"]["before_source"] == "bundle"


def test_r1_before_blank_when_bundle_missing(tmp_path):
    """没绑 bundle 时不能编造 15/50，应显式置空并标注来源不可用。"""
    rules = IterationRules(log_root=str(tmp_path))
    n = RULES["R1_bias_record"]["consecutive_rounds"]
    for r in range(n):
        rules.record_deviation("m2", "TST2", "主胜", "客胜", [], "0-1", "中", round_no=r + 1)
    rows = [x for x in rules.logger.read("R1_bias_record") if x["event"] == "elo_recalibrate"]
    assert rows
    assert rows[-1]["payload"]["before"] == {"K": None, "home_advantage": None}
    assert rows[-1]["payload"]["before_source"] == "unavailable"
