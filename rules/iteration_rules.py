"""
固定规范 v1.0 / 技术需求 v1.1 · 迭代规则 R1–R5 → 代码配置项 + 日志规则
规则名锁定不变，阈值全部取自 config.RULES（唯一入口），禁止硬编码。

R1 偏差记录        → 每轮赛后记录实际比分 vs 首选比分/胜平负倾向偏差；连续 consecutive_rounds 轮同方向偏差触发该队 Elo 重校准
R2 伤病升级        → 初版标记"中/低置信度"的场次，赛前 48h 用最终伤病名单复算升级为"确认版"后作为最终输出
R3 国家队系数窗口  → 仅 FIFA 窗口后首轮启用；国脚出场>180min 或跨洲旅行>5000km 的球队下调预期进球 8%–15%
R4 分歧场次标注    → Elo 与 Dixon-Coles 胜平负方向不一致时，置信度强制降一档并在风险备注中明示
R5 连续跟踪        → 从第 9 轮起每轮记录编号连续累加；每次输出末尾附上一轮偏差摘要

每个日志条目统一结构：{rule, match_id, event, ts, payload}，按 RULE_LOGS 写独立 JSONL。
"""
from __future__ import annotations
import json, os
from datetime import datetime
from config import LOG_DIR, RULES, RULE_LOGS

# 置信度等级（从低到高），用于"降一档"
_CONF_ORDER = ["低", "中", "高"]


class RuleLogger:
    """统一日志规则：按规则写独立 JSONL 文件。"""
    def __init__(self, root: str = LOG_DIR):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def log(self, rule_key: str, match_id: str, event: str, payload: dict):
        if rule_key not in RULE_LOGS:
            return
        path = os.path.join(self.root, RULE_LOGS[rule_key])
        rec = {"rule": rule_key, "match_id": match_id, "event": event,
               "ts": datetime.now().isoformat(), "payload": payload}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read(self, rule_key: str) -> list[dict]:
        path = os.path.join(self.root, RULE_LOGS.get(rule_key, ""))
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))
        return rows


class IterationRules:
    def __init__(self, log_root: str = LOG_DIR):
        self.cfg = RULES
        self.logger = RuleLogger(log_root)
        self._streak: dict[str, int] = {}      # team -> 连续同方向偏差轮数
        self._last_dir: dict[str, str] = {}    # team -> 上一轮偏差方向
        self._round_seq: dict[str, int] = {}   # team -> 连续轮编号（R5，自 start_round 起）
        self._last_round: dict[str, int] = {}  # team -> 上一跟踪轮次

    # ============ R1 偏差记录 + 连续重校准 ============
    def record_deviation(self, match_id: str, team: str, pick_dir: str,
                         actual_dir: str, score_top2: list, actual_score: str,
                         confidence: str, round_no: int) -> str:
        """记录该队偏差，更新连续同方向偏差计数；达阈值触发 Elo 重校准日志。返回 deviation_type。"""
        hit = actual_score in [s["score"] for s in score_top2]
        same_dir = pick_dir == actual_dir
        dev_type = "比分命中" if hit else ("方向命中" if same_dir else "方向偏差")

        # 连续同方向偏差计数
        n = self.cfg["R1_bias_record"]["consecutive_rounds"]
        if not same_dir:                       # 方向偏差
            if self._last_dir.get(team) == pick_dir:
                self._streak[team] = self._streak.get(team, 0) + 1
            else:
                self._streak[team] = 1
            self._last_dir[team] = pick_dir
        else:                                  # 命中则清零
            self._streak[team] = 0

        self.logger.log(
            "R1_bias_record", match_id, "record",
            {"round": round_no, "team": team, "pick_direction": pick_dir,
             "actual_direction": actual_dir, "score_top2": score_top2,
             "actual_score": actual_score, "deviation_type": dev_type,
             "consecutive_streak": self._streak.get(team, 0)})

        if self._streak.get(team, 0) >= n:
            self.logger.log(
                "R1_bias_record", match_id, "elo_recalibrate",
                {"team": team, "round": round_no, "streak": self._streak[team],
                 "consecutive_rounds": n, "recalib_target": ["K", "home_advantage"],
                 "before": {"K": 15, "home_advantage": 50}})  # TODO: 重校准后记录真实参数
        return dev_type

    def streak(self, team: str) -> int:
        return self._streak.get(team, 0)

    # ============ R2 伤病升级（置信度驱动 48h 复算） ============
    def needs_confirmed_recalc(self, confidence: str, last_updated: str,
                               kickoff: str, match_id: str = "") -> bool:
        """初版置信度 <= downgrade_confidence_below 且距开赛 <=48h → 须用最终伤病名单复算升级为确认版。"""
        conf_level = _CONF_ORDER.index(confidence) if confidence in _CONF_ORDER else 1
        below = _CONF_ORDER.index(self.cfg["R2_injury_upgrade"]["downgrade_confidence_below"])
        hours = (datetime.fromisoformat(kickoff[:19])
                 - datetime.fromisoformat(last_updated[:19])).total_seconds() / 3600
        in_window = 0 < hours <= self.cfg["R2_injury_upgrade"]["confirm_window_hours"]
        need = conf_level <= below and in_window
        if need:
            self.logger.log(
                "R2_injury_upgrade", match_id, "upgrade_to_confirmed",
                {"confidence": confidence, "hours_before": round(hours, 1),
                 "window_hours": self.cfg["R2_injury_upgrade"]["confirm_window_hours"]})
        return need

    # ============ R3 国家队系数窗口（xG 下调 8%–15%） ============
    def xg_discount(self, team: str, is_first_round_after_fifa: bool,
                    nt_minutes: int = 0, travel_km: int = 0,
                    match_id: str = "") -> float:
        """返回该队预期进球下调比例（0.08–0.15）；未命中条件返回 0.0。"""
        cfg = self.cfg["R3_national_window"]
        if not (cfg["fifa_window_first_round_only"] and is_first_round_after_fifa):
            self.logger.log("R3_national_window", match_id, "window",
                            {"team": team, "active": False, "xg_discount": 0.0})
            return 0.0
        heavy = nt_minutes > cfg["minutes_threshold"] or travel_km > cfg["travel_km_threshold"]
        discount = 0.0
        if heavy:
            # 线性映射：出场/旅行越重折扣越大，落在 [min, max]
            load = max(nt_minutes / (cfg["minutes_threshold"] * 2),
                       travel_km / (cfg["travel_km_threshold"] * 2))
            discount = round(cfg["xg_discount_min"] + (cfg["xg_discount_max"] - cfg["xg_discount_min"])
                             * min(load, 1.0), 3)
        self.logger.log("R3_national_window", match_id, "window",
                        {"team": team, "active": True, "nt_minutes": nt_minutes,
                         "travel_km": travel_km, "xg_discount": discount})
        return discount

    # ============ R4 分歧场次标注（Elo vs Dixon-Coles） ============
    def mark_divergence(self, elo_dir: str, dc_dir: str, pick: str,
                        match_id: str = "", elo_home_p: float | None = None,
                        dc_home_p: float | None = None) -> dict:
        """Elo 与 Dixon-Coles 方向不一致，且分歧强度(Δp)≥阈值 → 置信度降一档并备注明示。
        threshold 为 config.RULES.R4.submodel_disagreement_threshold（实测标定=0.10）。
        """
        cfg = self.cfg["R4_divergence_flag"]
        threshold = float(cfg["submodel_disagreement_threshold"])
        dp = abs(elo_home_p - dc_home_p) if (elo_home_p is not None and dc_home_p is not None) else None
        flagged = bool((elo_dir != dc_dir) and (dp is None or dp >= threshold))
        result = {
            "flagged": flagged,
            "note_prefix": "模型分歧：" if flagged else "",
            "confidence_downgrade_steps": cfg["confidence_downgrade_steps"] if flagged else 0,
        }
        self.logger.log("R4_divergence_flag", match_id, "mark",
                        {"elo_dir": elo_dir, "dc_dir": dc_dir, "pick": pick,
                         "flagged": flagged, "delta_p": float(dp) if dp is not None else None,
                         "threshold": threshold})
        return result

    def downgrade_confidence(self, confidence: str, steps: int) -> str:
        idx = max(0, _CONF_ORDER.index(confidence) - steps) if confidence in _CONF_ORDER else 1
        return _CONF_ORDER[idx]

    # ============ R5 连续跟踪（第 9 轮起 + 上一轮偏差摘要） ============
    def track_round_seq(self, team: str, round_no: int, match_id: str = "") -> int:
        """自 start_round 起连续轮编号累加；非连续轮则重置为 1。"""
        start = self.cfg["R5_continuous_tracking"]["start_round"]
        if round_no < start:
            return 0
        last = self._last_round.get(team)
        if last is not None and round_no == last + 1:
            self._round_seq[team] = self._round_seq.get(team, 0) + 1
        else:
            self._round_seq[team] = 1
        self._last_round[team] = round_no
        self.logger.log("R5_continuous_tracking", match_id, "track",
                        {"team": team, "round": round_no, "seq": self._round_seq[team]})
        return self._round_seq[team]

    def prev_round_bias_summary(self, round_no: int) -> list[dict]:
        """上一轮偏差摘要（R5/R1 全局输出）。"""
        return [r for r in self.logger.read("R1_bias_record")
                if r.get("event") == "record" and r.get("payload", {}).get("round") == round_no - 1]
