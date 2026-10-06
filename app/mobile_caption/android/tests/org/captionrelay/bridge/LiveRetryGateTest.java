// Synthetic JVM regression tests; no live device, pairing secret, or caption history is required.
// 合成 JVM 回归测试，不需要真机、配对密钥或字幕历史。
package org.captionrelay.bridge;

public final class LiveRetryGateTest {
    private static int checks;
    private static void check(boolean value, String message) { checks++; if (!value) throw new AssertionError(message); }
    public static void main(String[] args) {
        LiveRetryGate gate = new LiveRetryGate();
        check(gate.remaining(100) == 0, "initial preview sends immediately");
        long generation = gate.generation();
        long now = 100;
        int[] delays = {100, 200, 400, 800, 1000, 1000, 1000};
        for (int delay : delays) {
            gate.failed(generation, now, 0);
            check(gate.remaining(now) == delay, "preview backoff bounded at one second");
            check(gate.remaining(now + delay) == 0, "retry becomes eligible at deadline");
            now += delay;
        }
        gate.success(); check(gate.remaining(now) == 0, "ACK resets backoff");
        gate.failed(generation, now, 99); check(gate.remaining(now) == 199, "first preview jitter is bounded");
        gate.changed(); check(gate.remaining(now) == 0, "network/config change wakes preview");
        gate.failed(generation, now, 99); check(gate.remaining(now) == 0, "cancelled attempt cannot restore old backoff");
        gate.failed(gate.generation(), now, 10000); check(gate.remaining(now) == 199, "large jitter clamped");
        gate.success(); gate.failed(gate.generation(), now, -100); check(gate.remaining(now) == 100, "negative jitter clamped");
        RetryGate history = new RetryGate();
        for (int i = 0; i < 6; i++) history.failed(history.generation(), now, 0);
        check(history.remaining(now) == 30000, "history still has conservative retry policy");
        gate.success(); check(gate.remaining(now) == 0 && history.remaining(now) == 30000, "live ACK cannot reset history retry gate");
        System.out.println("LiveRetryGate regression tests passed (" + checks + " checks)");
    }
}
