# 德甲预测机器人 · P1–P4 完整版（概率推演系统）

> 状态：**P0 种子 → P1 历史重训 → P2 真实数据/跨赛事/伤病 → P3 回测闭环 → P4 性能/解释/UI/模型微调 全部完成**
> 部署：Streamlit Cloud 公网（push main → 自动重部署，链接不变）
>
> 边界声明：本系统输出为概率推演，不构成投注建议。比分 Top2 是分布最集中区域，不代表确定结果。
> 伤病与国家队负荷在赛前 48 小时仍可能显著变动，初版与确认版之间的差异是系统的设计特性，不是缺陷。

---

# 📖 一、日常使用 SOP（周末实战怎么用）

## 1.1 打开系统
1. 浏览器打开公网地址（链接固定不变）：
   `https://bundesliga-predictor-wmqwjphqxotytzxohqpa85.streamlit.app`
2. 若打开是"Manage app / 加载中"，说明 Streamlit Cloud 免费版正在冷启动或自动重部署，**等 2–5 分钟刷新即可**，无需任何操作。

## 1.2 一次完整的预测流程（6 步）
| 步骤 | 操作 | 说明 |
|---|---|---|
| ① | 顶部选「赛事」 | 德甲 / 欧冠可用；欧联/德国杯免费层 403 会诚实提示，不选即可 |
| ② | 选「轮次」 | 当前最新轮；过旧轮次会走 GBM→DC 兜底输出基准概率 |
| ③ | 选「阶段」 | 初版（立即出预测）；确认版（需赛前 48h 真实伤病，会先拦提示） |
| ④ | 点「生成预测」 | 顶部 spinner 期间按钮已禁用防连点；约几秒出 9 场 |
| ⑤ | 看「本轮推荐速览表」 | 9 场胜平负 + 推荐比分 + 推荐方向 + 阶段，一目了然 |
| ⑥ | 点开折叠卡片看细节 | 每场：伤病偏移、疲劳折扣、📈 触底反弹、让球、预测解释、model_breakdown |

## 1.3 读懂卡片与标签
- **推荐胜平负**：主胜/平/客胜三概率，取最大者为推荐（如"拜仁慕尼黑胜 71%"）。
- **比分 Top2**：分布最集中的两个比分，非确定结果。
- **让球/谨慎度**：负让球=看好强队；"中高"表示 R4 分歧或伤病影响，注意风险。
- **预测解释（Explainable AI）**：卡片底部一段人话，说明模型为什么这么看——含伤病缺阵、疲劳/加时赛、欧冠前置、📈 触底反弹、战意不足扣 xG 等。
- **黄牌标记**：⚠️ "伤病偏移量未标定""未实时校准"= 该系数是规则化设定，仅供参考，不构成投注依据。
- **📈 进攻端触底反弹**：该队近 5 场机会创造强（射正高）但进球少（高 xG 低转化），模型微调上调其预期进球权重。

## 1.4 伤病录入（确认版才需要）
1. 点「🔎 自动抓取伤病候选（ESPN国内直连）」→ 抓到的球员渲染为勾选框。
2. 一键勾选伤停球员 → 点生成（确认版）→ 自动折入 injury_shift 复算。
3. ESPN 当前无伤病记录时诚实提示，**落回手动录入**：按 `队名: 球员名` 换行输入（支持简称/全名）。
> 免费层无真 xG，用「射正 HST 代理」诚实标注；不虚拟数据。

## 1.5 历史回测看板
页面底部「📊 历史回测看板」：918 场 walk-forward 静态结果（方向命中率 52.4%、爆冷率 22.2%、按轮次/按赛季命中曲线、Elo/DC 分歧效应）。**只读展示，不实时重算**，不会拖慢主页面。

## 1.6 常见问答
- **疯狂刷新会不会烧 API？** 不会。10 分钟内同一轮次/赛事命中 `@st.cache_data` 本地缓存，只消耗 1 次额度；按钮已防抖。
- **超限会怎样？** 前端显示红条"当前请求过载，请 1 分钟后再试"，不会抛代码堆栈。
- **预测准吗？** 历史回测方向命中约 52–55%，爆冷率约 22%。概率推演，仅供参考，**足球反买，别墅靠海**。

---

# 🛠 二、部署维护指南（给开发者/维护者）

## 2.1 本地运行
```bash
pip install -r requirements.txt
python3 -m pytest tests/                 # 40 项测试（P0–P4）
FOOTBALL_DATA_TOKEN=xxx streamlit run app.py --server.headless true --server.port 8504
```
首次运行自动训练并缓存 `artifacts/models.pkl`；之后快速加载。

## 2.2 部署原理（Streamlit Cloud）
- 仓库：`github.com/Zhangyu1982/bundesliga-predictor`（已公开）。
- **改代码 → commit → push main → Streamlit Cloud 自动重部署，公网链接不变**。
- 敏感配置走 **Secrets 注入**（`.streamlit/secrets.toml` 已 .gitignore，勿入库）：
  - `FOOTBALL_DATA_TOKEN`（football-data.org 免费层，10 req/min）
  - 推送时若本地 remote 为干净 URL，可用一次性 `git push https://<user>:<PAT>@github.com/... main` 带凭据，推完恢复干净 URL；PAT 勿入库。
- 免费版冷启动/重部署较慢属正常（2–8 分钟），期间显示 Manage/加载页，等即可。

## 2.3 参数微调（最常用）
所有阈值集中在 `config.py`，改一处即生效：
| 参数 | 位置 | 当前值 | calibrated |
|---|---|---|---|
| 伤病偏移 | `injury_shift` | 缺阵0.10/存疑0.05/上限0.30 | ❌ 未标定（黄牌） |
| 运气回归 | `LUCK_REGRESSION` | window5 / gap_z0.7 / boost0.06 | ❌ 未标定（黄牌） |
| 战意规则 | `MOTIVATION_RULES` | 欧冠前3天+对手低5名→扣0.15xG | ❌ 未标定（黄牌） |
| 疲劳 | `FATIGUE_RULES` | 单日1.4%/封顶4%/轮换2%/加时6% | ✅ 已标定 |
| R1/R4 | `RULES` | consecutive3 / Δp0.10 | ✅ 已标定 |
> **微调纪律**：任何改 `calibrated=False` 的参数必须同步保留黄牌标注；标定需历史回测支撑（见 2.4）。

## 2.4 历史回测 / 重生成报告 / 模型标定
```bash
python3 scripts/backtest.py              # 918 场 walk-forward → 写 bias.db + 日志
python3 scripts/backtest_report.py       # 生成静态 data/backtest_report.json（页面只读渲染）
python3 scripts/calibrate_fatigue.py     # 疲劳系数真实标定（已跑，勿随意覆盖）
python3 scripts/calibrate_injury.py      # injury_shift 敏感性标定 → data/calibration_report.json
python3 scripts/record_live_result.py    # 记录真实比赛结果→R1偏差库(bias.db)；诚实只记确知字段，缺失概率留NULL
```
回测看板**只在本地跑，不塞进主应用**，避免每次交互重算拖慢页面。
injury_shift：因无历史真实伤病名单数据源（TheSportsDB 403/Transfermarkt Cloudflare/ESPN 德甲空），
脚本做**合成伤病注入的敏感性分析**，结论是系数对整体质量影响极小 → 维持经验值，**calibrated 保持 False（诚实黄牌）**，不造假。

## 2.5 新增联赛
- football-data 免费层各赛事代码见 `config.COMPETITIONS`（BL1/CL 可用；EL/DFB 403 需付费）。
- 新增：`config.COMPETITIONS` 加条目 + `LEAGUE_NAME2CODE`/跨赛事 Elo 兜底 → 赛事切换器自动出现；受限赛事如实标注"免费层403"。
- 历史 CSV（新联赛）放入 `data/historical/D1_<赛季>.csv`，`historical_data.load_seasons` 自动并入。

## 2.6 数据源与限流
- 赛程/积分：football-data.org v4（`FOOTBALL_DATA_TOKEN`）。
- 伤病：ESPN 国内直连（`site.api.espn.com`）自动 + 手动兜底；免费层无真 xG → 射正 HST 代理。
- 免费层 **10 req/min**：`data_stores/cache.py` 从响应头读 `x-requests-available-minute` 动态拦截防 403；429 需等 60s。
- 已实测死路（勿重试）：TheSportsDB 403、Transfermarkt Cloudflare、Fotmob 404、新浪 403、GoogleNews 超时、懂球帝 567、UECL 404。

## 2.7 常见故障排查
| 现象 | 处理 |
|---|---|
| 公网显示 Manage/加载 | Streamlit Cloud 免费版冷启动/重部署，等 2–8 分钟 |
| 卡片显示种子对阵（非真实） | 缺 `FOOTBALL_DATA_TOKEN` 或触发限流回退种子，检查 Secrets/等窗口重置 |
| "当前请求过载"红条 | 免费层 1 分钟配额用完，等 60s 再点 |
| 本地 st.bar_chart/st.table 报 pyarrow 需 NumPy2.0 | 本机 NumPy 1.26，规避——页面已改 HTML 内联条形 / Markdown 表 |
| 想验证"ESPN被屏蔽时本地兜底" | 设 `INJURY_FORCE_FALLBACK=1` 启动 → 点抓取会降级读本地 JSON 并黄提示（维护/测试开关，默认关闭） |
| 伤病刷新后丢失 | 不会——手动录入实时保存到 `data/injuries_backup.json`（已 gitignore，不入库），刷新自动载入 |

**P5 优化（P0 缓存/ P1 本地兜底/ P2 敏感性标定/ P3 移动端卡片）**：
- P0：football-data 缓存 TTL=3600s、ESPN TTL=1800s；`st.session_state`+按钮防抖 disabled 已完备，刷新/切换选项不再消耗外部 API 额度。
- P1：ESPN 抓取成功 → 写本地 `injuries_backup.json`；失败 → 降级读本地缓存并黄提示"当前使用本地缓存数据"；手动录入 on_change 实时保存回 JSON。
- P2：侧边栏「模型历史表现」（命中率/爆冷/赛季/injury_shift 敏感性），ROI 因无历史赔率如实标注不可算；`data/calibration_report.json` 已生成。
- P3：9 场速览改 `st.container(border=True)` 卡片 + `st.progress` 概率条（移动端无需横向滑动）；伤病核心**红**/轮换**橙**/替补**灰**高亮。

---


- **数据源**：football-data.co.uk 德甲(D1) 近 3 赛季 CSV → `data/historical/{D1_2526,D1_2425,D1_2324}.csv`（各 306 场，共 918+）。
- **清洗**：`data/historical_data.py`（`load_seasons` + `add_rolling_features`：近3轮积分、滚动进球/射正、H2H）。
- **重训**：`trainer.py` → `trainer.ModelBundle`（缓存至 `artifacts/models.pkl`）
  - **Elo**：时间滚动更新 + **K 网格搜索**（10/15/20）× 主场优势（40/50/60）；
  - **Dixon-Coles**：**时间衰减(decay)** 加权拟合 attack/defence + **rho 网格**低比分修正；
  - **GBM**：**XGBoost + LightGBM** 三分类(H/D/A)，特征含滚动 xG 代理(射正)、近 3 轮状态、Elo 差分、H2H。
- **真实输出验证**：第 9 轮"美因茨 vs 拜仁"已从种子版"美因茨胜"回归 **"拜仁慕尼黑胜 · 客胜 71.9%"**（Elo 拜仁 2065 vs 美因茨 1517），体现真实实力差。

### ⚠️ 数据缺口说明（诚实标注）
football-data.co.uk 德甲 D1 **不含 xG 列（HG/AG）**；P1 用**射正(HST/AST)滚动均值作 xG 代理**进 GBM 特征。
P2 接入 football-data skill `get_event_xg` 替换真 xG，可进一步提升区分度。

## P0 数据链路说明（保留）

- **默认零密钥种子源**：`data/seed_data.py`（内置德甲 2026-27 对阵/积分/近期状态/伤病，标注"缓存/示例"）。
  实时源 football-data.org 免费层需 `X-Auth-Token`（本环境无 token，实测 403）；football-data skill / API-Football MCP 未提供。
- **接入实时源**：设置环境变量 `FOOTBALL_DATA_TOKEN` 后，`resolve_provider()` 自动切换 LiveProvider。
- **数据时效**：UI 顶部显示"数据最后拉取时间"，缓存数据标注"（缓存）"。
- **Provider/Repository 抽象**：本地历史源(`historical_data`)驱动训练与预测，实时 API(Provider)管实时伤病/赛程，二者通过 `data/api_client.resolve_provider` 与 `trainer` 解耦，互不干扰。


## P0 4 项验收条件（均已通过测试 + AppTest 真实渲染验证）

| 条件 | 实现 | 验证 |
|---|---|---|
| #1 数据时效提示 | 顶部"数据最后拉取时间 +（缓存）"标签 | `tests/test_p0.py` + AppTest |
| #2 未标定阈值透传 | 黄色警告横幅/卡片徽章"⚠️ 伤病偏移量未标定，仅供参考" | 同上 |
| #3 初版/确认版切换 | `st.session_state`；零密钥源无实时伤病 → 拦截提示"暂无赛前 48h 实时伤病数据，无法生成确认版" | 同上 |
| #4 降级容错 | 未来无数据轮次 GBM→DC 兜底输出基准概率，不抛 500 | 同上 |

## 目录职责

| 文件 | 职责 | 状态 |
|---|---|---|
| `app.py` | Streamlit 主页面（时效标签/未标定徽章/阶段拦截/卡片渲染） | ✅ P0+P1 可运行 |
| `config.py` | 单一配置源（`config.RULES` 唯一阈值入口 + calibrated） | 阈值待标定 |
| `schema.py` | 输出 JSON Schema + 校验 | ✅ |
| `pipeline.py` | Provider 数据链路 + P1 模型 + R1–R5 规则 + 降级容错 | ✅ P1 |
| `trainer.py` | Elo/DC/GBM 重训 + ModelBundle 融合 + 缓存 | ✅ P1 |
| `data/historical_data.py` | 近 3 赛季 CSV 清洗 + 滚动特征 + 队名映射 | ✅ P1 |
| `data/historical/` | 3 赛季德甲 CSV（football-data.co.uk D1） | ✅ P1 |
| `data/api_client.py` | Live(需token)/零密钥种子 双 Provider + 时效元数据 | ✅ P0 |
| `data/seed_data.py` | 德甲 2026-27 种子（赛程/伤病；预测现由历史模型驱动） | ✅ 兜底 |
| `models/` | Elo、Dixon-Coles、GBM 骨架（训练在 trainer.py） | 骨架 |
| `rules/iteration_rules.py` | R1–R5（v1.1 语义）落地 + 独立日志 | ✅ |
| `tests/test_p0.py` | 4 项 P0 验收测试 | ✅ 全绿 |
| `tests/test_p1.py` | 5 项 P1 验收测试 | ✅ 全绿 |

## P1 5 项验收清单（均已通过）

1. ✅ 数据接入：近 3 赛季德甲逐轮 CSV 抓取并清洗为模型可用格式。
2. ✅ 参数拟合：Elo K 网格搜索；DC 时间衰减系数 + rho 拟合。
3. ✅ 模型集成：XGBoost/LightGBM 训练，特征含滚动 xG(射正代理)、近 3 轮状态。
4. ✅ 真实输出验证：seed→historical 驱动，重跑 AppTest，R9 美因茨 vs 拜仁概率体现真实实力差（拜仁客胜 71.9%）。
5. ✅ 保持 Provider 抽象：本地历史读取与实时 API 通过统一接口层，互不干扰。

## R1–R5（技术需求 v1.1，阈值全部外置 config.RULES）

| 规则 | 语义 |
|---|---|
| R1 偏差记录 | 记录实际比分 vs 首选比分/胜平负倾向偏差；连续 3 轮同方向偏差触发该队 Elo 重校准 |
| R2 伤病升级 | 初版"中/低置信度"场次，赛前 48h 用最终伤病名单复算升级为确认版 |
| R3 国家队系数窗口 | 仅 FIFA 后首轮；国脚>180min 或跨洲>5000km 下调 xG 8%–15% |
| R4 分歧场次标注 | Elo 与 Dixon-Coles 方向不一致 → 置信度降一档并在风险备注明示 |
| R5 连续跟踪 | 第 9 轮起连续编号；输出末尾附上一轮偏差摘要 |

规则名锁定不变；`calibrated=false` 项页面显示"阈值未标定，仅供参考"。

## P2 · 跨赛事分析（德甲 + 欧冠，免费层真实跑通）

免费层额度评估（football-data.org v4，10req/min 共享）：

| 赛事 | 代码 | 免费层 | 说明 |
|---|---|---|---|
| 德甲 | BL1 | ✅ 200 | 真实赛程/积分 |
| 欧冠 | CL | ✅ 200 | 真实赛程（144 场，含拜仁/多特/莱比锡/斯图加特） |
| 欧联 | EL | ❌ 403 | 免费层受限，需付费 token |
| 德国杯 | DFB | ❌ 403 | 免费层受限，需付费 token |
| 欧协 | — | ❌ 404 | football-data 无此赛事（v4 的 EC=欧锦赛） |

实现（`data/cross_competition.py` + pipeline + app）：
- **疲劳特征引擎 FatigueEngine**：真实休息天数差（<3 天→xG 打折）、欧战客场疲劳附加、欧冠前置（本场后 7 天内该队有欧冠→"可能轮换"标记）。德甲轮次叠加欧冠赛程计算。
- **跨赛事 Elo CrossElo**：德甲队用模型 Elo；其他联赛对手优先精选豪门基准 Elo（Real/Arsenal/Man City 等 `CL_TEAM_ELO`），拉不到用联赛平均系数兜底（`LEAGUE_BASE_ELO`）；赛事 K 倍率（欧冠 1.25）。
- **Streamlit 赛事切换器**：德甲/欧冠免费可用，欧联/德国杯标注"免费层403需付费"，欧协标注"数据源无此赛事"；受限赛事切换→诚实提示不抛 500。
- **UI 预警**：德甲卡片若某队欧冠前置→"⚠️ 欧冠前置，可能轮换（已标定折入轮换折扣）"；欧冠卡片标注"跨赛事 Elo 兜底（未实时校准）"。
- 诚实标注：**疲劳折扣系数已标定（calibrated=True）**——用 3 季(2324/2425/2526)真实欧冠赛程+德甲 918 场 Elo 前瞻残差做队内配对标定（scripts/calibrate_fatigue.py）：欧冠前置(轮换)实测 ≈ -0.036 球/场(n=144, ≈2%)，单日折扣 1.4%、封顶 4%、轮换扁平折扣 2%（非早期保守默认 4%/12%）；短休息<3 天在德甲轮次内罕见。跨赛事 Elo 的对手兜底（精选豪门/联赛平均）仍为静态基准、未实时校准。

真实链路示例（第 9 轮 + 欧冠 MD4）：
- 德甲 R9 美因茨 vs 拜仁：主休息7/客休息6，拜仁客胜 0.719（疲劳折扣 0，R9 无 <3 天休息队）。
- 欧冠 MD4 马竞 vs 拜仁：拜仁客胜 0.812；莱比锡 vs 曼城：莱比锡主胜（模型 Elo 支持）。

## P2 · 伤病自动化（ESPN 直连自动抓取 + 一键勾选确认）

用户追加要求"伤病不能只靠手动刷"。三方案实测：

- **方案一（新闻 RSS）**：Google News→ReadTimeout、新浪体育→403、百度新闻→安全验证壳、懂球帝→567 反爬 → **国内不可用**。
- **方案二（API-Sports 官网）**：dashboard.api-football.com 需您注册免费账号拿 Token 后直连（当前无该 Token，未测）。
- **方案三（ESPN 直连）**：`site.api.espn.com/apis/site/v2/sports/soccer/ger.1` 国内 HTTP 200（`injuries`/`news`/`teams`），**为当前唯一可用自动源**。

落地 `data/injury_fetch.py`（ESPN 双源，无需 token）：
- `fetch_espn_injuries()`：官方结构化伤病接口（当前德甲为空 → 诚实空，正常）。
- `fetch_espn_news_candidates()`：`/news` 关键词提取（ruled out/out for/injured/sidelined/doubtful…），**已过滤 penalty/角球误报**（如 "Kane misses penalty" 不会误判为伤病）。
- `fetch_all_candidates()`：合并双源 → `{candidates, note}`。

UI（app.py）：保留手动录入框，新增 **"🔎 自动抓取伤病候选（ESPN国内直连）"按钮** → 抓到的球员渲染为 Checkbox → 一键勾选 → `_merge_injuries` 并入对应场次 injuries → 生成确认版复算。ESPN 当前无伤病记录时诚实提示并落回手动录入兜底。

> 若后续方案二（您注册 API-Sports 免费 Token）可用，可在 `injury_fetch.py` 增一个 provider 与 ESPN 并列；当前 ESPN 已满足"自动抓新闻→关键词提取→一键勾选确认"主链路。

## P2 阶段 · 真实数据联调（本轮完成，逻辑闭环）

用您提供的 `FOOTBALL_DATA_TOKEN` 跑通第 9 轮真实数据：

- **真实赛程/积分**：football-data.org v4 第 9 轮 9 场真实对阵（含 `1. FSV Mainz 05 vs FC Bayern München`，id=565851）；真实积分榜（拜仁 18 分第 1、美因茨 10 分第 8）。新增**队名归一化映射层**（`FDO_NAME2CODE`，football-data 真实名 → code/中文）。
- **动态限流防 403（用户点名）**：`cache.py` 从 Response Headers 读 `x-requests-available-minute` + `X-RequestCounter-Reset`（免费层 10 req/**min**），持久化到 `quota_state`；分钟窗口剩余 0 时直接拦截防 403，`_call` 每次响应后更新。
- **伤病替代源（按优先级实测）**：方案 A TheSportsDB(test key=3)→**403 不可用**；方案 B Transfermarkt→Cloudflare(202/0B)、Fotmob(404) 被网络阻断；**采用方案 C：Streamlit 前端手动录入伤病文本框**（`美因茨: 布卡特\n拜仁: 凯恩,穆夏拉,格纳布里?`，支持简称/全名双向匹配）。
- **确认版 48h 复算闭环**：手动伤病按 `injury_shift`（缺阵0.10/存疑0.05，上限0.30）折入 DC 复算概率。第 9 轮美因茨 vs 拜仁：初版客胜 0.719 → 确认版 0.679（主0.1/客0.25 偏移）。R9 距开赛 6 天未进入 48h 窗口 → 如实标注"复算预览"。
- **真 xG**：football-data.org 无 xG 字段（已实测）；Understat 在本环境仅返回 JS 壳页（无数据 JSON）；第 9 轮未开赛本无真 xG → **诚实保留射正(HST)代理**，未假装接入。
- **标定 R2/R3/injury_shift**：无足够真实伤病/国家队数据 → 保留保守默认值，`calibrated=False`，前端黄牌"⚠️ 伤病偏移量未标定，仅供参考"。

## P2 准备（API 接入层，Token 到位即启用）

- **双实时源**：`data/api_client.py` 新增 `ApiFootballProvider`（读 `RAPIDAPI_KEY`，API-Football v3）与 `LiveFootballDataProvider`（读 `FOOTBALL_DATA_TOKEN`）；`resolve_provider()` 按优先级自动启用，否则回退零密钥种子。
- **每日限流保护**：`data_stores/cache.py` 新增日配额记账 + 当日同资源缓存命中，超配额返回缓存/降级，绝不抛 500。
- **get_event_xg**：API-Football `/fixtures/statistics` 拉取 Expected Goals；无 token/源不支持时返回 `None`（pipeline 回退射正 xG 代理）。
- 您提供的两个 token 到位后，实时伤病（R2 确认版闸门）与真 xG 即自动启用。

## P3 闭环基础 · R1/R4 历史实测标定 + R5 记录库（本轮完成）

运行 `python3 scripts/backtest.py` 对 3 赛季 918 场 walk-forward 回溯，结果：
- **R5 记录库**：`data_stores/bias.db`（bias_predictions 表，918 条）存储"历史结果 vs 预测结果"比对；命中率 52.4%，Elo/DC 分歧率 35.9%。
- **真实赛果持久化（P6）**：`bias.db` 为运行时库（`*.db` 不入库，云端重部署重建为空）。真实赛果经 `scripts/record_live_result.py` **同时写入** `data/live_results.json`（静态、入库），app 启动经 `data.live_results.merge_with_bias()` 与 bias.db 历史合并，保证 R1 偏差记录与 R5 连续跟踪在公网端真正生效；侧边栏「真实赛果记录」展示实战场次与命中。
- **R1 实测标定**：基线方向错误率 47.6%；连续 3 轮同方向偏差后下轮仍错率 64.4%（n=45，+16.8pp）→ `consecutive_rounds=3`，`calibrated=True`。
- **R4 实测标定**：基线爆冷率 22.2%；Elo/DC 分歧场爆冷率 30.0%（一致仅 17.9%）；Δp≥0.10 后爆冷率抬升至 24%+ → `submodel_disagreement_threshold=0.10`（实数，代码已实际使用），`calibrated=True`。

## TODO 清单（按开发阶段）

- **P1**：✅ 历史数据灌入 + Elo/DC/GBM 重训 + R9 真实概率验证。
- **P2**：✅ 接口层/限流缓存/get_event_xg 就绪；待 `FOOTBALL_DATA_TOKEN` + `RAPIDAPI_KEY` 到位后实时启用。
- **P3**：✅ R1/R4 历史标定 + R5 记录库；待办：R2/R3/injury_shift 阈值标定、偏差回溯看板、R1 重校准算子落地。
