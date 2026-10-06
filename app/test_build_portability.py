"""Exercise build preflight and public APK mappings using synthetic files only.

仅使用合成文件检查构建预检和公开 APK 映射。
"""
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from build_portable import ADB_FILES, default_adb_directory, validate_build_inputs
from mobile_bridge import public_apk_downloads
from mobile_caption.protocol import Config
from mobile_caption.receiver import CaptionServer


class BuildPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "app"
        self.desktop = self.repo / "mobile_caption/desktop"
        self.adb = self.root / "sdk/platform-tools"
        self.apk = self.root / "current.apk"
        relative = ["build/caption-bridge.jar", "mobile_caption/tools/cloudflared.exe",
                    "mobile_caption/desktop/CaptionRelayCaptions.spec", "mobile_caption/desktop/version_info.txt",
                    "mobile_caption/desktop/PORTABLE_README.txt", "atomic_io.py", "bridge.py", "captions.py",
                    "caption_history.py", "caption_window.py", "caption_viewer_control.py", "mobile_bridge.py",
                    "mobile_caption_control.py", "runtime_paths.py"]
        for file in [self.apk, *(self.repo / item for item in relative), *(self.adb / name for name in ADB_FILES)]:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"synthetic build input; never executed")

    def validate(self):
        return validate_build_inputs(self.apk, self.adb, repo=self.repo, desktop=self.desktop)

    def test_complete_preflight_accepts_all_required_inputs(self):
        self.assertEqual(len(self.validate()), 20)

    def test_each_platform_tools_file_is_required_before_packaging(self):
        for name in ADB_FILES:
            with self.subTest(name=name):
                file = self.adb / name
                content = file.read_bytes()
                file.unlink()
                try:
                    with self.assertRaisesRegex(ValueError, name.replace(".", r"\.")):
                        self.validate()
                finally:
                    file.write_bytes(content)

    def test_missing_tunnel_apk_and_usb_helper_are_reported_together(self):
        self.apk.unlink()
        (self.repo / "mobile_caption/tools/cloudflared.exe").unlink()
        (self.repo / "build/caption-bridge.jar").unlink()
        with self.assertRaises(ValueError) as caught:
            self.validate()
        for name in ("current.apk", "cloudflared.exe", "caption-bridge.jar"):
            self.assertIn(name, str(caught.exception))

    def test_no_adb_has_installation_guidance(self):
        with self.assertRaisesRegex(ValueError, "platform-tools"):
            validate_build_inputs(self.apk, None, repo=self.repo, desktop=self.desktop)

    def test_sdk_environment_and_path_discovery(self):
        with patch.dict(os.environ, {"ANDROID_SDK_ROOT": str(self.adb.parent)}, clear=True), patch("build_portable.shutil.which", return_value=None):
            self.assertEqual(default_adb_directory(), self.adb)
        with patch.dict(os.environ, {}, clear=True), patch("build_portable.shutil.which", return_value=str(self.adb / "adb.exe")), patch("build_portable.REPO", self.repo):
            self.assertEqual(default_adb_directory(), self.adb)
        with patch.dict(os.environ, {}, clear=True), patch("build_portable.shutil.which", return_value=None), patch("build_portable.REPO", self.repo):
            self.assertIsNone(default_adb_directory())


class PublicApkMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-apk-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_apk(self, relative, content=b"synthetic APK fixture; never installed"):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def test_source_and_frozen_generic_artifacts_have_current_version_alias(self):
        for relative in ("Android/CaptionRelayCaptionBridge.apk",
                         "mobile_caption/android/build/CaptionRelayCaptionBridge.apk"):
            with self.subTest(relative=relative):
                apk = self.write_apk(relative)
                self.assertEqual(public_apk_downloads(self.root), {"CaptionRelayCaptionBridge-0.3.0.apk": apk})
                apk.unlink()

    def test_explicit_current_version_wins_and_legacy_is_not_invented(self):
        self.write_apk("Android/CaptionRelayCaptionBridge.apk")
        current = self.write_apk("Android/CaptionRelayCaptionBridge-0.3.0.apk")
        self.assertEqual(public_apk_downloads(self.root), {current.name: current})

    def test_current_route_downloads_generic_artifact_and_other_routes_remain_closed(self):
        apk = self.write_apk("Android/CaptionRelayCaptionBridge.apk")
        config = Config("synthetic-device", "synthetic-token-" + "x" * 32, b"k" * 32)
        server = CaptionServer(("127.0.0.1", 0), config, None, download_apks=public_apk_downloads(self.root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for route, expected in (("/downloads/CaptionRelayCaptionBridge-0.3.0.apk", 200),
                                    ("/downloads/CaptionRelayCaptionBridge-0.2.0.apk", 404),
                                    ("/downloads/CaptionRelayCaptionBridge-0.3.0.apk?x=1", 404),
                                    ("/downloads/../config.json", 404)):
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                try:
                    connection.request("GET", route)
                    response = connection.getresponse()
                    data = response.read()
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertEqual(data, apk.read_bytes())
                        self.assertIn("0.3.0.apk", response.getheader("Content-Disposition"))
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class ToolchainResolutionTests(unittest.TestCase):
    def setUp(self):
        self.shell = shutil.which("pwsh") or shutil.which("powershell")
        if not self.shell:
            self.skipTest("PowerShell unavailable; Windows build resolver tests require it")
        self.temp = tempfile.TemporaryDirectory(prefix="toolchain-resolution-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = Path(__file__).resolve().parent / "build_tools.ps1"
        self.jdk = self.root / "explicit-jdk"
        self.other_jdk = self.root / "environment-jdk"
        self.sdk = self.root / "android-sdk"
        self.sdk.mkdir()
        for jdk in (self.jdk, self.other_jdk):
            (jdk / "bin").mkdir(parents=True)
            for name in ("java.exe", "javac.exe"):
                (jdk / "bin" / name).write_bytes(b"synthetic marker; never executed")

    def invoke(self, function, *, requested="", java_home="", sdk_root="", sdk_home="", compiler_path=None):
        # The child process evaluates path resolution only; marker executables are never launched.
        # 子进程只执行路径解析，不启动标记用的可执行文件。
        environment = dict(os.environ)
        environment.update(CAPTION_RESOLVER_HELPER=str(self.helper), CAPTION_RESOLVER_INPUT=requested,
                           JAVA_HOME=java_home, ANDROID_SDK_ROOT=sdk_root, ANDROID_HOME=sdk_home)
        if compiler_path is not None:
            environment["PATH"] = str(compiler_path)
        script = "$ErrorActionPreference='Stop'; . $env:CAPTION_RESOLVER_HELPER; " + \
                 "try { $value=" + function + " -Requested $env:CAPTION_RESOLVER_INPUT; " + \
                 "@{ok=$true;value=$value}|ConvertTo-Json -Compress } catch { " + \
                 "@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress }"
        result = subprocess.run([self.shell, "-NoProfile", "-NonInteractive", "-Command", script],
                                env=environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def test_jdk_parameter_then_environment_then_compiler_path(self):
        result = self.invoke("Resolve-CaptionJavaHome", requested=str(self.jdk), java_home=str(self.other_jdk))
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["value"]), self.jdk)
        result = self.invoke("Resolve-CaptionJavaHome", java_home=str(self.other_jdk))
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["value"]), self.other_jdk)
        result = self.invoke("Resolve-CaptionJavaHome", compiler_path=self.jdk / "bin")
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["value"]), self.jdk)

    def test_invalid_explicit_jdk_does_not_silently_use_environment(self):
        result = self.invoke("Resolve-CaptionJavaHome", requested=str(self.root / "missing"), java_home=str(self.jdk))
        self.assertFalse(result["ok"])
        self.assertIn("JDK directory not found", result["error"])

    def test_sdk_environment_priority_and_missing_sdk_guidance(self):
        other_sdk = self.root / "other-sdk"
        other_sdk.mkdir()
        result = self.invoke("Resolve-CaptionAndroidSdk", sdk_root=str(self.sdk), sdk_home=str(other_sdk))
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["value"]), self.sdk)
        result = self.invoke("Resolve-CaptionAndroidSdk")
        self.assertFalse(result["ok"])
        self.assertIn("sdkmanager", result["error"])
        self.assertIn("ANDROID_SDK_ROOT", result["error"])


if __name__ == "__main__":
    unittest.main()
