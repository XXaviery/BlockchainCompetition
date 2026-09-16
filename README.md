# 智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人

本 Git 工作区维护代码工程、可移植运行说明和辅助工具。

## 公开代码根目录

- `code/software/`：唯一 Python 软件工程根目录，Python 包为 `air_governance`。
- `code/robot/`：唯一硬件、ROS2 和 Web 源码根目录。
- `code/robot/firmware/`：ESP32 PlatformIO 固件工程。
- `code/robot/ros2_ws/`：ROS2 工作空间源码，其中 ROS 技术包名保持 `mof_esp32_bridge`。
- `code/robot/web/`：小写目录名的 Web 控制台和独立污染态势仿真演示。

源码引用均使用相对路径。串口、ROS2 工作空间、地图、rosbag、端口和部署主机是运行环境参数，不是项目源码路径。

## 快速开始

```bash
cd code/software
python -m pip install -e ".[dev]"
air-governance-demo --root .
air-governance-gui --demo-headless --root .
air-governance-evaluate --root .
python -m pytest -q
```

Web 静态预览（不需要 ROS）：

```bash
cd code/robot
python3 web/server.py --host 127.0.0.1 --port 4173 --pollution-demo
```

固件和 ROS2 的入口、部署参数、安全链及测试命令见 [`SUBMISSION_GUIDE.md`](SUBMISSION_GUIDE.md)。
