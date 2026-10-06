# Troubleshooting and known pitfalls / 故障排查与已知坑

## English

Work from the first failed checkpoint toward the next. Preserve state before changing it. Do not delete a queue, replace a pairing identity, uninstall an app, kill unrelated processes, or reset the computer clock just to make a status indicator green.

### The five checkpoints

1. **Native phone text:** does the phone's own feature display fresh source and translated captions?
2. **Local app preview:** does accessibility read those captions into the app?
3. **Authenticated transport:** does this PC receive a new packet from the configured phone after startup?
4. **Live window:** does fresh preview text appear promptly without waiting for durable backlog?
5. **Reliable history:** does the new text enter this session's Markdown and is its durable queue acknowledged?

When a checkpoint fails, fix that layer first. For example, networking cannot fix an empty accessibility preview, and a visible old history entry cannot prove current transport.

### 1. Build or test failures

| Symptom | Likely cause | Ordered fix |
| --- | --- | --- |
| `javac`/`keytool` missing | JRE or incomplete/wrong JDK | Select a complete JDK 21 with `-JavaHome`; check all three JDK tools |
| `android.jar` missing | Wrong SDK root or platform missing | Point at the SDK root and install `platforms;android-36` |
| `aapt`, `d8`, `zipalign`, `apksigner` missing | Build-tools not installed or wrong version | Install `build-tools;36.0.0`, then check the exact files |
| `sdkmanager.bat` missing | Command-line tools extracted into an extra directory | Correct `cmdline-tools/latest/bin` layout or use Android Studio SDK Manager |
| Import/module not found | Different Python or missing desktop module path | Use the explicit `.venv` interpreter and the test `PYTHONPATH` from [Setup](SETUP.md) |
| Bilingual output is garbled / `UnicodeEncodeError` | Windows legacy encoding, often GBK | Set `$env:PYTHONUTF8='1'` in this terminal before Python, or invoke `python -X utf8 ...`; keep files UTF-8 |
| Tk unavailable | Python installed without Tcl/Tk | Repair the official Windows Python installation with Tcl/Tk |
| Migration/path tests disagree on TEMP | Windows 8.3 alias vs resolved long path | Use the process-only long TEMP/TMP test setup; preserve real migration evidence |
| PowerShell blocks script | Execution policy | Review the script and use a process-only policy if appropriate; do not change machine-wide policy |
| Portable builder rejects Python | It requires Windows x64 / Python 3.11 x64 | Use that baseline rather than assuming passing unit tests on another Python permits packaging |
| Portable build input missing | APK, JAR, ADB, or cloudflared not built/provided | Finish the exact dependency order before invoking desktop build |

JVM unit tests do not need the Android SDK. APK compilation does. Passing tests with a short-path workaround does not repair all mixed-path real backups. Do not build the portable application by copying private runtime directories into the source tree.

### 2. APK will not install or update

**Fresh public app:** use the public package `org.captionrelay.bridge` and a newly generated owner signing key. The private signing directory stays outside Git. A different package name is a separate Android installation, even if the displayed app names are similar.

**Existing public app:** updates need the same package, original signing certificate, and an appropriate higher `versionCode`. Build with `-KeystorePath` and `-SigningPasswordFile`; omit `-AllowNewSigningKey`. The builder refuses a missing original key by default.

If Android reports a certificate mismatch, stop. Restore the original key instead of uninstalling the app to bypass it. Uninstalling/clearing app data can destroy queued captions and pairing state. There is no OTA update service to recover the original certificate for you. See [Migration and upgrades](MIGRATION_AND_UPGRADES.md).

### 3. Native captions appear, but the app preview is blank

1. Confirm accessibility is explicitly enabled for **this package**; enabling a previous app's service does not enable this one.
2. Return to Caption Relay and use Local preview, not just the network Start button.
3. Confirm the native caption window is visible. A minimized, hidden, or changed UI may expose different nodes.
4. Check both counts/roles. Zero translation nodes differs from no source nodes.
5. If sideload restrictions prevent permission, inspect App info → Allow restricted settings, then enable accessibility yourself.
6. Inspect vendor battery/background-data restrictions and whether the service disconnects after leaving the app.
7. Only then investigate a changed node layout. Use synthetic captions and add a bounded adapter/test; avoid dumping unrelated accessibility windows or personal screen text.

The current reader is deliberately narrow. Switching to arbitrary whole-screen scraping is not a substitute for a correct supported-caption adapter. Local preview does not automatically enable transmission.

### 4. Phone was configured, but PC receives nothing

Run from `app`:

```powershell
$env:PYTHONUTF8 = '1'
& $captionPython .\mobile_caption_control.py status
```

Inspect locally; the output can contain your current endpoint and paths. Do not paste it unredacted into an issue.

1. Does the controller-owned tunnel actually run and have a confirmed endpoint?
2. Is the phone address the current **full** `https://<host>/v1/captions` endpoint? Compare the whole address, including the suffix.
3. Did the tunnel get recreated after the phone address was saved? Temporary hostnames change.
4. On the phone, tap Save endpoint → Reload → Start, and check capture/sending switches.
5. If no USB, use `start --phone-configured` **after** the manual update. The flag alone cannot establish a connection.
6. Check the phone's active network, internet availability, certificate validation, and captive portal status.
7. Preserve existing identity/key. If this PC is a replacement, import the old PC's private migration first rather than creating a competing identity.

An HTTP 404 at `/` may mean an HTTP service answered, but it says nothing about authentication or correct caption routing. The protocol endpoint is designed for authenticated POST envelopes, not a browser GET health demonstration. No raw transcript or secret should be sent by an improvised probe.

### 5. “No newly authenticated phone packet ... within 25 seconds”

This is a startup acceptance timeout. It does not prove the phone is permanently broken. A phone already in capped retry/backoff can exceed one 25-second controller wait, and an internet route can be temporarily unavailable.

Recheck the complete endpoint and phone Start state, then perform a deliberate Reload or network restoration through the phone UI. Read status and try the controller again. Do not repeatedly spawn receivers: a failed start can clean up the service it just started, while a pre-existing service is preserved. The controller checks ownership and port occupancy before switching sources.

To establish recovery, require a packet with receive time after this collector's start time. Old status, an old heartbeat, and a previous session file are not enough. A diagnostic packet proves its synthetic path only; finish with a fresh real caption.

### 6. Authentication or protocol errors

| Error family | What to inspect | Preserve |
| --- | --- | --- |
| `unauthorized` / HTTP 401 | Same configured token and identity; migration state; complete pairing import | Existing app queue/key |
| Envelope/decryption/schema errors | Matching client/server protocol and AES key; unmodified envelope | Raw private state for local review only |
| Sequence/gap/ACK failures | Device/stream domains, selected immutable batch, verified HMAC acknowledgement | Unacknowledged durable rows |
| `not_found` / HTTP 404 | Complete endpoint and protocol path | Current pairing identity |
| `live_unavailable` | Receiver actually has the live buffer/configuration and correct version | Reliable channel and history |
| `receiver_unavailable` / HTTP 503 | Local receiver/database/filesystem health | Inbox and consumer cursor |

Do not “fix” a failure by accepting any acknowledgement, disabling authentication, changing device identity while data is pending, or making the app discard unconfirmed rows. Batch fallback on an older endpoint is compatible behavior; it is not permission to weaken validation.

### 7. Queue grows or history catches up slowly

First identify which lane is slow:

- **Local preview blank:** capture problem; queue tuning does not help.
- **Live preview fresh, durable queue large:** current display is working; reliable history is catching up independently.
- **Both lanes offline:** endpoint/network/authentication issue.
- **Phone ACK advances, PC window/history stalls:** receiver-to-consumer/window issue; inspect the current inbox/cursor and owned processes locally.

The reliable queue is bounded at 2000 rows; an explicit gap means data could not be retained, not that it has been reconstructed. Batches are bounded at 100 records / 1 MiB and sent without waiting for a full batch. Only validated committed data earns a reliable acknowledgement.

Ordinary new captions do not cancel retry backoff. Network/configuration changes wake the relevant retry gate, and the independent live lane can carry current frames without waiting for old reliable rows. Flooding retries or reducing every wait to zero cannot remove public-network RTT and may make failures worse.

### 8. Preview works, but Markdown seems stale

Check the session path reported by the running viewer. It writes `app/output/caption_viewer/session_*/captions.md`; a different master MD is not synchronized automatically. Check that you opened the current session rather than an older folder.

Live confirmation is not a durable history confirmation. Let the reliable lane catch up and inspect its queue/acknowledgement separately. Do not move a consumer cursor forward manually or delete the inbox to make counts align. Cursor advancement is designed to follow output writes so failures can replay safely.

### 9. Reported delay is huge, negative, or inconsistent

Separate native recognition, source reading, queue residence, request RTT, inbox consumption, and window display. A phone timestamp minus a PC timestamp includes clock skew. Request RTT on the phone is not speech-to-window latency; a faster poll does not remove native ASR, translation, or network delays.

Measure with harmless synthetic or real-device test speech and document the exact reference event. Use monotonic clocks within a process. The GUI's UTC+8 rendering changes display only; the application does not require you to change Windows timezone, Windows Time service, or the system clock. Do not promise millisecond end-to-end performance based on constants in the code.

### 10. Port 18765 occupied / duplicate service

Identify the listening process locally before acting. The controller refuses to replace an unowned listener. Close a receiver you deliberately started, or stop it with its controller, then retry. Do not kill every Python/cloudflared process; another project may own it. The tunnel's loopback metrics port is 18766.

Keep only one service owner for one runtime directory. A source instance and a portable instance pointing at different states can appear to work independently while the phone talks to only one. Know which instance owns the endpoint and receiver.

### 11. App or window was closed, but work continues

Closing the manager is not a full shutdown. Stop via `mobile_caption_control.py stop`; without USB also tap Stop in the phone app. Check status afterward. The desktop window, receiver supervisor, tunnel, and phone capture/send switches have different lifetimes. Do not infer that a closed visible window means the phone stopped accumulating history.

### 12. A support report that is safe to share

Share only what is needed: public source revision, app version, OS/tool versions, which of the five checkpoints failed, error code, and synthetic reproduction. Remove or replace device IDs, tunnel hostnames, user paths, phone serials, credentials, captions, names, customers, and business details. Do not attach:

- `config.json`, `pairing-code.txt`, signing JKS/password files;
- SQLite databases, actual `captions.md`, accessibility screen dumps;
- a private migration ZIP, browser state, raw runtime/log archives;
- real call/contact/CRM material.

Source-only publication tools must work from an explicit source allowlist, not recursively zip the directory after you have run the app.

---

## 简体中文

按第一个失败的检查点逐层处理，改动前保留状态。不能为了“显示正常”删队列、替换身份、卸载 App、结束无关进程或重置电脑时间。

### 五个检查点

1. **手机原生文字：** 手机自身是否有新的原文、译文？
2. **App 本地预览：** 无障碍是否读到了这些文字？
3. **认证传输：** 电脑是否收到本次启动后的新认证手机包？
4. **即时窗口：** 新预览是否不等耐久积压直接出现？
5. **可靠历史：** 新文字是否进入本次会话 Markdown，耐久队列是否得到正确确认？

先修当前层。网络修不好空的本地预览，旧历史也不能证明当前连接。

### 1. 构建与测试问题

| 症状 | 常见原因 | 按顺序修正 |
| --- | --- | --- |
| 找不到 `javac`/`keytool` | 装成 JRE 或选错 JDK | 用 `-JavaHome` 指向完整 JDK 21，检查三个工具 |
| 找不到 `android.jar` | SDK 根错或没装 platform | 选正确根，装 `platforms;android-36` |
| 找不到 aapt/d8/zipalign/apksigner | build-tools 没装或版本不同 | 装 `build-tools;36.0.0`，逐个检查 |
| sdkmanager 不在示例位置 | 解压多套目录 | 整理成 `cmdline-tools/latest/bin` 或用 Studio |
| Python 模块找不到 | 解释器/PYTHONPATH 不同 | 用明确 `.venv` 路径和 [Setup](SETUP.md) 测试路径 |
| 双语输出乱码/UnicodeEncodeError | Windows 旧编码，常见为 GBK | 当前终端先 `$env:PYTHONUTF8='1'` 或单条 `python -X utf8 ...`，文件保存 UTF-8 |
| Tk 不可用 | Python 缺 Tcl/Tk | 修复官方 Python 安装组件 |
| TEMP 路径断言失败 | 8.3 短路径与规范长路径不一致 | 只在测试进程统一 TEMP/TMP；保留真实迁移证据 |
| PowerShell 不让运行 | 执行策略 | 审核后按需临时 Process 策略，不改全机策略 |
| 便携构建拒绝 Python | 要求 Windows x64/Python 3.11 x64 | 用构建基线；其它版本测试通过不等于可打包 |
| 便携构建缺输入 | APK/JAR/ADB/cloudflared 未准备 | 按安装指南先完成各构建输入 |

JVM 核心测试不需要 Android SDK，APK 编译需要。测试短路径规避不等于真实备份路径兼容修复。不能把私有运行目录复制进源码来“补齐构建输入”。

### 2. APK 安装或覆盖失败

**首次公开 App：** 使用 `org.captionrelay.bridge` 和自己的新签名，签名目录留在 Git 外。不同包名是独立安装，即使界面名字相似。

**已有公开 App：** 更新必须同包名、原证书及合适的更高 `versionCode`。传入原 `-KeystorePath`、`-SigningPasswordFile`，不使用 `-AllowNewSigningKey`。缺原签名时脚本默认拒绝。

证书不一致就先停，找回原密钥，不卸载绕过。卸载/清数据可能毁掉未发字幕和配对。没有 OTA 替你找回签名，见[迁移与升级](MIGRATION_AND_UPGRADES.md)。

### 3. 原生字幕有字，App 本地为空

1. 明确开启的是**当前包**无障碍，旧 App 权限不会继承。
2. 回 App 点 Local preview，不只点发送。
3. 保持原生字幕窗口可见；隐藏/缩小/换界面可能变节点。
4. 看两个角色计数：没译文与没原文不是同一问题。
5. 若系统阻止侧载权限，检查应用信息 → 允许受限设置，再由本人开启无障碍。
6. 看电池/后台数据及离开 App 后服务是否断开。
7. 最后才查节点布局变化，使用合成字幕增加窄范围适配及测试，不导出无关屏幕或个人文字。

读取器有意限制范围，不能用任意全屏抓取代替正确字幕适配。本地预览不会自动开始发送。

### 4. 手机已填地址，电脑没收到

从 `app` 运行 `& $captionPython .\mobile_caption_control.py status`，仅本地查看；输出可能含地址和路径，公开前必须脱敏。

1. 自己控制的隧道是否运行、是否确认地址？
2. 手机是否完整填入当前 `https://<host>/v1/captions`？包括后缀。
3. 保存之后是否又重建隧道？临时域名会变。
4. 手机 Save endpoint → Reload → Start，确认采集/发送开关。
5. 无 USB 时更新之后再 `start --phone-configured`，参数本身不能证明连通。
6. 检查当前网络、联网、TLS 及网页登录式网络限制。
7. 保留原身份/密钥，换电脑先导入旧电脑私密迁移，别创建竞争身份。

在 `/` 打开得到 HTTP 404 只说明可能有 HTTP 服务回答，不说明认证或路由正确。字幕协议是认证 POST，不是 GET 网页健康检查。不要临时探针发送真实字幕或密钥。

### 5. 25 秒内没有新认证包

这是启动验收超时，不等于永久失败。手机处于有上限的重试退避时，可能超过一次 25 秒等待；网络也可能短暂不可用。

重查完整地址和 Start 状态，经手机界面主动 Reload 或恢复网络，再看 status/重试控制器。不能反复开接收器：失败启动可能清理刚创建的服务，但保留原先已有服务。切换前会检查归属和端口。

恢复必须是接收时间晚于本次采集器启动的新包。旧状态、旧心跳、旧会话不算。诊断包只证明合成路径，最终还要新真实字幕。

### 6. 认证或协议错误

| 错误类 | 检查 | 保留 |
| --- | --- | --- |
| unauthorized/401 | 同一 token/身份、迁移状态、完整导入 | App 队列/密钥 |
| envelope/解密/schema | 客户端服务端协议及 AES 密钥、未修改密文 | 私密状态仅本地复核 |
| 序号/gap/ACK | 设备/stream 域、不可变批次、HMAC | 未确认耐久行 |
| not_found/404 | 完整基础地址、协议路径 | 当前配对身份 |
| live_unavailable | 接收器 live 缓冲/配置及版本 | 可靠通道/历史 |
| receiver_unavailable/503 | 接收器、数据库、文件系统 | 收件库与游标 |

不能接受任意 ACK、关认证、有待发数据时换身份，或直接丢未确认行。旧端点的批量回退是兼容措施，不是放松校验。

### 7. 排队大、补传慢

先分清哪一条慢：本地预览空是采集问题；即时新而耐久队列大，是可靠历史独立追赶；两条离线查地址/网络/认证；手机确认前进但电脑显示/历史停，查接收后消费、游标及窗口。

可靠队列上限 2000 行，gap 表示当时留不住，不代表恢复。每批最多 100 条/1 MiB，不等凑满，校验且落库后才确认。

新字幕不取消网络退避；网络/配置变化释放对应等待，即时线程可以不等耐久积压。洪泛重试或把所有等待改零不能消除公网 RTT，还可能加剧故障。

### 8. 预览新、MD 旧

确认正在看的路径是运行中 viewer 的当前 `app/output/caption_viewer/session_*/captions.md`，不是旧会话或另一本总 MD。不会自动同步其它总记录。

即时 ACK 不等于耐久 ACK；让可靠通道追上，单独查队列/确认。别手改消费游标或删收件库凑计数，游标设计为输出完成后前进，以支持失败重放。

### 9. 延迟巨大、负数或乱跳

分别测原生识别、采集、队列、RTT、收件消费及显示。手机时间减电脑时间会包含时钟偏差；手机 RTT 不是讲话到显示；轮询更快也不能去掉识别/翻译/网络延迟。

使用无敏感测试语音，说明参照事件，进程内用单调时钟。GUI UTC+8 只是显示，不要求修改 Windows 时区、时间服务或系统钟，不能凭代码常量保证毫秒端到端。

### 10. 18765 占用或服务重复

本地先确认监听进程归属。控制器拒绝覆盖别人占用的端口，关闭自己明确启动的旧接收器或用它自己的控制器停。不能结束全部 Python/cloudflared。隧道本地指标端口为 18766。

一个 runtime 目录只保留一个服务主人。源码实例和便携实例若使用不同状态，可能各自看似运行，而手机只连一个；要清楚 endpoint 属于谁。

### 11. 关窗口后还在运行

关管理器不等于停。执行 `mobile_caption_control.py stop`，无 USB 时手机也点 Stop，之后查状态。管理器、窗口、supervisor、隧道、手机开关生命周期不同，不能从关窗推断手机已不排队。

### 12. 如何公开安全的排障报告

只提供必要的公开源码版本、App/OS/工具版本、五检查点哪一层失败、错误码和合成复现。脱敏设备 ID、隧道域名、用户路径、序列号、凭据、字幕、人员、客户及业务特征。不要附：

- config、配对码、JKS/密码；
- SQLite、真实 captions.md、无障碍屏幕转储；
- 私密迁移 ZIP、浏览器状态、原始 runtime/log 包；
- 真实电话/联系人/CRM 数据。

公开源包必须从明确白名单导出，不能运行 App 后递归压缩整个目录。
