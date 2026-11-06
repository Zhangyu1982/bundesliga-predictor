# 本轮修复说明（P7 · 2026-11-06）

commit `bc0dcd7`（父 `b995bfd`），已 push main，Streamlit Cloud 自动重部署。

## 五个问题的根因与修法

### ① 推荐标签与概率冲突（汉堡 vs 门兴：平局最高却推主胜）
- 根因：`pipeline.py` 里 `pick_dir = "home_win" if home > away else "away_win"` —— 只比主客，平局从不参与比较。汉堡场 `{主 0.284 / 平 0.549 / 客 0.168}` 因此被判成主胜。
- 修法：改为三向 `max(("home_win","draw","away_win"), key=...)`；卡片新增 `pick` / `pick_dir` / `pick_prob`；UI 按 `pick_prob` 取对应概率，支持"平局"分支。

### ② 阶段标签未联动（选了确认版，卡片仍显示初版）
- 根因 A：`pipeline.py` 末尾 48h 窗口校验在 `confirm_probs is None` 时无条件把 `stage` 打回"初版"。
- 根因 B（更致命）：`schema.py` 的 `validate_stage_window()` 用 naive 时间相减。`kickoff` 来自 football-data.org 是 **UTC（…Z）**，`last_updated` 是 **+08:00**，8 小时时差被算进窗口，几乎任何场次都判"窗口外"，从而触发根因 A。
- 修法：`_dt()` 统一补时区（Z→+00:00，无时区按 +08:00）再相减；窗口外但有复算结果时保留确认版并标注"（赛前48h窗口外，本为复算预览）"；本场无伤病录入时也保留确认版并标注"（本场未录入伤病，概率与初版一致）"——只有"有伤病却没生成复算"这一种情况才回退初版。

### ③ 手动录入伤病不生效
三重机制叠加：
- (a) 解析器丢行：中文输入法全角 `：`、`，`、`？` 不被 `":" not in line` 识别，整行静默丢弃；`乙?[轮换]` 这种"问号在重要性标注前"的写法会残留 `?`。
- (b) 对称抵消：单队缺 1 核心固定 0.10 偏移，主客各缺 1 人时完全对称，融合概率几乎不动（0.284→0.276）。
- (c) **比分层不吃伤病**：`dc_score_matrix(h, a, 8, home_disc, away_disc)` 没传 `inj_shift`，所以 Top2 比分初版/确认版永远是 `1:1 / 1:0` —— 这是"改了像没改"的直接原因。
- 修法：抽出 `injury_text.py`（全角→半角、行首 markdown 符号剥离、先拆 `[重要性]` 再判 `?`、坏行显式上报）；UI 回显"解析结果：HSV 缺阵1人/存疑0人…"，坏行红字提示"这些行的伤病不会生效"；`manual_ok` 从"非空即为真"改为"能解析才为真"；`dc_score_matrix` 补上 `home_disc + inj_shift_h, away_disc + inj_shift_a`；风险说明输出初版→确认版三项概率对比，并对近似对称的伤病明确写"属对称抵消，不是没生效"。

### ④ ESPN 伤病接口永远无数据
- 实测结论：ESPN 足球侧**没有结构化伤病接口**——`/soccer/ger.1/injuries` 200 但 `injuries=0`；`/teams/598/injuries` 返回 `{}`；`site.web.api…common/v3/…/injuries` 404；`/athletes/{id}/injuries` 404；官方伤病页 `espn.com/soccer/injuries` 404。替代源（kicker 403 / sofascore 403 / fotmob 404 / bundesliga.com 404 / TheSportsDB 403 / Transfermarkt Cloudflare）全部不可用。
- 修法：不换源、不编造。`fetch_all_candidates()` 返回诚实说明 + `structured_available=False`；`_espn_reachable()` 探测端点由 `/injuries`（恒 200 + 空数组，会把"接口没数据"误报成"抓取成功 0 条"）改为 `/news`；UI 明确写"以手动录入为准"。
- 遗留：自动抓取目前只能在新闻里提线索，不能替代手动录入。要真正自动化，需要另找可用的付费/授权伤病源。

### ⑤ 汉堡场平局 55%（罕见）——真凶是 GBM 外推，不是伤病、不是 R2 复算
融合权重 `elo 0.15 / poisson(xgb) 0.30 / lightgbm 0.40`（以 config 为准），汉堡场三分量实测：
- ELO `{主 0.450 / 平 0.183 / 客 0.366}`
- DC `{0.511 / 0.276 / 0.214}`
- **GBM `{0.026 / 0.913 / 0.061}`** ← 病根
- 融合 `{0.284 / 0.549 / 0.168}`

即：GBM（xgb+lgbm 合计权重 0.45）单类输出 91.3%，把融合平局硬拽到 54.9%。该异常在**初版就存在**，与 R2 伤病复算无关，更不是"两队伤病抵消"。
- 修法（本轮）：在融合层加 GBM 可信度护栏——`max(gbm) >= 0.80` 或 `min(gbm) <= 0.05` 时 `gbm_trust = 0`（GBM 完全不计入，权重重分配给 Elo/DC）；双方 `form` 与 `roll_hg` 全为默认 1.0（升班马/无历史）时降到 0.3 权重。`model_breakdown` 暴露 `gbm_trust` 便于追溯。
- 修复后汉堡场：初版 `{0.494 / 0.250 / 0.255}`、`gbm_trust=0`；确认版（主客各缺 1 核心）`{0.481 / 0.265 / 0.254}`，Top2 比分由 `["1:1","1:0"]` 变为 `1:1 0.137 / 1:0 0.133`（比分层已吃伤病）。平局回到 25%，落入德甲正常 20–30% 区间。
- 未做（需另开一轮）：`trainer.py` 里 `h2h_hg/h2h_ag` 等特征仍以 1.0 兜底，且未重训模型。本轮是在融合层兜住，不是根治 GBM 外推。

## 验证
- `pytest tests -q`：69 passed（原 61 + 新增 8 条回归 `tests/test_p7_bugfix.py`）。
- Streamlit `AppTest` 无界面端到端：全角录入 `汉堡：格拉策尔[核心] / 门兴：克莱因迪恩斯特?[轮换]` → 切确认版 → 生成预测。
  - `exception=[]`
  - 9 张卡片阶段全部为 `确认版`
  - 推荐全部为概率最大项
  - 解析回显正确：`HSV 缺阵1人/存疑0人；BMG 缺阵0人/存疑1人`
  - 汉堡场风险行：`主 49.4%→46.9%、平 25.0%→26.4%、客 25.5%→26.7%`
