package org.captionrelay.bridge;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private final Handler handler = new Handler();
    private TextView status, preview;
    private EditText endpoint;
    private final Runnable refresh = new Runnable() {
        @Override public void run() { render(); handler.postDelayed(this, 1000); }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density); layout.setPadding(pad, pad + 24, pad, pad + 24);
        scroll.addView(layout); setContentView(scroll);
        TextView title = text("CaptionRelay 字幕桥", 22); layout.addView(title);
        layout.addView(text("仅同步小米已有的中英文字幕。手机与电脑可使用不同网络。不会录音或改变通话麦克风、喇叭。", 14));
        button(layout, "1. 开启无障碍权限 / Accessibility", v -> {
            Intent intent = new Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS");
            intent.putExtra(Intent.EXTRA_COMPONENT_NAME, new ComponentName(this, CaptionAccessibilityService.class));
            try { startActivity(intent); } catch (Exception unavailable) { startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)); }
        });
        button(layout, "仅在手机预览 / Local preview", v -> command("preview"));
        button(layout, "2. 开始加密发送 / Start", v -> command("start"));
        button(layout, "停止采集和发送 / Stop", v -> command("stop"));
        button(layout, "粘贴配对码 / Import pairing code", v -> pairingDialog());
        endpoint = new EditText(this); endpoint.setSingleLine(true);
        endpoint.setHint("https://.../v1/captions"); endpoint.setTextSize(14); layout.addView(endpoint);
        try { endpoint.setText(new JSONObject(Util.readFile(new File(getFilesDir(), "config.json"), 16384)).optString("endpoint")); }
        catch (Exception missing) { /* Pairing is provisioned by Codex over USB once. */ }
        button(layout, "保存电脑地址 / Save endpoint", v -> saveEndpoint());
        button(layout, "重新加载配对配置 / Reload", v -> command("reload"));
        status = text("等待无障碍服务。", 14); layout.addView(status);
        CheckBox diagnostics = new CheckBox(this); diagnostics.setText("详细诊断（平时关闭） / Troubleshooting");
        diagnostics.setChecked(getSharedPreferences("settings", 0).getBoolean("diagnostics_enabled", false));
        diagnostics.setOnCheckedChangeListener((button, checked) -> command(checked ? "diagnostics_on" : "diagnostics_off"));
        layout.addView(diagnostics);
        button(layout, "发送测试字幕 / Diagnostic", v -> new AlertDialog.Builder(this)
                .setTitle("发送一条明确标记的测试字幕？")
                .setMessage("这会写入电脑字幕记录，不是实际通话字幕。")
                .setNegativeButton("取消", null).setPositiveButton("发送测试", (dialog, which) -> {
                    boolean sent = CaptionAccessibilityService.diagnostic(this);
                    Toast.makeText(this, sent ? "测试已入队" : "请先开启权限并开始发送，确认配对配置有效", Toast.LENGTH_LONG).show();
                }).show());
        button(layout, "应用设置 / App settings", v -> startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()))));
        layout.addView(text("字幕预览 / Caption preview", 16));
        preview = text("尚未读到字幕。请保持小米通话翻译窗口可见。", 16); preview.setTextIsSelectable(true); layout.addView(preview);
        applyAction(getIntent());
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); applyAction(intent); }
    private void applyAction(Intent intent) {
        String action = intent.getStringExtra("action");
        if ("start".equals(action) || "stop".equals(action) || "reload".equals(action) || "preview".equals(action)
                || "diagnostics_on".equals(action) || "diagnostics_off".equals(action)) command(action);
        else if ("test".equals(action)) {
            boolean queued = CaptionAccessibilityService.diagnostic(this);
            Toast.makeText(this, queued ? "测试已入队" : "测试未入队：请先开始发送", Toast.LENGTH_SHORT).show();
        }
    }
    private void saveEndpoint() {
        try {
            JSONObject config = new JSONObject(Util.readFile(new File(getFilesDir(), "config.json"), 16384));
            String address = endpoint.getText().toString().trim();
            java.net.URL url = new java.net.URL(address);
            boolean local = "http".equals(url.getProtocol()) && "127.0.0.1".equals(url.getHost());
            if ((!"https".equals(url.getProtocol()) && !local) || url.getUserInfo() != null
                    || url.getQuery() != null || url.getRef() != null || url.getHost().isEmpty()) throw new IllegalArgumentException();
            config.put("endpoint", address); Util.writeJson(this, "config.json", config);
            command("reload"); Toast.makeText(this, "电脑地址已保存", Toast.LENGTH_SHORT).show();
        } catch (Exception invalid) {
            Toast.makeText(this, "先由电脑完成配对；地址需要完整 https://.../v1/captions", Toast.LENGTH_LONG).show();
        }
    }
    private void pairingDialog() {
        EditText input = new EditText(this); input.setSingleLine(true); input.setHint("CRCP1:…");
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        input.setSaveEnabled(false);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("导入电脑生成的配对码")
                .setMessage("配对码包含连接密钥，请勿分享。导入不会自动打开无障碍或开始发送。")
                .setView(input).setNegativeButton("取消", null).setNeutralButton("从剪贴板粘贴", null)
                .setPositiveButton("检查并导入", null).create();
        dialog.setOnShowListener(shown -> {
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> {
                ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                ClipData data = clipboard == null ? null : clipboard.getPrimaryClip();
                if (data != null && data.getItemCount() > 0) {
                    CharSequence value = data.getItemAt(0).getText();
                    if (value != null && value.length() <= 24006) input.setText(value);
                }
            });
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                try {
                    Config config = Config.pairingCode(input.getText().toString());
                    JSONObject plan = CaptionAccessibilityService.pairingPlan(this, config);
                    boolean replace = "replace".equals(plan.getString("kind"));
                    if (replace && (plan.getInt("queue_count") > 0 || plan.getLong("pending_gap") > 0)) {
                        Toast.makeText(this, "旧配对还有未确认字幕，禁止替换身份或密钥。先恢复旧连接并发送完。", Toast.LENGTH_LONG).show();
                        return;
                    }
                    input.setText(""); dialog.dismiss();
                    if (replace) {
                        new AlertDialog.Builder(this).setTitle("更换配对身份或密钥？")
                                .setMessage("当前没有未确认字幕。更换后会创建新字幕流；电脑已有记录不变。\n目标：" + config.endpoint.getHost()
                                        + "\n设备：" + config.deviceId + "\n只有确认更换后才会重置本机流序号。")
                                .setNegativeButton("取消", null).setPositiveButton("确认更换", (confirmation, which) -> applyPairing(config, true)).show();
                    } else applyPairing(config, false);
                } catch (Exception invalid) {
                    Toast.makeText(this, "配对码无效或配置暂不可写，请检查完整 CRCP1 配对码。", Toast.LENGTH_LONG).show();
                }
            });
        });
        dialog.show();
    }
    private void applyPairing(Config config, boolean confirmedReset) {
        try {
            CaptionAccessibilityService.importPairing(this, config, confirmedReset);
            endpoint.setText(config.endpoint.toExternalForm());
            Toast.makeText(this, "配对已保存。发送开关保持原状态；首次使用请开启权限后点击开始。", Toast.LENGTH_LONG).show();
            render();
        } catch (Exception failure) {
            String message = failure.getMessage();
            Toast.makeText(this, "pairing_blocked_pending_captions".equals(message)
                    ? "确认期间出现新的未发送字幕，已阻止更换；先停止采集并清空待发队列。"
                    : "配对未完成；原有待发字幕保留，请重试。", Toast.LENGTH_LONG).show();
        }
    }
    private void command(String action) { CaptionAccessibilityService.command(this, action); render(); }
    @Override protected void onResume() { super.onResume(); handler.post(refresh); }
    @Override protected void onPause() { handler.removeCallbacks(refresh); super.onPause(); }
    private TextView text(String value, int size) { TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setPadding(0, 8, 0, 8); return view; }
    private void button(LinearLayout parent, String label, View.OnClickListener action) { Button button = new Button(this); button.setText(label); button.setAllCaps(false); button.setOnClickListener(action); parent.addView(button); }
    private void render() {
        if (status == null) return;
        try {
            JSONObject data = new JSONObject(Util.readFile(new File(getFilesDir(), "probe.json"), 262144));
            JSONObject network = data.optJSONObject("network"), queue = data.optJSONObject("outbox");
            String state = data.optString("capture_state");
            String net = network == null ? "stopped" : network.optString("state");
            status.setText("权限连接: " + data.optBoolean("accessibility_connected") + "   采集: " + state
                    + "\n英文 " + data.optInt("original_count") + " / 中文 " + data.optInt("translation_count")
                    + "\n发送: " + net + "   排队: " + (queue == null ? 0 : queue.optInt("queue_count"))
                    + "   缺口: " + (queue == null ? 0 : queue.optInt("dropped_total"))
                    + "\n状态时间: " + data.optString("timestamp")
                    + "\n配对配置由电脑 Codex 设置；若权限被限制，请在应用信息右上角允许受限设置。");
            StringBuilder captions = new StringBuilder();
            JSONObject snapshot = data.optJSONObject("source_snapshot");
            JSONArray windows = snapshot == null ? null : snapshot.optJSONArray("windows");
            if (windows != null) for (int w = 0; w < windows.length(); w++) {
                JSONArray nodes = windows.getJSONObject(w).getJSONArray("nodes");
                for (int n = 0; n < nodes.length(); n++) {
                    JSONObject node = nodes.getJSONObject(n);
                    boolean original = node.getString("id").endsWith("/message_body")
                            || "com.xiaomi.aiasst.vision:id/recyclerView_source".equals(node.optString("container_id"));
                    captions.append(original ? "原文  " : "译文  ").append(node.getString("text")).append("\n\n");
                }
            }
            preview.setText(captions.length() == 0 ? "尚未读到字幕。请保持小米通话翻译窗口可见。" : captions.toString());
        } catch (Exception unavailable) {
            status.setText("等待无障碍权限。开启后先在本机预览，点击开始才发送。\n如系统阻止开启：应用信息 → 右上角 → 允许受限设置。");
        }
    }
}
