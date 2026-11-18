"""球队跨赛季生命周期：降级保留参数、回归按缺席季数衰减。

为什么不删降级队：他们的参数是经过德甲检验的；一旦升回来，用「原参数 × 衰减」
比拿德乙数据重估更可信。衰减只作用于偏离联赛基准 1.0 的部分。
"""
from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_lifecycle.json")


def load_lifecycle() -> dict:
    with open(_PATH, encoding="utf-8") as f:
        return json.load(f)


def _season_start(season: str) -> int:
    """"2627" -> 2026。"""
    s = str(season)
    return 2000 + int(s[:2])


def seasons_absent(code: str, season: str) -> int:
    """该队在 season 赛季已缺席几个赛季（未降级过 = 0）。"""
    relegated = load_lifecycle()["relegated"]
    since = relegated.get(code)
    if not since:
        return 0
    return max(0, _season_start(season) - _season_start(since))


def decay_per_season() -> float:
    return float(load_lifecycle()["decay_per_season"])


def decay_factor(code: str, season: str) -> float:
    return decay_per_season() ** seasons_absent(code, season)


def decayed(attack: float, defence: float, factor: float) -> tuple[float, float]:
    """只衰减偏离 1.0 的部分：att' = 1 + (att-1)*f；def' 同理。"""
    return (1 + (attack - 1) * factor, 1 + (defence - 1) * factor)


def carryover_params(params: dict, roster_codes, season: str) -> tuple[dict, list[str]]:
    """对「曾降级、现在又在本季名单里」的队应用衰减，返回 (新参数, 受影响 code)。"""
    out = dict(params)
    touched = []
    for code in sorted(set(roster_codes)):
        if code not in out or seasons_absent(code, season) <= 0:
            continue
        att, dfn = out[code]
        out[code] = decayed(att, dfn, decay_factor(code, season))
        touched.append(code)
    return out, touched


def promoted_since(code: str) -> str | None:
    return load_lifecycle()["promoted"].get(code)
