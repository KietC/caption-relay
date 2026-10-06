package org.captionrelay.bridge;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.UUID;
import org.json.JSONObject;

/** All mutations are synchronous and transactional; a sequence is allocated only for durable rows. */
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
            PairingPolicy.checkReplacement(replace, size(db), number(db, "dropped"), confirmedReset);
            if (replace) {
                put(db, "stream_id", UUID.randomUUID().toString()); put(db, "next_seq", "1");
                put(db, "acked", "0"); put(db, "dropped", "0"); put(db, "dropped_total", "0");
            }
            put(db, "device_id", next.deviceId); put(db, "pairing_key_hash", next.keyHash);
            // Journal the intended file update with the identity change. Recovery completes it before sending.
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
    synchronized boolean enqueue(JSONObject body, boolean heartbeat) throws Exception {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            flushGap(db);
            // Old liveness messages have no value; sequence allocation happens after this test.
            if (heartbeat && size(db) > 0) { db.setTransactionSuccessful(); return false; }
            String json = body.toString();
            boolean accepted = true;
            if (Util.bytes(json).length > 32768) { lost(db, "payload_too_large"); accepted = false; }
            else if (size(db) >= CAPACITY) { if (!heartbeat) lost(db, "outbox_full"); accepted = false; }
            else append(db, json);
            db.setTransactionSuccessful(); return accepted;
        } finally { db.endTransaction(); }
    }
    static final class Pending {
        final long seq; final String envelope;
        Pending(long seq, String envelope) { this.seq = seq; this.envelope = envelope; }
    }
    synchronized Pending first(Config config) throws Exception {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            flushGap(db);
            Pending result = null;
            try (Cursor c = db.query("outbox", new String[]{"seq", "body", "envelope", "key_hash"}, null, null, null, null, "seq ASC", "1")) {
                if (c.moveToFirst()) {
                    long seq = c.getLong(0); String envelope = c.getString(2);
                    if (envelope == null) {
                        envelope = CaptionCrypto.encrypt(config, stream(), seq, c.getString(1)).toString();
                        ContentValues values = new ContentValues(); values.put("envelope", envelope); values.put("key_hash", config.keyHash);
                        db.update("outbox", values, "seq=?", new String[]{Long.toString(seq)});
                    } else if (!config.keyHash.equals(c.getString(3))) {
                        throw new IllegalStateException("key_changed_with_pending_data");
                    }
                    result = new Pending(seq, envelope);
                }
            }
            db.setTransactionSuccessful(); return result;
        } finally { db.endTransaction(); }
    }
    synchronized void acknowledge(Pending row) {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            int deleted = db.delete("outbox", "seq=? AND envelope=?", new String[]{Long.toString(row.seq), row.envelope});
            if (deleted != 1) throw new IllegalStateException("ack_row_missing");
            put(db, "acked", Long.toString(row.seq)); db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }
}
