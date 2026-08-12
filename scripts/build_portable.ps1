param(
    [string]$Python = "C:\tmp\qinchecker_venv\Scripts\python.exe",
    [switch]$RefreshBrowser,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$browserRoot = Join-Path $projectRoot "build\playwright-browsers"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "找不到 Python：$Python"
}

Push-Location $projectRoot
try {
    & $Python -m pip install ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "无法安装打包依赖" }

    $hasBrowser = Test-Path -LiteralPath $browserRoot -PathType Container
    if ($hasBrowser) {
        $hasBrowser = (Get-ChildItem -LiteralPath $browserRoot -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue).Count -gt 0
    }
    if (-not $SkipBrowser -and ($RefreshBrowser -or -not $hasBrowser)) {
        if (Test-Path -LiteralPath $browserRoot) {
            Remove-Item -LiteralPath $browserRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $browserRoot | Out-Null
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
        & $Python -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "无法下载 Playwright Chromium" }
    }

    $pyInstallerArgs = @("--noconfirm", "--clean", "--onedir", "--windowed", "--name", "QinChecker",
        "--paths", (Join-Path $projectRoot "src"),
        "--add-data", "$projectRoot\config;config",
        "--collect-all", "playwright",
        "--collect-all", "openpyxl")
    if (-not $SkipBrowser) {
        $pyInstallerArgs += @("--add-data", "$browserRoot;browsers")
    }
    $pyInstallerArgs += (Join-Path $projectRoot "src\qinchecker\app.py")
    & $Python -m PyInstaller @pyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

    # Windows PowerShell 5.1 可能以系统代码页读取无 BOM 的脚本；用码点构造中文文件名。
    $helpName = "$([char]0x4F7F)$([char]0x7528)$([char]0x5E2E)$([char]0x52A9).txt"
    Copy-Item -LiteralPath (Join-Path $projectRoot $helpName) `
        -Destination (Join-Path $projectRoot "dist\QinChecker\$helpName") -Force

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $archivePath = Join-Path $projectRoot "QinChecker_$timestamp.zip"
    Compress-Archive -LiteralPath (Join-Path $projectRoot "dist\QinChecker") `
        -DestinationPath $archivePath -CompressionLevel Optimal

    Write-Output "绿色版已生成：$(Join-Path $projectRoot 'dist\QinChecker\QinChecker.exe')"
    Write-Output "时间命名压缩包已生成：$archivePath"
}
finally {
    Pop-Location
}
