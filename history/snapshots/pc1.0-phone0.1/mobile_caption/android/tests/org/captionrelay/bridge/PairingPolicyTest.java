package org.captionrelay.bridge;

public final class PairingPolicyTest {
    private static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private static void denied(boolean replace, int queued, long gaps, boolean confirmed, String expected) {
        try { PairingPolicy.checkReplacement(replace, queued, gaps, confirmed); throw new AssertionError("replacement should fail"); }
        catch (IllegalStateException failure) { check(expected.equals(failure.getMessage()), "wrong denial reason"); }
    }
    public static void main(String[] args) {
        check("fresh".equals(PairingPolicy.kind("", "", "phone-a", "key-a")), "fresh pairing");
        check("same".equals(PairingPolicy.kind("phone-a", "key-a", "phone-a", "key-a")), "same identity/key keeps stream");
        check("replace".equals(PairingPolicy.kind("phone-a", "key-a", "phone-b", "key-a")), "device replacement");
        check("replace".equals(PairingPolicy.kind("phone-a", "key-a", "phone-a", "key-b")), "key replacement");
        check("replace".equals(PairingPolicy.kind("phone-a", "", "phone-a", "key-a")), "unknown old key cannot silently match");
        denied(true, 1, 0, false, "pairing_blocked_pending_captions");
        denied(true, 1, 0, true, "pairing_blocked_pending_captions");
        denied(true, 0, 1, true, "pairing_blocked_pending_captions");
        denied(true, 0, 0, false, "pairing_replacement_confirmation_required");
        PairingPolicy.checkReplacement(true, 0, 0, true);
        PairingPolicy.checkReplacement(false, 2000, 50, false);
        System.out.println("PairingPolicy regression tests passed (11 checks)");
    }
}
