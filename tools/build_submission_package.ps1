[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SchoolName = '学校全称',
    [string]$CaptainName = '队长姓名'
)

$ErrorActionPreference = 'Stop'
$workName = '智驭新风——基于多源环境感知与自主决策的移动空气治理机器人'
$invalidNameChars = [IO.Path]::GetInvalidFileNameChars()
foreach ($value in @($SchoolName, $CaptainName)) {
    if ([string]::IsNullOrWhiteSpace($value) -or $value.IndexOfAny($invalidNameChars) -ge 0) {
        throw 'SchoolName and CaptainName must be non-empty folder-name components.'
    }
}

$projectRootPath = [IO.Path]::GetFullPath($ProjectRoot)
$pythonRoot = Join-Path $projectRootPath 'code\zhiyu_brain_final_software\zhiyu_brain'
$hardwareRoot = Join-Path $projectRootPath 'code\code'
$submissionRoot = Join-Path $projectRootPath 'submission_package_template'
$packageName = "$SchoolName-$CaptainName-$workName"
$packageRoot = Join-Path $submissionRoot $packageName

foreach ($required in @($pythonRoot, $hardwareRoot, (Join-Path $projectRootPath 'SUBMISSION_GUIDE.md'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path not found: $required"
    }
}

$submissionFull = [IO.Path]::GetFullPath($submissionRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$packageFull = [IO.Path]::GetFullPath($packageRoot)
if (-not $packageFull.StartsWith($submissionFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe package target: $packageFull"
}
# Keep the staging root and any manually supplied files. The builder only
# refreshes the generated artifacts listed below, so rerunning it cannot erase
# a video, PDF, signed statement, or an existing process record.

$folders = 1..7 | ForEach-Object { '{0:D2}' -f $_ }
$folderNames = @(
    '01_作品文件', '02_作品展示', '03_设计文档', '04_作品信息',
    '05_承诺书', '06_源文件', '07_过程记录'
)
New-Item -ItemType Directory -Path $packageFull -Force | Out-Null
foreach ($folder in $folderNames) {
    New-Item -ItemType Directory -Path (Join-Path $packageFull $folder) -Force | Out-Null
}

function Copy-IfMissing {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}

$guideTarget = Join-Path $packageFull "01_作品文件\${workName}_安装与运行说明.md"
Copy-IfMissing -Source (Join-Path $projectRootPath 'SUBMISSION_GUIDE.md') -Destination $guideTarget

$placeholderText = @{
    '02_作品展示' = @"
状态：待补充
正式材料：${workName}_演示视频.mp4 或 ${workName}_演示课件.ppt
当前目录未放入正式作品展示材料；单文件须控制在 500MB 以内。
"@
    '03_设计文档' = @"
状态：待补充
正式材料：${workName}_设计文档.pdf
当前目录未放入正式设计文档。
"@
    '04_作品信息' = @"
状态：待补充
正式材料：${workName}_信息概要表.pdf
当前目录未放入正式作品信息；学校与团队信息须使用真实内容。
"@
    '05_承诺书' = @"
状态：待补充
正式材料：${workName}_承诺书.pdf
当前目录未放入正式承诺书；签字页须使用全体成员真实信息。
"@
}
foreach ($entry in $placeholderText.GetEnumerator()) {
    $path = Join-Path $packageFull "$($entry.Key)\待补充.txt"
    if (-not (Test-Path -LiteralPath $path)) {
        Set-Content -LiteralPath $path -Value $entry.Value -Encoding utf8
    }
}

$excludedDirectoryNames = @(
    '.git', '.pio', '__pycache__', 'recovery', 'build', 'install', 'log',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.venv', 'venv',
    'node_modules', 'dist', '.vscode', 'backups', 'codex', 'render', 'rendered', 'render_output', 'render_artifacts'
)
$excludedExtensions = @(
    '.mcap', '.db', '.sqlite', '.sqlite3', '.bin', '.elf', '.hex', '.uf2',
    '.tmp', '.temp', '.bak', '.old', '.log', '.pyc', '.pyo', '.swp', '.swo'
)
$exclusionRows = [Collections.Generic.List[object]]::new()

function Get-InclusionDecision {
    param(
        [IO.FileInfo]$File,
        [string]$SourceRoot,
        [ValidateSet('python', 'hardware')][string]$SourceKind
    )
    $relative = [IO.Path]::GetRelativePath($SourceRoot, $File.FullName).Replace('\', '/')
    $parts = $relative.Split('/')
    if ($SourceKind -eq 'hardware' -and $parts[0] -eq 'code') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '重复内层仓库 code/code/code' }
    }
    if ($SourceKind -eq 'python' -and $parts[0] -eq 'outputs') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '运行输出与报告生成物，保留在本地工程并移至项目根 logs_backup 或本地输出目录' }
    }
    foreach ($part in $parts[0..([Math]::Max(0, $parts.Length - 2))]) {
        if ($excludedDirectoryNames -contains $part) {
            return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除目录 $part" }
        }
        if ($part.EndsWith('.egg-info', [StringComparison]::OrdinalIgnoreCase)) {
            return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除安装元数据 $part" }
        }
    }
    if ($File.Name.StartsWith('~$') -or $File.Name.EndsWith('~')) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '临时文件' }
    }
    if ($excludedExtensions -contains $File.Extension.ToLowerInvariant()) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除扩展名 $($File.Extension)" }
    }
    if ($SourceKind -eq 'hardware' -and $relative -eq 'DEBUG_ARCHIVE.md') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '历史调试归档，隔离于提交源码' }
    }
    return [pscustomobject]@{ Include = $true; Relative = $relative; Reason = '' }
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
function New-FilteredSourceArchive {
    param(
        [string]$SourceRoot,
        [ValidateSet('python', 'hardware')][string]$SourceKind,
        [string]$ArchiveRoot,
        [string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Force
    }
    $stream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew)
    $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $SourceRoot -Recurse -Force -File | Sort-Object FullName) {
            $decision = Get-InclusionDecision -File $file -SourceRoot $SourceRoot -SourceKind $SourceKind
            if (-not $decision.Include) {
                $exclusionRows.Add([pscustomobject]@{
                    Source = $SourceKind
                    Path = $decision.Relative
                    Reason = $decision.Reason
                })
                continue
            }
            $entryName = "$ArchiveRoot/$($decision.Relative)"
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip, $file.FullName, $entryName, [IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $zip.Dispose()
        $stream.Dispose()
    }
}

$sourceFolder = Join-Path $packageFull '06_源文件'
$pythonZip = Join-Path $sourceFolder "${workName}_Python软件工程源码.zip"
$hardwareZip = Join-Path $sourceFolder "${workName}_硬件_ROS2_Web源码.zip"
New-FilteredSourceArchive -SourceRoot $pythonRoot -SourceKind python -ArchiveRoot 'zhiyu_brain' -Destination $pythonZip
New-FilteredSourceArchive -SourceRoot $hardwareRoot -SourceKind hardware -ArchiveRoot 'mof_robot' -Destination $hardwareZip

$exclusionList = Join-Path $sourceFolder '排除文件清单.tsv'
@( "来源`t相对路径`t排除原因" ) + ($exclusionRows | ForEach-Object { "$($_.Source)`t$($_.Path)`t$($_.Reason)" }) |
    Set-Content -LiteralPath $exclusionList -Encoding utf8

$exclusionSummary = @'
# 源文件排除规则

实际逐文件排除结果见 `排除文件清单.tsv`。

- 版本与构建缓存：`.git/`、`.pio/`、`.vscode/`、`build/`、`install/`、`log/`、`dist/`、`codex/`。
- Python/前端缓存：`__pycache__/`、`.pytest_cache/`、`.venv/`、`venv/`、`node_modules/`、`*.pyc`。
- Python 运行输出：`outputs/`；原始日志记录副本位于项目根 `logs_backup/python_outputs_logs/`，不进入源码 ZIP。
- 恢复与历史运行：`recovery/`、历史数据库 `*.db/*.sqlite*`、`DEBUG_ARCHIVE.md`。
- 大体积或设备产物：`*.mcap`、固件备份目录 `backups/`、固件备份 `*.bin/*.elf/*.hex/*.uf2`、`*.log`。
- 临时与渲染产物：`~$*`、`*~`、`*.tmp/*.temp/*.bak/*.old/*.swp/*.swo`、render 类目录。
- 重复内层仓库：硬件源码根下的 `code/`，即原目录 `code/code/code/`；原目录未删除。
'@
$exclusionSummary = $exclusionSummary.Replace(
    '实际逐文件排除结果见 `排除文件清单.tsv`。',
    "实际逐文件排除结果见 `排除文件清单.tsv`，共 $($exclusionRows.Count) 个文件。"
)
Set-Content -LiteralPath (Join-Path $sourceFolder '排除规则说明.md') -Value $exclusionSummary -Encoding utf8

$hashLines = foreach ($archive in @($pythonZip, $hardwareZip)) {
    $hash = Get-FileHash -LiteralPath $archive -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($archive))"
}
Set-Content -LiteralPath (Join-Path $sourceFolder 'SHA256SUMS.txt') -Value $hashLines -Encoding utf8

$recordSource = Join-Path $projectRootPath 'CODE_REFACTOR_RECORD.md'
$recordTarget = Join-Path $packageFull "07_过程记录\${workName}_代码封装与路径重构记录.md"
if (Test-Path -LiteralPath $recordSource) {
    Copy-IfMissing -Source $recordSource -Destination $recordTarget
}
else {
    Set-Content -LiteralPath $recordTarget -Value '代码重构记录将在最终验证后补充。' -Encoding utf8
}

$statusText = @"
根目录命名已按竞赛要求保留真实信息占位符。提交前必须将“学校全称”和“队长姓名”替换为真实值，并删除各目录中的“待补充.txt”后放入正式文件。

本次仅完成代码工程封装、可移植路径、源文件压缩包和运行说明；未生成或修改最终设计文档。
"@
Set-Content -LiteralPath (Join-Path $packageFull '提交状态说明.txt') -Value $statusText -Encoding utf8

$allFiles = Get-ChildItem -LiteralPath $packageFull -Recurse -File | Sort-Object FullName
$manifestTarget = Join-Path $packageFull '文件清单.tsv'
$allFiles = $allFiles | Where-Object { $_.FullName -ne $manifestTarget }
$manifestLines = @( "SHA256`tBytes`tRelativePath" )
foreach ($file in $allFiles) {
    $relative = [IO.Path]::GetRelativePath($packageFull, $file.FullName).Replace('\', '/')
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestLines += "$hash`t$($file.Length)`t$relative"
}
Set-Content -LiteralPath $manifestTarget -Value $manifestLines -Encoding utf8

$archives = Get-Item -LiteralPath $pythonZip, $hardwareZip
$totalMiB = [Math]::Round((($archives | Measure-Object Length -Sum).Sum / 1MB), 2)
[pscustomobject]@{
    PackageRoot = $packageFull
    PythonArchiveMiB = [Math]::Round(($archives[0].Length / 1MB), 2)
    HardwareArchiveMiB = [Math]::Round(($archives[1].Length / 1MB), 2)
    TotalSourceArchiveMiB = $totalMiB
    ExcludedFiles = $exclusionRows.Count
} | ConvertTo-Json
