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


IMPORTANCE_LEVELS = ["核心", "轮换", "替补"]


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


def _parse_importance(player: str) -> tuple[str, str]:
    """解析球员条目里的重要性标注 [核心]/[轮换]/[替补]，无标注默认核心。"""
    for tag in IMPORTANCE_LEVELS:
        marker = f"[{tag}]"
        if marker in player:
            return player.replace(marker, "").strip(), tag
    return player.strip(), "核心"


def _parse_manual_injuries(text: str) -> dict | None:
    """解析手动录入伤病文本 → {code: {"key_out":[...],"doubtful":[...]}}，条目含重要性。
    格式每行：`队名关键词: 缺阵球员,存疑球员?`；重要性用 `球员[轮换]` 标注。空 → None。"""
    from data.seed_data import TEAMS
    from data.api_client import FDO_NAME2CODE, EXTRA_TEAMS_ZH
    text = (text or "").strip()
    if not text:
        return None
    # 名称关键词 → code（优先中文/种子名，其次真实 API 名）
    code_by_key = {}
    for c, zh in {**TEAMS, **EXTRA_TEAMS_ZH}.items():
        code_by_key[zh] = c
    for raw, c in FDO_NAME2CODE.items():
        code_by_key[raw] = c
        code_by_key.setdefault(raw.replace(" ", ""), c)
        code_by_key.setdefault(raw.split()[-1], c)   # "Bayern München" → "München"
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        team_part, players = line.split(":", 1)
        team_part = team_part.strip()
        code = None
        for key, c in code_by_key.items():
            # 双向子串匹配：既支持"拜仁慕尼黑"（全名）也支持"拜仁"（简称/关键词）
            if key and (key in team_part or team_part in key):
                code = c
                break
        if code is None:
            continue
        key_out, doubtful = [], []
        for p in players.split(","):
            p = p.strip()
            if not p:
                continue
            if p.endswith("?") or p.startswith("存疑"):
                name, imp = _parse_importance(p.strip("?存疑 "))
                doubtful.append({"name": name, "importance": imp})
            else:
                name, imp = _parse_importance(p)
                key_out.append({"name": name, "importance": imp})
        out[code] = {"key_out": key_out, "doubtful": doubtful}
    return out or None


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
    stage_sel = st.radio("阶段", ["初版", "确认版"], horizontal=True)
    ss["stage"] = stage_sel

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
        st.success("✅ 已录入伤病（实时保存到本地 JSON，刷新不丢失），确认版复算将启用（R2 injury_shift 按球员重要性加权，未标定）")

# 数据源能力说明
auto_has = bool(ss["auto_candidates"]) and any(st.session_state.get(f"cand_{i}", False)
                                               for i in range(len(ss["auto_candidates"])))
realtime_injury = (pipe.provider.realtime_injury_available
                   or bool(ss["manual_injuries"].strip()) or auto_has)
if not pipe.provider.realtime_injury_available:
    st.caption("数据源说明：football-data.org 不提供伤病接口；TheSportsDB(403)/Transfermarkt(Cloudflare)/"
               "Google新闻/新浪均被网络阻断；已接入 **ESPN 直连自动抓取**（上方按钮）+ 手动录入兜底。")

# ---------- 验收条件 #3：确认版拦截（无实时伤病亦无手动录入） ----------
if ss["stage"] == "确认版" and not realtime_injury:
    st.warning("⛔ **暂无赛前 48h 实时伤病数据，无法生成确认版。** "
               "请在上方手动录入伤病名单（方案 C），否则系统拦截并保留为初版。")
    ss["stage"] = "初版"

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
                                        luck_map=_luck_map())
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
        # P3·移动端：卡片式布局替代宽表格（st.container border + st.progress，无需横向滑动）
        st.markdown("### 📋 本轮推荐速览（胜平负 + 推荐比分）")
        for idx, c in enumerate(cards, 1):
            p = c["probabilities"]
            top = c["score_top2"][0]["score"] if c.get("score_top2") else "—"
            pick = c["pick"]
            with st.container(border=True):
                hl, hr = st.columns([3, 1])
                hl.markdown(f"**{idx}. {c['home_team']} vs {c['away_team']}**")
                hr.markdown(f"阶段 `{c['stage']}`", help="初版=基准；确认版=含伤病复算")
                st.markdown(f"推荐 **{pick}** · 预测比分 `{top}`")
                st.progress(p["home_win"], text=f"主胜 {p['home_win']:.0%}")
                st.progress(p["draw"], text=f"平局 {p['draw']:.0%}")
                st.progress(p["away_win"], text=f"客胜 {p['away_win']:.0%}")
        st.caption("胜/平/负为概率；预测比分取 Top1（分布最集中区域）。点开下方卡片可看伤病/解释等细节。")

    # 确认版被拦截的整体提示
    blocked = [c for c in cards if c.get("confirm_blocked")]
    if blocked:
        st.warning(f"⚠️ 有 {len(blocked)} 场因**暂无赛前 48h 实时伤病数据**被降级为初版，未生成确认版。")

    # 每场卡片（P4·优先级三①：折叠面板，标题带推荐方向与概率；展开才看明细）
    for c in cards:
        title = (f"{c['home_team']} vs {c['away_team']}"
                 if c.get("home_team") != "—" else f"第{c['match_id'].split('_R')[-1]}轮 · 无数据基准")
        if c["pick"] == "基准":
            title_pick = "基准"
        else:
            pp = c["probabilities"]
            title_pick = f"{c['pick']} ({pp['home_win'] if c['pick']==c['home_team']+'胜' else pp['away_win']:.0%})"
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
                markers.append(f"疲劳xG折扣 主{fatigue.get('home_fatigue_discount')}/客{fatigue.get('away_fatigue_discount')}"
                               f"（休息 主{fatigue.get('home_rest_days','—')}/客{fatigue.get('away_rest_days','—')}天）")
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
            st.markdown(f"**胜/平/负**　主胜 `{p['home_win']}` · 平 `{p['draw']}` · 客胜 `{p['away_win']}`")
            if ss["competition"] == "CL":
                st.caption("欧冠跨赛事预测：德甲队用模型 Elo，其他联赛对手用精选豪门/联赛平均 Elo 兜底（未实时校准）。")
            # 确认版：展示初版→确认版概率对比（injury_shift）
            if c.get("confirm_probs"):
                bp = c["base_probs"]
                ish = c.get("injury_shift", {})
                st.markdown("**初版→确认版**　" + "　".join(
                    f"`{k}{'主' if k=='home_win' else ('平' if k=='draw' else '客')}胜`"
                    f"{bp[k]}→{c['confirm_probs'][k]}"
                    for k in ("home_win", "draw", "away_win")))
                st.markdown(f"**伤病偏移（按重要性加权）**　主`{ish['home']}` / 客`{ish['away']}`（R2 injury_shift，未标定）")
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
