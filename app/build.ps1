# Compile the separate USB capture helper against an explicit JDK and Android SDK.
# 使用明确指定的 JDK 和 Android SDK 编译独立 USB 采集辅助程序。
param(
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { $env:ANDROID_HOME }),
    [string]$BuildToolsVersion = '36.0.0',
    [string]$Platform = 'android-36'
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
. (Join-Path $taskRoot 'build_tools.ps1')
$jdk = Resolve-CaptionJavaHome -Requested $JavaHome
$sdk = Resolve-CaptionAndroidSdk -Requested $AndroidSdkRoot
$platformName = if ($Platform.StartsWith('android-')) { $Platform } else { "android-$Platform" }
$androidJar = Join-Path $sdk "platforms\$platformName\android.jar"
$captionDexTool = Join-Path $sdk "build-tools\$BuildToolsVersion\d8.bat"
foreach ($captionRequiredTool in @((Join-Path $jdk 'bin\javac.exe'), $androidJar, $captionDexTool)) {
    if (-not (Test-Path -LiteralPath $captionRequiredTool)) { throw "Missing build input: $captionRequiredTool" }
}
$build = Join-Path $taskRoot 'build'
$classes = Join-Path $build 'classes'
New-Item -ItemType Directory -Path $classes -Force | Out-Null
& (Join-Path $jdk 'bin\javac.exe') -encoding UTF-8 --release 8 -cp $androidJar -d $classes (Join-Path $taskRoot 'device\CaptionBridge.java')
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }
$classFiles = @(Get-ChildItem -LiteralPath $classes -Filter '*.class' -Recurse | ForEach-Object FullName)
$oldJavaHome = $env:JAVA_HOME
try {
    $env:JAVA_HOME = $jdk
    & $captionDexTool --min-api 26 --lib $androidJar --output (Join-Path $build 'caption-bridge.jar') @classFiles
    if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }
} finally {
    $env:JAVA_HOME = $oldJavaHome
}
Get-Item -LiteralPath (Join-Path $build 'caption-bridge.jar') | Select-Object FullName,Length,LastWriteTime
