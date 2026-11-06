# P5 · 赛前首发反推（「赛前 1 小时终极刷新」）

> 目标：ESPN 没有结构化伤病数据，改用**赛前官方 11 人首发**反推「常规主力到底谁没上」，
> 折算成 xG 折扣接入模型，作为终极确认版的预测依据；抓不到首发就自动回退，不编造。

---

## 一、可行性评估结论（先给答案）

| 问题 | 结论 |
|---|---|
| 数据能不能拿到？ | **能，但只在临场才出**。ESPN `summary?event=` 的 `rosters` 在赛前为空（`athletes=0`），开球前约 60 分钟才填 11 首发 + 9 替补 |
| 结构够不够用？ | **够**。每名球员带 `starter`(bool)、`position`、`subbedIn/subbedOut`、`athlete.id/displayName`，**"谁在替补席"可精确判定** |
| 历史能不能回溯？ | **能**。逐场 `summary` 可回溯到 2025-12，能统计"近 N 场首发频率" |
| 工程复杂度 | 中等。新增 3 个模块 + 4 处接入，无新依赖、无新 token、无付费源 |
| 主要风险 | ① ESPN **不提供逐人出场分钟**，"替补登场价值"只能用「在替补席=半额」近似；② 赛前 1 小时窗口是现实约束，不是 bug |

**所以直接做了。**

### 实测证据（本轮真机验证，全部可复现）

| 探针 | 结果 |
|---|---|
| `summary?event=401884739`（今日汉堡 vs 门兴，赛前 5.9 小时） | `rosters` 存在，每队 `athletes=0` → **赛前确实无首发** |
| `summary?event=401884753`（2026-10-31 斯图加特@勒沃库森，已完赛） | 每队 `total=20 / starters=11 / subs=9 / formation="4-2-3-1"`；字段 `['active','athlete','formationPlace','jersey','media','position','starter','stats','subbedIn','subbedOut']` |
| `scoreboard?dates=202610`（整月） | 200 / 34 场可用 |
| `scoreboard?dates=20261001-20261031`（区间） | **400**（不支持区间，只能单日或整月） |
| `/teams` → `/teams/{id}/schedule` | 200；18 队各 8 条近期 event（**构建基线最省请求的路径**） |
| `find_event_id("HSV","BMG","2026-11-06T19:30:00Z")` | 正确返回 `401884739` |

> 已踩过的死路（勿重复尝试）：`/ger.1/injuries` 恒 0 条、`/teams/{id}/injuries` 空 `{}`、
> 官方伤病页 404；kicker/sofascore/fotmob/Transfermarkt/新浪 全被拦。

---

## 二、落地的四块逻辑

### 1. 常规主力库 `data/baseline.py`

- 路径：`/teams` 拿 18 队 id → `/teams/{id}/schedule` 拿近期 event → 批量 `fetch_match_lineup` 抓首发
- 统计：`rate = 首发次数 / 参与统计场次`，逐场**按该队实际的主客位置**取名单（防对手球员混入）
- 选人：门将位单独取首发最多的 1 人 + 其余按 `rate` 取 10 人 = 11 人 XI
- 常规主力集合 `core = rate >= 0.6`
- 产物：`data/baseline_xi.json`（**已入库**，公网才能直接用）
- 重建：`python -m data.baseline 8`（约 1–2 分钟，18 队各 8 场）

本轮真机构建结果：**18/18 队可用**，每队 8 场、11 人 XI、core 8–11 人。
抽查：汉堡门将 `Daniel Heuer Fernandes` rate=1.0、拜仁 `Harry Kane` 0.875 / `Dayot Upamecano` 1.0。

### 2. 首发偏差 `lineup_shift.py`

| 情况 | 处理 |
|---|---|
| 常规主力在首发 | 满分，不扣 |
| 常规主力在替补席 | 扣 `unit × rate × 0.5`（下半场能上，含超级替补价值） |
| 常规主力不在名单 | 扣 `unit × rate × 1.0`（全额） |
| 单队累计 | 截断到 `max_total_shift = 0.30` |

`unit = 0.10`，**刻意与 `RULES.injury_shift.key_out_shift=0.10` 同单位**，这样"首发反推"和"手动录入伤病"
可以同口径比较。`rate` 缩放意味着「8 场里 7 场首发的绝对主力」比「8 场里 4 场的轮换」扣得多。

### 3. 接入模型 `pipeline.py`

- `predict_match(..., lineup=dict|None)`：仅 `competition=="BL1"` 且 `lineup.available` 时计算
- 折扣合并：`inj_shift[k] = max(手动伤病, 首发反推)` —— **首发是硬证据、手动是情报，取大值避免同一缺失被扣两次**
- 新增 `shift_source`：`首发反推 + 手动伤病（取较大值）` / `赛前首发反推` / `手动伤病录入` / `无`
- `stage` 新增第三档 `终极版`；48h 窗口校验**只对确认版生效**（终极版是临场首发，不受该窗口约束）
- `predict_round(..., with_lineup=True)`：终极版才去 `find_event_id` → `fetch_match_lineup`，其他阶段零网络开销
- 卡片新增 `lineup` / `lineup_shift` / `manual_shift` / `shift_source` 四个键

### 4. 前端 `app.py`

- 阶段 radio 变三档：`初版` / `确认版` / `⚡ 终极版（首发反推）`
- 新增 expander「⚡ 赛前 1 小时终极刷新」，写明原理 + 当前基线库状态（几队可用、窗口场次、更新时间）
- 卡片顶部醒目条（有扣减=红条，无扣减=绿条）：

  > ⚠️ **首发对比**：汉堡：缺失常规主力 2 人 —— Warmed Omari（替补席）、Fabio Vieira（替补席），
  > 已自动扣除 0.06 折扣（约 -0.10 xG） ｜ 门兴格拉德巴赫：…… → 已自动扣除 xG：汉堡 -0.10 / 门兴 -0.09

- 概率对比行改为 `初版→终极版　主胜 49.4%→48.6%　平局 25.0%→25.8%　客胜 25.5%→25.6%`
- 折扣行标注来源：`折扣偏移（来源：赛前首发反推） 主0.06 / 客0.05`

---

## 三、验证

### 单元测试 `tests/test_p5_lineup.py`（8 条，全绿）

零扣减 / 替补席半额 / 不在名单全额并按 rate 加权 / 上限截断 / 无基线或无首发→`available=False`（**绝不默认满分**）/
文案含"替补席" / 门将优先凑 11 人 / `_aggregate` 主客侧混算不串队。

`pytest tests -q` → **77 passed**（上一轮 69 + 本轮 8）。

### 端到端（`_diag11.txt`，真实基线 + 真实第 9 轮赛程）

```
[初版]    汉堡 0.494 / 0.250 / 0.255
[终极版]  汉堡 0.470 / 0.261 / 0.269   shift_source=赛前首发反推
          injury_shift={home:0.0813, away:0.0}   lineup_shift 同值   manual_shift={0,0}
          detail: 汉堡缺失 2 人（Fabio Vieira 不在名单、Warmed Omari 替补席）→ -0.14 xG
[终极版-无首发] → 回退 stage=初版 ✅
```

主队缺 2 人 → 主胜下降、客胜上升，方向正确。

### UI 两端验证（AppTest，`_at5.txt` / `_at6.txt`）

| 场景 | 结果 |
|---|---|
| 真实终极版（今天首发未出） | 9 场全部回退初版，并明确提示「本轮没有任何一场拿到官方首发……请在开球前 1 小时内再点一次」 |
| 注入构造首发（模拟首发已出） | 9/9 场阶段=`终极版`，每场都有「首发对比」红条 + xG 扣减 + 初版→终极版概率对比 |

### 真实接口验证（`_diag12.txt`）

`find_event_id` 命中 `401884739`；赛前 `available=False`、距开球 356 分钟、窗口 `in_window=False`；
已完赛场次 `401884753` 解析出 `starters=11 / bench=9`。

---

## 四、现实约束与兜底（必须知道的三件事）

1. **只在赛前约 60 分钟内有效**。今天实测距开球 5.9 小时时 ESPN 名单为空。点早了只会看到"已回退"，这是设计约束。
   为避免误判成"功能坏了"，终极版抓不到首发时会**同时算出最近一场距开球还有多久**并直接写进提示：
   「官方首发还没出，现在点还太早。最近一场是 汉堡 vs 门兴格拉德巴赫，距开球还有约 5.7 小时（341 分钟）；
   ESPN 一般要等到开球前 60 分钟内才填名单。」
2. **不在首发窗口内时，卡片上仍会显示「联赛/欧战疲劳折扣」这一行**（来自 R2/R3 的疲劳引擎）。它与首发无关，属于常规提示。
3. **抓不到就回退，不编造**。无首发 → `available=False` → 折扣 0 → 回到「手动录入伤病 + 基准概率」，卡片上明写原因。
4. **超级替补价值是近似**。数据能精确告诉我们"谁在替补席"，但 ESPN 不给逐人出场分钟，
   无法计算"替补登场 30 分钟贡献多少"。当前用 `bench_factor=0.5` 折半表达"他还有下半场"，
   方向正确但不是精确模型 —— 已在 `LINEUP_SHIFT.calibration_note` 里如实写明，
   且 `calibrated=False`（UI 显示黄牌「未标定，仅供参考」）。

## 四点五、转会窗容错（P5.1）

### 盲区是什么

基线是**静态快照**（近 8 场首发率）。夏窗/冬窗卖掉的核心（例如凯恩式转会）会永久留在 `xi` 里，
每场都被判「不在大名单 → 全额扣分」；新援则完全不在库里，只落进 `unknown_starters`，UI 上完全静默。

### 数据可行性（本轮实测）

| 结论 | 证据 |
|---|---|
| `/ger.1/teams/{id}/roster` → **200**，扁平 `athletes` 数组 | 每队 27–35 人，带 `id/fullName/position/jersey/age/status` |
| athlete id 与 `summary` 的 `athlete.id` **同一套** | BAY/HSV/DOR 三队 baseline XI 各 11/11 全匹配（0 人不在 roster） |
| `transactions` / `seasons` / `leagues` 取不到转会明细 | 只是 `http://sports.core.api.espn.pvt/...` 的 `$ref`，逐人追要 30+ 次/队 |
| **ESPN 无身价 / 转会费 / 出场分钟** | `injuries` 恒为空数组，`statistics` 为空 → 用户要求的"身价较高"判据**无法满足** |
| 该端点偶发 ReadTimeout | 单次响应约 200 KB，已加 3 次重试 + `timeout=30` + 24h 磁盘缓存 |

### 三条机制怎么落地

**① 大名单校验（`data/baseline.py` + `lineup_shift.py`）**
- 构建时：`fetch_team_roster()` 抓 18 队当前大名单 → `prune_by_roster()` 把已不在队的人从 `xi`/`core` 剔除，
  每队存 `roster{ids,n,fetched_at,checked}` 与 `departed[]`。
- 运行时：`starter_deviation(..., roster=...)` 再校验一次（缓存 24h），已离队者进 `departed[]`、**不扣分**。
- **安全约定**：`roster` 为空（抓失败/未登录）时**原样保留全部基线球员** —— 空名单 ≠ 全队走光。
  此时返回 `roster_check="roster_unavailable"`，UI 明写"⚠️ 大名单未取到，未做离队校验"。

**② 冬/夏窗洗牌检测**
- 每队存 `roster.ids` 快照；`roster_drift()` 以**基线 XI 为分母**算离队比例。
- `departed_ratio >= drift_warn_ratio(0.25)` → `drift=True`，`check_baseline_health()` 汇总成 `need_rebuild`。
- UI 两处提示：卡片出现 `♻️ 该队阵容变动较大…建议 python -m data.baseline 8`；
  基线 expander 里快照超过 `roster_ttl_grace_hours(72)` → `⏳ 已 N 天未重建`。
- **目前是"检测 + 提示"，不是自动重建**：一次重建要 18 次 roster + 72 场 summary，
  在 Streamlit Cloud 上自动触发会产生大量外部请求与超时风险，故保留人工确认。

**③ 新援 / 奇兵识别**
- 每队额外存 `seen[]`（窗口内**所有**首发过的球员，14–22 人/队，含 rate/starts/pos）。
- 本场首发但**不在 `seen`** → `newcomers[]`，UI 出 `🆕 新面孔首发：X（F）—— 可能是新援或被提上来的替补`。
- 只提示、**不加减分**：因为无法量化其水平（ESPN 无身价、无出场分钟）。
- **判据代理如实声明**：用户要求里"身价较高/联赛出场次数多"这两个判据 ESPN 都没有，
  实现只能用「基线窗口内从未首发」代理。旧版基线（无 `seen` 字段）会退化为标"疑似新面孔"（`estimated=True`），
  不假装确定。

### 新增配置（`config.LINEUP_SHIFT`）

```python
"roster_check": True,          # 扣分前先用当前大名单校验
"roster_cache_hours": 24,      # 大名单磁盘缓存 TTL
"roster_ttl_grace_hours": 72,  # 基线超过该时长未重建 → UI 提示
"drift_warn_ratio": 0.25,      # 基线 XI 离队比例阈值 → 判定洗牌
"bench_depth_warn": 3,         # 剔除后 XI 不足该人数 → 基线不可用
"newcomer_hint": True,         # 是否提示新面孔首发
```

### 端到端实测（`_diag15.txt`）

- 18/18 队 roster 全部取到（27–35 人），重建后 `departed=0`（本赛季无跨队流失，符合预期），
  `seen` 每队 14–22 人。
- 真实已完赛场 `401884753`（STU@LEV）：两队 `roster_check=ok`、`departed=[]`，扣分逻辑不变
  （LEV 缺 1 人 0.0625 / STU 缺 4 人 0.2375）；`Lucas Vázquez`、`Bilal El Khannouss` 等
  虽非基线 XI 但**在 `seen` 内**，故 `newcomers=[]` —— 老队员轮换不误报为新援。
- 模拟"移除 Dayot Upamecano 的 id"：`shift=0.0`、`departed=['Dayot Upamecano']`、
  文案「常规主力全部首发…已剔除 1 名离队球员」，扣分被正确豁免。

### 单测（`tests/test_p5_transfer_window.py`，13 条全绿）

已离队不扣分 / 空名单不清空基线 / `roster_check` 可关闭 / 离队与真缺阵可分离 /
洗牌阈值触发与不触发 / 双侧缺一不算洗牌 / 新援识别 / 轮换老队员不误报 /
`_id_key` id 优先姓名兜底 / 旧基线标"疑似" / 文案含"离队"。

---

## 四点六、基线自动重建 + 核心口径收紧（P5.2）

### 1. 不再手敲命令行

基线是静态快照，3 轮之后就和当前阵容脱节。现在两层保障：

**① 自动检测与后台重建（`data/baseline.py`）**
- 基线 JSON 新增 `built_round`（记录构建时的轮次）与 `updated_at_iso`。
- `baseline_staleness()` 两条判据（满足任一即"陈旧"）：
  - `built_round` 落后当前轮次 **≥ 3 轮**（`rebuild_round_gap`）；
  - `updated_at` 距今 **≥ 14 天**（`rebuild_max_age_days`）。
- 旧基线没有 `built_round` 时**只按日期判** —— 宁可少重建一次，也绝不因为"不知道轮次"每次启动都重建。
- `maybe_auto_rebuild()` 判定陈旧后在**后台守护线程**里静默重建，不阻塞页面；
  用 `data/.baseline_rebuild.lock`（O_EXCL 原子创建，15 分钟后视为僵尸锁自动回收）防并发，
  状态写 `data/.baseline_rebuild.json`，UI 据此显示「正在重建 / 已完成 / 失败」。

**② 显眼的「🔄 更新主力基线」按钮**
- 位置在预测页主区（P5 expander **上方**，不折叠），与「生成预测」同级可见。
- 点击后同步重建，带 spinner 与进度文案；完成后自动 `st.rerun()`，新基线立即生效。
- 同一把锁 → 后台正在跑时按钮自动 disabled，不会双跑。

**重建绝不能毁掉旧基线**（这是自动化的最大风险，已专门加固）：
- 结果可用队数 < `rebuild_min_teams(9)` → `save_baseline()` **拒绝写入**，旧 JSON 原样保留；
- 所有写入都是「临时文件 + `os.replace`」原子替换，进程中途被杀不会留下半个 JSON；
- 后台线程任何异常都只写 `status=failed`，不动 JSON。

**平台限制（必须知道）**：
- 公网 Streamlit Cloud 的容器文件系统是临时的 —— 重建结果**只在当前容器存活期内生效**，
  容器重启 / 重新部署会回退到仓库里的版本。真要永久更新，需本地重建后把
  `data/baseline_xi.json` push 到仓库（UI 上也写了这句）。
- 云端免费实例在无活动时会休眠，后台线程可能被打断 → 这就是保留手动按钮的原因。
  本地 launcher 跑 Streamlit 时不存在这个问题。

### 2. 核心口径收紧：0.7 × 6 场

| 项 | 之前 | 现在 |
|---|---|---|
| 统计窗口 `window_matches` | 8 场 | **6 场** |
| 核心阈值 `core_rate` | 0.6（4.8 场 ≈ 5/8） | **0.7**（4.2 场 ⇒ **必须 5/6 或 6/6**） |
| 实际效果 | 4/8 首发也算核心，正常轮换被扣分 | **首发 4 场（0.667）不算核心**，只有 5–6 场才算 |

`0.7 × 6 = 4.2`，因为首发次数是整数，等价于"**6 场里至少首发 5 场**"（5/6 = 0.833 ✅，4/6 = 0.667 ❌）。
重建后各队 `core` 从 8–11 人降到 **3–11 人**（汉堡 3、拜仁 4、勒沃库森 11——阵容越稳越多），
轮换球员不再进入扣分集合。判据抽成 `select_core()` 单独可测。

### 3. 新增配置（`config.LINEUP_SHIFT`）

```python
"window_matches": 6,
"core_rate": 0.7,
"auto_rebuild": True,        # 应用启动/预测前检测陈旧 → 后台静默重建
"rebuild_round_gap": 3,      # 落后 >= 3 轮 → 重建
"rebuild_max_age_days": 14,  # 距今 >= 14 天 → 重建
"rebuild_lock_minutes": 15,  # 互斥锁 TTL（超过视为僵尸锁）
"rebuild_min_teams": 9,      # 可用队数低于此值 ⇒ 拒绝覆盖旧基线
```

### 4. 验证（`_at10.txt` / `_auto1.txt` / `tests/test_p5_auto_rebuild.py`）

- 单测 21 条：轮次差 3 触发 / 差 2 不触发 / 15 天触发 / 缺 `built_round` 不误判 /
  时间戳损坏不崩 / 低质量结果不覆盖 / 原子写无残留 tmp / 僵尸锁回收 / 并发 busy /
  线程异常保留旧基线 / `select_core` 5/6 入选 4/6 淘汰 / 0.6 对照说明收紧生效。
- 全量 `pytest tests -q` → **111 passed**。
- 端到端：注入 `built_round=3`、`updated_at=2026-10-20` 的旧快照 → `maybe_auto_rebuild()` 起线程 →
  轮询到 `status=done`，锁已释放，JSON 变为 `built_round=9 / window=6 / core_rate=0.7`，
  再次调用返回 `fresh`（不会重复重建）。
- UI：`AppTest` 预测页 `exception=[]`，按钮列表含「🔄 更新主力基线」，caption 显示
  「近 6 场 · 核心阈值 0.7（≈ 6 场里首发 ≥5 场）· 记录轮次 9」。
- `staleness` 实测：`round=9 → fresh`；`round=12 → 落后 3 轮（阈值 3）`。

---

## 五、口径与未做项（诚实清单）

- `LINEUP_SHIFT.calibrated = False`。0.10/人、替补席折半都是启发式，**未用历史「首发-赛果」回归标定**。
  要标定需要逐场首发 + 赛果的大样本回归，是独立一轮工作。
- 基线窗口取近 6 场（`/teams/{id}/schedule` 一次只回 8 条，够用；再往前要按月分片抓取）。
- 只对 `BL1` 生效（欧冠没有逐场例行首发统计路径，且对手多为非德甲队，基线库不覆盖）。
- **新援判据无法按身价/出场次数做**：ESPN 不提供身价、转会费、逐人出场分钟，
  只能用「基线 8 场窗口内从未首发」这一代理；相应地新援**只提示、不加分**。
- **洗牌检测不会自动重建基线**？已升级：现在会**自动后台重建**（P5.2），但仍保留手动按钮；
  云端容器重启会回退到仓库版本，所以"永久更新"仍需本地重建后 push。
- 转会记录本身（谁转去哪、转会费）ESPN 拿不到（`transactions` 是 `.pvt` 内部 `$ref`），
  所以「已离队」只能表达为"不在当前大名单"，不能说明是转会、解约还是退役。
- 上一轮遗留仍未解决：GBM 外推未根治（未改 `trainer.py` h2h 默认 1.0、未重训模型）。

## 六、怎么用

1. 平时照旧：`初版` 看基准，`确认版` 录伤病后复算。
2. 比赛日**开球前 1 小时**：把阶段切到 **⚡ 终极版（首发反推）** → 点「生成预测」。
3. 看卡片顶部「⚠️ 首发对比」条：谁缺、在替补席还是不在名单、扣了多少 xG。
4. 若提示"本轮没有任何一场拿到官方首发" → 说明官方名单还没出，过 10 分钟再点一次。
5. 基线库需要定期刷新（建议每 2–3 轮）：`python -m data.baseline 8`，然后提交 `data/baseline_xi.json`。
