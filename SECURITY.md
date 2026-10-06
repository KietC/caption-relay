# Security / 安全说明

## English

The supported code is under `app/`. Historical snapshots are unsupported reference material. Use the repository's private vulnerability-reporting feature when available; otherwise open a public issue that states only that a private reporting channel is needed. Do not disclose tokens, AES keys, pairing codes, real captions, device identifiers or exploit details in a public issue.

Transport uses HTTPS and AES-256-GCM; ACK authentication is independent for live and durable streams. A public tunnel is an external transport provider, not local-only processing. Access to a tunnel URL alone does not authorize caption ingestion. Avoid binding the receiver to a public interface; use the intended loopback/tunnel arrangement.

Local SQLite queues, desktop Markdown and migration ZIPs are not encrypted at rest by this project. Protect the host, device and backups. Keep private pairing and signing materials outside shared folders. An authenticated packet proves transport identity, not that a caption was spoken correctly or fully observed.

For a suspected exposed key, stop sending and receiving, privately back up pending data, investigate where it was shared and plan a coordinated identity replacement. Do not reset an identity while a pending queue exists. The application deliberately refuses this operation to prevent data loss and replay confusion.

## 简体中文

当前支持的代码位于 `app/`；历史快照仅供参考，不提供维护承诺。如仓库启用了私密漏洞报告，请使用该渠道；否则公开 Issue 只说明需要私密报告渠道，不要公布令牌、AES 密钥、配对码、真实字幕、设备标识或利用细节。

传输使用 HTTPS 与 AES-256-GCM，实时和可靠通道的 ACK 验证相互隔离。公开隧道属于外部传输服务，并非全程本地处理。仅掌握隧道网址不代表有权限写入字幕。接收端应使用既定的回环地址加隧道方式，不要直接绑定公网接口。

本项目不对本机 SQLite 队列、桌面 Markdown 或迁移 ZIP 做静态加密。请保护设备、主机和备份，并把配对和签名资料留在非共享目录。认证数据包只能证明传输身份，不能证明字幕识别正确或采集完整。

怀疑密钥泄露时，先停止收发、私密备份未确认数据、核查泄露位置，再协调更换身份。待发送队列非空时不要重置身份；应用会拒绝此操作，以免丢失数据或混淆重放状态。
