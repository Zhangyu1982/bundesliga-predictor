"""
Elo 模型：净胜球加权 Elo。
固定参数（网格搜索定值）：K=15、主场优势=50。
"""
from config import ELO_K, HOME_ADV


class EloRating:
    K = ELO_K
    HOME_ADV = HOME_ADV
    BASE = 1500.0

    def __init__(self):
        self.ratings: dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.BASE)

    def _expected(self, r_a: float, r_b: float) -> float:
        return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))

    def update(self, home: str, away: str, g_h: int, g_a: int):
        """净胜球加权更新 [TODO: 按 liveljack 口径实现加权系数]"""
        r_h, r_a = self.rating(home), self.rating(away)
        exp_h = self._expected(r_h + self.HOME_ADV, r_a)
        # goal-margin weighting —— 待按项目源码校准
        margin = g_h - g_a
        w = 1.0 if margin == 0 else (1.5 if margin <= 2 else 2.0)
        self.ratings[home] = r_h + self.K * (int(g_h > g_a) - exp_h) * w
        self.ratings[away] = r_a + self.K * (int(g_a > g_h) - (1 - exp_h)) * w

    def match_prob(self, home: str, away: str) -> dict[str, float]:
        """从 Elo 期望推导三向概率（示意，含主场优势修正）[TODO: 标定映射]"""
        exp_h = self._expected(self.rating(home) + self.HOME_ADV, self.rating(away))
        return {"home_win": exp_h, "draw": 0.0, "away_win": 1 - exp_h}
