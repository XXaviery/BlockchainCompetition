# MOF-Robot 双导航主线与实车验收计划

> 状态：执行基线  
> 建立日期：2026-08-13  
> 适用范围：底盘解算、IMU、EKF、SLAM/Nav2、轮速—惯性航位推算  
> 详细历史留痕：[DEBUG_ARCHIVE.md](../../../DEBUG_ARCHIVE.md)  
> 双端同步协议：[SYNC_PROTOCOL.md](SYNC_PROTOCOL.md)

## 1. 最终目标

项目最终需要提供两种相互独立、共享底层状态估计的导航方式。

### 模式 A：EKF + SLAM/Nav2

- 编码器轮速提供局部平移速度 `vx/vy`。
- IMU 陀螺仪提供 `gyro_z`。
- EKF 发布连续的 `/odom` 和唯一的 `odom -> base_footprint`。
- 在线建图或在线 SLAM 导航时，由 `slam_toolbox` 发布唯一的 `map -> odom`。
- 使用保存地图导航时，由 AMCL 发布唯一的 `map -> odom`，此时不得同时启动 `slam_toolbox` mapping。
- Nav2 的速度命令必须经过 velocity smoother 和 Collision Monitor 后才能进入底盘。

### 模式 B：轮速—惯性辅助航位推算

- 继续使用相同的 `/wheel/odom + /imu -> EKF -> /odom` 局部估计层。
- 不启动 SLAM、AMCL、map server 和全局地图定位。
- 在连续的 `odom` 坐标系内执行相对位移、相对转向及返回起点。
- 雷达可以仅服务于 Collision Monitor，不参与定位；这仍属于不依赖雷达定位的轮速惯导。
- 若完全关闭雷达，则初期只能在空旷受控区域运行，不具备可靠主动避障能力。

模式 B 的工程名称应使用“轮速—惯性辅助航位推算”或“轮速—IMU 组合惯导”。纯 IMU 双积分只能作为后续实验，不作为第一版可靠导航方案。

## 2. 当前可靠基线

### 2.1 固件与底盘

- 已验证固件应用分区：`0x10000`。
- 固件大小：`317888 bytes`。
- 固件 SHA-256：

  ```text
  4352c4cc2152ab6a0412cfaa47dba0c9e9f9f6248cbbb3d510c99db44945c6e6
  ```

- `CHASSIS_LOW_SPEED_TARGET=0.07f`。
- 当前 NVS：

  ```text
  start-pos/neg:       110 / 110 / 110
  run-pos/neg:          83 / 83 / 83
  ff-pos/neg:          2.0 / 2.0 / 2.0
  normal PID:          900 / 80 / 0
  rotation PID:       1500 / 800 / 0
  ```

- 编码器方向、电机方向、三轮运动学、轮径、轮距已验证，不能因单次平移异常随意修改。
- `wheel_odom_angular_scale` 保持 `1.0`。
- 双向旋转已经通过核心验收，暂时冻结 rotation PID。

### 2.2 EKF

当前 [mof_ekf.yaml](config/mof_ekf.yaml) 采用保守融合：

- `/wheel/odom`：仅融合 `vx/vy`。
- `/imu`：仅融合 `gyro_z`。
- 不融合 Mahony 绝对 yaw。
- 不融合 IMU 线加速度。
- EKF 独占 `/odom` 和 `odom -> base_footprint`。
- 静止 15 秒实测 EKF yaw 漂移约 `+0.217°`，未见跳变。

轮式里程计 yaw 通常比 IMU 小约 10%～14%，但受全向轮地面打滑影响，当前不做固定比例硬校准。

### 2.3 最近一次掉线状态

最近一次执行的是只发布零速度的 `--zero-only`，随后因电池耗尽造成树莓派网络离线。

掉线前最后一次成功读取：

```text
target   = 0 / 0 / 0
measured = 0 / 0 / 0
PWM      = 0 / 0 / 0
```

真实 `--expect-blocked` 非零拦截请求尚未执行。掉线前状态不能代替重新上电后的安全检查，因此恢复后必须从 Gate 0 开始，不能从超时命令继续。

## 3. 唯一允许的速度安全链

```text
/cmd_vel_nav
    ↓
velocity_smoother
    ↓
/cmd_vel_smoothed
    ↓
collision_monitor
    ↓
/cmd_vel
    ↓
esp32_cmd_vel_bridge
    ↓
ESP32
```

必须始终满足：

- bridge 只消费 Collision Monitor 的最终输出 `/cmd_vel`。
- 手动测试、相对导航控制器和 Nav2 都从 `/cmd_vel_nav` 进入。
- Collision Monitor 是最后一个速度处理节点。
- 不允许任何测试工具直接绕过安全链向 `/cmd_vel` 或 `/cmd_vel_smoothed` 发布运动指令。
- 同一时间只允许一个 `/cmd_vel_nav` 运动发布者。
- 不允许同时启动 `safe_velocity.launch.py` 和会再次启动 smoother/monitor 的完整 Nav2 bringup。
- 不得单独杀掉 smoother 或 monitor；停止和恢复必须针对完整 launch 进程组。

相关文件：

- [safe_velocity.launch.py](launch/safe_velocity.launch.py)
- [mof_nav2_params.yaml](config/mof_nav2_params.yaml)
- [esp32_cmd_vel_bridge.py](mof_esp32_bridge/esp32_cmd_vel_bridge.py)
- [ros_safe_roundtrip_test.py](../../../tools/ros_safe_roundtrip_test.py)

官方参考：

- [Nav2 Velocity Smoother](https://docs.nav2.org/configuration/packages/configuring-velocity-smoother.html)
- [Nav2 Collision Monitor](https://docs.nav2.org/tutorials/docs/using_collision_monitor.html)

## 4. 总执行主线

```text
P0  测试工具补强
 ↓
G0  掉电恢复与全零确认
 ↓
G1  真实障碍拦截
 ↓
G2  八方向短程折返
 ↓
G3  平移参数收口
 ↓
G4  EKF + SLAM 建图
 ↓
G5  EKF + SLAM/Nav2 往返导航
 ↓
冻结模式 A 基线
 ↓
G6  轮速 + IMU 相对导航与返航
 ↓
G7  设备时间戳、标定、ZUPT、ESKF
```

上一 Gate 未通过时，不得跳到下一 Gate。

## 5. P0：非零运动前补强测试工具

电池充电期间可以只修改电脑本地脚本、配置和文档，并完成静态检查；用户明确确认“电池充满”前，不连接树莓派、不发串口、不运动。

### 5.0 双端对账与唯一真源

执行任何 P0 修改或部署前，先完整执行 [SYNC_PROTOCOL.md](SYNC_PROTOCOL.md)：

- 电脑工作区是唯一持久源码真源。
- 树莓派只作为部署镜像和运行环境，不直接持续编辑源码。
- 先发现树莓派实际源码路径和安装前缀，再生成双端逐文件 SHA-256 清单。
- 远端独有修改必须先备份、复制回电脑并合并，禁止直接覆盖本地 WIP。
- 所有持久修改只在电脑完成；本地检查通过后按文件白名单部署。
- 白名单文件双端 SHA-256 全部一致且远端构建成功，才允许进入 Gate 0。

当前电脑 `tools/ros_safe_roundtrip_test.py` 是较新的 P0 部分实现；远端旧脚本不得覆盖电脑版本。

### 5.1 完整拓扑检查

当前安全脚本不能只检查发布者，还必须检查订阅者：

- `/cmd_vel_nav`：测试时仅 `mof_safe_roundtrip_test` 发布，只有 `velocity_smoother` 订阅。
- `/cmd_vel_smoothed`：仅 `velocity_smoother` 发布，只有 `collision_monitor` 订阅。
- `/cmd_vel`：仅 `collision_monitor` 发布，只有 bridge 订阅。
- `velocity_smoother` 和 `collision_monitor` lifecycle 都必须为 `active`。

### 5.2 拦截测试不得把“没有输出”当成“零输出”

发出非零上游请求后：

- 必须在限定时间内收到新的 `/cmd_vel` 样本。
- 必须收到若干个最终零速样本。
- 如果没有新的最终消息，结论只能是“链路无输出/验收失败”，不能判定为“成功拦截”。

### 5.3 普通折返必须证明命令确实到达并产生运动

每段普通运动至少确认：

- `/cmd_vel` 出现预期的非零速度。
- ESP32 target 出现预期非零轮速。
- 编码器或 EKF 产生最小有效位移。
- 若命令未到达、机器人完全未动或只发生噪声级变化，应立即发零并判失败，不得自动执行反向段。

### 5.4 Collision Marker 过滤

MarkerArray 只统计：

- `action=ADD`；
- `type=POINTS`；
- `header.frame_id=base_footprint`。

若 marker 不在 `base_footprint`，必须先进行 TF 转换，不能直接用点坐标与 StopZone 边界比较。

### 5.5 退出与失联保护

- 捕获 `SIGINT`、`SIGTERM` 和 `SIGHUP`，统一进入连续发零流程。
- 正常、异常和 Ctrl+C 退出后连续发零至少 1.5 秒。
- `SIGKILL` 和直接掉电无法捕获，因此仍依赖 bridge 与 ESP32 的双层 0.5 秒超时。
- 调试阶段将 `velocity_smoother.velocity_timeout` 从 `1.0 s` 暂时收紧至约 `0.3 s`。
- 外层超时管理优先发送 `SIGINT` 或 `SIGTERM`，不要直接强杀。

### 5.6 文档与版本留痕

- 修正旧 README、WASD 和旧测试工具中指向 `/cmd_vel_smoothed` 的默认入口。
- `DEBUG_ARCHIVE.md`、`tools/chassis_tuner.py`、`tools/ros_rotation_test.py`、`tools/ros_safe_roundtrip_test.py` 当前受根目录 `.gitignore` 影响，应添加明确例外并纳入版本留痕。
- 每次修改记录修改前后值、理由、静态检查、部署哈希和回滚方式。
- 在 P0 阶段不修改 rotation PID、运动学、编码器方向、轮距、轮径、NVS 或固件。

## 6. Gate 0：掉电恢复

电池充满并重新上电后执行。

### 6.1 上电静置

- 机器人保持静止至少 5 秒，让 IMU 完成启动零偏标定。
- 确认电池已充足；如系统支持，检查低压、brownout、USB 重连和异常重启记录。
- 网络稳定后再进行 ROS 检查。

### 6.2 远端状态

- 检查 uptime，判断树莓派是否发生过重启。
- 检查并清理残留测试脚本，但不能误杀无关进程。
- 确认没有旧 ROS launch 进程组。
- `/dev/ttyUSB0` 只能由一个 bridge 占用。
- 只启动完整 `safe_velocity.launch.py`。
- 不允许单独停止 smoother 或 monitor。

### 6.3 ROS 与底盘全零门槛

- smoother、monitor lifecycle 均为 `active`。
- 三段速度话题的发布者、订阅者身份正确且唯一。
- `/wheel/odom`、`/odom`、`/imu`、`/scan`、`/chassis/debug` 全部新鲜。
- EKF diagnostics 正常。
- target、measured、PWM 全零。

任意一项失败都禁止发送非零速度。

## 7. Gate 1：真实 Collision Monitor 拦截

### 7.1 障碍板 A/B 检查

1. 移走障碍板：StopZone 内点数应稳定 `<4`。
2. 放入真实障碍板：StopZone 内点数应稳定 `>=4`。
3. marker 坐标系和类型必须符合 P0 过滤规则。
4. 如果移走障碍后仍有至少 4 个近原点点，可能存在机器人本体反射或坐标系错误，禁止运动。

### 7.2 非零拦截请求

使用上游请求：

```text
vx=+0.05 m/s
vy=0
w=0
duration=1.0 s
```

通过条件必须同时成立：

```text
/cmd_vel_nav          非零
/cmd_vel_smoothed     出现非零请求
/cmd_vel              收到新的消息且始终为零
ESP32 target          全零
ESP32 PWM             全零
EKF 位移              ≤ 0.005 m
机器人实际            不动
```

障碍物在测试中消失、任一最终速度非零、target/PWM 非零或机器人移动时立即发零并停止。

### 7.3 清障恢复

- 移除障碍板。
- 确认 StopZone 点数重新 `<4`。
- 再执行一次 `--zero-only`。
- 所有状态保持全零后才允许进入 Gate 2。

## 8. Gate 2：八方向短程折返

### 8.1 现场条件

- 机器人周围每个方向至少留出 0.5 m 空间。
- 地面标记实际起点。
- 用户站在电源开关附近并观察真实方向。
- 每次只运行一组，结束后分析，禁止批量连续运行。
- 所有指令从 `/cmd_vel_nav` 进入完整安全链。

### 8.2 Tier 1：首次安全动作

单程限制：

```text
合速度 ≤ 0.10 m/s
距离 ≤ 0.04 m
时间 ≤ 0.8 s
距离和时间先到者触发停止
```

每组流程：

```text
起点全零
→ 出程
→ 连续零速并停稳
→ 使用相反速度向量返回
→ 连续零速并停稳
→ 读取最终遥测
```

测试顺序：

| 顺序 | 方向 | `vx` m/s | `vy` m/s |
|---:|---|---:|---:|
| 1 | +X | +0.1000 | 0 |
| 2 | -X | -0.1000 | 0 |
| 3 | +Y | 0 | +0.1000 |
| 4 | -Y | 0 | -0.1000 |
| 5 | +45° | +0.0707 | +0.0707 |
| 6 | +135° | -0.0707 | +0.0707 |
| 7 | +225° | -0.0707 | -0.0707 |
| 8 | +315° | +0.0707 | -0.0707 |

测试正负起始方向是为了识别静摩擦和正反 PWM 的不对称，不能因为返程已经经过相反方向就省略对应出程测试。

### 8.3 Tier 2 与 Tier 3

Tier 1 全部通过后：

```text
Tier 2：单程 0.08 m 或 1.2 s
Tier 3：单程 0.15 m 或 1.5 s，每方向重复 3 组
```

初步验收目标：

- 实际物理方向正确。
- 单程偏航不超过约 5°。
- 横向异常偏移不超过约 0.02 m。
- 无单轮持续停转。
- 无持续 PWM 饱和。
- Tier 3 返回位置残差约不超过 0.03 m。
- 返回 yaw 残差约不超过 3°。
- 三次重复结果具有一致性。

以上数值是首轮工程门槛，后续根据真实场地测量和重复统计调整，不能把 EKF 位移当作唯一真值。

### 8.4 立即停止条件

任一条件发生即连续发零，不执行下一组，也不自动进行第三段补偿：

- lifecycle 不再 active。
- 话题发布或订阅拓扑发生变化。
- 任何必需遥测超过 0.6 秒未更新。
- SSH/网络掉线且机器人仍在运动。
- 命令绕过 Collision Monitor。
- 实际方向与请求方向相反。
- 第一轮偏航超过 5°。
- 横向异常偏移超过 0.02 m。
- 有效目标轮连续 0.3 秒测量速度 `<0.005 m/s`。
- PWM 连续 0.3 秒接近饱和。
- 实测轮速异常超过约 0.20～0.25 m/s。
- 达到当前单程的时间或距离上限。
- 机器人物理上离开起点 0.15 m 安全包络。
- 雷达触发障碍拦截、树莓派重启、电池明显压降或串口异常。

## 9. Gate 3：平移问题的判定与调参

### 9.1 低速平移分支

当前 `0.10 m/s` 平移命令会进入非纯旋转低速控制分支：

- `CHASSIS_STARTUP_MIN_TARGET=0.15`。
- 小于该门槛的非纯旋转目标不会强制应用 start/run PWM 下限。
- 混合低速目标小于 `0.12 m/s` 时使用固定 mixed PID `600/100`，不是 NVS normal PID。

典型轮速：

| 方向 | 三轮目标速度 m/s | 风险 |
|---|---|---|
| +X | `0, +0.0866, -0.0866` | 两个有效轮通常较容易启动 |
| +Y | `-0.10, +0.05, +0.05` | 两个 `0.05` 轮可能受静摩擦停转 |
| 对角 | 可能包含约 `0.026` | 小分量轮最容易停转 |

因此第一次运动必须先测试 +X，再逐步进入 Y 和对角方向。

### 9.2 调参原则

- 若 Y 或对角小目标轮不转，不先怀疑编码器方向、电机方向或运动学。
- 至少重复三次并比较电池状态、target、measured、PWM 和编码器。
- 区分静摩擦、mixed PID、前馈、地面打滑和供电压降。
- 一次只修改一类或一个参数。
- 每次记录修改前后值、调整依据、对照测试和回滚方法。
- 优先使用现有在线/NVS接口；只有确认编译期低速逻辑必须变化时才重新烧录。
- 不修改已经通过的 rotation PID。
- 达到稳定启动、无停轮、结果可重复即可，不追求没有外部真值支撑的“完美参数”。

## 10. Gate 4：EKF + SLAM 建图

Gate 2、Gate 3 通过后才进入。

### 10.1 TF 所有权

在线建图时必须为：

```text
slam_toolbox:  map -> odom
EKF:           odom -> base_footprint
静态 TF:       base_footprint -> base_link -> imu_link
               base_footprint -> laser
```

- 不允许其他节点发布 `odom -> base_footprint`。
- mapping 模式不启动 AMCL。
- `slam_toolbox` 和 AMCL 不得同时发布 `map -> odom`。

### 10.2 建图轨迹

采用逐步扩大的闭环轨迹：

1. 短十字折返。
2. 小正方形闭环。
3. 带一次正反旋转的闭环。
4. 空间允许后再扩大范围。

同时录制：

```text
/cmd_vel_nav
/cmd_vel_smoothed
/cmd_vel
/chassis/debug
/imu
/wheel/odom
/odom
/scan
/tf
/tf_static
/diagnostics
/collision_monitor_state
/map
```

检查：

- 返回后激光墙面是否重新重合。
- 地图是否出现重影、跳变、放射状毛刺。
- `map -> odom` 是否连续。
- EKF odom 是否存在明显横移或航向异常。
- 闭环前后物理位置和地图位置是否一致。

通过后保存地图、rosbag、配置哈希及运行命令。

## 11. Gate 5：EKF + SLAM/Nav2 往返导航

### 11.1 先整理唯一启动入口

`safe_velocity.launch.py` 已经启动 bridge、EKF、smoother、monitor；Jazzy 标准 Nav2 bringup 也启动
smoother、monitor 和 lifecycle manager，二者不能直接叠加。保存地图导航统一使用
`mof_nav_with_ekf.launch.py`，它组合 `esp32_ekf.launch.py` 与一套 Nav2 bringup，不包含
`safe_velocity.launch.py`。

在自主导航前应明确拆分：

```text
mof_nav_with_ekf.launch.py
  esp32_ekf.launch.py
    唯一 bridge（订阅最终 /cmd_vel）+ 静态 TF + 唯一 EKF
  Nav2 bringup（保存地图 + AMCL）
    controller -> /cmd_vel_nav
    唯一 velocity_smoother -> /cmd_vel_smoothed
    唯一 collision_monitor -> /cmd_vel
```

在线 SLAM 导航时不启动 AMCL/map_server；使用保存地图导航时停止 mapping，改由 AMCL 发布 `map -> odom`。

### 11.2 首次 Nav2 启动与往返

初始速度限制：

```text
最大线速度：0.15 m/s
最低有效平移速度：0.10 m/s
最大角速度：0.60～0.70 rad/s
角加速度：  0.80～1.00 rad/s²
```

执行：

1. 记录起始位姿 A。
2. 不先执行 0.08 m/s 手动测试；发送机器人 +X 正前方空旷、yaw 不变、约 0.25～0.30 m 的目标 B。
3. 到达 B 后确认停止和定位状态。
4. 使用保存的起始 map 位姿发送 NavigateToPose 目标 A 返回，不直接发布负速度。
5. 取消导航目标并确认最终 `/cmd_vel`、target、measured、PWM 全零。
6. 本轮不再重复真实障碍 blocked；此前物理拦截已通过，旧脚本的“必须收到新 final 零速”判据
   与 `stop_pub_timeout` 语义不兼容，不得继续阻塞 Nav2 主线。

交付：

- 去程和返程目标误差。
- 实际路径与最大速度。
- Collision Monitor 状态。
- EKF、SLAM 位姿变化。
- 最终返回残差。
- TF 和速度话题唯一所有权证据。

完成 Gate 5 后冻结模式 A 基线，再开启独立的惯性导航任务。

## 12. Gate 6：轮速—IMU 相对导航与返航

建议在新任务中实现，避免与模式 A 的 SLAM/Nav2 联调混杂。

### 12.1 运行节点

保留：

```text
bridge
/wheel/odom
/imu
EKF /odom
relative_navigation_node
唯一 safety pipeline
```

关闭：

```text
slam_toolbox
AMCL
map_server
Nav2 全局规划和全局 costmap
```

### 12.2 相对导航接口

建议实现 Action `/navigate_relative`：

```text
输入：dx、dy、dyaw、速度上限、总超时
反馈：剩余距离、航向误差、当前阶段、是否受安全链拦截
结果：成功/失败原因、最终位置残差、最终航向残差
输出：/cmd_vel_nav
```

控制器在任务开始时保存当前 `/odom` 位姿作为局部起点，在连续的 odom 坐标系计算目标和返航。不要为了回到起点而重置 EKF 或制造 TF 跳变。

### 12.3 验收顺序

1. 在定位层不使用 `/scan` 的情况下确认 `/wheel/odom`、`/imu`、`/odom` 持续工作。
2. 四方向 0.15 m 折返。
3. 对角方向 0.15 m 折返。
4. 0.3～0.5 m 的 A -> B -> A。
5. 小正方形闭环。
6. 先旋转、再平移、最后返回起点。
7. 多次重复，统计不同距离和时间下的 P50/P95 返回误差。

最终应依据实测统计定义“无外部位置修正的可靠距离和可靠运行时间”，不能宣称无限范围惯性导航。

## 13. Gate 7：高级惯导与 ESKF 前置条件

当前 bridge 已解析 ESP32 `timestamp_us` 和 sequence，但发布 `/imu` 时仍使用树莓派接收时刻。当前仅融合 `gyro_z` 尚可继续调试；融合加速度或实现 ESKF 前必须完成：

1. MCU `micros()` 回绕展开。
2. MCU 设备时间到 ROS 时间的映射。
3. 串口延迟和抖动统计。
4. 轮速遥测增加设备采样时间和 sequence。
5. IMU 六面静置标定。
6. IMU X/Y/Z 轴与机器人坐标系外参验证。
7. 加速度零偏、比例因子和温漂分析。
8. 静止检测与 ZUPT。
9. rosbag 离线回放和误差统计。
10. 最后再评估 ESKF 和线加速度融合。

在这些条件满足前：

- 不融合 Mahony 绝对 yaw。
- 不将 IMU 线加速度直接打开到现有 EKF。
- 不做纯 IMU 位置双积分作为正式导航输出。
- 不因 wheel odom yaw 偏小 10%～14% 就修改固定轮距或比例。

## 14. 统一留痕要求

每个 Gate 必须在 `DEBUG_ARCHIVE.md` 追加：

1. 时间和电源状态。
2. 修改前运行状态。
3. 完整命令。
4. 修改文件和关键 diff。
5. 本地与远端 SHA-256。
6. ROS 节点、话题、TF 和串口所有权。
7. 指令、target、measured、PWM、编码器、IMU、wheel odom、EKF 数据。
8. 用户看到的真实物理运动。
9. 通过/失败结论及依据。
10. 停止确认和回滚方法。

不得用“看起来正常”“大概能跑”代替定量结果。

## 15. 可直接复制给底盘解算与 EKF 对话的续接提示词

```text
继续执行 MOF-Robot 底盘解算与 EKF 主线。

先完整阅读：
- ros2_ws/src/mof_esp32_bridge/NAVIGATION_MAINLINE.md
- ros2_ws/src/mof_esp32_bridge/SYNC_PROTOCOL.md
- DEBUG_ARCHIVE.md 最新部分
- 当前 git status 和相关 diff

电脑工作区是唯一持久源码真源，树莓派只是部署镜像和运行环境。禁止直接在树莓派项目源码中持续编辑。当前树莓派已重新启动，但任何非零测试前必须先完成双端对账。

先执行 Sync Gate：

1. 不发非零速度、不写 NVS、不烧录固件。
2. 只读发现树莓派实际源码包路径、install 前缀和当前 ROS 使用来源，不假设一定是 ros2_ws 或 ros2_ws。
3. 分别生成电脑和树莓派关键源码的相对路径、大小、时间和 SHA-256 清单。
4. 若发现远端独有修改，先复制到电脑时间戳恢复目录并逐文件 diff，再把要保留的内容合并进电脑；禁止整目录互相覆盖，禁止 rsync --delete。
5. 当前电脑 tools/ros_safe_roundtrip_test.py 是 713 行 P0 部分实现，基线 SHA-256 为 1f6fcede6d33d499069e9a35602c416eeeefd6198787fe867f0f15a59a23d23a；树莓派最后已知 /tmp 版本 65a8bb6f... 是旧版，不能回灌覆盖电脑。
6. 完成对账后，所有持久修改只在电脑进行。

先完成 P0：

1. 补强 tools/ros_safe_roundtrip_test.py：
   - 同时检查 /cmd_vel_nav、/cmd_vel_smoothed、/cmd_vel 的发布者和订阅者身份；
   - 检查 velocity_smoother 和 collision_monitor lifecycle 均为 active；
   - blocked 测试发出非零上游请求后，必须收到新的最终 /cmd_vel 零速消息，空消息不能判成功；
   - 普通折返必须看到非零 final、非零 ESP32 target 和最小有效位移，完全不动不能判成功；
   - MarkerArray 只统计 POINTS/ADD，验证 frame_id=base_footprint；
   - 捕获 SIGINT、SIGTERM、SIGHUP，统一连续发送零速；
   - 出程未产生有效运动、方向错误或任何异常时不得执行返程或第三段残差补偿。
   - 不要因为常量、函数或client已经写入就判定功能完成；必须确认 lifecycle 查询、5°偏航、0.02m横移、控制消息新鲜度、编码器最小变化量和停止信号都实际进入运行路径。
   - 当前部分实现中 wait_ready/run_leg/run_blocked_test/main 仍保留旧流程，必须完成状态机接线后再验收。
2. 将 velocity_smoother 的 velocity_timeout 暂时收紧至约 0.3 秒。
3. 修正文档和旧工具中绕过安全链的 /cmd_vel_smoothed 默认入口。
4. 确保 DEBUG_ARCHIVE.md 与三份 tools 脚本能够纳入版本留痕。
5. 将所有修改、哈希、静态检查和回滚方式追加到 DEBUG_ARCHIVE.md。
6. 不修改 rotation PID、编码器方向、运动学、轮距、轮径、wheel_odom_angular_scale、NVS 或固件。

P0 本地检查全部通过后：

1. 在树莓派创建时间戳备份。
2. 只部署本轮明确修改的白名单文件，不整目录覆盖。
3. 暂存文件与电脑 SHA-256 一致后再覆盖远端 src。
4. 覆盖后再次逐文件比较双端 SHA-256。
5. 远端 colcon 构建成功，并确认 ROS 使用刚构建的 install 前缀。
6. /tmp 测试脚本 SHA-256 必须等于电脑 tools/ros_safe_roundtrip_test.py。
7. 任一文件 MISMATCH 时停止，不得进入运动测试。

用户确认电池充满后，严格按 Gate 执行：

Gate 0：掉电恢复
- 上电后保持机器人静止至少 5 秒；
- 检查 uptime、低压/USB 异常、残留进程、串口唯一占用；
- 整体启动 safe_velocity.launch.py，禁止单独 kill smoother 或 monitor；
- lifecycle active、完整发布/订阅链唯一、所有遥测新鲜、target/measured/PWM 全零。

Gate 1：真实 Collision Monitor 拦截
- 移除障碍时 StopZone 点数稳定 <4；
- 放入障碍时稳定 >=4；
- 执行 vx=+0.05、1 秒的 --expect-blocked；
- 必须看到 smoothed 非零、最终 /cmd_vel 有新的零速消息、target/PWM 为零、EKF 位移 <=5 mm；
- 任一证据缺失都算失败。

Gate 2：全向短程折返
- 每次只跑一组；
- 第一轮单程 0.04 m 或 0.8 秒，合速度 <=0.10 m/s；
- 顺序为 +X、-X、+Y、-Y、四个对角方向；
- 每组均执行出程、停稳、相反向量返回、停稳；
- 用户必须观察真实物理方向，机器人周围至少留出 0.5 m；
- 偏航 >5°、横移 >0.02 m、单轮停转、PWM 持续饱和、遥测过期、拓扑变化或掉线时立即发零并停止；
- +Y 或对角小目标轮不转时，优先调查低速平移 mixed PID/启动补偿，不改运动学和方向逻辑。

Tier 1 全部通过后再做 0.08 m/1.2 秒，最后做 0.15 m/1.5 秒并每方向重复三次。

随后继续按 NAVIGATION_MAINLINE.md 的 Gate 3～Gate 5 完成平移参数收口、EKF+SLAM 建图和 EKF+SLAM/Nav2 的 A→B→A。

每个 Gate 完成后先输出数据表、用户物理观察、结论、停止状态和回滚方式，再进入下一 Gate。禁止跳过 Gate，禁止批量盲跑。
```

## 16. 任务拆分建议

- 当前“底盘解算与 EKF”对话负责 P0～Gate 5。
- Gate 5 的 EKF+SLAM/Nav2 `A -> B -> A` 通过并冻结基线后，再新开：

  ```text
  MOF-Robot 轮速惯导、相对返航、设备时间戳与 ESKF
  ```

- subagent 可以进行只读配置、TF、话题、日志和算法审查。
- 所有机器人运动、SSH、串口写入和文件部署必须由一个主 agent 统一控制，禁止多个 agent 同时控制底盘。

## 17. 本文档更新记录

### 2026-08-13：初版

- 固化电池耗尽掉线后的恢复起点。
- 固化速度安全链及测试脚本 P0 修正项。
- 固化八方向分级折返方案。
- 明确 EKF+SLAM/Nav2 与轮速惯导两种最终模式。
- 明确高级惯导进入 ESKF 前的时间戳、外参、标定和 ZUPT 前置条件。

### 2026-08-13：增加双端同步门槛

- 电脑工作区确定为唯一持久源码真源。
- 新增 [SYNC_PROTOCOL.md](SYNC_PROTOCOL.md)。
- P0 前增加电脑—树莓派逐文件对账、部署白名单和 SHA-256 一致性 Gate。
- 记录当前电脑 P0 脚本为部分实现，禁止远端旧脚本覆盖或直接用于非零运动。
