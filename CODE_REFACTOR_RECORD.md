# 代码封装与提交路径重构过程记录（07 过程记录源稿）

日期：2026-09-14  
范围：只处理工程结构、可移植路径、提交暂存目录和运行说明。  
状态：代码封装与验证已完成；学校全称、队长姓名仍待真实信息；未生成或修改最终设计文档。

本文件用于后续重新生成设计文档时追溯工程变更，不是最终设计文档。

## 0. 竞赛要求核对与安全边界

已读取项目根目录的竞赛要求 DOCX：
`人工智能及应用大类其他人工智能应用赛道说明与提交要求.docx`。

核对结果如下：

- 正式提交根目录命名格式为 `学校全称-队长姓名-作品名称`。
- 正式子目录必须为：`01_作品文件`、`02_作品展示`、`03_设计文档`、`04_作品信息`、`05_承诺书`、`06_源文件`、`07_过程记录`。
- `06_源文件`源码与材料压缩包总大小要求不超过 100 MB。
- `07_过程记录`可记录 AI 使用截图、版本迭代和修改记录，本次将代码封装、路径重构、排除清单、测试结果写入此处。
- DOCX 本身未修改；本机没有 LibreOffice，未进行 DOCX 页面渲染，文本与表格已完成读取核对。

本轮安全边界：

- 没有执行递归删除，不删除 `code/code/code/` 原目录；该目录只在提交暂存包中排除。
- 本轮检查时项目内没有根 `.git` 或嵌套 `.git` 可供删除；未以初始化 Git 为理由删除任何源文件。
- `Report/`、`material/`、`PNG/` 中的现有证据和文档未编辑。
- 不调用训练、仿真数据生成或最终报告生成流程；不改报告数字、模型版本、指标字段、日志字段和输出文件名。

## A. 重构前后目录树

### 重构前（本轮接手时）

```text
BlockchainCompetition/
├─ code/
│  ├─ zhiyu_brain_final_software/zhiyu_brain/
│  │  ├─ app/ config/ data/ docs/ models/ outputs/
│  │  ├─ scripts/ services/ src/ tests/ ui/
│  │  ├─ requirements.txt、README.md
│  │  └─ 尚无正式 pyproject.toml；入口依赖源码目录导入习惯
│  └─ code/                         # 硬件、ROS2、Web 工作区
│     ├─ catkin_ws/ core/ tools/ Web/
│     ├─ recovery/、.pio/、编辑器工作文件
│     └─ code/                      # 重复内层仓库，原目录保留
├─ Report/ material/ PNG/ Reference/
└─ 人工智能及应用大类其他人工智能应用赛道说明与提交要求.docx
```

### 重构后（当前工程）

```text
BlockchainCompetition/
├─ .gitignore
├─ README.md
├─ SUBMISSION_GUIDE.md
├─ CODE_REFACTOR_RECORD.md
├─ tools/build_submission_package.ps1
├─ code/
│  ├─ zhiyu_brain_final_software/zhiyu_brain/  # 唯一 Python 根
│  │  ├─ pyproject.toml                         # 安装与命令入口
│  │  ├─ app/ config/ data/ docs/ models/ outputs/
│  │  ├─ scripts/ services/ src/ tests/ ui/
│  │  └─ README.md
│  └─ code/                                    # 唯一硬件/ROS2/Web 根
│     ├─ core/ catkin_ws/ tools/ Web/
│     └─ code/                                  # 原重复目录，保留但提交排除
├─ submission_package_template/
│  └─ 学校全称-队长姓名-智驭新风——基于多源环境感知与自主决策的移动空气治理机器人/
├─ Report/ material/ PNG/ Reference/
└─ 竞赛要求 DOCX
```

## B. 入口与运行方式

### B1. Python 软件工程

唯一工程根目录：`code/zhiyu_brain_final_software/zhiyu_brain/`。建议在该目录执行：

```powershell
Set-Location code/zhiyu_brain_final_software/zhiyu_brain
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
zhiyu-brain-demo --root .
zhiyu-brain-gui --root .
zhiyu-brain-gui --demo-headless --root .
zhiyu-brain-evaluate --root .
python -m pytest -q
```

模块方式和后续证据入口：

```bash
python -m scripts.run_demo --root .
python -m scripts.run_gui --demo-headless --root .
zhiyu-brain-export-evidence --root .   # 仅后续文档阶段使用
```

从外部调度器启动时，使用 `--root <zhiyu_brain 根目录>` 或环境变量
`ZHIYU_BRAIN_ROOT`。该根目录必须包含 `config/system.yaml`、
`models/risk/model.json`、`models/ranker/model.json`；`config/`、`data/`、
`models/`、`outputs/`、`outputs/logs/` 均从此根目录解析。

本次没有运行训练、数据集构建、仿真数据生成和证据导出脚本。

### B2. ESP32 固件

```bash
cd code/code/core
pio run
pio run -t upload --upload-port COM9
```

`COM9` 只是部署环境参数，可替换为目标设备端口；它不是项目源文件路径。

### B3. ROS2

将相对源码目录 `code/code/catkin_ws/src/mof_esp32_bridge/` 复制到目标 ROS2 工作空间的
`src/`，并在目标机设置部署参数：

```bash
export MOF_ROS_WS="${MOF_ROS_WS:-$HOME/ros2_ws}"
export MOF_SERIAL_PORT="${MOF_SERIAL_PORT:-/dev/mof_esp32}"
export MOF_MAP_PATH="${MOF_MAP_PATH:-$HOME/maps/mof_room_v2.yaml}"
export MOF_RVIZ_CONFIG="${MOF_RVIZ_CONFIG:-$HOME/rviz2/rviz.rviz}"
cd "$MOF_ROS_WS"
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch mof_esp32_bridge safe_velocity.launch.py \
  port:="$MOF_SERIAL_PORT" baud:=921600
```

冻结安全链：

```text
/cmd_vel_nav → velocity_smoother → /cmd_vel_smoothed
→ collision_monitor → /cmd_vel → esp32_cmd_vel_bridge → ESP32
```

不得把 WASD、Nav2 或其他输入直接接到 `/cmd_vel_smoothed`、`/cmd_vel` 绕过安全链。

### B4. Web

从硬件/ROS2/Web 源码根运行静态预览：

```bash
cd code/code
python3 Web/server.py --host 127.0.0.1 --port 4173
```

ROS 工作空间、SSH 主机、串口、地图/MCAP 根目录、证据目录和端口均为部署参数，
详见 `code/code/Web/README.md`；它们不写入源码路径。

## C. 提交暂存目录树

当前暂存根保留竞赛要求的七个目录，学校和队长未知时使用占位符：

```text
submission_package_template/
└─ 学校全称-队长姓名-智驭新风——基于多源环境感知与自主决策的移动空气治理机器人/
   ├─ 01_作品文件/
   │  └─ 智驭新风——基于多源环境感知与自主决策的移动空气治理机器人_安装与运行说明.md
   ├─ 02_作品展示/待补充.txt
   ├─ 03_设计文档/待补充.txt
   ├─ 04_作品信息/待补充.txt
   ├─ 05_承诺书/待补充.txt
   ├─ 06_源文件/
   │  ├─ …_Python软件工程源码.zip
   │  ├─ …_硬件_ROS2_Web源码.zip
   │  ├─ SHA256SUMS.txt
   │  ├─ 排除规则说明.md
   │  └─ 排除文件清单.tsv
   ├─ 07_过程记录/
   │  └─ …_代码封装与路径重构记录.md
   ├─ 提交状态说明.txt
   └─ 文件清单.tsv
```

`待补充.txt` 仅是未知道真实身份/材料时的占位提示，不得直接作为正式竞赛材料提交。
构建脚本重跑时不递归删除提交根，也不会覆盖已存在的人工补充文件。

## D. 排除文件清单

完整逐文件结果由 `06_源文件/排除文件清单.tsv` 记录。规则如下：

- 版本/构建/编辑器：`.git/`、`.pio/`、`.vscode/`、`build/`、`install/`、`log/`、
  `dist/`、`__pycache__/`、`.pytest_cache/`、`.venv/`、`venv/`、`node_modules/`、
  `*.egg-info/`。
- 恢复/历史/内部记录：`recovery/`、`backups/`、`codex/`、`DEBUG_ARCHIVE.md`。
- 历史数据库和运行包：`*.db`、`*.sqlite*`、`*.mcap`、`*.log`。
- 固件或构建产物：`*.bin`、`*.elf`、`*.hex`、`*.uf2`。
- 临时/渲染产物：`~$*`、`*~`、`*.tmp`、`*.temp`、`*.bak`、`*.old`、交换文件，
  以及 `render/`、`rendered/`、`render_output/`、`render_artifacts/`。
- 重复内层仓库：硬件源根下的 `code/`，即原目录 `code/code/code/`；只排除提交包，
  不删除工作区原目录。

本次生成器逐文件排除 1138 个文件；Python 源码包 155 个条目（约 6.18 MiB），
硬件/ROS2/Web 源码包 77 个条目（约 0.22 MiB），源码包合计约 6.40 MiB，低于 100 MB。
两个 ZIP 的禁用模式命中数均为 0，重复内层仓库条目数为 0。

## E. 可移植性检查

- `pyproject.toml` 已补充正式安装配置和 4 个命令入口：`zhiyu-brain-demo`、
  `zhiyu-brain-gui`、`zhiyu-brain-export-evidence`、`zhiyu-brain-evaluate`。
- 已移除活动 Python 源码中的 `sys.path.insert()`/`sys.path.append()` 依赖；安装包直接
  通过包导入运行。
- `resolve_project_root()` 统一按“显式 `--root` → 当前活动根 → `ZHIYU_BRAIN_ROOT` →
  工程标志文件发现”解析。入口启动时校验并激活根目录；安装包从外部工作目录启动也可用。
- 运行结果中的数据库路径序列化为 `outputs/logs/brain.db` 等项目相对路径，避免新指标
  和日志记录带出主机盘符。
- 活动 Python 工程、指标文本/JSON 和源码文档中未发现 `/mnt/data/phase3_work/merged`、
  `/home/pi` 或盘符绝对源码路径；设备路径、ROS 工作空间、地图、RViz 和 Web 端口仅作为
  部署参数说明。重复仓库和历史调试归档中的旧痕迹未修改，因而不进入提交包。
- `code/code/core/.vscode/` 等机器专用编辑器配置被隔离并在提交包排除；固件核心源码未编辑。
- 现有 `models/`、`data/`、`outputs/metrics/` 字段和输出文件名保持不变；未重新训练模型，
  未生成新的仿真数据。

## F. 关键 smoke test 结果

本次运行全部使用移动后的临时副本，不覆盖 canonical 工程的模型、数据、指标和日志。

- Python 源码 AST 检查：通过，活动 Python 文件 72 个。
- ROS2/Web Python AST 检查：通过，排除重复仓库和构建目录后 28 个文件。
- wheel 构建：通过，产出 `zhiyu_brain-0.1.0-py3-none-any.whl`；正式入口元数据存在。
- 安装后外部根目录检查：通过；从项目外部工作目录通过 `--root` 解析到移动副本的
  `config/system.yaml`。
- 移动副本全量测试：`30 passed, 1164 warnings in 33.22s`。警告来自本机 Matplotlib
  缺少中文字体，不是测试失败。
- `zhiyu-brain-demo --root <移动副本>`：通过，`completed_cycles=3`、
  `final_state=COMPLETED`，输出 `log_db=outputs/logs/brain.db`。
- `zhiyu-brain-gui --demo-headless --root <移动副本>`：通过，3 个周期完成，
  `decision_id=demo_decision_003`、`task_id=task_003`，数据库路径保持相对形式。
- `zhiyu-brain-evaluate --root <移动副本>`：通过，风险模型 `MAE=1.7899560516184143`、
  `RMSE=3.2672760249856347`、`R²=0.8856446619695464`、趋势准确率 `0.796875`；
  Ranker `NDCG@3=0.9595783911173021`、`Precision@3=0.4927536231884058`、
  `Top-1=0.927536231884058`、138 个查询组，与现有指标证据一致。
- 活动源码和 `outputs/metrics` 便携路径扫描：未发现机器专用绝对路径命中；数据库字段为
  相对路径。
- 提交包重建：通过，七个正式目录均存在；Python ZIP 155 条、硬件 ZIP 77 条，禁用模式
  命中 0，排除 1138 个文件，源码 ZIP 合计约 6.40 MiB。
- 提交包清单格式复核：`排除文件清单.tsv` 和 `文件清单.tsv` 均使用真实制表符分隔，
  表头字段可被 TSV 解析器正确读取。
- 文件总清单校验：列出其余 12 个实际文件并排除清单自身，12/12 条目的 SHA-256 与字节数
  均匹配，避免“清单自包含”造成不可避免的自校验失配。
- 未在本机执行 ROS2 实机启动、串口上传或 Web 生产模式；这些入口已完成静态语法/路径
  检查，实际设备参数仍由部署环境提供。

## G. 冻结算法、架构与指标核验

重构前后 49 个基准文件 SHA-256 全部一致：

- 8 个冻结 Python 文件：`RiskModel`、`RankerModel`、`RuleBasedPolicy`、
  `SafetySupervisor`、`TaskManager`、`TaskStateMachine`、两个 Mock adapter。
- `code/code/core/` 15 个非构建固件源文件：运动学、PID、串口协议和雷达处理逻辑不变。
- 26 个模型、数据、指标证据文件：模型文件、数据集、指标值、日志字段和输出文件名不变。

以下冻结项未被触碰：

- 风险预测、排序、规则策略、安全监督、任务管理、状态机和 Mock adapter 行为。
- ROS 安全链 `/cmd_vel_nav → velocity_smoother → /cmd_vel_smoothed →
  collision_monitor → /cmd_vel → esp32_cmd_vel_bridge → ESP32`。
- `code/code/core` 的固件运动学、PID、串口协议和雷达处理逻辑。
- 没有重新训练模型、生成新的仿真数据、修改报告实验数字、改变模型版本或改变指标口径。

## H. 实际变更记录与后续操作

新增或补充：

- 根目录 `README.md`、`.gitignore`、`SUBMISSION_GUIDE.md`、本过程记录、
  `tools/build_submission_package.ps1`。
- Python `pyproject.toml`、`scripts/_paths.py`、路径单元测试 `tests/unit/test_paths.py`。

路径封装修改：

- `src/zhiyu_brain/common/config.py`、`logging/store.py`、`runtime/software_loop.py`。
- `services/gui_backend.py`、`services/report_exporter.py`、`models/explain.py`。
- `scripts/run_demo.py`、`run_gui.py`、`evaluate_models.py`、`export_report_evidence.py`、
  `build_dataset.py`、`generate_simulation_data.py`、`train_risk_model.py`、
  `train_ranker.py`、`freeze_final_report.py`。

运行说明与部署参数修改：

- Python README、硬件根 README、ROS2 bridge README、Web README。
- `nav2_bringup.launch.py`、`mof_nav_with_ekf.launch.py`、`mof_nav_with_slam.launch.py`。
- `run_mof_web_console_remote.sh` 的 ROS 工作空间改为 `MOF_ROS_WS` 部署参数。
- 提交生成器清单表头改为真实制表符，并保留人工补充文件；重跑时只删除并重建两个受管 ZIP。
- `文件清单.tsv` 的自身条目不纳入校验范围，其余暂存文件均纳入 SHA-256/字节数清单。

以上修改均限于安装/路径/说明/提交封装；`Report/`、`material/`、`PNG/` 未修改。

提交包可重复构建：

```powershell
pwsh -File tools/build_submission_package.ps1
```

真实学校和队长信息确定后运行：

```powershell
pwsh -File tools/build_submission_package.ps1 -SchoolName '真实学校全称' -CaptainName '真实队长姓名'
```

替换身份信息、补齐 02/03/04/05 后，才可将占位暂存目录转为正式竞赛提交目录。本轮不生成
最终设计文档。
