## 本次改动

<!-- 一句话说明改了什么、为什么 -->

## 检查表

- [ ] 已跑 `python -m pytest -q --ignore=tests/test_p3.py`，全绿
- [ ] 若改了概率/比分/阈值等输出层，已按 CALIBRATION_GATE 给出四件套：时间切分、配对 bootstrap CI、效应量、churn 计数
- [ ] **本次是否引入新的模型/口径提案？若是，已在 `CALIBRATION_GATE.md` 被测清单登记（提案 / 效应量 / n / 结论 / 日期）**
- [ ] 未改动 `models.pkl` / β / mu / rho（除非样本冻结线已解除并在描述中写明）
- [ ] 涉及跨环境同步时，用 `scripts/sync_from_raw.ps1`（大小校验）而非裸下载

## 备注

<!-- 留痕：被否的方案也写一句，避免下次重复试 -->
