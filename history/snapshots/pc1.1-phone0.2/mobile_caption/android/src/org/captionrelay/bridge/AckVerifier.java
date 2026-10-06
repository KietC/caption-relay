package org.captionrelay.bridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Legacy signed ACK verification, independent of Android so batch boundaries can be regression tested. */
final class AckVerifier {
    static void verify(byte[] key, String expectedDevice, String expectedStream, long expectedSeq,
                       int version, String device, String stream, long seq, String signature) throws Exception {
        if (version != 1 || !expectedDevice.equals(device) || !expectedStream.equals(stream) || expectedSeq != seq) {
            throw new IllegalArgumentException("ack_identity_mismatch");
        }
        if (signature == null || !signature.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("ack_mac_invalid");
        }
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        byte[] expected = mac.doFinal(("caption-relay-ack-v1|" + expectedDevice + "|" + expectedStream + "|" + expectedSeq)
                .getBytes(StandardCharsets.UTF_8));
        byte[] actual = new byte[32];
        for (int i = 0; i < actual.length; i++) {
            actual[i] = (byte) Integer.parseInt(signature.substring(i * 2, i * 2 + 2), 16);
        }
        if (!MessageDigest.isEqual(expected, actual)) throw new IllegalArgumentException("ack_mac_invalid");
    }
}
