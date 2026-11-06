"""
德甲 2026-27 · 零密钥种子数据集（P0 数据链路占位源）

⚠️ 重要声明：
- 本模块是 P0 阶段"零密钥数据源"的具体实现（离线种子数据，清晰标注"缓存/示例"）。
- 实时数据源 football-data.org 免费层必须持有 X-Auth-Token（本环境无 token，实测 403）；
  football-data skill / API-Football MCP 在本环境未提供。因此 P0 用本种子数据跑通链路，
  标定/上线时替换为 live provider 即可（接口层已抽象，见 api_client.py）。
- 对阵/积分/近期状态/伤病为合理的示例数据，用于验证管道与 UI，非官方实时数据。
"""
from __future__ import annotations
from datetime import datetime, timezone

from data.timeutil import utc_now_iso  # 时区口径：存 UTC 带标记，展示转本地

# 队伍：code -> 中文名
TEAMS = {
    "BAY": "拜仁慕尼黑", "LEV": "勒沃库森", "DOR": "多特蒙德", "RBL": "莱比锡红牛",
    "STU": "斯图加特", "SGE": "法兰克福", "SCF": "弗赖堡", "MAI": "美因茨",
    "BMG": "门兴格拉德巴赫", "WOB": "沃尔夫斯堡", "SVW": "云达不莱梅", "FCA": "奥格斯堡",
    "TSG": "霍芬海姆", "FCU": "柏林联合", "BOC": "波鸿", "STP": "圣保利",
    "HDH": "海登海姆", "HSV": "汉堡",
}

# 第 8 轮后积分榜：code -> (rank, pts)
STANDINGS = {
    "BAY": (1, 18), "LEV": (2, 17), "RBL": (3, 15), "DOR": (4, 14),
    "SGE": (5, 13), "STU": (6, 13), "SCF": (7, 12), "MAI": (8, 10),
    "BMG": (9, 10), "WOB": (10, 9), "SVW": (11, 8), "FCA": (12, 8),
    "TSG": (13, 7), "FCU": (14, 7), "BOC": (15, 6), "STP": (16, 5),
    "HDH": (17, 4), "HSV": (18, 3),
}

# 近 3 场状态：code -> [W/D/L]
FORM3 = {
    "BAY": ["D", "W", "W"], "LEV": ["W", "W", "D"], "DOR": ["W", "L", "W"],
    "RBL": ["W", "W", "L"], "STU": ["D", "W", "L"], "SGE": ["W", "D", "D"],
    "SCF": ["L", "W", "W"], "MAI": ["D", "W", "L"], "BMG": ["L", "D", "W"],
    "WOB": ["D", "L", "W"], "SVW": ["L", "L", "D"], "FCA": ["W", "L", "D"],
    "TSG": ["L", "W", "L"], "FCU": ["D", "L", "L"], "BOC": ["L", "W", "L"],
    "STP": ["L", "D", "L"], "HDH": ["L", "L", "W"], "HSV": ["D", "L", "L"],
}

# 伤病：code -> {"key_out":[...], "doubtful":[...]}
INJURIES = {
    "BAY": {"key_out": ["Musiala", "Luis Díaz", "Neuer"], "doubtful": ["Gnabry"]},
    "LEV": {"key_out": ["Wirtz"], "doubtful": []},
    "DOR": {"key_out": [], "doubtful": ["Brandt"]},
    "RBL": {"key_out": [], "doubtful": []},
    "STU": {"key_out": ["Undav"], "doubtful": []},
    "SGE": {"key_out": [], "doubtful": ["Marmoush"]},
    "SCF": {"key_out": [], "doubtful": []},
    "MAI": {"key_out": [], "doubtful": ["Burkardt"]},
    "BMG": {"key_out": [], "doubtful": []},
    "WOB": {"key_out": [], "doubtful": []},
    "SVW": {"key_out": [], "doubtful": []},
    "FCA": {"key_out": [], "doubtful": []},
    "TSG": {"key_out": [], "doubtful": []},
    "FCU": {"key_out": [], "doubtful": []},
    "BOC": {"key_out": [], "doubtful": []},
    "STP": {"key_out": [], "doubtful": []},
    "HDH": {"key_out": [], "doubtful": []},
    "HSV": {"key_out": [], "doubtful": []},
}

# 每轮对阵：round -> [(home_code, away_code, kickoff_iso)]
# 注：round 9 包含文档示例场次（美因茨 vs 拜仁，2026-11-07）；round 12 为"已排期未开赛、无伤病"的未来轮次（容错测试用）
FIXTURES = {
    8: [
        ("SGE", "LEV", "2026-11-01T20:30:00+08:00"), ("DOR", "SCF", "2026-11-01T22:30:00+08:00"),
        ("RBL", "STU", "2026-11-01T00:30:00+08:00"), ("MAI", "SVW", "2026-11-02T00:30:00+08:00"),
        ("BMG", "FCA", "2026-11-02T20:30:00+08:00"), ("WOB", "TSG", "2026-11-02T22:30:00+08:00"),
        ("FCU", "BOC", "2026-11-03T00:30:00+08:00"), ("HDH", "STP", "2026-11-03T20:30:00+08:00"),
        ("HSV", "BAY", "2026-11-03T22:30:00+08:00"),
    ],
    9: [
        ("MAI", "BAY", "2026-11-07T22:30:00+08:00"), ("LEV", "DOR", "2026-11-08T03:30:00+08:00"),
        ("RBL", "SGE", "2026-11-08T22:30:00+08:00"), ("STU", "SCF", "2026-11-08T00:30:00+08:00"),
        ("BMG", "WOB", "2026-11-09T03:30:00+08:00"), ("SVW", "FCA", "2026-11-09T22:30:00+08:00"),
        ("TSG", "FCU", "2026-11-09T00:30:00+08:00"), ("BOC", "STP", "2026-11-10T03:30:00+08:00"),
        ("HDH", "HSV", "2026-11-10T22:30:00+08:00"),
    ],
    10: [
        ("BAY", "LEV", "2026-11-21T22:30:00+08:00"), ("DOR", "RBL", "2026-11-22T03:30:00+08:00"),
        ("SGE", "MAI", "2026-11-22T22:30:00+08:00"), ("SCF", "BMG", "2026-11-22T00:30:00+08:00"),
        ("WOB", "STU", "2026-11-23T03:30:00+08:00"), ("FCA", "TSG", "2026-11-23T22:30:00+08:00"),
        ("FCU", "SVW", "2026-11-23T00:30:00+08:00"), ("STP", "HDH", "2026-11-24T03:30:00+08:00"),
        ("HSV", "BOC", "2026-11-24T22:30:00+08:00"),
    ],
    11: [
        ("LEV", "BAY", "2026-11-28T22:30:00+08:00"), ("RBL", "SCF", "2026-11-29T03:30:00+08:00"),
        ("STU", "BMG", "2026-11-29T22:30:00+08:00"), ("MAI", "DOR", "2026-11-29T00:30:00+08:00"),
        ("WOB", "SGE", "2026-11-30T03:30:00+08:00"), ("SVW", "TSG", "2026-11-30T22:30:00+08:00"),
        ("FCA", "FCU", "2026-11-30T00:30:00+08:00"), ("BOC", "HDH", "2026-12-01T03:30:00+08:00"),
        ("STP", "HSV", "2026-12-01T22:30:00+08:00"),
    ],
    # 未来轮次（已排期未开赛，且无伤病数据 → 用于降级容错测试）
    12: [
        ("BAY", "RBL", "2026-12-05T22:30:00+08:00"), ("DOR", "STU", "2026-12-06T03:30:00+08:00"),
        ("SGE", "LEV", "2026-12-06T22:30:00+08:00"), ("SCF", "MAI", "2026-12-06T00:30:00+08:00"),
        ("BMG", "SVW", "2026-12-07T03:30:00+08:00"), ("WOB", "FCA", "2026-12-07T22:30:00+08:00"),
        ("TSG", "BOC", "2026-12-07T00:30:00+08:00"), ("FCU", "HDH", "2026-12-08T03:30:00+08:00"),
        ("STP", "HSV", "2026-12-08T22:30:00+08:00"),
    ],
}

# 未来轮次标记：这些轮次已排期但未开赛（无伤病数据）—— 用于容错测试
FUTURE_NO_INJURY_ROUNDS = {12}


def _match(home: str, away: str, kickoff: str, round_no: int) -> dict:
    h_rank, h_pts = STANDINGS[home]
    a_rank, a_pts = STANDINGS[away]
    inj_h = INJURIES.get(home, {"key_out": [], "doubtful": []})
    inj_a = INJURIES.get(away, {"key_out": [], "doubtful": []})
    # 未来轮次（未开赛）强制无伤病数据，用于降级容错测试
    if round_no in FUTURE_NO_INJURY_ROUNDS:
        inj_h = inj_a = {"key_out": [], "doubtful": []}
    match_id = f"BUN_2026_27_R{round_no:02d}_{home}_{away}"
    return {
        "match_id": match_id,
        "home_team": TEAMS[home], "away_team": TEAMS[away],
        "home_code": home, "away_code": away,
        "kickoff": kickoff,
        "stage": "初版",
        # 修复：原来是 utcnow 的字符串却硬标 +08:00（差 8 小时的根因之一）
        "last_updated": utc_now_iso(),
        "standings": {"home": {"rank": h_rank, "pts": h_pts},
                      "away": {"rank": a_rank, "pts": a_pts}},
        "form_last3": {"home": FORM3[home], "away": FORM3[away]},
        "injuries": {"home_key_out": inj_h["key_out"], "away_key_out": inj_a["key_out"],
                     "home_doubtful": inj_h["doubtful"], "away_doubtful": inj_a["doubtful"]},
        "confidence": "中",   # 模型层初版置信度占位
    }


def get_round_fixtures(round_no: int) -> list[dict]:
    """返回指定轮次的标准化比赛列表；无数据的轮次返回空列表（由调用方降级）。"""
    pairs = FIXTURES.get(round_no)
    if not pairs:
        return []
    return [_match(h, a, k, round_no) for (h, a, k) in pairs]


def seed_freshness() -> dict:
    """种子源时效元数据：标注为缓存/示例，非实时。"""
    return {
        "fetched_at": utc_now_iso(),
        "source": "零密钥·种子(示例)",
        "cached": True,
        "note": "离线种子数据，非实时；替换 live provider 后此标注由实时接口覆盖",
    }
