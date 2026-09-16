# MOF-Robot

MOF-Robot 包含两部分代码：

- `firmware/`：ESP32 下位机固件，负责底盘运动控制、MS200 雷达解析和串口协议。
- `ros2_ws/src/mof_esp32_bridge/`：树莓派 ROS2 上位机包，负责串口桥接、发布 `/scan` 和 `/odom`，并提供 WASD、SLAM 建图和 Nav2 导航启动文件。
- `web/`：本地 Web 控制台。

本目录 `code/robot/` 是硬件、ROS2 和 Web 的唯一源码根目录。以下源码引用均相对于本目录；内层 `code/` 是重复仓库，仅保留在原工作区，不进入竞赛提交包。

## 源码路径与部署参数

源码路径固定使用相对形式：`firmware/`、`ros2_ws/src/mof_esp32_bridge/`、`web/`、`tools/`。目标机工作空间、串口、地图、RViz 配置和 Web 端口由部署环境提供；它们不代表本源码树中的目录。例如：

```bash
export MOF_ROS_WS="${MOF_ROS_WS:-$HOME/ros2_ws}"
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_MAP_PATH="${MOF_MAP_PATH:-$HOME/maps/mof_room_v2.yaml}"
export MOF_RVIZ_CONFIG="${MOF_RVIZ_CONFIG:-$HOME/rviz2/rviz.rviz}"
```

Windows 固件上传端口可用 `pio run -t upload --upload-port COM9` 显式传入。`COM8`、`COM9` 或 `/dev/ttyUSB*` 是设备参数，不是项目源文件路径。

所有运动命令必须经过完整安全链：

```text
wasd_teleop / 测试 / Nav2 -> /cmd_vel_nav
-> velocity_smoother -> /cmd_vel_smoothed
-> collision_monitor -> /cmd_vel -> esp32_cmd_vel_bridge -> ESP32
```

## 树莓派编译

从本源码根将 `ros2_ws/src/mof_esp32_bridge/` 复制到目标工作空间的 `src/`，然后在树莓派编译：

```bash
cd "$MOF_ROS_WS"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

如果缺少串口库：

```bash
sudo apt install python3-serial
```

如果没有串口权限：

```bash
sudo usermod -a -G dialout $USER
```

执行后重新登录树莓派。

## WASD 手动控制

终端 1：整体启动 bridge、EKF、velocity smoother 与 Collision Monitor。

```bash
cd "$MOF_ROS_WS"
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:="$MOF_SERIAL_PORT" baud:=921600
```

终端 2：启动键盘遥控。

```bash
source "$MOF_ROS_WS/install/setup.bash"
ros2 run mof_esp32_bridge wasd_teleop
```

按键：

- `w`：前进
- `s`：后退
- `a`：左移
- `d`：右移
- `q`：逆时针旋转
- `e`：顺时针旋转
- 空格：停车
- `Ctrl-C`：停车并退出

WASD 默认发布到上游 `/cmd_vel_nav`；bridge 只订阅 Collision Monitor 的最终输出
`/cmd_vel`。不得把 WASD 改到 `/cmd_vel_smoothed` 或 `/cmd_vel` 绕过安全链。

## SLAM 建图

终端 1：启动完整安全链。

```bash
cd "$MOF_ROS_WS"
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:="$MOF_SERIAL_PORT" baud:=921600
```

终端 2：启动 slam_toolbox 在线建图。

```bash
source "$MOF_ROS_WS/install/setup.bash"
ros2 launch mof_esp32_bridge slam_mapping.launch.py
```

终端 3：用 WASD 慢速遥控机器人走完整个区域。

```bash
source "$MOF_ROS_WS/install/setup.bash"
ros2 run mof_esp32_bridge wasd_teleop
```

建图时建议：

- 先在空旷区域确认 `/scan`、`/odom` 正常。
- 慢速移动，避免急转。
- 如果地图出现明显散开、跳变或放射状毛刺，停止 `slam_toolbox` 后重新开始建图。

保存地图：

```bash
mkdir -p ~/maps
ros2 run nav2_map_server map_saver_cli -f ~/maps/mof_room_v2
```

保存后应得到：

```text
~/maps/mof_room_v2.yaml
~/maps/mof_room_v2.pgm
```

## Nav2 自主导航

历史无效地图文件不得参考。完成 `mof_room_v2` 建图验收后，
必须整体停止 `safe_velocity.launch.py`，再使用 `mof_nav_with_ekf.launch.py`；二者禁止叠加。
该集成入口中的 Jazzy Nav2 bringup 独占唯一 smoother/monitor，并把 controller 输出重映射到
`/cmd_vel_nav`。详见 `ros2_ws/src/mof_esp32_bridge/NAVIGATION_MAINLINE.md`。

RViz 操作顺序：

1. 点击 `2D Pose Estimate`，在地图上给机器人当前位置和朝向。
2. 等 AMCL 粒子云收敛，确认激光点云和地图墙壁基本重合。
3. 点击 `2D Goal Pose`，首次只给机器人 +X 正前方 0.25～0.30 m、yaw 不变的目标。
4. 观察 RViz 是否出现路径，以及机器人是否开始移动。

常用检查命令：

```bash
ros2 topic hz /scan
ros2 topic hz /odom
ros2 topic hz /cmd_vel_nav
ros2 topic hz /cmd_vel_smoothed
ros2 topic hz /cmd_vel
ros2 topic echo /amcl_pose --no-arr
ros2 run tf2_ros tf2_echo odom base_footprint
```

当前调试配置里 `collision_monitor` 的 StopZone 动作是 `stop`，不得为跑通链路改成
`none`。任何非零测试必须依次通过同步 Gate、Gate 0 和真实障碍拦截 Gate。

---

## 底盘运动学解算

### 坐标系与轮子布局

```
           前 (+X)
              ↑
   M3(60°)    |    M2(300°)
       \      |      /
        \     |     /
         \    |    /
          [Center]
               |
            M1(180°)
               后

  +Y = 左      ω↺ = 逆时针为正
```

三个轮子以 120° 均匀分布，位置角从 +X 轴逆时针量起：

| 电机 | 位置角 α | 安装位置 |
|------|----------|----------|
| M1   | 180°     | 正后方   |
| M2   | 300°     | 右前方   |
| M3   | 60°      | 左前方   |

---

### 逆运动学（机器人速度 → 轮速）

通用公式（每个轮子沿切线方向滚动）：

$$
v_i = -\sin(\alpha_i)\,v_x + \cos(\alpha_i)\,v_y - R\,\omega
$$

代入三个轮子的位置角展开：

$$
\begin{aligned}
v_1 &= -v_y - R\,\omega \\[4pt]
v_2 &= \dfrac{\sqrt{3}}{2}\,v_x + \dfrac{1}{2}\,v_y - R\,\omega \\[4pt]
v_3 &= -\dfrac{\sqrt{3}}{2}\,v_x + \dfrac{1}{2}\,v_y - R\,\omega
\end{aligned}
$$

矩阵形式：

$$
\begin{bmatrix} v_1 \\ v_2 \\ v_3 \end{bmatrix}
=
\begin{bmatrix}
0 & -1 & -R \\[2pt]
\dfrac{\sqrt{3}}{2} & \dfrac{1}{2} & -R \\[4pt]
-\dfrac{\sqrt{3}}{2} & \dfrac{1}{2} & -R
\end{bmatrix}
\begin{bmatrix} v_x \\ v_y \\ \omega \end{bmatrix}
$$

**变量说明：**

| 符号 | 含义 | 单位 | 代码常量 |
|------|------|------|----------|
| $v_x$ | 机器人前进（+X）速度 | m/s | — |
| $v_y$ | 机器人左向（+Y）速度 | m/s | — |
| $\omega$ | 角速度，逆时针为正 | rad/s | — |
| $R$ | 轮中心到机器人中心距离 | m | `WHEEL_BASE_RADIUS = 0.138` |
| $v_1,v_2,v_3$ | 各轮线速度 | m/s | `MAX_WHEEL_SPEED = 0.65` |

代码入口：[chassis.cpp](firmware/lib/chassis/chassis.cpp#L137-L149)

```cpp
set_wheel_targets(-vy - w * WHEEL_BASE_RADIUS,
                   SQRT3_OVER_2 * vx + 0.5f * vy - w * WHEEL_BASE_RADIUS,
                  -SQRT3_OVER_2 * vx + 0.5f * vy - w * WHEEL_BASE_RADIUS);
```

---

### 正运动学（轮速 → 机器人速度）

由逆矩阵反解：

$$
\begin{aligned}
v_x      &= \dfrac{v_2 - v_3}{\sqrt{3}} \\[6pt]
v_y      &= \dfrac{-2v_1 + v_2 + v_3}{3} \\[6pt]
\omega   &= -\dfrac{v_1 + v_2 + v_3}{3R}
\end{aligned}
$$

---

### 编码器 → 轮速

$$
v_i = \frac{\Delta\mathrm{enc}_i}{\mathrm{PPR}_\mathrm{wheel}} \times C_\mathrm{wheel} \times \frac{1}{\Delta t}
$$

$$
\mathrm{PPR}_\mathrm{wheel} = \mathrm{PPR}_\mathrm{motor} \times G \times 2
                            = 11 \times 30 \times 2 = 660 \ \text{pulses/rev}
$$

$$
C_\mathrm{wheel} = 2\pi \times R_\mathrm{wheel} = 2\pi \times 0.031 \approx 0.1948 \ \text{m}
$$

| 常量 | 值 |
|------|----|
| `ENCODER_PPR_MOTOR` | 11 pulses/rev（电机轴） |
| `GEAR_RATIO` | 30 |
| `WHEEL_RADIUS` | 0.031 m |
| `ENCODER_PPR_WHEEL` | 660 pulses/rev（轮轴） |

代码入口：[chassis.cpp](firmware/lib/chassis/chassis.cpp#L69-L71)

---

### PID 闭环控制

每轮独立 PI + 前馈，周期 20 ms：

$$
\mathrm{PWM}_i = \underbrace{\frac{v_{i,\mathrm{target}}}{v_\mathrm{max}} \times \mathrm{PWM}_\mathrm{max}}_{\text{前馈}}
+ K_p\,e_i + K_i\int e_i\,dt + K_d\,\dot{e}_i
\qquad e_i = v_{i,\mathrm{target}} - v_{i,\mathrm{measured}}
$$

**PID 参数：**

| 场景 | $K_p$ | $K_i$ | $K_d$ |
|------|-------|-------|-------|
| 正常速度 | 900.0 | 80.0 | 0.0 |
| 低速纯旋转（$\|v\|<0.18$ m/s） | 300.0 | 40.0 | 0.0 |
| 低速混合运动（$\|v\|<0.12$ m/s） | 600.0 | 100.0 | 0.0 |

| 常量 | 值 |
|------|----|
| `CHASSIS_PID_INTEGRAL_LIMIT` | ±2.0（积分限幅） |
| `START_EFFECTIVE_PWM` | 80（启动阶段最小有效 PWM） |
| `MIN_RUNNING_PWM` | 50（运行中最小有效 PWM） |
| `ROTATION_MIN_RUNNING_PWM` | 70（纯旋转最小有效 PWM） |

---

### 串口协议（ESP32 ↔ 上位机）

**指令帧（上位机 → ESP32），共 16 字节：**

```
[0xAA][0x66][0x0C][  vx float LE  ][  vy float LE  ][  ω float LE   ][checksum]
  1B    1B    1B        4B                4B                4B             1B
```

**遥测帧（ESP32 → 上位机），共 46 字节，周期 50 ms：**

```
[0xAA][0x77][0x2A][ target×3 float ][ measured×3 float ][ pwm×3 int16 ][ encoder×3 int32 ][ checksum ]
  1B    1B    1B          12B                  12B              6B               12B              1B
```

校验：`checksum = Σ payload bytes`，超时 500 ms 无指令自动停车。
