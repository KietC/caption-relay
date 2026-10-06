package org.captionrelay.bridge;

import android.content.Context;
import android.util.Base64;
import java.io.File;
import java.net.URL;
import org.json.JSONObject;

final class Config {
    final URL endpoint;
    final String token;
    final byte[] key;
    final String keyHash;
    final String deviceId;
    final JSONObject value;
    private Config(URL endpoint, String token, byte[] key, String deviceId) throws Exception {
        this.endpoint = endpoint; this.token = token; this.key = key;
        this.deviceId = deviceId; this.keyHash = Util.sha256(key);
        this.value = new JSONObject().put("endpoint", endpoint.toExternalForm()).put("token", token)
                .put("key_b64", Base64.encodeToString(key, Base64.NO_WRAP)).put("device_id", deviceId);
    }
    boolean sameConnection(Config other) {
        return other != null && endpoint.toExternalForm().equals(other.endpoint.toExternalForm())
                && token.equals(other.token) && keyHash.equals(other.keyHash) && deviceId.equals(other.deviceId);
    }
    static Config load(Context context) throws Exception {
        File path = new File(context.getFilesDir(), "config.json");
        if (!path.exists()) throw new IllegalArgumentException("config_missing");
        return parse(new JSONObject(Util.readFile(path, 16384)));
    }
    static Config pairingCode(String code) throws Exception {
        String value = code.trim();
        if (!value.startsWith("CRCP1:")) throw new IllegalArgumentException("pairing_code_prefix_invalid");
        String encoded = value.substring(6);
        if (encoded.length() > 24000 || !encoded.matches("[A-Za-z0-9_-]+={0,2}")) throw new IllegalArgumentException("pairing_code_encoding_invalid");
        byte[] data = Base64.decode(encoded, Base64.URL_SAFE | Base64.NO_WRAP);
        if (data.length > 16384) throw new IllegalArgumentException("pairing_code_too_large");
        Config parsed = parse(new JSONObject(new String(data, java.nio.charset.StandardCharsets.UTF_8)));
        if (!"https".equals(parsed.endpoint.getProtocol())) throw new IllegalArgumentException("pairing_requires_https");
        return parsed;
    }
    static Config parse(JSONObject value) throws Exception {
        URL url = new URL(value.getString("endpoint"));
        boolean local = "http".equals(url.getProtocol()) && "127.0.0.1".equals(url.getHost());
        if ((!"https".equals(url.getProtocol()) && !local) || url.getUserInfo() != null
                || url.getRef() != null || url.getQuery() != null || url.getHost().isEmpty()) {
            throw new IllegalArgumentException("endpoint_requires_https");
        }
        String token = value.getString("token");
        if (token.length() < 20 || token.length() > 512 || !token.matches("[A-Za-z0-9_~.\\-]+")) {
            throw new IllegalArgumentException("invalid_token");
        }
        byte[] key = Base64.decode(value.getString("key_b64"), Base64.DEFAULT);
        if (key.length != 32) throw new IllegalArgumentException("key_must_be_32_bytes");
        String device = value.getString("device_id");
        if (!device.matches("[A-Za-z0-9_-]{1,100}")) throw new IllegalArgumentException("invalid_device_id");
        return new Config(url, token, key, device);
    }
}
