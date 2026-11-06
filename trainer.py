"""
P1 训练器：灌入德甲历史数据，拟合 Elo + Dixon-Coles，训练 GBM(XGB+LGBM) 集成。
产出 ModelBundle 并缓存到 artifacts/models.pkl（避免每次运行重复训练）。

三模型融合权重（config.ENSEMBLE_WEIGHTS）：elo=0.15 / poisson=0.40 / xgboost=0.30 / lightgbm=0.15
"""
from __future__ import annotations
import os, pickle, time
import numpy as np
import math
import pandas as pd
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier

from config import ENSEMBLE_WEIGHTS, ELO_K, HOME_ADV, DC_DECAY, DC_RHO
from data.historical_data import (load_seasons, add_rolling_features,
                                  team_last_state, CODE2ENG, ENG2CODE)

ARTIFACT_PATH = os.path.join(os.path.dirname(__file__), "artifacts", "models.pkl")
VERSION = "t1.1"
# 修复：pickle 反序列化不会调用 __init__，旧 artifact 缺字段会在使用时 AttributeError。
# 加版本号，读到的产物版本不符就直接重训。
_BUNDLE_VERSION = 4

FEATURE_COLS = ["elo_diff", "form_home", "form_away", "roll_hg_home", "roll_hg_away",
                "roll_hst_home", "roll_hst_away", "h2h_hg", "h2h_ag"]
FTR_ORDER = ["H", "D", "A"]


def _sigmoid(x: float) -> float:
    x = max(min(x, 30.0), -30.0)
    return 1.0 / (1.0 + np.exp(-x))


class ModelBundle:
    """训练产物：Elo 评分、DC 参数、GBM、特征口径。"""
    def __init__(self):
        self.elo_ratings: dict = {}
        self.elo_k = ELO_K
        self.elo_home_adv = HOME_ADV
        self.dc_attack: dict = {}
        self.dc_defence: dict = {}
        self.dc_rho = DC_RHO
        self.dc_decay = DC_DECAY
        self.dc_mu_home = 1.5
        self.dc_mu_away = 1.3
        self.gbm_xgb = None
        self.gbm_lgb = None
        self.df: pd.DataFrame | None = None      # 最新历史（含滚动特征状态）
        self.metrics: dict = {}
        self.version = _BUNDLE_VERSION            # 修复：旧 artifact 无此字段 → 触发重训

    # ---------- 三向概率：Elo ----------
    def elo_prob(self, home: str, away: str) -> dict:
        rh = self.elo_ratings.get(home, 1500.0)
        ra = self.elo_ratings.get(away, 1500.0)
        diff = (rh + self.elo_home_adv) - ra
        ph = _sigmoid(diff / 400.0)
        pa = _sigmoid(-diff / 400.0)
        dr = 0.25 * (1.0 - abs(ph - pa))          # 平局随实力差缩小而上升（示意口径）
        s = ph + pa + dr
        return {"home_win": ph / s, "draw": dr / s, "away_win": pa / s}

    # ---------- 三向概率：Dixon-Coles ----------
    def dc_lambda(self, home: str, away: str) -> tuple[float, float]:
        lam = self.dc_mu_home * self.dc_attack.get(home, 1.0) * self.dc_defence.get(away, 1.0)
        mu = self.dc_mu_away * self.dc_attack.get(away, 1.0) * self.dc_defence.get(home, 1.0)
        return max(lam, 1e-6), max(mu, 1e-6)

    def dc_score_matrix(self, home: str, away: str, max_goals: int = 8,
                        home_disc: float = 0.0, away_disc: float = 0.0) -> dict:
        # 修复：折扣 >=1 时 λ 落到 1e-6，矩阵几乎全压在 0:0 → 输出趋近"100% 平局"。
        # 上限 0.90，任何上游叠加错误都退化成"强折扣"而不是"平局"。
        home_disc = min(max(float(home_disc or 0.0), 0.0), 0.90)
        away_disc = min(max(float(away_disc or 0.0), 0.0), 0.90)
        a_h = max(self.dc_attack.get(home, 1.0) * (1.0 - home_disc), 1e-6)
        a_a = max(self.dc_attack.get(away, 1.0) * (1.0 - away_disc), 1e-6)
        lam = max(self.dc_mu_home * a_h * self.dc_defence.get(away, 1.0), 1e-6)
        mu = max(self.dc_mu_away * a_a * self.dc_defence.get(home, 1.0), 1e-6)
        m = {}
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = (np.exp(-lam) * lam ** i / math.factorial(i)
                     * np.exp(-mu) * mu ** j / math.factorial(j))
                # 修复：_dc_tau 在 rho>0 且 lam*mu 较大时会变负 → 负概率。
                m[(i, j)] = p * max(self._dc_tau(i, j, lam, mu), 1e-9)
        return m

    def _dc_tau(self, i: int, j: int, lam: float, mu: float) -> float:
        rho = self.dc_rho
        if i == 0 and j == 0: return 1.0 - lam * mu * rho
        if i == 0 and j == 1: return 1.0 + lam * rho
        if i == 1 and j == 0: return 1.0 + mu * rho
        if i == 1 and j == 1: return 1.0 - rho
        return 1.0

    def dc_prob(self, home: str, away: str) -> dict:
        return self.dc_prob_discounted(home, away)

    def dc_prob_discounted(self, home: str, away: str,
                           home_disc: float = 0.0, away_disc: float = 0.0) -> dict:
        """DC 三向概率；可选 R3 xG 折扣（局部作用于本场，不污染共享参数）。"""
        m = self.dc_score_matrix(home, away, 8, home_disc, away_disc)
        hw = sum(p for (i, j), p in m.items() if i > j)
        dr = sum(p for (i, j), p in m.items() if i == j)
        aw = sum(p for (i, j), p in m.items() if i < j)
        s = hw + dr + aw
        return {"home_win": hw / s, "draw": dr / s, "away_win": aw / s}

    # ---------- GBM ----------
    def gbm_prob(self, feats: dict) -> dict:
        X = np.array([[feats[c] for c in FEATURE_COLS]], dtype=float)
        p = np.zeros(3)
        if self.gbm_xgb is not None:
            p += ENSEMBLE_WEIGHTS["xgboost"] * self.gbm_xgb.predict_proba(X)[0]
        if self.gbm_lgb is not None:
            p += ENSEMBLE_WEIGHTS["lightgbm"] * self.gbm_lgb.predict_proba(X)[0]
        if p.sum() == 0:
            p = np.array([1 / 3, 1 / 3, 1 / 3])
        return {"home_win": p[0] / p.sum(), "draw": p[1] / p.sum(), "away_win": p[2] / p.sum()}


# ================= 训练 =================

def train_elo(df: pd.DataFrame, k_grid=(10, 15, 20), ha_grid=(40, 50, 60)) -> ModelBundle:
    """按时间滚动更新 Elo，网格搜索 K / 主场优势，返回最优。"""
    best = None
    for k in k_grid:
        for ha in ha_grid:
            ratings = {}
            brier = 0.0
            n = 0
            for row in df.itertuples():
                rh = ratings.get(row.HomeTeam, 1500.0)
                ra = ratings.get(row.AwayTeam, 1500.0)
                eh = _sigmoid((rh + ha - ra) / 400.0)
                actual = 1.0 if row.FTR == "H" else (0.5 if row.FTR == "D" else 0.0)
                margin = abs(row.FTHG - row.FTAG)
                w = 1.0 if margin <= 1 else (1.5 if margin <= 2 else 2.0)
                ratings[row.HomeTeam] = rh + k * (actual - eh) * w
                ratings[row.AwayTeam] = ra - k * (actual - eh) * w
                brier += (actual - eh) ** 2
                n += 1
            score = brier / n
            if best is None or score < best[0]:
                best = (score, k, ha, ratings)
    bundle = ModelBundle()
    bundle.elo_k, bundle.elo_home_adv = best[1], best[2]
    bundle.elo_ratings = best[3]
    bundle.metrics["elo_brier"] = best[0]
    return bundle


def _weighted_mean(pairs: list[tuple[float, float]]) -> float:
    s = sum(v * w for (v, w) in pairs)
    return s / sum(w for (_, w) in pairs) if pairs else 1.0


def fit_dixon_coles(df: pd.DataFrame, decay_grid=(0.0, 0.01, 0.02, 0.03, 0.05)) -> ModelBundle:
    """拟合 DC：时间衰减加权下的 attack/defence + rho + 衰减系数。"""
    teams = sorted(set(df["HomeTeam"]) | set(df["AwayTeam"]))
    dates = df["Date"]
    span = (dates.max() - dates.min()).days
    mu_home = df["FTHG"].mean()
    mu_away = df["FTAG"].mean()

    best = None
    for decay in decay_grid:
        # 时间权重：近期比赛权重更高
        wts = np.exp(-decay * (dates.max() - dates).dt.days / span)
        attack = {t: 1.0 for t in teams}
        defence = {t: 1.0 for t in teams}
        for _ in range(25):  # 乘性更新（比率法，分母为对手强度加权和）
            for t in teams:
                hm = [(row.FTHG, wts[i], defence[row.AwayTeam])
                      for i, row in enumerate(df.itertuples()) if row.HomeTeam == t]
                num = sum(g * w for g, w, _ in hm)
                den = sum(w * mu_home * dfac for _, w, dfac in hm)
                attack[t] = max(num / den if den > 0 else 1.0, 0.2)
            for t in teams:
                am = [(row.FTAG, wts[i], attack[row.AwayTeam])
                      for i, row in enumerate(df.itertuples()) if row.AwayTeam == t]
                num = sum(g * w for g, w, _ in am)
                den = sum(w * mu_away * afac for _, w, afac in am)
                defence[t] = max(num / den if den > 0 else 1.0, 0.2)
        # 用负对数似然评估（含 rho 粗选）
        nll = 0.0
        for i, row in enumerate(df.itertuples()):
            lam = mu_home * attack[row.HomeTeam] * defence[row.AwayTeam]
            mu = mu_away * attack[row.AwayTeam] * defence[row.HomeTeam]
            nll += -(np.log(np.exp(-lam) * lam ** row.FTHG / math.factorial(row.FTHG))
                     + np.log(np.exp(-mu) * mu ** row.FTAG / math.factorial(row.FTAG))) * wts[i]
        if best is None or nll < best[0]:
            best = (nll, decay, dict(attack), dict(defence))

    bundle = ModelBundle()
    bundle.dc_decay = best[1]
    bundle.dc_attack, bundle.dc_defence = best[2], best[3]
    bundle.dc_mu_home, bundle.dc_mu_away = mu_home, mu_away
    # rho 网格拟合（Dixon-Coles 低比分修正）
    bundle.dc_rho = _fit_rho(df, bundle)
    bundle.metrics["dc_nll"] = best[0]
    return bundle


def _fit_rho(df: pd.DataFrame, bundle: ModelBundle,
             # 修复：DC 的 rho 理论区间在负侧；正 rho 会让 tau(0,0)=1-lam*mu*rho 变负
             grid=(-0.30, -0.25, -0.20, -0.15, -0.10, -0.05, 0.0)) -> float:
    best = (1e18, DC_RHO)
    for rho in grid:
        bundle.dc_rho = rho
        nll = 0.0
        for row in df.itertuples():
            lam, mu = bundle.dc_lambda(row.HomeTeam, row.AwayTeam)
            p = (np.exp(-lam) * lam ** row.FTHG / math.factorial(row.FTHG)
                 * np.exp(-mu) * mu ** row.FTAG / math.factorial(row.FTAG))
            p *= bundle._dc_tau(row.FTHG, row.FTAG, lam, mu)
            if p > 0:
                nll -= np.log(p)
        if nll < best[0]:
            best = (nll, rho)
    return best[1]


def train_gbm(df: pd.DataFrame) -> ModelBundle:
    """用滚动特征训练 XGBoost + LightGBM 三分类（H/D/A）。"""
    rows = df.dropna(subset=FEATURE_COLS + ["FTR"]).copy()
    # Elo 差分作为 GBM 特征（用当前 bundle 评分由外部注入，此处用简单代理：form 差）
    y = rows["FTR"].map({"H": 0, "D": 1, "A": 2}).astype(int).values
    X = rows[FEATURE_COLS].values.astype(float)
    # 补充 elo_diff（以 form 差近似，见 trainer.attach_elo）
    bundle = ModelBundle()
    bundle.gbm_xgb = XGBClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9,
        colsample_bytree=0.9, eval_metric="mlogloss", n_jobs=-1,
        random_state=42)
    bundle.gbm_xgb.fit(X, y)
    bundle.gbm_lgb = LGBMClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9,
        colsample_bytree=0.9, n_jobs=-1, random_state=42, verbose=-1)
    bundle.gbm_lgb.fit(X, y)
    return bundle


def attach_elo_diff(df: pd.DataFrame, bundle: ModelBundle) -> pd.DataFrame:
    """把 Elo 差分写入特征列（供 GBM）。"""
    df = df.copy()
    diffs = []
    for row in df.itertuples():
        rh = bundle.elo_ratings.get(row.HomeTeam, 1500.0)
        ra = bundle.elo_ratings.get(row.AwayTeam, 1500.0)
        diffs.append((rh + bundle.elo_home_adv) - ra)
    df["elo_diff"] = diffs
    return df


def attach_elo_diff_rolling(df: pd.DataFrame, k: float, home_adv: float) -> pd.DataFrame:
    """按时间滚动计算**赛前** Elo 差分（修复训练期目标泄漏）。

    与 attach_elo_diff 的区别：后者用"最终"评分回填历史，属于 look-ahead；
    这里每场先用当前评分算 diff，再按同样规则更新评分，等价于"当时能看到的"。
    """
    df = df.copy()
    ratings: dict = {}
    diffs = []
    for row in df.itertuples():
        rh = ratings.get(row.HomeTeam, 1500.0)
        ra = ratings.get(row.AwayTeam, 1500.0)
        diffs.append((rh + home_adv) - ra)
        eff = _sigmoid((rh + home_adv - ra) / 400.0)
        actual = 1.0 if row.FTR == "H" else (0.5 if row.FTR == "D" else 0.0)
        margin = abs(row.FTHG - row.FTAG)
        w = 1.0 if margin <= 1 else (1.5 if margin <= 2 else 2.0)
        ratings[row.HomeTeam] = rh + k * (actual - eff) * w
        ratings[row.AwayTeam] = ra - k * (actual - eff) * w
    df["elo_diff"] = diffs
    return df


def build_feature_vector(bundle: ModelBundle, home: str, away: str) -> dict:
    """对一场待预测比赛构建 GBM 特征向量。"""
    hs = team_last_state(bundle.df, home)
    aw = team_last_state(bundle.df, away)
    h2h = bundle.df[(bundle.df["HomeTeam"] == home) & (bundle.df["AwayTeam"] == away)]
    h2h_hg = float(h2h["FTHG"].tail(5).mean()) if len(h2h) else 1.0
    h2h_ag = float(h2h["FTAG"].tail(5).mean()) if len(h2h) else 1.0
    rh = bundle.elo_ratings.get(home, 1500.0)
    ra = bundle.elo_ratings.get(away, 1500.0)
    return {
        "elo_diff": (rh + bundle.elo_home_adv) - ra,
        "form_home": hs["form"], "form_away": aw["form"],
        "roll_hg_home": hs["roll_hg"], "roll_hg_away": aw["roll_hg"],
        "roll_hst_home": hs["roll_hst"], "roll_hst_away": aw["roll_hst"],
        "h2h_hg": h2h_hg, "h2h_ag": h2h_ag,
    }


def _normalize_dc(b: ModelBundle) -> ModelBundle:
    """把 DC 的 attack/defence 归一到「平均队 = 1.0」，修默认尺度的语义错位。

    背景：λ = mu_home * attack[主] * defence[客]。拟合出来的 attack 均值约 0.614、
    defence 均值约 1.591，而 `dc_probs` 对**历史里没出现过的球队**（升班马、欧战对手）
    走 `.get(team, 1.0)` 的默认值。于是"默认值 1.0"并不等于"联赛平均"，而是等于
    "攻强守弱"——拜仁(1.26/1.51) 主场打升班马会算出 λ≈2.19 vs 2.20，五五开。

    修法：attack 除以自身均值、defence 乘同一个数，成对做所以 attack*defence 不变——
    λ、比分分布、所有已见球队的预测**完全不变**，只是把 gauge 挪到"1.0 = 平均"。
    幂等：归一后再调用一次 ma≈1.0，结果不变。
    """
    if not getattr(b, "dc_attack", None):
        return b
    ma = sum(b.dc_attack.values()) / len(b.dc_attack)
    if ma > 0:
        b.dc_attack = {k: v / ma for k, v in b.dc_attack.items()}
        b.dc_defence = {k: v * ma for k, v in b.dc_defence.items()}
    return b


def train(force: bool = False, verbose: bool = True) -> ModelBundle:
    """完整训练并缓存。"""
    if not force and os.path.exists(ARTIFACT_PATH):
        # 修复：pickle 不走 __init__，旧产物缺 dc_mu_home 等字段会在使用时 AttributeError。
        # 版本不符/读取异常一律重训，而不是把坏产物返回给上层。
        try:
            with open(ARTIFACT_PATH, "rb") as f:
                _b = pickle.load(f)
            if getattr(_b, "version", 0) == _BUNDLE_VERSION:
                # 缓存产物也要归一：线上读的是 pickle，不是新训练结果，
                # 不在这里调等于"改了没上线"。
                return _normalize_dc(_b)
            print("[trainer] 旧 artifact 版本不符，重新训练")
        except Exception as _e:
            print(f"[trainer] artifact 读取失败（{type(_e).__name__}），重新训练")
    t0 = time.time()
    df = load_seasons()
    df = add_rolling_features(df)
    bundle = ModelBundle()
    bundle.df = df
    bundle.elo_k = ELO_K
    bundle.elo_home_adv = HOME_ADV

    # 1) Elo
    elo_b = train_elo(df)
    bundle.elo_ratings = elo_b.elo_ratings
    bundle.elo_k = elo_b.elo_k
    bundle.elo_home_adv = elo_b.elo_home_adv
    bundle.metrics["elo_brier"] = elo_b.metrics["elo_brier"]

    # 2) DC
    dc_b = fit_dixon_coles(df)
    bundle.dc_attack, bundle.dc_defence = dc_b.dc_attack, dc_b.dc_defence
    bundle.dc_decay, bundle.dc_rho = dc_b.dc_decay, dc_b.dc_rho
    bundle.dc_mu_home, bundle.dc_mu_away = dc_b.dc_mu_home, dc_b.dc_mu_away
    bundle.metrics["dc_nll"] = dc_b.metrics["dc_nll"]

    # 3) GBM（含 Elo 差分特征）
    # 修复：原实现用"跑完全部历史后的最终 Elo"回填每一场特征 → 目标泄漏 →
    # 训练/线上特征分布不一致（GBM 输出离谱的根因之一）。改为按时间滚动取"赛前"评分。
    df_e = attach_elo_diff_rolling(df, bundle.elo_k, bundle.elo_home_adv)
    gb = train_gbm(df_e)
    bundle.gbm_xgb, bundle.gbm_lgb = gb.gbm_xgb, gb.gbm_lgb
    bundle.df = df_e

    # 新训练完也归一（与缓存路径同一口径），落盘的就是归一后的尺度
    bundle = _normalize_dc(bundle)

    os.makedirs(os.path.dirname(ARTIFACT_PATH), exist_ok=True)
    with open(ARTIFACT_PATH, "wb") as f:
        pickle.dump(bundle, f)
    if verbose:
        print(f"训练完成 {time.time()-t0:.1f}s | Elo_brier={bundle.metrics['elo_brier']:.4f} | "
              f"DC_nll={bundle.metrics['dc_nll']:.0f}")
    return bundle


def fused_prob(bundle: ModelBundle, home: str, away: str) -> dict:
    """三模型权重融合。"""
    pe = bundle.elo_prob(home, away)
    pd_ = bundle.dc_prob(home, away)
    feats = build_feature_vector(bundle, home, away)
    pg = bundle.gbm_prob(feats)
    w = ENSEMBLE_WEIGHTS
    fused = {
        "home_win": w["elo"] * pe["home_win"] + w["poisson"] * pd_["home_win"]
                   + (w["xgboost"] + w["lightgbm"]) * pg["home_win"],
        "draw": w["elo"] * pe["draw"] + w["poisson"] * pd_["draw"]
                + (w["xgboost"] + w["lightgbm"]) * pg["draw"],
        "away_win": w["elo"] * pe["away_win"] + w["poisson"] * pd_["away_win"]
                    + (w["xgboost"] + w["lightgbm"]) * pg["away_win"],
    }
    s = sum(fused.values())
    return {k: v / s for k, v in fused.items()}

