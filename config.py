"""
德甲预测机器人 · 统一配置源（单一配置，规则与阈值全部在此维护）
技术需求文档 v1.1：R1–R5 规则名锁定，所有阈值外置到 config.RULES，calibrated 标记控制是否可用于生产。
禁止在模型代码中硬编码阈值。

边界声明：
本系统输出为概率推演，不构成投注建议。比分 Top2 是分布最集中区域，不代表确定结果。
伤病与国家队负荷在赛前 48 小时仍可能显著变动，初版与确认版之间的差异是系统的设计特性，不是缺陷。
"""

# ============ 基础 ============
SEASON = 2026
LEAGUE = "BL1"            # football-data.org 德甲代号
ROUND_MIN, ROUND_MAX = 1, 34
TIMEZONE = "+08:00"

# ============ 模型参数 ============
HOME_ADV = 50             # Elo 主场优势（网格搜索定值）
ELO_K = 15                # Elo 步长
DC_DECAY = 0.03           # Dixon-Coles 时间衰减系数
DC_RHO = -0.20            # Dixon-Coles 参数（移植自 tip-ai）
# 集成权重（liveljack 最优配比）
ENSEMBLE_WEIGHTS = {
    "elo": 0.15,
    "poisson": 0.40,
    "xgboost": 0.30,
    "lightgbm": 0.15,
}
CATBOOST_MIN_SAMPLES = 2000   # 德甲数据 <=2000 场时禁用 CatBoost（防过拟合）

# ============ 分期/阶段 ============
STAGES = ["初版", "确认版"]

# ============ config.RULES —— 唯一阈值入口（v1.1 精确结构）
# 规则名 R1–R5 锁定不变；所有阈值外置于此。calibrated=false 项仅作占位示意，须回测后定稿。
# 改阈值只动这一处，不触碰模型代码和输出 schema。
RULES = {
    "R1_bias_record": {
        "name": "偏差记录",
        "consecutive_rounds": 3,          # 连续同方向偏差轮数，达此数触发该队 Elo 重校准
        "calibrated": True,
        "calibration_note": "历史回溯实测(3赛季918场)：基线方向错误率47.6%；连续3轮同方向偏差后下轮仍错率64.4%(n=45,提升16.8pp)；T=4达66.7%但样本27，取T=3为鲁棒触发拐点",
    },
    "R2_injury_upgrade": {
        "name": "伤病升级",
        "confirm_window_hours": 48,        # 赛前复算窗口
        "downgrade_confidence_below": "中",  # 中/低置信度场次强制复算
        "calibrated": False,
        "calibration_note": "需对比赛前48h/24h/6h伤病名单变动率，选择复算收益最大窗口",
    },
    "R3_national_window": {
        "name": "国家队系数窗口",
        "fifa_window_first_round_only": True,
        "minutes_threshold": 180,          # 国脚出场分钟阈值
        "travel_km_threshold": 5000,       # 跨洲旅行距离阈值
        "xg_discount_min": 0.08,           # 预期进球下调下限
        "xg_discount_max": 0.15,           # 预期进球下调上限
        "calibrated": False,
        "calibration_note": "需FIFA窗口后首轮 vs 非窗口轮的进球差分布标定",
    },
    "R4_divergence_flag": {
        "name": "分歧场次标注",
        "submodel_disagreement_threshold": 0.10,  # Elo与DC主胜概率差(Δp)≥此值且方向不一致即降档（实测标定）
        "confidence_downgrade_steps": 1,
        "calibrated": True,
        "calibration_note": "历史回溯实测：基线爆冷率22.2%；Elo/DC方向分歧场爆冷率30.0%(一致仅17.9%)；|Δp|≥0.10后爆冷率从~19%抬升至24%+，取0.10为分歧强度阈值",
    },
    "R5_continuous_tracking": {
        "name": "连续跟踪",
        "start_round": 9,                  # 从第 9 轮起开始记录
        "append_prev_bias_summary": True,  # 每次输出末尾附上一轮偏差摘要
        "calibrated": True,                # 结构规则，无需数据标定
    },
    # 伤病折扣偏移量 —— 当前为示意值，最需要标定
    "injury_shift": {
        "key_out_shift": 0.10,             # 每名关键缺阵球员的预期进球偏移
        "doubtful_shift": 0.05,            # 每名存疑球员的预期进球偏移
        "max_total_shift": 0.30,           # 单队累计偏移上限
        # 球员重要性权重（核心=1.0 全算，轮换/替补递减）。启发式权重，未做回归标定 → 诚实保留 calibrated=False
        "importance_factor": {"核心": 1.0, "轮换": 0.6, "替补": 0.3},
        "importance_default": "核心",      # 未标注重要性的球员默认按"核心"计（保守，不低估伤病影响）
        "calibrated": False,
        "calibration_note": "用带伤病名单的历史比赛做回归，估计每名球员缺阵对xG的边际影响；需区分位置（门将/中卫/前腰/前锋）；重要性权重为核心/轮换/替补启发式，待回测定稿",
    },
}

# ============ 规则日志映射（R1–R5 各落独立 JSONL） ============
LOG_DIR = "./logs"
RULE_LOGS = {
    "R1_bias_record": "r1_bias_record.jsonl",
    "R2_injury_upgrade": "r2_injury_upgrade.jsonl",
    "R3_national_window": "r3_national_window.jsonl",
    "R4_divergence_flag": "r4_divergence_flag.jsonl",
    "R5_continuous_tracking": "r5_continuous_tracking.jsonl",
}

# ============ 每轮必须更新的输入变量（v1.1 §7 表） ============
INPUT_VARIABLES = [
    {"seq": 1, "var": "三年逐轮历史结果（滚动）", "priority": "模型底座", "window": "赛季内持续维护"},
    {"seq": 2, "var": "各队最近 3 轮进球/失球/xG", "priority": "高", "window": "每轮赛后更新"},
    {"seq": 3, "var": "赛前伤病/停赛名单", "priority": "高", "window": "赛前 48–72 小时"},
    {"seq": 4, "var": "国家队比赛日负荷（国脚数量/出场时间/旅行距离）", "priority": "高（仅FIFA窗口后首轮）", "window": "赛前 72 小时"},
    {"seq": 5, "var": "最新积分榜 + 赛程密度", "priority": "中", "window": "每轮赛后更新"},
    {"seq": 6, "var": "市场赔率（校准特征，非必选）", "priority": "中", "window": "赛前 24 小时"},
]

# ============ 数据源 ============
DATA_SOURCES = {
    "football_data_org": {
        "free_limit_per_min": 10,
        "free_limit_per_day": 100,        # 免费层每日配额（用于每日限流保护）
        "base_url": "https://api.football-data.org/v4",   # P0 复核
        "deep_monthly": 29,           # €/月，实时首发升级触发
    },
    "football_data_skill": {          # 零密钥
        "provides": ["xg", "injury_news", "lineup"],
        "xg_leagues": ["BL1"],
        "realtime": False,
    },
    "api_football": {
        "base_url": "https://api-football-v1.p.rapidapi.com/v3",
        "host": "api-football-v1.p.rapidapi.com",
        "league_id": 78,              # 德甲
        "daily_limit": 50,            # 免费层每日请求上限（限流保护）
        "provides": ["injuries", "suspensions", "xg_statistics"],
    },
    "odds_addon_monthly": 15,         # €/月，赔率校准（可选）
}

# ============ 跨赛事（P2·跨赛事分析） ============
# 赛事免费层可用性（football-data.org v4 实测）：
#   BL1 德甲 200 ✅ | CL 欧冠 200 ✅ | EL 欧联 403(付费) | DFB 德国杯 403(付费) | 欧协(Conference League) 404(不存在,EC=欧锦赛)
COMPETITIONS = {
    "BL1": {"name": "德甲", "code": "BL1", "available": True, "note": "免费层可用"},
    "CL":  {"name": "欧冠", "code": "CL",  "available": True, "note": "免费层可用"},
    "EL":  {"name": "欧联", "code": "EL",  "available": False, "note": "免费层403，需付费token"},
    "DFB": {"name": "德国杯", "code": "DFB", "available": False, "note": "免费层403，需付费token"},
    "UECL": {"name": "欧协", "code": None, "available": False, "note": "football-data无此赛事(404)；EC为欧锦赛"},
}

# 疲劳特征规则（欧战/杯赛对德甲预测的干扰，GBM 特征库）
# 已按 3 季历史欧冠赛程 + 德甲 918 场 Elo 前瞻残差做队内配对标定（scripts/calibrate_fatigue.py）：
#   - 欧冠前置（本场后7天有欧冠→可能轮换）实测残差 ≈ -0.036 球/场（n=144）→ 扁平轮换折扣 ~2%
#   - 德甲轮次内「休息<3天」几乎不出现（周中欧冠→周末德甲为3-4天，实测无拖累甚至略正）
#   - 故折扣量级很小：单日 1.4%，封顶 4%（非早期保守默认的 4%/12%）
FATIGUE_RULES = {
    "min_rest_days": 3,                # 距上一场欧战/杯赛休息 < 3 天 → xG 打折（德甲轮次内极少触发）
    "xG_discount_per_day": 0.014,      # 每少一天休息，预期进球下调 1.4%（实测标定，连续封顶）
    "xG_discount_max": 0.04,           # 单队疲劳 xG 折扣上限 4%（实测标定）
    "cl_rotation_discount": 0.02,      # 欧冠前置（未来7天有欧冠→可能轮换）扁平折扣 2%（实测标定）
    "midweek_window_days": 7,          # 未来 7 天内若有欧冠 → 标记"欧冠前置，可能轮换"
    "cl_away_intensity_bonus": 0.02,   # 上一场为欧战客场 → 疲劳附加 2%（保留保守默认）
    # 加时赛极端疲劳：上一场打满 120 分钟（加时/点球）→ 极端消耗，xG 额外折扣 6%（超出常规 4% 封顶）
    "extra_time_discount": 0.06,         # 加时赛 xG 额外折扣（极端惩罚）
    "extra_time_recovery_days": 3,       # 加时赛后 N 天内视为极端疲劳窗口（逾此不额外惩罚）
    "calibrated": True,                  # 已用真实欧冠赛程+德甲历史回测标定（加时赛折扣为保守规则化设定）
    "calibration_note": "3季(2324/2425/2526)欧冠赛程+德甲918场Elo前瞻残差·队内配对；欧冠前置n=144效应-0.036球/场(≈2%)，短休息<3天德甲轮次内罕见；单日折扣1.4%、封顶4%",
}

# 运气回归（触底反弹）特征（P4·优先级二①）
# 对比近5场"实际进球均值" vs "xG(射正HST代理)均值"：若机会创造强(HST高)但转化极差(进球少)，
# → 判定"进攻端触底反弹"，解释模块打 📈 标签，并在底层微调增加该队预期进球权重。
# 阈值用"联赛相对分布"判定（HST 是射正计数代理，尺度远高于进球，须相对联赛基线，否则全员触发无意义）：
#   gap = 近5场 射正均值 − 实际进球均值；触发需 gap ≥ 联赛均值 + gap_z·σ 且 射正均值 ≥ 联赛中位数。
# ⚠️ 诚实标注：football-data.org 免费层无真 xG，用射正 HST 代理；加成量未做回测标定。
LUCK_REGRESSION = {
    "window_games": 5,                 # 近 5 场（HST/实际进球 各队滚动均值）
    "gap_z": 0.7,                      # gap 须高于"联赛均值 + 0.7σ"（相对离群，≈ 前25%转化最差队）
    "require_hst_at_median": True,     # 射正均值须 ≥ 联赛中位数（确保机会创造真的高）
    "xg_boost": 0.06,                  # 触底反弹 → 该队预期进球权重上调 0.06（冲抵 xG 折扣）
    "calibrated": False,               # 规则化设定，未做回测标定 → 诚实黄牌
    "calibration_note": "需历史赛季 HST vs 实际进球的回归：验证触底反弹是否真的带来后续进球回升（现为保守启发式）",
}

# 战意与赛程规则化（P4·优先级二②）—— 用户直接定死的硬规则：
# 球队在欧冠比赛前 3 天内打联赛，且联赛对手排名比自己低 5 名以上（明显弱旅、无需死拼）→ 直接强制扣减 0.15 预期进球。
# 硬扣减，不被常规疲劳封顶吞掉（独立作用于 xG 折扣）。
MOTIVATION_RULES = {
    "cl_league_window_days": 3,        # 欧冠比赛前 3 天内打的联赛
    "opponent_rank_gap": 5,            # 联赛对手排名比该队低 ≥5 名
    "xg_deduction": 0.15,              # 直接扣减 0.15 xG（写死）
    "calibrated": False,               # 规则化设定（用户指定），未做回归标定 → 诚实黄牌
    "calibration_note": "欧冠前3天联赛+对手低5名 → 战意不足直接扣0.15xG；规则写死，需真实赛程回测验证量级",
}

# 跨赛事 Elo：不同赛事赢球权重不同（K 倍率）
ELO_K_MULT = {"BL1": 1.0, "CL": 1.25, "EL": 1.15, "DFB": 0.80, "UECL": 1.10}

# 其他联赛球队的平均 Elo 兜底（对手非德甲时；拉不到其本国联赛 Elo 用此系数）
LEAGUE_BASE_ELO = {
    "PD": 1650,      # 西甲
    "PL": 1670,      # 英超
    "SA": 1620,      # 意甲
    "FL1": 1600,     # 法甲
    "DED": 1590,     # 荷甲
    "PPL": 1560,     # 葡超
    "SUI": 1540,     # 瑞士超
    "BL1": 1560,     # 德甲（参照系）
    "_default": 1500,
}
LEAGUE_NAME2CODE = {
    "Primera Division": "PD", "LaLiga": "PD", "Spanish": "PD",
    "Premier League": "PL", "English Premier League": "PL",
    "Serie A": "SA", "Italian": "SA",
    "Ligue 1": "FL1", "French Ligue 1": "FL1",
    "Eredivisie": "DED", "Dutch": "DED",
    "Primeira Liga": "PPL", "Portuguese": "PPL",
    "Super Lig": "SUI", "Turkish": "SUI",
}

# 精选欧战豪门基准 Elo（跨赛事对手拉不到其本国联赛实时 Elo 时，比"联赛平均"更准）。
# 诚实标注：静态基准，未按当前状态实时校准；非列表内球队仍用联赛平均兜底。
CL_TEAM_ELO = {
    # 英超
    "Manchester City": 1750, "Manchester United": 1720, "Arsenal": 1740,
    "Liverpool": 1745, "Chelsea": 1725, "Tottenham": 1700, "Aston Villa": 1685,
    "Newcastle United": 1680, "Brighton": 1665,
    # 西甲
    "Real Madrid": 1780, "Barcelona": 1765, "Atlético de Madrid": 1750,
    "Club Atlético de Madrid": 1750, "Real Betis": 1660, "Villarreal": 1670, "Sevilla": 1660,
    # 意甲
    "Inter": 1750, "FC Internazionale Milano": 1750, "Juventus": 1735,
    "Milan": 1720, "AC Milan": 1720, "Napoli": 1720, "Roma": 1690, "Lazio": 1690,
    # 法甲
    "Paris Saint-Germain": 1755, "PSG": 1755, "Lille": 1700, "Lille OSC": 1700,
    "Monaco": 1700, "AS Monaco": 1700, "Lyon": 1685, "Marseille": 1680, "Lens": 1660,
    # 荷甲/葡超/其他
    "Ajax": 1690, "PSV": 1680, "Feyenoord": 1675,
    "Benfica": 1685, "Porto": 1680, "Sporting": 1685,
    "Galatasaray": 1640, "Fenerbahçe": 1635, "Slavia Praha": 1625, "Shakhtar Donetsk": 1620,
    "Club Brugge": 1620, "Bodø/Glimt": 1615, "Viking FK": 1600, "Slovan Bratislava": 1600,
}


def uncalibrated_thresholds() -> list[str]:
    """列出 calibrated=False 的阈值项，供 UI 显示"阈值未标定，仅供参考"。"""
    out = []
    for key, cfg in RULES.items():
        if not cfg.get("calibrated", True):
            out.append(key)
    return out
