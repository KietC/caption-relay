param([string]$JavaHome)
$ErrorActionPreference = 'Stop'
$jdk = $JavaHome
if ([string]::IsNullOrWhiteSpace($jdk)) { $jdk = $env:JAVA_HOME }
if ([string]::IsNullOrWhiteSpace($jdk)) { $jdk = 'C:\Program Files\Android\openjdk\jdk-21.0.8' }
if (-not (Test-Path -LiteralPath $jdk -PathType Container)) { throw "JDK directory not found: $jdk. Pass -JavaHome or set JAVA_HOME." }
$jdk = (Resolve-Path -LiteralPath $jdk).Path
foreach ($binary in @('java.exe', 'javac.exe')) {
    $toolPath = Join-Path $jdk "bin\$binary"
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Required JDK tool missing: $toolPath. Select a complete JDK with -JavaHome." }
}
$testClasses = Join-Path $PSScriptRoot 'build\test-classes'
New-Item -ItemType Directory -Path $testClasses -Force | Out-Null
$names = @('RetryGate','PairingPolicy','PendingBatch','AckVerifier')
$testSources = @($names | ForEach-Object {
    Join-Path $PSScriptRoot "src\org\captionrelay\bridge\$_.java"
    Join-Path $PSScriptRoot "tests\org\captionrelay\bridge\${_}Test.java"
})
& (Join-Path $jdk 'bin\javac.exe') -encoding UTF-8 --release 8 -d $testClasses @testSources
if ($LASTEXITCODE -ne 0) { throw 'Android core tests failed to compile' }
foreach($name in $names) {
    & (Join-Path $jdk 'bin\java.exe') -cp $testClasses "org.captionrelay.bridge.${name}Test"
    if ($LASTEXITCODE -ne 0) { throw "$name tests failed" }
}
