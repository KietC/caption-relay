param(
    [string]$JavaHome,
    [string]$AndroidSdkRoot,
    [string]$BuildToolsVersion = '36.0.0',
    [string]$Platform = '36',
    [string]$KeystorePath = '',
    [string]$SigningPasswordFile = '',
    [switch]$AllowNewSigningKey
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$jdk = $JavaHome
if ([string]::IsNullOrWhiteSpace($jdk)) { $jdk = $env:JAVA_HOME }
if ([string]::IsNullOrWhiteSpace($jdk)) { $jdk = 'C:\Program Files\Android\openjdk\jdk-21.0.8' }
$sdk = $AndroidSdkRoot
if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = $env:ANDROID_SDK_ROOT }
if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = $env:ANDROID_HOME }
if ([string]::IsNullOrWhiteSpace($sdk)) { $sdk = 'C:\Program Files (x86)\Android\android-sdk' }
if (-not (Test-Path -LiteralPath $jdk -PathType Container)) { throw "JDK directory not found: $jdk. Pass -JavaHome or set JAVA_HOME (JDK21 recommended)." }
if (-not (Test-Path -LiteralPath $sdk -PathType Container)) { throw "Android SDK directory not found: $sdk. Pass -AndroidSdkRoot or set ANDROID_SDK_ROOT/ANDROID_HOME." }
$jdk = (Resolve-Path -LiteralPath $jdk).Path
$sdk = (Resolve-Path -LiteralPath $sdk).Path
$toolsDir = Join-Path $sdk "build-tools\$BuildToolsVersion"
$platformName = if ($Platform.StartsWith('android-')) { $Platform } else { "android-$Platform" }
$androidJar = Join-Path $sdk "platforms\$platformName\android.jar"
foreach ($binary in @('java.exe', 'javac.exe', 'keytool.exe')) {
    $toolPath = Join-Path $jdk "bin\$binary"
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Required JDK tool missing: $toolPath. Select a complete JDK with -JavaHome." }
}
foreach ($binary in @('aapt.exe', 'd8.bat', 'zipalign.exe', 'apksigner.bat')) {
    $toolPath = Join-Path $toolsDir $binary
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Android build tool missing: $toolPath. Install SDK Build-Tools $BuildToolsVersion or pass -BuildToolsVersion." }
}
if (-not (Test-Path -LiteralPath $androidJar -PathType Leaf)) { throw "Android platform jar missing: $androidJar. Install SDK platform $Platform or pass -Platform (36 recommended)." }
$buildRoot = Join-Path $taskRoot 'build'
$stage = Join-Path $buildRoot ('work-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$classes = Join-Path $stage 'classes'
$dex = Join-Path $stage 'dex'
$generated = Join-Path $stage 'generated'
$signing = Join-Path $taskRoot 'signing'
New-Item -ItemType Directory -Path $classes,$dex,$generated -Force | Out-Null
$unsigned = Join-Path $stage 'unsigned.apk'
$aligned = Join-Path $stage 'aligned.apk'
$apk = Join-Path $buildRoot 'CaptionRelayCaptionBridge.apk'
$stagedApk = Join-Path $stage 'CaptionRelayCaptionBridge.apk'
$keystore = if ($KeystorePath) { $KeystorePath } else { Join-Path $signing 'caption-release.jks' }
$passwordFile = if ($SigningPasswordFile) { $SigningPasswordFile } else { Join-Path $signing 'password.txt' }
if ((Test-Path -LiteralPath $keystore) -and -not (Test-Path -LiteralPath $passwordFile -PathType Leaf)) {
    throw 'The existing signing key has no password.txt. Restore the private signing folder; do not replace the key for an installed app upgrade.'
}
if (-not (Test-Path -LiteralPath $keystore)) {
    if (-not $AllowNewSigningKey) {
        throw 'Original signing key is required for an installed-app upgrade. Pass -KeystorePath and -SigningPasswordFile. New keys require explicit -AllowNewSigningKey for a new installation only.'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $keystore),(Split-Path -Parent $passwordFile) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $passwordFile)) {
        $random = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($random)
        [IO.File]::WriteAllText($passwordFile, [Convert]::ToBase64String($random), (New-Object Text.UTF8Encoding($false)))
    }
    & (Join-Path $jdk 'bin\keytool.exe') -genkeypair -keystore $keystore -storetype JKS -storepass:file $passwordFile -keypass:file $passwordFile -alias caption -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=CaptionRelay Caption Bridge,OU=Local Tools,O=CaptionRelay,C=CN' -noprompt
    if ($LASTEXITCODE -ne 0) { throw 'keytool failed' }
}
& (Join-Path $toolsDir 'aapt.exe') package -f -M (Join-Path $taskRoot 'AndroidManifest.xml') -S (Join-Path $taskRoot 'res') -I $androidJar -F $unsigned -J $generated
if ($LASTEXITCODE -ne 0) { throw 'aapt package failed' }
$sources = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'src') -Filter '*.java' -Recurse | ForEach-Object FullName)
& (Join-Path $jdk 'bin\javac.exe') -encoding UTF-8 --release 8 -cp $androidJar -d $classes @sources
if ($LASTEXITCODE -ne 0) { throw 'javac failed' }
$classFiles = @(Get-ChildItem -LiteralPath $classes -Filter '*.class' -Recurse | ForEach-Object FullName)
$previousJavaHome = $env:JAVA_HOME
try {
    $env:JAVA_HOME = $jdk
    & (Join-Path $toolsDir 'd8.bat') --min-api 26 --lib $androidJar --output $dex @classFiles
    if ($LASTEXITCODE -ne 0) { throw 'd8 failed' }
    Push-Location -LiteralPath $dex
    try { & (Join-Path $toolsDir 'aapt.exe') add $unsigned 'classes.dex' }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'aapt add dex failed' }
    & (Join-Path $toolsDir 'zipalign.exe') -f -p 4 $unsigned $aligned
    if ($LASTEXITCODE -ne 0) { throw 'zipalign failed' }
    & (Join-Path $toolsDir 'apksigner.bat') sign --ks $keystore --ks-key-alias caption --ks-pass "file:$passwordFile" --v4-signing-enabled false --out $stagedApk $aligned
    if ($LASTEXITCODE -ne 0) { throw 'apksigner failed' }
    & (Join-Path $toolsDir 'apksigner.bat') verify --verbose $stagedApk
    if ($LASTEXITCODE -ne 0) { throw 'APK verification failed' }
} finally { $env:JAVA_HOME = $previousJavaHome }
Copy-Item -LiteralPath $stagedApk -Destination $apk -Force
Get-Item -LiteralPath $apk | Select-Object FullName,Length,LastWriteTime
Get-FileHash -LiteralPath $apk -Algorithm SHA256
