# Run Android-independent core tests with disposable class output; no phone or signing key is used.
# 使用一次性 class 输出运行不依赖 Android 的核心测试，不使用手机或签名密钥。
param([string]$JavaHome)
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'build_tools.ps1')
$jdk = Resolve-CaptionJavaHome -Requested $JavaHome
foreach ($binary in @('java.exe', 'javac.exe')) {
    $toolPath = Join-Path $jdk "bin\$binary"
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Required JDK tool missing: $toolPath. Select a complete JDK with -JavaHome." }
}
$testClasses = Join-Path $PSScriptRoot 'build\test-classes'
New-Item -ItemType Directory -Path $testClasses -Force | Out-Null
$names = @('RetryGate','PairingPolicy','PendingBatch','AckVerifier','LatestSlot','LiveRetryGate','LiveAck')
$testSources = @($names | ForEach-Object {
    $source = Join-Path $PSScriptRoot "src\org\captionrelay\bridge\$_.java"
    if (Test-Path -LiteralPath $source -PathType Leaf) { $source }
    Join-Path $PSScriptRoot "tests\org\captionrelay\bridge\${_}Test.java"
})
& (Join-Path $jdk 'bin\javac.exe') -encoding UTF-8 --release 8 -d $testClasses @testSources
if ($LASTEXITCODE -ne 0) { throw 'Android core tests failed to compile' }
foreach($name in $names) {
    & (Join-Path $jdk 'bin\java.exe') -cp $testClasses "org.captionrelay.bridge.${name}Test"
    if ($LASTEXITCODE -ne 0) { throw "$name tests failed" }
}
