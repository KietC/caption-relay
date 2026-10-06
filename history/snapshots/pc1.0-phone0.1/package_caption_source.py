"""Make an allowlisted source archive without live captions, credentials or signing keys."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

BASE = Path(__file__).resolve().parent
ROOT_FILES = ["atomic_io.py", "runtime_paths.py", "bridge.py", "captions.py", "caption_history.py",
              "caption_window.py", "caption_viewer_control.py", "mobile_bridge.py", "mobile_caption_control.py",
              "package_caption_source.py", "verify_mobile_recovery.py", "verify_portable_caption.py", "SOURCE_README.md", "caption_window_README.md",
              "test_atomic_io.py", "test_captions.py", "test_caption_history.py", "test_caption_window.py",
              "test_caption_viewer_control.py", "test_mobile_caption.py", "test_mobile_caption_control.py",
              "test_mobile_migration.py", "build.ps1", "device/CaptionBridge.java", "build/caption-bridge.jar"]


def source_files():
    files = {BASE / name for name in ROOT_FILES}
    module = BASE / "mobile_caption"
    files.update(path for path in module.iterdir() if path.is_file() and path.suffix in {".py", ".md", ".txt"})
    android = module / "android"
    for sub in ("src", "res", "tests"):
        files.update(path for path in (android / sub).rglob("*") if path.is_file())
    files.update(android / name for name in ("AndroidManifest.xml", "build.ps1", "test.ps1", "README.md"))
    files.add(android / "build" / "CaptionRelayCaptionBridge.apk")
    desktop = module / "desktop"
    files.update(path for path in desktop.iterdir() if path.is_file() and
                 path.suffix in {".py", ".ps1", ".txt", ".md", ".spec"})
    files.update(path for path in (desktop / "THIRD_PARTY_NOTICES").rglob("*") if path.is_file())
    files.add(module / "tools" / "cloudflared-release.json")
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or linked source input: " + str(path))
        name = path.relative_to(BASE).as_posix().lower()
        if any(part in name.split("/") for part in ("runtime", "output", "signing", ".buildenv", "__pycache__")):
            raise RuntimeError("Private/build path in source allowlist: " + name)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    args.file.parent.mkdir(parents=True, exist_ok=True)
    files = source_files()
    manifest = {path.relative_to(BASE).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    with zipfile.ZipFile(args.file, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "CaptionRelayCaptions-source/" + path.relative_to(BASE).as_posix())
        archive.writestr("CaptionRelayCaptions-source/SOURCE.sha256.json", json.dumps(manifest, indent=2))
    print(json.dumps({"archive": str(args.file.resolve()), "files": len(files),
                      "sha256": hashlib.sha256(args.file.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
