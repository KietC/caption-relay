// Require an explicit reset before changing the identity of a queue that may contain unsent data.
// 队列可能含未发数据时，改变身份前必须明确确认重置。
package org.captionrelay.bridge;

/**
 * Identity-change rules shared by the pairing preview and transactional import.
 * 配对预览和事务式导入共用的身份变更规则。
 */
final class PairingPolicy {
    static String kind(String oldDevice, String oldKey, String nextDevice, String nextKey) {
        if (oldDevice.isEmpty()) return "fresh";
        return oldDevice.equals(nextDevice) && !oldKey.isEmpty() && oldKey.equals(nextKey) ? "same" : "replace";
    }
    static void checkReplacement(boolean replace, int queued, long gaps, boolean confirmed) {
        if (!replace) return;
        if (queued > 0 || gaps > 0) throw new IllegalStateException("pairing_blocked_pending_captions");
        if (!confirmed) throw new IllegalStateException("pairing_replacement_confirmation_required");
    }
}
