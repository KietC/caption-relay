# Historical source snapshots / 历史源码快照

## English

These are sanitized reference snapshots, included to explain the design's progression without publishing old Git history, deployment receipts or private data. The maintained build target is [`../app/`](../app/). Snapshot paths and identifiers have been neutralized; they are not byte-for-byte copies of a deployed private application and cannot upgrade one.

| Snapshot | Design stage | Main limitation |
| --- | --- | --- |
| [`pc1.0-phone0.1`](snapshots/pc1.0-phone0.1/) | Initial encrypted phone-to-desktop transport, recovery and local history | Earlier single-envelope transport; no independent latest-only channel |
| [`pc1.1-phone0.2`](snapshots/pc1.1-phone0.2/) | Durable batch boundary, authenticated ACK and continuous draining | Historical backlog can still delay current preview |
| [`../app/`](../app/) | Desktop 1.2 / Android 0.3 design: live latest-only delivery plus reliable history | Requires device acceptance; cannot recover text never observed |

Use the current [setup guide](../docs/SETUP.md), [architecture](../docs/ARCHITECTURE.md) and [checks](../tools/run_checks.py). Old packaging scripts and SDK paths remain only as historical source; do not execute them as the supported release process. Historical tests are retained for study and are not included in current CI acceptance.

Current `app/` comments are maintained in English and Chinese. These historical snapshots are not maintained and may retain single-language comments; their code and comments describe earlier behavior, not the current configuration contract.

The source-only exporter hashes the exact files selected at export time. No original path map, prior production checksum, local test log, signer, pairing file or captured transcript is published.

## 简体中文

这里保留脱敏后的参考快照，用于解释设计演进，不公开旧 Git 历史、部署回执或私密数据。当前维护的构建入口是 [`../app/`](../app/)。快照的路径和标识已经中立化，不是某个私有部署的逐字节副本，也不能用于覆盖升级那个应用。

| 快照 | 设计阶段 | 主要限制 |
| --- | --- | --- |
| [`pc1.0-phone0.1`](snapshots/pc1.0-phone0.1/) | 初始加密手机到电脑传输、恢复和本地历史 | 较早的逐条传输，没有独立的最新预览通道 |
| [`pc1.1-phone0.2`](snapshots/pc1.1-phone0.2/) | 可靠批次边界、签名 ACK 与连续排空 | 历史积压仍可能阻挡当前预览 |
| [`../app/`](../app/) | 桌面 1.2 / Android 0.3 设计：最新实时发送与可靠历史并行 | 必须真机验收；不能补回未观察到的文本 |

使用当前 [配置手册](../docs/SETUP.md)、[架构文档](../docs/ARCHITECTURE.md) 和 [检查器](../tools/run_checks.py)。旧打包脚本和 SDK 路径只作为历史源码保留，不应作为支持的发布流程执行。历史测试保留供研究，不算当前 CI 验收。

当前 `app/` 注释按中英文维护。历史快照不再维护，可能保留单语注释；其代码与注释说明的是早期行为，不能作为当前配置依据。

源码导出器会对导出时明确选定的文件计算哈希。本仓库不公布原始路径映射、旧生产校验值、本地测试日志、签名、配对文件或实际字幕。
