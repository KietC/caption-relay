package org.captionrelay.bridge;

import android.util.Base64;
import java.security.MessageDigest;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.json.JSONObject;

final class CaptionCrypto {
    private static final SecureRandom RANDOM = new SecureRandom();
    static JSONObject encrypt(Config config, String stream, long seq, String body) throws Exception {
        byte[] nonce = new byte[12]; RANDOM.nextBytes(nonce);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(config.key, "AES"), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(Util.bytes("caption-relay-v1|" + config.deviceId + "|" + stream + "|" + seq));
        return new JSONObject().put("v", 1).put("device_id", config.deviceId).put("stream_id", stream).put("seq", seq)
                .put("nonce", Base64.encodeToString(nonce, Base64.NO_WRAP))
                .put("ciphertext", Base64.encodeToString(cipher.doFinal(Util.bytes(body)), Base64.NO_WRAP));
    }
    static void verifyAck(Config config, String stream, long seq, String response) throws Exception {
        JSONObject ack = new JSONObject(response);
        if (ack.getInt("v") != 1 || !config.deviceId.equals(ack.getString("device_id"))
                || !stream.equals(ack.getString("stream_id")) || ack.getLong("seq") != seq) {
            throw new IllegalArgumentException("ack_identity_mismatch");
        }
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(config.key, "HmacSHA256"));
        String expected = Util.hex(mac.doFinal(Util.bytes("caption-relay-ack-v1|" + config.deviceId + "|" + stream + "|" + seq)));
        String actual = ack.getString("mac");
        if (!actual.matches("[0-9a-f]{64}") || !MessageDigest.isEqual(Util.bytes(expected), Util.bytes(actual))) {
            throw new IllegalArgumentException("ack_mac_invalid");
        }
    }
}
