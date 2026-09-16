[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SchoolName = '',
    [string]$CaptainName = '',
    [string]$ProcessRecordPath = ''
)

$ErrorActionPreference = 'Stop'
$workName = '智驭新风——基于环境风险预测与安全任务调度的移动空气治理机器人'
$invalidNameChars = [IO.Path]::GetInvalidFileNameChars()
foreach ($value in @($SchoolName, $CaptainName)) {
    if ([string]::IsNullOrWhiteSpace($value) -or $value.IndexOfAny($invalidNameChars) -ge 0) {
        throw 'SchoolName and CaptainName are required packaging parameters.'
    }
}

$gitRootPath = [IO.Path]::GetFullPath($ProjectRoot)
$submissionRoot = [IO.Path]::GetFullPath((Split-Path -Parent $gitRootPath))
$temporaryRoot = Join-Path $submissionRoot '暂时存放'
if ([IO.Path]::GetFileName($gitRootPath) -ne '06_源文件') {
    throw "ProjectRoot must be the 06_源文件 Git workspace: $gitRootPath"
}
if ([string]::IsNullOrWhiteSpace($ProcessRecordPath)) {
    $ProcessRecordPath = Join-Path $temporaryRoot 'CODE_REFACTOR_STAGE3_RECORD.md'
}
$pythonRoot = Join-Path $gitRootPath 'code\software'
$robotRoot = Join-Path $gitRootPath 'code\robot'
$packageName = "$SchoolName-$CaptainName-$workName"
$packageFull = $submissionRoot

foreach ($required in @($pythonRoot, $robotRoot, (Join-Path $gitRootPath 'SUBMISSION_GUIDE.md'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Container) -and -not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required path not found: $required"
    }
}

$folderNames = @(
    '01_作品文件', '02_作品展示', '03_设计文档', '04_作品信息',
    '05_承诺书', '06_源文件', '07_过程记录'
)
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
foreach ($folder in $folderNames) {
    New-Item -ItemType Directory -Path (Join-Path $packageFull $folder) -Force | Out-Null
}

# 旧版封装曾把状态文件和清单放在提交包根目录。将这两个脚本生成文件收纳到 07，
# 使提交包根目录严格只保留竞赛要求的七个两位数字目录；其他未知根项目不静默处理。
$processFolder = Join-Path $packageFull '07_过程记录'
foreach ($legacyFileName in @('提交状态说明.txt', '文件清单.tsv')) {
    $legacyPath = Join-Path $packageFull $legacyFileName
    $legacyTarget = Join-Path $processFolder $legacyFileName
    if (Test-Path -LiteralPath $legacyPath -PathType Leaf) {
        Move-Item -LiteralPath $legacyPath -Destination $legacyTarget -Force
    }
}
$allowedRootItems = @($folderNames + '暂时存放')
$unexpectedRootItems = @(Get-ChildItem -LiteralPath $packageFull -Force | Where-Object {
    $_.Name -notin $allowedRootItems
})
if ($unexpectedRootItems.Count -gt 0) {
    $names = ($unexpectedRootItems | ForEach-Object { $_.Name }) -join ', '
    throw "Project root must contain only 01_作品文件 through 07_过程记录 and 暂时存放; unexpected items: $names"
}

function Copy-IfMissing {
    param([string]$Source, [string]$Destination)
    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}

Copy-IfMissing -Source (Join-Path $gitRootPath 'SUBMISSION_GUIDE.md') -Destination (Join-Path $packageFull "01_作品文件\${workName}_安装与运行说明.md")

$folderNotes = @{
    '02_作品展示' = @"
目录用途：作品展示材料。
当前代码工作区不包含展示视频或课件。
正式材料的格式和内容以竞赛提交信息为准。
"@
    '03_设计文档' = @"
目录用途：设计文档材料。
设计文档不属于代码工作区，内容以正式设计文档为准。
"@
    '04_作品信息' = @"
目录用途：作品信息材料。
代码工作区不记录未公开的报名身份信息。
"@
    '05_承诺书' = @"
目录用途：承诺书材料。
签署页记录实际签署信息。
"@
}
foreach ($entry in $folderNotes.GetEnumerator()) {
    $path = Join-Path $packageFull "$($entry.Key)\目录说明.txt"
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
$excludedFileNames = @('advise.md', 'pi.md', 'mof_room.pgm')
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
    if ($excludedFileNames -contains $File.Name.ToLowerInvariant()) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = '本地部署资料或地图文件' }
    }
    if ($excludedExtensions -contains $File.Extension.ToLowerInvariant()) {
        return [pscustomobject]@{ Include = $false; Relative = $relative; Reason = "排除扩展名 $($File.Extension)" }
    }
    return [pscustomobject]@{ Include = $true; Relative = $relative; Reason = '' }
}

function Get-TrackedSourceFiles {
    param([string]$Prefix, [string]$Root)

    $trackedPaths = @(& git '-c' "safe.directory=$gitRootPath" '-C' $gitRootPath 'ls-files' '--full-name' '--' $Prefix)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git tracked files for $Prefix."
    }
    foreach ($trackedPath in $trackedPaths) {
        if ([string]::IsNullOrWhiteSpace($trackedPath)) {
            continue
        }
        $normalizedPath = $trackedPath.Trim().Replace('/', [IO.Path]::DirectorySeparatorChar)
        $absolutePath = [IO.Path]::GetFullPath((Join-Path $gitRootPath $normalizedPath))
        if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
            throw "Tracked source file is missing from the working tree: $trackedPath"
        }
        $file = Get-Item -LiteralPath $absolutePath -Force
        if ([IO.Path]::GetFullPath($file.DirectoryName).StartsWith([IO.Path]::GetFullPath($Root), [StringComparison]::OrdinalIgnoreCase)) {
            $file
        }
    }
}

foreach ($source in @(
    [pscustomobject]@{ Root = $pythonRoot; Label = 'code/software'; Prefix = 'code/software' },
    [pscustomobject]@{ Root = $robotRoot; Label = 'code/robot'; Prefix = 'code/robot' }
)) {
    # 封装输入来自 Git 跟踪清单，工作树中的未跟踪文件不进入扫描和 ZIP。
    foreach ($file in Get-TrackedSourceFiles -Prefix $source.Prefix -Root $source.Root | Sort-Object FullName) {
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
$sourceFolder = Join-Path $temporaryRoot '代码封装备份'
New-Item -ItemType Directory -Path $sourceFolder -Force | Out-Null
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

封装输入来自两个源码根的 Git 跟踪清单；未跟踪文件不进入扫描、清单或 ZIP。

- 版本与构建缓存：`.git/`、`.pio/`、`build/`、`install/`、`log/`、`dist/`、`codex/`、`.vscode/`。
- Python/前端缓存：`__pycache__/`、`.pytest_cache/`、`.venv/`、`venv/`、`node_modules/`、`*.egg-info/`、`*.pyc`。
- 运行输出与历史运行：`outputs/`、`recovery/`、`backups/`、`codex/`、本地部署归档。
- 历史数据库与设备产物：`*.db`、`*.sqlite*`、`*.mcap`、`*.bin`、`*.elf`、`*.hex`、`*.uf2`、`*.log`。
- 临时与渲染产物：`~$*`、`*~`、`*.tmp`、`*.temp`、`*.bak`、`*.old`、`*.swp`、`*.swo`、render 类目录。
- 重复内层仓库：机器人源码根下的 `code/`；迁移前副本保存在本地 `暂时存放/logs_backup/stage2_20260916/`，不进入 ZIP。
- `start_pi.sh`：含设备登录凭据，仅保留在本地，不进入 Git 或 ZIP。
- `../暂时存放/Report/`、`../暂时存放/material/`、`../暂时存放/PNG/`、`../暂时存放/Reference/` 不属于两个公开源码根，脚本不会扫描或复制它们。
'@
$exclusionSummary = $exclusionSummary.Replace('__EXCLUDED_COUNT__', [string]$exclusionRows.Count)
Set-Content -LiteralPath (Join-Path $sourceFolder '排除规则说明.md') -Value $exclusionSummary -Encoding utf8

"$((Get-FileHash -LiteralPath $sourceZip -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($sourceZip))" |
    Set-Content -LiteralPath (Join-Path $sourceFolder 'SHA256SUMS.txt') -Encoding utf8

$recordTarget = Join-Path $packageFull '07_过程记录\目录归位过程记录.md'
if (Test-Path -LiteralPath $ProcessRecordPath -PathType Leaf) {
    Copy-Item -LiteralPath $ProcessRecordPath -Destination $recordTarget -Force
}

$statusText = @"
提交根目录：项目根目录
作品名称：$workName
代码 Git 工作区：06_源文件
材料、日志和备份：暂时存放
报名身份信息以正式提交材料为准，代码工作区不记录未公开的身份信息。

代码工程封装范围：可移植路径、Web 污染仿真演示和运行说明。设计文档生成与修改不属于脚本功能。
"@
Set-Content -LiteralPath (Join-Path $packageFull '07_过程记录\提交状态说明.txt') -Value $statusText -Encoding utf8

$manifestTarget = Join-Path $packageFull '07_过程记录\文件清单.tsv'
$manifestLines = [Collections.Generic.List[string]]::new()
$manifestLines.Add("SHA256`tBytes`tRelativePath")
foreach ($folder in @('01_作品文件', '02_作品展示', '03_设计文档', '04_作品信息', '05_承诺书', '07_过程记录')) {
    $folderPath = Join-Path $packageFull $folder
    foreach ($file in Get-ChildItem -LiteralPath $folderPath -Recurse -Force -File | Where-Object { $_.FullName -ne $manifestTarget } | Sort-Object FullName) {
        $relative = [IO.Path]::GetRelativePath($packageFull, $file.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $manifestLines.Add("$hash`t$($file.Length)`t$relative")
    }
}
$manifestLines.Add("SEE_GIT`t-`t06_源文件/（Git 工作区，源文件由 Git 索引维护）")
Set-Content -LiteralPath $manifestTarget -Value $manifestLines -Encoding utf8

[pscustomobject]@{
    SubmissionRoot = $packageFull
    GitRoot = $gitRootPath
    TemporaryRoot = $temporaryRoot
    PublicSourceRootCount = 2
    ArchivedSourceFiles = $archiveFiles.Count
    ExcludedFiles = $exclusionRows.Count
    UntrackedFilesSkipped = $true
    SourceZipMiB = [Math]::Round(((Get-Item -LiteralPath $sourceZip).Length / 1MB), 2)
    PackageIdentity = "$SchoolName / $CaptainName"
} | ConvertTo-Json
