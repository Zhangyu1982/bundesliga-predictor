"""
数据层 · Provider 抽象（P0 + P2 准备）

职责：
- 拉取"轮次对阵 + 积分 + 近期状态 + 伤病"，统一为标准化 match dict。
- 返回数据时效元数据（fetched_at / cached / source），供 UI"数据最后拉取时间"标签。
- 提供"是否具备实时伤病数据"（realtime_injury_available），用于确认版闸门。
- P2：API 拉取 + 每日限流保护缓存 + get_event_xg 接口。

Provider 一览：
- LiveFootballDataProvider：football-data.org v4（需 FOOTBALL_DATA_TOKEN，免费层限流）。
- ApiFootballProvider：API-Football v3（需 RAPIDAPI_KEY；提供伤病 + get_event_xg）。
- ZeroKeySeedProvider：零密钥种子数据（离线，标注"缓存/示例"），默认兜底。

限流保护：Cache 每日配额记账；同一自然日同一资源命中缓存不再调 API，超配额返回缓存/降级，绝不抛 500。
get_event_xg：API-Football /fixtures/statistics 返回 Expected Goals；无 token/源不支持时返回 None（pipeline 自动回退射正代理）。
"""
from __future__ import annotations
import os, time
import requests
from config import DATA_SOURCES, LEAGUE, SEASON
from data.seed_data import get_round_fixtures, seed_freshness, TEAMS
from data.historical_data import load_seasons, team_last_state, CODE2ENG
from data_stores.cache import Cache


# ---------- P2 队名归一化映射层：football-data.org 真实队名 → 种子 code ----------
# 真实 API 名（如 "1. FSV Mainz 05"/"FC Bayern München"）≠ historical CODE2ENG 英文名
# （"Mainz"/"Bayern Munich"）≠ seed TEAMS 中文。新增本层把真实名归一化到 code。
FDO_NAME2CODE = {
    "1. FSV Mainz 05": "MAI", "FC Bayern München": "BAY", "Borussia Mönchengladbach": "BMG",
    "Hamburger SV": "HSV", "SC Freiburg": "SCF", "Bayer 04 Leverkusen": "LEV",
    "RB Leipzig": "RBL", "Borussia Dortmund": "DOR", "Eintracht Frankfurt": "SGE",
    "SV Werder Bremen": "SVW", "VfB Stuttgart": "STU", "FC Augsburg": "FCA",
    "TSG 1899 Hoffenheim": "TSG", "1. FC Union Berlin": "FCU",
    "VfL Wolfsburg": "WOB", "VfL Bochum": "BOC", "FC St. Pauli": "STP",
    "1. FC Heidenheim 1846": "HDH",
    # 2026-27 升班马/回归队（不在种子 TEAMS，无历史参数，模型给默认）
    "1. FC Köln": "KOE", "SC Paderborn 07": "PAD", "SV 07 Elversberg": "ELV", "FC Schalke 04": "SCH",
}
# 升班马等新增队中文名（用于卡片显示）
EXTRA_TEAMS_ZH = {"KOE": "科隆", "PAD": "帕德博恩", "ELV": "埃尔弗斯贝格", "SCH": "沙尔克04"}


def fdo_team_to_code(raw_name: str) -> str | None:
    """真实 API 队名 → code；失败返回 None（调用方用 raw_name 兜底）。"""
    if not raw_name:
        return None
    return FDO_NAME2CODE.get(raw_name) or FDO_NAME2CODE.get(raw_name.strip())


def team_zh(code: str, raw_name: str = "") -> str:
    """code → 中文显示名（seed TEAMS 优先，升班马用 EXTRA_TEAMS_ZH，兜底 raw_name）。"""
    if code in TEAMS:
        return TEAMS[code]
    if code in EXTRA_TEAMS_ZH:
        return EXTRA_TEAMS_ZH[code]
    return raw_name or code


class ProviderUnavailableError(Exception):
    """实时 Provider 不可用（无 token / 配额超限 / 网络受限）。"""


class _BaseProvider:
    name = "base"
    realtime_injury_available = False

    def fetch_round(self, round_no: int) -> list[dict]:
        raise NotImplementedError

    def freshness(self) -> dict:
        raise NotImplementedError

    def get_event_xg(self, match_id: str) -> dict | None:
        """P2 接口：拉取指定比赛的 Expected Goals（home/away）。
        返回 None 表示该源不提供 xG（pipeline 回退射正代理）。"""
        return None


class _CachedProvider(_BaseProvider):
    """带每日限流保护缓存的实时 Provider 基类。"""
    resource_prefix = "api"

    def __init__(self):
        self.cache = Cache()
        self._fetched_at = None

    def _get_cached(self, resource: str) -> dict | None:
        return self.cache.get_daily(f"{self.resource_prefix}:{resource}")

    def _set_cached(self, resource: str, payload) -> None:
        self.cache.set_daily(f"{self.resource_prefix}:{resource}", payload)

    def _call(self, resource: str, url: str, headers: dict, params: dict,
              daily_limit: int) -> dict:
        """限流保护：命中缓存直接用；每日配额/动态分钟窗口超限则抛错（调用方降级，不抛 500）。
        P2：动态识别 Response Headers 的剩余额度（x-requests-available-minute），防 403。"""
        cached = self._get_cached(resource)
        if cached is not None:
            return cached | {"cached": True}
        if not self.cache.can_call_dynamic(resource, daily_limit):
            raise ProviderUnavailableError(
                f"{self.name}: 配额受限（日额度{daily_limit}或分钟窗口剩余为0），"
                f"请等待重置或降级到缓存/种子源")
        self.cache.record_call(resource)
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        self.cache.update_quota_from_headers(resource, r.headers)  # 动态识别剩余额度
        payload = r.json()
        self._set_cached(resource, payload)
        self._fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return payload | {"cached": False}


class LiveFootballDataProvider(_CachedProvider):
    """football-data.org v4（Free 层，需 X-Auth-Token）。"""
    name = "football-data.org 实时"
    # football-data.org 免费层不提供伤病/阵容接口 → 确认版复算依赖方案C手动录入
    realtime_injury_available = False
    resource_prefix = "fdo"

    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.base = DATA_SOURCES["football_data_org"]["base_url"]
        self._daily_limit = DATA_SOURCES["football_data_org"]["free_limit_per_day"]

    @classmethod
    def from_env(cls) -> "LiveFootballDataProvider | None":
        token = os.environ.get("FOOTBALL_DATA_TOKEN", "")
        if not token:
            return None
        return cls(token)

    def _hdr(self) -> dict:
        return {"X-Auth-Token": self.token}

    def fetch_round(self, round_no: int) -> list[dict]:
        if not self.token:
            raise ProviderUnavailableError("football-data.org 需要 X-Auth-Token")
        return self.fetch_competition_round(LEAGUE, round_no)

    def fetch_competition_round(self, comp: str, round_no: int) -> list[dict]:
        """按赛事拉取某一轮（德甲 BL1 / 欧冠 CL）。免费层 EL/DFB 403 → 抛 ProviderUnavailableError。"""
        if not self.token:
            raise ProviderUnavailableError("football-data.org 需要 X-Auth-Token")
        if comp not in ("BL1", "CL"):
            raise ProviderUnavailableError(
                f"football-data.org 免费层无 {comp}（EL/DFB=403付费；欧协404不存在），仅 BL1/CL 可用")
        url = f"{self.base}/competitions/{comp}/matches"
        try:
            data = self._call(f"round:{comp}:{round_no}", url, self._hdr(),
                              {"matchday": round_no}, self._daily_limit)
        except (ProviderUnavailableError, requests.HTTPError, requests.RequestException):
            if comp == LEAGUE:
                return self._fallback_fixtures(round_no, data_source=self.name)
            raise ProviderUnavailableError(f"{comp} 赛程拉取失败/配额受限")
        if comp == LEAGUE:
            standings_map = self._fetch_standings_map()
            return self._parse_fdo_matches(data.get("matches", []) or [], standings_map)
        return self._parse_fdo_matches(data.get("matches", []) or [], {}, competition=comp)

    def fetch_competition_matches(self, comp: str) -> list[dict]:
        """拉取某赛事全部比赛（疲劳特征用：最近比赛日期/欧战客场）。失败返回 []。"""
        if comp not in ("BL1", "CL"):
            return []
        url = f"{self.base}/competitions/{comp}/matches"
        try:
            data = self._call(f"all:{comp}", url, self._hdr(), {}, self._daily_limit)
        except (ProviderUnavailableError, requests.HTTPError, requests.RequestException):
            return []
        return self._parse_fdo_matches(data.get("matches", []) or [], {}, competition=comp,
                                       with_kickoff=True)

    def _fetch_standings_map(self) -> dict:
        """拉取 BL1 积分榜 → {code: (rank, pts)}；失败返回空（卡片不阻塞）。"""
        url = f"{self.base}/competitions/{LEAGUE}/standings"
        try:
            data = self._call("standings:all", url, self._hdr(), {}, self._daily_limit)
        except (ProviderUnavailableError, requests.HTTPError, requests.RequestException):
            # 免费层 10req/min：429 限流时积分榜缺失不应拖垮整轮预测
            return {}
        try:
            table = data.get("standings", [{}])[0].get("table", [])
            out = {}
            for t in table:
                code = fdo_team_to_code(t.get("team", {}).get("name", ""))
                if code:
                    out[code] = (t.get("position"), t.get("points"))
            return out
        except Exception:
            return {}

    def get_event_xg(self, match_id: str) -> dict | None:
        """football-data.org v4 不直接提供 xG → 返回 None，回退射正代理。"""
        return None

    def _parse_fdo_matches(self, matches: list, standings_map: dict,
                           competition: str = LEAGUE, with_kickoff: bool = False) -> list[dict]:
        out = []
        form_cache = self._form_cache() if competition == LEAGUE else {}
        for m in matches:
            home_raw = (m.get("homeTeam") or {}).get("name", "")
            away_raw = (m.get("awayTeam") or {}).get("name", "")
            home_code = fdo_team_to_code(home_raw) or home_raw
            away_code = fdo_team_to_code(away_raw) or away_raw
            m_id = m.get("id")
            st = {}
            if standings_map:
                if home_code in standings_map:
                    st["home"] = {"rank": standings_map[home_code][0],
                                  "pts": standings_map[home_code][1]}
                if away_code in standings_map:
                    st["away"] = {"rank": standings_map[away_code][0],
                                  "pts": standings_map[away_code][1]}
            out.append({
                "match_id": m_id,
                "competition": competition,
                "home_team": team_zh(home_code, home_raw), "away_team": team_zh(away_code, away_raw),
                "home_code": home_code, "away_code": away_code,
                "kickoff": m.get("utcDate", ""),
                "status": m.get("status", ""),
                "score": m.get("score", {}),
                "stage": "初版",
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "standings": st,
                "form_last3": {"home": form_cache.get(home_code, ["—"]),
                               "away": form_cache.get(away_code, ["—"])},
                "injuries": {"home_key_out": [], "away_key_out": [],
                             "home_doubtful": [], "away_doubtful": []},
                "confidence": "中",
            })
        return out

    def _form_cache(self) -> dict:
        """真实近期状态（历史 CSV 最新 3 场 W/D/L），归一化到 code。football-data.org 不返回 form，以此兜底。"""
        try:
            df = load_seasons()
            out = {}
            for code, eng in CODE2ENG.items():
                st = team_last_state(df, eng)
                # form 为浮点均值，转为可读 W/D/L 近似（>=2 → W, ~1 → D, <=0 → L）
                f = st["form"]
                out[code] = ["W" if f >= 2 else ("D" if f >= 1 else "L")]
            return out
        except Exception:
            return {}

    def _fallback_fixtures(self, round_no: int, data_source: str) -> list[dict]:
        """实时配额/故障时回退种子赛程（标注缓存）。"""
        return get_round_fixtures(round_no)

    def freshness(self) -> dict:
        return {"fetched_at": self._fetched_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": self.name, "cached": self._fetched_at is None}


class ApiFootballProvider(_CachedProvider):
    """API-Football v3（需 RAPIDAPI_KEY）。提供伤病 + get_event_xg。"""
    name = "API-Football 实时"
    realtime_injury_available = True
    resource_prefix = "api_fb"

    def __init__(self, rapidapi_key: str):
        super().__init__()
        self.key = rapidapi_key
        self.base = DATA_SOURCES["api_football"]["base_url"]
        self.host = DATA_SOURCES["api_football"]["host"]
        self.league = DATA_SOURCES["api_football"]["league_id"]
        self.season = SEASON
        self._daily_limit = DATA_SOURCES["api_football"]["daily_limit"]

    @classmethod
    def from_env(cls) -> "ApiFootballProvider | None":
        key = os.environ.get("RAPIDAPI_KEY", "")
        if not key:
            return None
        return cls(key)

    def _hdr(self) -> dict:
        return {"x-rapidapi-key": self.key, "x-rapidapi-host": self.host}

    def fetch_round(self, round_no: int) -> list[dict]:
        url = f"{self.base}/fixtures"
        params = {"league": self.league, "season": self.season,
                  "round": f"Regular Season - {round_no}"}
        try:
            data = self._call(f"round:{round_no}", url, self._hdr(), params, self._daily_limit)
        except ProviderUnavailableError:
            return get_round_fixtures(round_no)
        return self._parse_fixtures(data.get("response", []))

    def fetch_injuries(self, round_no: int | None = None) -> list[dict]:
        """伤病/停赛（api-football /injuries?league&season，可选 round）。"""
        url = f"{self.base}/injuries"
        params = {"league": self.league, "season": self.season}
        if round_no:
            params["round"] = f"Regular Season - {round_no}"
        data = self._call(f"injuries:{round_no or 'all'}", url, self._hdr(), params, self._daily_limit)
        return data.get("response", [])

    def get_event_xg(self, match_id: str) -> dict | None:
        """P2：拉取该场 Expected Goals（home/away）。
        api-football /fixtures/statistics 含 Expected Goals；失败/无返回 → None。
        """
        url = f"{self.base}/fixtures/statistics"
        params = {"fixture": match_id}
        try:
            data = self._call(f"xg:{match_id}", url, self._hdr(), params, self._daily_limit)
        except ProviderUnavailableError:
            return None
        response = data.get("response", [])
        if not response:
            return None
        for team_stats in response:
            side = team_stats.get("team", {}).get("id")
            for s in team_stats.get("statistics", []):
                if s.get("type", "").lower() in ("expected goals", "xg"):
                    return {"home_xg": s["value"] if side else None,
                            "away_xg": None, "side_id": side}
        return None

    def _parse_fixtures(self, response: list) -> list[dict]:
        out = []
        for f in response:
            home = (f.get("teams") or {}).get("home", {})
            away = (f.get("teams") or {}).get("away", {})
            out.append({
                "match_id": str(f.get("fixture", {}).get("id", "")),
                "home_team": home.get("name", ""), "away_team": away.get("name", ""),
                "kickoff": f.get("fixture", {}).get("date", ""),
                "injuries": {"home_key_out": [], "away_key_out": []},
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            })
        return out

    def freshness(self) -> dict:
        return {"fetched_at": self._fetched_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": self.name, "cached": self._fetched_at is None}


class ZeroKeySeedProvider(_BaseProvider):
    """零密钥种子数据（离线，标注缓存/示例）。默认数据源。"""
    name = "零密钥·种子(示例)"
    realtime_injury_available = False   # 无实时伤病 → 确认版闸门应拦截

    def fetch_round(self, round_no: int) -> list[dict]:
        return get_round_fixtures(round_no)

    def fetch_competition_matches(self, comp: str) -> list[dict]:
        """种子源没有真实赛程：返回空，由调用方（本周总览）降级提示，而不是抛 AttributeError。"""
        return []

    def get_event_xg(self, match_id: str) -> dict | None:
        return None   # 种子无 xG

    def freshness(self) -> dict:
        return seed_freshness()


def resolve_provider() -> _BaseProvider:
    """自动选择 Provider（优先级）：API-Football > football-data.org > 零密钥种子。"""
    for p in (ApiFootballProvider.from_env(), LiveFootballDataProvider.from_env()):
        if p is not None:
            return p
    return ZeroKeySeedProvider()
