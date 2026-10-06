# Resolve explicit build toolchains without assuming a particular workstation layout.
# 解析明确指定的构建工具链，不假设某台电脑的目录布局。

function Resolve-CaptionJavaHome {
    param([string]$Requested)
    $candidate = $Requested
    if ([string]::IsNullOrWhiteSpace($candidate)) { $candidate = $env:JAVA_HOME }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        # PATH must expose a complete JDK, not a JRE or an unrelated java.exe.
        # PATH 必须指向完整 JDK，而不是 JRE 或无关的 java.exe。
        $compiler = Get-Command javac.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $compiler) {
            throw 'JDK missing. Install JDK 21, set JAVA_HOME, add its bin directory to PATH, or pass -JavaHome.'
        }
        $compilerPath = $compiler.Source
        $compilerItem = Get-Item -LiteralPath $compilerPath
        if ($compilerItem.PSObject.Methods.Name -contains 'ResolveLinkTarget') {
            $resolvedCompiler = $compilerItem.ResolveLinkTarget($true)
            if ($resolvedCompiler) { $compilerPath = $resolvedCompiler.FullName }
        }
        $candidate = Split-Path (Split-Path $compilerPath -Parent) -Parent
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "JDK directory not found: $candidate. Pass -JavaHome or set JAVA_HOME to a complete JDK 21 installation."
    }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    foreach ($binary in @('java.exe', 'javac.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolved "bin\$binary") -PathType Leaf)) {
            throw "Complete JDK required: $binary missing under $resolved. Pass -JavaHome or set JAVA_HOME."
        }
    }
    return $resolved
}

function Resolve-CaptionAndroidSdk {
    param([string]$Requested)
    $candidate = $Requested
    if ([string]::IsNullOrWhiteSpace($candidate)) { $candidate = $env:ANDROID_SDK_ROOT }
    if ([string]::IsNullOrWhiteSpace($candidate)) { $candidate = $env:ANDROID_HOME }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw 'Android SDK missing. Install Android SDK Command-line Tools, then sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0". Set ANDROID_SDK_ROOT or pass -AndroidSdkRoot.'
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "Android SDK directory not found: $candidate. Set ANDROID_SDK_ROOT/ANDROID_HOME or pass -AndroidSdkRoot."
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}
