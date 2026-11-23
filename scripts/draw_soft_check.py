"""平局校准证据补丁：软饱和映射 + 配对 bootstrap CI + pick 翻转 + clamp 用量。

映射形式（不再折线硬接）：
    p' = sigmoid(a + b * logit(p))，再截到 [0.01, 0.45]
参数 a/b 在 2324↔2425 上做留一季交叉选择；2526 只做留出评估。
"""
from __future__ import annotations
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIT = ("2324", "2425")
HOLDOUT = "2526"
ROWS = os.path.join("data", "walkforward_rows.json")
SEED = 20261123


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x > 40:
        return 1.0
    if x < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def apply_soft(p: float, a: float, b: float, lo: float = 0.01, hi: float = 0.45) -> float:
    return min(max(sigmoid(a + b * logit(p)), lo), hi)


def prep(rows):
    for r in rows:
        r["actual_home"] = int(r["ftr"] == "H")
        r["actual_away"] = int(r["ftr"] == "A")
        r["draw_before"] = r["pred_draw"]


def score(rows, a, b):
    """返回 (brier, logloss, hit)；用软饱和后的 draw 与缩放后的主客。"""
    brier = ll = 0.0
    ok = 0
    for r in rows:
        p = r["pred_draw"]
        p2 = apply_soft(p, a, b)
        if 0 < p < 1:
            scale = (1 - p2) / (1 - p)
        else:
            scale = 1.0
        ph, pa = r["pred_home"] * scale, r["pred_away"] * scale
        brier += (ph - r["actual_home"]) ** 2 + (p2 - r["actual_draw"]) ** 2 + (pa - r["actual_away"]) ** 2
        act = "home_win" if r["actual_home"] else ("draw" if r["actual_draw"] else "away_win")
        prob = {"home_win": ph, "draw": p2, "away_win": pa}[act]
        ll += -math.log(max(prob, 1e-12))
        ok += int(max({"home_win": ph, "draw": p2, "away_win": pa},
                      key=lambda k: {"home_win": ph, "draw": p2, "away_win": pa}[k]) == act)
    n = len(rows)
    return brier / n, ll / n, ok / n


def cv_pick(rows):
    best = None
    a_grid = [round(-1.6 + i * 0.05, 3) for i in range(33)]   # -1.60 ~ 0.00
    b_grid = [round(0.0 + i * 0.05, 3) for i in range(21)]    # 0 ~ 1.0
    for a in a_grid:
        for b in b_grid:
            deltas = []
            for hold in FIT:
                te = [r for r in rows if r["season"] == hold]
                b0, l0, h0 = score(te, 0.0, 1.0)
                b1, l1, h1 = score(te, a, b)
                deltas.append((b1 - b0, l1 - l0, h1 - h0))
            db = sum(d[0] for d in deltas) / 2
            dl = sum(d[1] for d in deltas) / 2
            worst_hit = min(d[2] for d in deltas)
            if worst_hit < -0.01:
                continue
            if best is None or (db, dl) < best[0]:
                best = ((db, dl), a, b, worst_hit)
    return best


def paired_bootstrap(rows, reps=2000):
    """逐场 ΔBrier 的配对 bootstrap 95% CI（抽场次、同时抽前后两个值）。"""
    diffs = []
    for r in rows:
        b0, _, _ = score([r], 0.0, 1.0)
        b1, _, _ = score([r], A, B)
        diffs.append(b1 - b0)
    rng = random.Random(SEED)
    means = []
    n = len(diffs)
    for _ in range(reps):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * reps)]
    hi = means[int(0.975 * reps) - 1]
    avg = sum(diffs) / n
    sd = (sum((d - avg) ** 2 for d in diffs) / (n - 1)) ** 0.5
    se = sd / math.sqrt(n)
    return {"mean": round(avg, 5), "ci95": [round(lo, 5), round(hi, 5)],
            "sd": round(sd, 4), "se": round(se, 5),
            "t": round(avg / se, 2) if se else None, "n": n, "reps": reps}


def pick_of(ph, pd, pa):
    m = {"home_win": ph, "draw": pd, "away_win": pa}
    return max(m, key=m.get)


rows = json.load(open(ROWS))
prep(rows)
fit_rows = [r for r in rows if r["season"] in FIT]
hold = [r for r in rows if r["season"] == HOLDOUT]

best = cv_pick(fit_rows)
(_, _), A, B, worst_hit = best
print(f"CV 选定软饱和参数 a={A} b={B}（一季ΔBrier {best[0][0]:+.4f}，ΔLL {best[0][1]:+.4f}，最差Δ命中 {worst_hit:+.3f}）")

# 留出指标
b0, l0, h0 = score(hold, 0.0, 1.0)
b1, l1, h1 = score(hold, A, B)
print(f"2526：Brier {b0:.4f}->{b1:.4f}  LogLoss {l0:.4f}->{l1:.4f}  命中 {h0:.4f}->{h1:.4f}")
ci = paired_bootstrap(hold)
print(f"ΔBrier 配对 bootstrap：mean {ci['mean']:+.5f} 95%CI [{ci['ci95'][0]:+.5f}, {ci['ci95'][1]:+.5f}] "
      f"sd {ci['sd']} se {ci['se']} t {ci['t']}")

# pick 翻转
flip = {"total": 0, "right_to_wrong": 0, "wrong_to_right": 0, "right_to_right": 0, "wrong_to_wrong": 0}
for r in hold:
    p = r["pred_draw"]
    p2 = apply_soft(p, A, B)
    scale = (1 - p2) / (1 - p) if 0 < p < 1 else 1.0
    ph0, pa0 = r["pred_home"], r["pred_away"]
    ph1, pa1 = r["pred_home"] * scale, r["pred_away"] * scale
    act = "home_win" if r["actual_home"] else ("draw" if r["actual_draw"] else "away_win")
    k0, k1 = pick_of(ph0, p, pa0), pick_of(ph1, p2, pa1)
    if k0 != k1:
        flip["total"] += 1
        flip[("right_to_wrong" if k0 == act else "wrong_to_right" if k1 == act else
              "right_to_right" if k0 == act else "wrong_to_wrong")] += 1
print(f"pick 翻转 {flip['total']} 场：对→错 {flip['right_to_wrong']}｜错→对 {flip['wrong_to_right']}｜"
      f"其余（错→错/对→对但方向改变）{flip['right_to_right'] + flip['wrong_to_wrong']}")

# clamp 用量
n35 = sum(1 for r in hold if r["pred_draw"] > 0.35)
n60 = sum(1 for r in hold if r["pred_draw"] >= 0.60)
n40 = sum(1 for r in hold if r["pred_draw"] > 0.40)
print(f"留出集 draw>0.35: {n35} 场；>0.40: {n40} 场；≥0.60: {n60} 场")
n35fit = sum(1 for r in fit_rows if r["pred_draw"] > 0.35)
print(f"拟合集 draw>0.35: {n35fit} 场（占 {n35fit / len(fit_rows):.1%}）")

# 分段 cohort（软饱和）
def cohort(rows, after):
    out = []
    for name, fn in [("≤0.15", lambda p: p <= 0.15), ("0.15–0.30", lambda p: 0.15 < p <= 0.30),
                     ("≥0.30", lambda p: p > 0.30)]:
        sel = [r for r in rows if fn(r["pred_draw"])]
        mass = sum((apply_soft(r["pred_draw"], A, B) if after else r["pred_draw"]) for r in sel)
        act = sum(r["actual_draw"] for r in sel)
        out.append((name, len(sel), round(mass, 2), act, round((act - mass) / math.sqrt(max(mass, 1e-9)), 2)))
    return out


print("留出 cohort 前:", cohort(hold, False))
print("留出 cohort 后:", cohort(hold, True))

json.dump({"a": A, "b": B, "lo": 0.01, "hi": 0.45, "ci": ci, "flip": flip,
           "draw_gt_035": n35, "draw_ge_060": n60,
           "holdout": {"brier": [round(b0, 4), round(b1, 4)],
                       "logloss": [round(l0, 4), round(l1, 4)],
                       "hit": [round(h0, 4), round(h1, 4)]}},
          open("data/draw_soft_report.json", "w"), ensure_ascii=False, indent=2)
print("已写入 data/draw_soft_report.json")
