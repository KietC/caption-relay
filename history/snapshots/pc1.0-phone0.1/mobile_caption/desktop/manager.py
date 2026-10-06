"""Small native control window; the caption viewer remains the reading UI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from entrypoint import PROGRAM, VERSION, package_root


def command(*arguments):
    root = package_root()
    if getattr(sys, "frozen", False):
        return [sys.executable, "mobile_caption_control.py", *arguments]
    return [sys.executable, str(root / "mobile_caption_control.py"), *arguments]


def run_command(arguments):
    result = subprocess.run(command(*arguments), cwd=package_root(), capture_output=True,
                            encoding="utf-8", errors="replace", timeout=180,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip() or "Operation failed")[-1400:])
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


class Manager:
    def __init__(self, root, schedule=True):
        self.root = root
        self.busy = False
        self.closing = False
        self.schedule = schedule
        self.messages = queue.Queue()
        self.actions = []
        root.title(f"{PROGRAM} {VERSION}")
        root.geometry("760x460")
        root.minsize(620, 400)
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="小米双语字幕", font=("Microsoft YaHei UI", 17, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="通过互联网同步到电脑；通话声音保持原样。", font=("Microsoft YaHei UI", 10)).grid(row=1, column=0, sticky="w", pady=(4, 12))
        self.summary = tk.StringVar(value="尚未读取状态 / Ready")
        ttk.Label(outer, textvariable=self.summary, font=("Microsoft YaHei UI", 11)).grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.details = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.details, wraplength=690).grid(row=3, column=0, sticky="w")
        self.endpoint = tk.StringVar(value="")
        ttk.Entry(outer, textvariable=self.endpoint, state="readonly").grid(row=4, column=0, sticky="ew", pady=(10, 4))
        self.manual = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="手机已手动更新接收地址（无 USB）", variable=self.manual).grid(row=5, column=0, sticky="w", pady=(2, 9))
        row = ttk.Frame(outer)
        row.grid(row=6, column=0, sticky="ew")
        self.button(row, "启动 / Start", self.start, 0)
        self.button(row, "停止 / Stop", lambda: self.submit(["stop"], "服务已停止"), 1)
        self.button(row, "刷新状态", lambda: self.submit(["status"], ""), 2)
        self.button(row, "复制接收地址", self.copy_endpoint, 3)
        transfer = ttk.Frame(outer)
        transfer.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        self.button(transfer, "导出迁移包并停止", self.export_migration, 0)
        self.button(transfer, "导入迁移包", self.import_migration, 1)
        self.button(transfer, "显示手机配对码", self.pairing_code, 2)
        self.note = tk.StringVar(value="迁移包和配对码含私密密钥，请勿公开共享。关闭本窗口不会停止字幕服务。")
        self.note_label = ttk.Label(outer, textvariable=self.note, wraplength=690, foreground="#4b5563")
        self.note_label.grid(row=8, column=0, sticky="nw", pady=(14, 0))
        outer.rowconfigure(9, weight=1)
        ttk.Label(outer, text="阅读、复制、滚动历史和置顶设置在字幕窗口中。", foreground="#6b7280").grid(row=10, column=0, sticky="sw")
        if schedule:
            root.after(100, lambda: self.submit(["status"], ""))
            root.after(100, self.drain)
            root.after(8000, self.refresh)

    def button(self, parent, text, callback, column):
        button = ttk.Button(parent, text=text, command=callback)
        button.grid(row=0, column=column, sticky="ew", padx=(0, 7))
        parent.columnconfigure(column, weight=1)
        self.actions.append(button)
        return button

    def start(self):
        arguments = ["start"]
        if self.manual.get():
            arguments.append("--phone-configured")
        self.submit(arguments, "已确认手机数据到达电脑")

    def submit(self, arguments, message, callback=None):
        if self.busy:
            return
        self.busy = True
        for button in self.actions:
            button.configure(state="disabled")
        self.note.set("正在执行，请稍候……" if arguments[0] != "status" else "正在读取状态……")
        def work():
            try:
                result = run_command(arguments)
                self.messages.put((True, result, message, callback))
            except Exception as error:
                self.messages.put((False, str(error), "", None))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        try:
            success, result, message, callback = self.messages.get_nowait()
        except queue.Empty:
            if self.schedule:
                self.root.after(100, self.drain)
            return
        self.busy = False
        for button in self.actions:
            button.configure(state="normal")
        if success:
            if isinstance(result, dict) and "viewer" in result:
                self.show_status(result)
            self.note.set(message or "迁移包和配对码含私密密钥，请勿公开共享。关闭本窗口不会停止字幕服务。")
            if callback:
                try:
                    callback(result)
                except Exception as error:
                    self.note.set(str(error))
        else:
            self.note.set(result)
        if self.closing:
            self.root.destroy()
        elif self.schedule:
            self.root.after(100, self.drain)

    def show_status(self, result):
        viewer = result.get("viewer") or {}
        capture = viewer.get("capture") or {}
        running = viewer.get("running", False)
        try:
            received = datetime.fromisoformat(capture.get("last_event_at", "").replace("Z", "+00:00"))
            fresh = -5 <= (datetime.now(timezone.utc) - received).total_seconds() <= 15
        except (TypeError, ValueError):
            fresh = False
        connected = running and capture.get("state") == "connected" and fresh
        delayed = capture.get("caption_freshness") == "delayed_or_clock_skew"
        self.summary.set("网络已连接，字幕延迟或时钟待确认" if connected and delayed else
                         "字幕连接已建立 / Connected" if connected else
                         "等待手机连接 / Waiting" if running else "字幕服务未运行 / Stopped")
        source = viewer.get("source", "—")
        self.details.set(f"来源：{source}    原文节点：{capture.get('original_count', 0)}    译文节点：{capture.get('translation_count', 0)}\n"
                         f"公网通道进程：{'运行中' if result.get('tunnel_running') else '未运行'}    "
                         f"最近数据：{capture.get('last_event_at', '—')}")
        self.endpoint.set(result.get("endpoint") or "")

    def refresh(self):
        if not self.busy and not self.closing:
            self.submit(["status"], "")
        if not self.closing:
            self.root.after(8000, self.refresh)

    def copy_endpoint(self):
        value = self.endpoint.get()
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.note.set("接收地址已复制；在手机 App 中粘贴并保存。")

    def export_migration(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="导出迁移包（将停止本机服务，含配对密钥）",
                                           defaultextension=".zip", initialfile="CaptionRelay-private-migration.zip",
                                           filetypes=[("Private migration package", "*.zip")])
        if path:
            self.submit(["export-migration", "--file", path], "迁移包已保存，本机服务已停止。请私下传到新电脑。")

    def import_migration(self):
        path = filedialog.askopenfilename(parent=self.root, title="导入迁移包（请先停止本机服务）",
                                         filetypes=[("Private migration package", "*.zip")])
        if path:
            self.submit(["import-migration", "--file", path], "迁移包已导入。新接收地址生成后，请更新手机地址再启动。")

    def pairing_code(self):
        path = package_root() / "mobile_caption" / "runtime" / "pairing-code.txt"
        self.submit(["pairing-code", "--file", str(path)], "配对码仅供自己的手机使用。",
                    lambda unused: self.show_code(path))

    def show_code(self, path):
        code = Path(path).read_text(encoding="utf-8-sig").strip()
        if not code.startswith("CRCP1:") and not code.startswith("CRCP1."):
            raise RuntimeError("配对码格式不正确，请查看控制器输出。")
        popup = tk.Toplevel(self.root)
        popup.title("私密手机配对码 / Private pairing code")
        popup.geometry("700x240")
        ttk.Label(popup, text="在自己的小米 App 中导入。此码包含密钥，请勿公开。", padding=10).pack(anchor="w")
        text = tk.Text(popup, wrap="char", height=6)
        text.pack(fill="both", expand=True, padx=10)
        text.insert("1.0", code)
        text.configure(state="disabled")
        def copy():
            popup.clipboard_clear()
            popup.clipboard_append(code)
        ttk.Button(popup, text="复制配对码", command=copy).pack(pady=10)

    def close(self):
        if self.busy:
            self.closing = True
            self.root.withdraw()
        else:
            self.root.destroy()


def gui_self_check():
    root = tk.Tk()
    root.withdraw()
    try:
        app = Manager(root, schedule=False)
        root.update_idletasks()
        names = [button.cget("text") for button in app.actions]
        assert len(names) == 7
        app.show_status({"viewer": {"running": False}, "tunnel_running": False})
        assert "Stopped" in app.summary.get()
        return {"ok": True, "controls": names, "no_subprocess_started": True, "no_clipboard_change": True}
    finally:
        root.destroy()


def run():
    from caption_window import enable_dpi_awareness
    enable_dpi_awareness()
    root = tk.Tk()
    Manager(root)
    root.mainloop()
    return 0
