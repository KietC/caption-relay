---
name: caption-relay
description: "Set up, diagnose, test, migrate and source-package the Caption Relay Android-to-Windows bridge. Configure native caption capture and independent live/durable transport without exposing keys or transcripts. 配置、诊断、测试、迁移并打包 Caption Relay 安卓到 Windows 字幕桥源码，保护密钥和字幕。"
---

# Caption Relay operator skill

## English

Use this skill for this repository's Android accessibility reader, sender, desktop receiver, viewer, local history and source release. It does not turn Codex into an audio transcriber and does not implement every Android/iOS caption engine.

### Inspect before changing anything

1. Confirm the intended repository root, the requested action and whether the user is configuring a fresh public app or upgrading an existing installation.
2. Read [Setup](../../docs/SETUP.md), [Architecture](../../docs/ARCHITECTURE.md), [Troubleshooting](../../docs/TROUBLESHOOTING.md) and [Migration](../../docs/MIGRATION_AND_UPGRADES.md). Use the actual CLI `--help` output when flags are uncertain.
3. Run `python tools/check_environment.py` from the root. Gather only public tool versions, process metadata and error codes. Do not open pairing files, databases, transcripts, screenshots or signing passwords to provide a status summary.
4. Inspect current sources and preserve unrelated edits. Use isolated directories for all tests and build outputs. Do not change the system clock, timezone, firewall or running private deployment merely to test this copy.

### Configure in dependency order

1. Verify that the phone itself displays usable original and translated native captions. API level alone is insufficient.
2. Install Python dependencies, run unique synthetic Python and JVM tests, and compile the adapter against the selected SDK.
3. Fresh installations may create a local signer only under the explicit new-key build option. An existing installation must keep its package, signer and queue. The public `org.captionrelay.bridge` namespace is a new app and needs new pairing.
4. Build the APK and optional USB helper, then configure the phone's accessibility service, native caption feature and battery behavior. Confirm phone-local preview before involving the network.
5. Generate private desktop pairing, start the loopback receiver and tunnel, and pair via the supported USB route or private manual code. Never paste a code into a chat, issue, README or public command log.
6. Validate a packet authenticated during the current run, then fresh phone caption text in desktop live preview and reliable history. Report each checkpoint separately. A process, tunnel URL, stale file or synthetic unit test is not a successful phone session.
7. Close/stop and restart using the documented lifecycle. Reconcile a changed tunnel URL without silently replacing device identity.

### Diagnose without inventing certainty

- Empty source/translation: check native captions, service enablement and role-specific nodes.
- Queue grows: check endpoint, network, authentication, batch rejection and matching signed ACK. Never delete pending rows to make counters look good.
- Preview is late: inspect live separately from durable backlog. Smaller queue polling does not guarantee instant audio-to-screen output.
- Startup times out: check for fresh authentication and the documented retry/backoff overlap before repeating start.
- Moving PCs: stop the old controller, export privately, import into a stopped new instance and update the endpoint. The migration archive is sensitive and not encrypted at rest.
- Missing signer: do not pretend a new key can overwrite the existing installation while preserving its queue.
- Windows temporary-path assertions: use the supplied test runner's canonical temporary path and isolate `TEMP/TMP/TMPDIR` for that process only.

### Test, package and hand off

Run from the repository root:

```powershell
python tools/run_checks.py
python tools/test_android_core.py
python tools/audit_public_source.py
```

Use a reviewed explicit fileset with `tools/generate_allowlist.py` and `tools/source_bundle.py` for source exports. Both refuse runtime state, secrets, binary files, path escapes and content changes. Store local receipts outside the repository. Publication requires the user's requested destination and visibility; a previous research request does not itself authorize upload.

Keep code comments and Markdown English first, Chinese second. Deliver changed files, exact commands, synthetic check results, real-device acceptance status, known limitations and rollback/recovery instructions. Never call a synthetic pass a current production pass.

## 简体中文

本技能用于本仓库的 Android 无障碍字幕读取器、发送器、桌面接收器、字幕窗口、本地历史及源码发布。它不会让 Codex 变成音频转写引擎，也不提供全部 Android/iOS 原生字幕实现。

### 修改前先检查

1. 确认仓库根目录、用户要求的动作，以及当前属于公开应用的新安装还是已有应用的升级。
2. 阅读 [配置](../../docs/SETUP.md)、[架构](../../docs/ARCHITECTURE.md)、[排错](../../docs/TROUBLESHOOTING.md) 和 [迁移](../../docs/MIGRATION_AND_UPGRADES.md)。参数不确定时以当前 CLI 的 `--help` 为准。
3. 在仓库根运行 `python tools/check_environment.py`。状态总结只收集公开工具版本、进程元数据和错误码，不打开配对文件、数据库、字幕、截图或签名密码。
4. 检查现有源码并保留无关修改。所有测试与构建采用隔离目录，不为验证此副本修改系统时间、时区、防火墙或现有私有部署。

### 按依赖顺序配置

1. 确认手机自身能显示可用的原文和译文字幕，不能只依据 API 等级。
2. 安装 Python 依赖，运行去重的 Python 合成测试和 JVM 测试，再针对选定 SDK 编译适配器。
3. 只有新安装能通过明确的新密钥选项创建本地签名；已有安装必须保留包名、签名和队列。公开命名空间 `org.captionrelay.bridge` 是一个新应用，需要重新配对。
4. 构建 APK 和可选 USB 辅助程序；设置手机无障碍、原生字幕及电池策略。先确认手机本地预览，再接入网络。
5. 创建桌面私密配对，启动回环接收器与隧道，通过支持的 USB 方式或私密手动码配对。配对码不得粘贴到聊天、Issue、README 或公开命令日志。
6. 验证本次运行产生的认证包，再验证手机新字幕进入桌面实时预览和可靠历史。各检查点分开报告；进程、隧道网址、旧文件或合成测试都不等于真机联通。
7. 使用文档中的生命周期关闭、停止和重启。隧道网址变化时更新端点，不悄悄更换设备身份。

### 排错时不夸大结论

- 原文或译文为空：检查手机原生字幕、服务开启状态和角色专用节点。
- 队列持续增长：检查端点、网络、认证、批次拒绝及签名 ACK；不能靠删除待发送行改善计数。
- 预览延迟：独立检查实时通道和可靠积压；减少轮询时间不能保证音频到屏幕瞬时完成。
- 启动超时：检查新认证及文档中的重试退避交叠，再决定重试。
- 更换电脑：停止旧控制器、私密导出、导入到已停止的新实例，再更新端点。迁移包含敏感数据，未做静态加密。
- 原签名缺失：不能声称换签名后能覆盖原安装并保留队列。
- Windows 临时路径断言：采用本测试器规范的临时路径，只对测试进程隔离 `TEMP/TMP/TMPDIR`。

### 验证、打包和交接

在仓库根运行：

```powershell
python tools/run_checks.py
python tools/test_android_core.py
python tools/audit_public_source.py
```

源码导出应使用经过审阅的明确文件清单，配合 `tools/generate_allowlist.py` 和 `tools/source_bundle.py`。两个工具拒绝运行数据、密钥、二进制、路径逃逸和内容变更。回执保存在仓库外。发布必须符合用户指定的目标和可见性；之前的研究请求本身不代表上传授权。

代码注释与 Markdown 英文在前、中文在后。交付应包含修改文件、完整命令、合成检查结果、真机验收状态、已知限制及回滚或恢复方法，不把合成通过写成当前生产通过。
