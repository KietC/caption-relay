"""Native, copyable view of Xiaomi's existing caption bridge; no audio or ASR."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font as tkfont, ttk

import psutil


from runtime_paths import BASE
POLL_MS = 50
HEARTBEAT_TIMEOUT = 20
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def enable_dpi_awareness():
    """Stop Windows bitmap-scaling the requested 1080 pixels into 1620 pixels."""
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            ctypes.windll.user32.SetProcessDPIAware()


def work_area(root):
    if os.name == "nt":
        from ctypes import wintypes
        area = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
            return area.left, area.top, area.right, area.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def owner_alive(pid, created):
    if pid is None:
        return True
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - created) < 0.1
    except (psutil.Error, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class ViewState:
    state: str
    message: str
    original: str = ""
    translation: str = ""
    captured_at: str = ""
    run_id: str = ""
    received_at: str = ""
    updated_at: str = ""
    transport: str = "usb"


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_beijing_time(value):
    """Format a display timestamp without depending on or changing the OS timezone."""
    stamp = value if isinstance(value, datetime) else parse_time(value)
    if stamp is None:
        return ""
    # Legacy naive timestamps have the same UTC interpretation as parse_time().
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def read_json(path):
    try:
        value = path.read_text(encoding="utf-8-sig")
        result = json.loads(value)
        return result if isinstance(result, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def caption_text(caption):
    """Keep source and translation roles; never infer languages or pair sentences."""
    result = {"original": [], "translation": []}
    items = caption.get("items", [])
    if not isinstance(items, list):
        return "", ""
    for item in items:
        if not isinstance(item, dict):
            continue
        role, value = item.get("role"), item.get("text")
        if isinstance(role, str) and role in result and isinstance(value, str) and value.strip():
            result[role].append(value.strip())
    return "\n\n".join(result["original"]), "\n\n".join(result["translation"])


def read_view(output, now=None):
    """Current-run/heartbeat checks prevent old files being called live captions."""
    now = now or datetime.now(timezone.utc)
    status = read_json(output / "status.json")
    if status is None:
        return ViewState("waiting", "等待手机字幕连接 / Waiting for the phone caption reader.")
    mobile = status.get("transport") == "mobile_https" or status.get("source") == "mobile"
    label = "网络" if mobile else "USB"
    english = "Network" if mobile else "USB"
    run_id = status.get("run_id", "")
    started = parse_time(status.get("started_at"))
    caption = read_json(output / "caption_latest.json")
    valid = (isinstance(run_id, str) and bool(run_id) and caption is not None
             and caption.get("run_id") == run_id
             and caption.get("type") == "caption_snapshot")
    captured = parse_time(caption.get("received_at")) if valid else None
    valid = bool(valid and captured and started and captured >= started)
    original, translation = caption_text(caption) if valid else ("", "")
    captured_label = format_beijing_time(captured) if valid else ""
    data = dict(original=original, translation=translation,
                captured_at=captured_label, run_id=run_id if isinstance(run_id, str) else "",
                received_at=caption["received_at"] if valid else "",
                transport="mobile_https" if mobile else "usb")
    state = status.get("state")
    if state in ("error", "stopped", "stopped_by_user"):
        message = "采集已停止，以下仅为旧字幕 / Caption reader stopped — last captured text only."
        if state == "error":
            error = " ".join(str(status.get("error", "USB connection unavailable")).split())
            message = "字幕采集异常 / Caption reader error — " + error[:160]
        return ViewState("offline", message, **data)
    if state != "connected":
        return ViewState("waiting", "正在连接手机，等待本次字幕 / Connecting — waiting for this session.", run_id=data["run_id"], transport=data["transport"])
    heartbeat = parse_time(status.get("last_event_at"))
    if heartbeat is None or not -5 <= (now - heartbeat).total_seconds() <= HEARTBEAT_TIMEOUT:
        return ViewState("offline", f"{label}连接无响应，以下仅为旧字幕 / {english} not responding — last captured text only.", **data)
    if not valid:
        return ViewState("waiting", f"{label}已连接，等待新字幕 / {english} connected — waiting for new captions.", run_id=data["run_id"], transport=data["transport"])
    if mobile and caption.get("freshness") == "delayed_or_clock_skew":
        return ViewState("delayed", "正在显示较早字幕，或手机与电脑时钟不一致 / Delayed captions or phone/PC clock mismatch.", **data)
    if not (original or translation):
        return ViewState("empty", f"{label}已连接，手机尚无可见字幕 / {english} connected — no captions visible on the phone.", **data)
    return ViewState("live", f"{label}已连接，正在显示手机字幕 / {english} connected — showing the phone's caption screen.", **data)


def copy_both_text(original, translation):
    sections = []
    if original.strip():
        sections.append("英文原文 / Original\n" + original.strip())
    if translation.strip():
        sections.append("中文翻译 / Translation\n" + translation.strip())
    return "\n\n".join(sections)


def read_history_view(output, current):
    """History belongs to this explicit viewer session and survives capture rollover."""
    history = read_json(output / "history.json")
    if history is None or not all(isinstance(history.get(key), str) for key in ("original", "translation")):
        return None
    stamp = parse_time(history.get("updated_at"))
    return replace(current, original=history["original"], translation=history["translation"],
                   captured_at=format_beijing_time(stamp) if stamp else current.captured_at,
                   updated_at=history["updated_at"] if stamp else current.received_at)


def changed_span(before, after):
    """Return the smallest one-span replacement, retaining unchanged prefix/suffix."""
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    end_before, end_after = len(before), len(after)
    while end_before > start and end_after > start and before[end_before - 1] == after[end_after - 1]:
        end_before -= 1
        end_after -= 1
    return start, end_before, after[start:end_after]


class CaptionWindow:
    def __init__(self, root, output, topmost=True, schedule=True, stop_file=None, owner_pid=None, owner_created=None):
        self.root, self.output = root, Path(output)
        root.tk.call("tk", "scaling", 96 / 72)
        self.stop_file = Path(stop_file) if stop_file is not None else None
        self.owner_pid, self.owner_created = owner_pid, owner_created
        self.closed = False
        self.history_seen = False
        self.history_signature = None
        self.history_cached = None
        self.last_window_status = None
        self.display_revision = 0
        self.displayed = ViewState("waiting", "等待手机字幕连接 / Waiting for the phone caption reader.")
        self.latest = self.displayed
        self.after_id = None
        self.topmost = tk.BooleanVar(root, value=topmost)
        self.follow = tk.BooleanVar(root, value=True)
        self.notice = tk.StringVar(root, value="")
        self.status = tk.StringVar(root, value=self.displayed.message)
        self.text_font = tkfont.Font(root=root, family="Segoe UI", size=14)
        root.title("CaptionRelay · 手机双语字幕 / Phone captions")
        root.configure(background="#eef3f7")
        left, top, right, bottom = work_area(root)
        width, height = min(1080, right - left - 48), min(620, bottom - top - 96)
        root.geometry(f"{width}x{height}+{left + 24}+{top + 24}")
        root.resizable(True, True)
        root.minsize(min(480, width), min(240, height))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.apply_topmost()
        self.refresh()
        if schedule:
            self.after_id = root.after(POLL_MS, self.tick)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef3f7")
        style.configure("TLabel", background="#eef3f7", foreground="#344256", font=("Segoe UI", 9))
        style.configure("TCheckbutton", background="#eef3f7", font=("Segoe UI", 9))
        style.configure("TButton", padding=(7, 4), font=("Segoe UI", 9))
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"), foreground="#102d48")
        style.configure("Pane.TLabel", font=("Segoe UI", 9, "bold"), foreground="#143d61")
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.toolbar = ttk.Frame(main)
        self.toolbar.grid(row=0, column=0, sticky="ew")
        self.toolbar.columnconfigure(1, weight=1)
        self.toolbar_heading = ttk.Label(self.toolbar, text="双语字幕 / Captions", style="Heading.TLabel")
        self.toolbar_actions = ttk.Frame(self.toolbar)
        self.copy_original_button = ttk.Button(self.toolbar_actions, text="复制英文 / EN", command=lambda: self.copy("original"))
        self.copy_original_button.pack(side="left", padx=(0, 5))
        self.copy_translation_button = ttk.Button(self.toolbar_actions, text="复制中文 / CN", command=lambda: self.copy("translation"))
        self.copy_translation_button.pack(side="left", padx=(0, 5))
        self.copy_both_button = ttk.Button(self.toolbar_actions, text="复制中英 / Both", command=lambda: self.copy("both"))
        self.copy_both_button.pack(side="left", padx=(0, 5))
        self.resume_button = ttk.Button(self.toolbar_actions, text="跟随最新 / Latest", command=self.resume)
        self.resume_button.pack(side="left")
        self.topmost_check = ttk.Checkbutton(self.toolbar, text="置顶 / On top", variable=self.topmost, command=self.apply_topmost)
        self.toolbar_compact = None
        panes = ttk.Frame(main)
        panes.grid(row=1, column=0, sticky="nsew", pady=(8, 5))
        panes.columnconfigure((0, 1), weight=1, uniform="captions")
        panes.rowconfigure(0, weight=1)
        self.original = self._pane(panes, 0, "英文原文 / Original")
        self.translation = self._pane(panes, 1, "中文翻译 / Translation")
        self.status_label = ttk.Label(main, textvariable=self.status, wraplength=1040)
        self.status_label.grid(row=2, column=0, sticky="ew")
        main.bind("<Configure>", self.resize_layout)
        self.resize_layout()

    def resize_layout(self, event=None):
        width = event.width if event is not None else self.root.winfo_width()
        self.status_label.configure(wraplength=max(200, width - 16))
        compact = width < 860
        if compact == self.toolbar_compact:
            return
        self.toolbar_compact = compact
        self.toolbar_heading.grid(row=0, column=0, sticky="w", padx=(0, 12))
        if compact:
            self.topmost_check.grid(row=0, column=1, sticky="e")
            self.toolbar_actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        else:
            self.toolbar_actions.grid(row=0, column=1, columnspan=1, sticky="w", pady=0)
            self.topmost_check.grid(row=0, column=2, sticky="e")

    def _pane(self, parent, column, heading):
        panel = ttk.Frame(parent, padding=(0, 0, 4 if column == 0 else 0, 0))
        panel.grid(row=0, column=column, sticky="nsew", padx=(4 if column else 0, 0))
        title = ttk.Frame(panel)
        title.pack(fill="x", pady=(0, 4))
        ttk.Label(title, text=heading, style="Pane.TLabel").pack(side="left")
        box = ttk.Frame(panel)
        box.pack(fill="both", expand=True)
        text = tk.Text(box, wrap="word", font=self.text_font, background="#ffffff", foreground="#142f45",
                       relief="solid", borderwidth=1, highlightthickness=0, padx=8, pady=6,
                       spacing1=0, spacing3=2, selectbackground="#c3dcf5", selectforeground="#102d48",
                       exportselection=False, state="disabled", width=20, height=12)
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=lambda *args: self.scroll_text(text, *args))
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.bind("<ButtonRelease-1>", lambda _: self.root.after_idle(lambda: self.update_follow_from_view(text, respect_selection=True)), add=True)
        text.bind("<B1-Motion>", self.pause_for_selection, add=True)
        for event in ("<MouseWheel>", "<Prior>", "<Next>", "<Home>", "<End>"):
            text.bind(event, lambda _, widget=text: self.scroll_event(widget), add=True)
        text.bind("<Control-a>", lambda _: self.select_all(text))
        text.bind("<Control-A>", lambda _: self.select_all(text))
        text.bind("<Control-c>", lambda _: self.copy_selected(text))
        text.bind("<Control-C>", lambda _: self.copy_selected(text))
        return text

    def apply_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost.get()))

    def pause_for_selection(self, _event=None):
        self.follow.set(False)
        self.render_status()

    def update_follow_from_view(self, widget, respect_selection=False):
        selected = bool(widget.tag_ranges("sel")) if respect_selection else False
        self.follow.set(widget.yview()[1] >= 0.9999 and not selected)
        self.render_status()

    def scroll_event(self, widget):
        self.follow.set(False)
        self.root.after_idle(lambda: self.update_follow_from_view(widget))

    def scroll_text(self, widget, *args):
        widget.yview(*args)
        self.update_follow_from_view(widget)

    def resume(self):
        self.follow.set(True)
        self.notice.set("")
        self.refresh()
        for widget in (self.original, self.translation):
            widget.tag_remove("sel", "1.0", "end")
            widget.yview_moveto(1.0)

    def select_all(self, widget):
        self.pause_for_selection()
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    def set_clipboard(self, value):
        if value:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.root.update_idletasks()
            self.notice.set("已复制到剪贴板，未发送消息。 / Copied to clipboard; nothing was sent.")
        else:
            self.notice.set("暂无可复制的字幕。 / No text available to copy yet.")
        self.render_status()

    def copy_selected(self, widget):
        try:
            self.set_clipboard(widget.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
        return "break"

    def copy(self, role):
        value = (copy_both_text(self.displayed.original, self.displayed.translation)
                 if role == "both" else getattr(self.displayed, role))
        self.set_clipboard(value)

    @staticmethod
    def replace_text(widget, value, follow=True):
        before = widget.get("1.0", "end-1c")
        if before == value:
            return
        start, end, insertion = changed_span(before, value)
        widget.mark_set("caption_view_anchor", widget.index("@0,0"))
        widget.mark_gravity("caption_view_anchor", "right")
        selected = widget.tag_ranges("sel")
        if selected:
            widget.mark_set("caption_selection_start", selected[0])
            widget.mark_set("caption_selection_end", selected[-1])
            widget.mark_gravity("caption_selection_start", "right")
            widget.mark_gravity("caption_selection_end", "left")
        widget.configure(state="normal")
        if end > start:
            widget.delete(f"1.0 + {start} chars", f"1.0 + {end} chars")
        widget.insert(f"1.0 + {start} chars", insertion)
        if selected:
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", "caption_selection_start", "caption_selection_end")
        widget.configure(state="disabled")
        if follow:
            widget.yview_moveto(1.0)
        elif widget.index("@0,0") != widget.index("caption_view_anchor"):
            widget.yview("caption_view_anchor")

    def refresh(self):
        current = read_view(self.output)
        history = self.cached_history(current)
        if history is not None:
            self.history_seen = True
            self.latest = history
        elif self.history_seen:
            self.latest = replace(current, original=self.latest.original, translation=self.latest.translation,
                                  updated_at=self.latest.updated_at, captured_at=self.latest.captured_at)
        else:
            self.latest = current
        if (self.displayed.original, self.displayed.translation) != (self.latest.original, self.latest.translation):
            self.notice.set("")
            self.display_revision += 1
        self.displayed = self.latest
        self.replace_text(self.original, self.displayed.original, follow=self.follow.get())
        self.replace_text(self.translation, self.displayed.translation, follow=self.follow.get())
        self.render_status()

    def cached_history(self, current):
        try:
            stat = (self.output / "history.json").stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None
        if signature != self.history_signature:
            self.history_signature = signature
            self.history_cached = read_history_view(self.output, current)
        if self.history_cached is None:
            return None
        return replace(current, original=self.history_cached.original, translation=self.history_cached.translation,
                       captured_at=self.history_cached.captured_at, updated_at=self.history_cached.updated_at)

    def render_status(self):
        label = "网络" if self.latest.transport == "mobile_https" else "USB"
        connection = (f"{label} 已连接 / Connected" if self.latest.state in ("live", "empty")
                      else f"{label} 已断开 / Offline" if self.latest.state == "offline"
                      else "补传或时钟差异 / Delayed or clock mismatch" if self.latest.state == "delayed"
                      else "等待连接 / Waiting")
        mode = "跟随最新 / Following" if self.follow.get() else "浏览历史，持续更新 / Browsing; updating"
        caption_time = self.displayed.captured_at or "--"
        message = f"{connection} · 字幕 / Caption {caption_time}（北京时间 UTC+8） · {mode}"
        if self.notice.get():
            message += " · " + self.notice.get()
        if self.latest.message:
            message += "\n" + self.latest.message
        self.status.set(message)
        color = "#166546" if self.latest.state in ("live", "empty") else "#946116"
        self.status_label.configure(foreground=color)
        self.write_window_status()

    def write_window_status(self):
        payload = {"displayed_updated_at": self.displayed.updated_at or self.displayed.received_at,
                   "displayed_received_at": self.displayed.received_at, "follow": self.follow.get(),
                   "status": self.latest.state, "original_chars": len(self.displayed.original),
                   "translation_chars": len(self.displayed.translation), "viewer_pid": os.getpid(),
                   "display_revision": self.display_revision}
        if payload == self.last_window_status:
            return
        try:
            temporary = self.output / "window_status.json.tmp"
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.output / "window_status.json")
            self.last_window_status = payload
        except OSError:
            pass

    def tick(self):
        if ((self.stop_file is not None and self.stop_file.exists())
                or not owner_alive(self.owner_pid, self.owner_created)):
            self.close()
            return
        self.refresh()
        self.after_id = self.root.after(POLL_MS, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
        self.root.destroy()


def self_test():
    """Finite native widget checks, using only a temporary capture fixture."""
    enable_dpi_awareness()
    with tempfile.TemporaryDirectory(prefix="captionrelay_caption_window_test_") as temporary:
        output = Path(temporary)
        stamp = datetime.now(timezone.utc).isoformat()
        status = {"state": "connected", "run_id": "self-test", "started_at": stamp, "last_event_at": stamp}
        caption = {"type": "caption_snapshot", "run_id": "self-test", "received_at": stamp,
                   "items": [{"role": "original", "text": "Widget test only"}, {"role": "translation", "text": "仅供窗口测试"}]}
        (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
        (output / "caption_latest.json").write_text(json.dumps(caption), encoding="utf-8")
        root = tk.Tk()
        saved_clipboard = None
        try:
            try:
                saved_clipboard = root.clipboard_get()
            except tk.TclError:
                pass
            app = CaptionWindow(root, output, schedule=False)
            root.update()
            assert root.winfo_viewable()
            left, top, right, bottom = work_area(root)
            assert root.winfo_width() == min(1080, right - left - 48)
            assert root.winfo_rooty() + root.winfo_height() <= bottom
            assert app.status_label.winfo_viewable()
            assert app.status_label.winfo_rooty() + app.status_label.winfo_height() <= root.winfo_rooty() + root.winfo_height()
            assert app.copy_both_button.winfo_viewable() and app.resume_button.winfo_viewable()
            if os.name == "nt":
                from ctypes import wintypes
                physical = wintypes.RECT()
                assert ctypes.windll.user32.GetClientRect(root.winfo_id(), ctypes.byref(physical))
                assert physical.right - physical.left == root.winfo_width()
                assert ctypes.windll.user32.IsProcessDPIAware()
            assert app.original.winfo_width() > 250 and app.translation.winfo_width() > 250
            assert root.attributes("-topmost")
            app.topmost_check.invoke()
            root.update()
            assert not root.attributes("-topmost")
            app.topmost_check.invoke()
            root.update()
            assert root.attributes("-topmost")
            app.copy_both_button.invoke()
            assert root.clipboard_get() == "英文原文 / Original\nWidget test only\n\n中文翻译 / Translation\n仅供窗口测试"
            app.copy("original")
            assert root.clipboard_get() == "Widget test only"
            app.copy("translation")
            assert root.clipboard_get() == "仅供窗口测试"
            app.original.event_generate("<ButtonPress-1>", x=20, y=20)
            root.update()
            assert app.follow.get(), "A plain click must not freeze the viewer"
            app.original.tag_add("sel", "1.0", "1.6")
            app.update_follow_from_view(app.original, respect_selection=True)
            before_selection = app.original.get("sel.first", "sel.last")
            caption["items"][0]["text"] = "Widget test only + fresh caption"
            (output / "caption_latest.json").write_text(json.dumps(caption), encoding="utf-8")
            app.refresh()
            assert app.displayed.original == "Widget test only + fresh caption"
            assert app.original.get("1.0", "end-1c") == app.displayed.original
            assert app.original.get("sel.first", "sel.last") == before_selection
            assert "浏览历史，持续更新" in app.status.get()
            app.resume_button.invoke()
            assert app.original.get("1.0", "end-1c") == "Widget test only + fresh caption"
            status["state"] = "stopped"
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            app.refresh()
            assert app.status.get().startswith("USB 已断开 / Offline")
            status.update(state="connected", run_id="new-run")
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            app.refresh()
            assert app.original.get("1.0", "end-1c") == ""
            assert app.translation.get("1.0", "end-1c") == ""
            assert app.text_font.actual("size") == 14
            history = {"original": "\n".join(f"History line {number}" for number in range(100)),
                       "translation": "\n".join(f"历史第 {number} 行" for number in range(100)), "updated_at": stamp, "entries": []}
            (output / "history.json").write_text(json.dumps(history), encoding="utf-8")
            app.refresh()
            root.update()
            app.scroll_text(app.original, "moveto", 0.2)
            app.original.tag_add("sel", "20.0", "20.7")
            root.update()
            top_before = app.original.index("@0,0")
            top_pixel_before = app.original.dlineinfo(top_before)[1]
            selected_before = app.original.get("sel.first", "sel.last")
            history["original"] += "\n" + "\n".join(f"Appended history {number}" for number in range(80)) + "\nA new history line"
            (output / "history.json").write_text(json.dumps(history), encoding="utf-8")
            app.refresh()
            root.update()
            assert app.original.index("@0,0") == top_before
            assert app.original.dlineinfo(top_before)[1] == top_pixel_before
            assert app.original.get("sel.first", "sel.last") == selected_before
            assert app.original.get("1.0", "end-1c").endswith("A new history line")
            assert not app.follow.get()
            window_status = read_json(output / "window_status.json")
            assert window_status["displayed_updated_at"] == history["updated_at"]
            assert window_status["original_chars"] == len(history["original"])
            status_mtime = (output / "window_status.json").stat().st_mtime_ns
            app.refresh()
            assert (output / "window_status.json").stat().st_mtime_ns == status_mtime
            history["original"] = history["original"].replace("History line 5\n", "Revised history line 5\n")
            (output / "history.json").write_text(json.dumps(history), encoding="utf-8")
            app.refresh()
            root.update()
            assert app.original.index("@0,0") == top_before
            assert app.original.get("sel.first", "sel.last") == selected_before
            app.scroll_text(app.original, "moveto", 1.0)
            root.update()
            assert app.follow.get(), "Scrolling to the bottom must resume following"
            assert app.original.get("1.0", "end-1c").endswith("A new history line")
            assert app.original.yview()[1] == 1.0
            status["state"] = "stopped"
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            app.refresh()
            assert "History line 0" in app.original.get("1.0", "end-1c")
            assert app.status.get().startswith("USB 已断开 / Offline")
            status.update(state="error", error="iPhone 未信任此电脑。请解锁手机并选择“信任”。 / Unlock the iPhone and tap Trust This Computer, then reconnect USB.")
            (output / "status.json").write_text(json.dumps(status), encoding="utf-8")
            app.refresh()
            assert status["error"] in app.status.get()
            default_size = (root.winfo_width(), root.winfo_height())
            for tested_width, tested_height in ((480, 260), (1280, 700)):
                root.geometry(f"{tested_width}x{tested_height}")
                root.update()
                assert (root.winfo_width(), root.winfo_height()) == (tested_width, tested_height)
                assert app.toolbar_compact == (tested_width < 860)
                for widget in (app.copy_original_button, app.copy_translation_button, app.copy_both_button,
                               app.resume_button, app.topmost_check, app.original, app.translation, app.status_label):
                    assert widget.winfo_viewable()
                    assert widget.winfo_rootx() >= root.winfo_rootx()
                    assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
                    assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height()
                for widget in (app.copy_original_button, app.copy_translation_button, app.copy_both_button,
                               app.resume_button, app.topmost_check):
                    assert widget.winfo_width() >= widget.winfo_reqwidth()
            root.geometry(f"{default_size[0]}x{default_size[1]}")
            root.update()
            result = {"passed": True, "width": root.winfo_width(), "height": root.winfo_height(),
                      "topmost_toggle": True, "clipboard_bilingual": True,
                      "selection_preserved": True, "stale_state": True, "run_isolation": True,
                      "dpi_aware_physical_width": True, "footer_visible": True,
                      "history_scroll_retained": True, "history_offline_retained": True, "caption_font_14pt": True,
                      "delivery_while_browsing": True, "bottom_resumes_follow": True, "window_status_matches_history": True,
                      "resized_480x260": True, "resized_1280x700": True, "toolbar_controls_unclipped": True,
                      "actionable_error_visible": True}
        finally:
            root.clipboard_clear()
            if saved_clipboard is not None:
                root.clipboard_append(saved_clipboard)
                root.update_idletasks()
            root.destroy()
        stop_file = output / "viewer.stop"
        stop_root = tk.Tk()
        stop_app = CaptionWindow(stop_root, output, schedule=False, stop_file=stop_file)
        stop_root.update()
        stop_file.write_text("stop", encoding="utf-8")
        stop_app.tick()
        assert stop_app.closed
        result["graceful_stop_file"] = True
        owner_root = tk.Tk()
        owner_app = CaptionWindow(owner_root, output, schedule=False, owner_pid=os.getpid(), owner_created=psutil.Process().create_time() + 5)
        owner_root.update()
        owner_app.tick()
        assert owner_app.closed
        result["owner_identity_exit"] = True
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BASE / "output")
    parser.add_argument("--no-topmost", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stop-file", type=Path, help="Optional supervisor stop marker; closes the viewer when present")
    parser.add_argument("--owner-pid", type=int, help="Optional supervising process ID")
    parser.add_argument("--owner-created", type=float, help="Supervisor creation time from psutil; requires --owner-pid")
    args = parser.parse_args()
    if (args.owner_pid is None) != (args.owner_created is None):
        parser.error("--owner-pid and --owner-created must be supplied together")
    if args.self_test:
        self_test()
        return
    enable_dpi_awareness()
    root = tk.Tk()
    CaptionWindow(root, args.output, topmost=not args.no_topmost, stop_file=args.stop_file,
                  owner_pid=args.owner_pid, owner_created=args.owner_created)
    root.mainloop()


if __name__ == "__main__":
    main()
