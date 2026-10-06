package org.captionrelay.bridge;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class PendingBatchTest {
    private static int checks;
    private static void check(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    private static String envelope(int bytes) {
        char[] text = new char[bytes - 8]; Arrays.fill(text, 'x');
        return "{\"x\":\"" + new String(text) + "\"}";
    }
    private static PendingBatch.Builder builder(int limit) {
        return new PendingBatch.Builder("stream-a", "key-a", limit);
    }
    private static void rejected(Runnable operation, String reason) {
        try { operation.run(); throw new AssertionError("Expected " + reason); }
        catch (IllegalStateException | IllegalArgumentException failure) { check(reason.equals(failure.getMessage()), reason); }
    }
    public static void main(String[] args) {
        check(builder(100).build() == null, "empty queue produces no request");
        PendingBatch.Builder singleton = builder(100);
        check(singleton.add(41, "{\"v\":1,\"ciphertext\":\"unchanged\"}"), "single row is ready immediately");
        PendingBatch one = singleton.build();
        check(one.count() == 1 && one.lastSeq() == 41, "batch need not fill before send");
        String legacy = new String(one.payload(false), StandardCharsets.UTF_8);
        check(legacy.equals("{\"v\":1,\"ciphertext\":\"unchanged\"}"), "legacy uses exact durable envelope");
        check(new String(one.payload(true), StandardCharsets.UTF_8).equals("{\"v\":1,\"envelopes\":[" + legacy + "]}"), "wire wrapper");
        byte[] damagedCopy = one.payload(true); damagedCopy[0] = 0;
        check(one.payload(true)[0] == '{', "caller cannot mutate saved retry payload");
        singleton.add(42, "{\"v\":1}");
        check(one.count() == 1 && one.lastSeq() == 41, "later enqueue cannot extend in-flight ACK boundary");
        try { one.rows.clear(); throw new AssertionError("rows must be immutable"); }
        catch (UnsupportedOperationException expected) { checks++; }
        check("stream-a".equals(one.stream) && "key-a".equals(one.keyHash), "selection retains stream and key identity");

        PendingBatch.Builder hundred = builder(100);
        for (int i = 1; i <= 100; i++) check(hundred.add(i, "{\"seq\":" + i + "}"), "select first 100 FIFO rows");
        check(!hundred.add(101, "{}"), "101st row remains queued");
        check(hundred.build().count() == 100 && hundred.build().lastSeq() == 100, "highest selected sequence is ACK boundary");
        rejected(() -> hundred.build().payload(false), "legacy_requires_one_row");
        PendingBatch.Builder singleLimit = builder(1);
        singleLimit.add(1, "{}"); check(!singleLimit.add(2, "{}"), "legacy selects one row only");

        PendingBatch.Builder exact = builder(100);
        int wrapper = "{\"v\":1,\"envelopes\":[]}".getBytes(StandardCharsets.UTF_8).length;
        int large = PendingBatch.MAX_ENVELOPE_BYTES;
        for (int i = 1; i <= 7; i++) check(exact.add(i, envelope(large)), "large prefix fits");
        int tailBytes = PendingBatch.MAX_BYTES - wrapper - 7 * large - 7;
        check(exact.add(8, envelope(tailBytes)), "body exactly 1 MiB is accepted");
        check(exact.build().payload(true).length == PendingBatch.MAX_BYTES, "count brackets commas and wrapper");
        check(!exact.add(9, "{}"), "body above 1 MiB stays queued");
        check(!exact.add(10, "{}"), "cannot skip rejected FIFO row");
        PendingBatch.Builder overflow = builder(100);
        for (int i = 1; i <= 7; i++) overflow.add(i, envelope(large));
        check(!overflow.add(8, envelope(tailBytes + 1)), "one byte above limit is rejected");
        check(overflow.build().lastSeq() == 7, "overflow row remains outside ACK boundary");
        check(!overflow.add(9, "{}"), "smaller later row cannot bypass large prefix row");

        char[] unicode = new char[30000]; Arrays.fill(unicode, '\u4e2d');
        String utf8Envelope = "{\"text\":\"" + new String(unicode) + "\"}";
        PendingBatch.Builder utf8 = builder(100);
        for (int i = 1; i <= 11; i++) check(utf8.add(i, utf8Envelope), "UTF-8 prefix fits");
        check(!utf8.add(12, utf8Envelope), "byte limit counts UTF-8 bytes rather than characters");
        check(utf8.build().payload(true).length <= PendingBatch.MAX_BYTES, "UTF-8 payload is bounded");
        rejected(() -> builder(100).add(1, envelope(PendingBatch.MAX_ENVELOPE_BYTES + 1)), "envelope_too_large");
        rejected(() -> builder(100).add(0, "{}"), "batch_sequence_invalid");
        PendingBatch.Builder ordered = builder(100); ordered.add(2, "{}");
        rejected(() -> ordered.add(1, "{}"), "batch_sequence_invalid");
        rejected(() -> ordered.add(2, "{}"), "batch_sequence_invalid");
        PendingBatch.Builder gap = builder(100); gap.add(1, "{}");
        rejected(() -> gap.add(3, "{}"), "batch_sequence_invalid");
        check(gap.build().lastSeq() == 1, "gap cannot extend ACK boundary");
        PendingBatch.Builder upper = builder(100);
        check(upper.add(Long.MAX_VALUE - 1, "{}"), "high starting sequence is preserved");
        check(upper.add(Long.MAX_VALUE, "{}"), "last representable consecutive sequence fits");
        rejected(() -> upper.add(Long.MIN_VALUE, "{}"), "batch_sequence_invalid");
        rejected(() -> upper.add(1, "{}"), "batch_sequence_invalid");
        check(upper.build().lastSeq() == Long.MAX_VALUE, "overflow cannot wrap the ACK boundary");
        rejected(() -> builder(101), "invalid_batch_limit");
        rejected(() -> builder(0), "invalid_batch_limit");
        for (int code : new int[]{404, 405, 501}) check(PendingBatch.unsupportedStatus(code), "legacy fallback status");
        for (int code : new int[]{200, 301, 400, 401, 403, 409, 413, 429, 500, 502, 503}) {
            check(!PendingBatch.unsupportedStatus(code), "auth/conflict/transient failures never downgrade");
        }
        System.out.println("PendingBatch regression tests passed (" + checks + " checks)");
    }
}
