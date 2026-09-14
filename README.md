# BlockchainCompetition

《智驭新风——基于多源环境感知与自主决策的移动空气治理机器人》竞赛工程。

## 工程根目录

- `code/zhiyu_brain_final_software/zhiyu_brain/`：唯一 Python 软件工程根目录。
- `code/code/`：硬件、ROS2 与 Web 源码根目录。
- `Report/`、`material/`、`PNG/`：既有文档、材料与图片证据，本轮代码重构未修改。
- `tools/build_submission_package.ps1`：生成竞赛七目录提交暂存包。

`code/code/code/` 是重复内层仓库，原目录保留，但不会进入根 Git 提交或竞赛源码压缩包。`code/code/core/.vscode/` 只保存本机编辑器配置，也会被隔离，不作为源码提交。

## 快速开始

Python 安装和入口见 `code/zhiyu_brain_final_software/zhiyu_brain/README.md`。硬件、ROS2 与 Web 入口见 `code/code/README.md`。提交运行总说明见 `SUBMISSION_GUIDE.md`。

生成带学校和队长占位符的提交暂存目录：

```powershell
pwsh -File tools/build_submission_package.ps1
```

脚本只刷新提交包中的源码压缩包、校验清单、状态说明和过程记录，不递归删除既有提交包目录；`02_作品展示`、`03_设计文档`、`04_作品信息`、`05_承诺书` 中的人工补充文件会保留。

已知真实信息后可显式传入：

```powershell
pwsh -File tools/build_submission_package.ps1 -SchoolName '学校全称' -CaptainName '队长姓名'
```

提交前必须用真实值替换占位符，并补齐作品展示、设计文档、作品信息和承诺书。本仓库的代码封装任务不生成或修改最终设计文档。

## 冻结边界

本轮不重新训练模型、不生成仿真数据、不修改报告实验数字，不改变 RiskModel、RankerModel、RuleBasedPolicy、SafetySupervisor、TaskManager、TaskStateMachine、Mock adapters、ROS 安全链以及 `code/code/core/` 中的运动学、PID、串口协议和雷达处理逻辑。

完整变更与验证记录见 `CODE_REFACTOR_RECORD.md`。
