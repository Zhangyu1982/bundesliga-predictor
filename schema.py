"""
输出 JSON Schema 定义 + 校验函数（与《开发交付包》§5 对应）
"""
from typing import Any, Dict

MATCH_CARD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "match_id", "home_team", "away_team", "kickoff", "stage", "last_updated",
        "standings", "form_last3", "injuries", "national_team_factor",
        "probabilities", "score_top2", "pick", "handicap_direction",
        "confidence", "risk_note", "model_breakdown",
    ],
    "properties": {
        "match_id": {"type": "string", "pattern": r"^BUN_\d{4}_\d{2}_R\d{2}_[A-Z]+_[A-Z]+$"},
        "stage": {"enum": ["初版", "确认版"]},
        "confidence": {"enum": ["高", "中", "低"]},
        "probabilities": {"$ref": "#/defs/probs"},
        "score_top2": {"type": "array", "minItems": 2, "maxItems": 2},
    },
    "defs": {
        "probs": {
            "type": "object",
            "required": ["home_win", "draw", "away_win"],
        }
    },
}


def validate_probs(probs: Dict[str, float], tol: float = 1e-3) -> None:
    """三项概率求和必须为 1（容差 1e-3）。"""
    keys = ("home_win", "draw", "away_win")
    if not all(k in probs for k in keys):
        raise ValueError(f"probabilities 缺少字段，应为 {keys}")
    s = sum(probs[k] for k in keys)
    if abs(s - 1.0) > tol:
        raise ValueError(f"probabilities 求和={s:.4f}，须为 1.0（容差 {tol}）")


def validate_stage_window(last_updated: str, kickoff: str, window_hours: int) -> bool:
    """确认版强制赛前 window_hours 内复算；否则视为初版并提示。"""
    from datetime import datetime
    fmt = "%Y-%m-%dT%H:%M:%S"
    updated = datetime.fromisoformat(last_updated[:19])
    ko = datetime.fromisoformat(kickoff[:19])
    hours_before = (ko - updated).total_seconds() / 3600.0
    return 0 < hours_before <= window_hours
