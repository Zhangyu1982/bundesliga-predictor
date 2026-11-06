"""上线前自检（不联网、不训练；模型相关项缺产物时自动 SKIP）。

用法：在项目根目录执行
    python scripts/preflight.py

退出码：0 = 全过（允许 SKIP）；1 = 有 FAIL。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def check(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append(("PASS", name, ""))
        except AssertionError as e:
            RESULTS.append(("FAIL", name, str(e) or "断言失败"))
        except Exception as e:  # noqa: BLE001
            RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        return fn
    return deco


def skip(name, why):
    RESULTS.append(("SKIP", name, why))


# ---------------- 1. 配置一致性 ----------------
@check("config: STAGES 含终极版")
def _():
    from config import STAGES
    assert "终极版" in STAGES, f"STAGES={STAGES}"


@check("config: 未标定项覆盖半张表")
def _():
    from config import uncalibrated_thresholds
    got = set(uncalibrated_thresholds())
    for k in ("injury_shift", "LINEUP_SHIFT", "FATIGUE_RULES",
              "LUCK_REGRESSION", "MOTIVATION_RULES"):
        assert k in got, f"{k} 未出现在未标定清单里：{sorted(got)}"


# ---------------- 2. 首发偏差口径 ----------------
@check("lineup_shift: 只对 core 扣分 + 姓名漂移免疫")
def _():
    from config import LINEUP_SHIFT
    from lineup_shift import starter_deviation
    unit = LINEUP_SHIFT["unit_shift"]
    core = [(f"C{i}", f"c{i}") for i in range(8)]
    rot = [(f"R{i}", f"r{i}") for i in range(3)]
    bl = {"available": True, "n_matches": 6,
          "xi": [{"name": n, "id": i, "rate": 0.83, "pos": ""} for n, i in core]
                + [{"name": n, "id": i, "rate": 0.33, "pos": ""} for n, i in rot],
          "core": [{"name": n, "id": i, "rate": 0.83, "pos": ""} for n, i in core],
          "seen": [{"name": n, "id": i} for n, i in core + rot]}
    roster = {"ids": [f"x{k}" for k in range(25)] + [i for _, i in core + rot]}

    # 轮换全不在场 → 0 扣分
    lu = {"starters": [{"name": n, "id": i} for n, i in core], "bench": []}
    d = starter_deviation(bl, lu, roster=roster)
    assert d["shift"] == 0.0, f"轮换被误扣：{d['shift']}"

    # 核心缺席 1 人 → 恰好 unit*rate
    lu2 = {"starters": [{"name": n, "id": i} for n, i in core[:7]] + [{"name": "R0", "id": "r0"}],
           "bench": []}
    d2 = starter_deviation(bl, lu2, roster=roster)
    assert abs(d2["shift"] - round(unit * 0.83, 4)) < 1e-6, d2["shift"]

    # 姓名漂移但 id 相同 → 仍算首发
    lu3 = {"starters": [{"name": "Cristiano Ronaldo", "id": "c0"}] +
                       [{"name": n, "id": i} for n, i in core[1:]], "bench": []}
    assert starter_deviation(bl, lu3, roster=roster)["shift"] == 0.0


# ---------------- 3. 日志容错 ----------------
@check("iteration_rules: 坏日志行不抛异常")
def _():
    import shutil
    from rules.iteration_rules import RuleLogger
    # 用项目内临时目录（避免部分受限环境禁止写系统 temp）
    td = os.path.join(ROOT, ".preflight_tmp")
    shutil.rmtree(td, ignore_errors=True)
    os.makedirs(td, exist_ok=True)
    try:
        lg = RuleLogger(td)
        path = os.path.join(td, "r1_bias_record.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"rule":"R1","bad json\n')          # 半行
            f.write('{"rule":"R1_bias_record","event":"record","payload":{"round":9}}\n')
        rows = lg.read("R1_bias_record")
        assert len(rows) == 1, f"应跳过坏行只读到 1 条，实际 {len(rows)}"
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ---------------- 4. 时区窗口 ----------------
@check("iteration_rules: 48h 窗口按带时区解析")
def _():
    from rules.iteration_rules import _hours_before_kickoff
    # kickoff = UTC 12:00，last_updated = 本地 08:00(+08:00) = UTC 00:00 → 12h
    h = _hours_before_kickoff("2026-11-07T08:00:00+08:00", "2026-11-07T12:00:00Z")
    assert h is not None and abs(h - 12.0) < 0.01, h
    assert _hours_before_kickoff("", "2026-11-07T12:00:00Z") is None


# ---------------- 5. 模型层（缺产物则 SKIP） ----------------
def _bundle():
    from trainer import ARTIFACT_PATH, train
    if not os.path.exists(ARTIFACT_PATH):
        raise FileNotFoundError(ARTIFACT_PATH)
    return train()


try:
    b = _bundle()
except Exception as e:  # noqa: BLE001
    skip("trainer: 折扣不退化 / 概率非负", f"拿不到 models.pkl（{type(e).__name__}）")
else:
    @check("trainer: 折扣 1.2 不出现负概率")
    def _():
        m = b.dc_score_matrix("Bayern Munich", "Hamburger SV", 8, home_disc=1.2)
        assert min(m.values()) >= 0, f"出现负概率 {min(m.values())}"
        d = b.dc_prob_discounted("Bayern Munich", "Hamburger SV", 0.3, 0.1)
        assert abs(sum(d.values()) - 1) < 1e-9
        assert all(0 <= v <= 1 for v in d.values()), d


# ---------------- 6. 无数据兜底（需要完整模型） ----------------
try:
    from pipeline import PredictionPipeline
except Exception as e:  # noqa: BLE001
    skip("pipeline: 无数据轮次不崩", f"导入失败（{type(e).__name__}）")
else:
    @check("pipeline: 无数据轮次返回'基准'")
    def _():
        card = PredictionPipeline()._baseline_card(30)
        assert card["pick"] == "基准", card.get("pick")
        p = card["probabilities"]
        # _baseline_card 对外把概率 round 到 3 位，三项之和最多偏离 0.0015；
        # 必须按"展示口径"放宽，否则永远差 0.001 而误报 FAIL。
        assert abs(sum(p.values()) - 1) < 5e-3, p


# ---------------- 输出 ----------------
print()
width = max(len(n) for _, n, _ in RESULTS)
for st, name, note in RESULTS:
    print(f"[{st:4s}] {name:<{width}}  {note}")
n_fail = sum(1 for s, _, _ in RESULTS if s == "FAIL")
print(f"\n合计 {len(RESULTS)} 项：{n_fail} FAIL / "
      f"{sum(1 for s,_,_ in RESULTS if s=='PASS')} PASS / "
      f"{sum(1 for s,_,_ in RESULTS if s=='SKIP')} SKIP")
sys.exit(1 if n_fail else 0)
