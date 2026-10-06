# Create an isolated build environment, use pinned build dependencies, and package only explicit inputs.
# 创建隔离构建环境，使用固定的构建依赖，只打包明确列出的输入。
param(
    [string]$Python = '',
    [string]$Apk = '',
    [string]$AdbDirectory = ''
)
$ErrorActionPreference = 'Stop'
$desktopDirectory = $PSScriptRoot
$buildPython = Join-Path $desktopDirectory '.buildenv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    if (-not $Python) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { $pythonCommand = Get-Command python -ErrorAction SilentlyContinue }
        if (-not $pythonCommand) { throw 'Python is not on PATH. Install Python 3.11 x64 or pass -Python with its full path.' }
        $Python = $pythonCommand.Source
    }
    & $Python -m venv (Join-Path $desktopDirectory '.buildenv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated build environment.' }
}
if (-not $AdbDirectory) {
    $adbCandidates = @()
    if ($env:ANDROID_SDK_ROOT) { $adbCandidates += (Join-Path $env:ANDROID_SDK_ROOT 'platform-tools') }
    if ($env:ANDROID_HOME) { $adbCandidates += (Join-Path $env:ANDROID_HOME 'platform-tools') }
    $repoDirectory = Split-Path (Split-Path $desktopDirectory -Parent) -Parent
    $adbCandidates += (Join-Path $repoDirectory 'tools')
    $adbCommand = Get-Command adb.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($adbCommand) { $adbCandidates += (Split-Path $adbCommand.Source -Parent) }
    $AdbDirectory = $adbCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'adb.exe') } | Select-Object -First 1
    if (-not $AdbDirectory) { throw 'ADB build input missing. Set ANDROID_SDK_ROOT / ANDROID_HOME or pass -AdbDirectory.' }
}
& $buildPython -m pip install -r (Join-Path $desktopDirectory 'build-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install pinned build requirements.' }
$buildArguments = @((Join-Path $desktopDirectory 'build_portable.py'), '--adb-dir', $AdbDirectory)
if ($Apk) { $buildArguments += @('--apk', $Apk) }
& $buildPython @buildArguments
if ($LASTEXITCODE -ne 0) { throw 'Portable build or verification failed; inspect desktop\builds logs.' }
