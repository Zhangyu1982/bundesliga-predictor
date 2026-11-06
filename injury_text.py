"""手动伤病文本解析（从 app.py 抽出，便于单测与复用）。

格式：每行 `队名: 缺阵球员,存疑球员?`；重要性用 `球员[核心|轮换|替补]` 标注。
中文输入法常打出全角标点（：，、？［］），必须先归一，否则整行被静默丢弃 ——
这正是"录入了伤病但结果完全没变"的根因。
"""

from config import IMPORTANCE_LEVELS

# 全角 → 半角
_FULLWIDTH_MAP = (
    ("：", ":"), ("，", ","), ("、", ","), ("；", ";"), ("？", "?"),
    ("［", "["), ("］", "]"), ("　", " "),
)


def normalize(text: str) -> str:
    """全角标点归一 + 行首 markdown 符号剥离。"""
    t = text or ""
    for a, b in _FULLWIDTH_MAP:
        t = t.replace(a, b)
    return "\n".join(line.strip().lstrip("-*·").strip() for line in t.splitlines())


def parse_importance(player: str) -> tuple[str, str]:
    """解析球员条目里的重要性标注 [核心]/[轮换]/[替补]，无标注默认核心。"""
    for tag in IMPORTANCE_LEVELS:
        marker = f"[{tag}]"
        if marker in player:
            return player.replace(marker, "").strip(), tag
    return player.strip(), "核心"


def _code_by_key() -> dict:
    from data.seed_data import TEAMS
    from data.api_client import FDO_NAME2CODE, EXTRA_TEAMS_ZH
    cbk = {zh: c for c, zh in {**TEAMS, **EXTRA_TEAMS_ZH}.items()}
    for raw, c in FDO_NAME2CODE.items():
        cbk[raw] = c
        cbk.setdefault(raw.replace(" ", ""), c)
        cbk.setdefault(raw.split()[-1], c)   # "Bayern München" → "München"
    return cbk


def parse_manual_injuries(text: str) -> dict | None:
    """→ {code: {"key_out": [{name,importance}], "doubtful": [...]}}；空文本 → None。"""
    norm = normalize(text)
    if not norm.strip():
        return None
    cbk = _code_by_key()
    out: dict = {}
    for line in norm.splitlines():
        if not line or ":" not in line:
            continue
        team_part, players = line.split(":", 1)
        team_part = team_part.strip()
        code = None
        for key, c in cbk.items():
            # 双向子串匹配：支持全名与简称/关键词
            if key and (key in team_part or team_part in key):
                code = c
                break
        if code is None:
            continue
        key_out, doubtful = [], []
        for p in players.split(","):
            p = p.strip()
            if not p:
                continue
            # 先拆重要性标注再判存疑，才能正确处理 "乙?[轮换]" 这种问号在前标注的写法
            name, imp = parse_importance(p)
            if name.endswith("?") or name.startswith("存疑"):
                name = name.strip("?存疑 ").strip()
                doubtful.append({"name": name, "importance": imp})
            else:
                key_out.append({"name": name, "importance": imp})
        out[code] = {"key_out": key_out, "doubtful": doubtful}
    return out or None


def unparsed_lines(text: str) -> list[str]:
    """无法解析的行（缺冒号或队名未识别），供 UI 明确报错，避免"录了等于没录"。"""
    norm = normalize(text)
    if not norm.strip():
        return []
    keys = list(_code_by_key().keys())
    bad = []
    for line in norm.splitlines():
        if not line:
            continue
        if ":" not in line:
            bad.append(line)
            continue
        team_part = line.split(":", 1)[0].strip()
        if not any(k and (k in team_part or team_part in k) for k in keys):
            bad.append(line)
    return bad
