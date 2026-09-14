# 智驭新风安装与运行说明

## 1. 源码根目录

解压两个源码包后，各自目录角色固定如下：

- `zhiyu_brain/`：唯一 Python 软件工程根目录。
- `mof_robot/`：硬件固件、ROS2 和 Web 源码根目录。
- `mof_robot/core/`：ESP32 PlatformIO 工程。
- `mof_robot/catkin_ws/src/mof_esp32_bridge/`：ROS2 包。
- `mof_robot/Web/`：Web 控制台。

源码说明只使用上述相对路径。`COM8`、`COM9`、`/dev/mof_esp32`、ROS 工作空间、地图路径和端口号都是部署环境参数，不是源码路径。

## 2. Python 安装与运行

```bash
cd zhiyu_brain
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
zhiyu-brain-demo --root .
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install -e .
zhiyu-brain-demo --root .
```

其他入口：

```bash
zhiyu-brain-gui --root .
zhiyu-brain-gui --demo-headless --root .
zhiyu-brain-evaluate --root .
python -m pytest -q
```

若调度器未从源码目录启动，可通过 `--root <Python软件工程根目录>` 或环境变量 `ZHIYU_BRAIN_ROOT` 指向 `zhiyu_brain/`。二者只在运行环境中设置，不写回源码；源码内的 `config/`、`data/`、`models/`、`outputs/` 和 `outputs/logs/` 始终按该根目录解析。

## 3. ESP32 固件

```bash
cd mof_robot/core
pio run
```

上传时显式提供当前设备端口；例如 Windows 可用 `pio run -t upload --upload-port COM9`。仓库中的默认端口只是部署默认值，切换机器时用命令行覆盖。

## 4. ROS2

将相对目录 `mof_robot/catkin_ws/src/mof_esp32_bridge/` 复制到目标 ROS2 工作空间的 `src/` 后执行：

```bash
cd <ROS2工作空间>
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_MAP_PATH="${MOF_MAP_PATH:-$HOME/maps/mof_room_v2.yaml}"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:="$MOF_SERIAL_PORT" baud:=921600
```

冻结的生产安全链为：

```text
/cmd_vel_nav
→ velocity_smoother
→ /cmd_vel_smoothed
→ collision_monitor
→ /cmd_vel
→ esp32_cmd_vel_bridge
→ ESP32
```

串口、地图、RViz 配置和目标 ROS2 工作空间路径均由部署环境提供。不得把 WASD 或 Nav2 直接接到 `/cmd_vel_smoothed` 或 `/cmd_vel` 绕过安全链。

## 5. Web 控制台

在 `mof_robot/` 根目录运行静态预览：

```bash
python3 Web/server.py --host 127.0.0.1 --port 4173
```

具备 ROS2 环境时可按 `Web/README.md` 使用 `--motion-only`、`--playback-only` 或 `--console`。`--bag-root`、`--evidence-dir` 和端口均为运行参数；提交包不包含 MCAP、recovery 或历史运行目录。

## 6. 冻结声明

提交封装未重新训练模型、未生成仿真数据、未改变模型版本与指标口径，也未修改 RiskModel、RankerModel、RuleBasedPolicy、SafetySupervisor、TaskManager、TaskStateMachine、Mock adapters、ROS 安全链及 `core/` 固件运动学、PID、串口协议和雷达处理逻辑。
