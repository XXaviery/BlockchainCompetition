# 报告证据索引

本索引仅对应最终统一工程。设计文档中的软件数值统一从 `outputs/final_report_metrics.json` 引用。

## 3.3 多源环境采集

- 证据：Phase-1 `EnvSample` 数据结构和模拟采样链路。
- 代码：`src/air_governance/common/types.py`；`src/air_governance/simulation/environment.py`；`src/air_governance/sensing/quality.py`。
- 日志：`outputs/logs/brain.db` → `env_samples`。
- CSV：`outputs/logs/csv/env_samples.csv`。
- 截图/图：`outputs/report_figures/01_system_overview.png`。
- 真实性：`SIMULATION` / `SOFTWARE_IN_THE_LOOP`。

## 3.4 区域污染状态

- 证据：每个区域独立 rolling buffer、均值/最大值/标准差、斜率、异常持续时间、邻区差和 data age。
- 代码：`src/air_governance/state/region_state.py`。
- 输出：`data/processed/region_state_dataset.csv`；`outputs/logs/brain.db` → `region_states`。
- 图片：`outputs/report_figures/02_spatial_risk_map.png`。

## 3.5 风险预测

- 代码：`src/air_governance/models/risk_model.py`。
- 模型：`models/risk/model.json`；`models/risk/feature_columns.json`；`models/risk/model_metadata.json`。
- 指标：`outputs/metrics/risk_metrics.json`；最终引用 `outputs/final_report_metrics.json` → `risk`。
- 数据集：`data/processed/risk_dataset.csv`；`data/processed/risk_dataset_manifest.json`。
- 脚本：`scripts/train_risk_model.py`；`scripts/evaluate_models.py`。
- 图片：`03_risk_prediction.png`、`05_feature_importance.png`、`06_shap_summary.png`、`10_prediction_residuals.png`、`11_trend_accuracy.png`。

## 3.6 任务排序

- 代码：`src/air_governance/models/ranker_model.py`；`src/air_governance/decision/task_ranker.py`；`src/air_governance/decision/candidate.py`。
- 模型：`models/ranker/model.json`；`models/ranker/feature_columns.json`；`models/ranker/model_metadata.json`。
- 指标：`outputs/metrics/rank_metrics.json`；最终引用 `outputs/final_report_metrics.json` → `ranker`。
- 数据集：`data/processed/rank_dataset.csv`；`data/simulation/candidate_ground_truth.csv`。
- 图片：`04_ranker_decision.png`、`12_ndcg_distribution.png`。

## 3.7 状态机

- 代码：`src/air_governance/task/state_machine.py`；`src/air_governance/task/manager.py`。
- 统一运行封装：`src/air_governance/runtime/software_loop.py`。
- 日志：`outputs/logs/brain.db` → `task_state_events`。
- 图片：`08_closed_loop_timeline.png`。

## 3.8 导航、避障与安全机制

- 导航接口：`src/air_governance/adapters/base.py`。
- Mock 导航：`src/air_governance/adapters/mock_navigation.py`。
- 安全监督：`src/air_governance/safety/supervisor.py`；`src/air_governance/task/manager.py::select_safe_task`。
- MODEL_ERROR fallback：`src/air_governance/runtime/software_loop.py` 调用 Phase-1 `RuleBasedPolicy`。
- 日志：`navigation_events`、`safety_events`。
- 图片：`09_safety_fallback.png`。
- 边界：Mock Navigation ≠ 实机导航。

## 3.9 空气治理与复测

- 治理接口：`src/air_governance/adapters/base.py`。
- Mock 治理：`src/air_governance/adapters/mock_purification.py`。
- 环境响应：`src/air_governance/simulation/environment.py::set_purification`。
- 日志：`purification_events`、`env_samples`、`region_states`。
- 图片：`14_cycle_risk_change.png`。
- 边界：Mock Purification ≠ 实机净化；Simulation PM2.5/VOC/CO₂ ≠ 真实传感器数据。

## 3.10 上位机交互与数据记录

- GUI：`ui/main_window.py`；`ui/pages/` 六个页面。
- Backend：`services/gui_backend.py` → `UnifiedBrainRuntime`。
- Replay：`services/replay_service.py`。
- Logger / DB：`src/air_governance/logging/store.py`。
- 数据库说明：`docs/database_schema.md`。

## 4.4 风险模型测试

- 指标来源：`outputs/final_report_metrics.json` → `risk`。
- 原始指标文件：`outputs/metrics/risk_metrics.json`。
- 数据集：`data/processed/risk_dataset.csv` 的 `split=test`。
- 测试样本划分：`data/processed/risk_dataset_manifest.json`。
- 脚本：`scripts/generate_simulation_data.py` → `scripts/build_dataset.py` → `scripts/train_risk_model.py` → `scripts/evaluate_models.py`。

## 4.5 Ranker 测试

- 指标来源：`outputs/final_report_metrics.json` → `ranker`。
- 原始指标文件：`outputs/metrics/rank_metrics.json`。
- 数据集：`data/processed/rank_dataset.csv` 的 `split=test`。
- query group：`decision_id`。
- 脚本：`scripts/train_ranker.py`；`scripts/evaluate_models.py`。
- 图：`04_ranker_decision.png`、`12_ndcg_distribution.png`。

## 4.6 Rule vs Ranker

- 指标来源：`outputs/final_report_metrics.json` → `policy_comparison`。
- 规则策略：`src/air_governance/decision/rule_policy.py`。
- Ranker：`src/air_governance/models/ranker_model.py`。
- 同一数据/utility：`scripts/train_ranker.py` 中 test split 与 `ground_truth_utility`。
- 相对提升公式：`(ranker_mean_utility - rule_mean_utility) / rule_mean_utility × 100%`，由程序计算。
- 图：`07_rule_vs_ranker.png`、`13_ranker_vs_rule_utility.png`。

## 4.7 闭环与安全测试

- CLI Demo：`scripts/run_demo.py`；结果文件 `outputs/metrics/run_demo_result.json`。
- GUI headless：`scripts/run_gui.py --demo-headless`；结果文件 `outputs/metrics/gui_headless_result.json`。
- 自动测试：`pytest -q`；冻结记录 `outputs/metrics/pytest_output.txt`。
- 图：`08_closed_loop_timeline.png`、`09_safety_fallback.png`、`14_cycle_risk_change.png`。

## 正式图来源清单

统一清单：`outputs/report_figures/manifest.csv`。每行包含 `figure_id`、`filename`、`generator_script`、`data_source`、`model_version`、`generated_at`、`source_type`。
