"""平局输出层校准（只动 draw，不碰 rho / λ）。

形状依据：两端反向、中间准（825 场分桶）。
- 低档（p≤0.15，强队场）系统性低估 → 抬到 0.16 附近；
- 中档（0.15<p≤0.30）基本准 → 几乎不动（0.15 → 0.165 保持单调）；
- 高档（p>0.30，均势场）系统性高估 → 压到 0.28。

锚点不是拍脑袋：在 2324↔2425 之间做时间切分交叉选择（每季用另一季拟合候选映射），
选 ΔBrier/ΔLogLoss 同时为正、Δ命中不低于 -1pp 的一组；2526 全程只做留出验证，不参与选择。
拟合/验证脚本：`scripts/draw_recal_validate.py`，报告 `data/draw_recal_report.json`。
"""
from __future__ import annotations
import bisect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIT_SEASONS = ("2324", "2425")
HOLDOUT_SEASON = "2526"
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "draw_map.json")

# (draw 概率, 校准后 draw 概率)；线性插值，两端裁剪
# 末端锚点止于 0.45：留出集 draw≥0.60 为 0 场，超过 0.45 一律按 0.30 处理，不外推
DEFAULT_MAP = [[0.0, 0.16], [0.15, 0.165], [0.30, 0.28], [0.45, 0.30]]


def load_map(path: str = MODEL_PATH) -> list[list[float]]:
    try:
        with open(path, encoding="utf-8") as f:
            knots = json.load(f)["map"]
        if len(knots) >= 2:
            return [[float(a), float(b)] for a, b in knots]
    except (OSError, ValueError, KeyError):
        pass
    return [list(k) for k in DEFAULT_MAP]


def _interp(knots: list[list[float]], p: float) -> float:
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, p)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (p - x0) / (x1 - x0)


def recalibrate_draw(probs: dict, knots: list[list[float]] | None = None) -> dict:
    """只改 draw：主/客按同一比例缩放，三者之和仍为 1。"""
    knots = knots if knots is not None else load_map()
    p = float(probs["draw"])
    if p <= 0.0 or p >= 1.0:
        return dict(probs)
    p2 = min(max(_interp(knots, p), 0.0), 1.0)
    scale = (1.0 - p2) / (1.0 - p)
    out = {"home_win": probs["home_win"] * scale, "draw": p2,
           "away_win": probs["away_win"] * scale}
    for k, v in probs.items():
        out.setdefault(k, v)
    return out


if __name__ == "__main__":  # 把当前锚点固化到 data/draw_map.json
    art = {"map": [list(k) for k in DEFAULT_MAP],
           "selection": "CV on 2324<->2425 (time-split), holdout 2526 untouched",
           "fit_seasons": list(FIT_SEASONS), "holdout": HOLDOUT_SEASON,
           "note": "输出层校准：只改 draw，主客同比例缩放；不改 rho/lambda，可 A/B 回滚"}
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(art, f, ensure_ascii=False, indent=2)
    print("已写入", MODEL_PATH)
