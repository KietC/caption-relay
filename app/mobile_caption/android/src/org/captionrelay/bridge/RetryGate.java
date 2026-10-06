// Track network/configuration generations to keep cancelled old attempts from restoring stale backoff.
// 跟踪网络和配置代数，防止已取消旧请求恢复过时退避。
package org.captionrelay.bridge;

/**
 * Monotonic-time retry state shared by the sender and route/configuration callbacks.
 * 发送线程和路由或配置回调共用的单调时钟重试状态。
 */
final class RetryGate {
    private long generation, retryAt;
    private int failures;
    synchronized long generation() { return generation; }
    synchronized long remaining(long now) { return Math.max(0, retryAt - now); }
    synchronized void changed() { generation++; retryAt = 0; failures = 0; }
    synchronized void success() { retryAt = 0; failures = 0; }
    synchronized void failed(long attemptGeneration, long now, int jitterMillis) {
        // A request cancelled by a new route/config cannot restore the old route's backoff.
        // 新路由或配置取消的请求，不能恢复旧路由的退避状态。
        if (attemptGeneration != generation) return;
        failures = Math.min(failures + 1, 6);
        retryAt = now + Math.min(30000, 500L << failures) + Math.max(0, Math.min(499, jitterMillis));
    }
}
