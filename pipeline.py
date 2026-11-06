"""
预测引擎编排：数据(Provider) → 特征 → 模型 → 规则(R1–R5) → 输出(JSON Schema)。
P0 新增：Provider 抽象、数据时效透传、确认版实时伤病闸门、无数据轮次降级容错（GBM→DC 基准概率，不抛 500）。
P1 新增：灌入德甲历史 CSV 重训 Elo + Dixon-Coles + GBM（XGB+LGBM），
        概率计算由 trainer.ModelBundle（训练产物）承担；历史源驱动真实预测，种子源仍供无网络/演示。
规则阈值全部取自 config.RULES；不在此硬编码。
"""
from __future__ import annotations
import math
from datetime import datetime
from config import (RULES, SEASON, ENSEMBLE_WEIGHTS, uncalibrated_thresholds,
                    FATIGUE_RULES, LUCK_REGRESSION, HOME_ADV)
from schema import validate_probs, validate_stage_window
from data.api_client import resolve_provider, team_zh
from data.historical_data import CODE2ENG
from data.cross_competition import FatigueEngine, CrossElo
from data.euro_fetch import fetch_current_euro


def _parse_dt(s: str):
    """ISO(可带 Z) → naive datetime；失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
from rules.iteration_rules import IterationRules, elo_params_from_bundle
from data_stores.cache import Cache
from data.timeutil import fmt_local  # 时间口径：存 UTC，展示转北京时间
import trainer


class PredictionPipeline:
    def __init__(self):
        self._bundle = trainer.train()      # 训练产物（Elo/DC/GBM），P1 历史数据驱动
        # R1 的"重校准前参数"取 bundle 里实际生效的标定值（K=20 / home_adv=60），
        # 不能用 config 原值（15/50）——那两个已被网格搜索覆盖。
        self.rules = IterationRules(elo_params=elo_params_from_bundle(self._bundle))
        self.cache = Cache()
        self.provider = resolve_provider()   # 自动选择：实时(token) 或 零密钥种子
        self.cross_elo = CrossElo(self._model_elo_map())

    # ---------------- 数据链路（P0 验收条件 #1/#3/#4） ----------------
    def fetch_round(self, round_no: int) -> tuple[list[dict], dict]:
        """拉取一轮对阵 + 时效元数据。"""
        fixtures = self.provider.fetch_round(round_no)
        freshness = self.provider.freshness()
        meta = {
            "freshness": freshness,
            "realtime_injury_available": self.provider.realtime_injury_available,
            "round_has_data": bool(fixtures),
            "round_no": round_no,
        }
        return fixtures, meta

    def data_freshness_label(self, meta: dict) -> str:
        """验收条件 #1：数据最后拉取时间 + 缓存标注。"""
        f = meta.get("freshness", {})
        cached = f.get("cached", False)
        label = f"数据最后拉取时间：{fmt_local(f.get('fetched_at'))}"
        if cached:
            label += "（缓存）"
        return label

    # ---------------- P1 模型预测核心（历史重训产物） ----------------
    def _eng(self, code: str) -> str:
        return CODE2ENG.get(code, code)

    def _model_elo_map(self) -> dict:
        """把训练产物的 Elo（英文名→分）映射到 code→Elo（供跨赛事用）。"""
        try:
            b = self._bundle
            elo = getattr(b, "elo_ratings", None)
            if not elo:
                return {}
            return {code: float(elo.get(eng, 1500))
                    for code, eng in CODE2ENG.items() if eng in elo}
        except Exception:
            return {}

    def _cross_probs_for(self, home_elo: float, away_elo: float,
                         comp: str = "CL") -> dict:
        """跨赛事概率：Elo 差 → 泊松得分矩阵（对非德甲对手，GBM/DC 特征不适用）。
        home_elo/away_elo 已含联赛平均兜底。K 倍率体现赛事强度权重。"""
        k = self.cross_elo.k_multiplier(comp)
        diff = ((home_elo - away_elo) + HOME_ADV * k) / 400.0   # Elo logit 差
        win_elo = 1.0 / (1 + 10 ** (-diff))       # Elo 胜率（主队）
        # 期望进球随 Elo 差显著拉开（10^(±diff/2)），保证强队 λ 明显更高
        lam_h = 1.35 * (10 ** (diff / 2.0))
        lam_a = 1.35 * (10 ** (-diff / 2.0))
        maxg = 6
        mat = {}
        for i in range(maxg + 1):
            for j in range(maxg + 1):
                ph = (lam_h ** i) * math.exp(-lam_h) / math.factorial(i)
                pa = (lam_a ** j) * math.exp(-lam_a) / math.factorial(j)
                mat[(i, j)] = ph * pa
        s = sum(mat.values())
        mat = {k_: v / s for k_, v in mat.items()}
        home_w = sum(v for (i, j), v in mat.items() if i > j)
        away_w = sum(v for (i, j), v in mat.items() if i < j)
        draw = sum(v for (i, j), v in mat.items() if i == j)
        top2 = sorted(mat.items(), key=lambda kv: -kv[1])[:2]
        return {
            "fused": {"home_win": home_w, "draw": draw, "away_win": away_w},
            # 修复：跨赛事没有可靠的平局模型，不再伪造 draw=away 的假三元组
            "elo": {"home_win": win_elo, "draw": None, "away_win": 1 - win_elo},
            "score_top2": [{"score": f"{i}:{j}", "prob": round(p, 3)} for (i, j), p in top2],
            "lam": {"home": round(lam_h, 2), "away": round(lam_a, 2)},
        }

    def _probs_for(self, home_code: str, away_code: str,
                   home_disc: float = 0.0, away_disc: float = 0.0,
                   inj_shift_h: float = 0.0, inj_shift_a: float = 0.0) -> dict:
        """由训练产物计算三模型 + 融合概率 + 比分 Top2。
        home_disc/away_disc：R3 国家队折扣；inj_shift_h/inj_shift_a：R2 伤病偏移（均折入 DC 攻击强度）。"""
        h, a = self._eng(home_code), self._eng(away_code)
        b = self._bundle
        elo = b.elo_prob(h, a)
        dc = b.dc_prob_discounted(h, a, home_disc + inj_shift_h, away_disc + inj_shift_a)
        feats = trainer.build_feature_vector(b, h, a)
        gbm = b.gbm_prob(feats)
        w = ENSEMBLE_WEIGHTS
        # ---------- GBM 可信度护栏 ----------
        # 树模型在"双方滚动特征全为默认值(1.0)"（升班马/无历史队）或输出单类压倒性概率时会外推，
        # 实测 汉堡vs门兴 GBM 给出 平局91% / 主胜2.6%，与 Elo(45/18/37)、DC(51/28/21) 完全背离。
        # 这类输出不能按固定权重直融，按可信度降权，剩余权重按 Elo:DC 原比例重分配。
        gbm_w = w["xgboost"] + w["lightgbm"]
        trust = 1.0
        gmax, gmin = max(gbm.values()), min(gbm.values())
        if gmax >= 0.80 or gmin <= 0.05:
            trust = 0.0
        elif (abs(feats["form_home"] - 1.0) < 1e-9 and abs(feats["form_away"] - 1.0) < 1e-9
              and abs(feats["roll_hg_home"] - 1.0) < 1e-9 and abs(feats["roll_hg_away"] - 1.0) < 1e-9):
            trust = 0.3
        w_elo, w_dc, w_gbm = w["elo"], w["poisson"], gbm_w * trust
        if trust < 1.0:
            rest = max(1.0 - w_gbm, 0.0)
            base = w["elo"] + w["poisson"]
            if base > 0:
                w_elo, w_dc = rest * w["elo"] / base, rest * w["poisson"] / base
        fused = {
            "home_win": w_elo * elo["home_win"] + w_dc * dc["home_win"] + w_gbm * gbm["home_win"],
            "draw": w_elo * elo["draw"] + w_dc * dc["draw"] + w_gbm * gbm["draw"],
            "away_win": w_elo * elo["away_win"] + w_dc * dc["away_win"] + w_gbm * gbm["away_win"],
        }
        s = sum(fused.values())
        fused = {k: v / s for k, v in fused.items()}
        matrix = b.dc_score_matrix(h, a, 8, home_disc + inj_shift_h, away_disc + inj_shift_a)
        top2 = sorted(matrix.items(), key=lambda kv: -kv[1])[:2]
        return {
            "fused": fused, "elo": elo, "dc": dc, "gbm": gbm,
            "gbm_trust": round(trust, 3),
            "score_top2": [{"score": f"{i}:{j}", "prob": round(p, 3)} for (i, j), p in top2],
        }

    # ---------------- 单场预测（含确认版闸门 + 无伤病标注 + 疲劳/跨赛事） ----------------
    def predict_match(self, match: dict, round_no: int, stage: str = "初版",
                      is_first_round_after_fifa: bool = False,
                      realtime_injury_available: bool = False,
                      fatigue: dict | None = None,
                      competition: str = "BL1",
                      luck_map: dict | None = None,
                      lineup: dict | None = None) -> dict:
        home, away = match["home_team"], match["away_team"]
        home_code = match.get("home_code", home)
        away_code = match.get("away_code", away)
        match_id = match.get("match_id", "")
        confidence = match.get("confidence", "中")

        # ---------- 验收条件 #3：确认版实时伤病闸门 ----------
        confirm_blocked = stage in ("确认版", "终极版") and not realtime_injury_available \
            and not (lineup and lineup.get("available"))
        if confirm_blocked:
            stage = "初版"   # 拦截：既无实时伤病、也无官方首发，无法生成确认版

        # 无伤病数据标注（验收条件 #4 / 风险提示）
        inj = match.get("injuries", {})
        injury_lists = [inj.get(k, []) for k in
                        ("home_key_out", "away_key_out", "home_doubtful", "away_doubtful")]
        no_injury_data = not any(injury_lists)

        # ---------- P5：赛前首发反推（把首发偏差折成 DC 攻击折扣） ----------
        lineup_dev = {"home": {}, "away": {}}
        lineup_shift = {"home": 0.0, "away": 0.0}
        lineup_info = {"available": False, "note": "未获取到本场官方首发（赛前约 60 分钟才公布）"}
        _base_lambda = float(getattr(self._bundle, "dc_mu_home", 1.5) or 1.5)
        if lineup and lineup.get("available") and competition == "BL1":
            # 修复：baseline 侧任一符号缺失（版本漂移）会让整批 9 场直接 ImportError，
            # 再被 app 显示成"请求过载"。这里降级为"不做大名单校验"。
            try:
                from data.baseline import (load_baseline, fetch_team_roster,
                                           fetch_team_ids, roster_drift)
            except Exception:
                load_baseline = lambda: {}                       # noqa: E731
                fetch_team_roster = lambda *a, **k: {}           # noqa: E731
                fetch_team_ids = lambda *a, **k: {}              # noqa: E731
                roster_drift = lambda *a, **k: {"available": False}  # noqa: E731
            from lineup_shift import (starter_deviation, describe_shift, xg_equivalent,
                                      describe_newcomers)
            bl_teams = (load_baseline() or {}).get("teams", {}) or {}
            team_ids = fetch_team_ids("BL1")
            details = {"home": "", "away": ""}
            newcomers = {"home": None, "away": None}
            drift = {"home": None, "away": None}
            for side, code, team_name in (("home", home_code, home), ("away", away_code, away)):
                entry = bl_teams.get(code)
                # 转会窗容错：拿当前大名单，把已离队的基线主力剔出扣分对象
                roster = fetch_team_roster(team_ids.get(code), "BL1") if team_ids.get(code) else {}
                dev = starter_deviation(entry, lineup.get(side), roster=roster)
                lineup_dev[side] = dev
                lineup_shift[side] = float(dev.get("shift", 0.0) or 0.0)
                details[side] = describe_shift(team_name, dev, _base_lambda)
                newcomers[side] = describe_newcomers(team_name, dev)
                d = roster_drift(entry or {}, roster)
                drift[side] = d if d.get("available") else None
            lineup_info = {
                "available": True,
                "source": lineup.get("source", "ESPN 首发名单"),
                "event_id": lineup.get("event_id"),
                "ready_at": lineup.get("ready_at"),
                "home": lineup_dev["home"], "away": lineup_dev["away"],
                "detail_lines": details,
                "newcomer_lines": newcomers,
                "roster_drift": drift,
                "xg_home": xg_equivalent(lineup_shift["home"], _base_lambda),
                "xg_away": xg_equivalent(lineup_shift["away"], _base_lambda),
                "note": "首发反推：常规主力在首发=满分，在替补席=半额，不在名单=全额；"
                        "计算前已用当前大名单校验，已离队球员不计扣分",
            }

        # ---------- 疲劳特征（欧战/杯赛干扰，进 GBM 特征库语义） ----------
        f = (fatigue or {}).get(match_id) or {}
        f_home, f_away = f.get("home", {}), f.get("away", {})
        fatigue_disc_h = f_home.get("fatigue_discount", 0.0)
        fatigue_disc_a = f_away.get("fatigue_discount", 0.0)
        cl_preceding_h = bool(f_home.get("cl_preceding"))
        cl_preceding_a = bool(f_away.get("cl_preceding"))

        # ---------- R3 国家队系数窗口（xG 折扣作用于 DC 本场概率） ----------
        # 修复：原实现把 nt_minutes / travel_km 写死成 (0, 0)，永远达不到 180min / 5000km
        # 的阈值 → R3 从上线起恒返回 0（config 里的 0.08~0.15 是摆设）。
        # 现在从比赛数据读取；provider 没提供时仍为 0，但"有数据就会生效"。
        nm_h = int(match.get("home_nt_minutes") or 0)
        nm_a = int(match.get("away_nt_minutes") or 0)
        km_h = int(match.get("home_travel_km") or 0)
        km_a = int(match.get("away_travel_km") or 0)
        disc_h = self.rules.xg_discount(self._eng(home_code), is_first_round_after_fifa, nm_h, km_h, match_id)
        disc_a = self.rules.xg_discount(self._eng(away_code), is_first_round_after_fifa, nm_a, km_a, match_id)
        # 疲劳 xG 折扣并入（欧战/杯赛休息不足等）
        disc_h += fatigue_disc_h
        disc_a += fatigue_disc_a
        # P4·优先级二②：战意规则（写死）—— 欧冠前3天打联赛 + 联赛对手排名低≥5名 → 硬扣 0.15 xG（不被疲劳封顶吞掉）
        mot_pen_h = float(f_home.get("cl_motivation_penalty", 0.0) or 0.0)
        mot_pen_a = float(f_away.get("cl_motivation_penalty", 0.0) or 0.0)
        disc_h += mot_pen_h
        disc_a += mot_pen_a
        # P4·优先级二①：运气回归（触底反弹）—— 近5场机会创造强但转化极差
        # 阈值用"联赛相对分布"：gap 须高于联赛均值 + gap_z·σ，且射正均值≥联赛中位数
        # （HST 为射正计数代理，尺度远高于进球，须相对联赛基线否则全员触发）。
        luck_cfg = LUCK_REGRESSION
        lu_map = luck_map or {}
        lu_h = lu_map.get(home_code) or {}
        lu_a = lu_map.get(away_code) or {}
        if lu_map:
            gaps = [v.get("gap", 0.0) for v in lu_map.values()]
            hsts = [v.get("roll_hst", 0.0) for v in lu_map.values()]
            _g = float(sum(gaps) / len(gaps))
            _sd = (sum((x - _g) ** 2 for x in gaps) / len(gaps)) ** 0.5
            _med_h = sorted(hsts)[len(hsts) // 2]
            _thr_gap = _g + luck_cfg["gap_z"] * _sd
        else:
            _thr_gap, _med_h = float("inf"), float("inf")

        def _rebound(lu: dict) -> bool:
            return bool(lu) and lu.get("roll_hst", 0.0) >= _med_h \
                and lu.get("gap", 0.0) >= _thr_gap
        rebound_h, rebound_a = _rebound(lu_h), _rebound(lu_a)
        if rebound_h:
            disc_h = max(disc_h - luck_cfg["xg_boost"], -luck_cfg["xg_boost"])
        if rebound_a:
            disc_a = max(disc_a - luck_cfg["xg_boost"], -luck_cfg["xg_boost"])

        # ---------- 初版基准概率（R3+疲劳折扣，无伤病偏移） ----------
        if competition == "BL1":
            res = self._probs_for(home_code, away_code, home_disc=disc_h, away_disc=disc_a)
        else:
            # 跨赛事：Bundesliga 用模型 Elo，其他联赛对手用联赛平均系数兜底
            h_elo = self.cross_elo.rating_for(home_code, home)
            a_elo = self.cross_elo.rating_for(away_code, away)
            res = self._cross_probs_for(h_elo, a_elo, competition)
        probs = res["fused"]
        validate_probs(probs)
        base_probs = {k: round(v, 3) for k, v in probs.items()}
        inj_shift = {"home": 0.0, "away": 0.0}

        # ---------- 确认版/终极版复算：把伤病名单（+首发偏差）折成 DC 偏移，重算概率 ----------
        confirm_probs = None
        manual_shift = {"home": 0.0, "away": 0.0}
        if stage in ("确认版", "终极版") and competition == "BL1" \
                and (not no_injury_data or lineup_info.get("available")):
            is_shift = self._injury_shift
            manual_shift["home"] = is_shift(inj.get("home_key_out", []),
                                            inj.get("home_doubtful", []))
            manual_shift["away"] = is_shift(inj.get("away_key_out", []),
                                            inj.get("away_doubtful", []))
            # 首发反推是硬证据（谁真的没上），手动录入是赛前情报；两者都反映"谁不能打"，
            # 取较大值避免同一缺失被重复扣两次。
            for k in ("home", "away"):
                inj_shift[k] = max(manual_shift[k], lineup_shift[k])
            if inj_shift["home"] or inj_shift["away"]:
                res_c = self._probs_for(home_code, away_code, home_disc=disc_h, away_disc=disc_a,
                                        inj_shift_h=inj_shift["home"], inj_shift_a=inj_shift["away"])
                confirm_probs = res_c["fused"]
                validate_probs(confirm_probs)
                probs = confirm_probs   # 卡片主概率 = 确认版复算结果
                res = res_c

        # 偏移来源标注
        if lineup_info.get("available") and (manual_shift["home"] or manual_shift["away"]):
            shift_source = "首发反推 + 手动伤病（取较大值）"
        elif lineup_info.get("available"):
            shift_source = "赛前首发反推"
        elif manual_shift["home"] or manual_shift["away"]:
            shift_source = "手动伤病录入"
        else:
            shift_source = "无"

        # ---------- R4 分歧场次标注：Elo vs DC 方向不一致 → 降一档 ----------
        elo_dir = "home_win" if res["elo"]["home_win"] > 0.5 else "away_win"
        dc = res.get("dc", res.get("elo", res))   # 跨赛事模式无 dc → 回退 elo/fused
        if dc.get("draw") is None:
            # 跨赛事没有 DC 三向概率 → R4 无从判定"方向分歧"，直接跳过（别硬造 draw）
            dc_dir = elo_dir
        else:
            dc_dir = "home_win" if dc["home_win"] > dc["draw"] and dc["home_win"] > dc["away_win"] else \
                     ("draw" if dc["draw"] >= dc["away_win"] else "away_win")
        # 推荐方向 = 三项概率取最大（平局也必须是候选，不能只在家胜/客胜里二选一）
        pick_dir = max(("home_win", "draw", "away_win"), key=lambda k: probs[k])
        div = self.rules.mark_divergence(elo_dir, dc_dir, pick_dir, match_id,
                                         elo_home_p=res["elo"]["home_win"],
                                         dc_home_p=dc["home_win"])
        confidence = self.rules.downgrade_confidence(confidence, div["confidence_downgrade_steps"])

        # ---------- R2 伤病升级：中/低置信度 + 48h → 复算升级（仅当具备实时伤病） ----------
        if stage == "初版" and realtime_injury_available and self.rules.needs_confirmed_recalc(
                confidence, match.get("last_updated", ""), match.get("kickoff", ""), match_id):
            stage = "确认版"

        # ---------- R5 连续跟踪 ----------
        self.rules.track_round_seq(home_code, round_no, match_id)
        self.rules.track_round_seq(away_code, round_no, match_id)

        if stage in ("确认版", "终极版") and confirm_probs is not None:
            head = "终极刷新（首发反推）" if stage == "终极版" else "确认版复算"
            note = (f"{head}：偏移来源={shift_source}；偏移 主队{inj_shift['home']:.2f}／客队{inj_shift['away']:.2f}（DC 攻击折扣口径）。"
                    f"主 {base_probs['home_win']:.1%}→{confirm_probs['home_win']:.1%}、"
                    f"平 {base_probs['draw']:.1%}→{confirm_probs['draw']:.1%}、"
                    f"客 {base_probs['away_win']:.1%}→{confirm_probs['away_win']:.1%}。")
            if lineup_info.get("available"):
                note += "｜" + "；".join(x for x in (lineup_info.get("detail_lines") or {}).values() if x) + "。"
            if abs(inj_shift["home"] - inj_shift["away"]) < 0.03 and inj_shift["home"] > 0:
                note += "两队偏移量近似对称，净影响本身就小（属对称抵消，不是没生效）。"
        else:
            note = div["note_prefix"] + ("无伤病数据，输出基准概率。" if no_injury_data
                                         else "临场缺阵或前场创造力缺失。")
        # 推荐方向与最可能比分方向不一致时明示（避免"推荐主胜却给 1:1"的观感矛盾）
        if res.get("score_top2"):
            top1 = res["score_top2"][0]["score"]
            g1, g2 = (int(x) for x in top1.split(":"))
            top_dir = "home_win" if g1 > g2 else ("away_win" if g1 < g2 else "draw")
            if top_dir != pick_dir:
                note += f"（注：最可能比分 {top1} 与推荐方向不一致，比分仅作参考）"
        # 疲劳/欧战前置信息（跨赛事分析，UI 醒目标记）
        fatigue_info = {
            "home_rest_days": f_home.get("rest_days"),
            "away_rest_days": f_away.get("rest_days"),
            # P6·按小时口径（覆盖周中欧冠/欧联/欧会 → 周末德甲的真实间隔）
            "home_rest_hours": f_home.get("rest_hours"),
            "away_rest_hours": f_away.get("rest_hours"),
            "home_prev_competition": f_home.get("prev_competition"),
            "away_prev_competition": f_away.get("prev_competition"),
            "home_prev_competition_zh": f_home.get("prev_competition_zh"),
            "away_prev_competition_zh": f_away.get("prev_competition_zh"),
            "home_prev_is_euro": bool(f_home.get("prev_is_euro")),
            "away_prev_is_euro": bool(f_away.get("prev_is_euro")),
            "home_prev_was_away": bool(f_home.get("prev_was_away")),
            "away_prev_was_away": bool(f_away.get("prev_was_away")),
            "home_bucket_tag": f_home.get("bucket_tag"),
            "away_bucket_tag": f_away.get("bucket_tag"),
            "home_fatigue_discount": round(fatigue_disc_h, 4),
            "away_fatigue_discount": round(fatigue_disc_a, 4),
            "fatigue_calibrated": bool(FATIGUE_RULES.get("calibrated", False)),
            "cl_preceding_home": cl_preceding_h,
            "cl_preceding_away": cl_preceding_a,
            "home_prev_extra_time": bool(f_home.get("prev_extra_time")),
            "away_prev_extra_time": bool(f_away.get("prev_extra_time")),
            # P4·优先级二②：战意规则硬扣（欧冠前置轮换的强条件，对手弱旅）
            "home_motivation_penalty": mot_pen_h,
            "away_motivation_penalty": mot_pen_a,
        }
        # P4·优先级二①：运气回归（触底反弹）标签与 gap
        luck_info = {"home_gap": lu_h.get("gap"), "away_gap": lu_a.get("gap"),
                     "home_rebound": rebound_h, "away_rebound": rebound_a}
        # 可选提示：某队在这套 DC 里没有任何历史参数（升班马/欧战新面孔）→ 走的是
        # 「联赛平均」默认值。归一化后 1.0 确实等于平均队，但卡片上仍要说清楚，
        # 免得被当成"真实强弱"读。
        try:
            _dc_keys = getattr(self._bundle, "dc_attack", {}) or {}
        except Exception:
            _dc_keys = {}
        no_history_teams = []
        for _code, _zh in ((match.get("home_code"), home), (match.get("away_code"), away)):
            if _code and _dc_keys and self._eng(_code) not in _dc_keys:
                no_history_teams.append(_zh)

        card = {
            "match_id": match_id, "competition": competition,
            "home_team": home, "away_team": away,
            "no_history_teams": no_history_teams,
            "kickoff": match.get("kickoff", ""), "stage": stage,
            "last_updated": match.get("last_updated", ""),
            "standings": match.get("standings"),
            "form_last3": match.get("form_last3"),
            "injuries": inj,
            "fatigue": fatigue_info,
            "national_team_factor": {"active": is_first_round_after_fifa,
                                     "home": 1 - disc_h, "away": 1 - disc_a},
            "probabilities": {k: round(v, 3) for k, v in probs.items()},
            "base_probs": base_probs,
            "confirm_probs": {k: round(v, 3) for k, v in confirm_probs.items()} if confirm_probs else None,
            "injury_shift": inj_shift,
            "manual_shift": manual_shift,
            "lineup_shift": lineup_shift,
            "shift_source": shift_source,
            "lineup": lineup_info,
            "luck": luck_info,
            "score_top2": res["score_top2"],
            "pick": {"home_win": f"{home}胜", "away_win": f"{away}胜", "draw": "平局"}[pick_dir],
            "pick_dir": pick_dir,
            "pick_prob": round(probs[pick_dir], 3),
            "handicap_direction": {"line": f"{away} -0.5", "caution": "中高"},
            "confidence": confidence,
            "risk_note": note,
            "data_availability": "无伤病数据" if no_injury_data else "含伤病数据",
            "confirm_blocked": confirm_blocked,
            "model_breakdown": {"elo_home": round(res["elo"]["home_win"], 3),
                                # 跨赛事 elo 没有平局项 → 展示为 "—"（原来会 round(None) 崩）
                                "elo_draw": (round(res["elo"]["draw"], 3)
                                             if res["elo"].get("draw") is not None else "—"),
                                "elo_away": round(res["elo"]["away_win"], 3),
                                "gbm_trust": res.get("gbm_trust", 1.0),
                                "dc_score_top2": [s["score"] for s in res["score_top2"]]},
        }

        # 确认版强制 48h 窗口校验（窗口取自 config.RULES.R2）；终极版是临场首发反推，不受该窗口约束
        if stage == "确认版" and not validate_stage_window(
                card["last_updated"], card["kickoff"],
                RULES["R2_injury_upgrade"]["confirm_window_hours"]):
            if confirm_probs is not None:
                # 手动伤病驱动的复算保留为确认版，但如实标注为赛前48h窗口外预览
                card["stage"] = "确认版"
                card["risk_note"] += "（赛前48h窗口外，本为复算预览）"
            elif no_injury_data:
                # 用户选了确认版：本场没有录入伤病，概率与初版相同，但仍标确认版并在卡片上说明，
                # 避免同一批卡片阶段标签不一致（"阶段标签未联动"的根因之一）
                card["stage"] = "确认版"
                card["risk_note"] += "（本场未录入伤病，概率与初版一致）"
            else:
                card["stage"] = "初版"
        # 修复：确认版必须有复算结果。本场没有伤病数据时 confirm_probs 为 None，
        # 原来仍标 [确认版]（概率其实是初版），现在统一回落为初版并写明原因。
        if card["stage"] == "确认版" and confirm_probs is None:
            card["stage"] = "初版"
            card["risk_note"] += "（本场无伤病复算数据，等同初版）"
        return card

    # ---------------- 整轮预测（含无数据降级容错，验收条件 #4） ----------------
    def _injury_shift(self, entries_key_out: list, entries_doubtful: list) -> float:
        """按 config.injury_shift 计算单队伤病偏移（有上限）。
        每个球员条目可为 str（默认"核心"重要性）或 dict{"name","importance":"核心/轮换/替补"}；
        重要性权重只缩放该球员的边际偏移，反映"核心缺阵 > 替补缺阵"。"""
        cfg = RULES["injury_shift"]
        imp = cfg["importance_factor"]
        dflt = cfg.get("importance_default", "核心")
        raw = 0.0
        for x in (entries_key_out or []):
            name, im = (x["name"], x.get("importance", dflt)) if isinstance(x, dict) else (str(x), dflt)
            raw += cfg["key_out_shift"] * imp.get(im, 1.0)
        for x in (entries_doubtful or []):
            name, im = (x["name"], x.get("importance", dflt)) if isinstance(x, dict) else (str(x), dflt)
            raw += cfg["doubtful_shift"] * imp.get(im, 1.0)
        return round(min(raw, cfg["max_total_shift"]), 4)

    # ---------------------------------------------------------------- 本周总览
    def week_overview(self, days_fwd: int = 7, days_back: int = 10,
                      with_lineup: bool = True) -> dict:
        """「本周德甲总览」：把本周德甲赛程 + 周二/三/四欧战 + 休息小时 + 疲劳折扣
        + 伤病 + 阵容消耗 放在同一张视图里。

        这是对"德甲/欧冠分开单选"的结构性修补：周五点预测即可一次看到本周全部德甲，
        并且每支球队的周中欧战消耗（含免费层拿不到的欧联/欧会）都在其中。
        """
        from datetime import datetime, timedelta
        from data.euro_fetch import fetch_lineup, lineup_rotation_summary, \
            EURO_LEAGUES, EURO_NAMES_ZH

        today = datetime.now()
        lo = today - timedelta(days=days_back)
        hi = today + timedelta(days=days_fwd)

        fetch = getattr(self.provider, "fetch_competition_matches", None)
        bl1_all = fetch("BL1") if callable(fetch) else []
        cl_all = fetch("CL") if callable(fetch) else []
        if not bl1_all:
            # 数据源不提供真实赛程（零密钥种子 / 未配置 token）：明确降级，不抛异常
            return {
                "window": [lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
                "days": [], "count": 0,
                "euro_window": {k: 0 for k in ("CL", "EL", "UECL")},
                "lineup_note": "",
                "fatigue_calibrated": bool(FATIGUE_RULES.get("calibrated", False)),
                "fatigue_note": FATIGUE_RULES.get("calibration_note", ""),
                "freshness": self.provider.freshness(),
                "note": ("当前数据源（%s）不提供真实赛程，无法生成本周总览。"
                         "请用带 FOOTBALL_DATA_TOKEN 的方式启动（桌面启动器已内置），"
                         "或设置该环境变量后重启应用。" % getattr(self.provider, "name", "未知")),
            }
        cur = fetch_current_euro(days_back=days_back, days_fwd=days_fwd)
        euro_all = list(cl_all) + [m for k in ("CL", "EL", "UECL")
                                   for m in (cur.get(k) or []) if m.get("status") == "FINISHED"]

        def _kick(m):
            return _parse_dt(m.get("kickoff", ""))

        # 本周德甲：从今天起 days_fwd 天内
        fx = [m for m in bl1_all
              if _kick(m) and today.replace(hour=0, minute=0, second=0) <= _kick(m) <= hi]
        fx.sort(key=lambda m: m.get("kickoff") or "")
        if not fx:
            # 周中打开：退回"未来 14 天内最近的一个比赛周"
            fx = [m for m in bl1_all if _kick(m) and today <= _kick(m) <= today + timedelta(days=14)]
            fx.sort(key=lambda m: m.get("kickoff") or "")

        fe = FatigueEngine(bl1_all, euro_all)
        fm = fe.analyze(fx, cl_all)

        # 每队的周中欧战（欧战在德甲这场之前、days_back 天内）
        euro_by_code: dict[str, list] = {}
        for key, lst in (cur or {}).items():
            for m in lst:
                if m.get("status") != "FINISHED":
                    continue
                for code, side in ((m.get("home_code"), "home"), (m.get("away_code"), "away")):
                    if not code:
                        continue
                    euro_by_code.setdefault(code, []).append({
                        "competition": key,
                        "competition_zh": EURO_NAMES_ZH.get(key, key),
                        "kickoff": m.get("kickoff"),
                        "event_id": m.get("espn_id") or m.get("match_id"),
                        "is_home": side == "home",
                        "opponent": (m.get("away_raw") if side == "home" else m.get("home_raw")) or "",
                        "score": m.get("score"),
                        "is_extra_time": bool(m.get("extra_time")),
                    })

        days = []
        seen_dates = []
        for m in fx:
            d = (m.get("kickoff") or "")[:10]
            if d not in seen_dates:
                seen_dates.append(d)
                days.append({"date": d, "matches": []})
            rec = fm.get(m.get("match_id")) or {}
            home_c, away_c = m.get("home_code"), m.get("away_code")
            row = {
                "match_id": m.get("match_id"),
                "kickoff": m.get("kickoff"),
                "home_code": home_c, "away_code": away_c,
                "home_team": team_zh(home_c), "away_team": team_zh(away_c),
                "status": m.get("status"),
                "injuries": m.get("injuries") or {},
            }
            for side, code in (("home", home_c), ("away", away_c)):
                f = rec.get(side) or {}
                eu = euro_by_code.get(code) or []
                # 只保留在这场德甲之前的欧战
                eu = [e for e in eu if e.get("kickoff") and e["kickoff"] < (m.get("kickoff") or "")]
                latest = eu[-1] if eu else None
                item = {
                    "code": code, "team": team_zh(code),
                    "rest_hours": f.get("rest_hours"),
                    "rest_days": f.get("rest_days"),
                    "prev_competition": f.get("prev_competition"),
                    "prev_competition_zh": f.get("prev_competition_zh"),
                    "prev_is_euro": f.get("prev_is_euro"),
                    "prev_was_away": f.get("prev_was_away"),
                    "prev_extra_time": f.get("prev_extra_time"),
                    "bucket_tag": f.get("bucket_tag"),
                    "fatigue_discount": f.get("fatigue_discount", 0.0),
                    "cl_preceding": bool(f.get("cl_preceding")),
                    "euro_matches": eu,
                    "last_euro": latest,
                    "lineup": None,
                }
                row[side] = item
            days[-1]["matches"].append(row)

        # 阵容（只抓最近一场欧战，且有 event_id 的德甲队）
        lineup_note = ""
        if with_lineup:
            for dgrp in days:
                for row in dgrp["matches"]:
                    for side in ("home", "away"):
                        it = row[side]
                        le = it.get("last_euro")
                        if not le or not le.get("event_id"):
                            continue
                        key = le.get("competition")
                        if key not in EURO_LEAGUES:
                            continue
                        try:
                            lu = fetch_lineup(str(le["event_id"]), key)
                            summ = lineup_rotation_summary(lu, side="home" if le["is_home"] else "away")
                            it["lineup"] = summ
                        except Exception:
                            it["lineup"] = {"available": False, "note": "阵容抓取失败"}
            lineup_note = ("阵容数据来自 ESPN 赛后 rosters：只反映**欧战当场的首发/换人**，"
                           "不含逐人出场分钟，也不提供赛中伤情；德甲本场首发要等开赛前 1 小时才有，"
                           "因此这里只作为「轮换风险」提示，不进入概率计算。")

        return {
            "window": [lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
            "days": days,
            "count": len(fx),
            "euro_window": {k: len(cur.get(k) or []) for k in ("CL", "EL", "UECL")},
            "lineup_note": lineup_note,
            "fatigue_calibrated": bool(FATIGUE_RULES.get("calibrated", False)),
            "fatigue_note": FATIGUE_RULES.get("calibration_note", ""),
            "freshness": self.provider.freshness(),
        }

    def predict_round(self, round_no: int, stage: str = "初版",
                      is_first_round_after_fifa: bool = False,
                      manual_injuries: dict | None = None,
                      competition: str = "BL1",
                      luck_map: dict | None = None,
                      with_lineup: bool | None = None) -> dict:
        if competition == "BL1":
            fixtures, meta = self.fetch_round(round_no)
        elif competition == "CL":
            try:
                cl = self.provider.fetch_competition_round("CL", round_no)
                fixtures = cl or []
                meta = {"freshness": self.provider.freshness(),
                        "realtime_injury_available": False,
                        "round_has_data": bool(fixtures), "round_no": round_no}
            except Exception:
                fixtures, meta = [], {"freshness": self.provider.freshness(),
                                      "realtime_injury_available": False,
                                      "round_has_data": False, "round_no": round_no}
        else:
            # EL/DFB/欧协：免费层不可用 → 诚实返回受限提示（不抛 500）
            from config import COMPETITIONS
            comp_cfg = COMPETITIONS.get(competition, {})
            meta = {"freshness": self.provider.freshness(),
                    "realtime_injury_available": False,
                    "round_has_data": False, "round_no": round_no,
                    "competition_unavailable": True,
                    "comp_note": comp_cfg.get("note", "免费层不可用")}
            return {"round": round_no, "competition": competition, "meta": meta,
                    "freshness_label": self.data_freshness_label(meta), "cards": [],
                    "comp_unavailable": True, "comp_note": meta["comp_note"]}

        # 方案 C：手动录入伤病（按 code 注入，football-data.org 不提供伤病）
        if manual_injuries:
            for m in fixtures:
                inj = m.get("injuries", {})
                hi = manual_injuries.get(m.get("home_code"))
                ai = manual_injuries.get(m.get("away_code"))
                if hi:
                    inj["home_key_out"] = hi.get("key_out", [])
                    inj["home_doubtful"] = hi.get("doubtful", [])
                if ai:
                    inj["away_key_out"] = ai.get("key_out", [])
                    inj["away_doubtful"] = ai.get("doubtful", [])
                m["injuries"] = inj
            meta["realtime_injury_available"] = True   # 手动伤病视为具备实时伤病

        # 疲劳特征：德甲轮次叠加欧战赛程（欧冠 + 欧联 + 欧会），CL 轮不需（自身即欧战）
        fatigue_map = {}
        if competition == "BL1":
            try:
                bl1_all = self.provider.fetch_competition_matches("BL1")
                cl_all = self.provider.fetch_competition_matches("CL")
                euro_all = list(cl_all)
                try:
                    # ESPN 补齐欧联/欧会（football-data 免费层 EL=403 / UECL=404）
                    cur = fetch_current_euro(days_back=10)
                    for key in ("CL", "EL", "UECL"):
                        for m in cur.get(key) or []:
                            if m.get("status") == "FINISHED":
                                euro_all.append(m)
                except Exception:
                    pass
                fe = FatigueEngine(bl1_all, euro_all)
                fatigue_map = fe.analyze(fixtures, cl_all)
            except Exception:
                fatigue_map = {}

        cards = []
        # P5：终极版（首发反推）才去抓官方首发；普通初版/确认版不打这个网络请求
        want_lineup = (with_lineup if with_lineup is not None else (stage == "终极版"))
        lineup_map = {}
        if want_lineup and competition == "BL1":
            try:
                from data.lineup import find_event_id, fetch_match_lineup
                for m in fixtures:
                    eid = find_event_id(m.get("home_code", ""), m.get("away_code", ""),
                                        m.get("kickoff", ""), "BL1")
                    if eid:
                        lu = fetch_match_lineup(eid, "BL1")
                        if lu.get("available"):
                            lineup_map[m.get("match_id")] = lu
            except Exception:
                lineup_map = {}

        if fixtures:
            for match in fixtures:
                cards.append(self.predict_match(
                    match, round_no, stage=stage,
                    is_first_round_after_fifa=is_first_round_after_fifa,
                    realtime_injury_available=meta["realtime_injury_available"],
                    fatigue=fatigue_map, competition=competition,
                    luck_map=luck_map,
                    lineup=lineup_map.get(match.get("match_id"))))
        else:
            # 无数据轮次：GBM 兜底退化 DC，输出基准概率（不抛 500）
            cards.append(self._baseline_card(round_no))
        return {
            "round": round_no, "competition": competition,
            "meta": meta,
            "freshness_label": self.data_freshness_label(meta),
            "cards": cards,
        }

    def _baseline_card(self, round_no: int) -> dict:
        """未来/无数据轮次的基准输出（GBM→DC 退化，中性对阵）。"""
        res = self._probs_for("NODATA", "NODATA")   # 未知队 → 默认中性
        probs = res["fused"]
        validate_probs(probs)
        return {
            "match_id": f"BUN_{SEASON}_27_R{round_no:02d}_NODATA",
            "home_team": "—", "away_team": "—",
            "kickoff": "", "stage": "初版", "last_updated": "",
            "standings": {}, "form_last3": {}, "injuries": {},
            "national_team_factor": {"active": False, "home": 1.0, "away": 1.0},
            "probabilities": {k: round(v, 3) for k, v in probs.items()},
            "score_top2": res["score_top2"],
            "pick": "基准", "handicap_direction": {"line": "—", "caution": "—"},
            "confidence": "低", "risk_note": "该轮暂无比赛数据，输出 GBM→DC 兜底基准概率。",
            "data_availability": "无数据", "confirm_blocked": False,
            "model_breakdown": {"elo_home": round(res["elo"]["home_win"], 3),
                                "elo_draw": round(res["elo"]["draw"], 3),
                                "elo_away": round(res["elo"]["away_win"], 3),
                                "dc_score_top2": [s["score"] for s in res["score_top2"]]},
        }

    # ---------------- 全局输出 ----------------
    def round_global_summary(self, round_no: int) -> dict:
        return {
            "round": round_no,
            "prev_round_bias_summary": self.rules.prev_round_bias_summary(round_no),
            "uncalibrated_thresholds": uncalibrated_thresholds(),
        }


