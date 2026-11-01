"""
特征工程：Elo、form、H2H、xG、伤病因子、国家队系数 → 供 GBM 集成使用。
"""
from __future__ import annotations


class FeatureEngineer:
    def __init__(self):
        self.team_form: dict[str, list[str]] = {}   # 近 N 场 W/D/L
        self.h2h: dict[tuple[str, str], list[tuple[int, int]]] = {}
        self.xg: dict[str, float] = {}

    def form_vector(self, team: str, n: int = 3) -> list[float]:
        """近 n 场 form 编码（W=3/D=1/L=0），补齐到 n 位。"""
        seq = self.team_form.get(team, [])[:n]
        code = {"W": 3.0, "D": 1.0, "L": 0.0}
        vec = [code.get(r, 0.0) for r in seq]
        return vec + [0.0] * (n - len(vec))

    def build_features(self, home: str, away: str) -> dict[str, float]:
        # TODO: 合并 Elo 差、form、H2H、xG 差、伤病因子、国家队系数 → 向量 X
        return {
            "elo_diff": 0.0,
            "form_home": self.form_vector(home)[0],
            "form_away": self.form_vector(away)[0],
            "xg_diff": self.xg.get(home, 0.0) - self.xg.get(away, 0.0),
            # ... 其余 ~70 维特征（参考 tip-ai）
        }
