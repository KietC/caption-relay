param(
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { $env:ANDROID_HOME }),
    [string]$BuildToolsVersion = '36.0.0',
    [string]$Platform = 'android-36'
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$jdk = if ($JavaHome) { $JavaHome } else { 'C:\Program Files\Android\openjdk\jdk-21.0.8' }
$sdk = if ($AndroidSdkRoot) { $AndroidSdkRoot } else { 'C:\Program Files (x86)\Android\android-sdk' }
$androidJar = Join-Path $sdk "platforms\$Platform\android.jar"
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
