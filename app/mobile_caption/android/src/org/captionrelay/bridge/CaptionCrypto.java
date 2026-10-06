// Encrypt with AES-256-GCM and authenticate device, stream, sequence, and lane identity.
// 使用 AES-256-GCM 加密，并认证设备、流、序号和通道身份。
package org.captionrelay.bridge;

import android.util.Base64;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.json.JSONObject;

final class CaptionCrypto {
    private static final SecureRandom RANDOM = new SecureRandom();
    static JSONObject encrypt(Config config, String stream, long seq, String body) throws Exception {
        return encryptDomain(config, stream, seq, body, "caption-relay-v1");
    }
    static JSONObject encryptLive(Config config, String stream, long seq, String body) throws Exception {
        return encryptDomain(config, stream, seq, body, "caption-relay-live-v1");
    }
    // Generate a fresh 96-bit nonce once per envelope; retries reuse the saved envelope instead.
    // 每个信封只生成一次新的 96 位随机 nonce；重试复用已保存信封。
    private static JSONObject encryptDomain(Config config, String stream, long seq, String body, String domain) throws Exception {
        byte[] nonce = new byte[12]; RANDOM.nextBytes(nonce);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(config.key, "AES"), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(Util.bytes(domain + "|" + config.deviceId + "|" + stream + "|" + seq));
        return new JSONObject().put("v", 1).put("device_id", config.deviceId).put("stream_id", stream).put("seq", seq)
                .put("nonce", Base64.encodeToString(nonce, Base64.NO_WRAP))
                .put("ciphertext", Base64.encodeToString(cipher.doFinal(Util.bytes(body)), Base64.NO_WRAP));
    }
    // A durable ACK must identify the exact selected queue boundary and carry a valid HMAC.
    // 可靠确认必须指向精确选定的队列边界，并带有有效 HMAC。
    static void verifyAck(Config config, String stream, long seq, String response) throws Exception {
        JSONObject ack = new JSONObject(response);
        AckVerifier.verify(config.key, config.deviceId, stream, seq, ack.getInt("v"),
                ack.getString("device_id"), ack.getString("stream_id"), ack.getLong("seq"), ack.getString("mac"));
    }
    // A live-only ACK is domain-separated and cannot authorize durable queue deletion.
    // 仅用于即时预览的确认按域隔离，不能授权删除可靠队列。
    static void verifyLiveAck(Config config, String stream, long seq, String response) throws Exception {
        JSONObject ack = new JSONObject(response);
        AckVerifier.verifyLive(config.key, config.deviceId, stream, seq, ack.getInt("v"),
                ack.getString("device_id"), ack.getString("stream_id"), ack.getLong("seq"),
                Boolean.TRUE.equals(ack.opt("live_only")), ack.getString("mac"));
    }
}
