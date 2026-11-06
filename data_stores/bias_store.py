"""
R5 连续跟踪 · 记录库（SQLite）：存储"历史比赛结果 vs 预测结果"逐场比对记录。
对应 R5_continuous_tracking：自第 9 轮起连续编号，输出末尾附上一轮偏差摘要。
记录来源：scripts/backtest.py 的 walk-forward 回测写入；生产运行也可增量写入。
"""
from __future__ import annotations
import sqlite3, os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "bias.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bias_predictions (
    season      TEXT NOT NULL,          -- 如 2526
    round_no    INTEGER NOT NULL,       -- 赛季内轮次
    match_id    TEXT PRIMARY KEY,
    home        TEXT, away TEXT,
    actual_hg   INTEGER, actual_ag INTEGER, actual_ftr TEXT,  -- 实际结果
    pred_home   REAL, pred_draw REAL, pred_away REAL,          -- 预测概率
    pred_pick   TEXT,                   -- 预测首选(主队/客队/平)
    pred_hit    INTEGER,                -- 预测首选是否命中实际结果 1/0
    elo_dir     TEXT, dc_dir TEXT,      -- Elo 与 DC 各自首选方向
    divergence  INTEGER,                -- Elo 与 DC 方向是否分歧 1/0
    bias_streak INTEGER                 -- 该队赛前连续同方向偏差轮数
)
"""


class BiasStore:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        """显式释放 SQLite 连接。Windows 上连接不关会让文件无法删除/替换（WinError 32）。"""
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        self.close()

    def write_records(self, records: list[dict]) -> int:
        """批量写入比对记录（upsert）。"""
        sql = ("INSERT OR REPLACE INTO bias_predictions "
               "(season,round_no,match_id,home,away,actual_hg,actual_ag,actual_ftr,"
               "pred_home,pred_draw,pred_away,pred_pick,pred_hit,elo_dir,dc_dir,"
               "divergence,bias_streak) VALUES "
               "(:season,:round_no,:match_id,:home,:away,:actual_hg,:actual_ag,:actual_ftr,"
               ":pred_home,:pred_draw,:pred_away,:pred_pick,:pred_hit,:elo_dir,:dc_dir,"
               ":divergence,:bias_streak)")
        self.conn.executemany(sql, records)
        self.conn.commit()
        return len(records)

    def query(self, season: str | None = None, round_no: int | None = None) -> list[dict]:
        sql = "SELECT * FROM bias_predictions WHERE 1=1"
        params = []
        if season:
            sql += " AND season=?"; params.append(season)
        if round_no:
            sql += " AND round_no=?"; params.append(round_no)
        rows = self.conn.execute(sql, params).fetchall()
        cols = [c[0] for c in self.conn.execute("SELECT * FROM bias_predictions").description]
        return [dict(zip(cols, r)) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM bias_predictions").fetchone()[0]

    def prev_round_bias_summary(self, round_no: int, season: str | None = None) -> dict:
        """上一轮偏差摘要（供 R5 输出末尾附注）。"""
        if season is None:
            seasons = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT season FROM bias_predictions ORDER BY season DESC")]
            if not seasons:
                return {"prev_round": None, "records": 0, "hit_rate": None, "note": "无历史记录"}
            season = seasons[0]
        prev = self.conn.execute(
            "SELECT * FROM bias_predictions WHERE season=? AND round_no=?", (season, round_no)).fetchall()
        if not prev:
            return {"prev_round": round_no, "records": 0, "hit_rate": None,
                    "note": f"第{round_no}轮无预测记录"}
        hit = sum(1 for r in prev if r[8] == 1)  # pred_hit
        return {"prev_round": round_no, "records": len(prev),
                "hit_rate": round(hit / len(prev), 3), "note": "详见偏差回溯记录"}

    def calibration_stats(self) -> dict:
        """记录库整体比对统计：预测命中率、Elo/DC 分歧率。"""
        n = self.count()
        if n == 0:
            return {"records": 0}
        hit = self.conn.execute(
            "SELECT COUNT(*) FROM bias_predictions WHERE pred_hit=1").fetchone()[0]
        div = self.conn.execute(
            "SELECT COUNT(*) FROM bias_predictions WHERE divergence=1").fetchone()[0]
        return {"records": n,
                "prediction_hit_rate": round(hit / n, 3),
                "elo_dc_divergence_rate": round(div / n, 3)}


def record_predictions_backtest(records: list[dict], db_path: str = DB_PATH) -> int:
    return BiasStore(db_path).write_records(records)
