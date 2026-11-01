"""
Dixon-Coles 模型：双泊松 + 低位比分修正 ρ，时间衰减 0.03。
移植参数：ρ=-0.20（tip-ai）；衰减 0.03（liveljack）。
"""
import math
from config import DC_DECAY, DC_RHO


def _pois_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


class DixonColes:
    DECAY = DC_DECAY
    RHO = DC_RHO

    def __init__(self):
        # 每队攻/守强度（λ 主、μ 客）待从历史数据标定 [TODO]
        self.attack: dict[str, float] = {}
        self.defence: dict[str, float] = {}

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        """λ_home, μ_away —— 需要联赛平均进球率标定 [TODO]"""
        lam = max(self.attack.get(home, 1.0) * self.defence.get(away, 1.0), 1e-6)
        mu = max(self.attack.get(away, 1.0) * self.defence.get(home, 1.0), 1e-6)
        return lam, mu

    def dc_correction(self, i: int, j: int, lam: float, mu: float) -> float:
        """Dixon-Coles 低位比分修正 tau(i,j) [TODO: 实现 rho 修正公式]"""
        if i == 0 and j == 0:
            return 1.0 - lam * mu * self.RHO
        if i == 0 and j == 1:
            return 1.0 + lam * self.RHO
        if i == 1 and j == 0:
            return 1.0 + mu * self.RHO
        if i == 1 and j == 1:
            return 1.0 - self.RHO
        return 1.0

    def score_matrix(self, home: str, away: str, max_goals: int = 8) -> dict[tuple[int, int], float]:
        lam, mu = self.expected_goals(home, away)
        m: dict[tuple[int, int], float] = {}
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = _pois_pmf(lam, i) * _pois_pmf(mu, j) * self.dc_correction(i, j, lam, mu)
                if p > 0:
                    m[(i, j)] = p
        return m

    def win_probs(self, home: str, away: str) -> dict[str, float]:
        m = self.score_matrix(home, away)
        hw = sum(p for (i, j), p in m.items() if i > j)
        dr = sum(p for (i, j), p in m.items() if i == j)
        aw = sum(p for (i, j), p in m.items() if i < j)
        return {"home_win": hw, "draw": dr, "away_win": aw}
