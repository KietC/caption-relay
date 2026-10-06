// Verify domain-separated signed acknowledgements without Android dependencies.
// 不依赖 Android，验证按域隔离的签名确认。
package org.captionrelay.bridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Legacy signed ACK verification, independent of Android so batch boundaries can be regression tested.
 * 旧格式签名确认验证不依赖 Android，因此可对批次边界进行回归测试。
 */
final class AckVerifier {
    static void verify(byte[] key, String expectedDevice, String expectedStream, long expectedSeq,
                       int version, String device, String stream, long seq, String signature) throws Exception {
        verifyDomain("caption-relay-ack-v1", key, expectedDevice, expectedStream, expectedSeq, version, device, stream, seq, signature);
    }
    static void verifyLive(byte[] key, String expectedDevice, String expectedStream, long expectedSeq,
                           int version, String device, String stream, long seq, boolean liveOnly, String signature) throws Exception {
        if (!liveOnly) throw new IllegalArgumentException("live_ack_marker_missing");
        verifyDomain("caption-relay-live-ack-v1", key, expectedDevice, expectedStream, expectedSeq, version, device, stream, seq, signature);
    }
    // Verify version and exact device/stream/sequence before a constant-time MAC comparison.
    // 先检查版本以及精确设备、流和序号，再进行恒定时间 MAC 比较。
    private static void verifyDomain(String domain, byte[] key, String expectedDevice, String expectedStream, long expectedSeq,
                                     int version, String device, String stream, long seq, String signature) throws Exception {
        if (version != 1 || !expectedDevice.equals(device) || !expectedStream.equals(stream) || expectedSeq != seq) {
            throw new IllegalArgumentException("ack_identity_mismatch");
        }
        if (signature == null || !signature.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException("ack_mac_invalid");
        }
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        byte[] expected = mac.doFinal((domain + "|" + expectedDevice + "|" + expectedStream + "|" + expectedSeq)
                .getBytes(StandardCharsets.UTF_8));
        byte[] actual = new byte[32];
        for (int i = 0; i < actual.length; i++) {
            actual[i] = (byte) Integer.parseInt(signature.substring(i * 2, i * 2 + 2), 16);
        }
        if (!MessageDigest.isEqual(expected, actual)) throw new IllegalArgumentException("ack_mac_invalid");
    }
}
