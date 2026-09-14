# 证据真实性边界

## A. REAL CODE IMPLEMENTATION

以下内容在最终软件工程中有可执行代码、测试或产物支持：

- Phase-1 `EnvSample`、`RegionState`、`CandidateTask`、`RobotStatus`、`SystemSnapshot`。
- 区域时序状态构建、滑动统计、趋势/数据质量特征。
- XGBoost Regressor 训练、保存、加载、推理、评价。
- XGBoost Ranker 训练、保存、加载、query-group 排序与评价。
- CandidateGenerator。
- RuleBasedPolicy。
- SafetySupervisor。
- TaskManager / TaskStateMachine。
- NavigationAdapter / PurificationAdapter 抽象接口。
- MockNavigationAdapter / MockPurificationAdapter。
- SQLite BrainLogStore、九张统一表、CSV 导出。
- Tkinter/ttk + matplotlib 六页面 GUI。
- 暂停、继续、单步、重置、Rule/Ranker 切换、安全事件注入、MODEL_ERROR → RuleBasedPolicy fallback。
- task_id 日志回放。
- 14 张报告 PNG、5 个报告 CSV、figure manifest。
- pytest 自动测试。

“REAL CODE IMPLEMENTATION”表示软件模块真实存在并可执行，不等于相应机器人行为已经经过真实硬件验证。

## B. SOFTWARE-IN-THE-LOOP

以下结果由软件模拟环境、Mock 适配器或模拟复测产生：

- 室内 PM2.5 / VOC / CO₂ / 温湿度变化。
- 机器人区域位置和移动目标。
- 导航到点过程与 `MOCK_NAVIGATING / MOCK_ARRIVED`。
- 风机档位、模拟治理过程和治理后环境变化。
- 治理后复测及风险变化。
- 三个完整闭环周期。
- 风险模型训练/测试所用 `data/simulation/` 数据。
- Ranker 的 counterfactual risk gain、utility、Rule vs Ranker 对照。
- 报告图中基于上述模拟数据和软件在环日志的曲线、热图、时间线与对照图。

明确边界：

- Mock Navigation ≠ 实机导航。
- Mock Purification ≠ 实机净化。
- Simulation PM2.5/VOC/CO₂ ≠ 真实传感器数据。
- 软件在环风险下降 ≠ 真实 CADR、净化效率、能耗或噪声实测。

## C. REAL HARDWARE EVIDENCE

最终软件 ZIP 内没有真实传感器日志、真实导航轨迹、真实净化效率、真实能耗或真实噪声测试数据。

外部设计文档材料中存在“三轮全向移动底盘实物样机”照片，可作为“已存在硬件样机外观”的证据；该照片本身不能证明本最终软件已完成真实底盘导航闭环、真实传感采集或真实空气治理性能测试。

因此，本工程不将任何软件指标、模拟曲线或 Mock 事件标记为 `REAL_HARDWARE`。
