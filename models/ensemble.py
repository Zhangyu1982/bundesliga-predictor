"""
GBM 集成：XGBoost + LightGBM（德甲数据量>2000 场时启用 CatBoost）+ 权重融合。
权重配比（liveljack）：elo=0.15 / poisson=0.40 / xgboost=0.30 / lightgbm=0.15。
"""
from __future__ import annotations
from config import ENSEMBLE_WEIGHTS, CATBOOST_MIN_SAMPLES


class GBMEnsemble:
    def __init__(self):
        self.xgb = None          # xgboost.XGBClassifier  [TODO: 安装依赖]
        self.lgb = None          # lightgbm.LGBMClassifier [TODO]
        self.cat = None          # catboost（可选）        [TODO]
        self.meta = None         # Ridge 元学习器（移植 tip-ai）[TODO]

    def fit(self, X, y, n_samples: int):
        self.xgb = ...  # 训练 XGBoost
        self.lgb = ...  # 训练 LightGBM
        if n_samples > CATBOOST_MIN_SAMPLES:
            self.cat = ...  # 德甲数据充足时才启用 CatBoost，防过拟合
        self.meta = ...  # Ridge 元学习器融合三模型

    def predict_proba(self, X) -> dict[str, float]:
        # 各基模型概率 → 元学习器 → 归一化三向概率 [TODO]
        ...


class EnsembleFusion:
    """三模型框架（Elo ∪ Poisson ∪ GBM）最终融合。"""
    def __init__(self, elo_rating, dc_model, gbm: GBMEnsemble):
        self.elo = elo_rating
        self.dc = dc_model
        self.gbm = gbm

    def fused_probs(self, home: str, away: str, X=None) -> dict[str, float]:
        p_elo = self.elo.match_prob(home, away)
        p_dc = self.dc.win_probs(home, away)
        p_gbm = self.gbm.predict_proba(X) if X is not None else None
        if p_gbm is None:                 # GBM 未训练时退化为 DC（骨架兜底）
            p_gbm = p_dc
        w = ENSEMBLE_WEIGHTS
        return {
            "home_win": w["elo"] * p_elo["home_win"] + w["poisson"] * p_dc["home_win"] + (w["xgboost"] + w["lightgbm"]) * p_gbm["home_win"],
            "draw":     w["elo"] * p_elo["draw"] + w["poisson"] * p_dc["draw"] + (w["xgboost"] + w["lightgbm"]) * p_gbm["draw"],
            "away_win": w["elo"] * p_elo["away_win"] + w["poisson"] * p_dc["away_win"] + (w["xgboost"] + w["lightgbm"]) * p_gbm["away_win"],
        }
