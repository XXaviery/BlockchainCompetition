# 智驭新风

《智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人》代码工程下载入口。

## 下载范围

- `code/zhiyu_brain_final_software/zhiyu_brain/`：唯一 Python 软件工程根目录。
- `code/code/`：硬件、ROS2 与 Web 源码根目录。
- `tools/`：提交包构建脚本。
- `README.md`、`SUBMISSION_GUIDE.md`：代码安装、运行和部署参数说明。

代码运行所需的模型和数据目录随对应工程保留；运行日志、数据库、MCAP、恢复目录、构建缓存和重复内层仓库不属于下载包。

## 快速开始

Python 安装和入口见 `code/zhiyu_brain_final_software/zhiyu_brain/README.md`。硬件、ROS2 与 Web 入口见 `code/code/README.md`。完整安装与运行说明见 `SUBMISSION_GUIDE.md`。

生成带学校和队长占位符的本地提交暂存目录：

```powershell
pwsh -File tools/build_submission_package.ps1
```

已知真实信息后可显式传入：

```powershell
pwsh -File tools/build_submission_package.ps1 -SchoolName '学校全称' -CaptainName '队长姓名'
```

`Report/`、`material/`、`PNG/`、`Reference/`、`submission_package_template/` 和 `logs_backup/` 是本地报告、素材、提交 staging 或日志备份目录，不属于代码下载范围。
