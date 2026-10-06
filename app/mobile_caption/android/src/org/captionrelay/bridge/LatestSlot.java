// Keep one replaceable value; a stale acknowledgement cannot clear its replacement.
// 保存一个可替换值；过时确认不能清除更新的替代值。
package org.captionrelay.bridge;

/**
 * One latest value, with identity-based acknowledgement and monitor-safe waiting.
 * 一个最新值，采用对象身份确认和监视器安全等待。
 */
final class LatestSlot<T> {
    private T item;

    synchronized void offer(T next) {
        if (next == null) throw new NullPointerException("next");
        item = next;
        notifyAll();
    }

    synchronized T peek() { return item; }

    /**
     * An acknowledgement for an older value must never remove its replacement.
     * 旧值的确认绝不能删除它的新替代值。
     */
    synchronized boolean clearIfSame(T expected) {
        if (item == null || item != expected) return false;
        item = null;
        return true;
    }

    synchronized void clear() { item = null; }

    /**
     * Returns the current value or null on timeout; zero means a nonblocking peek.
     * 返回当前值或在超时后返回 null；零表示非阻塞查看。
     */
    synchronized T await(long millis) throws InterruptedException {
        if (millis < 0) throw new IllegalArgumentException("millis must be nonnegative");
        long timeoutNanos = millis > Long.MAX_VALUE / 1000000L
                ? Long.MAX_VALUE : millis * 1000000L;
        long started = System.nanoTime();
        long remaining = timeoutNanos;
        while (item == null && remaining > 0) {
            wait(remaining / 1000000L, (int) (remaining % 1000000L));
            remaining = timeoutNanos - (System.nanoTime() - started);
        }
        return item;
    }

    /**
     * Rechecks the wait condition without manufacturing a value.
     * 重新检查等待条件，不制造新的值。
     */
    synchronized void wake() { notifyAll(); }
}
