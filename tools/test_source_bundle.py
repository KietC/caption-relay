"""Safety invariants for the source-only export, using isolated synthetic files.

使用隔离合成文件验证仅源码导出的安全不变量。
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

try:
    from . import generate_allowlist, source_bundle
except ImportError:
    import generate_allowlist
    import source_bundle


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="caption-source-test-")
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        self.allowlist = self.base / "allowlist.json"
        self.output = self.base / "source.zip"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, data=b"plain source\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def rows(self, names):
        return [{"path": name, "sha256": hashlib.sha256((self.root / name).read_bytes()).hexdigest()}
                for name in names]

    def save_allowlist(self, rows):
        self.allowlist.write_text(json.dumps({"schema": 1, "files": rows}), encoding="utf-8")

    def build(self):
        return source_bundle.build(self.root, self.allowlist, self.output, 2)

    def test_java_xml_spec_manifest_and_deterministic_archive(self):
        content = {"app/Sender.java": b"final class Sender {}\n",
                   "app/AndroidManifest.xml": b"<manifest/>\n",
                   "app/desktop.spec": b"a = Analysis([])\n"}
        for name, data in content.items():
            self.write(name, data)
        self.save_allowlist(self.rows(content))
        receipt = self.build()
        self.assertEqual(receipt["status"], "PREPARED_NOT_UPLOADED")
        self.assertEqual(receipt["counts"]["uploads"], 0)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), set(content) | {"BUNDLE_MANIFEST.json"})
            manifest = json.loads(archive.read("BUNDLE_MANIFEST.json"))
            self.assertEqual(manifest["schema"], 1)
            for entry in manifest["files"]:
                data = archive.read(entry["path"])
                self.assertEqual(data, content[entry["path"]])
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(entry["bytes"], len(data))
                self.assertEqual(archive.getinfo(entry["path"]).date_time, (1980, 1, 1, 0, 0, 0))
        second = self.base / "second.zip"
        source_bundle.build(self.root, self.allowlist, second, 1)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())

    def test_changed_source_is_rejected_and_partial_removed(self):
        path = self.write("main.py", b"first\n")
        self.save_allowlist(self.rows(["main.py"]))
        path.write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "SOURCE_BINDING_MISMATCH"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.base.glob("*.partial")), [])

    def test_binary_and_secret_candidates_are_rejected(self):
        candidates = [b"source\x00binary", b"gh" + b"p_" + b"A" * 40]
        for data in candidates:
            with self.subTest(data=data[:6]):
                self.write("main.py", data)
                self.save_allowlist(self.rows(["main.py"]))
                with self.assertRaisesRegex(ValueError, "BINARY_OR_SECRET_CANDIDATE"):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_unsafe_paths_and_private_names_are_rejected(self):
        names = ["/absolute.py", "../escape.py", "a/../../escape.py", "C:/escape.py",
                 "a\\file.py", "./main.py", "signing/readme.md", "runtime/private.py",
                 "password.txt", "config.json", ".env.sample", "a/probe.json", "a/auth.json"]
        for name in names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                source_bundle.safe_relative(name)

    def test_output_inside_source_and_existing_output_are_rejected(self):
        self.write("main.py")
        self.save_allowlist(self.rows(["main.py"]))
        with self.assertRaisesRegex(ValueError, "OUTPUT_INSIDE_SOURCE_REFUSED"):
            source_bundle.build(self.root, self.allowlist, self.root / "export.zip", 1)
        self.output.write_bytes(b"preserve existing")
        with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"preserve existing")
        self.output.unlink()
        receipt = self.output.with_suffix(self.output.suffix + ".receipt.json")
        receipt.write_text("preserve receipt", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"):
            self.build()
        self.assertEqual(receipt.read_text(encoding="utf-8"), "preserve receipt")

    def test_symlink_is_rejected_even_when_target_is_source(self):
        self.write("actual.py")
        linked = self.root / "linked.py"
        try:
            os.symlink(self.root / "actual.py", linked)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("host cannot create a symlink: " + type(exc).__name__)
        self.save_allowlist(self.rows(["linked.py"]))
        with self.assertRaisesRegex(ValueError, "REPARSE_POINT_REFUSED"):
            self.build()
        self.assertFalse(self.output.exists())
        fileset = self.base / "fileset.json"
        fileset.write_text(json.dumps({"schema": 1, "files": ["linked.py"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "REPARSE_POINT_REFUSED"):
            generate_allowlist.generate(self.root, fileset, self.base / "generated.json")

    def test_generate_explicit_allowlist_without_recursive_discovery(self):
        self.write("main.py", b"print('synthetic')\n")
        self.write("runtime_private/ignored.py", b"\x00must not read")
        fileset = self.base / "fileset.json"
        fileset.write_text(json.dumps({"schema": 1, "files": ["main.py"]}), encoding="utf-8")
        generated = self.base / "generated.json"
        report = generate_allowlist.generate(self.root, fileset, generated)
        self.assertEqual(report["files"], 1)
        self.assertEqual(report["uploads"], 0)
        packet = json.loads(generated.read_text(encoding="utf-8"))
        self.assertEqual(packet, {"schema": 1, "files": self.rows(["main.py"])})
        with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"):
            generate_allowlist.generate(self.root, fileset, generated)
        with self.assertRaisesRegex(ValueError, "OUTPUT_INSIDE_SOURCE_REFUSED"):
            generate_allowlist.generate(self.root, fileset, self.root / "generated.json")
        fileset.write_text(json.dumps({"schema": 1, "files": ["main.py", "MAIN.py"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ARCHIVE_PATH"):
            generate_allowlist.generate(self.root, fileset, self.base / "duplicates.json")

    def test_generator_rejects_binary_private_file_and_invalid_schema(self):
        fileset = self.base / "fileset.json"
        output = self.base / "generated.json"
        self.write("main.py", b"binary\x00")
        fileset.write_text(json.dumps({"schema": 1, "files": ["main.py"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "BINARY_OR_SECRET_CANDIDATE"):
            generate_allowlist.generate(self.root, fileset, output)
        fileset.write_text(json.dumps({"schema": 1, "files": ["password.txt"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PRIVATE_FILE_REFUSED"):
            generate_allowlist.generate(self.root, fileset, output)
        fileset.write_text(json.dumps({"schema": 1, "files": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "FILESET_COUNT_INVALID"):
            generate_allowlist.generate(self.root, fileset, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
