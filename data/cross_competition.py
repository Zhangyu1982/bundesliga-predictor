"""
P2 · 跨赛事分析（德甲 + 欧冠，免费层真实可用）

一、疲劳特征引擎（防守端：欧战/杯赛对德甲预测的干扰，进入 GBM 特征库）
  1. 休息天数差 Rest Days Delta：主客队距上一场(任何赛事)的休息天数；< 3 天 → xG 打折。
  2. 欧战客场疲劳：上一场为欧战客场 → 疲劳附加。
  3. 上一场消耗度：free 层无首发/红牌，用赛事权重代理（CL 强度 > BL1）。

二、跨赛事 Elo（进攻端：直接预测欧战）
  1. 跨赛事打通：不同赛事赢球权重不同（K 倍率，欧冠调高）。
  2. 对手为其他联赛球队（拜仁 vs 皇马）：尝试用其本国联赛 Elo，拉不到 → 联赛平均系数兜底。

免费层事实（football-data.org v4）：
  BL1 德甲 200 ✅ | CL 欧冠 200 ✅ | EL 欧联 403(付费) | DFB 德国杯 403(付费) | 欧协 404(不存在)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from config import (FATIGUE_RULES, MOTIVATION_RULES, ELO_K_MULT, LEAGUE_BASE_ELO,
                    LEAGUE_NAME2CODE, CL_TEAM_ELO)


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_extra_time(m: dict) -> bool:
    """是否打满 120 分钟（加时/点球）。football-data.org score.duration ∈ {EXTRA_TIME, PENALTY_SHOOTOUT}，
    或存在 extraTime 比分。"""
    sc = m.get("score") or {}
    dur = (sc.get("duration") or "").upper()
    return dur in ("EXTRA_TIME", "PENALTY_SHOOTOUT") or bool(sc.get("extraTime"))


class FatigueEngine:
    """基于真实赛程（德甲 + 欧冠/欧联/欧会）计算每场德甲比赛的疲劳特征。

    P6 改造：口径由"天"改为"小时"，并纳入 ESPN 接入的欧联(EL)/欧会(UECL)赛程，
    否则周四欧联参赛队（勒沃库森/霍芬海姆/弗赖堡等）的周中消耗完全漏算。
    """

    def __init__(self, bl1_matches: list[dict], euro_matches: list[dict] | None = None):
        """bl1_matches: 德甲全部赛程；euro_matches: 欧战赛程（CL/EL/UECL 合并）。"""
        self.history: dict[str, list] = {}
        self._feed(bl1_matches, is_euro=False)
        self._feed(euro_matches or [], is_euro=True)
        for lst in self.history.values():
            lst.sort(key=lambda x: x[0])

    def _feed(self, matches: list[dict], is_euro: bool) -> None:
        euro_comps = set(FATIGUE_RULES.get("euro_competitions", ["CL", "EL", "UECL"]))
        for m in matches:
            d = _parse_date(m.get("kickoff", ""))
            if d is None or m.get("status") not in ("FINISHED", "FT"):
                continue
            comp = m.get("competition", "BL1")
            euro_flag = is_euro or comp in euro_comps
            mid = m.get("match_id")
            et = bool(m.get("extra_time")) or _is_extra_time(m)
            for side, key in (("home", "home_code"), ("away", "away_code")):
                code = m.get(key)
                if not code:
                    # 欧战对手可能是非德甲队（code=None）→ 跳过该侧即可，不影响德甲队一侧
                    continue
                self.history.setdefault(code, []).append(
                    (d, comp, side == "away", mid, et, euro_flag))

    def _prev(self, code: str, match_date: datetime, exclude_id=None) -> dict:
        """最近一场(≠当前场次)信息：{date, competition, was_away, extra_time, is_euro} 或 {}。"""
        prev = None
        for (d, comp, away, mid, et, is_euro) in self.history.get(code, []):
            if d >= match_date:
                break
            if mid == exclude_id:
                continue
            prev = (d, comp, away, et, is_euro)
        if not prev:
            return {}
        return {"date": prev[0], "competition": prev[1], "was_away": prev[2],
                "extra_time": prev[3], "is_euro": prev[4]}

    def hours_since_prev(self, code: str, match_date: datetime, exclude_id=None) -> float | None:
        """距该队最近一场(≠当前场次)的**小时数**（P6 按小时口径）。"""
        prev = self._prev(code, match_date, exclude_id)
        if not prev:
            return None
        return max(0.0, (match_date - prev["date"]).total_seconds() / 3600.0)

    def rest_days(self, code: str, match_date: datetime, exclude_id=None) -> int | None:
        """兼容旧接口：休息天数（= 小时/24 向下取整）。"""
        h = self.hours_since_prev(code, match_date, exclude_id)
        return None if h is None else int(h // 24)

    @staticmethod
    def bucket_discount(hours: float | None) -> tuple[float, str]:
        """按实测小时分桶取 xG 折扣（config FATIGUE_RULES.hour_bucket_discounts）。"""
        if hours is None:
            return 0.0, ""
        for b in FATIGUE_RULES.get("hour_bucket_discounts", []):
            cap = b.get("max_hours")
            if cap is None or hours < cap:
                return float(b.get("discount", 0.0)), f"<{cap}h" if cap else ">=上限"
        return 0.0, ""

    def midweek_cl(self, code: str, match_date: datetime, window: int = 7) -> bool:
        """该队在本场之后 window 天内是否有欧冠（欧冠前置，可能轮换）。"""
        return False

    def analyze(self, fixtures: list[dict], cl_fixtures: list[dict]) -> dict:
        """返回 {match_id: {home:{...}, away:{...}, flags}} 疲劳特征。
        cl_fixtures：未来欧冠（判定"欧冠前置"）。"""
        out = {}
        cl_dates = []
        for c in cl_fixtures:
            d = _parse_date(c.get("kickoff", ""))
            if d:
                cl_dates.append((d, c.get("home_code"), c.get("away_code")))
        cfg = FATIGUE_RULES
        mot = MOTIVATION_RULES
        cap_base = float(cfg.get("xG_discount_max", 0.04))
        away_bonus = float(cfg.get("cl_away_intensity_bonus", 0.02))
        et_disc = float(cfg.get("extra_time_discount", 0.06))
        et_hours = float(cfg.get("extra_time_recovery_hours", 72))
        for m in fixtures:
            kick = _parse_date(m.get("kickoff", ""))
            mid = m.get("match_id")
            home_c, away_c = m.get("home_code"), m.get("away_code")
            rec = {"home": {}, "away": {}, "flags": []}
            if kick:
                for side, code in (("home", home_c), ("away", away_c)):
                    hours = self.hours_since_prev(code, kick, exclude_id=mid)
                    prev = self._prev(code, kick, exclude_id=mid)
                    bucket_disc, bucket_tag = self.bucket_discount(hours)
                    fatigue = bucket_disc
                    # 上一场为欧战客场 → 附加消耗
                    if prev.get("is_euro") and prev.get("was_away") and hours is not None \
                            and hours < float(cfg.get("min_rest_hours", 96)):
                        fatigue += away_bonus
                    prev_extra = bool(prev.get("extra_time"))
                    if prev_extra and hours is not None and hours <= et_hours:
                        fatigue += et_disc
                    rec[side] = {
                        "rest_hours": round(hours, 1) if hours is not None else None,
                        "rest_days": (int(hours // 24) if hours is not None else None),
                        "prev_competition": prev.get("competition"),
                        "prev_competition_zh": _comp_zh(prev.get("competition")),
                        "prev_is_euro": bool(prev.get("is_euro")),
                        "prev_was_away": bool(prev.get("was_away")),
                        "prev_extra_time": prev_extra,
                        "bucket_tag": bucket_tag,
                        "_fatigue": fatigue,
                    }
                # 欧冠前置：本场后 7 天内该队有欧冠 → 可能轮换，折入轮换折扣
                stnd = m.get("standings") or {}
                hr = (stnd.get("home") or {}).get("rank")
                ar = (stnd.get("away") or {}).get("rank")
                for d, hc, ac in cl_dates:
                    gap_days = (d - kick).days
                    if 0 < gap_days <= cfg["midweek_window_days"]:
                        if hc == home_c:
                            rec["home"]["cl_preceding"] = True
                            rec["home"]["_fatigue"] += cfg["cl_rotation_discount"]
                        if ac == away_c:
                            rec["away"]["cl_preceding"] = True
                            rec["away"]["_fatigue"] += cfg["cl_rotation_discount"]
                    # 战意规则（P4·优先级二②，用户定死）：欧冠前 cl_league_window_days 天内打联赛，
                    # 且联赛对手排名比自己低 opponent_rank_gap 名以上（弱旅无需死拼）→ 直接扣 xg_deduction。
                    if 0 < gap_days <= mot["cl_league_window_days"]:
                        if hc == home_c and hr and ar and (ar - hr) >= mot["opponent_rank_gap"]:
                            rec["home"]["cl_motivation_penalty"] = mot["xg_deduction"]
                        if ac == away_c and hr and ar and (hr - ar) >= mot["opponent_rank_gap"]:
                            rec["away"]["cl_motivation_penalty"] = mot["xg_deduction"]
                # 统一封顶：常规 xG_discount_max；若上一场为加时赛 → 上限放宽一个 extra_time_discount
                for side in ("home", "away"):
                    f = rec[side]
                    cap = cap_base + (et_disc if f.get("prev_extra_time") else 0.0)
                    f["fatigue_discount"] = round(min(f.get("_fatigue", 0.0), cap), 4)
                    f.pop("_fatigue", None)
            out[mid] = rec
        return out


def _comp_zh(comp: str | None) -> str:
    return {"BL1": "德甲", "CL": "欧冠", "EL": "欧联", "UECL": "欧会",
            "DFB": "德国杯", None: "无", "": "无"}.get(comp, comp or "无")


class CrossElo:
    """跨赛事 Elo 评估：Bundesliga 队用模型 Elo，其他联赛对手用联赛平均系数兜底。"""

    def __init__(self, model_elo: dict | None = None):
        self.model_elo = model_elo or {}   # {code: elo}（Bundesliga 已训）

    @staticmethod
    def league_of(team_name: str) -> str:
        """由对手队名推断其本国联赛 code（用于平均系数兜底）。"""
        n = (team_name or "").lower()
        for kw, code in LEAGUE_NAME2CODE.items():
            if kw.lower() in n:
                return code
        # 常见球队名关键字
        for club, code in {
            "real madrid": "PD", "barcelona": "PD", "atletico": "PD",
            "manchester": "PL", "liverpool": "PL", "arsenal": "PL", "chelsea": "PL",
            "tottenham": "PL", "newcastle": "PL", "aston villa": "PL",
            "inter": "SA", "juventus": "SA", "milan": "SA", "napoli": "SA", "roma": "SA",
            "psg": "FL1", "lille": "FL1", "monaco": "FL1", "lyon": "FL1", "marseille": "FL1",
            "ajax": "DED", "psv": "DED", "feyenoord": "DED",
            "benfica": "PPL", "porto": "PPL", "sporting": "PPL",
            "galatasaray": "SUI", "fenerbahce": "SUI",
        }.items():
            if club in n:
                return code
        return "_default"

    def rating_for(self, code: str, team_name: str) -> float:
        """返回某队用于跨赛事预测的 Elo。
        优先序：模型 Elo（Bundesliga）→ 精选豪门基准 Elo → 联赛平均系数兜底。"""
        if code in self.model_elo:
            return float(self.model_elo[code])
        for club, elo in CL_TEAM_ELO.items():
            if club.lower() in (team_name or "").lower():
                return float(elo)
        return float(LEAGUE_BASE_ELO.get(self.league_of(team_name), LEAGUE_BASE_ELO["_default"]))

    @staticmethod
    def k_multiplier(competition: str) -> float:
        """不同赛事赢球权重：欧冠调高 K。"""
        return ELO_K_MULT.get(competition, 1.0)
