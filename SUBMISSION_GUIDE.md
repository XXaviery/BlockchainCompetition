# 智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人：代码安装与运行说明

本文件只说明代码工程、部署参数和验收方法，不是最终设计文档。本轮不生成或修改任何报告 DOCX。

## 1. 工程边界

从竞赛项目根目录看，公开源码固定为：

- `code/software/`：唯一 Python 软件工程根目录。
- `code/robot/`：唯一硬件、ROS2 和 Web 源码根目录。
- `code/robot/firmware/`：ESP32 固件。
- `code/robot/ros2_ws/src/mof_esp32_bridge/`：ROS2 技术包；包名不改。
- `code/robot/web/`：Web 控制台和污染态势仿真演示。

`Report/`、`material/`、`PNG/`、`Reference/`、`logs_backup/` 和 `submission_package_template/` 是本地材料、证据、备份或暂存目录，不是公开代码根目录。

## 2. Python 软件

### 安装

```bash
cd code/software
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 正式入口

```bash
air-governance-demo --root .
air-governance-gui --root .
air-governance-gui --demo-headless --root .
air-governance-evaluate --root .
air-governance-export-evidence --root .
python -m pytest -q
```

`air-governance-export-evidence` 仅保留为既有证据导出入口，本轮不修改报告内容、不执行报告 DOCX 生成。

### 可移植路径

`code/software/src/air_governance/common/config.py` 以配置、模型标志文件自动向上发现软件工程根目录；外部调度器也可以显式提供：

```powershell
$env:AIR_GOVERNANCE_ROOT = (Resolve-Path .).Path
air-governance-demo --root $env:AIR_GOVERNANCE_ROOT
```

```bash
export AIR_GOVERNANCE_ROOT="$(pwd)"
air-governance-demo --root "$AIR_GOVERNANCE_ROOT"
```

`AIR_GOVERNANCE_ROOT` 和 `--root` 只接受 `code/software/`。配置、数据、模型、输出和日志都按该根目录下的相对路径解析；设备端口和 ROS2 工作空间不得填入该变量。

## 3. 固件

```bash
cd code/robot/firmware
pio run
```

上传时由部署环境显式提供端口，例如：

```bash
pio run -t upload --upload-port COM9
```

`COM9` 只是部署环境参数，不是项目源文件路径。固件运动学、PID、串口协议和雷达处理逻辑保持冻结。

## 4. ROS2

将相对目录 `code/robot/ros2_ws/src/mof_esp32_bridge/` 复制到目标 ROS2 工作空间的 `src/`，再在目标工作空间运行：

```bash
export MOF_ROS_WS="${MOF_ROS_WS:-$HOME/ros2_ws}"
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_MAP_PATH="${MOF_MAP_PATH:-$HOME/maps/mof_room_v2.yaml}"
cd "$MOF_ROS_WS"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py port:="$MOF_SERIAL_PORT" baud:=921600
```

生产安全链固定为：

```text
/cmd_vel_nav
→ velocity_smoother
→ /cmd_vel_smoothed
→ collision_monitor
→ /cmd_vel
→ esp32_cmd_vel_bridge
→ ESP32
```

不得把测试、WASD 或 Nav2 直接接到 `/cmd_vel_smoothed` 或 `/cmd_vel` 绕过安全链。真实 ROS2、树莓派、串口、雷达、碰撞监测和机器人运动属于未在本地执行的实机项目。

## 5. Web 控制台与污染演示

静态预览不需要 ROS：

```bash
cd code/robot
python3 web/server.py --host 127.0.0.1 --port 4173
```

带污染态势仿真演示：

```bash
python3 web/server.py --pollution-demo --host 127.0.0.1 --port 4173
```

污染演示只增加以下只读接口：

```text
GET /api/pollution/catalog
GET /api/pollution/snapshot?scenario_id=scenario_1_pm25_spike&step=0&metric=pm25
```

数据固定为 `SIMULATION / SOFTWARE-IN-THE-LOOP`，不是实机传感器读数。场景、步号和指标使用白名单；路径穿越、任意文件路径、非有限数值和过大请求均拒绝。该模式不初始化 ROS、不启动子进程、不发布 Twist、不产生 rosbag 主题，也不接受污染相关 POST。

带 ROS 的既有模式仍按 `code/robot/web/README.md` 使用：`--motion-only`、`--playback-only` 和 `--console`。运动控制令牌、序列号校验、300 ms 看门狗、BagManager 和既有安全链不因污染演示改变。

## 6. 竞赛暂存目录

封装脚本按竞赛要求创建以下七个目录：

```text
01_作品文件/
02_作品展示/
03_设计文档/
04_作品信息/
05_承诺书/
06_源文件/
07_过程记录/
```

默认学校和队长信息为占位符，不得虚构；正式提交前由参赛者补充真实信息和正式材料。本轮代码 ZIP 只包含 `code/software` 与 `code/robot` 的公开源文件，不包含报告、素材、日志、恢复目录、构建产物、历史数据库、MCAP、固件备份、`start_pi.sh` 或重复内层仓库。

## 7. 冻结与验收边界

本轮不重新训练模型、不生成新的仿真数据、不改变现有模型版本、指标数值、日志字段或输出文件名；RiskModel、RankerModel、RuleBasedPolicy、SafetySupervisor、TaskManager、TaskStateMachine、Mock adapters、ROS 安全链以及 `firmware/` 的运动学/PID/串口/雷达逻辑均保持冻结。`send_velocity_frame` 因声明目标不存在而从 ROS2 `setup.py` 中移除，未生成伪实现。
