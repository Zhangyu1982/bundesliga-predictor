"""P0-1 shift 台账：只统计"算过且可信"的场次。

三态口径（防第 9 轮那类"显示 0.00 其实没算"）：
- None：未计算（没拿到首发/伤病数据）——不计入样本；
- "computed"：算了且可信，0.00 是真实的零偏移——计入；
- "unverified"：有值但未声明来源——不计入，单列出来等补声明。
"""
from __future__ import annotations
import json
import os

LIVE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "live_results.json")


def _load(path: str | None = None) -> list[dict]:
    try:
        with open(path or LIVE_PATH, encoding="utf-8") as f:
            return json.load(f).get("records", [])
    except (OSError, ValueError):
        return []


def valid_records(path: str | None = None) -> list[dict]:
    """只返回 shift_basis == "computed" 且至少一侧有值的场次。"""
    out = []
    for r in _load(path):
        if r.get("shift_basis") != "computed":
            continue
        if r.get("shift_home") is None and r.get("shift_away") is None:
            continue
        out.append(r)
    return out


def unverified_records(path: str | None = None) -> list[dict]:
    return [r for r in _load(path) if r.get("shift_basis") == "unverified"]


def summary(path: str | None = None) -> dict:
    rs = valid_records(path)
    vals = [float(v) for r in rs for v in (r.get("shift_home"), r.get("shift_away")) if v is not None]
    capped = [v for v in vals if v >= 0.2999]
    return {"n_valid": len(rs), "values": len(vals),
            "mean": round(sum(vals) / len(vals), 4) if vals else None,
            "top_rate": round(len(capped) / len(vals), 4) if vals else None,
            "unverified": len(unverified_records(path)),
            "missing": sum(1 for r in _load(path) if r.get("shift_basis") in (None, ""))}
