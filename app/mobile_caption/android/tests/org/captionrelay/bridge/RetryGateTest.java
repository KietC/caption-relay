// Synthetic JVM regression tests; no live device, pairing secret, or caption history is required.
// 合成 JVM 回归测试，不需要真机、配对密钥或字幕历史。
package org.captionrelay.bridge;

/** Host JVM regression tests; invokes the actual sender retry state without Android mocks. */
public final class RetryGateTest {
    private static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    public static void main(String[] args) {
        RetryGate gate = new RetryGate();
        long initial = gate.generation();
        gate.failed(initial, 100, 20);
        check(gate.remaining(100) == 1020, "first failure backoff");
        gate.failed(initial, 1120, 0);
        check(gate.remaining(1120) == 2000, "second failure backoff");
        gate.changed();
        check(gate.remaining(1120) == 0, "route change must release wait immediately");
        gate.failed(initial, 1130, 0);
        check(gate.remaining(1130) == 0, "old socket cancellation must not restore backoff");
        long next = gate.generation();
        gate.failed(next, 2000, 0);
        check(gate.remaining(2000) == 1000, "new route starts with fresh failure count");
        for (int i = 0; i < 20; i++) gate.failed(next, 3000, 499);
        check(gate.remaining(3000) == 30499, "cap backoff after offline period");
        gate.changed();
        check(gate.remaining(4000) == 0, "reconnection after offline period immediate");
        gate.success();
        gate.failed(gate.generation(), 5000, 0);
        check(gate.remaining(5000) == 1000, "success resets exponential history");
        check(gate.remaining(6001) == 0, "expired retry deadline never negative");
        gate.changed();
        long stale = gate.generation();
        gate.changed();
        gate.failed(stale, 9000, 9999);
        check(gate.remaining(9000) == 0, "endpoint reload invalidates pending old request");
        System.out.println("RetryGate regression tests passed (10 checks)");
    }
}
