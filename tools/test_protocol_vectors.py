#!/usr/bin/env python3
"""Check immutable synthetic ACK vectors against Python output and JVM test fixtures.

用固定合成 ACK 向量核验 Python 输出与 JVM 测试基准，避免协议重命名后失配。
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
VECTOR_FILE = ROOT / "tests/protocol_vectors.json"


def load_protocol(relative: str, name: str):
    """Load source-only protocol modules; never load a pairing or runtime configuration.

    只载入协议源码模块，不读取配对或运行配置。
    """
    location = ROOT / relative / "mobile_caption/protocol.py"
    spec = importlib.util.spec_from_file_location(name, location)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass needs its defining module to be registered.
    # dataclass 需要先注册定义它的模块。
    spec.loader.exec_module(module)
    return module


class ProtocolVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(ROOT / "app"))
        cls.fixture = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
        cls.key = bytes.fromhex(cls.fixture["key_hex"])
        cls.current = load_protocol("app", "_caption_relay_vector_current")
        cls.vectors = {(v["lane"], v["seq"]): v for v in cls.fixture["vectors"]}

    def assert_python_vectors(self, module, lanes):
        # This is a public test key, not an identity recovered from an installed phone.
        # 这是公开测试密钥，不是从已安装手机取得的身份信息。
        config = module.Config(self.fixture["device_id"], "synthetic-not-a-pairing-token", self.key)
        for (lane, seq), vector in self.vectors.items():
            if lane not in lanes:
                continue
            with self.subTest(lane=lane, seq=seq):
                make_ack = module.ack if lane == "durable" else module.live_ack
                output = make_ack(config, self.fixture["stream_id"], seq)
                expected = {"v": 1, "device_id": self.fixture["device_id"],
                            "stream_id": self.fixture["stream_id"], "seq": seq,
                            "mac": vector["mac_hex"]}
                if lane == "live":
                    expected["live_only"] = True
                self.assertEqual(output, expected)

    def test_fixed_vectors_match_independent_standard_library_hmac(self):
        self.assertEqual(self.fixture["schema"], 1)
        self.assertEqual(self.key, bytes(range(32)))
        self.assertEqual(len(self.vectors), 8)
        for vector in self.fixture["vectors"]:
            with self.subTest(lane=vector["lane"], seq=vector["seq"]):
                message = "|".join((vector["domain"], self.fixture["device_id"],
                                    self.fixture["stream_id"], str(vector["seq"])))
                self.assertEqual(hmac.new(self.key, message.encode("ascii"), hashlib.sha256).hexdigest(),
                                 vector["mac_hex"])

    def test_current_python_acks_match_both_lane_vectors(self):
        self.assert_python_vectors(self.current, {"durable", "live"})

    def test_single_packet_history_python_acks_match_durable_vectors(self):
        module = load_protocol("history/snapshots/pc1.0-phone0.1", "_caption_relay_vector_single")
        self.assert_python_vectors(module, {"durable"})

    def test_batch_history_python_acks_match_durable_vectors(self):
        module = load_protocol("history/snapshots/pc1.1-phone0.2", "_caption_relay_vector_batch")
        self.assert_python_vectors(module, {"durable"})

    def test_java_durable_golden_fixtures_match_public_vectors(self):
        # JVM execution separately verifies these same constants against Java's verifier.
        # 另行运行 JVM 测试，用相同常量核验 Java 验证函数。
        for relative in ("app", "history/snapshots/pc1.1-phone0.2"):
            path = ROOT / relative / "mobile_caption/android/tests/org/captionrelay/bridge/AckVerifierTest.java"
            source = path.read_text(encoding="utf-8")
            values = dict(re.findall(r'private static final String MAC_(\d+) = "([0-9a-f]{64})";', source))
            self.assertEqual(values, {str(seq): v["mac_hex"] for (lane, seq), v in self.vectors.items()
                                      if lane == "durable"})

    def test_java_live_fixture_matches_domain_separated_vectors(self):
        source = (ROOT / "app/mobile_caption/android/tests/org/captionrelay/bridge/LiveAckTest.java").read_text(encoding="utf-8")
        values = dict(re.findall(r'private static final String (LIVE_MAC|DURABLE_MAC) = "([0-9a-f]{64})";', source))
        self.assertEqual(values, {"LIVE_MAC": self.vectors[("live", 100)]["mac_hex"],
                                  "DURABLE_MAC": self.vectors[("durable", 100)]["mac_hex"]})

    def test_live_ack_and_durable_ack_cannot_share_a_mac(self):
        for seq in (1, 99, 100, 101):
            self.assertNotEqual(self.vectors[("live", seq)]["mac_hex"],
                                self.vectors[("durable", seq)]["mac_hex"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
