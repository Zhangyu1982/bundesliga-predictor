"""
P2 · 自动抓取伤病候选（方案三：ESPN 直连，国内可达）

背景：海外 API(RapidAPI/TheSportsDB/Transfermarkt/Fotmob) 被国内网络 403/Cloudflare 拦截，
Google/百度/新浪新闻源亦受限。实测 **ESPN 站点接口国内直连可用**（HTTP 200）：
  - /injuries ：结构化伤病记录（当前德甲为空 → 正常）
  - /news     ：德甲新闻，用关键词提取"伤退/缺阵/停赛"候选

工作流（半自动化 + 人审）：
  fetch_all_candidates() → 返回候选列表[{player, team_code, team_name, status, source}]
  → Streamlit 渲染 Checkbox，用户一键勾选确认 → 加入对应场次 injuries 参与确认版复算。

ESPN 无需 token，是当前国内环境唯一直连的稳定伤病源。
"""
from __future__ import annotations
import re
import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1"
ESPN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ESPN displayName → 本项目 code（与 seed.TEAMS / historical.CODE2ENG 统一）
ESPN_NAME2CODE = {
    "Bayern Munich": "BAY", "Mainz": "MAI", "Borussia Mönchengladbach": "BMG",
    "Hamburg SV": "HSV", "Bayer Leverkusen": "LEV", "RB Leipzig": "RBL",
    "SC Freiburg": "SCF", "Werder Bremen": "SVW", "Borussia Dortmund": "DOR",
    "Eintracht Frankfurt": "SGE", "FC Augsburg": "FCA", "VfB Stuttgart": "STU",
    "TSG Hoffenheim": "TSG", "1. FC Union Berlin": "FCU", "FC Cologne": "KOE",
    "Schalke 04": "SCH", "SC Paderborn 07": "PAD", "SV Elversberg": "ELV",
}
# ESPN abbreviation → code（兜底）
ESPN_ABBR2CODE = {"MUN": "BAY", "M05": "MAI", "B04": "LEV", "SCP": "PAD", "S04": "SCH",
                  "FCU": "FCU", "DOR": "DOR", "BMG": "BMG", "SGE": "SGE", "FCA": "FCA",
                  "KOE": "KOE", "HSV": "HSV", "RBL": "RBL", "SCF": "SCF", "ELV": "ELV",
                  "TSG": "TSG", "VFB": "STU", "SVW": "SVW"}

# 伤病关键词（英文 ESPN 新闻）→ 状态映射
INJURY_KW = {
    "ruled out": "key_out", "out for": "key_out", "sidelined": "key_out",
    "injured": "key_out", "misses": "key_out", "miss": "key_out",
    "absence": "key_out", "won't be involved": "key_out",
    "doubtful": "doubtful", "may miss": "doubtful", "fitness doubt": "doubtful",
}
_INJ_RE = re.compile("|".join(re.escape(k) for k in INJURY_KW), re.IGNORECASE)


def espn_team_to_code(name: str = "", abbr: str = "") -> str | None:
    """ESPN 队名/缩写 → code。"""
    if name:
        if name in ESPN_NAME2CODE:
            return ESPN_NAME2CODE[name]
        for k, c in ESPN_NAME2CODE.items():
            if k.lower() in name.lower():
                return c
    if abbr and abbr in ESPN_ABBR2CODE:
        return ESPN_ABBR2CODE[abbr]
    return None


def _get(url: str, timeout: int = 12) -> dict | None:
    try:
        r = requests.get(url, headers=ESPN_UA, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_espn_injuries() -> list[dict]:
    """ESPN 结构化伤病记录 → 候选列表。"""
    d = _get(f"{ESPN_BASE}/injuries")
    if not d:
        return []
    out = []
    for inj in d.get("injuries", []) or []:
        player = (inj.get("player") or {}).get("displayName", "")
        team = inj.get("team") or {}
        code = espn_team_to_code(team.get("displayName", ""), team.get("abbreviation", ""))
        if not player or not code:
            continue
        out.append({
            "player": player, "team_code": code,
            "team_name": team.get("displayName", code),
            "status": "key_out" if inj.get("status") not in ("doubtful",) else "doubtful",
            "source": "ESPN官方伤病",
        })
    return out


# 负向语境：这些词出现在关键词附近 → 非伤病（如"misses penalty"=点球射失）
_NEGATIVE_KW = ("penalty", "corner", "open play", "chance", "cross")


def _is_injury_context(text: str, start: int) -> bool:
    """关键词附近的语境是否真是伤病（排除 point-miss 类误报）。"""
    win = text[max(0, start - 18): start + 34].lower()
    return not any(nk in win for nk in _NEGATIVE_KW)


def _extract_name(sentence: str) -> str:
    """从伤病句子里提取可能的球员名（关键词附近的大写连续词）。"""
    toks = re.findall(r"\b[A-Z][a-zA-Z\u00c0-\u024f']+\b", sentence)
    if not toks:
        return ""
    # 取与关键词最近的一个，作为球员名
    m = _INJ_RE.search(sentence)
    best, best_d = "", 1e9
    for t in toks:
        d = abs(sentence.find(t) - (m.start() if m else 0))
        if d < best_d:
            best, best_d = t, d
    return best


def fetch_espn_news_candidates(limit: int = 25) -> list[dict]:
    """ESPN 德甲新闻 → 关键词提取伤病候选（best-effort，人审兜底）。"""
    d = _get(f"{ESPN_BASE}/news", timeout=12)
    if not d:
        return []
    out = []
    seen = set()
    for art in d.get("articles", []) or []:
        text = f"{art.get('headline', '')}. {art.get('description', '')}"
        for m in _INJ_RE.finditer(text):
            if not _is_injury_context(text, m.start()):
                continue   # 过滤 penalty/角球等误报
            seg = text[max(0, m.start() - 40): m.end() + 40]
            # 需要句子里出现球队名（ESPN 队名）才算候选
            code, team_name = None, ""
            for k, c in ESPN_NAME2CODE.items():
                if k.lower() in text.lower():
                    code, team_name = c, k
                    break
            if code is None:
                continue
            player = _extract_name(seg)
            if not player:
                continue
            key = (player, code)
            if key in seen:
                continue
            seen.add(key)
            kw = m.group(0).lower()
            status = INJURY_KW.get(kw, "key_out")
            out.append({
                "player": player, "team_code": code, "team_name": team_name,
                "status": status, "source": "ESPN新闻提取",
                "context": seg.strip()[:90],
            })
    # 去重：同一球员+球队只留一条
    return out


def fetch_all_candidates() -> dict:
    """合并 ESPN 官方伤病 + 新闻提取候选。返回 {candidates, note, espn_injuries_count}。"""
    structured = fetch_espn_injuries()
    news = fetch_espn_news_candidates()
    seen = set()
    merged = []
    for c in structured + news:
        key = (c.get("player"), c.get("team_code"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)
    note = "ESPN 官方伤病接口当前无记录" if not structured else f"ESPN 官方伤病 {len(structured)} 条"
    if news:
        note += f"；新闻关键词提取 {len(news)} 条候选（请人工复核勾选）"
    return {"candidates": merged, "note": note,
            "espn_injuries_count": len(structured), "news_candidates_count": len(news)}


# =============================================================================
# P1·数据源稳定性：本地 JSON 持久化兜底（用户点名）
# 逻辑流：先尝试 ESPN 实时抓取 -> 成功则更新本地 injuries_backup.json
#         -> 失败/超时自动降级读取本地 JSON 历史缓存，并给出黄色提示"当前使用本地缓存数据"。
# 手动录入的伤病文本也实时保存回本 JSON（app.py 调用 save_backup 完成），刷新不丢失。
# =============================================================================
import os as _os
import json as _json
import datetime as _dt

BACKUP_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "injuries_backup.json")


def load_backup() -> dict:
    """读取本地伤病缓存 JSON。不存在/损坏 → {}（不会抛异常）。"""
    try:
        with open(BACKUP_PATH, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {}


def save_backup(payload: dict) -> None:
    """写本地伤病缓存 JSON（含 ESPN 候选与手动录入）。失败静默（不影响主流程）。"""
    try:
        _os.makedirs(_os.path.dirname(BACKUP_PATH), exist_ok=True)
        with open(BACKUP_PATH, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _espn_reachable() -> bool:
    """探测 ESPN 实时接口是否可达（一次轻量 /injuries 请求）。失败/超时返回 False。
    维护开关：环境变量 INJURY_FORCE_FALLBACK=1 时强制降级（用于测试/屏蔽ESPN场景验证本地兜底）。"""
    if _os.environ.get("INJURY_FORCE_FALLBACK") == "1":
        return False
    return _get(f"{ESPN_BASE}/injuries", timeout=8) is not None


def _live_fetch_candidates() -> dict | None:
    """纯实时抓取（无副作用、可被 Streamlit @st.cache_data 缓存）。
    成功 → {candidates, note, espn_injuries_count, news_candidates_count}；失败 → None。"""
    if not _espn_reachable():
        return None
    return fetch_all_candidates()


def fetch_with_fallback() -> dict:
    """ESPN 实时抓取 + 本地 JSON 持久化兜底（P1·数据源稳定性）。
    成功 → 更新本地 JSON，used_backup=False；
    失败 → 降级读本地 JSON 历史缓存，used_backup=True（供 UI 显示黄色提示）。
    返回：{candidates, note, espn_injuries_count, news_candidates_count, source, used_backup, updated_at}"""
    now = _dt.datetime.now().isoformat(timespec="seconds")
    live = _live_fetch_candidates()
    if live is not None:
        payload = {
            "candidates": live["candidates"], "note": live["note"],
            "espn_injuries_count": live["espn_injuries_count"],
            "news_candidates_count": live["news_candidates_count"],
            "source": "espn", "updated_at": now,
        }
        save_backup(payload)
        return {**payload, "used_backup": False}
    bk = load_backup()
    cands = bk.get("candidates", [])
    used = bool(cands)
    if used:
        note = f"{bk.get('note','')}（⚠️ ESPN 抓取失败，当前使用本地缓存数据 · 缓存于 {bk.get('updated_at','?')}）"
    else:
        note = "ESPN 抓取失败且本地无历史缓存，请使用下方手动录入伤病。"
    return {"candidates": cands, "note": note, "espn_injuries_count": 0,
            "news_candidates_count": 0, "source": "backup", "used_backup": used,
            "updated_at": bk.get("updated_at", now)}


if __name__ == "__main__":
    import json as _j
    res = fetch_with_fallback()
    print(_j.dumps(res, ensure_ascii=False, indent=2))
