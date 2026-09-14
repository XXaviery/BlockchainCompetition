# mof_esp32_bridge

ROS2 上位机串口桥接节点，负责同一个 ESP32 串口上的三件事：

- 订阅 Collision Monitor 的最终输出 `/cmd_vel`，发送底盘速度帧到 ESP32。
- 解析 ESP32 回传的 MS200 雷达帧，发布 `/scan`。
- 解析 ESP32 回传的底盘遥测，发布 `/odom` 和 `odom -> base_footprint` TF。

上位机不再打印底盘编码器、PWM、目标速度等遥测日志，避免运行 Nav2/SLAM 时刷屏。

## 源码根与部署参数

本包在电脑源码树中的相对路径是 `catkin_ws/src/mof_esp32_bridge/`，上一级唯一源码根是 `code/code/`。源码文档不依赖电脑上的固定盘符或用户目录。

目标 ROS2 工作空间、串口、地图与 RViz 配置属于部署参数，可在树莓派终端设置：

```bash
export MOF_ROS_WS="${MOF_ROS_WS:-$HOME/ros2_ws}"
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_MAP_PATH="${MOF_MAP_PATH:-$HOME/maps/mof_room_v2.yaml}"
export MOF_RVIZ_CONFIG="${MOF_RVIZ_CONFIG:-$HOME/rviz2/rviz.rviz}"
export MOF_VALIDATION_SCRIPT="${MOF_VALIDATION_SCRIPT:-/path/to/mof_ros_safe_roundtrip_test.py}"
```

文档后文出现的 `/dev/...`、`$HOME/...` 和实际 ROS 工作空间都表示设备侧运行参数，不是源码路径。

## ESP32 速度帧

```text
AA 66 0C + vx(float32 LE) + vy(float32 LE) + w(float32 LE) + checksum
```

`checksum = payload 12 字节累加和 & 0xFF`。

对应关系：

```text
Twist.linear.x  -> vx，前进为正，m/s
Twist.linear.y  -> vy，左移为正，m/s
Twist.angular.z -> w，逆时针为正，rad/s
```

## 树莓派构建

```bash
cd "$MOF_ROS_WS"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

缺串口库时：

```bash
sudo apt install python3-serial
sudo apt install ros-jazzy-robot-localization
```

没有串口权限时：

```bash
sudo usermod -a -G dialout $USER
```

然后重新登录树莓派。

## 启动方式

### 稳定ESP32串口名

MOF ESP32当前通过Silicon Labs CP2102连接，实机识别属性为VID:PID `10c4:ea60`、序列号
`0001`、树莓派USB物理路径`platform-xhci-hcd.1-usb-0:2:1.0`。由于`0001`是常见默认序列号，
项目规则同时匹配物理端口，生成稳定链接：

```text
/dev/mof_esp32 -> /dev/ttyUSB0 或 /dev/ttyUSB1
```

从电脑真源部署`udev/99-mof-esp32.rules`后，在树莓派安装：

```bash
sudo install -m 0644 \
  "$MOF_ROS_WS/src/mof_esp32_bridge/udev/99-mof-esp32.rules" \
  /etc/udev/rules.d/99-mof-esp32.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=add
udevadm settle
readlink -f /dev/mof_esp32
```

所有launch和bridge默认使用`/dev/mof_esp32`。紧急诊断仍可显式指定实际设备，例如
`port:=/dev/ttyUSB1`，但不得把临时编号重新写死到源码。若更换树莓派USB插口，先用
`udevadm info --query=property --name=<设备>`重新确认`ID_PATH`并在电脑真源更新规则。

回滚规则：保持机器人全零并整体停止launch，执行
`sudo rm /etc/udev/rules.d/99-mof-esp32.rules`，reload/trigger后确认`/dev/mof_esp32`消失；
再通过显式`port:=/dev/ttyUSBx`启动。源码回滚必须先在电脑完成并按白名单重新部署。

裸 `esp32_bridge.launch.py` 只用于传感器只读诊断或维护，不能作为运动入口。
需要发布任何运动命令时，必须整体启动唯一安全链：

```bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:=/dev/mof_esp32 baud:=921600
```

完整链路为 `/cmd_vel_nav -> velocity_smoother -> /cmd_vel_smoothed ->
collision_monitor -> /cmd_vel -> bridge`。

只读维护入口：

```bash
ros2 launch mof_esp32_bridge esp32_bridge.launch.py port:=/dev/mof_esp32 baud:=921600
```

该启动方式现在还会发布原始 IMU：

- 话题：`/imu`
- 消息：`sensor_msgs/Imu`
- 坐标系：`imu_link`
- 角速度单位：`rad/s`
- 线加速度单位：`m/s^2`
- 上电后需保持静止约 2 秒，状态有效后才发布 `/imu`
- 三轴角速度已扣除本次上电测得的零偏
- orientation 由六轴 Mahony PI 输出；roll/pitch 受重力约束
- 六轴 IMU 没有绝对 yaw 参考，因此 yaw 仍会随时间缓慢漂移

ESP32 回传的 AA88 帧为：

```text
[AA][88][40]
[status:u8][who_am_i:u8][timestamp_us:u32 LE][sequence:u16 LE]
[ax,ay,az:3*f32][gx,gy,gz:3*f32][temperature:f32]
[qx,qy,qz,qw:4*f32][bias_x,bias_y,bias_z:3*f32]
[checksum:u8]
```

`status`：bit0 传感器就绪、bit1 样本有效、bit2 零偏标定完成、
bit3 Mahony 姿态有效。`checksum` 是 64 字节 payload 的累加和。

检查 IMU：

```bash
ros2 topic hz /imu
ros2 topic echo /imu --once
ros2 run tf2_ros tf2_echo base_link imu_link
```

需要仅做轮式里程计与陀螺仪 z 轴 EKF 的只读诊断时，可使用独立入口：

```bash
ros2 launch mof_esp32_bridge esp32_ekf.launch.py port:=/dev/mof_esp32 baud:=921600
```

该入口让 bridge 发布 `/wheel/odom` 且不发布里程计 TF，由
`robot_localization` 输出融合后的 `/odom` 和唯一的
`odom -> base_footprint` TF，避免两个节点同时发布同一 TF。

如果 SLAM 地图出现大量放射状毛刺、黑色孤立点，先用更保守的雷达过滤参数启动：

```bash
ros2 launch mof_esp32_bridge esp32_bridge.launch.py \
  port:=/dev/mof_esp32 \
  baud:=921600 \
  lidar_range_max:=3.0 \
  lidar_spike_filter:=true \
  lidar_spike_filter_window:=3 \
  lidar_spike_filter_min_neighbors:=3 \
  lidar_spike_filter_max_delta:=0.20
```

裸 bridge 的只读接口需要 `TwistStamped` 兼容性时：

```bash
ros2 launch mof_esp32_bridge esp32_bridge.launch.py port:=/dev/mof_esp32 baud:=921600 message_type:=twist_stamped
```

默认发布：

- `/scan`
- `/odom`
- `odom -> base_footprint`
- `base_footprint -> base_link`
- `base_footprint -> laser`

当前实车确认的雷达安装方向：

- `laser_yaw` 默认 `1.5707963`，即雷达坐标相对 `base_footprint` 旋转 90 度。
- 校准依据：实车测试 `laser_yaw:=1.5707963` 时，实物目标在机器人 `+Y` 方向，RViz 中也落在 `+Y`，无可见角度偏差。
- MS200 扫描点方向已在 bridge 中反转为 ROS LaserScan 约定，避免 RViz 左右镜像。

## 基础检查

检查话题：

```bash
ros2 topic list
ros2 topic hz /scan
ros2 topic hz /odom
```

检查 TF：

```bash
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 run tf2_ros tf2_echo odom base_link
ros2 run tf2_ros tf2_echo base_footprint laser
```

`base_footprint -> laser` 应显示：

- translation 约为 `[0.000, 0.000, 0.120]`
- yaw 约为 `1.5708 rad`
- roll 和 pitch 约为 `0`

如果 RViz 中雷达方向突然又歪了，先完全停止旧节点并关闭 RViz2，再重启 ROS daemon 后重新打开，避免旧的 `/tf_static` 显示状态干扰判断：

```bash
ros2 daemon stop
ros2 daemon start
```

## 后续操作

`advise.md` 建议的后续工作分成实车操作和仓库内配置两部分。仓库内已提供 Nav2 参数和启动文件：

- `config/mof_nav2_params.yaml`
- `launch/nav2_bringup.launch.py`
- `launch/mof_nav_with_ekf.launch.py`

推荐在树莓派上按下面顺序做只读验证；任何运动都必须改用完整安全入口。

### 1. 启动完整安全链

```bash
cd "$MOF_ROS_WS"
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:=/dev/mof_esp32 baud:=921600
```

另开终端检查频率：

```bash
source "$MOF_ROS_WS/install/setup.bash"
ros2 topic hz /scan
ros2 topic hz /odom
```

预期：

- `/scan` 接近 50Hz。
- `/odom` 接近 20Hz。
- TF 链包含 `odom -> base_footprint -> base_link -> laser`。

### 2. 在线建图

```bash
source "$MOF_ROS_WS/install/setup.bash"
ros2 launch mof_esp32_bridge slam_mapping.launch.py
```

如果上一轮地图已经像放射状毛刺一样散开，不要在旧状态上继续走。停止 `slam_toolbox` 后重新启动，再从空地图开始建。

用遥控或 teleop 慢速走一圈，RViz2 中地图没有明显跳变后保存地图：

```bash
ros2 run nav2_map_server map_saver_cli -f ~/maps/mof_room_v2
```

### 3. 录制 rosbag

```bash
mkdir -p ~/bags
ros2 bag record -o ~/bags/mof_nav2_check /scan /odom /tf /tf_static
```

录 3 到 5 分钟正常运动数据，后续可以离线复现 `/scan`、`/odom` 和 TF 问题。

### 4. 启动 Nav2

自主导航必须先整体停止 `safe_velocity.launch.py`，再使用唯一集成入口；二者禁止叠加：

```bash
ros2 launch mof_esp32_bridge mof_nav_with_ekf.launch.py \
  map:="$MOF_MAP_PATH" use_rviz:=false
```

该入口只包含一个 bridge、一个 EKF，以及 Jazzy Nav2 bringup 所属的一套 smoother/monitor。
标准 Nav2 controller 和 behavior 输出被重映射到 `/cmd_vel_nav`，完整链为
`/cmd_vel_nav -> /cmd_vel_smoothed -> /cmd_vel -> bridge`。
`map` 是必填参数；历史无效地图不得用于定位和导航。地图文件位置由部署参数 `MOF_MAP_PATH` 提供。

当前 Nav2 初始参数：

- `robot_radius: 0.20`
- `max_vel_x: 0.15`
- `max_vel_y: 0.15`
- `max_vel_theta: 0.70`
- `min_speed_xy: 0.10`
- `base_frame_id: base_footprint`

第一次自主导航保持 `0.15 m/s` 平移上限，不再先做 `0.08 m/s` 手动运动。确认地图、AMCL、costmap
和完整安全链后，发送机器人 `+X` 正前方 `0.25～0.30 m`、yaw 不变的 NavigateToPose；目标不得短于
`0.20 m`。如果 final/target 非零但 PWM 低于约 110 且编码器不动，先取消目标并按平移启动补偿不足处理，
不得误判为 Nav2 或编码器故障。

首次运动验收不得使用单条 `ros2 topic pub --once`，因为它不能验证完整链路、
生命周期、遥测和退出清零。按 Gate 顺序使用受控测试脚本，例如 Gate 0：

```bash
python3 "${MOF_VALIDATION_SCRIPT:?set MOF_VALIDATION_SCRIPT to the deployed validation script}" --zero-only
```

键盘遥控：

```bash
ros2 run mof_esp32_bridge wasd_teleop \
  --linear-speed 0.20 --lateral-speed 0.30 \
  --angular-speed 0.35 --publish-rate 20
```

按键映射：

- `w`：前进，`s`：后退
- `a`：左移，`d`：右移
- `q`：逆时针自转，`e`：顺时针自转
- 空格：停车

WASD 只能发布到安全链上游 `/cmd_vel_nav`，不能选择 `/cmd_vel_smoothed` 或最终 `/cmd_vel`。
默认前后速度 `0.20 m/s`、左右速度 `0.30 m/s`、角速度 `0.35 rad/s`、发布频率 `20 Hz`；
人工空场模式的前后速度只接受 `0.10～0.25 m/s`，左右速度只接受 `0.10～0.30 m/s`，角速度不超过
`0.35 rad/s`。A/D 使用独立的 `--lateral-speed`；`0.30 m/s` 横移会解算为一个 `0.30 m/s` 主轮和两个
`0.15 m/s` 小目标轮，以避开已知低速迟滞边缘。按下一个运动键后会以固定频率持续发布该命令，
空格立即停车；Ctrl+C、SIGTERM、SIGHUP和异常退出都会连续清零至少1.5秒。

### 人工空场调试模式（临时旁路避障）

仅当机器人周围空旷、有人现场监护并准备立即按空格停车时，才可整体启动：

```bash
ros2 launch mof_esp32_bridge manual_open_field.launch.py
```

启动日志必须显示：

```text
MANUAL OPEN-FIELD MODE: COLLISION MONITOR BYPASSED
```

该模式的唯一速度链为：

```text
/cmd_vel_nav -> velocity_smoother -> /cmd_vel -> bridge -> ESP32
```

它使用独立的 `mof_manual_open_field_params.yaml`，保留 `velocity_timeout=0.3s`、速度/加速度限制和
超时停车；人工上限为 X `0.25 m/s`、Y `0.35 m/s`、角速度 `0.35 rad/s`，平移加减速度为
`1.5 m/s²`。当前W/S默认0.20、A/D默认0.30；生产
`mof_nav2_params.yaml` 仍保持 `0.15 m/s`。
该模式**不启动 Collision Monitor**，
因此不可用于正式导航、无人运行或有障碍环境，也不得与 `safe_velocity.launch.py` 同时启动。
完成底盘调试后整体停止该进程组，恢复生产入口：

```bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py
```

生产入口继续使用 `/cmd_vel_nav -> smoother -> /cmd_vel_smoothed -> Collision Monitor -> /cmd_vel`，
其 StopZone 配置没有被人工调试入口删除。

## RViz 雷达方向确认

`advise.md` 中 LaserScan 角度方向需要实车确认，不要盲改。

启动 bridge 后打开 RViz2：

```bash
rviz2
```

添加 `LaserScan`，topic 选 `/scan`。让机器人正面对一面墙，观察点云是否出现在机器人正前方：

- 如果墙在 RViz 中也位于正前方，且左右物体也对应，保持当前配置。
- 如果前后反了，优先检查 `laser_yaw`。
- 如果前后正确但左右镜像，检查 bridge 中的 `LIDAR_REVERSE_SCAN_DIRECTION`。

## 下位机遮挡角

当前雷达位置的机械遮挡区已实测并写入 `core/lib/lidar/lidar.h`：

```cpp
#define LIDAR_BLIND_SECTOR_MEASURE_MODE 0
#define LIDAR_IGNORE_SECTOR_COUNT 1
#define LIDAR_DEFAULT_IGNORE_SECTORS {{150, 260}}
#define LIDAR_IGNORE_MAX_DISTANCE_MM 80
```

含义：

- 只处理 MS200 原始角度 `150°~260°` 的机械遮挡区。
- 只有该扇区内 `0 < 距离 <= 80mm` 的点会被发给上位机前置为无效距离。
- 该扇区内的远处真实障碍不会被整段删除。

## 常用参数

- `port`：ESP32 稳定串口链接，默认 `/dev/mof_esp32`
- `baud`：默认 `921600`
- `topic`：bridge 的最终安全速度输入，默认 `/cmd_vel`
- `message_type`：`twist` 或 `twist_stamped`
- `timeout_s`：速度超时自动停车，默认 `0.5`
- `max_vx`：默认 `0.6`
- `max_vy`：默认 `0.6`
- `max_w`：默认 `3.0`
- `discard_rx`：默认 `true`，保持串口接收侧被消费
- `publish_scan`：默认 `true`
- `scan_topic`：默认 `/scan`
- `scan_frame_id`：默认 `laser`
- `lidar_range_min`：默认 `0.02`
- `lidar_range_max`：默认 `12.0`，建图毛刺多时建议先试 `3.0`
- `lidar_spike_filter`：默认 `true`，过滤孤立雷达点
- `lidar_spike_filter_window`：默认 `2`
- `lidar_spike_filter_min_neighbors`：默认 `2`，毛刺多时可设为 `3`
- `lidar_spike_filter_max_delta`：默认 `0.45`，毛刺多时可降到 `0.20` 或 `0.30`；真实墙面被吃掉时调回 `0.45` 或 `0.60`
- `publish_odom`：默认 `true`
- `odom_topic`：默认 `/odom`
- `odom_frame_id`：默认 `odom`
- `base_frame_id`：默认 `base_footprint`
- `publish_odom_tf`：默认 `true`
- `laser_x / laser_y / laser_z / laser_roll / laser_pitch / laser_yaw`：雷达相对 `base_footprint` 的静态 TF，`laser_yaw` 默认 `1.5707963`

## 直接串口速度帧（禁止用于当前实车流程）

`send_velocity_frame` 会绕过 ROS 安全链。当前 P0～Gate 2 禁止使用；仅在断开电机或
专用台架、具有独立急停和明确授权时才能用于协议维护。

## 底盘调试遥测

Bridge 将 ESP32 的 `AA 77` 遥测发布到 `/chassis/debug`，消息类型为
`std_msgs/msg/Float64MultiArray`。15 个元素依次为：三个轮子的目标速度
（m/s）、实测速度（m/s）、PWM、累计编码器计数、累计轮距（m）。
