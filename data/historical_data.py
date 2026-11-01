"""
历史数据模块（P1）：
- 读取 football-data.co.uk 德甲(D1) CSV，清洗为模型可用格式。
- 建立 种子code/中文  ↔  football-data英文名 的队名映射。
- 计算逐场滚动特征（近3轮积分、滚动进球/射正、H2H），供 Elo/DC/GBM 训练与预测。

⚠️ 数据说明：football-data.co.uk 德甲 D1 无 xG 列（HG/AG），P1 用"射正(HST/AST)滚动均值"作 xG 代理；
   P2 接入 football-data skill get_event_xg 替换真 xG。
"""
from __future__ import annotations
import os
from collections import deque
import pandas as pd
import numpy as np

HISTORICAL_DIR = os.path.join(os.path.dirname(__file__), "historical")
SEASONS = ["2526", "2425", "2324"]   # 近 3 个完整赛季

# 种子 code → football-data.co.uk 英文队名
CODE2ENG = {
    "BAY": "Bayern Munich", "LEV": "Leverkusen", "DOR": "Dortmund", "RBL": "RB Leipzig",
    "STU": "Stuttgart", "SGE": "Ein Frankfurt", "SCF": "Freiburg", "MAI": "Mainz",
    "BMG": "M'gladbach", "WOB": "Wolfsburg", "SVW": "Werder Bremen", "FCA": "Augsburg",
    "TSG": "Hoffenheim", "FCU": "Union Berlin", "BOC": "Bochum", "STP": "St Pauli",
    "HDH": "Heidenheim", "HSV": "Hamburg",
}
ENG2CODE = {v: k for k, v in CODE2ENG.items()}


def load_seasons(seasons: list[str] | None = None) -> pd.DataFrame:
    """读取并清洗近 3 赛季德甲数据，按时间排序。"""
    seasons = seasons or SEASONS
    frames = []
    for s in seasons:
        path = os.path.join(HISTORICAL_DIR, f"D1_{s}.csv")
        df = pd.read_csv(path, encoding="utf-8-sig")
        df = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
                 "HS", "AS", "HST", "AST"]].copy()
        df["Date"] = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce")
        df["season"] = s
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["Date", "FTHG", "FTAG", "FTR"]).sort_values("Date").reset_index(drop=True)
    out["FTR"] = out["FTR"].str.strip().str.upper()  # H / D / A
    return out


def _series_from_map(series_idx: pd.Index, store: dict) -> pd.Series:
    r = pd.Series(np.nan, index=series_idx)
    for i, v in store.items():
        r.at[i] = v
    return r


def add_rolling_features(df: pd.DataFrame, form_n: int = 3, roll_n: int = 5,
                         h2h_n: int = 5) -> pd.DataFrame:
    """为每场比赛计算滚动特征（本场前状态，杜绝未来泄漏）。"""
    teams = sorted(set(df["HomeTeam"]) | set(df["AwayTeam"]))

    # 每队近期状态队列：points, hg, hst 与出场记录
    state = {t: {"pts": deque(maxlen=form_n), "hg": deque(maxlen=roll_n),
                 "hst": deque(maxlen=roll_n)} for t in teams}
    # H2H：pair -> deque 进球
    h2h_store = {}
    h2h_val = {}

    form_h, form_a = {}, {}
    roll_hg_h, roll_hg_a = {}, {}
    roll_hst_h, roll_hst_a = {}, {}

    for i, row in df.iterrows():
        home, away, ftr = row["HomeTeam"], row["AwayTeam"], row["FTR"]
        hg, ag, hst, ast = row["FTHG"], row["FTAG"], row["HST"], row["AST"]

        # —— 本场前状态（用 deque 现有内容）——
        def _mean(dq: deque) -> float:
            return float(np.mean(list(dq))) if dq else 1.0
        form_h[i] = _mean(state[home]["pts"]);   form_a[i] = _mean(state[away]["pts"])
        roll_hg_h[i] = _mean(state[home]["hg"]); roll_hg_a[i] = _mean(state[away]["hg"])
        roll_hst_h[i] = _mean(state[home]["hst"]); roll_hst_a[i] = _mean(state[away]["hst"])
        key = tuple(sorted((home, away)))
        if key in h2h_store:
            h2h_val[i] = (float(np.mean([x[0] for x in list(h2h_store[key])][-h2h_n:])),
                          float(np.mean([x[1] for x in list(h2h_store[key])][-h2h_n:])))
        else:
            h2h_val[i] = (np.nan, np.nan)

        # —— 用本场更新状态（下一场可见）——
        state[home]["pts"].append({"H": 3, "D": 1, "A": 0}[ftr])
        state[away]["pts"].append({"H": 0, "D": 1, "A": 3}[ftr])
        state[home]["hg"].append(float(hg));   state[away]["hg"].append(float(ag))
        state[home]["hst"].append(float(hst)); state[away]["hst"].append(float(ast))
        h2h_store.setdefault(key, deque(maxlen=10)).append((hg, ag))

    out = df.copy()
    out["form_home"] = _series_from_map(out.index, form_h)
    out["form_away"] = _series_from_map(out.index, form_a)
    out["roll_hg_home"] = _series_from_map(out.index, roll_hg_h)
    out["roll_hg_away"] = _series_from_map(out.index, roll_hg_a)
    out["roll_hst_home"] = _series_from_map(out.index, roll_hst_h)
    out["roll_hst_away"] = _series_from_map(out.index, roll_hst_a)
    out["h2h_hg"] = pd.Series({i: v[0] for i, v in h2h_val.items()})
    out["h2h_ag"] = pd.Series({i: v[1] for i, v in h2h_val.items()})
    return out


def team_last_state(df: pd.DataFrame, team: str, form_n: int = 3, roll_n: int = 5) -> dict:
    """某队截至最新历史的滚动状态（用于待预测比赛）。"""
    sub = df[(df["HomeTeam"] == team) | (df["AwayTeam"] == team)].tail(roll_n)
    pts, hg, hst = [], [], []
    for _, row in sub.iterrows():
        if row["HomeTeam"] == team:
            pts.append({"H": 3, "D": 1, "A": 0}[row["FTR"]])
            hg.append(float(row["FTHG"])); hst.append(float(row["HST"]))
        else:
            pts.append({"H": 0, "D": 1, "A": 3}[row["FTR"]])
            hg.append(float(row["FTAG"])); hst.append(float(row["AST"]))
    return {
        "form": float(np.mean(pts[-form_n:])) if pts else 1.0,
        "roll_hg": float(np.mean(hg)) if hg else 1.0,
        "roll_hst": float(np.mean(hst)) if hst else 1.0,
    }


def luck_map(seasons: list[str] | None = None, window: int = 5) -> dict:
    """运气回归特征（P4·优先级二①）：每队近 window 场"实际进球均值 − xG(射正HST代理)均值"。
    返回 {code: {"roll_hg", "roll_hst", "gap"}}；gap = roll_hst - roll_hg。
    诚实口径：football-data.org 免费层无真 xG，用射正 HST 作代理（历史 CSV D1 提供）。
    """
    df = load_seasons(seasons or ["2526"])
    out = {}
    for code, eng in CODE2ENG.items():
        st = team_last_state(df, eng, roll_n=window)
        hg = st["roll_hg"]; hst = st["roll_hst"]
        out[code] = {"roll_hg": round(hg, 3), "roll_hst": round(hst, 3),
                     "gap": round(hst - hg, 3)}
    return out
