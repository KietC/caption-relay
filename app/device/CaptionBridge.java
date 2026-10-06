// USB-only UiAutomation caption reader; this is a separate capture mode, not the ordinary app service.
// 仅通过 USB 使用 UiAutomation 读取字幕；这是独立采集模式，不是普通应用服务。
import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Read-only, package-filtered accessibility text stream, launched as the adb shell UID.
 * 以 ADB shell UID 启动的只读字幕流，按应用包过滤无障碍文字。
 */
public final class CaptionBridge {
    private static final long POLL_MS = 250;
    private static final long DEBOUNCE_MS = 40;
    private static final long HEARTBEAT_MS = 5000;
    private static final int MAX_NODES_PER_WINDOW = 5000;
    private final Set<String> packages = new LinkedHashSet<>();
    private final AtomicLong eventDue = new AtomicLong(Long.MAX_VALUE);
    private final SimpleDateFormat timestamp =
            new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT);
    private HandlerThread callbackThread;
    private UiAutomation automation;
    private Method disconnect;
    private boolean connected;
    private volatile boolean stopped;
    private long durationSeconds;
    private String previousSnapshot;

    private CaptionBridge(String[] args) {
        String packageArgument = "com.xiaomi.aiasst.vision,com.android.incallui";
        for (int i = 0; i < args.length; i++) {
            if ("--duration".equals(args[i]) && i + 1 < args.length) {
                durationSeconds = Long.parseLong(args[++i]);
                if (durationSeconds < 0 || durationSeconds > Long.MAX_VALUE / 1000) {
                    throw new IllegalArgumentException("--duration must be nonnegative seconds");
                }
            } else if ("--packages".equals(args[i]) && i + 1 < args.length) {
                packageArgument = args[++i];
            } else {
                throw new IllegalArgumentException(
                        "Usage: CaptionBridge [--duration seconds] [--packages pkg1,pkg2]");
            }
        }
        for (String value : packageArgument.split(",")) {
            String name = value.trim();
            if (!name.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)+")) {
                throw new IllegalArgumentException("Invalid or empty package name");
            }
            packages.add(name);
        }
        if (packages.isEmpty()) {
            throw new IllegalArgumentException("At least one package is required");
        }
        timestamp.setTimeZone(TimeZone.getTimeZone("UTC"));
    }

    public static void main(String[] args) {
        long processStarted = SystemClock.elapsedRealtime();
        stage("main");
        if (Looper.getMainLooper() == null) {
            Looper.prepareMainLooper();
        }
        stage("main-looper-ready");
        CaptionBridge bridge = null;
        int exitCode = 0;
        try {
            bridge = new CaptionBridge(args);
            bridge.startWatchdog(processStarted);
            final CaptionBridge active = bridge;
            Runtime.getRuntime().addShutdownHook(new Thread(active::close, "caption-cleanup"));
            bridge.run();
        } catch (Exception error) {
            System.err.println("CaptionBridge failed: " + error);
            if (error.getCause() != null) {
                System.err.println("Cause: " + error.getCause());
            }
            exitCode = 1;
        } finally {
            if (bridge != null) {
                bridge.close();
            }
        }
        System.exit(exitCode);
    }

    private void startWatchdog(long processStarted) {
        if (durationSeconds == 0) {
            return;
        }
        final long durationMs = durationSeconds * 1000;
        final long hardLimit = durationMs > Long.MAX_VALUE - 20000
                ? Long.MAX_VALUE : durationMs + 20000;
        Thread watchdog = new Thread(() -> {
            try {
                long remaining;
                while ((remaining = hardLimit
                        - (SystemClock.elapsedRealtime() - processStarted)) > 0) {
                    Thread.sleep(remaining);
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            stage("hard-deadline-exceeded-halting-124");
            Runtime.getRuntime().halt(124);
        }, "caption-hard-deadline");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    private void run() throws Exception {
        stage("start-callback-thread");
        callbackThread = new HandlerThread("caption-events");
        callbackThread.start();
        stage("create-connection");
        Class<?> connectionType = Class.forName("android.app.IUiAutomationConnection");
        Object connection = Class.forName("android.app.UiAutomationConnection")
                .getDeclaredConstructor().newInstance();
        // This constructor is present in AOSP Android 16. Reflection avoids hidden SDK imports.
        // AOSP Android 16 提供该构造器；通过反射避免引用隐藏 SDK 类型。
        Constructor<UiAutomation> constructor =
                UiAutomation.class.getConstructor(Looper.class, connectionType);
        automation = constructor.newInstance(callbackThread.getLooper(), connection);
        disconnect = UiAutomation.class.getMethod("disconnect");
        // Never call the zero-argument connect(): it suppresses existing accessibility services.
        // 绝不调用无参数 connect()；它会抑制已存在的无障碍服务。
        stage("before-connect-flags-1");
        UiAutomation.class.getMethod("connect", int.class).invoke(
                automation, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
        connected = true;
        stage("after-connect");

        stage("before-get-service-info");
        AccessibilityServiceInfo info = automation.getServiceInfo();
        stage("after-get-service-info");
        info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
        info.eventTypes = AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED
                | AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
                | AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                | AccessibilityEvent.TYPE_WINDOWS_CHANGED;
        info.packageNames = packages.toArray(new String[0]);
        info.notificationTimeout = 20;
        automation.setServiceInfo(info);
        stage("after-set-service-info");
        automation.setOnAccessibilityEventListener(event -> {
            CharSequence packageName = event.getPackageName();
            if (packageName != null && packages.contains(packageName.toString())) {
                eventDue.accumulateAndGet(SystemClock.elapsedRealtime() + DEBOUNCE_MS, Math::min);
            }
        });

        long started = SystemClock.elapsedRealtime();
        long nextPoll = started;
        long nextHeartbeat = started + HEARTBEAT_MS;
        emitStatus("ready", started);
        stage("ready");
        while (!stopped && (durationSeconds == 0
                || SystemClock.elapsedRealtime() - started < durationSeconds * 1000)) {
            long now = SystemClock.elapsedRealtime();
            long due = eventDue.get();
            if (now >= nextPoll || now >= due) {
                eventDue.compareAndSet(due, Long.MAX_VALUE);
                emitSnapshot();
                nextPoll = SystemClock.elapsedRealtime() + POLL_MS;
            }
            if (now >= nextHeartbeat) {
                emitStatus("heartbeat", started);
                nextHeartbeat = now + HEARTBEAT_MS;
            }
            Thread.sleep(20);
        }
        stage("capture-loop-finished");
    }

    private void emitSnapshot() throws Exception {
        boolean firstSnapshot = previousSnapshot == null;
        if (firstSnapshot) {
            stage("before-first-snapshot");
        }
        JSONArray windows = new JSONArray();
        List<AccessibilityWindowInfo> visibleWindows = automation.getWindows();
        if (firstSnapshot) {
            stage("first-get-windows-complete");
        }
        try {
            for (AccessibilityWindowInfo window : visibleWindows) {
                AccessibilityNodeInfo root = window.getRoot();
                if (root == null) {
                    continue;
                }
                String rootPackage = string(root.getPackageName());
                if (!packages.contains(rootPackage)) {
                    root.recycle();
                    continue;
                }
                JSONArray nodes = readNodes(root);
                Rect bounds = new Rect();
                window.getBoundsInScreen(bounds);
                windows.put(new JSONObject()
                        .put("id", window.getId())
                        .put("package", rootPackage)
                        .put("bounds", bounds(bounds))
                        .put("nodes", nodes));
            }
        } finally {
            for (AccessibilityWindowInfo window : visibleWindows) {
                window.recycle();
            }
        }
        String current = windows.toString();
        if (!current.equals(previousSnapshot)) {
            System.out.println(new JSONObject()
                    .put("type", "snapshot")
                    .put("timestamp", timestamp.format(new Date()))
                    .put("windows", windows).toString());
            System.out.flush();
            if (System.out.checkError()) {
                stopped = true;
            }
            previousSnapshot = current;
        }
        if (firstSnapshot) {
            stage("first-snapshot-complete");
        }
    }

    private void emitStatus(String type, long started) throws Exception {
        System.out.println(new JSONObject()
                .put("type", type)
                .put("timestamp", timestamp.format(new Date()))
                .put("pid", android.os.Process.myPid())
                .put("uptime_ms", SystemClock.elapsedRealtime() - started).toString());
        System.out.flush();
        if (System.out.checkError()) {
            stopped = true;
        }
    }

    private static void stage(String name) {
        System.err.println("CaptionBridge stage=" + name);
        System.err.flush();
    }

    private JSONArray readNodes(AccessibilityNodeInfo root) throws Exception {
        JSONArray result = new JSONArray();
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        ArrayDeque<String> containers = new ArrayDeque<>();
        queue.add(root);
        containers.add("");
        int visited = 0;
        try {
            while (!queue.isEmpty() && visited++ < MAX_NODES_PER_WINDOW) {
                AccessibilityNodeInfo node = queue.removeFirst();
                String container = containers.removeFirst();
                try {
                    String packageName = string(node.getPackageName());
                    if (!packages.contains(packageName)) {
                        continue; // Never read text or descendants from a foreign package.
                    }
                    String resourceId = string(node.getViewIdResourceName());
                    if (resourceId.equals("com.xiaomi.aiasst.vision:id/recyclerView_source")
                            || resourceId.equals("com.xiaomi.aiasst.vision:id/recyclerView_dest")) {
                        container = resourceId;
                    }
                    String text = string(node.getText());
                    String description = string(node.getContentDescription());
                    if (!text.isEmpty() || !description.isEmpty()) {
                        Rect bounds = new Rect();
                        node.getBoundsInScreen(bounds);
                        JSONObject captured = new JSONObject()
                                .put("package", packageName)
                                .put("id", resourceId)
                                .put("text", text)
                                .put("description", description)
                                .put("bounds", bounds(bounds));
                        if (resourceId.equals("com.xiaomi.aiasst.vision:id/sentence_id") && !container.isEmpty()) {
                            captured.put("container_id", container);
                        }
                        result.put(captured);
                    }
                    for (int i = 0; i < node.getChildCount(); i++) {
                        AccessibilityNodeInfo child = node.getChild(i);
                        if (child != null) {
                            queue.addLast(child);
                            containers.addLast(container);
                        }
                    }
                } finally {
                    node.recycle();
                }
            }
        } finally {
            while (!queue.isEmpty()) {
                queue.removeFirst().recycle();
            }
        }
        return result;
    }

    private static String string(CharSequence value) {
        return value == null ? "" : value.toString();
    }

    private static JSONArray bounds(Rect rect) {
        return new JSONArray().put(rect.left).put(rect.top).put(rect.right).put(rect.bottom);
    }

    private synchronized void close() {
        stopped = true;
        if (automation != null && connected) {
            try {
                stage("before-disconnect");
                automation.setOnAccessibilityEventListener(null);
                disconnect.invoke(automation);
                stage("after-disconnect");
            } catch (Exception error) {
                System.err.println("CaptionBridge cleanup failed: " + error.getClass().getSimpleName());
            } finally {
                connected = false;
            }
        }
        if (callbackThread != null) {
            callbackThread.quitSafely();
        }
    }
}
