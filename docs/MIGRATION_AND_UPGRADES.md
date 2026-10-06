# Migration, upgrades, and recovery / 迁移、升级与恢复

## English

Use the same UTF-8 shell setup as [Setup](SETUP.md): set `$env:PYTHONUTF8='1'` at the beginning of each terminal before invoking Python. It affects that terminal's child processes only.

### What must survive

| State | Why it matters | Safe rule |
| --- | --- | --- |
| Device ID, token, AES key | Receiver authentication and envelope decryption | Preserve the established pairing during PC migration |
| Phone stream/sequence and outbox | Ordering and unacknowledged history | Do not uninstall, clear app data, or replace keys while pending |
| PC inbox and consumer cursor | Durable receipt and replay boundary | Move together while local service is stopped |
| Local history | Your saved conversation text | Back up privately; never include in Git |
| Android signing certificate | Same-package update permission | Back up outside the repository and reuse for updates |

Migration files are private, contain credentials and actual history, and are **not encrypted archives** by this application. Store/transfer them with protection you control. A source ZIP is not a runtime backup, and a runtime migration ZIP is not a source release.

### 1. Move an established public installation to another PC

Do not begin by generating new credentials on the replacement PC. Prepare the same public source/complete portable version and dependencies first, but do not start its service.

On the old PC, from `app` (or use the manager's Export migration and stop action):

```powershell
$env:PYTHONUTF8 = '1'
$captionMigration = 'C:\PrivateBackups\caption-relay-migration.zip'
& $captionPython .\mobile_caption_control.py export-migration --file $captionMigration
& $captionPython .\mobile_caption_control.py status
```

1. Use a new output filename; the exporter preserves an existing archive instead of overwriting it.
2. Export stops the old local receiver/viewer and tunnel, then verifies that they stopped before exporting.
3. The phone queue may remain active while disconnected, so unacknowledged text can later reach the replacement PC. Keep its capacity limits in mind; pause phone capture if you do not need it during a long transfer.
4. Transfer the private ZIP directly to the replacement PC. Do not upload it to this repository or attach it to an issue.

On the new PC, with its local receiver/tunnel stopped:

```powershell
& $captionPython .\mobile_caption_control.py import-migration --file 'C:\PrivateBackups\caption-relay-migration.zip'
& $captionPython .\mobile_caption_control.py tunnel-start
& $captionPython .\mobile_caption_control.py status
```

The importer validates hashes/database state, backs up existing state, performs transactional replacement, and remaps local paths/consumer state. Preserve those local recovery backups. A validation failure is a reason to inspect the original privately, not to edit archive checksums or delete sequence evidence.

Update the phone to the replacement PC's **new full endpoint** while retaining device ID/key and phone stream/outbox:

- With USB, run `pair-usb`, then `start`.
- Without USB, save the current `https://<host>/v1/captions` on the phone → Reload → Start, then run `start --phone-configured` on PC.

Finish the real acceptance in [Setup](SETUP.md): a new authenticated packet after this start, fresh source/translation in the window, and new reliable Markdown history. Old imported records demonstrate retained history, not current connectivity. Keep the old stopped installation until acceptance succeeds.

### 2. Start the public namespace alongside a different private app

`org.captionrelay.bridge` is a new independent package. Android does not treat it as an upgrade of an app with a different package name. Use a new owner signing identity and fresh public pairing as described in [Setup](SETUP.md). Leave the previous app and its queue/history untouched until you intentionally handle them with its own tools.

Do not blindly import a migration bundle from a differently branded/protocol-namespaced product. The public pairing prefix is `CRCP1:` and this release has its own source/state expectations. Cross-product import is not claimed as a supported migration. Keeping old data privately is safer than pretending a package rename preserves it.

### 3. Update the public Android app in place

Before building:

1. Confirm the package remains `org.captionrelay.bridge`.
2. Make a private PC backup; let the phone's durable queue drain when possible and stop capture/sending for the upgrade window.
3. Locate the **same signing key/password** used for this public app's first installation.
4. Increase the manifest `versionCode` for the new release and set a matching human-readable `versionName`; do not invent a newer code to install unchanged source as an “upgrade.”
5. Run Python/JVM tests before building.

From `app`:

```powershell
$captionUpgradeOptions = @{
  JavaHome = $captionJdk
  AndroidSdkRoot = $captionSdk
  KeystorePath = $captionKey
  SigningPasswordFile = $captionPasswordFile
}
.\mobile_caption\android\build.ps1 @captionUpgradeOptions
& "$captionSdk\platform-tools\adb.exe" install --no-incremental -r .\mobile_caption\android\build\CaptionRelayCaptionBridge.apk
```

Do not add `-AllowNewSigningKey` to rescue a missing certificate. A successful APK signature verification checks the new file; it does not prove that the certificate matches the installed app unless you compare the certificates or perform the permitted same-signature update.

After install, inspect permissions and switches, reload the existing pairing, update an endpoint only if needed, then redo fresh real-caption acceptance. The durable queue is meant to survive an in-place same-package update; device-specific behavior still needs acceptance. No code here automatically downloads and installs updates over the air.

### 4. Upgrade the Windows receiver without losing state

Stop through the existing controller and confirm its owned receiver/tunnel have stopped. Create a private migration backup before replacing program files. Build/install the new program into a separate clean directory rather than overwriting a running Python/Tk/EXE folder.

Restore/import private state through the stopped importer, or retain the stopped runtime only through a deliberately reviewed compatible update. Do not recursively copy the old whole directory into a new source or public package: it mixes secrets, logs, databases, and program files.

After upgrade, use the controller to start once, update the phone's current endpoint if it changed, and verify both lanes. A portable `--self-check` or GUI self-check is a packaging check; it is not a phone/network acceptance test.

### 5. Change phones

Let the old phone send pending reliable rows, verify receipt, and stop it. Do not assume Android system backups transfer this app's private state; manifest backup is disabled. A new phone needs fresh explicit pairing and a native-caption/accessibility-node compatibility check.

If old pending data cannot be delivered, preserve the old app/device/state privately rather than clearing it. The project has no universal phone-to-phone queue transfer and no complete iPhone implementation.

### 6. Recover from an interrupted start or network loss

Follow [Troubleshooting](TROUBLESHOOTING.md) in order: native text → local preview → complete endpoint → pairing/authentication → owned receiver → live preview → reliable history. Keep queue/inbox/cursor intact. A temporary tunnel restart requires an endpoint update but not a new AES identity.

The controller manages its own collector lifecycle and rejects an unrelated occupied port. Avoid starting a second copy. Network/configuration changes release retry waits; new captions do not bypass network backoff.

### 7. Rollback limits

Keep a known previous **complete** desktop program directory plus its private stopped-state backup. Rollback has two separate questions: is the old program compatible with the newer protocol, and is it compatible with the newer database/history schema? Review both before restoring it. A syntactically valid rollback script is not evidence that your current runtime rollback has been tested.

Android normally rejects a lower versionCode and a different signing certificate. Do not use uninstall/data-clear as an automatic rollback strategy. If a safe same-signature forward fix can preserve data, prefer it over a destructive downgrade. Keep an explicit backup and recovery plan before any schema migration.

---

## 简体中文

同 [Setup](SETUP.md)，每个新终端在 Python 命令前先 `$env:PYTHONUTF8='1'`，只影响当前终端的子进程，避免双语输出编码问题。

### 必须保留什么

| 状态 | 为什么重要 | 操作原则 |
| --- | --- | --- |
| device ID、token、AES key | 认证及解密 | 换电脑保留已有配对 |
| 手机 stream/序号/outbox | 排序及未确认历史 | 有待发时不卸载、不清数据、不换密钥 |
| 电脑 inbox/消费游标 | 耐久接收及重放边界 | 本地停止时一起迁移 |
| 本地历史 | 已保存的对话文字 | 私密备份，不进 Git |
| Android 签名证书 | 同包名更新权限 | 仓库外备份，更新复用 |

迁移 ZIP 含连接凭据和真实历史，本应用**不对 ZIP 加密**，保存/转移时自行保护。源码 ZIP 不是运行备份，迁移 ZIP 也不是公开源码。

### 1. 已有公开安装换电脑

先准备相同公开源码/完整便携版和依赖，但新电脑不要启动服务、不要先生成新凭据。

旧电脑从 `app` 执行 `export-migration --file <仓库外私密ZIP>`，或用管理器“导出迁移包并停止”，之后看 status。英文第 1 节给出完整命令。

1. 选择新文件名，导出器保留已有 ZIP，不覆盖。
2. 导出停止本机接收/窗口和隧道，确认停完才导出。
3. 手机队列可以暂时留着，未确认文字随后补传；长时间转移要考虑容量，不需要继续采集则暂停手机。
4. 私下传 ZIP 到新电脑，不上传仓库/Issue。

新电脑服务停止时 `import-migration --file <ZIP>`，再 `tunnel-start`、`status`。导入校验哈希和数据库、备份已有状态、事务替换并重映射路径/消费者状态。保留本地恢复备份；失败时本地检查原包，不改校验和或删序号证据。

保留身份/密钥、手机 stream/队列，只改新完整地址：有 USB `pair-usb` → `start`；无 USB 手机保存新 `/v1/captions` → Reload → Start → 电脑 `start --phone-configured`。

按 [Setup](SETUP.md) 验收本次启动后的新认证包、新的原文/译文及可靠 Markdown。导入旧记录只证明历史保留，不证明新连接。新验收通过前保留旧停止安装。

### 2. 与不同私有 App 并存

`org.captionrelay.bridge` 是新独立包，不能覆盖不同包名。按 Setup 使用自己的新签名和新公开配对；此前 App 及队列/历史先保持原状，之后用它自己的工具处理。

不要盲目导入其它品牌/协议命名的私有迁移。公开前缀为 `CRCP1:`，本版有自己的源码/状态格式，不宣称跨产品导入兼容。私下保留旧资料，不能假装改包名自动保留队列。

### 3. 公开 Android 覆盖更新

先确认包名保持不变，私密备份电脑，尽量补完手机队列并停止。找回本公开 App 首次安装的**同一签名/密码**。新版本提高 manifest 的 `versionCode` 并设置匹配 `versionName`，不能给没变的代码随意加码冒充更新；先测 Python/JVM。

从 `app` 使用 `build.ps1 -JavaHome ... -AndroidSdkRoot ... -KeystorePath ... -SigningPasswordFile ...`，不加 `-AllowNewSigningKey`；再 `adb install --no-incremental -r ...\CaptionRelayCaptionBridge.apk`。完整命令在英文第 3 节。

缺原证书不能用新签名补救。APK 验签成功只证明新文件有效，还要比较原安装证书或完成合法同签名覆盖，才说明匹配。

安装后检查权限/开关，Reload 原配对，按需更换地址并重新真实验收。同包覆盖设计上保留耐久队列，但机型行为仍需验证，没有 OTA 自动安装更新。

### 4. 电脑升级保留状态

旧控制器停止并确认归属服务/隧道已停，先做私密迁移备份。新程序放独立干净目录，不覆盖正在运行的 Python/Tk/EXE。

停止状态下经 importer 恢复，或者经兼容性审查保留原 runtime。不要递归把旧整个目录复制到新源码/公开包，避免把秘密、日志和数据库混入程序。

更新后只启动一次，地址变化就更新手机，验收双通道。`--self-check`/GUI 自检只是打包检查，不是真机联网验收。

### 5. 换手机

旧手机先补完可靠数据、确认接收、再停止。manifest 禁用备份，不能假设 Android 自动迁移 App 私有状态。新手机要重新明确配对并验证原生字幕/无障碍节点。

旧待发无法补完时保留旧设备/App/状态，别清掉。本项目没有通用手机间队列转移或完整 iPhone 实现。

### 6. 启动中断或掉线恢复

按 [Troubleshooting](TROUBLESHOOTING.md) 的顺序：原生文字 → 本地预览 → 完整地址 → 认证 → 正确归属接收器 → 即时 → 历史。保留 queue/inbox/cursor。临时隧道重建要改地址，不需要新 AES 身份。

控制器管自己的进程，拒绝占用的无关端口，不再开第二份。网络/配置变化释放重试，新字幕不绕过退避。

### 7. 回滚限制

保留上版**完整**电脑程序和私密停止状态备份。回滚分别检查旧程序是否兼容新协议、新数据库/历史 schema；脚本语法正确不代表自己的真实回滚已经验证。

Android 通常拒绝更低 versionCode 或不同证书，不把卸载/清数据作为自动回滚。能以同签名前向修复保留数据时优先使用，schema 迁移前应有备份和恢复方案。
