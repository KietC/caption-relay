// Send the latest preview independently; never delete reliable outbox rows.
// 独立发送最新预览，绝不删除可靠待发队列记录。
package org.captionrelay.bridge;

import android.os.SystemClock;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Random;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/**
 * Volatile preview lane. Deliberately has no Outbox reference or durable acknowledgement operation.
 * 易失预览通道，刻意不引用 Outbox，也不执行可靠队列确认。
 */
final class LiveSender implements Runnable {
    private static final int RESPONSE_LIMIT = 16384;
    private static final long REQUEST_DEADLINE_MS = 6000;
    private final LatestSlot<Item> latest = new LatestSlot<Item>();
    private final LiveRetryGate retries = new LiveRetryGate();
    private final Random jitter = new Random();
    private final ScheduledExecutorService deadlines = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "caption-live-deadline"); thread.setDaemon(true); return thread;
    });
    private final Thread thread;
    private volatile HttpURLConnection active;
    private Config config;
    private boolean enabled, closed, unsupported, authBlocked;
    private long generation, nextSeq = 1, offeredCount, replacedCount, confirmedCount, lastAckSeq;
    private String session = UUID.randomUUID().toString();
    private String state = "stopped", lastAckAt = "";
    private long lastRttMs = -1;

    private static final class Item {
        final Config config;
        final String stream, body;
        final long seq, generation;
        byte[] payload;
        Item(Config config, String stream, long seq, long generation, String body) {
            this.config = config; this.stream = stream; this.seq = seq; this.generation = generation; this.body = body;
        }
        byte[] payload() throws Exception {
            // A retry reuses this exact ciphertext. Only the single live worker calls this method.
            // 重试复用同一密文；只有单个即时工作线程调用此方法。
            if (payload == null) payload = Util.bytes(CaptionCrypto.encryptLive(config, stream, seq, body).toString());
            return payload;
        }
    }

    LiveSender() { thread = new Thread(this, "caption-live-network"); thread.start(); }

    // Connection generations retire obsolete in-flight work; preview sessions have independent sequence spaces.
    // 连接代数淘汰过时的在途请求；预览会话使用独立序号空间。
    synchronized void configure(Config next, boolean sending) {
        if (closed) return;
        boolean changed = next == null ? config != null : !next.sameConnection(config);
        if (changed) {
            config = next; session = UUID.randomUUID().toString(); nextSeq = 1;
            unsupported = false; authBlocked = false; lastAckSeq = 0; lastRttMs = -1; lastAckAt = "";
        }
        boolean nextEnabled = sending && next != null;
        if (changed || nextEnabled != enabled) {
            generation++; retries.changed(); latest.clear(); cancelActive();
        }
        enabled = nextEnabled;
        if (!enabled) state = "stopped";
        else if (unsupported) state = "unsupported";
        else if (authBlocked) state = "auth_blocked";
        else if (!authBlocked && (changed || "stopped".equals(state))) state = "ready";
        notifyAll();
    }

    synchronized void suspend() { configure(config, false); }
    synchronized long generation() { return generation; }

    // Overwrite only the volatile slot; the reliable sender continues handling every persisted row.
    // 只覆盖易失槽位；可靠发送器继续处理每条已持久记录。
    synchronized boolean offer(JSONObject snapshot) {
        if (closed || !enabled || config == null || unsupported || authBlocked) return false;
        if (!"snapshot".equals(snapshot.optString("type"))) return false;
        String body = snapshot.toString();
        if (Util.bytes(body).length > 32768) { state = "live_snapshot_too_large"; return false; }
        if (nextSeq == Long.MAX_VALUE) {
            generation++; retries.changed(); session = UUID.randomUUID().toString(); nextSeq = 1;
            latest.clear(); cancelActive();
        }
        if (latest.peek() != null) replacedCount++;
        latest.offer(new Item(config, session, nextSeq++, generation, body)); offeredCount++;
        notifyAll(); return true;
    }

    synchronized void networkChanged(boolean cancelRequest) {
        if (closed) return;
        retries.changed();
        if (cancelRequest) cancelActive();
        notifyAll();
    }

    synchronized void close() {
        if (closed) return;
        closed = true; enabled = false; generation++; latest.clear(); cancelActive();
        state = "stopped"; notifyAll(); deadlines.shutdownNow();
    }

    private void cancelActive() {
        HttpURLConnection request = active;
        if (request != null) request.disconnect();
    }

    synchronized JSONObject status() throws Exception {
        return new JSONObject().put("enabled", enabled).put("state", state).put("live_only", true)
                .put("pending_latest", latest.peek() != null).put("offered_count", offeredCount)
                .put("replaced_count", replacedCount).put("confirmed_count", confirmedCount)
                .put("last_ack_seq", lastAckSeq).put("last_ack_at", lastAckAt).put("last_rtt_ms", lastRttMs);
    }

    private synchronized Item nextItem() throws InterruptedException {
        while (!closed) {
            if (!enabled || config == null || unsupported || authBlocked) { wait(); continue; }
            long remaining = retries.remaining(SystemClock.elapsedRealtime());
            if (remaining > 0) { wait(remaining); continue; }
            Item item = latest.peek();
            if (item != null) return item;
            // offer() checks and signals under this same monitor, so an empty-queue wake cannot be lost.
            // offer() 在同一监视器内检查并通知，空队列的唤醒不会丢失。
            wait();
        }
        return null;
    }

    // A cancelled attempt may complete later; generation and connection checks prevent stale state updates.
    // 取消的请求可能稍后才结束；用代数和连接检查阻止过时请求更新状态。
    private synchronized boolean current(Item item) {
        return !closed && enabled && item.generation == generation && item.config.sameConnection(config);
    }

    private static String errorCode(Exception failure) {
        String message = failure.getMessage();
        return message != null && message.matches("[a-z_0-9]{1,80}") ? message : failure.getClass().getSimpleName();
    }

    // Bound connect/read/whole-request time separately, then clear only this exact acknowledged object.
    // 分别限制连接、读取和整次请求时间，随后只清除本次确认的精确对象。
    @Override public void run() {
        while (true) {
            Item item;
            try { item = nextItem(); }
            catch (InterruptedException interrupted) { continue; }
            if (item == null) return;
            long retryGeneration = retries.generation();
            HttpURLConnection request = null;
            ScheduledFuture<?> deadline = null;
            AtomicBoolean expired = new AtomicBoolean(false);
            try {
                byte[] payload = item.payload();
                request = (HttpURLConnection) new URL(item.config.endpoint.toExternalForm() + "/live").openConnection();
                synchronized (this) {
                    if (!current(item)) { request.disconnect(); continue; }
                    active = request; state = "sending";
                }
                final HttpURLConnection timedRequest = request;
                deadline = deadlines.schedule(() -> {
                    if (active == timedRequest) { expired.set(true); timedRequest.disconnect(); }
                }, REQUEST_DEADLINE_MS, TimeUnit.MILLISECONDS);
                request.setRequestMethod("POST"); request.setDoOutput(true); request.setInstanceFollowRedirects(false);
                request.setConnectTimeout(3000); request.setReadTimeout(3000);
                request.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                request.setRequestProperty("Authorization", "Bearer " + item.config.token);
                request.setRequestProperty("User-Agent", "CaptionRelayCaptionBridge/0.3-live");
                request.setFixedLengthStreamingMode(payload.length);
                long started = SystemClock.elapsedRealtime();
                if (!current(item)) continue;
                if (expired.get()) throw new IllegalStateException("live_request_deadline");
                try (OutputStream out = request.getOutputStream()) {
                    // Cancellation before HTTP initialization can precede a socket to disconnect.
                    // HTTP 初始化前的取消，可能早于可断开的套接字创建；写入前需再次检查。
                    if (!current(item)) continue;
                    if (expired.get()) throw new IllegalStateException("live_request_deadline");
                    out.write(payload);
                }
                int code = request.getResponseCode();
                if (code != 200) {
                    InputStream error = request.getErrorStream();
                    if (error != null) try { Util.read(error, RESPONSE_LIMIT); } catch (Exception ignored) { }
                    synchronized (this) {
                        if (!current(item)) continue;
                        if (PendingBatch.unsupportedStatus(code)) {
                            unsupported = true; state = "unsupported"; latest.clear(); continue;
                        }
                        if (code == 401 || code == 403) {
                            authBlocked = true; state = "http_" + code; latest.clear(); continue;
                        }
                    }
                    throw new IllegalStateException("http_" + code);
                }
                String response = Util.read(request.getInputStream(), RESPONSE_LIMIT);
                if (expired.get()) throw new IllegalStateException("live_request_deadline");
                CaptionCrypto.verifyLiveAck(item.config, item.stream, item.seq, response);
                synchronized (this) {
                    if (!current(item)) continue;
                    latest.clearIfSame(item); // Never erase a newer preview offered while this request was in flight.
                    // 绝不能删除在本请求进行期间提交的更新预览。
                    lastRttMs = Math.max(0, SystemClock.elapsedRealtime() - started);
                    lastAckSeq = item.seq; lastAckAt = Util.now(); confirmedCount++; state = "connected"; retries.success();
                }
            } catch (Exception failure) {
                synchronized (this) {
                    if (current(item)) {
                        state = expired.get() ? "live_request_deadline" : errorCode(failure);
                        retries.failed(retryGeneration, SystemClock.elapsedRealtime(), jitter.nextInt(100));
                    }
                }
            } finally {
                if (deadline != null) deadline.cancel(false);
                synchronized (this) { if (active == request) active = null; }
                if (request != null) request.disconnect();
            }
        }
    }
}
