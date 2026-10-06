package org.captionrelay.bridge;

/** Monotonic-time retry state shared by the sender and route/configuration callbacks. */
final class RetryGate {
    private long generation, retryAt;
    private int failures;
    synchronized long generation() { return generation; }
    synchronized long remaining(long now) { return Math.max(0, retryAt - now); }
    synchronized void changed() { generation++; retryAt = 0; failures = 0; }
    synchronized void success() { retryAt = 0; failures = 0; }
    synchronized void failed(long attemptGeneration, long now, int jitterMillis) {
        // A request cancelled by a new route/config cannot restore the old route's backoff.
        if (attemptGeneration != generation) return;
        failures = Math.min(failures + 1, 6);
        retryAt = now + Math.min(30000, 500L << failures) + Math.max(0, Math.min(499, jitterMillis));
    }
}
