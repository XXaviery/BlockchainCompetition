[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SchoolName = '学校全称',
    [string]$CaptainName = '队长姓名',
    [string]$ProcessRecordPath = ''
)

$ErrorActionPreference = 'Stop'
$workName = '智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人'
$invalidNameChars = [IO.Path]::GetInvalidFileNameChars()
foreach ($value in @($SchoolName, $CaptainName)) {
    if ([string]::IsNullOrWhiteSpace($value) -or $value.IndexOfAny($invalidNameChars) -ge 0) {
        throw 'SchoolName and CaptainName must be non-empty folder-name components.'
    }
}

$projectRootPath = [IO.Path]::GetFullPath($ProjectRoot)
if ([string]::IsNullOrWhiteSpace($ProcessRecordPath)) {
    $ProcessRecordPath = Join-Path $projectRootPath 'logs_backup\stage2_20260916\CODE_REFACTOR_STAGE2_RECORD.md'
}
$pythonRoot = Join-Path $projectRootPath 'code\software'
$robotRoot = Join-Path $projectRootPath 'code\robot'
$submissionRoot = Join-Path $projectRootPath 'submission_package_template'
$packageName = "$SchoolName-$CaptainName-$workName"
$packageRoot = Join-Path $submissionRoot $packageName

foreach ($required in @($pythonRoot, $robotRoot, (Join-Path $projectRootPath 'SUBMISSION_GUIDE.md'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Container) -and -not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required path not found: $required"
    }
}

$submissionFull = [IO.Path]::GetFullPath($submissionRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$packageFull = [IO.Path]::GetFullPath($packageRoot)
if (-not $packageFull.StartsWith($submissionFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe package target: $packageFull"
}

$folderNames = @(
    '01_作品文件', '02_作品展示', '03_设计文档', '04_作品信息',
    '05_承诺书', '06_源文件', '07_过程记录'
)
New-Item -ItemType Directory -Path $packageFull -Force | Out-Null
foreach ($folder in $folderNames) {
    New-Item -ItemType Directory -Path (Join-Path $packageFull $folder) -Force | Out-Null
}

function Copy-IfMissing {
    param([string]$Source, [string]$Destination)
    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}

Copy-IfMissing -Source (Join-Path $projectRootPath 'SUBMISSION_GUIDE.md') -Destination (Join-Path $packageFull "01_作品文件\${workName}_安装与运行说明.md")

$placeholderText = @{
    '02_作品展示' = @"
状态：待补充
正式材料：${workName}_演示视频.mp4 或 ${workName}_演示课件.ppt
当前目录未放入正式作品展示材料；正式文件大小和格式以竞赛要求为准。
"@
    '03_设计文档' = @"
状态：待补充
正式材料：${workName}_设计文档.pdf
本轮不生成或修改最终设计文档。
"@
    '04_作品信息' = @"
状态：待补充
正式材料：${workName}_信息概要表.pdf
学校和团队信息必须使用真实内容。
"@
    '05_承诺书' = @"
状态：待补充
正式材料：${workName}_承诺书.pdf
签字页必须使用全体成员真实信息。
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
    'outputs', 'backups', 'codex', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.venv', 'venv', 'node_modules', 'dist', '.vscode', 'render', 'rendered',
    'render_output', 'render_artifacts'
)
$excludedExtensions = @(
    '.mcap', '.db', '.sqlite', '.sqlite3', '.bin', '.elf', '.hex', '.uf2',
    '.tmp', '.temp', '.bak', '.old', '.log', '.pyc', '.pyo', '.swp', '.swo'
)
$exclusionRows = [Collections.Generic.List[object]]::new()
$archiveFiles = [Collections.Generic.List[object]]::new()

function Get-InclusionDecision {
    param([IO.FileInfo]$File, [string]$SourceRoot, [string]$SourceLabel)
    $relative = [IO.Path]::GetRelativePath($SourceRoot, $File.FullName).Replace('\', '/')
    $parts = $relative.Split('/')
    foreach ($part in $parts) {
        if ($excludedDirectoryNames -contains $part) {
            return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除目录 $part" }
        }
        if ($part.EndsWith('.egg-info', [StringComparison]::OrdinalIgnoreCase)) {
            return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除安装元数据 $part" }
        }
    }
    if ($SourceLabel -eq 'code/robot' -and $parts[0] -eq 'code') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '重复内层仓库，仅保留在本地备份' }
    }
    if ($SourceLabel -eq 'code/robot' -and $parts.Count -eq 1 -and $File.Name -notin @('.gitignore', 'README.md')) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '本地部署/工作区文件，不属于公开源码' }
    }
    if ($File.Name -ieq 'start_pi.sh') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '本地部署入口含设备凭据' }
    }
    if ($File.Name -ieq 'DEBUG_ARCHIVE.md') {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '历史调试归档' }
    }
    if ($File.Name.StartsWith('~$') -or $File.Name.EndsWith('~')) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '临时文件' }
    }
    if ($excludedExtensions -contains $File.Extension.ToLowerInvariant()) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除扩展名 $($File.Extension)" }
    }
    return [pscustomobject]@{ Include = $true; Relative = $relative; Reason = '' }
}

foreach ($source in @(
    [pscustomobject]@{ Root = $pythonRoot; Label = 'code/software'; Prefix = 'code/software' },
    [pscustomobject]@{ Root = $robotRoot; Label = 'code/robot'; Prefix = 'code/robot' }
)) {
    foreach ($file in Get-ChildItem -LiteralPath $source.Root -Recurse -Force -File | Sort-Object FullName) {
        $decision = Get-InclusionDecision -File $file -SourceRoot $source.Root -SourceLabel $source.Label
        if ($decision.Include) {
            $archiveFiles.Add([pscustomobject]@{ File = $file; Entry = "$($source.Prefix)/$($decision.Relative)" })
        }
        else {
            $exclusionRows.Add([pscustomobject]@{ Source = $source.Label; Path = $decision.Relative; Reason = $decision.Reason })
        }
    }
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sourceFolder = Join-Path $packageFull '06_源文件'
$sourceZip = Join-Path $sourceFolder "${workName}_代码源码.zip"
if (Test-Path -LiteralPath $sourceZip) {
    Remove-Item -LiteralPath $sourceZip -Force
}
$stream = [IO.File]::Open($sourceZip, [IO.FileMode]::CreateNew)
$zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $false)
try {
    foreach ($item in $archiveFiles | Sort-Object Entry) {
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $item.File.FullName, $item.Entry, [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $zip.Dispose()
    $stream.Dispose()
}

$sourceManifestLines = [Collections.Generic.List[string]]::new()
$sourceManifestLines.Add("SHA256`tBytes`tRelativePath")
foreach ($item in $archiveFiles | Sort-Object Entry) {
    $hash = (Get-FileHash -LiteralPath $item.File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceManifestLines.Add("$hash`t$($item.File.Length)`t$($item.Entry)")
}
$sourceManifestLines | Set-Content -LiteralPath (Join-Path $sourceFolder '代码源文件清单.tsv') -Encoding utf8

@( "来源`t相对路径`t排除原因" ) + ($exclusionRows | ForEach-Object { "$($_.Source)`t$($_.Path)`t$($_.Reason)" }) |
    Set-Content -LiteralPath (Join-Path $sourceFolder '排除文件清单.tsv') -Encoding utf8

$exclusionSummary = @'
# 源文件排除规则

实际逐文件排除结果见 `排除文件清单.tsv`，共 __EXCLUDED_COUNT__ 个文件。

- 版本与构建缓存：`.git/`、`.pio/`、`build/`、`install/`、`log/`、`dist/`、`codex/`、`.vscode/`。
- Python/前端缓存：`__pycache__/`、`.pytest_cache/`、`.venv/`、`venv/`、`node_modules/`、`*.egg-info/`、`*.pyc`。
- 运行输出与历史运行：`outputs/`、`recovery/`、`backups/`、`codex/`、`DEBUG_ARCHIVE.md`。
- 历史数据库与设备产物：`*.db`、`*.sqlite*`、`*.mcap`、`*.bin`、`*.elf`、`*.hex`、`*.uf2`、`*.log`。
- 临时与渲染产物：`~$*`、`*~`、`*.tmp`、`*.temp`、`*.bak`、`*.old`、`*.swp`、`*.swo`、render 类目录。
- 重复内层仓库：机器人源码根下的 `code/`；迁移前副本保存在本地 `logs_backup/stage2_20260916/`，不进入 ZIP。
- `start_pi.sh`：含设备登录凭据，仅保留在本地，不进入 Git 或 ZIP。
- `Report/`、`material/`、`PNG/`、`Reference/` 不属于两个公开源码根，脚本不会扫描或复制它们。
'@
$exclusionSummary = $exclusionSummary.Replace('__EXCLUDED_COUNT__', [string]$exclusionRows.Count)
Set-Content -LiteralPath (Join-Path $sourceFolder '排除规则说明.md') -Value $exclusionSummary -Encoding utf8

"$((Get-FileHash -LiteralPath $sourceZip -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($sourceZip))" |
    Set-Content -LiteralPath (Join-Path $sourceFolder 'SHA256SUMS.txt') -Encoding utf8

$recordTarget = Join-Path $packageFull "07_过程记录\${workName}_代码重构过程记录.md"
if (Test-Path -LiteralPath $ProcessRecordPath -PathType Leaf) {
    Copy-Item -LiteralPath $ProcessRecordPath -Destination $recordTarget -Force
}
elseif (-not (Test-Path -LiteralPath $recordTarget)) {
    Set-Content -LiteralPath $recordTarget -Value '阶段过程记录将在本地验收完成后补充。' -Encoding utf8
}

$statusText = @"
提交目录名称：$packageName
学校全称和队长姓名当前为占位符，不得虚构；正式提交前必须替换为真实信息。

本轮只完成代码工程封装、可移植路径、Web 污染仿真演示和运行说明；未生成或修改最终设计文档。
"@
Set-Content -LiteralPath (Join-Path $packageFull '提交状态说明.txt') -Value $statusText -Encoding utf8

$manifestTarget = Join-Path $packageFull '文件清单.tsv'
$manifestLines = [Collections.Generic.List[string]]::new()
$manifestLines.Add("SHA256`tBytes`tRelativePath")
foreach ($file in Get-ChildItem -LiteralPath $packageFull -Recurse -Force -File | Where-Object { $_.FullName -ne $manifestTarget } | Sort-Object FullName) {
    $relative = [IO.Path]::GetRelativePath($packageFull, $file.FullName).Replace('\', '/')
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestLines.Add("$hash`t$($file.Length)`t$relative")
}
Set-Content -LiteralPath $manifestTarget -Value $manifestLines -Encoding utf8

[pscustomobject]@{
    PackageRoot = $packageFull
    PublicSourceRootCount = 2
    ArchivedSourceFiles = $archiveFiles.Count
    ExcludedFiles = $exclusionRows.Count
    SourceZipMiB = [Math]::Round(((Get-Item -LiteralPath $sourceZip).Length / 1MB), 2)
    PlaceholderIdentity = "$SchoolName / $CaptainName"
} | ConvertTo-Json
