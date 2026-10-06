// Freeze a contiguous queue prefix so concurrent capture cannot enlarge an acknowledgement boundary.
// 冻结连续队列前缀，防止并发采集扩大确认边界。
package org.captionrelay.bridge;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * An immutable FIFO selection: neither new queue entries nor retries can enlarge its ACK boundary.
 * 不可变 FIFO 选择；新队列项或重试都不能扩大确认边界。
 */
final class PendingBatch {
    static final int MAX_COUNT = 100;
    static final int MAX_BYTES = 1024 * 1024;
    static final int MAX_ENVELOPE_BYTES = 131072;
    private static final String PREFIX = "{\"v\":1,\"envelopes\":[";
    private static final String SUFFIX = "]}";

    static final class Row {
        final long seq;
        final String envelope;
        Row(long seq, String envelope) { this.seq = seq; this.envelope = envelope; }
    }

    final String stream;
    final String keyHash;
    final List<Row> rows;
    private final String json;

    private PendingBatch(String stream, String keyHash, List<Row> selected) {
        this.stream = stream;
        this.keyHash = keyHash;
        this.rows = Collections.unmodifiableList(new ArrayList<Row>(selected));
        StringBuilder body = new StringBuilder(PREFIX);
        for (Row row : rows) {
            if (body.length() != PREFIX.length()) body.append(',');
            // Reuse the exact durable encrypted envelope; never re-encrypt or reserialize a retry.
            // 原样复用已持久保存的加密信封；重试不重新加密或序列化。
            body.append(row.envelope);
        }
        this.json = body.append(SUFFIX).toString();
    }

    long lastSeq() { return rows.get(rows.size() - 1).seq; }
    int count() { return rows.size(); }
    byte[] payload(boolean batch) {
        if (!batch && count() != 1) throw new IllegalStateException("legacy_requires_one_row");
        return (batch ? json : rows.get(0).envelope).getBytes(StandardCharsets.UTF_8);
    }
    static boolean unsupportedStatus(int code) { return code == 404 || code == 405 || code == 501; }

    static final class Builder {
        private final String stream, keyHash;
        private final int limit;
        private final List<Row> rows = new ArrayList<Row>();
        private int bytes = PREFIX.length() + SUFFIX.length();
        private boolean full;

        Builder(String stream, String keyHash, int limit) {
            if (limit < 1 || limit > MAX_COUNT) throw new IllegalArgumentException("invalid_batch_limit");
            this.stream = stream; this.keyHash = keyHash; this.limit = limit;
        }

        boolean add(long seq, String envelope) {
            if (full) return false;
            long previous = rows.isEmpty() ? 0 : rows.get(rows.size() - 1).seq;
            if (seq < 1 || (!rows.isEmpty() && (previous == Long.MAX_VALUE || seq != previous + 1))) {
                throw new IllegalArgumentException("batch_sequence_invalid");
            }
            int envelopeBytes = envelope.getBytes(StandardCharsets.UTF_8).length;
            if (envelopeBytes > MAX_ENVELOPE_BYTES) throw new IllegalStateException("envelope_too_large");
            int extra = envelopeBytes + (rows.isEmpty() ? 0 : 1);
            if (rows.size() >= limit || bytes + extra > MAX_BYTES) {
                // Do not skip a large queued row and admit a later, smaller row.
                // 不能跳过队首较大的记录，转而接纳后面较小的记录。
                full = true; return false;
            }
            rows.add(new Row(seq, envelope)); bytes += extra;
            return true;
        }

        PendingBatch build() { return rows.isEmpty() ? null : new PendingBatch(stream, keyHash, rows); }
    }
}
