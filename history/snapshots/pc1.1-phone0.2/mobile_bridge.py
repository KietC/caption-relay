"""Receive encrypted phone captions; never reads or changes audio devices."""
import argparse
from datetime import datetime
import msvcrt
import os
from pathlib import Path
import threading
import time

from bridge import write_json
from mobile_caption.collector import Collector
from mobile_caption.protocol import Config
from mobile_caption.receiver import CaptionServer
from mobile_caption.store import Inbox, now

from runtime_paths import BASE


def owner_gone(pid, created):
    if not pid:
        return False
    import psutil
    try:
        owner = psutil.Process(pid)
        return created is not None and abs(owner.create_time() - created) > 0.01
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=BASE / "mobile_caption" / "runtime" / "config.json")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history-md", "--markdown", dest="history_md", type=Path)
    parser.add_argument("--inbox", type=Path)
    parser.add_argument("--duration", type=int, default=3600, help="1..86400 seconds")
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--owner-created", type=float)
    parser.add_argument("--lean", action="store_true", help="Compatibility flag; caption-only is always enabled")
    args = parser.parse_args(argv)
    if not 1 <= args.duration <= 86400 or not 1 <= args.port <= 65535:
        parser.error("duration must be 1..86400 seconds and port 1..65535")
    config = Config.load(args.config)
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output / ".capture.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        raise SystemExit("Another capture is already running for this output directory.")
    stop = args.output / "stop.request"
    stop.unlink(missing_ok=True)
    status = {"state": "starting", "source": "mobile", "transport": "mobile_https",
              "run_id": datetime.now().strftime("%Y%m%d_%H%M%S_%f"), "started_at": now(),
              "host_pid": os.getpid(), "device_id": config.device_id,
              "listen": f"127.0.0.1:{args.port}", "audio_control": False,
              "automatic_send": False, "text_node_count": 0}
    server = None
    inbox = None
    thread = None
    try:
        inbox = Inbox(args.inbox or config.inbox_path or args.config.parent / "inbox.sqlite3")
        collector = Collector(inbox, args.output, args.history_md or args.output / "captions.md",
                              status["run_id"], status)
        server = CaptionServer(("127.0.0.1", args.port), config, inbox,
                               update_apk=BASE / "Android" / "CaptionRelayCaptionBridge-0.2.0.apk")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            if stop.exists() or owner_gone(args.owner_pid, args.owner_created):
                status["state"] = "stopped_by_user"
                break
            collector.apply_pending()
            time.sleep(0.02)
        else:
            status["state"] = "stopped"
    except KeyboardInterrupt:
        status["state"] = "stopped_by_user"
    except Exception as error:
        status.update(state="error", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        if inbox is not None:
            inbox.close()
        status["ended_at"] = now()
        write_json(args.output / "status.json", status)
        lock.close()


if __name__ == "__main__":
    main()
