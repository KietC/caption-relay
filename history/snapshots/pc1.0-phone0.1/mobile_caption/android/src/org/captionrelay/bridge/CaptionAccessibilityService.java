package org.captionrelay.bridge;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.Context;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Build;
import android.os.Looper;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.lang.ref.WeakReference;
import java.util.ArrayDeque;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Ordinary app accessibility service: no shell UiAutomation, gestures, or audio capture. */
public final class CaptionAccessibilityService extends AccessibilityService {
    private static final String PACKAGE = "com.xiaomi.aiasst.vision";
    private static final String SOURCE_CONTAINER = PACKAGE + ":id/recyclerView_source";
    private static final String DEST_CONTAINER = PACKAGE + ":id/recyclerView_dest";
    private static final String COMPACT_SENTENCE = PACKAGE + ":id/sentence_id";
    private static WeakReference<CaptionAccessibilityService> instance = new WeakReference<>(null);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Outbox outbox;
    private Sender sender;
    private JSONObject snapshot = new JSONObject();
    private String previousWindows = null;
    private String lastQueuedWindows = null;
    private String lastRejectedWindows = null;
    private String captureState = "waiting";
    private String lastSnapshotAt = "";
    private String lastCaptureError = "";
    private JSONObject accessibilityProbe = new JSONObject();
    private JSONObject lastEvent = new JSONObject();
    private JSONObject extraWindowProbe = new JSONObject();
    private long nextExtraWindowProbe;
    private long eventCount, readCount;
    private String lastCacheRefresh = "";
    private boolean lastCacheClearResult;
    private int originalCount, translationCount;
    private long updates, nextHeartbeat, nextProbe, nextSnapshotRefresh;
    private boolean connected;
    private boolean eventScheduled;
    private final Runnable eventPoll = new Runnable() {
        @Override public void run() {
            eventScheduled = false; handler.removeCallbacks(poll); handler.post(poll);
        }
    };
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (!connected) return;
            tick(); handler.postDelayed(this, 400);
        }
    };
    static void command(Context context, String action) {
        if ("diagnostics_on".equals(action) || "diagnostics_off".equals(action)) {
            context.getSharedPreferences("settings", 0).edit().putBoolean("diagnostics_enabled", "diagnostics_on".equals(action)).commit();
            return;
        }
        boolean send = "start".equals(action);
        boolean capture = send || "preview".equals(action);
        if (!"reload".equals(action)) {
            context.getSharedPreferences("settings", 0).edit().putBoolean("send_enabled", send)
                    .putBoolean("capture_enabled", capture).commit();
        }
        CaptionAccessibilityService service = instance.get();
        if (service != null) {
            if (!"reload".equals(action)) service.sender.setEnabled(send);
            if ("reload".equals(action)) service.refreshAccessibility();
            service.sender.reload(); service.lastQueuedWindows = null; service.lastRejectedWindows = null;
            service.handler.removeCallbacks(service.poll); service.handler.post(service.poll);
        }
    }
    static JSONObject pairingPlan(Context context, Config next) throws Exception {
        CaptionAccessibilityService service = instance.get();
        Outbox queue = service == null ? new Outbox(context) : service.outbox;
        try { return queue.pairingPlan(context, next); }
        finally { if (service == null) queue.close(); }
    }
    static void importPairing(Context context, Config next, boolean confirmedReset) throws Exception {
        CaptionAccessibilityService service = instance.get();
        Outbox queue = service == null ? new Outbox(context) : service.outbox;
        if (service != null) service.sender.pausePairing();
        try {
            queue.importPairing(context, next, confirmedReset);
            if (service != null) { service.lastQueuedWindows = null; service.lastRejectedWindows = null; service.nextSnapshotRefresh = 0; }
        } finally {
            if (service == null) queue.close();
            else service.sender.resumePairing();
        }
    }
    @Override protected void onServiceConnected() {
        refreshAccessibility();
        connected = true; instance = new WeakReference<>(this);
        if (outbox == null) outbox = new Outbox(this);
        if (sender == null) sender = new Sender(this, outbox);
        handler.removeCallbacks(poll); handler.post(poll);
    }
    private void refreshAccessibility() {
        AccessibilityServiceInfo info = getServiceInfo();
        info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
        info.packageNames = new String[]{PACKAGE, "com.android.incallui"};
        setServiceInfo(info);
        if (Build.VERSION.SDK_INT >= 33) lastCacheClearResult = clearCache();
        lastCacheRefresh = Util.now();
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (!connected) return;
        String pkg = string(event.getPackageName());
        eventCount++;
        try {
            lastEvent = new JSONObject().put("package", pkg).put("type", event.getEventType())
                    .put("window_id", event.getWindowId()).put("at", Util.now());
        } catch (Exception ignored) { }
        if ((pkg.isEmpty() || allowed(pkg)) && !eventScheduled) {
            // Schedule from the first event; an ongoing event stream must not postpone capture forever.
            eventScheduled = true; handler.postDelayed(eventPoll, 60);
        }
    }
    @Override public void onInterrupt() { captureState = "accessibility_interrupted"; writeProbe(); }
    @Override public void onDestroy() {
        connected = false; instance.clear(); handler.removeCallbacksAndMessages(null);
        if (sender != null) sender.close();
        captureState = "accessibility_disconnected"; writeProbe();
        // The sender may be finishing an HTTP request; it owns no further capture and exits itself.
        super.onDestroy();
    }
    private boolean capturing() { return getSharedPreferences("settings", 0).getBoolean("capture_enabled", true); }
    private void tick() {
        try {
            if (capturing()) {
                JSONArray windows = readWindows();
                lastCaptureError = "";
                String value = windows.toString();
                if (!value.equals(previousWindows)) {
                    snapshot = new JSONObject().put("type", "snapshot").put("timestamp", Util.now()).put("windows", windows);
                    previousWindows = value; lastSnapshotAt = snapshot.getString("timestamp"); updates++;
                    countCaptions(windows);
                }
                captureState = originalCount + translationCount > 0 ? "captions_visible" : "waiting_for_caption_nodes";
                if (sender.enabled() && sender.canQueue() && (!value.equals(lastQueuedWindows)
                        || SystemClock.elapsedRealtime() >= nextSnapshotRefresh)) {
                    if (!value.equals(lastRejectedWindows) || outbox.stats().getInt("queue_count") < Outbox.CAPACITY - 1) {
                        // Timestamp refers to capture; source text is never regenerated or translated here.
                        if (outbox.enqueue(new JSONObject(snapshot.toString()).put("timestamp", Util.now()), false)) {
                            lastQueuedWindows = value; lastRejectedWindows = null;
                            nextSnapshotRefresh = SystemClock.elapsedRealtime() + 15000;
                        }
                        else lastRejectedWindows = value;
                        sender.wake();
                    }
                }
                long now = SystemClock.elapsedRealtime();
                if (sender.enabled() && sender.canQueue() && now >= nextHeartbeat) {
                    outbox.enqueue(new JSONObject().put("type", "heartbeat").put("timestamp", Util.now())
                            .put("captions_visible", originalCount + translationCount > 0), true);
                    nextHeartbeat = now + 5000; sender.wake();
                }
            } else captureState = "stopped";
        } catch (Exception error) {
            captureState = "capture_error_" + error.getClass().getSimpleName();
            String message = error.getMessage();
            lastCaptureError = error.getClass().getSimpleName()
                    + (message != null && message.matches("[a-z_0-9]{1,80}") ? ":" + message : "");
        }
        if (SystemClock.elapsedRealtime() >= nextProbe) { writeProbe(); nextProbe = SystemClock.elapsedRealtime() + 1000; }
    }
    private JSONArray readWindows() throws Exception {
        JSONArray result = new JSONArray();
        JSONArray metadata = new JSONArray();
        boolean diagnostics = getSharedPreferences("settings", 0).getBoolean("diagnostics_enabled", false);
        accessibilityProbe = new JSONObject().put("enabled", diagnostics).put("at", Util.now()).put("read_count", ++readCount);
        if (diagnostics) {
            accessibilityProbe.put("windows", metadata);
            AccessibilityServiceInfo serviceInfo = getServiceInfo();
            accessibilityProbe.put("service_flags", serviceInfo.flags).put("service_capabilities", serviceInfo.getCapabilities())
                    .put("service_event_types", serviceInfo.eventTypes).put("sdk", Build.VERSION.SDK_INT);
            if (Build.VERSION.SDK_INT >= 33) accessibilityProbe.put("cache_enabled", isCacheEnabled());
            JSONArray configuredPackages = new JSONArray();
            if (serviceInfo.packageNames != null) for (String name : serviceInfo.packageNames) configuredPackages.put(name);
            accessibilityProbe.put("service_packages", configuredPackages);
        }
        List<AccessibilityWindowInfo> windows = getWindows();
        accessibilityProbe.put("get_windows_count", windows.size());
        int allowedWindows = 0, presentRoots = 0;
        try {
            for (AccessibilityWindowInfo window : windows) {
                JSONObject detail = null;
                if (diagnostics) {
                    detail = new JSONObject().put("id", window.getId()).put("type", window.getType())
                            .put("active", window.isActive()).put("focused", window.isFocused());
                    if (Build.VERSION.SDK_INT >= 30) detail.put("display_id", window.getDisplayId());
                    metadata.put(detail);
                }
                AccessibilityNodeInfo root = window.getRoot();
                if (detail != null) detail.put("root_present", root != null);
                if (root == null) continue;
                presentRoots++;
                String pkg = string(root.getPackageName());
                if (detail != null) detail.put("root_package", pkg).put("allowed_root", allowed(pkg));
                if (!allowed(pkg)) { root.recycle(); continue; }
                allowedWindows++;
                // Only caption-package titles are read. Other app metadata never includes visible text.
                if (detail != null) {
                    detail.put("title", string(window.getTitle())).put("root_class", string(root.getClassName()))
                            .put("root_id", string(root.getViewIdResourceName())).put("root_child_count", root.getChildCount());
                    if (Build.VERSION.SDK_INT >= 34) detail.put("root_data_sensitive", root.isAccessibilityDataSensitive());
                }
                JSONArray nodes = readNodes(root, detail);
                if (detail != null) detail.put("captured_caption_nodes", nodes.length());
                if (nodes.length() == 0) continue;
                if (result.length() >= 16) throw new IllegalStateException("caption_window_limit");
                Rect rect = new Rect(); window.getBoundsInScreen(rect);
                result.put(new JSONObject().put("id", window.getId()).put("package", pkg)
                        .put("bounds", bounds(rect)).put("nodes", nodes));
            }
        } finally {
            accessibilityProbe.put("allowed_window_count", allowedWindows).put("present_root_count", presentRoots);
            for (AccessibilityWindowInfo window : windows) window.recycle();
        }
        if (diagnostics && result.length() == 0 && SystemClock.elapsedRealtime() >= nextExtraWindowProbe) {
            extraWindowProbe = readExtraWindowMetadata(); nextExtraWindowProbe = SystemClock.elapsedRealtime() + 2000;
        }
        if (diagnostics) accessibilityProbe.put("extra_window_metadata", extraWindowProbe);
        return result;
    }
    private JSONObject readExtraWindowMetadata() throws Exception {
        JSONObject extra = new JSONObject().put("at", Util.now());
        AccessibilityNodeInfo activeRoot = getRootInActiveWindow();
        try {
            extra.put("active_root_present", activeRoot != null);
            if (activeRoot != null) extra.put("active_root_package", string(activeRoot.getPackageName()))
                    .put("active_root_child_count", activeRoot.getChildCount());
        } finally { if (activeRoot != null) activeRoot.recycle(); }
        if (Build.VERSION.SDK_INT >= 30) {
            JSONArray all = new JSONArray(); extra.put("all_displays", all);
            android.util.SparseArray<List<AccessibilityWindowInfo>> displays = getWindowsOnAllDisplays();
            for (int d = 0; d < displays.size(); d++) {
                JSONArray details = new JSONArray();
                all.put(new JSONObject().put("display_id", displays.keyAt(d)).put("windows", details));
                List<AccessibilityWindowInfo> windows = displays.valueAt(d);
                try {
                    for (AccessibilityWindowInfo window : windows) {
                        JSONObject detail = new JSONObject().put("id", window.getId()).put("type", window.getType());
                        AccessibilityNodeInfo root = window.getRoot();
                        try {
                            detail.put("root_present", root != null);
                            if (root != null) detail.put("root_package", string(root.getPackageName()));
                        } finally { if (root != null) root.recycle(); }
                        details.put(detail);
                    }
                } finally { for (AccessibilityWindowInfo window : windows) window.recycle(); }
            }
        }
        return extra;
    }
    private JSONArray readNodes(AccessibilityNodeInfo root, JSONObject detail) throws Exception {
        JSONArray nodes = new JSONArray();
        JSONArray targetNodes = new JSONArray();
        JSONArray knownIds = new JSONArray();
        if (detail != null) detail.put("target_nodes", targetNodes).put("encountered_ids", knownIds);
        ArrayDeque<NodeContext> queue = new ArrayDeque<>(); queue.add(new NodeContext(root, ""));
        int visited = 0, skippedForeign = 0, skippedNullPackage = 0, nullChildren = 0;
        try {
            while (!queue.isEmpty() && visited++ < 5000) {
                NodeContext current = queue.removeFirst();
                AccessibilityNodeInfo node = current.node;
                try {
                    String pkg = string(node.getPackageName());
                    if (!allowed(pkg)) { if (pkg.isEmpty()) skippedNullPackage++; else skippedForeign++; continue; }
                    String id = string(node.getViewIdResourceName());
                    String container = current.container;
                    if (PACKAGE.equals(pkg) && (SOURCE_CONTAINER.equals(id) || DEST_CONTAINER.equals(id))) container = id;
                    if (detail != null && !id.isEmpty() && knownIds.length() < 128) knownIds.put(id);
                    boolean legacy = id.equals(PACKAGE + ":id/message_body") || id.equals(PACKAGE + ":id/tv_dest_message");
                    boolean compact = COMPACT_SENTENCE.equals(id) && (SOURCE_CONTAINER.equals(container) || DEST_CONTAINER.equals(container));
                    if (PACKAGE.equals(pkg) && (legacy || compact)) {
                        String text = string(node.getText());
                        if (detail != null && targetNodes.length() < 128) {
                            JSONObject target = new JSONObject().put("id", id).put("text_length", text.length())
                                    .put("visible_to_user", node.isVisibleToUser()).put("child_count", node.getChildCount());
                            if (compact) target.put("container_id", container);
                            if (Build.VERSION.SDK_INT >= 34) target.put("data_sensitive", node.isAccessibilityDataSensitive());
                            targetNodes.put(target);
                        }
                        if (!text.trim().isEmpty()) {
                            if (nodes.length() >= 128 || text.length() > 16384) throw new IllegalStateException("caption_node_limit");
                            Rect rect = new Rect(); node.getBoundsInScreen(rect);
                            JSONObject captured = new JSONObject().put("package", pkg).put("id", id).put("text", text)
                                    .put("description", "").put("bounds", bounds(rect));
                            if (compact) captured.put("container_id", container);
                            nodes.put(captured);
                        }
                    }
                    for (int i = 0; i < node.getChildCount(); i++) {
                        AccessibilityNodeInfo child = node.getChild(i);
                        if (child != null) queue.addLast(new NodeContext(child, container)); else nullChildren++;
                    }
                } finally { node.recycle(); }
            }
            if (!queue.isEmpty()) throw new IllegalStateException("accessibility_tree_limit");
        } finally {
            if (detail != null) detail.put("visited_nodes", visited).put("skipped_foreign_nodes", skippedForeign)
                    .put("skipped_null_package_nodes", skippedNullPackage).put("null_children", nullChildren);
            while (!queue.isEmpty()) queue.removeFirst().node.recycle();
        }
        return nodes;
    }
    private static final class NodeContext {
        final AccessibilityNodeInfo node;
        final String container;
        NodeContext(AccessibilityNodeInfo node, String container) { this.node = node; this.container = container; }
    }
    private void countCaptions(JSONArray windows) throws Exception {
        originalCount = translationCount = 0;
        for (int w = 0; w < windows.length(); w++) {
            JSONArray nodes = windows.getJSONObject(w).getJSONArray("nodes");
            for (int n = 0; n < nodes.length(); n++) {
                JSONObject node = nodes.getJSONObject(n);
                if (node.getString("id").endsWith("/message_body") || SOURCE_CONTAINER.equals(node.optString("container_id"))) originalCount++;
                else translationCount++;
            }
        }
    }
    private void writeProbe() {
        try {
            Util.writeJson(this, "probe.json", new JSONObject().put("timestamp", Util.now())
                    .put("accessibility_connected", connected).put("capture_enabled", capturing())
                    .put("capture_state", captureState).put("original_count", originalCount).put("translation_count", translationCount)
                    .put("snapshot_updates", updates).put("last_snapshot_at", lastSnapshotAt)
                    .put("accessibility_diagnostics", accessibilityProbe).put("accessibility_event_count", eventCount)
                    .put("last_accessibility_event", lastEvent).put("last_capture_error", lastCaptureError)
                    .put("last_cache_refresh", lastCacheRefresh).put("last_cache_clear_result", lastCacheClearResult)
                    .put("network", sender == null ? JSONObject.NULL : sender.status())
                    .put("outbox", outbox == null ? JSONObject.NULL : outbox.stats()).put("source_snapshot", snapshot));
        } catch (Exception ignored) { /* Private status file is diagnostic; durable caption writes use the database. */ }
    }
    static boolean diagnostic(Context context) {
        CaptionAccessibilityService service = instance.get();
        if (service == null || !service.sender.enabled() || !service.sender.configured()) return false;
        try {
            String marker = "[DIAGNOSTIC " + Util.now() + "] ";
            JSONArray nodes = new JSONArray();
            nodes.put(new JSONObject().put("package", PACKAGE).put("id", PACKAGE + ":id/message_body")
                    .put("text", marker + "Connection test; not a real caption.").put("description", "").put("bounds", new JSONArray("[0,0,1,1]")));
            nodes.put(new JSONObject().put("package", PACKAGE).put("id", PACKAGE + ":id/tv_dest_message")
                    .put("text", marker + "连接测试；不是实际字幕。").put("description", "").put("bounds", new JSONArray("[0,1,1,2]")));
            JSONObject body = new JSONObject().put("type", "snapshot").put("timestamp", Util.now())
                    .put("windows", new JSONArray().put(new JSONObject().put("id", -1).put("package", PACKAGE)
                            .put("bounds", new JSONArray("[0,0,1,2]")).put("nodes", nodes)));
            boolean accepted = service.outbox.enqueue(body, false); service.sender.wake(); return accepted;
        } catch (Exception error) { return false; }
    }
    private static boolean allowed(String pkg) { return PACKAGE.equals(pkg) || "com.android.incallui".equals(pkg); }
    private static String string(CharSequence value) { return value == null ? "" : value.toString(); }
    private static JSONArray bounds(Rect value) { return new JSONArray().put(value.left).put(value.top).put(value.right).put(value.bottom); }
}
