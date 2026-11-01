"""真实赛果持久化（入库 JSON）——让 R1 偏差记录与 R5 连续跟踪在公网端真正生效。

背景：data_stores/bias.db 为运行时 SQLite（.gitignore 忽略 *.db，不入库；Streamlit
Cloud 每次重部署会重建为空库）。因此真实比赛结果同步写入本文件
data/live_results.json（静态、入库、随 main 推送），公网端启动时读取并可
与 bias.db 历史记录合并，保证真实赛果不因云端重建而丢失。

用法：
  from data.live_results import load_live_results, save_live_results, merge_with_bias
  lr = load_live_results()                      # 读全部真实赛果
  save_live_results(lr + [rec])                 # 追加并写回
  merged = merge_with_bias(lr)                  # bias.db 历史 + 真实赛果合并
"""
from __future__ import annotations
import datetime
import json
import os

LIVE_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "live_results.json")


def load_live_results(path: str | None = None) -> list[dict]:
    """读取真实赛果记录；文件缺失/损坏时静默返回空列表（不阻塞应用）。"""
    p = path or LIVE_RESULTS_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", []) or []
        return [r for r in records if isinstance(r, dict)]
    except Exception:
        return []


def save_live_results(records: list[dict], path: str | None = None,
                      updated_at: str | None = None) -> str:
    """把真实赛果写入 live_results.json（入库）；返回写入路径。"""
    p = path or LIVE_RESULTS_PATH
    payload = {
        "updated_at": updated_at
        or datetime.datetime.now().isoformat(timespec="seconds"),
        "records": records,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p


def merge_with_bias(records: list[dict], bias_db_path: str | None = None) -> list[dict]:
    """把 bias.db 的历史记录与本文件真实赛果合并（按 match_id 去重，真实优先）。

    bias.db 为回测 walk-forward 记录（本地有值、云端重建为空）；合并后供 R1/R5
    统计展示，真实赛果（records）优先覆盖同名回测行。
    """
    merged: list[dict] = []
    seen: set = set()
    # 真实赛果优先
    for r in records or []:
        mid = r.get("match_id")
        if mid not in seen:
            merged.append(r)
            seen.add(mid)
    # bias.db 历史
    try:
        from data_stores.bias_store import BiasStore
        for r in BiasStore(bias_db_path).query():
            mid = r.get("match_id")
            if mid not in seen:
                merged.append(r)
                seen.add(mid)
    except Exception:
        pass  # bias.db 不可读时仅保留真实赛果，不中断
    return merged
