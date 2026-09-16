# 智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人：Python 软件工程

项目：**《智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人》**

本目录是唯一 Python 软件工程根目录。`config/`、`data/`、`models/`、`outputs/` 和日志配置均以本目录为基准定位，不依赖竞赛工程在磁盘上的绝对位置。

## 1. 安装

从 `06_源文件` Git 工作区进入本目录：

```powershell
Set-Location code/software
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Linux/macOS 激活虚拟环境：

```bash
cd code/software
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

可编辑安装会注册正式命令入口，同时保留 `config/`、`data/`、`models/` 和 `outputs/` 作为可审计的运行资产。测试环境可安装 `python -m pip install -e ".[dev]"`。

## 2. 正式运行入口

无界面闭环 smoke test：

```bash
air-governance-demo --root .
```

桌面 GUI：

```bash
air-governance-gui --root .
```

无显示器 GUI 闭环：

```bash
air-governance-gui --demo-headless --root .
```

模型只读评估：

```bash
air-governance-evaluate --root .
```

报告证据导出入口用于后续文档阶段；当前代码工程不执行报告生成：

```bash
air-governance-export-evidence --root .
```

安装完成后仍可使用模块方式运行：

```bash
python -m scripts.run_demo --root .
python -m scripts.run_gui --demo-headless --root .
python -m scripts.run_gui --root .
```

## 3. 可移植路径

默认情况下，代码从已安装模块位置和当前工作目录向上查找包含以下标志文件的目录：

- `config/system.yaml`
- `models/risk/model.json`
- `models/ranker/model.json`

因此整个 `air_governance/` 目录移动后仍能定位运行资产。所有正式 CLI 也接受同一个 `--root` 参数；它会在启动时校验并激活该目录，使配置、数据、模型、输出和日志在外部工作目录下仍指向同一份移动后的资产。若使用非可编辑安装或从外部调度器启动，请显式提供根目录：

```powershell
$env:AIR_GOVERNANCE_ROOT = (Resolve-Path .).Path
air-governance-demo --root $env:AIR_GOVERNANCE_ROOT
```

```bash
export AIR_GOVERNANCE_ROOT="$(pwd)"
air-governance-demo --root "$AIR_GOVERNANCE_ROOT"
```

`AIR_GOVERNANCE_ROOT` 和 `--root` 只接受 Python 软件工程根目录；串口号、设备文件和 ROS 工作空间属于部署环境参数，不得写入这里作为源码路径。运行资产仍保持原有相对目录和文件名：`config/`、`data/`、`models/`、`outputs/metrics/`、`outputs/logs/`。

## 4. 统一闭环与冻结边界

```text
IndoorEnvironmentSimulator
→ EnvSample
→ RegionStateBuilder / RegionState
→ RiskModel
→ CandidateGenerator / CandidateTask
→ RankerModel 或 RuleBasedPolicy
→ SafetySupervisor / TaskManager / TaskStateMachine
→ MockNavigationAdapter
→ MockPurificationAdapter
→ Recheck
→ Redecision
```

`services/gui_backend.py` 是 `UnifiedBrainRuntime` 的薄封装，不包含第二套风险公式、Ranker、规则打分、安全算法或状态机。

工程封装保留并冻结：RiskModel、RankerModel、RuleBasedPolicy、SafetySupervisor、TaskManager、TaskStateMachine、Mock adapters、现有模型版本、数据、指标字段、日志字段和输出文件名。安装检查不重新训练模型、不生成仿真数据、不改写报告数字。

## 5. 目录职责

- `src/air_governance/`：统一核心 Python 包。
- `app/`、`services/`、`ui/`：应用、服务与桌面界面。
- `scripts/`：已安装命令入口的实现与离线维护脚本。
- `config/`：运行配置。
- `data/`：现有模拟与处理后数据。
- `models/`：现有冻结模型。
- `outputs/`：现有指标、图表、表格、决策与运行输出。
- `tests/`：单元和软件在环测试。

## 6. 测试

```bash
python -m pytest -q
```

测试应在安装后的环境中执行，不再依赖测试或脚本中的 `sys.path.insert()`。

## 7. 真实性声明

`data/simulation/`、模型测试集、Mock Navigation、Mock Purification、闭环风险变化均属于模拟或软件在环证据，不能表述为真实传感器、实机导航、实机净化、真实能耗或真实噪声测试结果。

训练与数据生成脚本仅为可追溯性保留：

```text
scripts/generate_simulation_data.py
scripts/build_dataset.py
scripts/train_risk_model.py
scripts/train_ranker.py
```

竞赛提交封装与 smoke test 不调用这些脚本。
