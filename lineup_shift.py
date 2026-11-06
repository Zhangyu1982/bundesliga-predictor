"""
P5 · 首发偏差 → 战力扣减
=========================
把「本场首发」与「常规主力库」比对，折算成与现有伤病链路同口径的 DC 攻击折扣。

判定规则（用户口径）：
  - 常规主力出现在首发      → 战力满分，不扣
  - 常规主力在替补席        → 扣一半（下半场能上）
  - 常规主力完全不在名单    → 全额扣（伤病/未入选）

单位对齐：config.RULES.injury_shift 的 key_out_shift=0.10 表示"缺 1 名关键球员 = DC 攻击折扣 0.10"，
本模块沿用同一单位（unit_shift=0.10），并按球员的历史首发率缩放（首发率越高越重要）。
"""
from __future__ import annotations

from config import LINEUP_SHIFT


def starter_deviation(baseline_team: dict | None, lineup_side: dict | None,
                      cfg: dict | None = None) -> dict:
    """计算单队首发偏差。

    baseline_team: {"xi":[{name,rate,...}], "core":[...], "n_matches":int}
    lineup_side:   {"starters":[{name,...}], "bench":[{name,...}], "code":..., "formation":...}
    """
    c = cfg or LINEUP_SHIFT
    unit = float(c.get("unit_shift", 0.10))
    bench_factor = float(c.get("bench_factor", 0.5))
    max_total = float(c.get("max_total_shift", 0.30))

    if not baseline_team or not baseline_team.get("available", True) or not baseline_team.get("xi"):
        return {"available": False, "shift": 0.0, "absent": [], "benched": [],
                "matched": 0, "unknown_starters": [], "n_matches": 0,
                "note": "无常规主力库（该队历史首发样本不足）"}
    if not lineup_side or not (lineup_side.get("starters") or []):
        return {"available": False, "shift": 0.0, "absent": [], "benched": [],
                "matched": 0, "unknown_starters": [], "n_matches": baseline_team.get("n_matches", 0),
                "note": "ESPN 尚未公布该队首发（赛前约 60 分钟才出）"}

    starters = {p.get("name"): p for p in (lineup_side.get("starters") or []) if p.get("name")}
    bench = {p.get("name"): p for p in (lineup_side.get("bench") or []) if p.get("name")}

    absent, benched = [], []
    raw = 0.0
    matched = 0
    for p in baseline_team["xi"]:
        name = p.get("name")
        if not name:
            continue
        rate = float(p.get("rate", 1.0) or 0)
        if name in starters:
            matched += 1
            continue
        penalty = unit * rate
        if name in bench:
            penalty *= bench_factor
            benched.append({"name": name, "rate": round(rate, 3), "pos": p.get("pos", ""),
                            "penalty": round(penalty, 4), "why": "在替补席"})
        else:
            absent.append({"name": name, "rate": round(rate, 3), "pos": p.get("pos", ""),
                           "penalty": round(penalty, 4), "why": "不在大名单"})
        raw += penalty

    shift = round(min(raw, max_total), 4)

    # 非 baseline 的球员首发（顶替者）—— 只作展示，不额外加减分
    xi_names = {p.get("name") for p in baseline_team["xi"] if p.get("name")}
    unknown = [{"name": n, "pos": (p or {}).get("pos", "")}
               for n, p in starters.items() if n not in xi_names]

    if shift <= 0:
        note = "常规主力全部首发，战力按满分计"
    else:
        note = (f"常规主力缺失 {len(absent)+len(benched)} 人（其中 {len(benched)} 人在替补席，按半额计），"
                f"扣除 DC 攻击折扣 {shift:.2f}")
    return {
        "available": True,
        "shift": shift,
        "absent": absent,
        "benched": benched,
        "matched": matched,
        "unknown_starters": unknown,
        "n_matches": baseline_team.get("n_matches", 0),
        "formation": (lineup_side or {}).get("formation", ""),
        "bench_size": len(bench),
        "note": note,
    }


def xg_equivalent(shift: float, base_lambda: float = 1.5) -> float:
    """把 DC 攻击折扣折算成"每场少约多少进球"，仅用于 UI 展示。

    shift 是 lambda 的相对折扣，因此实际 xG 减少 ≈ shift × base_lambda。
    默认 lambda 1.5（德甲单队场均进球量级），调用方应传入本场真实 lambda。
    """
    try:
        return round(float(shift) * float(base_lambda), 3)
    except Exception:
        return 0.0


def describe_shift(side_name: str, dev: dict, base_lambda: float = 1.5) -> str:
    """生成 UI 用的一行说明。"""
    if not dev.get("available"):
        return f"{side_name}：{dev.get('note', '未获取首发')}"
    if dev.get("shift", 0) <= 0:
        return f"{side_name}：常规主力全部首发（近 {dev.get('n_matches', 0)} 场基线），无额外扣减"
    names = [f"{x['name']}（{'替补席' if x['why']=='在替补席' else '不在名单'}）"
             for x in (dev.get("absent") or []) + (dev.get("benched") or [])]
    xg = xg_equivalent(dev["shift"], base_lambda)
    return (f"{side_name}：缺失常规主力 {len(names)} 人 —— " + "、".join(names)
            + f"，已自动扣除 {dev['shift']:.2f} 折扣（约 -{xg:.2f} xG）")
