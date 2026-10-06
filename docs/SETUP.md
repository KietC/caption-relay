# Setup: from a clean clone to real captions / 从干净源码到真实字幕

## English

Follow the numbered phases in order. Commands below use Windows PowerShell and start from the repository root unless a phase explicitly changes directory. Paths are generic examples: replace JDK/SDK locations with the directories on **your** computer. No example contains a real pairing secret or production endpoint.

At the beginning of **each new PowerShell terminal**, set Python's UTF-8 mode before running its commands:

```powershell
$env:PYTHONUTF8 = '1'
```

This changes only the current terminal and its child Python processes. It avoids Windows legacy-encoding/GBK failures or garbled bilingual CLI output; it does not change the system locale, console code page, or machine environment. You may instead invoke a command as `python -X utf8 ...` if you do not want the environment variable. Keep scripts and Markdown saved as UTF-8.

### 1. Check the phone before building anything

1. On your own compatible Xiaomi phone, enable the phone's native bilingual caption/call-translation feature.
2. Use non-sensitive speech or media that the native feature supports.
3. Confirm that source text and translated text both visibly update. The bridge does not enable recognition or translation for you.
4. Keep the caption overlay/window visible. Unsupported UI variants may not expose the nodes the reader expects.
5. Read the supported node selection in `app/mobile_caption/android/src/org/captionrelay/bridge/CaptionAccessibilityService.java` before assuming another phone/ROM is supported.

**Checkpoint:** native source and translated captions appear. If not, fix the phone-native feature first; an APK cannot fix absent native captions. This is an Android-to-Windows project, not an iPhone setup guide.

### 2. Prepare the toolchain

| Tool | Build baseline | Obtain from | Check |
| --- | --- | --- | --- |
| Git | A maintained Windows version | [Git for Windows](https://gitforwindows.org/) | `git --version` |
| Python | **3.11 x64** for the portable build | [Python.org](https://www.python.org/downloads/windows/) | `py -3.11 -V` |
| JDK | **21**, complete JDK including `javac` and `keytool` | [Temurin](https://adoptium.net/temurin/releases/?version=21) or another trusted JDK vendor | `java -version`, `javac -version` |
| Android SDK | Platform **36**, Build-Tools **36.0.0**, Platform-Tools | [Android SDK Manager](https://developer.android.com/tools/sdkmanager) | Required files below |
| cloudflared | Official Windows x64 executable | [Cloudflare downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) | `cloudflared.exe --version` |

The APK builder uses SDK command-line tools directly; it does not require Gradle. Install Android Studio/SDK Manager, or lay out the official command-line tools under your SDK root. A typical command-line layout is `C:\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat`; an accidental extra `cmdline-tools\cmdline-tools` directory prevents that path from working.

If using `sdkmanager`, install the exact inputs and review/accept the SDK license prompts:

```powershell
$captionSdk = 'C:\Android\Sdk'
$captionJdk = 'C:\Tools\jdk-21'
$env:JAVA_HOME = $captionJdk
$captionSdkPackages = @('platform-tools', 'platforms;android-36', 'build-tools;36.0.0')
& "$captionSdk\cmdline-tools\latest\bin\sdkmanager.bat" "--sdk_root=$captionSdk" @captionSdkPackages
& "$captionSdk\cmdline-tools\latest\bin\sdkmanager.bat" --sdk_root=$captionSdk --licenses
& "$captionJdk\bin\java.exe" -version
& "$captionJdk\bin\javac.exe" -version
```

Check these files before continuing:

```powershell
$captionRequired = @(
  "$captionJdk\bin\java.exe", "$captionJdk\bin\javac.exe", "$captionJdk\bin\keytool.exe",
  "$captionSdk\platforms\android-36\android.jar",
  "$captionSdk\build-tools\36.0.0\aapt.exe", "$captionSdk\build-tools\36.0.0\d8.bat",
  "$captionSdk\build-tools\36.0.0\zipalign.exe", "$captionSdk\build-tools\36.0.0\apksigner.bat",
  "$captionSdk\platform-tools\adb.exe"
)
$captionRequired | ForEach-Object { [PSCustomObject]@{Path=$_; Exists=Test-Path -LiteralPath $_} }
```

**Checkpoint:** all `Exists` values are `True`. A JRE is insufficient. The SDK root is the directory containing `platforms`, `build-tools`, and `platform-tools`, not one of those child directories. These variables are examples for this shell; no machine-wide environment change is required.

### 3. Clone and create an isolated Python environment

Start in the parent directory where you want the clone. If you already cloned the repository, skip `git clone` and enter your existing repository root before creating `.venv`.

```powershell
git clone https://github.com/KietC/caption-relay.git
Set-Location -LiteralPath .\caption-relay
py -3.11 -m venv .venv
$captionPython = (Resolve-Path -LiteralPath .\.venv\Scripts\python.exe).Path
& $captionPython -m pip install --upgrade pip
& $captionPython -m pip install -r .\app\mobile_caption\requirements.txt
& $captionPython -c "import tkinter, cryptography, psutil; print('Python dependencies OK')"
& $captionPython .\tools\check_environment.py --java-home $captionJdk --android-sdk $captionSdk --require-build
```

Use the official Python Windows distribution with Tcl/Tk. Keep using `$captionPython` or the explicit `.venv\Scripts\python.exe` path; a different `python` on `PATH` can silently select the wrong environment. You do not need to activate the virtual environment. These `$caption...` variables last only in this terminal: after opening a new terminal, enter the clone and set the interpreter/JDK/SDK/private-file variables again before copying later commands.

**Checkpoint:** dependencies import successfully and the build-input check is `ok: true`. Without `--require-build`, the diagnostic only requires Python dependencies; `apk`, `usb_jar`, and `cloudflared` fields may legitimately remain false before later phases. Do not launch the live receiver yet.

### 4. Run synthetic Python and pure-JVM tests first

The tests use synthetic data and disposable canonical temporary directories. They do not certify the phone's permissions or live network. Run the safe wrappers from the repository root:

```powershell
& $captionPython .\tools\run_checks.py
if ($LASTEXITCODE -ne 0) { throw 'Python checks failed; fix before proceeding.' }
& $captionPython .\tools\test_android_core.py --java-home $captionJdk --android-jar "$captionSdk\platforms\android-36\android.jar"
if ($LASTEXITCODE -ne 0) { throw 'Java checks failed; fix before proceeding.' }
```

If script execution is blocked, inspect your policy. For an owner-reviewed script, a temporary process-only policy can be used: `Set-ExecutionPolicy -Scope Process Bypass`. Do not weaken the machine-wide policy as a troubleshooting shortcut.

`run_checks.py` resolves temporary paths, deduplicates tests, parses Python syntax, and refuses real-device/tunnel subprocesses and external/normal-service connections. It succeeds only when there are no failures, errors, or skips. Optional `--temp-parent <existing-directory>` selects a private temp parent; optional `--report <file-outside-repository>` writes metadata only. `test_android_core.py` runs seven JVM groups and, with `--android-jar`, compiles the Android adapter without signing or installing it. To run JVM groups without an SDK, omit `--android-jar`.

**Checkpoint:** the Python result is `ok: true`, with zero failures/errors/skips; the JVM groups and adapter compile complete. Canonical temporary paths avoid Windows 8.3 alias mismatches in tests. Mixed short/long paths in real migration data still need care; adjusting the test environment is not a general compatibility fix.

### 5. Build the new-install Android APK and optional USB reader

For your first public-package installation, create a **new private signing identity** outside the clone. The script's explicit `-AllowNewSigningKey` is only for a new installation. Save the generated key/password securely; future updates of this public app require this same key.

```powershell
$captionPrivate = Join-Path $env:LOCALAPPDATA 'CaptionRelay-private'
New-Item -ItemType Directory -Path $captionPrivate -Force | Out-Null
$captionKey = Join-Path $captionPrivate 'caption-release.jks'
$captionPasswordFile = Join-Path $captionPrivate 'signing-password.txt'
Push-Location -LiteralPath .\app
try {
  $captionBuildOptions = @{
    JavaHome = $captionJdk
    AndroidSdkRoot = $captionSdk
    KeystorePath = $captionKey
    SigningPasswordFile = $captionPasswordFile
  }
  .\mobile_caption\android\build.ps1 @captionBuildOptions -AllowNewSigningKey
  .\build.ps1 -JavaHome $captionJdk -AndroidSdkRoot $captionSdk
} finally { Pop-Location }
Test-Path -LiteralPath .\app\mobile_caption\android\build\CaptionRelayCaptionBridge.apk
Test-Path -LiteralPath .\app\build\caption-bridge.jar
```

The APK pipeline compiles Java → DEX → packages resources → aligns → signs → verifies. The second command builds the optional USB reader JAR required by the full portable bundle. Neither command starts the receiver or enables phone permissions.

**Checkpoint:** the APK verifies successfully and both files exist. Do not publish your key/password. A public namespace change does not migrate a different private app's installation; treat `org.captionrelay.bridge` as a new independent app and preserve the old app/state separately.

### 6. Install, enable accessibility, and confirm local preview

With USB:

1. Enable developer options and USB debugging on the phone.
2. Connect it, unlock it, and approve your own computer's RSA fingerprint prompt.
3. Check the list; exactly one authorized device should be present unless you deliberately select a serial.
4. Install without uninstalling an existing same-package installation.

```powershell
& "$captionSdk\platform-tools\adb.exe" devices
$captionApk = '.\app\mobile_caption\android\build\CaptionRelayCaptionBridge.apk'
& "$captionSdk\platform-tools\adb.exe" install --no-incremental -r $captionApk
```

Without USB, privately transfer **your own built APK** to the phone and install it through the Android package installer. The code does not provide an OTA installer. Never obtain binaries from an untrusted mirror.

On the phone:

1. Open Caption Relay.
2. Tap **Accessibility / 开启无障碍权限** and explicitly enable its service.
3. If Android blocks a sideloaded app, inspect App info → menu → **Allow restricted settings / 允许受限设置**, then return to accessibility. Labels vary by OS; approve only the app you built/trust.
4. Ensure battery/background-data rules do not immediately suspend the service; there is no guarantee that every vendor keeps it alive.
5. Enable the phone-native caption UI, then tap **Local preview / 仅在手机预览**.
6. Speak a harmless fresh sentence and confirm both text roles in the app preview.

**Checkpoint:** the app sees source and translated nodes. Local preview intentionally does not start encrypted network transmission. If native text appears but local preview is empty, investigate accessibility/nodes before networking.

### 7. Install the official tunnel executable locally

Download the Windows x64 cloudflared executable from the official source, validate available publisher/hash information, and rename it `cloudflared.exe`. Copy it to the exact location used by the controller:

```powershell
New-Item -ItemType Directory -Path .\app\mobile_caption\tools -Force | Out-Null
Copy-Item -LiteralPath 'C:\Downloads\cloudflared-windows-amd64.exe' -Destination .\app\mobile_caption\tools\cloudflared.exe
& .\app\mobile_caption\tools\cloudflared.exe --version
$env:PATH = "$captionSdk\platform-tools;" + $env:PATH
```

The download path is an example; use the actual file you downloaded. The source repository does not contain this executable. The included controller creates a temporary Quick Tunnel; stopping/recreating it can change its hostname. The phone does not need your PC's private LAN IP when using this path.

**Checkpoint:** cloudflared runs and ADB is available in this shell. The controller will own the tunnel; do not separately start another copy for the same setup.

### 8. Pair and start — choose one route

Run the following routes from `app`:

```powershell
Set-Location -LiteralPath .\app
```

#### Route A: first pairing with USB

```powershell
& $captionPython .\mobile_caption_control.py tunnel-start
& $captionPython .\mobile_caption_control.py pair-usb
& $captionPython .\mobile_caption_control.py start
& $captionPython .\mobile_caption_control.py status
```

The controller creates a random device ID, token, and 32-byte AES key on first configuration. `pair-usb` writes the private configuration into the app, then reloads it. `start` starts the phone sender and local supervised receiver/window. For multiple phones add `--serial <your-authorized-device>`; never paste device identifiers into a public issue.

#### Route B: first pairing without USB

```powershell
$captionPairingFile = Join-Path $captionPrivate 'pairing-code.txt'
& $captionPython .\mobile_caption_control.py pairing-code --file $captionPairingFile
```

1. This command starts the tunnel and saves a private code beginning with `CRCP1:`. Transfer it through a private channel to your phone; do not paste it into GitHub, a public chat, or a screenshot.
2. Open the phone app → **Import pairing code / 粘贴配对码** → paste the complete code → check/import.
3. Verify that the full endpoint appears, ending in `/v1/captions`.
4. Tap **Reload / 重新加载配对配置**, then **Start / 开始加密发送**. Importing a pairing code does not enable accessibility or start transmission for you.
5. Start the PC receiver:

```powershell
& $captionPython .\mobile_caption_control.py start --phone-configured
& $captionPython .\mobile_caption_control.py status
```

`--phone-configured` is your statement that you manually updated the phone. It is **not** an authentication bypass or proof of connectivity. The controller still requires a fresh authenticated packet.

#### Existing pairing, new tunnel address, no USB

Do not generate a replacement identity/key. Start the tunnel, inspect `status` locally, copy the **current full** `https://<host>/v1/captions` endpoint to the phone, tap **Save endpoint → Reload → Start**, then run `start --phone-configured` on PC. Keeping the same device ID/key preserves the stream, sequence, and pending queue. A bare hostname, `/`, a PC LAN address, or a previous tunnel hostname is incorrect for this route.

**Checkpoint:** PC status reflects a new authenticated phone packet from this startup. The initial wait is bounded; a retrying phone can take longer than one startup attempt. Use [Troubleshooting](TROUBLESHOOTING.md) rather than deleting pairing state or creating another receiver.

### 9. Accept a real bilingual session

1. Confirm phone accessibility is connected and capture/sending are enabled.
2. Speak a new harmless sentence supported by the native caption feature.
3. Observe fresh source and translated text on the phone and in the desktop window.
4. Read the generated session Markdown locally under `app/output/caption_viewer/session_*/captions.md`; confirm it grows with this new session.
5. Confirm live preview updates without waiting for old reliable backlog and reliable history subsequently catches up. The live endpoint is derived as `/v1/captions/live`; do not manually configure the phone base endpoint to that path.
6. Restart only through the controller if you want a recovery check. Run the same fresh-text acceptance after restarting.

**Checkpoint:** both current display and saved history are verified. A heartbeat proves transport only. The app's diagnostic test explicitly writes synthetic history and must not be called proof of real recognition. Do not run `verify_mobile_recovery.py --live` casually: it changes phone networking and restarts controlled processes.

### 10. Daily start, stop, and GUI

From `app`, open the manager if desired:

```powershell
& $captionPython .\mobile_caption\desktop\entrypoint.py
```

Use its Start/Stop/Status controls or the equivalent controller commands. After tunnel recreation, repeat the phone endpoint update. Do not run a second receiver on port 18765. To stop completely:

```powershell
& $captionPython .\mobile_caption_control.py stop
& $captionPython .\mobile_caption_control.py status
```

Without USB, also tap **Stop** on the phone. Closing the manager alone does not stop background services; closing only the caption window is not a full mobile-service stop. Leave queue/history databases intact. Back up private data outside Git; see [Migration and upgrades](MIGRATION_AND_UPGRADES.md).

### 11. Optional: build a portable Windows folder

Complete the tests, APK, USB JAR, cloudflared, and ADB steps first. Use Python 3.11 x64 for this build:

```powershell
# Run from app.
$captionPortableOptions = @{
  Python = $captionPython
  AdbDirectory = "$captionSdk\platform-tools"
  Apk = '.\mobile_caption\android\build\CaptionRelayCaptionBridge.apk'
}
.\mobile_caption\desktop\build.ps1 @captionPortableOptions
```

Before the build, you can run `& $captionPython ..\tools\check_environment.py --android-sdk $captionSdk --java-home $captionJdk --require-build --require-portable` from `app`. Missing downloaded/built files are errors at this stage.

The build creates a separate build environment, installs pinned build dependencies, runs PyInstaller, and checks the result. Inspect `app/mobile_caption/desktop/builds/latest-build.json` locally for the output location. Extract and move the **whole** one-folder distribution. `CaptionRelayCaptions.exe` alone is insufficient: Python/Tk runtime, scripts, USB tools, APK, and tunnel client belong to the complete folder.

Packaging self-checks are separate from real-phone acceptance. Do not publish your locally generated runtime data alongside the build. Do not treat the APK download path as an OTA system; signed future APKs require explicit distribution and same-signature installation.

---

## 简体中文

严格按下面阶段操作。命令使用 Windows PowerShell；除非明确进入 `app`，默认位于仓库根。JDK/SDK 路径只是通用示例，请替换为自己的安装位置。示例不包含真实配对密钥或生产地址。

**每次新开 PowerShell 终端**，先开启当前终端的 Python UTF-8 模式，再执行 Python 命令：

```powershell
$env:PYTHONUTF8 = '1'
```

只影响当前终端及它启动的 Python 子进程，避免 Windows 旧编码/GBK 导致双语 CLI 乱码或编码报错，不修改系统区域、控制台代码页或全局环境。也可单条用 `python -X utf8 ...`。脚本及 Markdown 文件保持 UTF-8。

### 1. 先检查手机原生字幕

1. 在自己的兼容小米手机上打开原生双语实时字幕/通话翻译。
2. 使用不敏感的语音或原生功能支持的媒体。
3. 确认原文、译文都能在手机上更新。本项目不会替你开启识别、翻译。
4. 保持字幕窗口可见；其它界面变体可能没有兼容无障碍节点。
5. 支持选择器在 `app/mobile_caption/android/src/org/captionrelay/bridge/CaptionAccessibilityService.java`，不能仅凭 Android 版本推断其它品牌/ROM 都能用。

**检查点：** 手机自身原文和译文可见。否则先解决原生功能，安装字幕桥不能让不存在的原生字幕出现。本项目不是 iPhone 配置教程。

### 2. 准备工具

| 工具 | 构建基线 | 官方来源 | 检查 |
| --- | --- | --- | --- |
| Git | 维护中的 Windows 版本 | [Git for Windows](https://gitforwindows.org/) | `git --version` |
| Python | 便携构建使用 **3.11 x64** | [Python.org](https://www.python.org/downloads/windows/) | `py -3.11 -V` |
| JDK | **21**，必须包含 `javac`、`keytool` | [Temurin](https://adoptium.net/temurin/releases/?version=21) 或其它可信 JDK | `java -version`、`javac -version` |
| Android SDK | platform **36**、Build-Tools **36.0.0**、Platform-Tools | [Android SDK Manager](https://developer.android.com/tools/sdkmanager) | 检查文件 |
| cloudflared | 官方 Windows x64 程序 | [Cloudflare 下载](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) | `cloudflared.exe --version` |

APK 使用 SDK 命令行工具构建，不依赖 Gradle。可用 Android Studio 的 SDK Manager，或把官方命令行工具正确放在 SDK 根目录。通常是 `C:\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat`；多套一层 `cmdline-tools\cmdline-tools` 会找不到。

按英文第 2 步命令设置当前终端的 `$captionSdk`、`$captionJdk`，安装 `platform-tools`、`platforms;android-36`、`build-tools;36.0.0`，阅读并接受 SDK 许可提示。运行文件检查列表。

**检查点：** 所有文件 `Exists=True`。JRE 不能代替 JDK。SDK 根目录必须包含 `platforms`、`build-tools`、`platform-tools`，不能指向其中一个子目录。不必修改全局环境变量。

### 3. 克隆、隔离 Python

从准备存放项目的父目录开始。已有克隆则跳过 `git clone`，进入现有仓库根再创建 `.venv`。

按英文第 3 步依次克隆、进入目录、创建 `.venv`、设置 `$captionPython`，安装 requirements，验证 `tkinter`、`cryptography`、`psutil`，运行 `check_environment.py --java-home ... --android-sdk ... --require-build`。使用带 Tcl/Tk 的官方 Windows Python。

后续继续使用 `$captionPython` 或 `.venv\Scripts\python.exe`，不要让 PATH 上另一个 Python 混入。无需 activate。变量只在当前终端有效，开新终端后先进入仓库，重新设置 Python/JDK/SDK/私密文件变量再执行后面的命令。**检查点：** 依赖导入成功，构建输入检查 `ok: true`。不加 `--require-build` 时只要求 Python 依赖；尚未构建/下载前 APK、JAR、cloudflared 为 false 正常。先别启动实际接收器。

### 4. 先完成 Python 和 JVM 测试

在仓库根运行：

```powershell
& $captionPython .\tools\run_checks.py
if ($LASTEXITCODE -ne 0) { throw 'Python checks failed' }
& $captionPython .\tools\test_android_core.py --java-home $captionJdk --android-jar "$captionSdk\platforms\android-36\android.jar"
if ($LASTEXITCODE -ne 0) { throw 'Java checks failed' }
```

`run_checks.py` 自动规范临时路径、去重测试、检查 Python 语法，并拒绝真实手机/隧道进程、外网或正常服务端口连接；失败、错误或 skip 均不能成功。可用 `--temp-parent <已存在目录>` 选临时父目录，`--report <仓库外文件>` 写纯元数据。Java helper 运行七组 JVM，带 android.jar 则仅编译适配器，不签名/安装；没有 SDK 时可不带 `--android-jar`。

脚本策略阻止运行时先检查。本人审核后的脚本可在当前终端设置 `Set-ExecutionPolicy -Scope Process Bypass`，不要为了排障放宽整台机器的策略。

**检查点：** Python `ok: true`，失败/错误/skip 均为零；JVM 和适配器编译成功。Windows TEMP 的 8.3 别名可能触发路径断言不一致；测试时规范长路径只是规避测试环境差异，不代表真实迁移的所有短/长路径混用都已修好。

### 5. 新安装签名及构建

首次安装公开包时，在仓库外建立自己的签名，例如 `%LOCALAPPDATA%\CaptionRelay-private`。英文第 5 步命令会通过明确的 `-AllowNewSigningKey` 生成新安装签名，然后编译 APK 和可选 USB JAR。

保存生成的 JKS 与密码文件，后续同一公开 App 更新必须继续使用这套密钥。不要提交 Git。构建步骤为 Java → DEX → 打包 → 对齐 → 签名 → 验证；USB JAR 用于完整便携包。

**检查点：** APK 验证成功，`CaptionRelayCaptionBridge.apk` 和 `caption-bridge.jar` 存在。公开包 `org.captionrelay.bridge` 与不同私有包是独立安装，不能当作覆盖迁移；旧 App 及未发队列应另行保留。

### 6. 安装、权限、本地预览

有 USB：开启开发者选项/USB 调试，解锁并批准自己电脑的 RSA 指纹，`adb devices` 显示一个授权设备，再按英文第 6 步 `adb install --no-incremental -r` 安装。多手机时要明确选择。

无 USB：私下传输自己构建的 APK，通过系统安装器安装。未提供 OTA，不从不可信镜像下载。

手机操作顺序：

1. 打开 Caption Relay。
2. 点“开启无障碍权限 / Accessibility”，由本人明确启用服务。
3. 若系统阻止侧载 App，检查应用信息 → 菜单 → 允许受限设置，再回无障碍页面；具体名称随系统不同，只批准自己可信 App。
4. 检查电池及后台数据限制，避免服务立刻被挂起；不能保证所有厂商系统都长期保活。
5. 打开原生字幕界面，点“仅在手机预览 / Local preview”。
6. 说一句新的无敏感内容的话，确认 App 预览中的原文和译文。

**检查点：** App 看到了两个角色的节点。本地预览不会自动开始网络发送。手机原生字幕有字、App 预览没有字时先查节点和权限，不先查隧道。

### 7. 放置官方隧道程序

从官方获取 Windows x64 cloudflared，检查可获得的签名/哈希，重命名为 `cloudflared.exe`，放到 `app/mobile_caption/tools/cloudflared.exe`。英文第 7 步给出复制与版本检查命令，下载目录需要换成自己的实际文件。

源码仓库不带可执行程序。内置控制器会创建临时隧道，重建后域名可能变化。这条路线不用给手机填电脑局域网 IP。**检查点：** cloudflared 能运行，当前终端能找到 ADB；不要另开第二个隧道来混淆。

### 8. 配对并启动：二选一

以下从 `app` 运行；英文第 8 步给出完整命令。

**首次有 USB：** `tunnel-start` → `pair-usb` → `start` → `status`。首次创建随机 device ID、token 和 32 字节 AES 密钥；USB 写入 App 私有配置并重新加载，再启动手机发送、电脑接收与窗口。多设备时加自己的 `--serial`，不要把序列号公开。

**首次无 USB：** `pairing-code --file <仓库外私密路径>` 自动启动隧道，生成 `CRCP1:` 配对码。私下传到手机 → “粘贴配对码 / Import pairing code” → 检查并导入 → 确认完整 `/v1/captions` 地址 → Reload → Start；电脑执行 `start --phone-configured` 再查 status。导入配对码不会替你开权限和发送。

`--phone-configured` 只是说明你手动更新了地址，不能跳过认证，也不能证明已连通；仍要收到新的认证手机包。

**已有配对、地址变更、无 USB：** 保留原身份/密钥，不重新生成。电脑启动隧道，本地看 status 当前完整 `https://<host>/v1/captions` → 手机保存地址 → Reload → Start → 电脑 `start --phone-configured`。裸域名、`/`、电脑 LAN IP 或旧隧道域名都不对。相同身份/密钥只改地址会保留 stream、序号和待发队列。

**检查点：** 电脑收到本次启动之后的新认证包。手机在退避重试时可能超过一次启动等待，应按排障处理，不能删配置或再开接收器。

### 9. 真实双语验收

1. 确认无障碍连接、采集和发送开关开启。
2. 说一句新的无敏感内容、原生功能支持的话。
3. 同时观察手机原文/译文和电脑最新显示。
4. 本地读取 `app/output/caption_viewer/session_*/captions.md`，确认本次文字进入历史。
5. 检查当前预览不等可靠积压，历史随后追上。`/v1/captions/live` 由程序派生，手机基础地址不要手动改成 live 路径。
6. 要测重启恢复，用控制器重启，再重复新文字验收。

**检查点：** 当前显示与可靠历史均通过。心跳只能证明传输；诊断测试按钮会写合成历史，不证明真实识别。`verify_mobile_recovery.py --live` 会变更手机网络、重启进程，不是普通只读检查。

### 10. 日常启动、停止与 GUI

从 `app` 运行 `& $captionPython .\mobile_caption\desktop\entrypoint.py` 打开管理器，也可直接用控制器。重建隧道后记得更新手机地址。不要在 18765 再开第二个接收器。

完整停止执行 `mobile_caption_control.py stop`，再看 status；没有 USB 时手机也要手动点 Stop。关管理器不等于停后台，单独关字幕窗口也不等于停止完整手机服务。保留队列/历史数据库，私密备份不进 Git；见[迁移与升级](MIGRATION_AND_UPGRADES.md)。

### 11. 可选 Windows 便携版

先完成测试、APK、USB JAR、cloudflared、ADB，可从 `app` 用 `check_environment.py --require-build --require-portable` 再检查，然后按英文第 11 步构建，使用 Python 3.11 x64。脚本创建独立构建环境、安装锁定依赖、PyInstaller 打包并检查结果，输出位置在本地 `desktop/builds/latest-build.json`。

便携版必须完整解压和搬迁整个 one-folder 目录，不能只拿 `CaptionRelayCaptions.exe`。Python/Tk、脚本、USB 工具、APK 和隧道客户端都属于完整包。打包自检不代替真机验收，不把本地运行资料带进公开发行；APK 下载地址不是 OTA，未来更新仍要显式分发、同签名安装。
