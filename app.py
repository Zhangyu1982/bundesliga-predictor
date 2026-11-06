"""
德甲预测机器人 · P0 Streamlit 主页面
验收条件（P0）：
  #1 数据时效性提示   —— 顶部显眼标签"数据最后拉取时间"，缓存数据标注"缓存"
  #2 未标定阈值透传   —— 黄色警告徽章"⚠️ 伤病偏移量未标定，仅供参考"
  #3 初版/确认版切换   —— 用 st.session_state；零密钥源无实时伤病 → 拦截提示"暂无赛前 48h 实时伤病数据，无法生成确认版"
  #4 降级容错         —— 未来无数据轮次 GBM→DC 兜底输出基准概率，不抛 500
部署：Streamlit Cloud，main file = app.py。
"""
import streamlit as st
from pipeline import PredictionPipeline
from data.injury_fetch import (fetch_all_candidates, _live_fetch_candidates,
                               load_backup, save_backup)

DISCLAIMER = ("本系统输出为概率推演，不构成投注建议。比分 Top2 是分布最集中区域，不代表确定结果。"
              "伤病与国家队负荷在赛前 48 小时仍可能显著变动，初版与确认版之间的差异是系统的设计特性，不是缺陷。")


from config import IMPORTANCE_LEVELS


def _merge_injuries(manual: dict | None, checked_candidates: list[dict]) -> dict | None:
    """把勾选的自动候选并入伤病字典（按 team_code）。
    每个球员条目为 {"name", "importance"}；importance ∈ 核心/轮换/替补，参与 injury_shift 加权。"""
    out = dict(manual or {})
    for c in checked_candidates or []:
        code = c.get("team_code")
        player = c.get("player")
        if not code or not player:
            continue
        entry = out.setdefault(code, {"key_out": [], "doubtful": []})
        bucket = "doubtful" if c.get("status") == "doubtful" else "key_out"
        imp = c.get("importance", "核心")
        item = {"name": player, "importance": imp if imp in IMPORTANCE_LEVELS else "核心"}
        if not any(e.get("name") == player for e in entry[bucket]):
            entry[bucket].append(item)
    return out or None


# 伤病文本解析已抽到 injury_text.py（便于单测）；以下别名保持既有调用点不变
from injury_text import parse_importance as _parse_importance
from injury_text import parse_manual_injuries as _parse_manual_injuries


def _manual_injuries_unparsed(text: str, parsed: dict | None = None) -> list[str]:
    """无法解析的行（UI 明确报错，避免"录了等于没录"）。"""
    from injury_text import unparsed_lines
    return unparsed_lines(text)


def _fmt_inj(lst) -> str:
    """伤病列表 → 可读字符串：`名字[重要性]`。兼容 str 与 dict 条目。"""
    parts = []
    for e in lst or []:
        if isinstance(e, dict):
            parts.append(f"{e.get('name','')}[{e.get('importance','核心')}]")
        else:
            parts.append(str(e))
    return "、".join(parts) or "—"


# 伤病重要性颜色映射（P3·移动端视觉高亮）：核心=红 / 轮换=橙 / 替补=灰
_INJ_COLOR = {"核心": "red", "轮换": "orange", "替补": "gray"}


def _fmt_inj_colored(lst) -> str:
    """伤病列表 → Streamlit 彩色 Markdown：核心伤缺标红、轮换橙、替补灰。"""
    parts = []
    for e in lst or []:
        if isinstance(e, dict):
            nm = e.get("name", "")
            imp = e.get("importance", "核心")
            color = _INJ_COLOR.get(imp, "red")
            parts.append(f":{color}[**{nm}**（{imp}）]")
        else:
            parts.append(str(e))
    return "、".join(parts) or "—"


def _build_explanation(c: dict, comp: str) -> str:
    """预测解释（P4·优先级二①）：基于真实卡片数据生成可读理由，不虚构 xG/裁判。
    覆盖：推荐方向与概率结构、伤病（按重要性加权偏移）、疲劳（休息不足/加时赛/欧冠前置轮换）。"""
    pick = c.get("pick", "")
    if pick == "基准":
        return "**预测解释：** 该轮暂无比赛数据，输出 GBM→DC 兜底基准概率，仅供参考。"
    p = c["probabilities"]
    parts = [f"**预测解释：** 模型更看好「{pick}」"]
    if p["home_win"] >= 0.55:
        parts.append(f"主队占优明显（主胜 {p['home_win']:.0%}）")
    elif p["away_win"] >= 0.55:
        parts.append(f"客队占优明显（客胜 {p['away_win']:.0%}）")
    elif p["draw"] >= max(p["home_win"], p["away_win"]):
        parts.append(f"双方势均力敌（平局 {p['draw']:.0%} 最高）")
    elif p["home_win"] > p["away_win"]:
        parts.append(f"主队略占优（主胜 {p['home_win']:.0%} vs 客胜 {p['away_win']:.0%}）")
    else:
        parts.append(f"客队略占优（客胜 {p['away_win']:.0%} vs 主胜 {p['home_win']:.0%}）")
    # 伤病：按重要性加权的偏移（P3：核心红/轮换橙/替补灰 彩色高亮）
    inj = c.get("injuries", {})
    hk = _fmt_inj_colored(inj.get("home_key_out", []))
    ak = _fmt_inj_colored(inj.get("away_key_out", []))
    if hk != "—":
        parts.append(f"{c['home_team']} 缺阵 {hk}")
    if ak != "—":
        parts.append(f"{c['away_team']} 缺阵 {ak}")
    ish = c.get("injury_shift", {})
    if ish.get("home"):
        parts.append(f"主队伤病偏移 {ish['home']}")
    if ish.get("away"):
        parts.append(f"客队伤病偏移 {ish['away']}")
    # 疲劳：休息不足 / 加时赛极端惩罚 / 欧冠前置轮换
    ft = c.get("fatigue", {})
    if ft.get("home_prev_extra_time"):
        parts.append(f"{c['home_team']} 上一场打满120分钟加时（触发疲劳惩罚）")
    if ft.get("away_prev_extra_time"):
        parts.append(f"{c['away_team']} 上一场打满120分钟加时（触发疲劳惩罚）")
    if ft.get("home_rest_days") is not None and ft["home_rest_days"] < 3:
        parts.append(f"{c['home_team']} 仅休息 {ft['home_rest_days']} 天")
    if ft.get("away_rest_days") is not None and ft["away_rest_days"] < 3:
        parts.append(f"{c['away_team']} 仅休息 {ft['away_rest_days']} 天")
    if ft.get("cl_preceding_home"):
        parts.append(f"{c['home_team']} 欧冠前置可能轮换")
    if ft.get("cl_preceding_away"):
        parts.append(f"{c['away_team']} 欧冠前置可能轮换")
    # P4·优先级二②：战意规则（欧冠前3天打联赛 + 对手弱旅 → 硬扣 xG）
    if ft.get("home_motivation_penalty"):
        parts.append(f"{c['home_team']} 欧冠前3天打联赛且对手弱旅 → 战意不足，扣减xG {ft['home_motivation_penalty']}")
    if ft.get("away_motivation_penalty"):
        parts.append(f"{c['away_team']} 欧冠前3天打联赛且对手弱旅 → 战意不足，扣减xG {ft['away_motivation_penalty']}")
    # P4·优先级二①：运气回归（触底反弹）标签 —— 近5场机会创造强但转化差（高xG低进球）
    lk = c.get("luck", {})
    if lk.get("home_rebound"):
        parts.append(f"📈 {c['home_team']} 进攻端触底反弹（近5场射正高但进球少，gap {lk['home_gap']}）")
    if lk.get("away_rebound"):
        parts.append(f"📈 {c['away_team']} 进攻端触底反弹（近5场射正高但进球少，gap {lk['away_gap']}）")
    if c.get("score_top2"):
        parts.append(f"最可能比分 {c['score_top2'][0]['score']}")
    if comp == "CL":
        parts.append("（欧冠跨赛事 Elo 推演）")
    return "；".join(parts) + "。"


st.set_page_config(page_title="德甲预测机器人 · P0", page_icon="⚽", layout="centered")

# 未标定阈值的中文友好映射（验收条件 #2）
UNCAL_LABEL = {
    "R1_bias_record": "偏差记录阈值", "R2_injury_upgrade": "伤病升级窗口",
    "R3_national_window": "国家队系数窗口", "R4_divergence_flag": "分歧标注阈值",
    "injury_shift": "伤病偏移量",
}


@st.cache_resource
def get_pipeline():
    return PredictionPipeline()


pipe = get_pipeline()

# ============ P4·优先级一① + P0：@st.cache_data 缓存所有外部 API（football-data / ESPN） ============
# football-data TTL=3600（1小时）、ESPN TTL=1800（30分钟）——相同参数下前端刷新不消耗外部 API 额度。
# 参数均为字符串（可 hash，规避 pandas/complex-dict 缓存报错）；返回 list/dict 均可 pickle。
# 用实例属性保存"原始方法"，只在首次运行时捕获，避免每次重跑把已缓存方法当作原始方法而嵌套。
# 零密钥种子源（测试/无 token）无 fetch_competition_round → 跳过补丁（它本无外部网络请求）。
if hasattr(pipe.provider, "fetch_competition_round") and hasattr(pipe.provider, "fetch_competition_matches"):
    if getattr(pipe.provider, "_p4_orig", None) is None:
        pipe.provider._p4_orig = (pipe.provider.fetch_competition_round,
                                  pipe.provider.fetch_competition_matches)
    _p4_orig_fr, _p4_orig_fm = pipe.provider._p4_orig


    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_fr(comp: str, round_no: int) -> list:
        """缓存某一赛事某一轮对阵（德甲 BL1 / 欧冠 CL）· TTL=1小时。"""
        return _p4_orig_fr(comp, round_no)


    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_fm(comp: str) -> list:
        """缓存某一赛事全部比赛（疲劳特征用）· TTL=1小时。"""
        return _p4_orig_fm(comp)


    pipe.provider.fetch_competition_round = _cached_fr
    pipe.provider.fetch_competition_matches = _cached_fm


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_espn() -> dict | None:
    """缓存 ESPN 实时抓取结果（30 分钟内不重复请求 ESPN）；失败返回 None（调用层降级读本地 JSON）。"""
    return _live_fetch_candidates()


def _espn_fetch_with_fallback():
    """P1·数据源稳定性：ESPN 实时抓取(缓存) → 成功则更新本地 JSON；失败降级读本地 JSON 并给黄提示。
    返回 (payload, used_backup)。写文件副作用留在本调用层，缓存只包住无副作用的实时抓取。"""
    import datetime
    now = datetime.datetime.now().isoformat(timespec="seconds")
    live = _cached_espn()
    if live is not None:
        payload = {**live, "source": "espn", "updated_at": now}
        save_backup(payload)
        return payload, False
    bk = load_backup()
    cands = bk.get("candidates", [])
    used = bool(cands)
    if used:
        note = f"{bk.get('note','')}（⚠️ ESPN 抓取失败，当前使用本地缓存数据 · 缓存于 {bk.get('updated_at','?')}）"
    else:
        note = "ESPN 抓取失败且本地无历史缓存，请使用下方手动录入伤病。"
    return {"candidates": cands, "note": note, "espn_injuries_count": 0,
            "news_candidates_count": 0, "source": "backup", "updated_at": bk.get("updated_at", now)}, used


# P4·优先级二①：运气回归特征（近5场 射正HST代理 vs 实际进球），读 2526 赛季历史 CSV（已入库部署）。
# 用 @st.cache_resource 缓存（CSV 读取 + 计算，非外部 API，无需 TTL 过期）。
@st.cache_resource
def _luck_map() -> dict:
    from data.historical_data import luck_map
    try:
        return luck_map()
    except Exception:
        return {}

# ================= 顶部 =================
st.title("德甲预测机器人")
st.caption(DISCLAIMER)

# ---------- 验收条件 #1：数据时效 + 缓存标注 ----------
freshness = pipe.provider.freshness()
cached = freshness.get("cached", False)
label = f"数据最后拉取时间：{freshness.get('fetched_at','—')}"
if cached:
    label += "（缓存）"
st.markdown(f"🕓 **{label}**　·　来源：{freshness.get('source','—')}")

# ---------- 验收条件 #2：未标定阈值黄色警告横幅 ----------
uncal = pipe.round_global_summary(9)["uncalibrated_thresholds"]
if uncal:
    names = "、".join(UNCAL_LABEL.get(k, k) for k in uncal)
    st.warning(f"⚠️ **{names}未标定，仅供参考。**（calibrated=false，须真实德甲历史/伤病数据回测后定稿）")

# ================= 交互（st.session_state 状态切换） =================
ss = st.session_state
ss.setdefault("round", 9)
ss.setdefault("stage", "初版")
ss.setdefault("competition", "BL1")
ss.setdefault("manual_injuries", "")
ss.setdefault("auto_candidates", None)   # [{player, team_code, team_name, status, source, context}]
ss.setdefault("auto_note", "")
ss.setdefault("used_backup", False)
# P1·本地持久化兜底：刷新时自动载入上一次成功保存的手动录入（若当前为空且本地有历史）
_bk0 = load_backup()
if not ss["manual_injuries"] and _bk0.get("manual_injuries"):
    ss["manual_injuries"] = _bk0["manual_injuries"]

# ================= P6·本周德甲总览（周五点预测的主视图） =================
@st.cache_data(ttl=900, show_spinner=False)
def _week_overview_cached() -> dict:
    """缓存本周总览（含 ESPN 欧战赛程与阵容，15 分钟过期），避免每次 rerun 重抓。"""
    return pipe.week_overview(days_fwd=7, days_back=10, with_lineup=True)


_DATE_ZH = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


def _date_zh(d: str) -> str:
    import datetime
    try:
        dt = datetime.date.fromisoformat(d)
        return f"{dt.month}月{dt.day}日 {_DATE_ZH[dt.weekday()]}"
    except Exception:
        return d


def _hours_label(h) -> str:
    if h is None:
        return "无纪录"
    return f"{h:.1f}h（{h/24:.1f} 天）"


def _score_txt(euro: dict) -> str:
    sc = (euro or {}).get("score") or {}
    ft = sc.get("fullTime") or {}
    h, a = ft.get("home"), ft.get("away")
    if h is None or a is None:
        return ""
    return f"{h}-{a}"


def _rotation_risk(lineup: dict | None) -> tuple[str, str, str] | None:
    """由欧战首发里「被换下人数/首发人数」给轮换消耗弱评级（不进模型）。"""
    if not lineup or not lineup.get("available"):
        return None
    n = lineup.get("starter_count") or 0
    out = len(lineup.get("subbed_out") or [])
    if n < 11:
        return None
    ratio = out / n
    if ratio >= 0.45:
        return ("较高", "#B91C1C", f"欧战首发 {out}/{n} 人被换下，消耗信号偏强")
    if ratio >= 0.27:
        return ("中等", "#B45309", f"欧战首发 {out}/{n} 人被换下")
    return ("较低", "#047857", f"欧战首发 {out}/{n} 人被换下，主力基本踢完")


def _team_brief(it: dict) -> str:
    """一支球队本周疲劳概要（一句话）。"""
    bits = [f"休息 **{_hours_label(it.get('rest_hours'))}**"]
    pc = it.get("prev_competition_zh") or "—"
    if it.get("prev_is_euro"):
        bits.append(f"上一场 ⚽ **{pc}**" + ("（客场）" if it.get("prev_was_away") else "（主场）"))
    else:
        bits.append(f"上一场 {pc}")
    disc = it.get("fatigue_discount") or 0.0
    bits.append(f"疲劳 xG 折扣 **{disc:.0%}**" if disc else "疲劳折扣 0%")
    if it.get("prev_extra_time"):
        bits.append("⚠️ 上一场打满加时")
    if it.get("cl_preceding"):
        bits.append("⚠️ 欧冠前置，可能轮换")
    return " · ".join(bits)


def _render_week_overview() -> None:
    try:
        ov = _week_overview_cached()
    except Exception as e:
        st.error(f"本周总览加载失败：{e}")
        return

    if ov.get("note"):
        st.info("ℹ️ " + ov["note"])
        return

    st.markdown(f"### 🗓️ 本周德甲总览 · 共 {ov['count']} 场")
    st.caption(f"窗口 {ov['window'][0]} → {ov['window'][1]}　·　周中欧战已覆盖："
               + "、".join(f"{k} {v} 场" for k, v in (ov.get("euro_window") or {}).items())
               + "（欧冠用 football-data / 欧联·欧会用 ESPN 补齐）")

    if not ov.get("fatigue_calibrated"):
        st.warning("⚠️ **疲劳折扣未标定（calibrated=False），仅供参考。** "
                   + (ov.get("fatigue_note") or ""))

    # ---------- 本周受周中欧战影响的球队 ----------
    affected = []
    for d in ov["days"]:
        for m in d["matches"]:
            for it in (m["home"], m["away"]):
                if it.get("prev_is_euro") and it.get("rest_hours") is not None:
                    affected.append(it)
    if affected:
        st.markdown("#### ⚽ 本周受周中欧战影响的球队")
        rows = ["| 球队 | 周中欧战（对手 / 主客） | 赛果 | 距德甲 | 疲劳折扣 | 轮换消耗 |",
                "|---|---|---|---|---|---|"]
        for it in affected:
            le = it.get("last_euro") or {}
            comp = (le.get("competition_zh") or it.get("prev_competition_zh") or "欧战")
            opp = le.get("opponent") or "—"
            ha = "主场" if le.get("is_home") else "客场"
            risk = _rotation_risk(it.get("lineup"))
            risk_txt = (f"<span style='color:{risk[1]};font-weight:600'>{risk[0]}</span>"
                        if risk else "—")
            rows.append(
                f"| {it['team']} | {comp} {ha} vs {opp} | {_score_txt(le) or '—'} | "
                f"{_hours_label(it.get('rest_hours'))} | {(it.get('fatigue_discount') or 0):.0%} | {risk_txt} |")
        st.markdown("\n".join(rows), unsafe_allow_html=True)
        st.caption("「轮换消耗」只看该队欧战首发里被换下的人数比例，是弱信号；"
                   "ESPN 不提供逐人出场分钟与赛中伤情，德甲本场首发要开赛前 1 小时才有。")
    else:
        st.info("本周没有球队处于周中欧战之后的短间隔窗口。")

    # ---------- 逐日逐场 ----------
    st.markdown("#### 📅 逐场明细")
    for d in ov["days"]:
        st.markdown(f"**{_date_zh(d['date'])}**")
        for m in d["matches"]:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"**{m['home_team']} vs {m['away_team']}**")
                with c2:
                    st.markdown(f"`{(m['kickoff'] or '')[11:16]}`")
                st.markdown(f"🏠 {m['home_team']}：{_team_brief(m['home'])}")
                st.markdown(f"✈️ {m['away_team']}：{_team_brief(m['away'])}")

                # 伤病（沿用 football-data / ESPN 抓到的赛季伤病）
                inj = m.get("injuries") or {}
                hk = inj.get("home_key_out") or []
                ak = inj.get("away_key_out") or []
                if hk or ak:
                    st.markdown(f"🏥 缺阵：主 [{_fmt_inj_colored(hk)}] ／ 客 [{_fmt_inj_colored(ak)}]",
                                unsafe_allow_html=True)

                # 阵容与轮换风险
                lu_h = m["home"].get("lineup")
                lu_a = m["away"].get("lineup")
                risks = []
                for it, lu in ((m["home"], lu_h), (m["away"], lu_a)):
                    r = _rotation_risk(lu)
                    if r:
                        risks.append(f"{it['team']} <span style='color:{r[1]};font-weight:600'>{r[0]}</span>（{r[2]}）")
                if risks:
                    st.markdown("🔄 轮换风险提示：" + "；".join(risks), unsafe_allow_html=True)
                elif lu_h is not None or lu_a is not None:
                    st.caption("🔄 本周无双队欧战阵容可比对（至少一队无周中欧战）。")

                with st.expander("展开：欧战阵容名单与消耗明细"):
                    shown = False
                    for it, lu in ((m["home"], lu_h), (m["away"], lu_a)):
                        if not lu or not lu.get("available"):
                            continue
                        shown = True
                        le = it.get("last_euro") or {}
                        st.markdown(f"**{it['team']}**（{le.get('competition_zh','欧战')} "
                                    f"{'主场' if le.get('is_home') else '客场'} vs {le.get('opponent','—')}）")
                        st.caption("首发：" + "、".join(lu.get("starters") or []) or "—")
                        if lu.get("subbed_out"):
                            st.caption("被换下：" + "、".join(lu["subbed_out"]))
                        if lu.get("bench_used"):
                            st.caption("替补登场：" + "、".join(lu["bench_used"]))
                        st.caption(f"替补席 {lu.get('bench_size', 0)} 人 · 全场换人事件 {lu.get('sub_events', 0)} 次")
                    if not shown:
                        st.caption("暂无可用欧战阵容数据。")

    # ---------- 边界说明 ----------
    st.divider()
    with st.expander("ℹ️ 这份总览的口径与边界（务必看）"):
        st.markdown(
            "- **为什么要总览**：原来「德甲 / 欧冠」是单选入口，周五点德甲时看不到周中欧战的真实消耗；"
            "欧联/欧会此前被免费层挡在门外（EL=403 / UECL=404），现已用 ESPN 补齐。\n"
            "- **疲劳怎么算**：按「上一场任何赛事 → 本场」的**实际小时数**分桶折扣，"
            "不再用「休息天数 <3 天」那种永远触发不了的旧口径（周四欧联→周日恰好 3 天）。\n"
            "- **标定结果**：三季 918 场德甲 + 231 条欧战样本实测，欧战后 <96h 与正常一周一赛的"
            "进球差异 **z≈1.03，统计不显著**，分桶还非单调 → 所以 `calibrated=False`，折扣是"
            "「实测分桶 × 0.3 收缩、封顶 4%」的保守量级，**不是**拟合出来的强效应。\n"
            "- **做得到 / 做不到**：欧战赛程、比分、阵容（首发/换人）可拿到；"
            "**逐人出场分钟、赛中伤病拿不到**（ESPN 无该事件类型），所以伤病只有赛前缺阵名单，"
            "赛中受伤只能靠「被提前换下」这种弱信号提示 + 你自己确认。\n"
            "- **阵容不进模型**：只作轮换风险提示，不参与概率计算。")


def _view_switch() -> str:
    return st.radio("视图", ["🗓️ 本周德甲总览", "🎯 单赛事预测"],
                    horizontal=True, index=0,
                    help="总览=周五一次看本周全部德甲（含周二/三/四欧战消耗）；"
                         "单赛事=原来的逐场预测（德甲 / 欧冠单选）")


VIEW = _view_switch()
if VIEW.startswith("🗓️"):
    _render_week_overview()
    st.stop()

# ---------- 跨赛事切换器（P2·跨赛事） ----------
from config import COMPETITIONS, FATIGUE_RULES
comp_labels = {k: f"{COMPETITIONS[k]['name']} · {COMPETITIONS[k]['note']}" for k in COMPETITIONS}
comp_sel = st.radio("赛事", list(comp_labels.keys()), index=0, format_func=lambda k: comp_labels[k])
ss["competition"] = comp_sel

col_r, col_s = st.columns([2, 1])
with col_r:
    r = st.selectbox("选择轮次", range(1, 35), index=ss["round"] - 1)
    ss["round"] = r
with col_s:
    stage_sel = st.radio("阶段", ["初版", "确认版", "⚡ 终极版（首发反推）"], horizontal=True,
                         index={"初版": 0, "确认版": 1, "终极版": 2}.get(ss.get("stage", "初版"), 0),
                         key="stage_radio",
                         help="终极版：用赛前约 1 小时公布的官方首发，反推常规主力缺失（替补席折半），"
                              "自动折算 xG 折扣；抓不到首发则自动回退到确认版/初版逻辑")
    ss["stage"] = "终极版" if stage_sel.startswith("⚡") else stage_sel

# ---------- 伤病获取：ESPN 自动抓取 + 手动录入兜底 ----------
with st.expander("🏥 伤病获取（ESPN 自动抓取 + 手动录入兜底）"):
    # 方案三：ESPN 自动抓取伤病候选（国内直连，无 token）——@st.cache_data(1800s) + 本地 JSON 兜底
    if st.button("🔎 自动抓取伤病候选（ESPN 国内直连）",
                 disabled=ss.get("_running", False)):
        with st.spinner("正在抓取 ESPN 伤病接口 + 德甲新闻……"):
            try:
                res, used_backup = _espn_fetch_with_fallback()
            except Exception:
                st.error("⚠️ 当前请求过载，请 1 分钟后再试。")
                st.stop()
            ss["auto_candidates"] = res["candidates"]
            ss["auto_note"] = res["note"]
            ss["used_backup"] = used_backup
    if ss["auto_note"]:
        (st.warning if ss.get("used_backup") else st.info)(ss["auto_note"])
    if ss["auto_candidates"]:
        st.markdown("**勾选要纳入确认版复算的伤病候选，并为每个球员标注重要性（核心/轮换/替补，影响 injury_shift）：**")
        for i, c in enumerate(ss["auto_candidates"]):
            col_chk, col_imp = st.columns([3, 1])
            with col_chk:
                st.checkbox(
                    f"{c['player']}（{c['team_name']} · {'缺阵' if c['status']=='key_out' else '存疑'}）· {c['source']}",
                    value=True, key=f"cand_{i}",
                    help=c.get("context", ""))
            with col_imp:
                st.selectbox("重要性", IMPORTANCE_LEVELS, index=0,
                             key=f"cand_imp_{i}", label_visibility="collapsed")
    else:
        st.caption("（尚未抓取 / 当前 ESPN 无德甲伤病记录 → 请用下方手动录入）")
    st.caption("说明：ESPN 足球侧没有结构化伤病接口（实测 `/soccer/ger.1/injuries` 恒返回 0 条、"
               "官方伤病页 404），自动抓取只能从德甲新闻里提取**文字线索**，准确性有限，"
               "请以手动录入为准。")
    st.divider()
    st.markdown("**手动录入（兜底）**　格式：`队名: 缺阵球员,存疑球员?`；重要性用 `球员[轮换]` 标注。　例：`拜仁: 凯恩[核心],穆夏拉[轮换],格纳布里?[替补]`")

    def _persist_manual():
        import datetime
        bk = load_backup()
        bk["manual_injuries"] = st.session_state.get("manual_injuries", "")
        bk["manual_updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_backup(bk)

    ss["manual_injuries"] = st.text_area(
        "伤病名单（空 = 无实时伤病，确认版将被拦截）", ss["manual_injuries"],
        height=80, placeholder="美因茨: 布卡特[核心]\n拜仁: 凯恩,穆夏拉[轮换]",
        on_change=_persist_manual)
    if ss["manual_injuries"].strip():
        parsed_txt = _parse_manual_injuries(ss["manual_injuries"])
        bad_lines = _manual_injuries_unparsed(ss["manual_injuries"], parsed_txt)
        if parsed_txt:
            st.success("✅ 已录入伤病（实时保存到本地 JSON，刷新不丢失），确认版复算将启用（R2 injury_shift 按球员重要性加权，未标定）")
            st.caption("解析结果：" + "；".join(f"{c} 缺阵{len(v['key_out'])}人/存疑{len(v['doubtful'])}人"
                                              for c, v in parsed_txt.items()))
        if bad_lines:
            st.error("⚠️ 以下行未能解析（队名未识别或缺英文冒号分隔），这些行的伤病**不会生效**："
                     + " ／ ".join(bad_lines)
                     + "　正确格式：`拜仁: 凯恩[核心],穆夏拉?[轮换]`")

# 数据源能力说明
auto_has = bool(ss["auto_candidates"]) and any(st.session_state.get(f"cand_{i}", False)
                                               for i in range(len(ss["auto_candidates"])))
manual_ok = bool(_parse_manual_injuries(ss["manual_injuries"]))
realtime_injury = (pipe.provider.realtime_injury_available or manual_ok or auto_has)
if ss["manual_injuries"].strip() and not manual_ok:
    st.error("⚠️ 伤病文本已填写但**没有任何一行能被解析**（队名未识别或缺英文冒号），"
             "本次会按无伤病处理。请按 `队名: 球员` 格式修正上方红字提示的行。")
if not pipe.provider.realtime_injury_available:
    st.caption("数据源说明：football-data.org 不提供伤病接口；TheSportsDB(403)/Transfermarkt(Cloudflare)/"
               "Google新闻/新浪均被网络阻断；已接入 **ESPN 直连自动抓取**（上方按钮）+ 手动录入兜底。")

# ---------- 验收条件 #3：确认版拦截（无实时伤病亦无手动录入） ----------
if ss["stage"] == "确认版" and not realtime_injury:
    st.warning("⛔ **暂无赛前 48h 实时伤病数据，无法生成确认版。** "
               "请在上方手动录入伤病名单（方案 C），否则本次生成会被逐场降级为初版（卡片右上角会标 [初版] 并在下方给出拦截提示）。")

# ---------- P5.2：常规主力库自动重建 + 手动刷新（主区常显，不放 expander 里） ----------
try:
    import config as _blcfg
    from data.baseline import (load_baseline as _load_bl,
                               baseline_staleness as _bl_stale,
                               maybe_auto_rebuild as _bl_auto,
                               rebuild_now as _bl_now,
                               rebuild_status as _bl_status)
    _bl = _load_bl() or {}
    _rs = _bl_status() or {}
    _bl_stale_info = _bl_stale(_bl, ss.get("round"))
    _auto_on = bool(_blcfg.LINEUP_SHIFT.get("auto_rebuild", True))

    if _rs.get("status") == "running":
        st.info(f"🔄 **常规主力库正在后台重建**…（启动于 {_rs.get('started_at', '')}，约 1–3 分钟；"
                "完成后刷新页面即生效，期间预测照旧用旧基线，不会失败）")
    elif _rs.get("status") == "done":
        st.caption(f"✅ 上次重建成功：{_rs.get('teams_ok', '?')} 队 · {_rs.get('updated_at', '')}"
                   f"（{'手动' if _rs.get('trigger') == 'manual' else '自动'}）")
    elif _rs.get("status") == "failed":
        st.caption(f"⚠️ 上次重建未完成：{_rs.get('error', '未知原因')}（**已保留原基线**，不影响预测）")

    # 陈旧检测：每个会话首次发现陈旧时，自动起一个后台线程（只试一次，避免反复触发）
    if _bl_stale_info.get("stale"):
        if not ss.get("_bl_auto_tried"):
            ss["_bl_auto_tried"] = True
            _res = _bl_auto(ss.get("round")) if _auto_on else {"started": False, "reason": "disabled"}
            if _res.get("started"):
                st.info(f"🔄 检测到基线已陈旧（{_bl_stale_info.get('reason')}），"
                        "已在后台自动重建，可继续正常预测。")
            elif _res.get("reason") == "busy":
                st.caption("另有重建任务正在运行，等它结束即可。")
            elif _res.get("reason") == "disabled":
                st.warning(f"⏳ 常规主力库已陈旧（{_bl_stale_info.get('reason')}）。"
                           "自动重建已关闭，请点下方「🔄 更新主力基线」按钮。")
        elif _rs.get("status") not in ("running", "done"):
            st.warning(f"⏳ 常规主力库已陈旧（{_bl_stale_info.get('reason')}），"
                       "建议点下方「🔄 更新主力基线」重建一次。")

    _c1, _c2 = st.columns([1, 3])
    with _c1:
        _bl_do = st.button("🔄 更新主力基线", key="btn_bl_rebuild",
                           disabled=bool(_rs.get("status") == "running"),
                           help=f"重新抓取近 {_bl.get('window_matches', '?')} 场首发 + 18 队当前大名单，"
                                "重建常规主力库（约 1–3 分钟）。重建失败会保留旧基线。")
    with _c2:
        st.caption(f"常规主力库：近 {_bl.get('window_matches', '?')} 场 · 核心阈值 "
                   f"{_bl.get('core_rate', '?')}（≈ 6 场里首发 ≥5 场）· 更新于 {_bl.get('updated_at', '—')}"
                   f" · 记录轮次 {_bl.get('built_round', '—')}"
                   "　｜　重建结果写入 `data/baseline_xi.json`；公网容器重启会回退到仓库版本，"
                   "需本地重建后 push 才永久生效")
    if _bl_do:
        _ph = st.empty()
        with st.spinner("正在重建常规主力库（约 1–3 分钟）……"):
            _r = _bl_now(ss.get("round"), progress=lambda m: _ph.caption(m))
        if _r.get("ok"):
            _ph.success("✅ " + _r.get("message", "已更新"))
            st.rerun()
        else:
            _ph.error("⚠️ " + _r.get("message", "重建失败（已保留旧基线）"))
except Exception as _e:
    st.caption(f"常规主力库面板异常（不影响预测）：{type(_e).__name__}: {_e}")

# ---------- P5：赛前 1 小时终极刷新 ----------
with st.expander("⚡ 赛前 1 小时终极刷新（用官方首发反推常规主力缺失）", expanded=False):
    st.markdown(
        "**原理**：ESPN 没有结构化伤病接口，但 `summary` 里有逐人首发/替补名单（赛前约 60 分钟公布）。"
        "系统把「本场 11 人首发」与「近 6 场统计出的常规主力库」比对：\n\n"
        "- 常规主力在首发 → 战力满分\n"
        "- 常规主力在替补席 → 扣一半（下半场能上，含超级替补价值）\n"
        "- 常规主力完全不在名单 → 全额扣（约 0.10 DC 折扣/人，按历史首发率加权）\n\n"
        "**核心口径**：近 6 场首发率 ≥ 0.7（等价「6 场里至少首发 5 场」）才算核心，"
        "正常战术轮换不进核心集合、不参与扣分。\n\n"
        "**转会窗容错**：基线是静态快照，卖走的人会永远留在库里。所以扣分前先用 ESPN 当前大名单校验——"
        "已离队的直接剔出、不扣分；本场首发但基线里从未出现过的人标为「新面孔首发」（新援/奇兵），只提示不加分。\n\n"
        "把上面阶段切到 **⚡ 终极版** 后点「生成预测」即触发；抓不到首发会自动回退，不会编造。")
    _bl = None
    try:
        from data.baseline import load_baseline, BASELINE_FILE
        _bl = load_baseline()
        if _bl.get("teams"):
            _ok = sum(1 for v in _bl["teams"].values() if v.get("available"))
            _checked = sum(1 for v in _bl["teams"].values()
                           if ((v.get("roster") or {}).get("checked") == "ok"))
            st.caption(f"常规主力库：{_ok}/{len(_bl['teams'])} 队可用 · 近 {_bl.get('window_matches')} 场统计 · "
                       f"更新于 {_bl.get('updated_at')} · 大名单已校验 {_checked} 队"
                       "（样本不足的队会回退为不扣分）")
            # 快照时效提醒：超过宽限期提示重建（转会窗期间尤其重要）
            try:
                from datetime import datetime as _dt
                import config as _cfg
                _grace = float(_cfg.LINEUP_SHIFT.get("roster_ttl_grace_hours", 72))
                _age = (_dt.now() - _dt.strptime(_bl.get("updated_at"), "%Y-%m-%d %H:%M")).total_seconds() / 3600
                if _age > _grace:
                    st.warning(f"⏳ 常规主力库已 {_age/24:.1f} 天未重建（超过 {_grace/24:.0f} 天宽限），"
                               "转会窗期间阵容可能已变 —— 点上方的「🔄 更新主力基线」按钮即可（不必敲命令行）。")
            except Exception:
                pass
        else:
            st.caption("常规主力库尚未构建：点上方的「🔄 更新主力基线」按钮生成（约 1–3 分钟）后即可使用。")
    except Exception:
        st.caption("常规主力库读取失败（不影响预测，只是首发反推不生效）")

# ================= 预测输出 =================
# P4·优先级一②③：按钮防抖禁用 + 全局异常兜底（超限红条，不抛堆栈）
if st.button("生成预测", type="primary", disabled=ss.get("_running", False)):
    if ss.get("_running"):
        st.info("正在生成中，请稍候……")
        st.stop()
    ss["_running"] = True
    manual = _parse_manual_injuries(ss["manual_injuries"])
    checked = []
    if ss["auto_candidates"]:
        checked = [dict(c, importance=st.session_state.get(f"cand_imp_{i}", "核心"))
                   for i, c in enumerate(ss["auto_candidates"])
                   if st.session_state.get(f"cand_{i}", False)]
    merged = _merge_injuries(manual, checked)
    try:
        with st.spinner("AI 正在拉取数据并计算……"):
            result = pipe.predict_round(ss["round"], stage=ss["stage"],
                                        manual_injuries=merged, competition=ss["competition"],
                                        luck_map=_luck_map(),
                                        with_lineup=(ss["stage"] == "终极版"))
    except Exception:
        ss["_running"] = False
        st.error("⚠️ 当前请求过载，请 1 分钟后再试。")
        st.stop()
    ss["_running"] = False
    st.info(f"🕓 {result['freshness_label']}")

    # 免费层受限赛事（EL/DFB/欧协）→ 诚实提示
    if result.get("comp_unavailable"):
        st.warning(f"⛔ **{COMPETITIONS.get(ss['competition'], {}).get('name','该赛事')} 免费层不可用**："
                   f"{result.get('comp_note')}。请使用德甲(BL1)或欧冠(CL)。")
    else:
        cards = result["cards"]
        st.markdown(f"本轮共 **{len(cards)}** 场预测"
                    + ("（欧冠跨赛事 Elo）" if ss["competition"] == "CL" else "（德甲）"))

    # ---------- 本轮推荐速览表（顶部，便于一键截图；用 Markdown 表格，避免 pyarrow/NumPy 兼容问题） ----------
    if not result.get("comp_unavailable") and cards:
        _n_lu_top = sum(1 for c in cards if (c.get("lineup") or {}).get("available"))
        # P5·伤病提醒横幅：赛前未抓到任何伤病数据 → 醒目提示（初版纯基准，建议录入/抓取生成确认版）
        if not realtime_injury:
            if ss["stage"] == "终极版" and _n_lu_top:
                st.error(f"⚡ **无实时伤病数据，但已用官方首发反推**常规主力缺失（{_n_lu_top}/{len(cards)} 场），"
                         "每场的「首发对比」条会列出谁在替补席/不在名单及折算的 xG 扣减。")
            else:
                st.error("⚠️ **暂无实时伤病数据，当前为初版纯基准概率，建议手动录入伤病或点击自动抓取以生成确认版！**")
        # P3·移动端：卡片式布局替代宽表格（st.container border + st.progress，无需横向滑动）
        st.markdown("### 📋 本轮推荐速览（胜平负 + 推荐比分）")
        for idx, c in enumerate(cards, 1):
            p = c["probabilities"]
            top = c["score_top2"][0]["score"] if c.get("score_top2") else "—"
            pick = c["pick"]
            with st.container(border=True):
                hl, hr = st.columns([3, 1])
                hl.markdown(f"**{idx}. {c['home_team']} vs {c['away_team']}**")
                hr.markdown(f"阶段 `{c['stage']}`",
                            help="初版=基准；确认版=含伤病复算；终极版=赛前官方首发反推")
                st.markdown(f"推荐 **{pick}** · 最可能比分 `{top}`")
                st.progress(p["home_win"], text=f"主胜 {p['home_win']:.0%}")
                st.progress(p["draw"], text=f"平局 {p['draw']:.0%}")
                st.progress(p["away_win"], text=f"客胜 {p['away_win']:.0%}")
        st.caption("胜/平/负为概率，推荐取三者概率最高的一项（平局也参与比较）；最可能比分取 Top1（分布最集中区域），与推荐方向可能不同。点开下方卡片可看伤病/解释等细节。")

    # 确认版被拦截的整体提示
    blocked = [c for c in cards if c.get("confirm_blocked")]
    if blocked:
        st.warning(f"⚠️ 有 {len(blocked)} 场因**暂无赛前 48h 实时伤病数据**被降级为初版，未生成确认版。")

    # P5·终极版：首发覆盖情况（没抓到就如实说明已回退）
    if ss["stage"] == "终极版" and not result.get("comp_unavailable"):
        n_lu = sum(1 for c in cards if (c.get("lineup") or {}).get("available"))
        if n_lu == 0:
            from data.lineup import minutes_to_kickoff
            _next = None
            for _c in cards:
                _m = minutes_to_kickoff(_c.get("kickoff") or "")
                if _m is not None and _m > 0 and (_next is None or _m < _next[0]):
                    _next = (_m, _c)
            if _next:
                _m, _c = _next
                _when = (f"约 {_m/60:.1f} 小时（{int(_m)} 分钟）" if _m >= 60
                         else f"{int(_m)} 分钟")
                st.warning(
                    f"⏳ **官方首发还没出，现在点还太早。** 最近一场是 {_c['home_team']} vs {_c['away_team']}，"
                    f"距开球还有**{_when}**；ESPN 一般要等到开球前 60 分钟内才填名单。\n\n"
                    "已按无首发回退：当前仅用手动录入 / 基准概率，未凭空推断谁缺阵。"
                    f"请在开球前 1 小时内再点一次。")
            else:
                st.warning("⚠️ 本轮**没有任何一场拿到官方首发**（ESPN 在开球前约 60 分钟才填名单）。"
                           "已按无首发回退：仅用上面的手动录入 / 基准概率，未凭空推断谁缺阵。"
                           "请在开球前 1 小时内再点一次。")
        elif n_lu < len(cards):
            st.success(f"✅ 已用官方首发反推 **{n_lu}/{len(cards)}** 场；其余 {len(cards)-n_lu} 场官方首发尚未公布，按回退处理。")
        else:
            st.success(f"✅ 全部 {n_lu} 场都用官方首发反推完成。")

    # 每场卡片（P4·优先级三①：折叠面板，标题带推荐方向与概率；展开才看明细）
    for c in cards:
        title = (f"{c['home_team']} vs {c['away_team']}"
                 if c.get("home_team") != "—" else f"第{c['match_id'].split('_R')[-1]}轮 · 无数据基准")
        if c["pick"] == "基准":
            title_pick = "基准"
        else:
            pp = c["probabilities"]
            # 推荐方向取三者最大，概率直接用 pick_prob（平局也正确）
            _dir = c.get("pick_dir") or max(("home_win", "draw", "away_win"), key=lambda k: pp[k])
            title_pick = f"{c['pick']} ({c.get('pick_prob', pp[_dir]):.0%})"
        with st.expander(f"{title} | {title_pick} [{c['stage']}]"):
            # ---------- 验收条件 #2：卡片内未标定阈值黄色徽章 ----------
            if uncal:
                badge = "⚠️ 伤病偏移量未标定，仅供参考"
                st.markdown(
                    f'<span style="background:#FEF3C7;color:#92400E;border:1px solid #F59E0B;'
                    f'padding:2px 10px;border-radius:14px;font-size:12px;font-weight:600;">{badge}</span>',
                    unsafe_allow_html=True)
            p = c["probabilities"]
            # 跨赛事疲劳/欧冠前置/加时赛标记
            fatigue = c.get("fatigue", {})
            markers = []
            if fatigue.get("cl_preceding_home"):
                markers.append(f"⚠️ {c['home_team']} 欧冠前置，可能轮换（已标定折入轮换折扣）")
            if fatigue.get("cl_preceding_away"):
                markers.append(f"⚠️ {c['away_team']} 欧冠前置，可能轮换（已标定折入轮换折扣）")
            if fatigue.get("home_prev_extra_time"):
                markers.append(f"⚠️ {c['home_team']} 上一场打满120分钟加时 → 极端疲劳惩罚")
            if fatigue.get("away_prev_extra_time"):
                markers.append(f"⚠️ {c['away_team']} 上一场打满120分钟加时 → 极端疲劳惩罚")
            if fatigue.get("home_fatigue_discount", 0) or fatigue.get("away_fatigue_discount", 0):
                markers.append(
                    f"疲劳xG折扣 主{fatigue.get('home_fatigue_discount')}/客{fatigue.get('away_fatigue_discount')}"
                    f"（休息 主{fatigue.get('home_rest_hours')}h/客{fatigue.get('away_rest_hours')}h"
                    f"，上一场 主{fatigue.get('home_prev_competition_zh','—')}/"
                    f"客{fatigue.get('away_prev_competition_zh','—')}）")
            # P6·口径未标定 → 卡片内如实标注
            if fatigue.get("home_prev_is_euro") or fatigue.get("away_prev_is_euro"):
                markers.append("⚽ 含周中欧战（欧冠/欧联/欧会）消耗；疲劳折扣未标定，请看总览页的口径说明")
            # P4·优先级二②：战意规则硬扣标记
            if fatigue.get("home_motivation_penalty"):
                markers.append(f"⚠️ {c['home_team']} 战意不足：欧冠前3天联赛+对手弱旅 → 扣xG {fatigue['home_motivation_penalty']}")
            if fatigue.get("away_motivation_penalty"):
                markers.append(f"⚠️ {c['away_team']} 战意不足：欧冠前3天联赛+对手弱旅 → 扣xG {fatigue['away_motivation_penalty']}")
            # P4·优先级二①：运气回归（触底反弹）标记
            lk = c.get("luck", {})
            if lk.get("home_rebound"):
                markers.append(f"📈 {c['home_team']} 进攻端触底反弹（近5场 gap {lk['home_gap']}）")
            if lk.get("away_rebound"):
                markers.append(f"📈 {c['away_team']} 进攻端触底反弹（近5场 gap {lk['away_gap']}）")
            if markers:
                st.warning("；".join(markers))
            # ---------- P5：首发对比（终极版核心展示） ----------
            lu = c.get("lineup") or {}
            if lu.get("available"):
                _lines = [x for x in (lu.get("detail_lines") or {}).values() if x]
                _has_shift = (c.get("lineup_shift") or {}).get("home") or (c.get("lineup_shift") or {}).get("away")
                _txt = "⚠️ **首发对比**：" + " ｜ ".join(_lines)
                if _has_shift:
                    _txt += (f"　→ 已自动扣除 xG：{c['home_team']} -{lu.get('xg_home', 0):.2f}"
                             f" / {c['away_team']} -{lu.get('xg_away', 0):.2f}")
                    st.error(_txt)
                else:
                    st.success(_txt)

                # ---- P5.1 转会窗容错：新援/奇兵提示 + 已离队剔除说明 ----
                _nc = [x for x in (lu.get("newcomer_lines") or {}).values() if x]
                if _nc:
                    st.info("🆕 **新面孔首发**：" + " ｜ ".join(_nc))
                _dep = []
                for _side, _name in (("home", c.get("home_team")), ("away", c.get("away_team"))):
                    _dev = (lu.get(_side) or {})
                    for _p in (_dev.get("departed") or []):
                        _dep.append(f"{_name}·{_p.get('name')}")
                if _dep:
                    st.caption("↩️ 已不计扣分（不在当前大名单）：" + "、".join(_dep[:8])
                               + (f" 等 {len(_dep)} 人" if len(_dep) > 8 else ""))
                # 阵容洗牌 → 基线过期提醒
                _dr = [d for d in (lu.get("roster_drift") or {}).values() if d and d.get("drift")]
                if _dr:
                    st.warning("♻️ **该队阵容变动较大**（基线 XI 已离队比例过高），"
                               "建议点上方的「🔄 更新主力基线」重建一次，否则新援不会被识别为常规主力。")
            st.markdown(f"**胜/平/负**　主胜 `{p['home_win']}` · 平 `{p['draw']}` · 客胜 `{p['away_win']}`")
            if ss["competition"] == "CL":
                st.caption("欧冠跨赛事预测：德甲队用模型 Elo，其他联赛对手用精选豪门/联赛平均 Elo 兜底（未实时校准）。")
            # 确认版/终极版：展示初版→当前阶段概率对比（injury_shift）
            if c.get("confirm_probs"):
                bp = c["base_probs"]
                ish = c.get("injury_shift", {})
                _stage_word = "终极版" if c.get("stage") == "终极版" else "确认版"
                st.markdown(f"**初版→{_stage_word}**　" + "　".join(
                    f"`{'主胜' if k=='home_win' else ('平局' if k=='draw' else '客胜')}`"
                    f"{bp[k]}→{c['confirm_probs'][k]}"
                    for k in ("home_win", "draw", "away_win")))
                st.markdown(f"**折扣偏移（来源：{c.get('shift_source', '—')}）**　主`{ish['home']}` / 客`{ish['away']}`"
                            + ("（首发反推 / R2 口径，未标定）" if c.get("stage") == "终极版"
                               else "（R2 injury_shift，未标定）"))
            st.markdown(f"**比分 Top2**　" + "　".join(f"`{s['score']}`({s['prob']})" for s in c["score_top2"]))
            st.markdown(f"**让球**　`{c['handicap_direction']['line']}` · 谨慎度 `{c['handicap_direction']['caution']}`")
            st.markdown(f"**数据**　{c['data_availability']} · 伤病 "
                        f"主缺阵[{_fmt_inj_colored(c['injuries'].get('home_key_out',[]))}] / "
                        f"客缺阵[{_fmt_inj_colored(c['injuries'].get('away_key_out',[]))}]"
                        f"（<span style='color:#dc2626'>红</span>=核心 · "
                        f"<span style='color:#ea580c'>橙</span>=轮换 · "
                        f"<span style='color:#6b7280'>灰</span>=替补）",
                        unsafe_allow_html=True)
            st.markdown(f"**风险**　{c['risk_note']}")
            # P4·优先级二①：预测解释（Explainable AI）——每卡底部可读理由
            st.markdown(_build_explanation(c, ss["competition"]))
            st.caption(f"model_breakdown: elo={c['model_breakdown']['elo_home']}/"
                       f"{c['model_breakdown']['elo_draw']}/{c['model_breakdown']['elo_away']} · "
                       f"dc_score_top2={c['model_breakdown']['dc_score_top2']}")

# ================= P4·优先级二③：历史回测看板（静态结果，非实时计算，避免拖慢主应用） =================
@st.cache_resource
def _backtest_report() -> dict:
    import json, os
    p = os.path.join(os.path.dirname(__file__), "data", "backtest_report.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_resource
def _calibration_report() -> dict:
    """P2·injury_shift 敏感性标定报告（静态 JSON，只读渲染）。"""
    import json, os
    p = os.path.join(os.path.dirname(__file__), "data", "calibration_report.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ================= P6：真实赛果（live_results.json 入库 + 与 bias.db 历史合并） =================
@st.cache_resource
def _live_results_records() -> tuple:
    """P6·真实赛果：live_results.json（入库、公网可持久）+ 与 bias.db 历史合并。
    bias.db 为运行时库（不入库，云端重建为空），故真实赛果以入库 JSON 为准。"""
    from data.live_results import load_live_results, merge_with_bias
    lr = load_live_results()
    return lr, merge_with_bias(lr)


# ================= P2③：侧边栏「模型历史表现」页签（胜率/爆冷/标定摘要；ROI 因无历史赔率如实标注不可算） =================
with st.sidebar:
    st.header("📊 模型历史表现")
    btrep = _backtest_report()
    if btrep:
        st.metric("方向命中率", f"{btrep.get('direction_accuracy', 0) * 100:.1f}%")
        st.metric("爆冷率", f"{btrep.get('upset_rate', 0) * 100:.1f}%")
        st.metric("回测场次", btrep.get("games", "—"))
        ssum = btrep.get("season_summary", [])
        if ssum:
            st.caption("按赛季：" + " · ".join(f"{s['season']} {s['acc'] * 100:.0f}%" for s in ssum))
        div = btrep.get("divergence", {})
        st.caption(f"Elo/DC分歧场爆冷 {div.get('div_upset', 0) * 100:.1f}% vs 一致场 {div.get('no_div_upset', 0) * 100:.1f}%")
    else:
        st.caption("（回测报告未生成，本地跑 scripts/backtest_report.py）")
    st.divider()
    cal = _calibration_report()
    if cal:
        st.markdown("**injury_shift 敏感性**（合成场景，非真实标定）")
        st.caption(f"{cal.get('games', '—')} 场 · 网格内 Log Loss 差 {cal.get('baseline', {}).get('logloss', '—')} → "
                   f"{cal.get('best', {}).get('logloss', '—')}，差异极小")
        st.caption("⚠️ 无历史真实伤病名单 → 维持经验值，**calibrated=False**（诚实黄牌）")
    st.divider()
    lr, merged = _live_results_records()
    if lr:
        picked = [r for r in lr if r.get("pred_pick")]
        hits = sum(1 for r in picked if r.get("pred_hit") == 1)
        st.markdown("**🏁 真实赛果记录**（live_results.json 入库）")
        st.caption(f"{len(lr)} 场实战 · 已预测 {len(picked)} 场 · 命中 {hits}/{max(len(picked),1)}"
                   f"（与 bias.db 历史合并共 {len(merged)} 条）")
        for r in lr[-3:]:
            pick = r.get("pred_pick")
            if pick:
                mark = "✅" if r.get("pred_hit") == 1 else "❌"
                tail = f"· 预测{pick} {mark}"
            else:
                tail = "· 未提供赛前预测"
            st.caption(f"{r.get('season')} R{r.get('round_no')} {r.get('home')} "
                       f"{r.get('actual_hg')}-{r.get('actual_ag')} {r.get('away')}{tail}")
    else:
        st.caption("🏁 暂无真实赛果记录（新赛果经 scripts/record_live_result.py 记录并推送）")
    st.caption("ROI：数据源无历史赔率，无法真实计算（未虚构）")


with st.expander("📊 历史回测看板（近3赛季 · 918场 walk-forward · 静态结果）"):
    btrep = _backtest_report()
    if not btrep:
        st.caption("（历史回测报告未生成——本地运行 `python3 scripts/backtest_report.py` 生成 "
                   "`data/backtest_report.json` 后自动显示；静态渲染不拖慢主页面）")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("回测场次", btrep.get("games", "—"))
        c2.metric("方向命中率", f"{btrep.get('direction_accuracy', 0) * 100:.1f}%")
        c3.metric("爆冷率", f"{btrep.get('upset_rate', 0) * 100:.1f}%")
        c4.metric("R4分歧阈值", btrep.get("r4", {}).get("threshold", "—"))
        st.markdown("**按轮次方向命中率**")
        rcs = btrep.get("round_curve", [])
        if rcs:
            bars = []
            for c in rcs:
                w = int(c["acc"] * 100)
                bars.append(
                    f'<div style="display:flex;align-items:center;margin:2px 0;font-size:12px;">'
                    f'<span style="width:34px;">R{c["round"]}</span>'
                    f'<div style="width:60%;background:#e5e7eb;border-radius:4px;height:12px;">'
                    f'<div style="width:{w}%;background:#2563eb;height:12px;border-radius:4px;"></div></div>'
                    f'<span style="margin-left:8px;">{c["acc"] * 100:.0f}% (n={c["n"]})</span></div>')
            st.markdown("".join(bars), unsafe_allow_html=True)
        ssum = btrep.get("season_summary", [])
        if ssum:
            st.markdown("**按赛季**")
            st.markdown("| 赛季 | 场次 | 命中率 | 爆冷率 |")
            st.markdown("|---|---|---|---|")
            for s in ssum:
                st.markdown(f"| {s['season']} | {s['n']} | {s['acc'] * 100:.1f}% | {s['upset'] * 100:.1f}% |")
        div = btrep.get("divergence", {})
        st.caption(f"Elo/DC **分歧**场爆冷率 {div.get('div_upset', 0) * 100:.1f}% vs "
                   f"**一致**场 {div.get('no_div_upset', 0) * 100:.1f}%"
                   f"；R1 偏差惯性基线错率 {btrep.get('r1', {}).get('baseline_wrong_rate', 0) * 100:.1f}%"
                   f"（更新于 {btrep.get('generated_at', '—')}）")

# ================= P4·优先级三②：合规免责声明 =================
st.markdown("---")
st.caption("🤖 预测基于概率模型，仅供参考。足球反买，别墅靠海。")
