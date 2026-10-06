// Synthetic JVM regression tests; no live device, pairing secret, or caption history is required.
// 合成 JVM 回归测试，不需要真机、配对密钥或字幕历史。
package org.captionrelay.bridge;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Host JVM tests for replacement, acknowledgement races, and monitor waiting. */
public final class LatestSlotTest {
    private static int checks;

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    private static Thread waiter(final LatestSlot<Object> slot, final long timeoutMillis,
            final AtomicReference<Object> received, final AtomicReference<Throwable> failure,
            final CountDownLatch entered) {
        Thread thread = new Thread(new Runnable() {
            @Override public void run() {
                try {
                    synchronized (slot) {
                        entered.countDown();
                        received.set(slot.await(timeoutMillis));
                    }
                } catch (Throwable error) {
                    failure.set(error);
                }
            }
        }, "latest-slot-test-waiter");
        thread.setDaemon(true);
        thread.start();
        return thread;
    }

    private static void finish(Thread thread, AtomicReference<Throwable> failure)
            throws InterruptedException {
        thread.join(2000);
        if (thread.isAlive()) {
            thread.interrupt();
            throw new AssertionError("waiter did not finish");
        }
        check(failure.get() == null, "waiter failed: " + failure.get());
    }

    public static void main(String[] args) throws Exception {
        LatestSlot<Object> slot = new LatestSlot<Object>();
        Object first = new Object();
        Object second = new Object();
        check(slot.peek() == null, "new slot is empty");
        check(slot.await(0) == null, "zero timeout is nonblocking");
        check(!slot.clearIfSame(null), "empty slot cannot acknowledge an item");
        slot.offer(first);
        check(slot.peek() == first, "offer stores exact reference");
        check(slot.await(0) == first, "preexisting offer cannot lose its wakeup");
        slot.offer(second);
        check(slot.peek() == second, "latest offer replaces pending value");
        check(!slot.clearIfSame(first), "old acknowledgement rejected");
        check(slot.peek() == second, "old acknowledgement preserves new value");
        check(slot.clearIfSame(second), "matching acknowledgement clears value");
        check(slot.peek() == null, "matching acknowledgement leaves empty slot");
        check(!slot.clearIfSame(second), "duplicate acknowledgement rejected");

        String equalFirst = new String("same");
        String equalSecond = new String("same");
        check(equalFirst.equals(equalSecond), "identity test inputs compare equal");
        slot.offer(equalFirst);
        slot.offer(equalSecond);
        check(!slot.clearIfSame(equalFirst), "equals is not acknowledgement identity");
        check(slot.peek() == equalSecond, "equal replacement survives old acknowledgement");
        slot.clear();
        check(slot.peek() == null, "clear empties slot");
        slot.clear();
        check(slot.peek() == null, "clear is idempotent");

        boolean nullRejected = false;
        slot.offer(first);
        try { slot.offer(null); } catch (NullPointerException expected) { nullRejected = true; }
        check(nullRejected && slot.peek() == first, "null offer rejected without losing item");
        boolean negativeRejected = false;
        try { slot.await(-1); } catch (IllegalArgumentException expected) { negativeRejected = true; }
        check(negativeRejected, "negative timeout rejected");
        check(slot.await(Long.MAX_VALUE) == first, "huge timeout never overflows with item ready");
        slot.clear();

        // The latch is counted down while holding the slot monitor: an offer after
        // it fires cannot enter until await atomically releases that monitor.
        AtomicReference<Object> received = new AtomicReference<Object>();
        AtomicReference<Throwable> failure = new AtomicReference<Throwable>();
        CountDownLatch entered = new CountDownLatch(1);
        Thread waiting = waiter(slot, 1000, received, failure, entered);
        check(entered.await(1, TimeUnit.SECONDS), "empty waiter started");
        slot.wake();
        slot.offer(second);
        finish(waiting, failure);
        check(received.get() == second, "offer wakes empty waiter with exact current value");
        check(slot.peek() == second, "await does not consume value before acknowledgement");
        slot.clear();

        received.set(first);
        failure.set(null);
        entered = new CountDownLatch(1);
        long started = System.nanoTime();
        waiting = waiter(slot, 60, received, failure, entered);
        check(entered.await(1, TimeUnit.SECONDS), "timeout waiter started");
        slot.wake();
        finish(waiting, failure);
        check(received.get() == null, "empty timeout returns null");
        check(System.nanoTime() - started >= TimeUnit.MILLISECONDS.toNanos(60),
                "wake cannot shorten wait without an item");

        received.set(null);
        failure.set(null);
        entered = new CountDownLatch(1);
        waiting = waiter(slot, Long.MAX_VALUE, received, failure, entered);
        check(entered.await(1, TimeUnit.SECONDS), "huge-timeout waiter started");
        slot.offer(first);
        finish(waiting, failure);
        check(received.get() == first, "huge timeout remains wakeable without overflow");
        slot.clear();

        failure.set(null);
        entered = new CountDownLatch(1);
        waiting = waiter(slot, 1000, received, failure, entered);
        check(entered.await(1, TimeUnit.SECONDS), "interruptible waiter started");
        waiting.interrupt();
        waiting.join(2000);
        check(!waiting.isAlive(), "interrupted wait terminates");
        check(failure.get() instanceof InterruptedException, "interruption is propagated");
        check(slot.peek() == null, "interruption does not invent a value");

        System.out.println("LatestSlot regression tests passed (" + checks + " checks)");
    }
}
