package org.captionrelay.bridge;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
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
        }
        thread = new Thread(this, "caption-network"); thread.start();
    }
    boolean enabled() { return enabled; }
    boolean configured() { return current != null && configValid; }
    boolean canQueue() { return current != null; }
    void pausePairing() { pairingPaused = true; reload(); }
    void resumePairing() { pairingPaused = false; reload(); }
    void setEnabled(boolean value) {
        enabled = value;
        context.getSharedPreferences("settings", 0).edit().putBoolean("send_enabled", value).commit();
        if (!value) { HttpURLConnection request = active; if (request != null) request.disconnect(); }
        wake();
    }
    void reload() {
        synchronized (this) { configDue = 0; configValid = false; retries.changed(); }
        HttpURLConnection request = active; if (request != null) request.disconnect();
        wake();
    }
    synchronized void wake() { notifyAll(); }
    void close() {
        closed = true;
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
                // Cancel only a request tied to a departed default network, not signal-strength updates.
                HttpURLConnection request = active;
                if (identityChanged && request != null) request.disconnect();
                wake();
            }
        } catch (Exception ignored) { /* No caption or transport data is altered by diagnostic failure. */ }
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
            if (changed) retries.changed();
        } catch (Exception failure) {
            configValid = false; state = errorCode(failure);
        }
    }
    private static String errorCode(Exception failure) {
        String message = failure.getMessage();
        return message != null && message.matches("[a-z_0-9]{1,80}") ? message : failure.getClass().getSimpleName();
    }
    @Override public void run() {
        while (!closed) {
            if (!enabled) { state = "stopped"; pause(1000); continue; }
            if (pairingPaused) { state = "pairing"; pause(250); continue; }
            refreshConfig();
            if (!configValid) { pause(1000); continue; }
            long remaining = retries.remaining(SystemClock.elapsedRealtime());
            // Recheck configuration during prolonged offline periods; ordinary queue wakes do not reset retries.
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
                request.setRequestProperty("User-Agent", "CaptionRelayCaptionBridge/0.2");
                request.setRequestProperty("Connection", "close");
                byte[] payload = selected.payload(useBatch);
                request.setFixedLengthStreamingMode(payload.length);
                if (closed || !enabled || pairingPaused || !configValid || retries.generation() != attemptGeneration) { request.disconnect(); continue; }
                try (OutputStream out = request.getOutputStream()) { out.write(payload); }
                int code = request.getResponseCode();
                if (useBatch && PendingBatch.unsupportedStatus(code)) {
                    // Probe once per connection configuration. Keep every row, retry the head immediately.
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
