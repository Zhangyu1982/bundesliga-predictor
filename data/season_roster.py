"""赛季名单：每个赛季一份 JSON（data/season_roster/<season>.json），按 season 读取。

为什么要按赛季读而不是写死一份：
- 每个赛季都有升班/降级，名单本身就是数据，不该硬编码在代码里；
- 读不到本季名单时必须「拒绝出卡」，不能悄悄用上一季名单顶替——
  否则旧季的 18 队会被当成合法赛程，整轮跑错却看着正常。

文件格式：
{
  "season": "2627",
  "teams": {"BAY": "拜仁慕尼黑", ...},          # 恰好 18 队
  "source": "升降级依据（谁升谁降）",
  "updated_at": "YYYY-MM-DD"
}
"""
from __future__ import annotations

import json
import os

ROSTER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "season_roster")


def roster_path(season: str) -> str:
    return os.path.join(ROSTER_DIR, f"{season}.json")


def load_roster(season: str | None = None) -> dict[str, str] | None:
    """读某季名单；season 缺省取当前赛季。文件不存在/格式不合法/不是 18 队 → 返回 None。"""
    if season is None:
        from data.timeutil import season_label
        season = season_label()
    path = roster_path(str(season))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    teams = data.get("teams") or {}
    if not isinstance(teams, dict) or len(teams) != 18:
        return None
    return {str(k): str(v) for k, v in teams.items()}


def roster_codes(season: str | None = None) -> set[str]:
    return set(load_roster(season) or {})


# 兼容别名：现有代码/测试引用的是 2627 常量，值一律来自上面的 JSON（单一来源）
SEASON_ROSTER_2627 = load_roster("2627") or {}
SEASON = "2627"
assert len(SEASON_ROSTER_2627) == 18, "data/season_roster/2627.json 缺失或不是 18 队"
