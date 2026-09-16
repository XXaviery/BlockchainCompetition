# GUI 架构说明（最终统一工程）

## 1. 分层关系

```text
ui/main_window.py + ui/pages/*
        │
        ▼
services/gui_backend.py
        │  仅作 GUI/service facade
        ▼
src/air_governance/runtime/software_loop.py
        │
        ├── common/types.py
        │     EnvSample / RegionState / CandidateTask /
        │     RobotStatus / SystemSnapshot
        ├── state/region_state.py
        │     RegionStateBuilder
        ├── models/risk_model.py
        │     Phase-1 XGBoost Regressor
        ├── models/ranker_model.py + decision/task_ranker.py
        │     Phase-1 XGBoost Ranker
        ├── decision/candidate.py + decision/rule_policy.py
        │     CandidateGenerator / RuleBasedPolicy
        ├── safety/supervisor.py
        │     SafetySupervisor
        ├── task/manager.py + task/state_machine.py
        │     TaskManager / TaskStateMachine
        ├── adapters/mock_navigation.py
        ├── adapters/mock_purification.py
        └── logging/store.py
              BrainLogStore / unified SQLite schema

services/replay_service.py ──> 同一 SQLite schema ──> 第 6 页任务回放
services/report_exporter.py ─> 最终模型/数据/日志 ─> PNG / CSV / manifest
scripts/run_demo.py ----------> UnifiedBrainRuntime
scripts/run_gui.py -----------> GuiBackend(UnifiedBrainRuntime)
```

## 2. GUI 与核心模块的复用关系

GUI 不实现第二套风险公式、Ranker、规则评分、安全判断或状态机。六个页面只读取由 `UnifiedBrainRuntime` 生成的 Phase-1 `SystemSnapshot`、`RegionState`、`CandidateTask` 和日志。

- 风险预测：`RiskModel.load(models/risk)` + `RiskModel.predict()`。
- 任务排序：`RankerModel.load(models/ranker)` + `rank_candidates()`。
- 规则切换：`RuleBasedPolicy.rank()`。
- 安全监督：`TaskManager.select_safe_task()` 内部调用 `SafetySupervisor.check()`。
- 状态机：`TaskManager.sm`，类型为 Phase-1 `TaskStateMachine`。
- 导航：Phase-1 `MockNavigationAdapter`。
- 治理：Phase-1 `MockPurificationAdapter` + `IndoorEnvironmentSimulator.set_purification()`。
- 日志：Phase-1 `BrainLogStore`。

`MODEL_ERROR` 不是第二套安全算法：它表示 Ranker 可用性故障，统一 runtime 选择 Phase-1 `RuleBasedPolicy` 作为 fallback；随后候选任务仍经过 Phase-1 `SafetySupervisor`。

## 3. 指标读取

GUI 不硬编码 MAE、RMSE、R²、Trend Accuracy、NDCG@3、Precision@3 或 Top-1 Accuracy。`services/gui_backend.py` 在启动时从以下最终产物读取：

- `outputs/metrics/risk_metrics.json`
- `outputs/metrics/rank_metrics.json`

设计文档最终引用的数字以 `outputs/final_report_metrics.json` 为唯一冻结来源。

## 4. 状态机

自动演示状态来自 Phase-1 `TaskStateMachine`：

```text
SENSING → DECIDING → NAVIGATING → ARRIVED
        → PURIFYING → RECHECKING → REDECIDING
        → 下一周期 / COMPLETED
```

导航与治理均为 Mock 软件在环执行，不代表真实机器人导航或净化性能。

## 5. SQLite

最终只维护 `src/air_governance/logging/store.py` 中的一套 schema。九张表为：

`env_samples`、`region_states`、`risk_predictions`、`candidate_tasks`、`rank_results`、`safety_events`、`navigation_events`、`purification_events`、`task_state_events`。

`BrainLogStore.log()` 是统一写入口；服务层仅通过查询接口或只读 SQLite 连接读取数据。

## 6. 报告导出

`services/report_exporter.py` 从最终数据集、模型和统一日志直接生成 14 张 1600×900 PNG、5 个 CSV 以及 `outputs/report_figures/manifest.csv`。正式图不依赖桌面截图或提前保存的 GUI PNG。

## 7. GUI 框架

桌面端使用 Tkinter/ttk + matplotlib。GUI 框架仅位于 `ui/`，核心 AI、任务、安全、适配器和日志层不依赖 Tkinter。
