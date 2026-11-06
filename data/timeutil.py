"""统一时间口径：**存储一律 UTC 带时区**，**展示一律北京时间**。

这个模块存在的唯一原因：同一屏曾出现「数据最后拉取时间 15:46:03」与
「常规主力库 更新于 22:26」，整整差 8 小时。前者是容器（UTC）里用
`time.strftime()` 生成的、且不带时区标记；后者是开发机（+08:00）写进
`data/baseline_xi.json` 的。两套语义混在一页上，用户没法判断谁新谁旧。

规则：
1. 新写入的时间戳一律 `utc_now_iso()`，形如 `2026-11-06T15:46:03+00:00`，
   带明确的时区标记；
2. 展示一律 `fmt_local()`，转成本地时间并带「（北京时间）」；
3. 读历史值用 `parse_any()`：带标记就听标记的，**不带标记的按本地 +08:00 补齐**
   —— 历史值就是开发机本地时间写的，当成 UTC 解析会整体偏 8 小时。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 中国无夏令时，固定 +08:00 与 Asia/Shanghai 等价，且不依赖 tzdata
# （Windows 上 ZoneInfo("Asia/Shanghai") 在没装 tzdata 包时会抛 ZoneInfoNotFoundError）。
LOCAL_TZ = timezone(timedelta(hours=8))
LOCAL_TZ_HINT = "北京时间"

_FALLBACK_FMTS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def utc_now_iso() -> str:
    """当前 UTC 时间，ISO8601 带 +00:00，秒级精度（存储统一用这个）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_any(raw, assume_tz=LOCAL_TZ):
    """把各种历史格式解析成 aware datetime；解析不了返回 None。

    支持的形态：带 +00:00 / Z / +08:00 的 ISO、naive ISO、`%Y-%m-%d %H:%M[:SS]`。
    无时区的一律按 `assume_tz`（默认本地 +08:00）补齐。
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        d = raw
    else:
        s = str(raw).strip()
        if not s or s in ("—", "?", "None", "-"):
            return None
        s = s.replace("Z", "+00:00")
        d = None
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            for fmt in _FALLBACK_FMTS:
                try:
                    d = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if d is None:
            return None
    return d if d.tzinfo is not None else d.replace(tzinfo=assume_tz)


def to_local(raw, assume_tz=LOCAL_TZ):
    d = parse_any(raw, assume_tz=assume_tz)
    return d.astimezone(LOCAL_TZ) if d is not None else None


def fmt_local(raw, assume_tz=LOCAL_TZ, fmt="%Y-%m-%d %H:%M:%S", hint=True) -> str:
    """展示用：转本地时间；hint=True 时补「（北京时间）」。解析不了原样返回。"""
    d = to_local(raw, assume_tz=assume_tz)
    if d is None:
        return "—" if raw in (None, "") else str(raw)
    return d.strftime(fmt) + (f"（{LOCAL_TZ_HINT}）" if hint else "")


def age_hours(raw, now=None, assume_tz=LOCAL_TZ):
    """距今多少小时（负数=未来）；解析不了返回 None。"""
    d = parse_any(raw, assume_tz=assume_tz)
    if d is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - d).total_seconds() / 3600.0
