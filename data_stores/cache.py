"""
本地缓存：SQLite / parquet，按 match_id 命中，避免重复拉取与超出配额。
P2 新增：
  1) 每日限流保护（daily-capped cache）：同一自然日对同一资源不重复调用 API；
     记录每日调用计数，超过配额返回 None（触发调用方回退缓存/降级），不再请求。
  2) 动态剩余额度识别（P2，用户点名要求）：从 Response Headers 读取
     `x-requests-available-minute` / `X-RequestCounter-Reset`（football-data.org 免费层
     10 req/**min**），把"本分钟窗口剩余可调用数 + 重置时刻"持久化到 quota_state；
     发起新调用前若窗口未重置且剩余为 0，直接判 ProviderUnavailableError，避免 403。
"""
from __future__ import annotations
import sqlite3, json, os, time
from datetime import datetime


class Cache:
    def __init__(self, db_path: str = "./data_stores/cache.db"):
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        # check_same_thread=False：Streamlit 会在不同线程重跑脚本，复用同一连接需跨线程许可
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "match_id TEXT PRIMARY KEY, key TEXT, payload TEXT, ts TEXT)")
        # 每日配额记账：date + resource -> count
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_quota ("
            "date TEXT, resource TEXT, count INT, PRIMARY KEY(date, resource))")
        # 动态剩余额度：resource -> 当前分钟窗口剩余可调用数 + 重置时刻(epoch秒)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS quota_state ("
            "resource TEXT PRIMARY KEY, remaining INT, reset_at REAL, updated_at TEXT)")
        self.conn.commit()

    # ---------- 基础键值缓存 ----------
    def get(self, match_id: str, key: str):
        row = self.conn.execute(
            "SELECT payload FROM cache WHERE match_id=? AND key=?",
            (match_id, key)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, match_id: str, key: str, payload) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache (match_id,key,payload,ts) VALUES (?,?,?,?)",
            (match_id, key, json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat()))
        self.conn.commit()

    # ---------- 每日限流保护 ----------
    def get_daily(self, resource: str, date: str | None = None) -> dict | None:
        """当日缓存命中：同一自然日同一资源不重复调 API。"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT payload FROM cache WHERE match_id=? AND key=?",
            (f"daily:{date}", f"{resource}")).fetchone()
        return json.loads(row[0]) if row else None

    def set_daily(self, resource: str, payload, date: str | None = None) -> None:
        date = date or datetime.now().strftime("%Y-%m-%d")
        self.set(f"daily:{date}", resource, payload)

    def daily_usage(self, resource: str, date: str | None = None) -> int:
        """该资源当日已调用次数。"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT count FROM daily_quota WHERE date=? AND resource=?",
            (date, resource)).fetchone()
        return row[0] if row else 0

    def can_call(self, resource: str, daily_limit: int, date: str | None = None) -> bool:
        """当日配额保护：未超限才允许发起真实 API 调用。"""
        return self.daily_usage(resource, date) < daily_limit

    def record_call(self, resource: str, date: str | None = None) -> None:
        """记录一次真实 API 调用（用于配额计数）。"""
        date = date or datetime.now().strftime("%Y-%m-%d")
        self.conn.execute(
            "INSERT INTO daily_quota(date,resource,count) VALUES(?,?,1) "
            "ON CONFLICT(date,resource) DO UPDATE SET count=count+1",
            (date, resource))
        self.conn.commit()

    # ---------- P2 动态剩余额度识别（防 403） ----------
    def update_quota_from_headers(self, resource: str, headers: dict) -> None:
        """从 Response Headers 读取剩余额度与重置时刻并持久化。
        football-data.org 免费层：`x-requests-available-minute`(剩余/分钟) +
        `X-RequestCounter-Reset`(距重置的秒数)。也兼容日级 `X-RequestCounter-Daily`。
        """
        rem = headers.get("x-requests-available-minute")
        reset = headers.get("X-RequestCounter-Reset")
        if rem is None:
            # 兜底：日级剩余（部分接口提供 X-RequestCounter-Daily）
            rem = headers.get("X-RequestCounter-Daily")
            if rem is None:
                return   # 无限流头信息，跳过（仍由每日配额兜底）
        now = time.time()
        try:
            remaining = int(rem)
        except (TypeError, ValueError):
            return
        reset_at = (now + float(reset)) if reset is not None else None
        self.conn.execute(
            "INSERT OR REPLACE INTO quota_state(resource,remaining,reset_at,updated_at) "
            "VALUES(?,?,?,?)",
            (resource, remaining, reset_at,
             datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def dynamic_quota(self, resource: str) -> tuple[int | None, float | None]:
        """返回 (本窗口剩余可调用数, 重置时刻epoch)。无记录 → (None,None)。"""
        row = self.conn.execute(
            "SELECT remaining, reset_at FROM quota_state WHERE resource=?",
            (resource,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def can_call_dynamic(self, resource: str, daily_limit: int,
                         date: str | None = None) -> bool:
        """双重配额判断：每日上限 + 动态分钟窗口剩余。
        分钟窗口未重置且剩余为 0 → 阻止调用（防 403）；无头信息/已重置 → 放行。
        """
        if not self.can_call(resource, daily_limit, date):
            return False
        remaining, reset_at = self.dynamic_quota(resource)
        if remaining is None:
            return True   # 尚无限流头信息，由每日配额兜底
        if reset_at is not None and time.time() >= reset_at:
            return True   # 窗口已重置，服务端会刷新剩余额度
        return remaining > 0
