// Persist encrypted FIFO records before sending and delete only a verified immutable ACK boundary.
// 发送前持久保存加密 FIFO 记录，只删除经验证且不可变的确认边界内记录。
package org.captionrelay.bridge;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.UUID;
import org.json.JSONObject;

/**
 * All mutations are synchronous and transactional; a sequence is allocated only for durable rows.
 * 所有变更均同步执行并具有事务性；只有持久记录才分配序号。
 */
final class Outbox extends SQLiteOpenHelper {
    static final int CAPACITY = 2000;
    Outbox(Context context) { super(context, "caption-outbox.db", null, 1); }
    @Override public void onConfigure(SQLiteDatabase db) { db.execSQL("PRAGMA synchronous=FULL"); }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
        db.execSQL("CREATE TABLE outbox (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, envelope TEXT, key_hash TEXT)");
        put(db, "stream_id", UUID.randomUUID().toString());
        put(db, "next_seq", "1");
        put(db, "dropped", "0");
        put(db, "dropped_total", "0");
        put(db, "acked", "0");
    }
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) { throw new IllegalStateException("unsupported_db_upgrade"); }
    private static String get(SQLiteDatabase db, String key, String fallback) {
        try (Cursor c = db.query("meta", new String[]{"v"}, "k=?", new String[]{key}, null, null, null)) {
            return c.moveToFirst() ? c.getString(0) : fallback;
        }
    }
    private static void put(SQLiteDatabase db, String key, String value) {
        ContentValues data = new ContentValues(); data.put("k", key); data.put("v", value);
        db.insertWithOnConflict("meta", null, data, SQLiteDatabase.CONFLICT_REPLACE);
    }
    private static long number(SQLiteDatabase db, String key) { return Long.parseLong(get(db, key, "0")); }
    private static int size(SQLiteDatabase db) {
        try (Cursor c = db.rawQuery("SELECT count(*) FROM outbox", null)) { c.moveToFirst(); return c.getInt(0); }
    }
    // Changing an endpoint preserves identity; replacing device or key requires explicit pairing import.
    // 更改端点保留身份；替换设备或密钥需要明确的配对导入。
    synchronized void bind(Config config) {
        SQLiteDatabase db = getWritableDatabase();
        String old = get(db, "device_id", "");
        if (!old.isEmpty() && !old.equals(config.deviceId)) throw new IllegalStateException("device_id_change_requires_new_pairing");
        String oldKey = keyHash(db);
        if (!oldKey.isEmpty() && !oldKey.equals(config.keyHash)) throw new IllegalStateException("key_change_requires_pairing_import");
        if (old.isEmpty()) put(db, "device_id", config.deviceId);
        if (get(db, "pairing_key_hash", "").isEmpty()) put(db, "pairing_key_hash", config.keyHash);
    }
    private static String keyHash(SQLiteDatabase db) {
        String value = get(db, "pairing_key_hash", "");
        if (!value.isEmpty()) return value;
        try (Cursor c = db.rawQuery("SELECT key_hash FROM outbox WHERE key_hash IS NOT NULL ORDER BY seq LIMIT 1", null)) {
            return c.moveToFirst() ? c.getString(0) : "";
        }
    }
    synchronized JSONObject pairingPlan(Context context, Config next) throws Exception {
        finishPairingWrite(context);
        SQLiteDatabase db = getReadableDatabase();
        String oldDevice = get(db, "device_id", ""), oldKey = keyHash(db);
        try {
            Config previous = Config.load(context);
            if (oldDevice.isEmpty()) oldDevice = previous.deviceId;
            if (oldKey.isEmpty() && previous.deviceId.equals(oldDevice)) oldKey = previous.keyHash;
        } catch (Exception unconfigured) { /* Database identity, when present, remains authoritative. */ }
        // 数据库身份一旦存在，仍以它为准。
        String kind = PairingPolicy.kind(oldDevice, oldKey, next.deviceId, next.keyHash);
        return new JSONObject().put("kind", kind).put("device_id", oldDevice)
                .put("queue_count", size(db)).put("pending_gap", number(db, "dropped"));
    }
    synchronized void importPairing(Context context, Config next, boolean confirmedReset) throws Exception {
        JSONObject plan = pairingPlan(context, next);
        boolean replace = "replace".equals(plan.getString("kind"));
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            // Recheck while holding the same lock used by capture and ACK processing.
            // 持有采集和确认处理所使用的同一把锁时，再次检查。
            PairingPolicy.checkReplacement(replace, size(db), number(db, "dropped"), confirmedReset);
            if (replace) {
                put(db, "stream_id", UUID.randomUUID().toString()); put(db, "next_seq", "1");
                put(db, "acked", "0"); put(db, "dropped", "0"); put(db, "dropped_total", "0");
            }
            put(db, "device_id", next.deviceId); put(db, "pairing_key_hash", next.keyHash);
            // Journal the intended file update with the identity change. Recovery completes it before sending.
            // 将预期文件更新和身份变更一起记入日志；恢复过程在发送前完成它。
            put(db, "pending_pairing_config", next.value.toString());
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
        finishPairingWrite(context);
    }
    synchronized void finishPairingWrite(Context context) throws Exception {
        SQLiteDatabase db = getWritableDatabase();
        String pending = get(db, "pending_pairing_config", "");
        if (pending.isEmpty()) return;
        Config checked = Config.parse(new JSONObject(pending));
        Util.writeJson(context, "config.json", checked.value);
        put(db, "pending_pairing_config", "");
    }
    synchronized String stream() { return get(getReadableDatabase(), "stream_id", ""); }
    synchronized JSONObject stats() throws Exception {
        SQLiteDatabase db = getReadableDatabase();
        return new JSONObject().put("queue_count", size(db)).put("queue_capacity", CAPACITY)
                .put("pending_gap", number(db, "dropped")).put("dropped_total", number(db, "dropped_total"))
                .put("last_ack_seq", number(db, "acked")).put("stream_id", get(db, "stream_id", ""))
                .put("device_id", get(db, "device_id", ""));
    }
    private static void append(SQLiteDatabase db, String body) {
        long seq = number(db, "next_seq");
        ContentValues row = new ContentValues(); row.put("seq", seq); row.put("body", body);
        db.insertOrThrow("outbox", null, row);
        put(db, "next_seq", Long.toString(seq + 1));
    }
    private static void lost(SQLiteDatabase db, String reason) {
        String at = Util.now();
        if (number(db, "dropped") == 0) put(db, "first_dropped_at", at);
        put(db, "last_dropped_at", at);
        put(db, "gap_reason", reason);
        put(db, "dropped", Long.toString(number(db, "dropped") + 1));
        put(db, "dropped_total", Long.toString(number(db, "dropped_total") + 1));
    }
    // Emit an explicit loss marker when capacity returns; missing caption contents are never reconstructed.
    // 容量恢复时发出明确的丢失标记，不凭空还原缺失字幕。
    private static void flushGap(SQLiteDatabase db) throws Exception {
        long count = number(db, "dropped");
        if (count == 0 || size(db) >= CAPACITY) return;
        append(db, new JSONObject().put("type", "gap").put("timestamp", Util.now())
                .put("dropped_snapshots", count).put("dropped", count)
                .put("first_dropped_at", get(db, "first_dropped_at", ""))
                .put("last_dropped_at", get(db, "last_dropped_at", ""))
                .put("reason", get(db, "gap_reason", "outbox_full")).toString());
        put(db, "dropped", "0");
    }
    // Queue limits record snapshot loss; never evict already-sequenced rows to make room.
    // 队列限额以快照丢失记录表示，不为腾空间而驱逐已分配序号的记录。
    synchronized boolean enqueue(JSONObject body, boolean heartbeat) throws Exception {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            flushGap(db);
            // Old liveness messages have no value; sequence allocation happens after this test.
            // 过时的存活消息没有价值；通过此检查后才分配序号。
            if (heartbeat && size(db) > 0) { db.setTransactionSuccessful(); return false; }
            String json = body.toString();
            boolean accepted = true;
            if (Util.bytes(json).length > 32768) { lost(db, "payload_too_large"); accepted = false; }
            else if (size(db) >= CAPACITY) { if (!heartbeat) lost(db, "outbox_full"); accepted = false; }
            else append(db, json);
            db.setTransactionSuccessful(); return accepted;
        } finally { db.endTransaction(); }
    }
    // Persist first-use ciphertext inside this transaction before it can be sent or retried.
    // 在事务内持久保存首次生成的密文，然后才能发送或重试。
    synchronized PendingBatch firstBatch(Config config, int limit) throws Exception {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            flushGap(db);
            String selectedStream = get(db, "stream_id", "");
            PendingBatch.Builder selected = new PendingBatch.Builder(selectedStream, config.keyHash, limit);
            try (Cursor c = db.query("outbox", new String[]{"seq", "body", "envelope", "key_hash"}, null, null, null, null, "seq ASC", Integer.toString(limit))) {
                while (c.moveToNext()) {
                    long seq = c.getLong(0); String envelope = c.getString(2);
                    boolean needsEncryption = envelope == null;
                    if (envelope == null) {
                        envelope = CaptionCrypto.encrypt(config, selectedStream, seq, c.getString(1)).toString();
                    } else if (!config.keyHash.equals(c.getString(3))) {
                        throw new IllegalStateException("key_changed_with_pending_data");
                    }
                    if (!selected.add(seq, envelope)) break;
                    if (needsEncryption) {
                        ContentValues values = new ContentValues(); values.put("envelope", envelope); values.put("key_hash", config.keyHash);
                        int updated = db.update("outbox", values, "seq=?", new String[]{Long.toString(seq)});
                        if (updated != 1) throw new IllegalStateException("pending_row_missing");
                    }
                }
            }
            PendingBatch result = selected.build();
            db.setTransactionSuccessful(); return result;
        } finally { db.endTransaction(); }
    }
    // Delete only rows captured by this immutable selection, not newly captured rows with higher sequences.
    // 只删除不可变选择中记录的行，不删除之后采集且序号更高的新行。
    synchronized void acknowledge(PendingBatch selected) {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            if (!selected.stream.equals(get(db, "stream_id", "")) || !selected.keyHash.equals(keyHash(db))) {
                throw new IllegalStateException("ack_queue_identity_mismatch");
            }
            for (PendingBatch.Row row : selected.rows) {
                int deleted = db.delete("outbox", "seq=? AND envelope=? AND key_hash=?",
                        new String[]{Long.toString(row.seq), row.envelope, selected.keyHash});
                // A mismatch anywhere rolls back every deletion in this batch.
                // 任何一处不匹配都会回滚整批删除。
                if (deleted != 1) throw new IllegalStateException("ack_row_missing");
            }
            put(db, "acked", Long.toString(selected.lastSeq())); db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }
}
