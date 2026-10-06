// Use monotonic preview retry timing independent of the reliable backlog.
// 使用单调时钟计算预览重试，与可靠积压队列互不影响。
package org.captionrelay.bridge;

/**
 * Preview retry timing is independent of the reliable history backoff.
 * 预览重试计时与可靠历史退避相互独立。
 */
final class LiveRetryGate {
    private long generation, retryAt;
    private int failures;
    synchronized long generation() { return generation; }
    synchronized long remaining(long now) { return Math.max(0, retryAt - now); }
    synchronized void changed() { generation++; retryAt = 0; failures = 0; }
    synchronized void success() { retryAt = 0; failures = 0; }
    synchronized void failed(long attemptGeneration, long now, int jitterMillis) {
        if (attemptGeneration != generation) return;
        failures = Math.min(failures + 1, 5);
        retryAt = now + Math.min(1000, (100L << (failures - 1)) + Math.max(0, Math.min(99, jitterMillis)));
    }
}
