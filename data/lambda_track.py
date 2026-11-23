"""λ 滚动账：预测总进球 vs 实际总进球，按泊松标准差算 z。

口径（用户定）：只看最近 20 场的 Σλ 与 Σ实际，z = (Σ实际 − Σλ_pred) / sqrt(Σλ_pred)（正=实际进球多于预测）；
|z| > 2 才谈调 DC 的 mu，否则只记账不改参数。
"""
from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lambda_log.json")


def _path(path: str | None = None) -> str:
    return path or _PATH


def load(path: str | None = None) -> dict:
    try:
        with open(_path(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"records": []}


def append(records: list[dict], path: str | None = None) -> int:
    """追加记录（按 match_id 去重，重复写入覆盖）。"""
    p = _path(path)
    d = load(p)
    by_id = {r.get("match_id"): r for r in d.get("records", [])}
    n = 0
    for r in records or []:
        if not r.get("match_id") or r.get("lam_pred") is None:
            continue
        by_id[r["match_id"]] = {
            "match_id": r["match_id"],
            "season": r.get("season"),
            "round_no": r.get("round_no"),
            "lam_pred": float(r["lam_pred"]),
            "actual_goals": int(r["actual_goals"]) if r.get("actual_goals") is not None else None,
        }
        n += 1
    d["records"] = list(by_id.values())
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return n


def rolling_z(window: int = 20, path: str | None = None) -> tuple[float, int]:
    """最近 window 场的 z 值（与样本数）。没有带实际进球的记录时返回 (0.0, 0)。"""
    recs = [r for r in load(path).get("records", []) if r.get("actual_goals") is not None]
    recs = recs[-window:] if window > 0 else recs
    if not recs:
        return 0.0, 0
    lam = sum(float(r["lam_pred"]) for r in recs)
    act = sum(int(r["actual_goals"]) for r in recs)
    if lam <= 0:
        return 0.0, len(recs)
    return (act - lam) / (lam ** 0.5), len(recs)
