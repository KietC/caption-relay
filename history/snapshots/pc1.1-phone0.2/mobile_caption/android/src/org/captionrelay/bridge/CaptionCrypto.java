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
        AckVerifier.verify(config.key, config.deviceId, stream, seq, ack.getInt("v"),
                ack.getString("device_id"), ack.getString("stream_id"), ack.getLong("seq"), ack.getString("mac"));
    }
}
