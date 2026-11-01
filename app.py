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
from data.injury_fetch import fetch_all_candidates

DISCLAIMER = ("本系统输出为概率推演，不构成投注建议。比分 Top2 是分布最集中区域，不代表确定结果。"
              "伤病与国家队负荷在赛前 48 小时仍可能显著变动，初版与确认版之间的差异是系统的设计特性，不是缺陷。")


def _merge_injuries(manual: dict | None, checked_candidates: list[dict]) -> dict | None:
    """把勾选的自动候选并入伤病字典（按 team_code）。无任何输入 → None。"""
    out = dict(manual or {})
    for c in checked_candidates or []:
        code = c.get("team_code")
        player = c.get("player")
        if not code or not player:
            continue
        entry = out.setdefault(code, {"key_out": [], "doubtful": []})
        bucket = "doubtful" if c.get("status") == "doubtful" else "key_out"
        if player not in entry[bucket]:
            entry[bucket].append(player)
    return out or None


def _parse_manual_injuries(text: str) -> dict | None:
    """解析手动录入伤病文本 → {code: {"key_out":[...],"doubtful":[...]}}。
    格式每行：`队名关键词: 缺阵球员,存疑球员`。空 → None。"""
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
                doubtful.append(p.strip("?存疑 "))
            else:
                key_out.append(p)
        out[code] = {"key_out": key_out, "doubtful": doubtful}
    return out or None


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
    # 方案三：ESPN 自动抓取伤病候选（国内直连，无 token）
    if st.button("🔎 自动抓取伤病候选（ESPN 国内直连）"):
        with st.spinner("正在抓取 ESPN 伤病接口 + 德甲新闻……"):
            res = fetch_all_candidates()
            ss["auto_candidates"] = res["candidates"]
            ss["auto_note"] = res["note"]
    if ss["auto_note"]:
        st.info(ss["auto_note"])
    if ss["auto_candidates"]:
        st.markdown("**勾选要纳入确认版复算的伤病候选：**")
        for i, c in enumerate(ss["auto_candidates"]):
            st.checkbox(
                f"{c['player']}（{c['team_name']} · {'缺阵' if c['status']=='key_out' else '存疑'}）· {c['source']}",
                value=True, key=f"cand_{i}",
                help=c.get("context", ""))
    else:
        st.caption("（尚未抓取 / 当前 ESPN 无德甲伤病记录 → 请用下方手动录入）")
    st.divider()
    st.markdown("**手动录入（兜底）**　格式：`队名: 缺阵球员,存疑球员?`　例：`拜仁: 凯恩,穆夏拉,格纳布里?`")
    ss["manual_injuries"] = st.text_area(
        "伤病名单（空 = 无实时伤病，确认版将被拦截）", ss["manual_injuries"],
        height=80, placeholder="美因茨: 布卡特\n拜仁: 凯恩,穆夏拉")
    if ss["manual_injuries"].strip():
        st.success("✅ 已录入伤病，确认版复算将启用（R2 injury_shift，未标定）")

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
if st.button("生成预测", type="primary"):
    manual = _parse_manual_injuries(ss["manual_injuries"])
    checked = []
    if ss["auto_candidates"]:
        checked = [c for i, c in enumerate(ss["auto_candidates"])
                   if st.session_state.get(f"cand_{i}", False)]
    merged = _merge_injuries(manual, checked)
    with st.spinner("正在拉取数据并推演……"):
        result = pipe.predict_round(ss["round"], stage=ss["stage"],
                                    manual_injuries=merged, competition=ss["competition"])
    st.info(f"🕓 {result['freshness_label']}")

    # 免费层受限赛事（EL/DFB/欧协）→ 诚实提示
    if result.get("comp_unavailable"):
        st.warning(f"⛔ **{COMPETITIONS.get(ss['competition'], {}).get('name','该赛事')} 免费层不可用**："
                   f"{result.get('comp_note')}。请使用德甲(BL1)或欧冠(CL)。")
    else:
        cards = result["cards"]
        st.markdown(f"本轮共 **{len(cards)}** 场预测"
                    + ("（欧冠跨赛事 Elo）" if ss["competition"] == "CL" else "（德甲）"))

    # 确认版被拦截的整体提示
    blocked = [c for c in cards if c.get("confirm_blocked")]
    if blocked:
        st.warning(f"⚠️ 有 {len(blocked)} 场因**暂无赛前 48h 实时伤病数据**被降级为初版，未生成确认版。")

    # 每场卡片
    for c in cards:
        title = (f"{c['home_team']} vs {c['away_team']}"
                 if c.get("home_team") != "—" else f"第{c['match_id'].split('_R')[-1]}轮 · 无数据基准")
        with st.expander(f"{title} · [{c['stage']}] {c['pick']} · 置信度 {c['confidence']}"):
            # ---------- 验收条件 #2：卡片内未标定阈值黄色徽章 ----------
            if uncal:
                badge = "⚠️ 伤病偏移量未标定，仅供参考"
                st.markdown(
                    f'<span style="background:#FEF3C7;color:#92400E;border:1px solid #F59E0B;'
                    f'padding:2px 10px;border-radius:14px;font-size:12px;font-weight:600;">{badge}</span>',
                    unsafe_allow_html=True)
            p = c["probabilities"]
            # 跨赛事疲劳/欧冠前置标记
            fatigue = c.get("fatigue", {})
            markers = []
            if fatigue.get("cl_preceding_home"):
                markers.append(f"⚠️ {c['home_team']} 欧冠前置，可能轮换（已标定折入轮换折扣）")
            if fatigue.get("cl_preceding_away"):
                markers.append(f"⚠️ {c['away_team']} 欧冠前置，可能轮换（已标定折入轮换折扣）")
            if fatigue.get("home_fatigue_discount", 0) or fatigue.get("away_fatigue_discount", 0):
                markers.append(f"疲劳xG折扣 主{fatigue.get('home_fatigue_discount')}/客{fatigue.get('away_fatigue_discount')}"
                               f"（休息 主{fatigue.get('home_rest_days','—')}/客{fatigue.get('away_rest_days','—')}天）")
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
                st.markdown(f"**伤病偏移**　主`{ish['home']}` / 客`{ish['away']}`（R2 injury_shift，未标定）")
            st.markdown(f"**比分 Top2**　" + "　".join(f"`{s['score']}`({s['prob']})" for s in c["score_top2"]))
            st.markdown(f"**让球**　`{c['handicap_direction']['line']}` · 谨慎度 `{c['handicap_direction']['caution']}`")
            st.markdown(f"**数据**　{c['data_availability']} · 伤病 "
                        f"主缺阵{c['injuries'].get('home_key_out',[])} / 客缺阵{c['injuries'].get('away_key_out',[])}")
            st.markdown(f"**风险**　{c['risk_note']}")
            st.caption(f"model_breakdown: elo={c['model_breakdown']['elo_home']}/"
                       f"{c['model_breakdown']['elo_draw']}/{c['model_breakdown']['elo_away']} · "
                       f"dc_score_top2={c['model_breakdown']['dc_score_top2']}")
