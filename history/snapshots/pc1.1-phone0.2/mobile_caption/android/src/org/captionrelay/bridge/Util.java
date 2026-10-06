package org.captionrelay.bridge;

import android.content.Context;
import android.util.AtomicFile;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import org.json.JSONObject;

final class Util {
    static String now() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }
    static byte[] bytes(String value) { return value.getBytes(StandardCharsets.UTF_8); }
    static String read(InputStream input, int limit) throws Exception {
        try (InputStream in = input; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[4096];
            int len;
            while ((len = in.read(buf)) != -1) {
                if (out.size() + len > limit) throw new IllegalArgumentException("response_too_large");
                out.write(buf, 0, len);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }
    static String readFile(File path, int limit) throws Exception { return read(new FileInputStream(path), limit); }
    static void writeJson(Context context, String name, JSONObject value) throws Exception {
        AtomicFile file = new AtomicFile(new File(context.getFilesDir(), name));
        FileOutputStream out = null;
        try {
            out = file.startWrite();
            out.write(bytes(value.toString()));
            file.finishWrite(out);
        } catch (Exception failure) {
            if (out != null) file.failWrite(out);
            throw failure;
        }
    }
    static String hex(byte[] value) {
        StringBuilder out = new StringBuilder();
        for (byte b : value) out.append(String.format(Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }
    static String sha256(byte[] value) throws Exception { return hex(MessageDigest.getInstance("SHA-256").digest(value)); }
}
