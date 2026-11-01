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
from config import (FATIGUE_RULES, ELO_K_MULT, LEAGUE_BASE_ELO, LEAGUE_NAME2CODE,
                    CL_TEAM_ELO)


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


class FatigueEngine:
    """基于真实赛程计算每场德甲比赛的疲劳特征。"""

    def __init__(self, bl1_matches: list[dict], cl_matches: list[dict]):
        # history[team_code] = [(date, competition, was_away, match_id), ...] 按时间升序
        self.history: dict[str, list] = {}
        for m in bl1_matches + cl_matches:
            d = _parse_date(m.get("kickoff", ""))
            if d is None or m.get("status") not in ("FINISHED", "FT"):
                continue
            mid = m.get("match_id")
            for side, key in (("home", "home_code"), ("away", "away_code")):
                code = m.get(key)
                if not code:
                    continue
                self.history.setdefault(code, []).append(
                    (d, m.get("competition", "BL1"), side == "away", mid))
        for lst in self.history.values():
            lst.sort(key=lambda x: x[0])

    def _prev(self, code: str, match_date: datetime, exclude_id=None):
        """最近一场(≠当前场次)信息：{date, competition, was_away} 或 {}。"""
        prev = None
        for (d, comp, away, mid) in self.history.get(code, []):
            if d >= match_date:
                break
            if mid == exclude_id:
                continue
            prev = (d, comp, away)
        if not prev:
            return {}
        return {"date": prev[0], "competition": prev[1], "was_away": prev[2]}

    def rest_days(self, code: str, match_date: datetime, exclude_id=None) -> int | None:
        """距该队最近一场(≠当前场次)的休息天数。"""
        prev = self._prev(code, match_date, exclude_id)
        if not prev:
            return None
        return max(0, (match_date - prev["date"]).days)

    def midweek_cl(self, code: str, match_date: datetime, window: int = 7) -> bool:
        """该队在本场之后 window 天内是否有欧冠（欧冠前置，可能轮换）。"""
        # 由外部传入的 cl_fixtures 判定：简化用 last 的竞争信息不足，改为方法接收 cl 赛程
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
        for m in fixtures:
            kick = _parse_date(m.get("kickoff", ""))
            mid = m.get("match_id")
            home_c, away_c = m.get("home_code"), m.get("away_code")
            rec = {"home": {}, "away": {}, "flags": []}
            if kick:
                for side, code in (("home", home_c), ("away", away_c)):
                    rd = self.rest_days(code, kick, exclude_id=mid)
                    prev = self._prev(code, kick, exclude_id=mid)
                    fatigue = 0.0
                    if rd is not None and rd < cfg["min_rest_days"]:
                        fatigue += (cfg["min_rest_days"] - rd) * cfg["xG_discount_per_day"]
                        if side == "away":
                            fatigue += cfg["cl_away_intensity_bonus"]
                    rec[side] = {
                        "rest_days": rd,
                        "prev_competition": prev.get("competition"),
                        "prev_was_away": bool(prev.get("was_away")),
                        "fatigue_discount": round(min(fatigue, cfg["xG_discount_max"]), 4),
                    }
                # 欧冠前置：本场后 7 天内该队有欧冠 → 可能轮换，折入轮换折扣(实测标定)
                for d, hc, ac in cl_dates:
                    if 0 < (d - kick).days <= cfg["midweek_window_days"]:
                        if hc == home_c:
                            rec["home"]["cl_preceding"] = True
                            rec["home"]["fatigue_discount"] = round(
                                min(rec["home"]["fatigue_discount"] + cfg["cl_rotation_discount"],
                                    cfg["xG_discount_max"]), 4)
                        if ac == away_c:
                            rec["away"]["cl_preceding"] = True
                            rec["away"]["fatigue_discount"] = round(
                                min(rec["away"]["fatigue_discount"] + cfg["cl_rotation_discount"],
                                    cfg["xG_discount_max"]), 4)
            out[mid] = rec
        return out


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
