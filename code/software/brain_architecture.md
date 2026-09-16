# 智驭新风“大脑”架构说明

## 1. 系统边界

本工程负责机器人任务级智能决策，不直接实现电机 PID、PWM、SLAM、雷达驱动、局部避障、风机底层驱动或 BMS 控制。底层能力通过 Adapter 接口接入。

核心闭环：

```text
EnvSample
  ↓
数据质量控制
  ↓
RegionState
  ↓
XGBoost Regressor
  ↓ predicted_risk / trend
CandidateTask
  ↓
XGBoost Ranker / RuleBasedPolicy fallback
  ↓
SafetySupervisor
  ↓
NavigationAdapter
  ↓
PurificationAdapter
  ↓
RECHECKING
  ↓
重新采样 → RegionState → Regressor → Ranker
```

## 2. 数据对象

### EnvSample

统一承载时间、区域、位姿、PM2.5、VOC、CO₂、温湿度、风机、电量、任务、决策周期、机器人状态、数据质量与来源类型。

`source_type` 支持：

- `REAL`
- `SIMULATION`
- `SYNTHETIC`
- `REPLAY`

当前最终软件训练数据全部来自模拟器并标记为 `SIMULATION`。

### RegionState

每个区域维护独立滑动缓存，计算：

- 当前 PM2.5 / VOC / CO₂ / 温湿度
- rolling mean / max / std
- PM2.5 / VOC / CO₂ slope
- abnormal_duration
- neighbor_diff
- data_age
- sample_interval
- valid_flag / quality_code
- hour / time_period

### CandidateTask

同一个 `decision_id` 下的全部区域候选组成一个 Ranker query group。候选包含风险、趋势、等待时间、距离、移动时间、能耗、电量、切换成本、可达性等字段。

## 3. 风险模型

风险教师标签将环境风险拆成污染、趋势、持续时间和温湿度影响四部分。训练目标不是当前规则风险，而是：

```text
RegionState(t) → Risk(t + Δ)
```

当前 `Δ = 300 s`。

数据质量通过 `risk_confidence` 独立表达，不通过简单乘法降低环境风险，避免出现“数据越差，风险越低”的错误逻辑。

CO₂ 可提高环境风险和通风提示，但普通滤芯不直接治理 CO₂。因此直接治理收益的 `purifiable_fraction` 只由 PM2.5 与 VOC 形成。

## 4. Ranker 标签

模拟器在每个 decision snapshot 对 A/B/C/D 分别执行反事实 rollout，计算目标区域治理带来的未来污染负荷下降量 `counterfactual_risk_gain`。

效用：

```text
U = α × risk_gain
  - β × response_cost
  - γ × energy_cost
  - δ × switch_cost
```

同一 `decision_id` 内按效用映射 relevance grade。训练使用 `XGBRanker(objective="rank:ndcg")`，不是普通分类器。

## 5. 安全监督

`SafetySupervisor` 位于模型和执行模块之间。模型得分不能绕过：

- reachable
- forbidden zone
- battery SOC
- localization status
- collision risk
- sensor health
- fan health
- emergency stop
- manual stop

输出为：`ALLOW / REJECT / SAFE_STOP / FALLBACK`。

## 6. 高层状态机

正常状态：

```text
IDLE
SENSING
DECIDING
NAVIGATING
ARRIVED
PURIFYING
RECHECKING
REDECIDING
COMPLETED
```

异常状态：

```text
PAUSED
SAFE_STOP
LOW_BATTERY
LOCALIZATION_ERROR
DEVICE_FAULT
MANUAL_CONTROL
```

每次状态变化写入 `task_state_events`。

## 7. Adapter 接口

### NavigationAdapter

- `navigate_to()`
- `cancel_navigation()`
- `get_pose()`
- `get_distance_to_target()`
- `is_arrived()`
- `is_localization_ok()`
- `is_reachable()`

最终软件在环运行使用 `MockNavigationAdapter`。

### PurificationAdapter

- `set_fan_level()`
- `start_purification()`
- `stop_purification()`
- `get_fan_state()`
- `is_fan_healthy()`

最终软件在环运行使用 `MockPurificationAdapter`。

## 8. 日志

SQLite 表：

- env_samples
- region_states
- risk_predictions
- candidate_tasks
- rank_results
- safety_events
- navigation_events
- purification_events
- task_state_events

所有表可导出 CSV，用于回放、测试报告和答辩佐证。

## 9. GUI 接口

`SystemSnapshot` 输出：

- robot_state
- robot_pose
- battery
- fan_level
- regions
- candidates
- selected_task
- safety_state
- task_history

上位机只需要消费该结构，不必直接访问模型内部对象。

## 10. 最终证据边界

当前模型指标、对照图和 demo 日志来自 `IndoorEnvironmentSimulator`。这些结果用于软件闭环、模型训练流程、状态机和算法对照的可复现验证，不代表真实机器人环境下的最终性能。
