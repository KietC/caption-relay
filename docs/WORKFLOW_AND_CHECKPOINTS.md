# End-to-end workflow and checkpoints / 全流程与检查点

## English

This document connects the program's actual components and the human review steps. It is an implementation map, not a record of any company's calls, contacts, customers or deployment.

### Capture → deliver → review

1. **Native captions:** the phone's existing feature recognizes speech and translates it. Confirm both lines on the phone before debugging the bridge.
2. **Accessibility capture:** the adapter reads exact supported caption nodes and role-specific source/translation text. An empty accessibility tree is not a network failure. Phone-local preview is the first checkpoint.
3. **Parallel send:** the most recent frame replaces an unsent live slot; history snapshots enter a bounded durable queue. Live can skip superseded preview frames, while history must retain accepted sequence continuity. Neither path can reconstruct words the reader never saw.
4. **Private transport:** a locally generated pairing establishes token/key/device identity. Encrypted envelopes use separate live/durable domains over HTTPS. Pairing stays private; a restarted temporary tunnel can require an endpoint update.
5. **Desktop validation:** live updates a volatile publisher; durable batches enter the inbox transaction before a signed ACK. Receiving HTTP success without the corresponding authenticated ACK is insufficient for phone deletion.
6. **Collector and window:** committed events update history/latest/status through atomic writes before the consumer cursor advances. The viewer displays recent information, while Markdown provides local review material.
7. **Assistant handoff:** a human may select an authorized, redacted passage for an assistant to analyze. This application does not automatically paste text into Codex, send a chat, call an LLM API or forward a call to a customer-management system. Such an integration needs its own explicit destination and consent policy.
8. **Session close:** use the manager/controller stop operation. Closing only the caption window does not necessarily stop every service. Keep pending data and signer when updating.

### Acceptance ladder

| Checkpoint | Evidence | What it does not prove |
| --- | --- | --- |
| Toolchain ready | Public versions and required files exist | Phone compatibility |
| Synthetic Python/JVM checks | Protocol, queue, storage, preview and migration tests pass | Actual caption visibility or live network health |
| Adapter/APK compile | SDK compilation and signing verification pass | Accessibility permission or usable native captions |
| Local phone preview | New source and translated text appear in the app | Desktop pairing |
| Fresh authenticated transport | Packet accepted during this controller run | All spoken words captured |
| Fresh desktop preview and history | New text appears in both paths; durable ACK/cursor advances | Fixed or millisecond end-to-end latency |
| Restart/recovery | No unintended identity reset; pending sequence drains | Indefinite background survival on every OS |

### Failure patterns this design addresses

- One historical queue blocks the current sentence → separate live and durable workers.
- A worker clears a newer frame after an older ACK → bind live completion to frame identity.
- Sender deletes rows on arbitrary HTTP success → authenticate ACK and match sequence boundaries.
- Queued rows are re-encrypted on retry → persist the sealed envelope and retry it unchanged.
- Collector crash loses text after advancing its cursor → write history atomically before cursor advancement.
- Re-pairing replaces keys while old rows remain → reject identity changes with pending data.
- A migration carries an old temporary endpoint → import stopped and clear the endpoint until the new PC is configured.
- Old files look like a successful new start → require fresh run-specific authentication and timestamps.
- A faster polling interval is advertised as instant speech delivery → distinguish queue delay, native ASR delay, network/ACK timing and clock skew.
- Windows short temporary paths disagree with resolved paths → run synthetic checks with canonical temporary roots.

See [Troubleshooting](TROUBLESHOOTING.md) for checks in order and [Migration](MIGRATION_AND_UPGRADES.md) for safe recovery. Record real-session notes outside the source repository, with access controls appropriate to the participants.

## 简体中文

本文连接程序的真实组件和人工审核步骤，是实现流程图，不记录任何公司的电话、联系人、客户或部署。

### 采集 → 传输 → 审阅

1. **原生字幕：**手机已有能力负责识别语音及翻译。排查字幕桥之前，先确认手机上两种文本都出现。
2. **无障碍采集：**适配器只读取支持的精确字幕节点，按原文和译文角色提取文本。无障碍树为空不等于网络故障；手机本地预览是首个检查点。
3. **并行发送：**最新帧替换尚未发送的实时槽，历史快照进入有上限的可靠队列。实时通道允许跳过被覆盖的预览，历史通道必须保持已接受序列连续；两者都不能重建没有观察到的词句。
4. **私密传输：**本机生成的配对建立令牌、密钥和设备身份。实时及可靠通道使用不同加密域，通过 HTTPS 发送。配对保持私密；临时隧道重启后可能需要更新端点。
5. **桌面校验：**实时帧更新内存发布器；可靠批次先写入 inbox 事务再返回签名 ACK。单纯 HTTP 成功不足以让手机删除队列数据。
6. **采集器与窗口：**已提交事件先原子写入历史、latest 和 status，再推进消费者游标。窗口显示最近信息，Markdown 用于本地审阅。
7. **助手交接：**人可以选择已授权且脱敏的片段交给助手分析。本应用不会自动向 Codex 粘贴内容、发送聊天、调用 LLM API 或把通话转发到客户管理系统。这类集成必须另行定义明确目标和许可策略。
8. **结束会话：**使用管理器或控制器的停止功能。仅关闭字幕窗口不一定停止全部服务；升级时保留未确认数据和原签名。

### 验收层级

| 检查点 | 证据 | 不能证明什么 |
| --- | --- | --- |
| 工具链就绪 | 公开版本及必需文件存在 | 手机兼容性 |
| Python/JVM 合成检查 | 协议、队列、存储、预览及迁移测试通过 | 真机字幕可见或当前网络联通 |
| 适配器/APK 编译 | SDK 编译和签名校验通过 | 无障碍权限或原生字幕可用 |
| 手机本地预览 | 应用出现新的原文及译文 | 桌面配对 |
| 新认证传输 | 本次控制器运行接受认证包 | 全部说话内容已被捕获 |
| 新桌面预览及历史 | 两条路径均出现新文本；可靠 ACK 和游标推进 | 固定延迟或毫秒级端到端传输 |
| 重启恢复 | 没有意外重置身份；待发送序列排空 | 所有系统都能无限后台运行 |

### 本设计处理的典型故障

- 历史队列阻挡当前句子 → 实时和可靠发送采用独立 worker。
- 旧 ACK 到来后清掉新帧 → 实时确认绑定具体帧身份。
- 任意 HTTP 成功就删队列 → 验证 ACK 并匹配序列边界。
- 重试时重新加密待发送行 → 保存加密后的 envelope，原样重试。
- 游标先推进导致采集器崩溃后丢词 → 先原子写历史，再推进游标。
- 旧队列非空时换密钥 → 拒绝这种身份变更。
- 迁移带入旧临时网址 → 停止状态导入，清空端点，待新电脑配置。
- 旧文件看起来像新启动成功 → 验证本次运行的新认证及时间信息。
- 更快轮询被当作瞬时语音传输 → 分别测量队列、原生 ASR、网络/ACK 及钟差。
- Windows 临时短路径与规范路径不一致 → 用规范临时根运行合成检查。

排查顺序见 [排错手册](TROUBLESHOOTING.md)，恢复步骤见 [迁移](MIGRATION_AND_UPGRADES.md)。真实会话记录保存在源码仓库外，并采用适合参与者的数据访问控制。
