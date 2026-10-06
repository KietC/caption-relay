# Core code reading guide / 核心代码阅读指引

## English

Read the actual implementation in this order. The links lead to complete source files, not a substitute implementation or production log.

### 1. Read supported text and keep roles separate

[`CaptionAccessibilityService.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/CaptionAccessibilityService.java) selects native caption nodes and retains source/translation roles. [`captions.py`](../app/captions.py) applies corresponding desktop parsing rules. Changing a resource ID needs captured synthetic tree fixtures and device acceptance; broad arbitrary screen scraping is not the current contract.

### 2. Keep current preview independent of queued history

[`LatestSlot.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/LatestSlot.java) holds one replaceable value. The essential completion condition is:

```java
synchronized boolean clearIfSame(T expected) {
    if (item == null || item != expected) return false;
    item = null;
    return true;
}
```

Identity comparison prevents an ACK for a previous frame from erasing a newer frame. [`LiveSender.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/LiveSender.java) owns this channel. [`Sender.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/Sender.java) and [`Outbox.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/Outbox.java) independently drain durable batches and retain sealed envelopes until a matching signed ACK arrives.

### 3. Authenticate each channel with a different domain

[`protocol.py`](../app/mobile_caption/protocol.py) validates schemas, identity, sequence, encryption and ACKs. [`AckVerifier.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/AckVerifier.java) mirrors the ACK boundary on the phone. Durable and live signatures use different prefixes:

```text
caption-relay-ack-v1|device|stream|sequence
caption-relay-live-ack-v1|device|stream|sequence
```

Those are public protocol formats, not pairing secrets. Do not replace the prefixes on one side only. Synthetic fixed vectors test the cross-language contract.

### 4. Persist before acknowledging

[`receiver.py`](../app/mobile_caption/receiver.py) decrypts and validates the entire durable request, calls `Inbox.accept_batch`, and replies with a signed ACK only after it returns. [`store.py`](../app/mobile_caption/store.py) uses `BEGIN IMMEDIATE` and SQLite's transaction context:

```python
with self.lock, self.db:
    self.db.execute("BEGIN IMMEDIATE")
    # Validate sequence and insert every record here.
# Return after COMMIT, so failures cannot acknowledge a partial batch.
return results
```

This excerpt shows ordering; read the file for complete replay, conflict and nonce checks. Live ACK proves volatile preview acceptance, not durable storage.

### 5. Write history before advancing recovery state

[`collector.py`](../app/mobile_caption/collector.py), [`caption_history.py`](../app/caption_history.py) and [`atomic_io.py`](../app/atomic_io.py) update local files before `mark_applied` advances the consumer cursor. [`migration.py`](../app/mobile_caption/migration.py) preserves identity, inbox and cursors, remaps safe local output paths and imports stopped.

### 6. Require fresh session evidence

[`mobile_caption_control.py`](../app/mobile_caption_control.py) manages pairing, tunnel and supervised start/stop. It checks a fresh authenticated phone packet for the current run, rather than trusting stale history. [`entrypoint.py`](../app/mobile_caption/desktop/entrypoint.py) dispatches source/frozen commands; [`manager.py`](../app/mobile_caption/desktop/manager.py) presents operational status.

Run the [setup checks](SETUP.md) and read [workflow checkpoints](WORKFLOW_AND_CHECKPOINTS.md) before treating any state as a completed phone session.

## 简体中文

按下面顺序阅读真实实现。链接指向完整源码，不是另一套伪代码实现，也不是生产日志。

### 1. 读取支持的文字并分离角色

[`CaptionAccessibilityService.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/CaptionAccessibilityService.java) 选择原生字幕节点，保留原文及译文角色；[`captions.py`](../app/captions.py) 实现相应桌面解析规则。修改资源 ID 后，需要合成树结构测试及真机验收；当前协议不是任意屏幕抓取。

### 2. 当前预览独立于历史积压

[`LatestSlot.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/LatestSlot.java) 只保存一个可替换值。确认时比较对象身份，旧帧的 ACK 不能删除新帧。完整实现见英文部分的片段及源文件。[`LiveSender.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/LiveSender.java) 独立管理实时通道；[`Sender.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/Sender.java) 和 [`Outbox.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/Outbox.java) 独立排空可靠批次，匹配签名 ACK 前保留加密后的 envelope。

### 3. 两条通道使用不同认证域

[`protocol.py`](../app/mobile_caption/protocol.py) 校验结构、身份、序列、加密与 ACK；[`AckVerifier.java`](../app/mobile_caption/android/src/org/captionrelay/bridge/AckVerifier.java) 在手机端执行对应确认边界。可靠签名采用 `caption-relay-ack-v1`，实时签名采用 `caption-relay-live-ack-v1`，后面依次拼接设备、流和序号。

这些是公开协议格式，不是配对秘密。不能只修改一侧的前缀；固定合成向量用于验证跨语言协议一致。

### 4. 先持久提交，再确认

[`receiver.py`](../app/mobile_caption/receiver.py) 解密并校验完整可靠请求，调用 `Inbox.accept_batch`，返回后才发送签名 ACK。[`store.py`](../app/mobile_caption/store.py) 使用 `BEGIN IMMEDIATE` 和 SQLite 事务上下文，离开事务并完成 COMMIT 后才返回。完整文件还包含重放、冲突及 nonce 校验。实时 ACK 只证明内存预览接受，不代表可靠存储。

### 5. 先写历史，再推进恢复状态

[`collector.py`](../app/mobile_caption/collector.py)、[`caption_history.py`](../app/caption_history.py) 和 [`atomic_io.py`](../app/atomic_io.py) 先更新本地文件，再由 `mark_applied` 推进消费者游标。[`migration.py`](../app/mobile_caption/migration.py) 保留身份、inbox 与游标，重映射安全的本地输出路径，并以停止状态导入。

### 6. 要求本次会话的新证据

[`mobile_caption_control.py`](../app/mobile_caption_control.py) 管理配对、隧道和受控启停，要求本次运行产生的新认证手机包，不依赖旧历史。[`entrypoint.py`](../app/mobile_caption/desktop/entrypoint.py) 处理源码和便携包入口；[`manager.py`](../app/mobile_caption/desktop/manager.py) 显示操作状态。

完成 [配置检查](SETUP.md) 并阅读 [全流程检查点](WORKFLOW_AND_CHECKPOINTS.md) 后，再判断真机会话是否完成。
