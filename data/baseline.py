"""
P5 · 常规主力库（Baseline XI）
==============================
用 ESPN 的历史首发名单统计「谁经常首发」，得到每队的常规主力与首发率。

数据来源（已实测）：
  - `/teams`              → 18 支队 id
  - `/teams/{id}/schedule`→ 该队近期已完成/待打的 event id
  - `/summary?event=`     → 该场 11 首发 + 9 替补（见 data/lineup.py）

口径：
  - 只统计**该队实际出场且首发名单已公布**的场次（含联赛+杯赛，ESPN 同一 league code 内）
  - 首发率 rate = 首发次数 / 参与统计的场次数，取值 0–1
  - baseline XI = 门将位取首发最多的 1 人 + 其余按 rate 取 10 人
  - 常规主力集合 core = rate >= cfg.core_rate（**2026-11-06 收紧为 0.7 + 6 场窗口**：
    等价于「6 场里至少首发 5 场」。0.7 只是阈值表达，4/6=0.667 达不到，故不会把
    正常战术轮换算成核心，避免轮换球员被当成"伤病缺阵"扣分）

P5.1 · 转会窗容错
----------------
基线是**静态快照**。夏窗/冬窗卖掉的人会一直留在库里，被当成"主力缺失"重扣；
新援则完全不在库里。因此：
  1. 构建时同时抓 `/teams/{id}/roster` 的**当前大名单**，把已不在队的球员从 XI/core 剔除；
  2. 每队存 `roster_ids` 快照与 `seen`（窗口内所有首发过的球员），供运行时二次校验；
  3. `roster_drift()` 比对快照与最新大名单，离队比例超阈值 → 判定"阵容洗牌，建议重建"。
大名单接口实测：`/ger.1/teams/{id}/roster` → 200，扁平球员数组（id/fullName/position/jersey/age），
athlete id 与 summary 里的 `athlete.id` 同一套（BAY/HSV/DOR 各 11/11 全匹配）。
（`transactions`/`seasons` 只是 `.pvt` 内部 $ref 链接，取不到转会明细，故新援识别只能用首发历史代理。）

P5.2 · 基线自动重建
------------------
静态快照必须定期刷新，否则 3 轮后就和当前阵容脱节。本模块提供：
  - `baseline_staleness()`：判定是否该重建（轮次落后 >= rebuild_round_gap，或距今 >= rebuild_max_age_days）；
  - `maybe_auto_rebuild()`：需要时起**后台守护线程**静默重建（磁盘锁防并发，状态写状态文件）；
  - `rebuild_now()`：同步重建（UI 按钮用，带进度回调）；
  - `rebuild_status()`：读状态文件，供 UI 显示"正在重建/已完成/失败"。
**安全约定**：重建结果可用队数 < `rebuild_min_teams` 时**绝不覆盖**已有 JSON（网络故障/接口挂掉
时旧基线仍可用于预测）；所有写入都是「临时文件 + os.replace」原子替换，进程中途退出不会留下半个文件。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta

from data.euro_fetch import espn_team_to_code
from data.lineup import (_get_json, ESPN_SOCCER_BASE, LINEUP_LEAGUES,
                         fetch_match_lineup, scoreboard_events, _load, _save)
from config import LINEUP_SHIFT as LINEUP_CFG

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_FILE = os.path.join(DATA_DIR, "baseline_xi.json")
ROSTER_CACHE = os.path.join(DATA_DIR, "roster_cache.json")
REBUILD_LOCK = os.path.join(DATA_DIR, ".baseline_rebuild.lock")
REBUILD_STATE = os.path.join(DATA_DIR, ".baseline_rebuild.json")

# 项目 code -> BL1 18 队（用于确认 baseline 覆盖到哪些队）
BL1_CODES = ["BAY", "BMG", "DOR", "ELV", "FCA", "FCU", "HSV", "KOE", "LEV",
             "MAI", "PAD", "RBL", "SCF", "SCH", "SGE", "STU", "SVW", "TSG"]


def load_baseline() -> dict:
    try:
        with open(BASELINE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json_atomic(path: str, data: dict) -> bool:
    """原子写：先写同目录临时文件，再 os.replace，避免写出半个 JSON。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def save_baseline(data: dict, guard: bool = True) -> bool:
    """落盘基线。guard=True 时，可用队数不足 `rebuild_min_teams` 的结果**拒绝写入**。

    失败的重建绝不能覆盖一份还能用的旧基线 —— 这是自动重建最危险的副作用，
    所以默认开着守卫。返回是否真正写入。
    """
    if guard:
        teams = (data or {}).get("teams") or {}
        n_ok = sum(1 for v in teams.values() if v.get("available"))
        need = int((LINEUP_CFG or {}).get("rebuild_min_teams", 9))
        if n_ok < need:
            print(f"[baseline] 拒绝保存：可用队数 {n_ok} < 阈值 {need}，保留旧基线")
            return False
    return _write_json_atomic(BASELINE_FILE, data)


def fetch_team_ids(league_key: str = "BL1", use_cache: bool = True) -> dict[str, str]:
    """ESPN team id 表：{code: espn_team_id}。"""
    lg = LINEUP_LEAGUES.get(league_key)
    if not lg:
        return {}
    from data.lineup import SCOREBOARD_CACHE, _load, _save
    cache = _load(SCOREBOARD_CACHE)
    tkey = f"teams:{lg}"
    if use_cache and tkey in cache:
        return cache[tkey]

    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/teams")
    if not j:
        return {}
    out = {}
    try:
        tlist = ((j.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    except Exception:
        tlist = []
    for t in tlist:
        team = t.get("team") or {}
        code = espn_team_to_code(team.get("displayName") or "", team.get("abbreviation") or "")
        if code:
            out[code] = str(team.get("id"))
    cache[tkey] = out
    _save(SCOREBOARD_CACHE, cache)
    return out


def fetch_team_events(team_id: str, league_key: str = "BL1") -> list[dict]:
    """该队近期赛事（含已结束），返回 [{event_id, date}]。"""
    lg = LINEUP_LEAGUES.get(league_key)
    if not lg or not team_id:
        return []
    j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/teams/{team_id}/schedule")
    if not j:
        return []
    out = []
    for e in j.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        state = ((comp.get("status") or {}).get("type") or {}).get("state") or ""
        out.append({"event_id": str(e.get("id")), "date": e.get("date") or "",
                    "name": e.get("name") or "", "state": state})
    return out


def fetch_team_roster(team_id: str, league_key: str = "BL1", use_cache: bool = True,
                      ttl_hours: int = 24) -> dict:
    """抓该队**当前大名单**（转会窗容错的基础）。

    返回 {ids: [athlete_id...], players: {id: {name,pos,jersey,age}}, n, fetched_at, source}
    接口不可用时返回 {"ids": [], "players": {}, "n": 0, "error": "..."} —— 调用方必须把空名单
    当作"校验不可用"而**不是**"全队已离队"，绝不能据此清空基线。
    """
    lg = LINEUP_LEAGUES.get(league_key)
    empty = {"ids": [], "players": {}, "n": 0, "source": "ESPN roster"}
    if not lg or not team_id:
        return {**empty, "error": "缺少 team_id 或 league"}

    cache = _load(ROSTER_CACHE)
    ck = f"{lg}:{team_id}"
    hit = cache.get(ck) or {}
    if use_cache and hit.get("ids"):
        try:
            from datetime import datetime as _dt
            age_h = (_dt.now() - _dt.strptime(hit["fetched_at"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
            if age_h <= ttl_hours:
                return hit
        except Exception:
            pass

    # 该端点单次约 200KB 且偶发慢（实测出现过 ReadTimeout），重试 3 次再放弃
    j = None
    for _i in range(3):
        j = _get_json(f"{ESPN_SOCCER_BASE}/{lg}/teams/{team_id}/roster", timeout=30)
        if j:
            break
        time.sleep(2)
    if not j:
        return {**empty, "error": "ESPN roster 请求失败（已重试 3 次）"}

    ids, players = [], {}
    for p in j.get("athletes") or []:
        pid = str(p.get("id") or "")
        name = p.get("fullName") or p.get("displayName") or ""
        if not pid:
            continue
        pos = p.get("position")
        pos_abbr = (pos or {}).get("abbreviation", "") if isinstance(pos, dict) else str(pos or "")
        ids.append(pid)
        players[pid] = {"name": name, "pos": pos_abbr,
                        "jersey": p.get("jersey") or "", "age": p.get("age")}
    if not ids:
        return {**empty, "error": "ESPN roster 为空"}

    out = {"ids": ids, "players": players, "n": len(ids),
           "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "source": "ESPN roster"}
    cache[ck] = out
    _save(ROSTER_CACHE, cache)
    return out


def _id_key(p: dict) -> str:
    """球员的身份键：优先 athlete id（转会/重名都更稳），其次姓名。"""
    return str(p.get("id") or "").strip() or ("name::" + (p.get("name") or ""))


def prune_by_roster(xi: list[dict], core: list[dict], roster: dict | None,
                    cfg: dict | None = None) -> tuple[list[dict], list[dict], list[dict], str]:
    """把"已经不在本队大名单"的基线球员剔除（不参与缺阵扣分）。

    返回 (xi_ok, core_ok, departed, status)
      status: "ok"（校验过）/ "roster_unavailable"（名单没拿到，保持原样）/ "disabled"
    roster 为空时**原样返回**——抓不到名单不等于全队走光，这是本函数最重要的安全约定。
    """
    c = cfg or {}
    if c.get("roster_check") is False:
        return xi, core, [], "disabled"
    ids = set((roster or {}).get("ids") or [])
    if not ids:
        return xi, core, [], "roster_unavailable"

    kept, departed = [], []
    for p in xi:
        if _id_key(p) in ids:
            kept.append(p)
        else:
            departed.append({"name": p.get("name", ""), "id": p.get("id", ""),
                             "pos": p.get("pos", ""), "rate": p.get("rate"),
                             "why": "已离队（不在当前大名单）"})
    kept_keys = {_id_key(p) for p in kept}
    core_ok = [p for p in core if _id_key(p) in kept_keys or _id_key(p) in ids]
    return kept, core_ok, departed, "ok"


def roster_drift(team_entry: dict, roster: dict | None) -> dict:
    """基线快照 vs 当前大名单：判断阵容是否洗牌（需重建基线）。

    以基线 XI 为分母算"离队比例"，因为只有这 11 人才会影响扣分。
    """
    ids = set((roster or {}).get("ids") or [])
    xi = (team_entry or {}).get("xi") or []
    if not xi or not ids:
        return {"available": False, "departed": [], "departed_ratio": 0.0,
                "drift": False, "note": "大名单或基线缺失，无法比对"}
    gone = [p for p in xi if _id_key(p) not in ids]
    ratio = round(len(gone) / len(xi), 3)
    return {"available": True,
            "departed": [p.get("name", "") for p in gone],
            "departed_ratio": ratio,
            "drift": ratio >= float((LINEUP_CFG or {}).get("drift_warn_ratio", 0.25)),
            "note": (f"基线 XI 中 {len(gone)} 人已不在当前大名单（{ratio:.0%}）"
                     if gone else "基线 XI 全部仍在当前大名单")}


def check_baseline_health(league_key: str = "BL1", use_cache: bool = True) -> dict:
    """体检基线库：逐队比对当前大名单 + 检查快照时效，供 UI 提示是否可以信任。"""
    bl = load_baseline()
    teams = bl.get("teams") or {}
    team_ids = fetch_team_ids(league_key, use_cache=use_cache)
    stale, ok_n, unavailable = [], 0, 0
    for code, entry in teams.items():
        if not entry.get("available"):
            continue
        tid = team_ids.get(code)
        ros = fetch_team_roster(tid, league_key) if tid else {}
        drift = roster_drift(entry, ros)
        if not drift.get("available"):
            unavailable += 1
            continue
        if drift.get("drift"):
            stale.append({"code": code, **drift})
        else:
            ok_n += 1
    out = {"updated_at": bl.get("updated_at", ""), "n_teams": len(teams),
           "ok": ok_n, "stale": stale, "roster_unavailable": unavailable,
           "need_rebuild": bool(stale)}
    return out


def _side_of(lu: dict, code: str) -> str:
    """判断该队在这场是主还是客。"""
    if ((lu.get("home") or {}).get("code")) == code:
        return "home"
    if ((lu.get("away") or {}).get("code")) == code:
        return "away"
    return ""


def _aggregate(team_code: str, lineups: list[dict]) -> dict:
    """把多场首发汇总成 {球员名: 统计}，逐场按该队实际的主/客位置取名单。"""
    stat: dict[str, dict] = {}
    n = 0
    for lu in lineups:
        side = _side_of(lu, team_code)
        if not side:
            continue
        sd = lu.get(side) or {}
        starters = sd.get("starters") or []
        if len(starters) < 11:
            continue
        n += 1
        for p in starters:
            name = p.get("name") or ""
            if not name:
                continue
            row = stat.setdefault(name, {"name": name, "id": p.get("id", ""),
                                         "pos": p.get("pos", ""), "starts": 0})
            row["starts"] += 1
            if p.get("pos") and not row["pos"]:
                row["pos"] = p["pos"]
    for r in stat.values():
        r["rate"] = round(r["starts"] / n, 3) if n else 0.0
    return {"n_matches": n, "players": stat}


def _pick_xi(players: dict) -> tuple[list[dict], list[dict]]:
    """从统计里挑 baseline XI（含门将 1 人）与常规主力集合。"""
    rows = sorted(players.values(), key=lambda r: (-r["rate"], -r["starts"], r["name"]))
    gks = [r for r in rows if (r.get("pos") or "").upper().startswith("G")]
    outfield = [r for r in rows if r not in gks]
    xi = ((gks[:1] if gks else []) + outfield)[:11]
    return xi, rows


def select_core(rows: list[dict], core_rate: float) -> list[dict]:
    """挑"核心"：rate >= core_rate 且至少首发过 1 场。

    6 场窗口 + 0.7 ⇒ 只有首发 5–6 场的人入选（4/6=0.667 < 0.7）。
    这是"绝对雷打不动的核心"口径，正常轮换（3–4 场）不会进来。
    """
    return [r for r in rows if r.get("starts", 0) > 0 and r.get("rate", 0) >= core_rate]


def build_baseline(window_matches: int | None = None, league_key: str = "BL1",
                   codes: list[str] | None = None, core_rate: float | None = None,
                   verbose: bool = False, round_hint: int | None = None,
                   save: bool = True) -> dict:
    """构建/刷新常规主力库并落盘。

    window_matches / core_rate 传 None 时取 `config.LINEUP_SHIFT` 的值（6 场 / 0.7）。
    round_hint：调用方已知的"当前轮次"（app 里就是用户选的轮次），写入产物供
    `baseline_staleness()` 判"落后几轮"；命令行构建拿不到时可为 None，此时只按日期判陈旧。
    save=True 时会走 `save_baseline()` 的守卫（可用队数不足则拒绝覆盖旧基线）。
    """
    if window_matches is None:
        window_matches = int((LINEUP_CFG or {}).get("window_matches", 6))
    if core_rate is None:
        core_rate = float((LINEUP_CFG or {}).get("core_rate", 0.7))

    team_ids = fetch_team_ids(league_key)
    target_codes = codes or [c for c in BL1_CODES if c in team_ids]

    # 1) 收集每队近期 event（去重后统一下载阵容）
    team_events: dict[str, list[str]] = {}
    for code in target_codes:
        tid = team_ids.get(code)
        if not tid:
            continue
        evs = [e for e in fetch_team_events(tid, league_key) if e.get("event_id")]
        # 多留几场余量：有些场次首发没公布/被跳过，取 window+6 保证够数
        team_events[code] = [e["event_id"] for e in evs[:window_matches + 6]]

    all_events = sorted({eid for evs in team_events.values() for eid in evs})
    if verbose:
        print(f"[baseline] teams={len(team_events)} events={len(all_events)}")

    lineups = {}
    for eid in all_events:
        lu = fetch_match_lineup(eid, league_key)
        if lu.get("available"):
            lineups[eid] = lu

    # 2) 每队当前大名单（转会窗容错：把已离队的人从 XI/core 里剔掉）
    rosters = {}
    for code in team_events:
        tid = team_ids.get(code)
        ros = fetch_team_roster(tid, league_key) if tid else {}
        rosters[code] = ros
        if verbose:
            print(f"[baseline] {code} roster: {ros.get('n', 0)} 人 {ros.get('error', '')}")

    # 3) 汇总
    teams_out = {}
    for code, evs in team_events.items():
        rows = []
        for eid in evs:
            lu = lineups.get(eid)
            if not lu:
                continue
            if not _side_of(lu, code):
                continue
            rows.append(lu)
            if len(rows) >= window_matches:
                break
        if not rows:
            teams_out[code] = {"n_matches": 0, "xi": [], "core": [], "seen": [],
                               "available": False, "note": "ESPN 未提供该队近期首发"}
            continue
        agg = _aggregate(code, rows)
        xi, all_rows = _pick_xi(agg["players"])
        core = select_core(all_rows, core_rate)
        xi_dicts = [{"name": r["name"], "id": r.get("id", ""), "pos": r.get("pos", ""),
                     "rate": r["rate"], "starts": r["starts"]} for r in xi]
        core_dicts = [{"name": r["name"], "id": r.get("id", ""), "rate": r["rate"],
                       "starts": r["starts"], "pos": r.get("pos", "")} for r in core]
        # 窗口内所有首发过的球员（供运行时判断"本场首发是否为生面孔/新援"）
        seen_dicts = [{"name": r["name"], "id": r.get("id", ""), "pos": r.get("pos", ""),
                       "rate": r["rate"], "starts": r["starts"]} for r in all_rows]

        ros = rosters.get(code) or {}
        xi_dicts, core_dicts, departed, rstatus = prune_by_roster(
            xi_dicts, core_dicts, ros, LINEUP_CFG)
        note = f"近 {agg['n_matches']} 场首发统计"
        if departed:
            note += f"；已剔除 {len(departed)} 名离队球员"
        if rstatus == "roster_unavailable":
            note += "；⚠️ 大名单未取到，未做离队校验"

        side_first = _side_of(rows[0], code)
        teams_out[code] = {
            "available": bool(xi_dicts) and len(xi_dicts) >= int(LINEUP_CFG.get("bench_depth_warn", 3)),
            "n_matches": agg["n_matches"],
            "formation_hint": (rows[0].get(side_first) or {}).get("formation", ""),
            "xi": xi_dicts,
            "core": core_dicts,
            "seen": seen_dicts,
            "roster": {"ids": ros.get("ids") or [], "n": ros.get("n", 0),
                       "fetched_at": ros.get("fetched_at", ""),
                       "checked": rstatus},
            "departed": departed,
            "note": note,
        }
        if verbose:
            print(f"[baseline] {code}: matches={agg['n_matches']} xi={len(xi_dicts)} "
                  f"core={len(core_dicts)} seen={len(seen_dicts)} departed={len(departed)}")

    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "league": LINEUP_LEAGUES.get(league_key, ""),
        "window_matches": window_matches,
        "core_rate": core_rate,
        "built_round": round_hint,
        "roster_checked": True,
        "teams": teams_out,
    }
    data["saved"] = save_baseline(data) if save else False
    return data


# ============ P5.2 · 基线自动重建 ============

def baseline_staleness(baseline: dict | None = None, current_round: int | None = None,
                       cfg: dict | None = None) -> dict:
    """判定基线是否陈旧，返回 {stale, reason, age_days, round_gap, ...}。

    两个判据（满足任一即陈旧）：
      1. 记录的 built_round 落后当前轮次 >= rebuild_round_gap（3 轮）；
      2. updated_at 距今 >= rebuild_max_age_days（14 天）。
    built_round 缺失（命令行构建）/ current_round 缺失时只走日期判据 —— 宁可少重建一次，
    也不能因为"不知道轮次"就每次启动都重建。
    """
    c = cfg or LINEUP_CFG or {}
    bl = baseline if baseline is not None else load_baseline()
    gap_need = int(c.get("rebuild_round_gap", 3))
    max_days = float(c.get("rebuild_max_age_days", 14))

    out = {"stale": False, "reason": "", "age_days": None, "round_gap": None,
           "built_round": (bl or {}).get("built_round"),
           "window_matches": (bl or {}).get("window_matches"),
           "core_rate": (bl or {}).get("core_rate")}

    if not (bl or {}).get("teams"):
        out.update({"stale": True, "reason": "no_baseline"})
        return out

    # 天数：优先 ISO 时间戳（无时区歧义），退化到旧的 %Y-%m-%d %H:%M
    age_days = None
    for key in ("updated_at_iso", "updated_at"):
        raw = (bl or {}).get(key)
        if not raw:
            continue
        try:
            if key == "updated_at_iso":
                age_days = (datetime.now() - datetime.fromisoformat(raw)).total_seconds() / 86400
            else:
                age_days = (datetime.now() - datetime.strptime(raw, "%Y-%m-%d %H:%M")).total_seconds() / 86400
            break
        except Exception:
            continue
    out["age_days"] = round(age_days, 2) if age_days is not None else None

    gap = None
    if current_round and out["built_round"]:
        try:
            gap = int(current_round) - int(out["built_round"])
        except Exception:
            gap = None
    out["round_gap"] = gap

    reasons = []
    if gap is not None and gap >= gap_need:
        reasons.append(f"落后 {gap} 轮（阈值 {gap_need}）")
    if age_days is not None and age_days >= max_days:
        reasons.append(f"已 {age_days:.0f} 天未更新（阈值 {max_days:.0f} 天）")
    if reasons:
        out.update({"stale": True, "reason": "；".join(reasons)})
    return out


def _lock_acquire(ttl_minutes: float = 15) -> bool:
    """磁盘互斥锁（O_EXCL 原子创建）。超过 TTL 的僵尸锁自动清掉。"""
    try:
        if os.path.exists(REBUILD_LOCK):
            age_min = (time.time() - os.path.getmtime(REBUILD_LOCK)) / 60
            if age_min > ttl_minutes:
                os.remove(REBUILD_LOCK)
            else:
                return False
        fd = os.open(REBUILD_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}".encode())
        os.close(fd)
        return True
    except Exception:
        return False


def _lock_release() -> None:
    try:
        if os.path.exists(REBUILD_LOCK):
            os.remove(REBUILD_LOCK)
    except Exception:
        pass


def _write_state(state: dict) -> None:
    _write_json_atomic(REBUILD_STATE, state)


def rebuild_status() -> dict:
    """读重建状态文件：{status: idle|running|done|failed, ...}"""
    try:
        with open(REBUILD_STATE, "r", encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            return st
    except Exception:
        pass
    return {"status": "idle"}


def _rebuild_worker(round_hint=None, window_matches=None, core_rate=None,
                    league_key="BL1") -> dict:
    """后台线程主体：重建 → 守卫落盘 → 写状态。任何异常都写 failed，不覆盖旧基线。"""
    started = datetime.now()
    _write_state({"status": "running", "started_at": started.isoformat(timespec="seconds"),
                  "round": round_hint})
    try:
        data = build_baseline(window_matches=window_matches, league_key=league_key,
                              core_rate=core_rate, round_hint=round_hint, save=True)
        n_ok = sum(1 for v in (data.get("teams") or {}).values() if v.get("available"))
        ok = bool(data.get("saved"))
        _write_state({
            "status": "done" if ok else "failed",
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "round": round_hint, "teams_ok": n_ok,
            "updated_at": data.get("updated_at", ""),
            "error": "" if ok else f"可用队数 {n_ok} 不足，已保留旧基线",
            "trigger": "auto",
        })
        return data
    except Exception as e:
        _write_state({"status": "failed", "started_at": started.isoformat(timespec="seconds"),
                      "finished_at": datetime.now().isoformat(timespec="seconds"),
                      "round": round_hint, "error": f"{type(e).__name__}: {e}",
                      "trigger": "auto"})
        return {}
    finally:
        _lock_release()


def maybe_auto_rebuild(current_round: int | None = None, league_key: str = "BL1",
                       window_matches: int | None = None, core_rate: float | None = None,
                       force: bool = False, baseline: dict | None = None) -> dict:
    """按需在**后台线程**静默重建基线。返回 {started, reason, ...}，不会阻塞调用方。

    reason: disabled | busy（已有重建在跑）| fresh（还没到阈值）| started | no_thread
    """
    c = LINEUP_CFG or {}
    if not force and c.get("auto_rebuild") is False:
        return {"started": False, "reason": "disabled"}

    stale = baseline_staleness(baseline, current_round, cfg=c)
    if not force and not stale.get("stale"):
        return {"started": False, "reason": "fresh", "staleness": stale}

    if not _lock_acquire(float(c.get("rebuild_lock_minutes", 15))):
        return {"started": False, "reason": "busy", "staleness": stale}

    th = threading.Thread(
        target=_rebuild_worker,
        kwargs={"round_hint": current_round, "window_matches": window_matches,
                "core_rate": core_rate, "league_key": league_key},
        daemon=True, name="baseline-rebuild")
    try:
        th.start()
    except Exception as e:
        _lock_release()
        return {"started": False, "reason": "no_thread", "error": str(e), "staleness": stale}
    return {"started": True, "reason": "started", "staleness": stale}


def rebuild_now(current_round: int | None = None, league_key: str = "BL1",
                window_matches: int | None = None, core_rate: float | None = None,
                progress=None) -> dict:
    """同步重建（UI 按钮用）。progress 是可调用对象，接受一段文案字符串。"""
    c = LINEUP_CFG or {}
    if not _lock_acquire(float(c.get("rebuild_lock_minutes", 15))):
        return {"ok": False, "reason": "busy",
                "message": "已有重建任务在跑，请等它结束（约 1–3 分钟）"}

    def _p(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    try:
        _p("正在读取 18 队赛程与历史首发……")
        data = build_baseline(window_matches=window_matches, league_key=league_key,
                              core_rate=core_rate, round_hint=current_round, save=True)
        n_ok = sum(1 for v in (data.get("teams") or {}).values() if v.get("available"))
        ok = bool(data.get("saved"))
        state = {"status": "done" if ok else "failed",
                 "started_at": datetime.now().isoformat(timespec="seconds"),
                 "finished_at": datetime.now().isoformat(timespec="seconds"),
                 "round": current_round, "teams_ok": n_ok,
                 "updated_at": data.get("updated_at", ""),
                 "error": "" if ok else f"可用队数 {n_ok} 不足，已保留旧基线",
                 "trigger": "manual"}
        _write_state(state)
        if ok:
            msg = (f"已更新：{n_ok} 队可用 · 近 {data.get('window_matches')} 场 · "
                   f"核心阈值 {data.get('core_rate')} · {data.get('updated_at')}")
        else:
            msg = f"重建结果只有 {n_ok} 队可用（低于阈值），已保留旧基线未覆盖"
        return {"ok": ok, "teams_ok": n_ok, "message": msg, "data": data}
    except Exception as e:
        _write_state({"status": "failed", "finished_at": datetime.now().isoformat(timespec="seconds"),
                      "error": f"{type(e).__name__}: {e}", "trigger": "manual"})
        return {"ok": False, "reason": "error", "message": f"重建失败：{type(e).__name__}: {e}"}
    finally:
        _lock_release()


if __name__ == "__main__":
    import sys
    argv = [a for a in sys.argv[1:]]
    w = int(argv[0]) if argv and argv[0].isdigit() else None
    rnd = None
    for i, a in enumerate(argv):
        if a in ("--round", "-r") and i + 1 < len(argv) and argv[i + 1].isdigit():
            rnd = int(argv[i + 1])
    d = build_baseline(window_matches=w, round_hint=rnd, verbose=True)
    n_ok = sum(1 for v in d["teams"].values() if v.get("available"))
    print(f"done: {n_ok}/{len(d['teams'])} teams with baseline "
          f"(window={d['window_matches']} core_rate={d['core_rate']} saved={d['saved']})")
