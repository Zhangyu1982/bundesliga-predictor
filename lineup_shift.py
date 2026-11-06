"""
P5 · 首发偏差 → 战力扣减
=========================
把「本场首发」与「常规主力库」比对，折算成与现有伤病链路同口径的 DC 攻击折扣。

判定规则（用户口径）：
  - 常规主力出现在首发      → 战力满分，不扣
  - 常规主力在替补席        → 扣一半（下半场能上）
  - 常规主力完全不在名单    → 全额扣（伤病/未入选）

**转会窗容错（P5.1）**：基线是静态快照，夏窗/冬窗卖掉的人会永远留在库里被当成"缺阵"。
所以在扣分之前先用 `roster`（当前大名单 athlete id 集合）校验：
  - 基线球员已不在当前大名单 → 记入 `departed[]`，**不扣分**（他已经不在这支队了）；
  - `roster` 没拿到（空）→ 保持原样，绝不把所有基线球员当成离队；
  - 本场首发但基线窗口内从未首发过的人 → 记入 `newcomers[]`（新援/奇兵），只展示不加减分。

单位对齐：config.RULES.injury_shift 的 key_out_shift=0.10 表示"缺 1 名关键球员 = DC 攻击折扣 0.10"，
本模块沿用同一单位（unit_shift=0.10），并按球员的历史首发率缩放（首发率越高越重要）。
"""
from __future__ import annotations

import logging

from config import LINEUP_SHIFT

logger = logging.getLogger(__name__)


def _pid(p: dict) -> str:
    """球员身份键：优先 athlete id（转会/重名都更稳），否则退回姓名。"""
    return str((p or {}).get("id") or "").strip() or ("name::" + ((p or {}).get("name") or ""))


def starter_deviation(baseline_team: dict | None, lineup_side: dict | None,
                      cfg: dict | None = None, roster: dict | None = None) -> dict:
    """计算单队首发偏差。

    baseline_team: {"xi":[{name,id,rate,...}], "core":[...], "seen":[...], "n_matches":int}
    lineup_side:   {"starters":[{name,id,...}], "bench":[...], "code":..., "formation":...}
    roster:        {"ids":[...]} 该队当前大名单（用于剔除已离队球员；空则跳过校验）
    """
    c = cfg or LINEUP_SHIFT
    unit = float(c.get("unit_shift", 0.10))
    bench_factor = float(c.get("bench_factor", 0.5))
    max_total = float(c.get("max_total_shift", 0.30))
    roster_check = c.get("roster_check", True)
    newcomer_hint = c.get("newcomer_hint", True)

    if not baseline_team or not baseline_team.get("available", True) or not baseline_team.get("xi"):
        return {"available": False, "shift": 0.0, "absent": [], "benched": [], "departed": [],
                "newcomers": [], "matched": 0, "unknown_starters": [], "n_matches": 0,
                "roster_check": "no_baseline", "roster_shakeup": False,
                "rebuild_recommended": False, "departed_ratio": 0.0,
                "note": "无常规主力库（该队历史首发样本不足）"}
    if not lineup_side or not (lineup_side.get("starters") or []):
        return {"available": False, "shift": 0.0, "absent": [], "benched": [], "departed": [],
                "newcomers": [], "matched": 0, "unknown_starters": [],
                "n_matches": baseline_team.get("n_matches", 0), "roster_check": "no_lineup",
                "roster_shakeup": False, "rebuild_recommended": False, "departed_ratio": 0.0,
                "note": "ESPN 尚未公布该队首发（赛前约 60 分钟才出）"}

    # ---- 转会窗校验：先把已不在本队的基线球员剔出扣分对象 ----
    # 修复 1：id 统一 str（防止 roster 侧出现 int 导致"全员离队"）
    ros_ids = {str(x) for x in ((roster or {}).get("ids") or [])}
    min_players = int(c.get("roster_min_players", 20))
    xi_raw = list(baseline_team.get("xi") or [])
    core_raw = list(baseline_team.get("core") or [])
    departed = []
    # 修复 2：名单可信度守卫。残缺名单（< roster_min_players）更可能是数据错，不做剔除。
    roster_suspect = bool(roster_check and ros_ids and len(ros_ids) < min_players)
    roster_shakeup = False
    rebuild_recommended = False
    departed_ratio = 0.0
    if roster_check and ros_ids and not roster_suspect:
        xi = [p for p in xi_raw if _pid(p) in ros_ids]
        core = [p for p in core_raw if _pid(p) in ros_ids]
        # 离队比例按基线 XI 的规模算：core 里可能有不在 xi 的人，混进分母会把比例算歪。
        xi_dep = [p for p in xi_raw if _pid(p) not in ros_ids]
        departed_ratio = len(xi_dep) / max(1, len(xi_raw))
        _seen = set()
        for p in xi_dep + [q for q in core_raw if _pid(q) not in ros_ids]:
            k = _pid(p)
            if k in _seen:
                continue
            _seen.add(k)
            departed.append({"name": p.get("name", ""), "id": p.get("id", ""),
                             "pos": p.get("pos", ""), "rate": p.get("rate"),
                             "why": "已离队（不在当前大名单），不计扣分"})
        # 修正：离队过半**不再**回退（冬窗大换血就该照常剔除，回退=把离队的人按缺阵扣分，
        # 那正是 P5.1 要修的坑）。只有"基线 XI 全员都对不上名单"才判名单不可信——
        # 一支队 11 人同时离队不可能，那基本是 id 口径错位（换过数据源/换过 id 体系）。
        if xi_raw and len(xi_dep) == len(xi_raw):
            roster_suspect = True
            xi, core, departed = xi_raw, core_raw, []
            departed_ratio = 0.0
            roster_status = "roster_suspect"
        else:
            roster_shakeup = departed_ratio >= float(c.get("drift_warn_ratio", 0.25))
            # 离队占比 ≥ departed_warn_ratio（默认 0.6）：基线已大概率过期，
            # 继续扣分会系统性低估该队；不打回退，但必须显式提示 + 进日志。
            rebuild_recommended = departed_ratio >= float(c.get("departed_warn_ratio", 0.6))
            if rebuild_recommended:
                logger.warning(
                    "lineup_shift: 基线可能过期 team=%s departed=%d/%d (%.0f%%) → 建议重建主力基线",
                    baseline_team.get("code") or baseline_team.get("team") or "?",
                    len(xi_dep), len(xi_raw), departed_ratio * 100)
            roster_status = "ok"
    else:
        xi, core = xi_raw, core_raw
        roster_status = ("disabled" if not roster_check
                         else ("roster_suspect" if roster_suspect else "roster_unavailable"))

    starters = {p.get("name"): p for p in (lineup_side.get("starters") or []) if p.get("name")}
    bench = {p.get("name"): p for p in (lineup_side.get("bench") or []) if p.get("name")}
    # 修复：姓名之外同时按 athlete id 建索引。ESPN 姓名写法漂移（Luis Díaz / Luis Diaz）
    # 会让"明明首发"的基线球员被判缺阵并扣分，也会把老队员误标成新援。
    _starter_ids = {_pid(p) for p in (lineup_side.get("starters") or [])}
    _bench_ids = {_pid(p) for p in (lineup_side.get("bench") or [])}

    def _started(p: dict) -> bool:
        return _pid(p) in _starter_ids or (p.get("name") in starters)

    def _on_bench(p: dict) -> bool:
        return _pid(p) in _bench_ids or (p.get("name") in bench)

    absent, benched = [], []
    raw = 0.0
    matched = 0
    # 修复（P0）：只对"核心"计扣分。原实现遍历固定 11 人的 xi，把正常轮换
    # （rate 0.30~0.50）也当成主力缺失，core_rate 调到 0.7 实际没有生效。
    # 口径（2026-11-06 用户确认）：扣分范围 = core 中仍在册的人（已在上方按 roster 过滤），
    # **不要求必须落在 xi**。core 是"近 6 场首发率≥0.7"的判定，xi 只是基线快照；
    # 用 core ∩ xi 会漏掉"已是核心但没进基线 XI"的球员。
    scope = core or xi
    for p in scope:
        name = p.get("name")
        if not name:
            continue
        rate = float(p.get("rate", 1.0) or 0)
        if _started(p):
            matched += 1
            continue
        penalty = unit * rate
        if _on_bench(p):
            penalty *= bench_factor
            benched.append({"name": name, "rate": round(rate, 3), "pos": p.get("pos", ""),
                            "penalty": round(penalty, 4), "why": "在替补席"})
        else:
            absent.append({"name": name, "rate": round(rate, 3), "pos": p.get("pos", ""),
                           "penalty": round(penalty, 4), "why": "不在大名单"})
        raw += penalty

    shift = round(min(raw, max_total), 4)

    # ---- 非基线 XI 的球员首发：区分「老队员轮换」与「新面孔/新援」 ----
    xi_names = {p.get("name") for p in xi_raw if p.get("name")}
    xi_ids = {_pid(p) for p in xi_raw}
    seen = baseline_team.get("seen") or []
    seen_ids = {_pid(p) for p in seen} if seen else set()

    unknown, newcomers = [], []
    for n, p in starters.items():
        if n in xi_names:
            continue
        row = {"name": n, "pos": (p or {}).get("pos", ""), "id": str((p or {}).get("id") or "")}
        unknown.append(row)
        if not newcomer_hint:
            continue
        if seen:
            # 基线窗口内**从未首发过** → 真生面孔（新援或被提上首发的替补）
            if row["id"] and row["id"] not in seen_ids:
                newcomers.append({**row, "why": "基线窗口内从未首发（新援/奇兵）"})
            elif not row["id"] and n not in {q.get("name") for q in seen}:
                newcomers.append({**row, "why": "基线窗口内从未首发（新援/奇兵）"})
        else:
            # 旧版基线没有 seen 字段，只能保守地标为"疑似"
            newcomers.append({**row, "why": "基线无历史名单（疑似新面孔）", "estimated": True})

    if shift <= 0:
        note = "常规主力全部首发，战力按满分计"
        if departed:
            note += f"（已剔除 {len(departed)} 名离队球员）"
    else:
        note = (f"常规主力缺失 {len(absent)+len(benched)} 人（其中 {len(benched)} 人在替补席，按半额计），"
                f"扣除 DC 攻击折扣 {shift:.2f}")
        if departed:
            note += f"；已剔除 {len(departed)} 名离队球员（不扣分）"
    return {
        "available": True,
        "shift": shift,
        "absent": absent,
        "benched": benched,
        "departed": departed,
        "newcomers": newcomers,
        "matched": matched,
        "unknown_starters": unknown,
        "n_matches": baseline_team.get("n_matches", 0),
        "formation": (lineup_side or {}).get("formation", ""),
        "bench_size": len(bench),
        "roster_check": roster_status,
        "roster_shakeup": roster_shakeup,
        "rebuild_recommended": rebuild_recommended,
        "departed_ratio": round(departed_ratio, 3),
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
        base = f"{side_name}：常规主力全部首发（近 {dev.get('n_matches', 0)} 场基线），无额外扣减"
    else:
        names = [f"{x['name']}（{'替补席' if x['why']=='在替补席' else '不在名单'}）"
                 for x in (dev.get("absent") or []) + (dev.get("benched") or [])]
        xg = xg_equivalent(dev["shift"], base_lambda)
        base = (f"{side_name}：缺失常规主力 {len(names)} 人 —— " + "、".join(names)
                + f"，已自动扣除 {dev['shift']:.2f} 折扣（约 -{xg:.2f} xG）")
    # 转会窗容错：把"校验做了什么/没能做什么"如实写出来
    dep = dev.get("departed") or []
    if dep:
        base += f"；已剔除 {len(dep)} 名离队球员（{ '、'.join(x['name'] for x in dep[:4]) }）"
    elif dev.get("roster_check") == "roster_unavailable":
        base += "；⚠️ 大名单未取到，未做离队校验"
    elif dev.get("roster_check") == "roster_suspect":
        base += "；⚠️ 大名单异常（人数过少/离队比例过高），已跳过校验"
    # 洗牌提示独立追加：不挡住上面的"已剔除"说明
    if dev.get("rebuild_recommended"):
        base += (f"；‼️ 基线可能过期（近 {dev.get('n_matches', 0)} 场首发中 "
                 f"{dev.get('departed_ratio', 0):.0%} 已离队），扣分可能系统性偏高，"
                 "请立即点「🔄 更新主力基线」重建")
    elif dev.get("roster_shakeup"):
        base += "；♻️ 阵容已大换血，建议点「🔄 更新主力基线」重建"
    return base


def describe_newcomers(side_name: str, dev: dict, max_n: int = 3) -> str | None:
    """UI 用的"新援/奇兵首发"提示；没有新面孔时返回 None。"""
    nc = (dev or {}).get("newcomers") or []
    if not nc:
        return None
    names = "、".join(f"{x['name']}" + (f"（{x['pos']}）" if x.get("pos") else "") for x in nc[:max_n])
    more = f" 等 {len(nc)} 人" if len(nc) > max_n else ""
    tail = "（基线缺少历史名单，属疑似）" if any(x.get("estimated") for x in nc) else ""
    return (f"{side_name}：新面孔首发 {names}{more} —— 不在近 "
            f"{dev.get('n_matches', 0)} 场常规主力里，可能是新援或被提上来的替补{tail}；"
            "这部分不额外加分，仅作提示")

