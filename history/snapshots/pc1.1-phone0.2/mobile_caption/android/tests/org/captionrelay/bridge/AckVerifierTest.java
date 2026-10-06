package org.captionrelay.bridge;

public final class AckVerifierTest {
    private static final String DEVICE = "phone-test";
    private static final String STREAM = "12345678-1234-5678-9abc-123456789abc";
    // Independent .NET HMAC-SHA256 vectors for caption-relay-ack-v1; public synthetic key bytes 00..1f.
    // 使用 .NET 独立计算 caption-relay-ack-v1 向量；00..1f 是公开合成测试密钥。
    // Keep these fixed values synchronized with tests/protocol_vectors.json, not the implementation under test.
    // 固定值应与 tests/protocol_vectors.json 一致，不从被测实现临时生成。
    private static final String MAC_1 = "eafa9a090c1e7a194aa56ff37f5a5b1591a464e2577487759e4f5e485b1b26a1";
    private static final String MAC_99 = "71c6ba3a0fd6b4619a94fdd65b71480ee9ab590dd15b02a7a6b83804580d5605";
    private static final String MAC_100 = "4ba9f9cf7a1e8de090eb66f3328590c75624ad406616aacf83e8954c44ad4fcd";
    private static final String MAC_101 = "9f737ad4c9629f425020dfa00bc8ed8bc7fcca9d89a510b195634dcb59e84afe";
    private static int checks;
    private static byte[] key() {
        byte[] key = new byte[32]; for (int i = 0; i < key.length; i++) key[i] = (byte) i;
        return key;
    }
    private static void denied(byte[] key, long expectedSeq, int version, String device,
                               String stream, long seq, String signature, String reason) throws Exception {
        try {
            AckVerifier.verify(key, DEVICE, STREAM, expectedSeq, version, device, stream, seq, signature);
            throw new AssertionError("Rejected ACK reached the dequeue boundary");
        } catch (IllegalArgumentException failure) {
            checks++; if (!reason.equals(failure.getMessage())) throw new AssertionError("Expected " + reason, failure);
        }
    }
    public static void main(String[] args) throws Exception {
        PendingBatch.Builder selected = new PendingBatch.Builder(STREAM, "fixture-key", 100);
        for (int i = 1; i <= 100; i++) selected.add(i, "{\"seq\":" + i + "}");
        PendingBatch batch = selected.build();
        AckVerifier.verify(key(), DEVICE, batch.stream, batch.lastSeq(), 1, DEVICE, STREAM, 100, MAC_100); checks++;
        AckVerifier.verify(key(), DEVICE, STREAM, 1, 1, DEVICE, STREAM, 1, MAC_1); checks++;
        // A valid signature for a prefix or a future row is still not an ACK for this selected batch.
        denied(key(), batch.lastSeq(), 1, DEVICE, STREAM, 99, MAC_99, "ack_identity_mismatch");
        denied(key(), batch.lastSeq(), 1, DEVICE, STREAM, 101, MAC_101, "ack_identity_mismatch");
        denied(key(), 100, 2, DEVICE, STREAM, 100, MAC_100, "ack_identity_mismatch");
        denied(key(), 100, 1, "other-phone", STREAM, 100, MAC_100, "ack_identity_mismatch");
        denied(key(), 100, 1, DEVICE, "other-stream", 100, MAC_100, "ack_identity_mismatch");
        denied(key(), 100, 1, DEVICE, STREAM, 100, MAC_99, "ack_mac_invalid");
        denied(key(), 100, 1, DEVICE, STREAM, 100, MAC_100.substring(0, 63) + "0", "ack_mac_invalid");
        byte[] wrongKey = key(); wrongKey[0] ^= 1;
        denied(wrongKey, 100, 1, DEVICE, STREAM, 100, MAC_100, "ack_mac_invalid");
        denied(key(), 100, 1, DEVICE, STREAM, 100, MAC_100.toUpperCase(java.util.Locale.ROOT), "ack_mac_invalid");
        denied(key(), 100, 1, DEVICE, STREAM, 100, MAC_100.substring(1), "ack_mac_invalid");
        denied(key(), 100, 1, DEVICE, STREAM, 100, "g" + MAC_100.substring(1), "ack_mac_invalid");
        denied(key(), 100, 1, DEVICE, STREAM, 100, null, "ack_mac_invalid");
        System.out.println("AckVerifier regression tests passed (" + checks + " checks)");
    }
}
