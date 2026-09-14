# 智驭新风 3–5 分钟竞赛演示脚本

## 0:00–0:30

打开“系统总览”，指出状态栏中的 `SIMULATION / SOFTWARE-IN-THE-LOOP`，点击“开始演示”。环境样本由 Phase-1 `IndoorEnvironmentSimulator` 产生并封装为 Phase-1 `EnvSample`，随后进入 `RegionStateBuilder`。

## 0:30–1:00

切换到“空间污染状态”和“风险预测”。说明风险结果来自最终 `models/risk/` 中的 Phase-1 XGBoost Regressor，页面指标从 `outputs/metrics/risk_metrics.json` 读取，不是写死在 GUI 中。

## 1:00–1:30

进入“任务排序与决策解释”。同一 `decision_id` 下展示多个 Phase-1 `CandidateTask`。在 Ranker 模式中结果来自 Phase-1 `RankerModel`；切换规则模式后调用 Phase-1 `RuleBasedPolicy`。

## 1:30–2:00

展示 `NAVIGATING → ARRIVED → PURIFYING`。明确导航来自 Phase-1 `MockNavigationAdapter`，治理来自 Phase-1 `MockPurificationAdapter` 和模拟环境响应，均为软件在环，不描述成实机数据。

## 2:00–2:30

展示 `RECHECKING → REDECIDING`。治理后样本重新进入 Phase-1 `RegionStateBuilder` 与风险/排序链路，形成复测闭环。

## 2:30–3:00

进入“闭环任务与日志”，按 `task_id` 回放 `task_state_events`。说明状态来自 Phase-1 `TaskManager.sm / TaskStateMachine`，九张日志表由 Phase-1 `BrainLogStore` 的最终统一 schema 写入。

## 3:00–3:30

触发 `MODEL_ERROR` 并单步推进到 `DECIDING`。展示 Ranker 可用性故障后统一 runtime 切换 Phase-1 `RuleBasedPolicy`；候选任务仍由 Phase-1 `SafetySupervisor` 检查。`safety_events` 记录该降级过程。

## 3:30–4:00

任选 `LOW_BATTERY`、`LOCALIZATION_ERROR` 或 `FORBIDDEN_ZONE` 演示。强调这是安全输入注入，最终判定来自 Phase-1 `SafetySupervisor`，不是 GUI 中的独立安全逻辑。

## 4:00–4:30

点击“导出报告图”，展示 `outputs/report_figures/`、`outputs/report_tables/` 和 `manifest.csv`。每张图都有生成脚本、数据源、模型版本、生成时间与真实性类型。

## 4:30–5:00

回到系统总览，确认 `completed_cycles=3`、`final_state=COMPLETED`。设计文档中的最终软件数字只引用 `outputs/final_report_metrics.json`。
