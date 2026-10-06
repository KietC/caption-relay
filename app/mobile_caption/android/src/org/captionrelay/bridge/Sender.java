// Send durable FIFO batches, verify signed ACKs, and preserve data across transient failures.
// 发送可靠 FIFO 批次，验证签名确认，并在短暂故障期间保留数据。
package org.captionrelay.bridge;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Random;
import org.json.JSONArray;
import org.json.JSONObject;

final class Sender implements Runnable {
    private final Context context;
    private final Outbox outbox;
    private final Thread thread;
    private final Random jitter = new Random();
    private final LiveSender live = new LiveSender();
    private volatile boolean closed;
    private volatile Config current;
    private volatile boolean configValid;
    private volatile boolean enabled;
    private volatile boolean pairingPaused;
    private volatile String state = "stopped";
    private volatile String lastAckAt = "";
    private volatile int lastBatchCount;
    private volatile String batchMode = "probing";
    private Config batchCapabilityConfig;
    private boolean batchUnsupported;
    private volatile HttpURLConnection active;
    private long configDue;
    private final RetryGate retries = new RetryGate();
    private final ConnectivityManager connectivity;
    private volatile Network defaultNetwork;
    private volatile JSONObject networkInfo = new JSONObject();
    private String capabilityKey = "";
    private boolean callbackRegistered;
    private final ConnectivityManager.NetworkCallback networkCallback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) { updateNetwork(network, null, true); }
        @Override public void onCapabilitiesChanged(Network network, NetworkCapabilities capabilities) {
            if (network.equals(defaultNetwork)) updateNetwork(network, capabilities, false);
        }
        @Override public void onLost(Network network) {
            if (network.equals(defaultNetwork)) updateNetwork(null, null, true);
        }
    };
    Sender(Context context, Outbox outbox) {
        this.context = context; this.outbox = outbox;
        enabled = context.getSharedPreferences("settings", 0).getBoolean("send_enabled", false);
        connectivity = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (connectivity != null) {
            try {
                Network network = connectivity.getActiveNetwork();
                updateNetwork(network, network == null ? null : connectivity.getNetworkCapabilities(network), true);
                connectivity.registerDefaultNetworkCallback(networkCallback); callbackRegistered = true;
            } catch (RuntimeException unavailable) { /* HTTP still works; network diagnostics remain best effort. */ }
            // HTTP 仍可工作；网络诊断尽力完成，不阻断主链路。
        }
        thread = new Thread(this, "caption-network"); thread.start();
    }
    boolean enabled() { return enabled; }
    boolean configured() { return current != null && configValid; }
    boolean canQueue() { return current != null; }
    synchronized void pausePairing() { pairingPaused = true; reload(); }
    synchronized void resumePairing() { pairingPaused = false; reload(); }
    synchronized boolean offerLive(JSONObject snapshot) { return live.offer(snapshot); }
    synchronized long liveGeneration() { return live.generation(); }
    synchronized void setEnabled(boolean value) {
        enabled = value;
        context.getSharedPreferences("settings", 0).edit().putBoolean("send_enabled", value).commit();
        if (!value) { HttpURLConnection request = active; if (request != null) request.disconnect(); }
        live.configure(current, value && configValid && !pairingPaused && !closed);
        wake();
    }
    synchronized void reload() {
        synchronized (this) { configDue = 0; configValid = false; retries.changed(); }
        live.suspend();
        HttpURLConnection request = active; if (request != null) request.disconnect();
        wake();
    }
    synchronized void wake() { notifyAll(); }
    synchronized void close() {
        closed = true;
        live.close();
        if (callbackRegistered) {
            try { connectivity.unregisterNetworkCallback(networkCallback); } catch (RuntimeException ignored) { }
            callbackRegistered = false;
        }
        HttpURLConnection request = active; if (request != null) request.disconnect(); wake();
    }
    synchronized void pause(long millis) { try { wait(millis); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); } }
    JSONObject status() throws Exception {
        Config config = current;
        return new JSONObject().put("enabled", enabled).put("configured", configured()).put("state", state)
                .put("endpoint_host", config == null ? "" : config.endpoint.getHost()).put("last_ack_at", lastAckAt)
                .put("last_batch_count", lastBatchCount).put("batch_mode", batchMode)
                .put("live", live.status())
                .put("default_network", networkInfo);
    }
    private synchronized void updateNetwork(Network network, NetworkCapabilities capabilities, boolean identityEvent) {
        if (closed) return;
        boolean identityChanged = network == null ? defaultNetwork != null : !network.equals(defaultNetwork);
        if (identityChanged || identityEvent) defaultNetwork = network;
        try {
            JSONArray transports = new JSONArray();
            if (capabilities != null) {
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) transports.put("wifi");
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) transports.put("cellular");
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) transports.put("vpn");
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) transports.put("ethernet");
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH)) transports.put("bluetooth");
            }
            boolean validated = capabilities != null && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
            boolean internet = capabilities != null && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
            String key = transports.toString() + "|" + validated + "|" + internet;
            boolean changed = identityChanged || !key.equals(capabilityKey);
            capabilityKey = key;
            networkInfo = new JSONObject().put("available", network != null)
                    .put("network_id", network == null ? "" : network.toString()).put("transports", transports)
                    .put("validated", validated).put("internet", internet).put("observed_at", Util.now());
            if (changed) {
                retries.changed();
                live.networkChanged(identityChanged);
                // Cancel only a request tied to a departed default network, not signal-strength updates.
                // 只取消属于已离开默认网络的请求，不因信号强度变化而取消。
                HttpURLConnection request = active;
                if (identityChanged && request != null) request.disconnect();
                wake();
            }
        } catch (Exception ignored) { /* No caption or transport data is altered by diagnostic failure. */ }
        // 诊断失败不会修改字幕或传输数据。
    }
    private synchronized void refreshConfig() {
        long now = SystemClock.elapsedRealtime();
        if (now < configDue) return;
        configDue = now + 5000;
        try {
            outbox.finishPairingWrite(context);
            Config next = Config.load(context); outbox.bind(next);
            if (!next.sameConnection(batchCapabilityConfig)) {
                batchCapabilityConfig = next; batchUnsupported = false; batchMode = "probing";
            }
            boolean changed = !configValid || !next.sameConnection(current);
            current = next; configValid = true;
            live.configure(next, enabled && !pairingPaused && !closed);
            if (changed) retries.changed();
        } catch (Exception failure) {
            configValid = false; state = errorCode(failure);
            live.suspend();
        }
    }
    private static String errorCode(Exception failure) {
        String message = failure.getMessage();
        return message != null && message.matches("[a-z_0-9]{1,80}") ? message : failure.getClass().getSimpleName();
    }
    // A transport response is not proof of durable receipt until its signed ACK has been verified.
    // 传输响应本身不证明可靠接收，必须先验证其签名确认。
    @Override public void run() {
        while (!closed) {
            if (!enabled) { state = "stopped"; pause(1000); continue; }
            if (pairingPaused) { state = "pairing"; pause(250); continue; }
            refreshConfig();
            if (!configValid) { pause(1000); continue; }
            long remaining = retries.remaining(SystemClock.elapsedRealtime());
            // Recheck configuration during prolonged offline periods; ordinary queue wakes do not reset retries.
            // 长时间离线期间重新检查配置；普通队列唤醒不重置重试退避。
            if (remaining > 0) { pause(Math.min(1000, remaining)); continue; }
            long attemptGeneration = retries.generation();
            Config attemptConfig = current;
            try {
                boolean useBatch = !batchUnsupported;
                PendingBatch selected = outbox.firstBatch(attemptConfig, useBatch ? PendingBatch.MAX_COUNT : 1);
                if (selected == null) { state = lastAckAt.isEmpty() ? "ready" : "connected"; retries.success(); pause(250); continue; }
                state = "sending";
                URL target = useBatch ? new URL(attemptConfig.endpoint.toExternalForm() + "/batch") : attemptConfig.endpoint;
                HttpURLConnection request = (HttpURLConnection) target.openConnection();
                active = request;
                request.setRequestMethod("POST"); request.setDoOutput(true); request.setInstanceFollowRedirects(false);
                request.setConnectTimeout(8000); request.setReadTimeout(10000);
                request.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                request.setRequestProperty("Authorization", "Bearer " + attemptConfig.token);
                request.setRequestProperty("User-Agent", "CaptionRelayCaptionBridge/0.3");
                byte[] payload = selected.payload(useBatch);
                request.setFixedLengthStreamingMode(payload.length);
                if (closed || !enabled || pairingPaused || !configValid || retries.generation() != attemptGeneration) { request.disconnect(); continue; }
                try (OutputStream out = request.getOutputStream()) {
                    if (closed || !enabled || pairingPaused || !configValid || retries.generation() != attemptGeneration) continue;
                    out.write(payload);
                }
                int code = request.getResponseCode();
                if (code != 200) {
                    InputStream error = request.getErrorStream();
                    if (error != null) try { Util.read(error, 16384); } catch (Exception ignored) { }
                }
                if (useBatch && PendingBatch.unsupportedStatus(code)) {
                    // Probe once per connection configuration. Keep every row, retry the head immediately.
                    // 每种连接配置只探测一次批量能力；保留所有记录，并立即从队首重试。
                    batchUnsupported = true; batchMode = "legacy"; state = "legacy_fallback";
                    continue;
                }
                if (code != 200) throw new IllegalStateException("http_" + code);
                String response = Util.read(request.getInputStream(), 16384);
                CaptionCrypto.verifyAck(attemptConfig, selected.stream, selected.lastSeq(), response);
                outbox.acknowledge(selected);
                lastBatchCount = selected.count(); batchMode = useBatch ? "batch" : "legacy";
                lastAckAt = Util.now(); state = "connected"; retries.success();
            } catch (Exception failure) {
                // Messages from networking can include a URL. Only expose short known error codes.
                // 网络错误可能包含 URL；只暴露短小且已知的错误码。
                state = errorCode(failure);
                retries.failed(attemptGeneration, SystemClock.elapsedRealtime(), jitter.nextInt(500));
            } finally {
                HttpURLConnection request = active; active = null;
                if (request != null) request.disconnect();
            }
        }
        state = "stopped";
    }
}
