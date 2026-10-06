// Synthetic JVM regression tests; no live device, pairing secret, or caption history is required.
// 合成 JVM 回归测试，不需要真机、配对密钥或字幕历史。
package org.captionrelay.bridge;

public final class LiveAckTest {
    private static final String DEVICE = "phone-test", STREAM = "12345678-1234-5678-9abc-123456789abc";
    // Independent .NET vectors for separate live/durable domains; public synthetic key bytes 00..1f.
    // 使用 .NET 独立计算即时与可靠通道的分域向量；00..1f 是公开合成测试密钥。
    // Fixed vectors also live in tests/protocol_vectors.json, so protocol renames cannot silently drift.
    // 固定向量也保存在 tests/protocol_vectors.json，避免协议改名后测试基准悄悄失配。
    private static final String LIVE_MAC = "64b0491935718fcf93cce921792fd777705cad91cf0c638a43fcb3c593a7e1f3";
    private static final String DURABLE_MAC = "4ba9f9cf7a1e8de090eb66f3328590c75624ad406616aacf83e8954c44ad4fcd";
    private static int checks;
    private static byte[] key() { byte[] value = new byte[32]; for (int i = 0; i < value.length; i++) value[i] = (byte) i; return value; }
    private interface Operation { void run() throws Exception; }
    private static void denied(Operation operation, String reason) throws Exception {
        try { operation.run(); throw new AssertionError("Unexpected cross-lane ACK acceptance"); }
        catch (IllegalArgumentException failure) {
            checks++; if (!reason.equals(failure.getMessage())) throw new AssertionError("Expected " + reason, failure);
        }
    }
    public static void main(String[] args) throws Exception {
        AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, LIVE_MAC); checks++;
        AckVerifier.verify(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, DURABLE_MAC); checks++;
        denied(() -> AckVerifier.verify(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, LIVE_MAC), "ack_mac_invalid");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, DURABLE_MAC), "ack_mac_invalid");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, false, LIVE_MAC), "live_ack_marker_missing");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 99, true, LIVE_MAC), "ack_identity_mismatch");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 101, true, LIVE_MAC), "ack_identity_mismatch");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, "old-session", 100, true, LIVE_MAC), "ack_identity_mismatch");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, "other-device", STREAM, 100, true, LIVE_MAC), "ack_identity_mismatch");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 2, DEVICE, STREAM, 100, true, LIVE_MAC), "ack_identity_mismatch");
        byte[] wrongKey = key(); wrongKey[0] ^= 1;
        denied(() -> AckVerifier.verifyLive(wrongKey, DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, LIVE_MAC), "ack_mac_invalid");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, "f" + LIVE_MAC.substring(1)), "ack_mac_invalid");
        denied(() -> AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, null), "ack_mac_invalid");
        Object sent = new Object(), newer = new Object();
        LatestSlot<Object> slot = new LatestSlot<Object>(); slot.offer(sent); slot.offer(newer);
        AckVerifier.verifyLive(key(), DEVICE, STREAM, 100, 1, DEVICE, STREAM, 100, true, LIVE_MAC);
        if (slot.clearIfSame(sent) || slot.peek() != newer) throw new AssertionError("Old live ACK erased the latest capture"); checks++;
        System.out.println("LiveAck regression tests passed (" + checks + " checks)");
    }
}
