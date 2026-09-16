# 最终 SQLite 数据库结构

最终 schema 只定义于 `src/air_governance/logging/store.py`，写入入口只使用 Phase-1 `BrainLogStore.log()`。Phase 1 原事件式日志与 Phase 2 typed schema 的冲突已合并：每张表同时保留 `event_key` / `payload_json` 追溯字段和 GUI/回放需要的 typed columns。

## 统一表清单

### `env_samples`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `pose_x` | `REAL` | 0 | 0 |
| `pose_y` | `REAL` | 0 | 0 |
| `pose_yaw` | `REAL` | 0 | 0 |
| `pm25` | `REAL` | 0 | 0 |
| `voc` | `REAL` | 0 | 0 |
| `co2` | `REAL` | 0 | 0 |
| `temperature` | `REAL` | 0 | 0 |
| `humidity` | `REAL` | 0 | 0 |
| `fan_level` | `INTEGER` | 0 | 0 |
| `battery_soc` | `REAL` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `robot_state` | `TEXT` | 0 | 0 |
| `valid_flag` | `INTEGER` | 0 | 0 |
| `quality_code` | `TEXT` | 0 | 0 |
| `source_type` | `TEXT` | 0 | 0 |
| `episode_id` | `TEXT` | 0 | 0 |
| `scenario_id` | `TEXT` | 0 | 0 |

### `region_states`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `current_risk` | `REAL` | 0 | 0 |
| `predicted_risk` | `REAL` | 0 | 0 |
| `trend` | `TEXT` | 0 | 0 |
| `pm25` | `REAL` | 0 | 0 |
| `voc` | `REAL` | 0 | 0 |
| `co2` | `REAL` | 0 | 0 |
| `temperature` | `REAL` | 0 | 0 |
| `humidity` | `REAL` | 0 | 0 |
| `abnormal_duration` | `REAL` | 0 | 0 |
| `waiting_time` | `REAL` | 0 | 0 |
| `data_age` | `REAL` | 0 | 0 |
| `distance` | `REAL` | 0 | 0 |
| `estimated_move_time` | `REAL` | 0 | 0 |
| `estimated_energy` | `REAL` | 0 | 0 |
| `switch_cost` | `REAL` | 0 | 0 |
| `valid_flag` | `INTEGER` | 0 | 0 |
| `quality_code` | `TEXT` | 0 | 0 |

### `risk_predictions`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `current_risk` | `REAL` | 0 | 0 |
| `predicted_risk` | `REAL` | 0 | 0 |
| `future_risk` | `REAL` | 0 | 0 |
| `trend` | `TEXT` | 0 | 0 |
| `true_trend` | `TEXT` | 0 | 0 |
| `predicted_trend` | `TEXT` | 0 | 0 |
| `source_type` | `TEXT` | 0 | 0 |

### `candidate_tasks`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `current_risk` | `REAL` | 0 | 0 |
| `predicted_risk` | `REAL` | 0 | 0 |
| `trend` | `TEXT` | 0 | 0 |
| `abnormal_duration` | `REAL` | 0 | 0 |
| `waiting_time` | `REAL` | 0 | 0 |
| `data_age` | `REAL` | 0 | 0 |
| `distance` | `REAL` | 0 | 0 |
| `estimated_move_time` | `REAL` | 0 | 0 |
| `estimated_energy` | `REAL` | 0 | 0 |
| `battery_soc` | `REAL` | 0 | 0 |
| `switch_cost` | `REAL` | 0 | 0 |
| `scene_weight` | `REAL` | 0 | 0 |
| `reachable` | `INTEGER` | 0 | 0 |
| `task_score` | `REAL` | 0 | 0 |
| `valid_flag` | `INTEGER` | 0 | 0 |
| `safety_result` | `TEXT` | 0 | 0 |
| `selected` | `INTEGER` | 0 | 0 |
| `policy` | `TEXT` | 0 | 0 |
| `rank_position` | `INTEGER` | 0 | 0 |

### `rank_results`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `task_score` | `REAL` | 0 | 0 |
| `rank_position` | `INTEGER` | 0 | 0 |
| `selected` | `INTEGER` | 0 | 0 |
| `policy` | `TEXT` | 0 | 0 |

### `safety_events`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `region` | `TEXT` | 0 | 0 |
| `event_type` | `TEXT` | 0 | 0 |
| `input_task` | `TEXT` | 0 | 0 |
| `action` | `TEXT` | 0 | 0 |
| `result` | `TEXT` | 0 | 0 |
| `reason` | `TEXT` | 0 | 0 |
| `fallback` | `TEXT` | 0 | 0 |

### `navigation_events`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `region` | `TEXT` | 0 | 0 |
| `target_region` | `TEXT` | 0 | 0 |
| `target_pose` | `TEXT` | 0 | 0 |
| `event` | `TEXT` | 0 | 0 |
| `status` | `TEXT` | 0 | 0 |
| `success` | `INTEGER` | 0 | 0 |
| `arrived` | `INTEGER` | 0 | 0 |
| `source_type` | `TEXT` | 0 | 0 |

### `purification_events`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `region` | `TEXT` | 0 | 0 |
| `event` | `TEXT` | 0 | 0 |
| `fan_level` | `INTEGER` | 0 | 0 |
| `pre_risk` | `REAL` | 0 | 0 |
| `post_risk` | `REAL` | 0 | 0 |
| `source_type` | `TEXT` | 0 | 0 |

### `task_state_events`

| 字段 | 类型 | 非空 | 主键 |
|---|---|---:|---:|
| `id` | `INTEGER` | 0 | 1 |
| `timestamp` | `TEXT` | 1 | 0 |
| `event_key` | `TEXT` | 0 | 0 |
| `payload_json` | `TEXT` | 1 | 0 |
| `decision_id` | `TEXT` | 0 | 0 |
| `task_id` | `TEXT` | 0 | 0 |
| `region_id` | `TEXT` | 0 | 0 |
| `region` | `TEXT` | 0 | 0 |
| `old_state` | `TEXT` | 0 | 0 |
| `new_state` | `TEXT` | 0 | 0 |
| `state` | `TEXT` | 0 | 0 |
| `event` | `TEXT` | 0 | 0 |
| `reason` | `TEXT` | 0 | 0 |
| `cycle` | `INTEGER` | 0 | 0 |

## 主追溯键

- `timestamp`：事件时间。
- `event_key`：Phase-1 logger 兼容追溯键，通常为 `task_id`、`decision_id` 或 `region_id`。
- `decision_id`：一次候选任务排序周期。
- `task_id`：一个闭环执行任务。
- `region_id`：区域标识（A/B/C/D）。
- `payload_json`：完整原始 payload 的 JSON 镜像；typed columns 用于检索/回放。

## Schema 范围

- 最终 schema 只保留 `BrainLogStore` 这一套实现；typed columns 合并进 `BrainLogStore`。
- `BrainLogStore` 启动时检测旧 schema；若发现不兼容旧表，会重建为最终 schema。

## 真实性边界

数据库中的 `source_type=SIMULATION` 或软件在环事件不等于真实硬件采集。详见 `docs/evidence_boundary.md`。
