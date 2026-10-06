"""Small, persistent history for the phone's visible subtitle rows.

Android node pairing uses observed UI adjacency; other sources supply their
own rows. There is no speaker or finality detection. Screen overlap preserves
row occurrences while visible rows may be revised. Rendering compacts
consecutive exact bilingual repeats; occurrence entries and raw capture remain intact.

持久保存手机屏幕中可见字幕行的历史。
Android 根据 UI 邻接关系配对，其他来源提供自己的行，不检测说话人或终稿。
屏幕重叠用于保留每次出现，可见行仍可能修订。
显示时压缩连续完全相同的双语重复；出现记录和原始采集数据仍然保留。
"""
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
from atomic_io import replace_with_retry


SIDES = ("original", "translation")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _item_rows(items):
    """Use traversal adjacency: animated/clipped bounds can split a real row.

    使用遍历中的邻接关系；动画或裁切后的坐标可能把真实的一行拆开。
    """
    lookup = {(item.get("window_index"), item.get("window_id"), item.get("node_index")): item
              for item in items if isinstance(item.get("node_index"), int)}
    rows = []
    consumed = set()
    for key in sorted(lookup, key=lambda value: (value[0] or 0, value[2])):
        item = lookup[key]
        if key in consumed or item.get("role") not in SIDES:
            continue
        following_key = (*key[:2], key[2] + 1)
        following = lookup.get(following_key)
        pair = (item["role"] == "original" and following is not None
                and following.get("role") == "translation")
        row = {side: None for side in SIDES}
        row[item["role"]] = item.get("text")
        related = [item]
        if pair:
            row["translation"] = following.get("text")
            consumed.add(following_key)
            related.append(following)
        consumed.add(key)
        bounds = [node.get("bounds") for node in related]
        bounds = [b for b in bounds if isinstance(b, (tuple, list)) and len(b) == 4
                  and all(isinstance(value, (int, float)) for value in b)]
        row["_window"] = key[:2]
        row["_top"] = min((b[1] for b in bounds), default=None)
        row["_bottom"] = max((max(b[1], b[3]) for b in bounds), default=None)
        row["_node"] = key[2]
        rows.append(row)
    rows.sort(key=lambda row: (row["_window"][0] or 0,
                              row["_top"] if row["_top"] is not None else row["_node"]))
    result = []
    for row in rows:
        # Recycler animations briefly expose a second copy over the same row.
        # Genuine repeated captions occupy separate non-overlapping row boxes.
        # 列表动画可能短暂在同一行显示第二份副本；真正重复的字幕位于彼此不重叠的独立行框中。
        duplicate = False
        if row["_top"] is not None:
            for previous in result:
                if (_key(previous) != _key(row) or previous["_window"] != row["_window"]
                        or previous["_top"] is None):
                    continue
                overlap = min(previous["_bottom"], row["_bottom"]) - max(previous["_top"], row["_top"])
                height = min(previous["_bottom"] - previous["_top"], row["_bottom"] - row["_top"])
                if height > 0 and overlap / height > 0.3:
                    duplicate = True
                    break
        if not duplicate:
            result.append(row)
    return result


def _rows(snapshot):
    items = snapshot.get("items")
    if items and all(isinstance(item.get("node_index"), int) for item in items):
        paragraphs = _item_rows(items)
    else:
        paragraphs = snapshot.get("paragraphs")
    if not isinstance(paragraphs, list):
        # Do not invent pairings if only raw items are available.
        # 仅有原始项时，不凭空配对原文和译文。
        paragraphs = [{item.get("role"): item.get("text")}
                      for item in snapshot.get("items", [])
                      if item.get("role") in SIDES]
    result = []
    for paragraph in paragraphs:
        row = {side: paragraph.get(side) if isinstance(paragraph.get(side), str)
               else None for side in SIDES}
        if any(value and value.strip() for value in row.values()):
            result.append(row)
    return result


def _key(row):
    return tuple(row.get(side) for side in SIDES)


def _source_key(source):
    if not isinstance(source, dict):
        return "legacy:android"
    return json.dumps([source.get(field) for field in ("platform", "device_id", "stream_id")],
                      ensure_ascii=False, separators=(",", ":"))


def _match_score(old, new, revise):
    if _key(old) == _key(new):
        return 10.0
    if not revise:
        return 0.0
    # A translation can arrive or be corrected while its source stays fixed.
    # 原文不变时，译文仍可能晚到或被修订。
    if any(old.get(side) and old.get(side) == new.get(side) for side in SIDES):
        return 3.0
    for side in SIDES:
        a, b = (old.get(side) or "").strip(), (new.get(side) or "").strip()
        if not a or not b:
            continue
        if a.startswith(b) or b.startswith(a):
            return 2.0
        # More than the last row can still be receiving recognizer corrections.
        # 识别器可能修订的不只是最后一行。
        if len(a) >= 4 and len(b) >= 4 and a[:3] == b[:3]:
            ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio >= 0.65:
                return ratio
    return 0.0


def _align(old, new, mutable_ids=None):
    """Order-preserving occurrence matching; never a global text set.

    按顺序匹配各次出现，不把相同文本放入全局集合后去重。
    """
    n, m = len(old), len(new)
    scores = [[_match_score(a, b, mutable_ids is None or a.get("entry_id") in mutable_ids)
               for b in new] for a in old]
    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = max(table[i + 1][j], table[i][j + 1],
                              scores[i][j] + table[i + 1][j + 1]
                              if scores[i][j] else 0.0)
    matches = {}
    i = j = 0
    while i < n and j < m:
        score = scores[i][j]
        if score and table[i][j] == score + table[i + 1][j + 1]:
            matches[j] = i
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return matches


def _atomic_write(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    replace_with_retry(temporary, path)


class CaptionHistory:
    def __init__(self, output: Path, markdown_path: Path):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.history_path = self.output / "history.json"
        self.markdown_path = Path(markdown_path)
        self.markdown_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries = []
        self.updated_at = None
        self._visible = []
        self.source_key = _source_key(None)
        self._source_start_id = 0
        if self.history_path.exists():
            saved = json.loads(self.history_path.read_text(encoding="utf-8"))
            self.entries = saved.get("entries", [])
            self.updated_at = saved.get("updated_at")
            self.source_key = saved.get("source_key", _source_key(None))
            self._source_start_id = saved.get("_source_start_id", 0)
            known = {entry["id"] for entry in self.entries}
            self._visible = [row for row in saved.get("_visible", [])
                             if row.get("entry_id") in known]
        self._next_id = max((entry["id"] for entry in self.entries), default=0) + 1
        if self.entries and not self.markdown_path.exists():
            self._write_markdown()

    def as_dict(self):
        displayed = list(self._display_entries())
        return {
            "original": "\n\n".join(entry["original"] for entry in displayed
                                    if entry.get("original")),
            "translation": "\n\n".join(entry["translation"] for entry in displayed
                                       if entry.get("translation")),
            "updated_at": self.updated_at,
            "entries": self.entries,
            "_visible": self._visible,
            "source_key": self.source_key,
            "_source_start_id": self._source_start_id,
            "markdown_path": str(self.markdown_path),
        }

    def _display_entries(self):
        previous = None
        for entry in self.entries:
            key = (_source_key(entry.get("source")), *_key(entry))
            if key != previous:
                yield entry
            previous = key

    def _write_markdown(self):
        lines = ["# 手机实时字幕 / Live phone captions", "",
                 "原文与可用译文按标注的采集来源保存，不判断说话人。",
                 "Original text and available translations retain their capture source; speakers are not inferred.",
                 "OCR 文字可能有识别错误 / OCR text may contain recognition errors.",
                 "相邻完全相同的双语行仅在显示时合并；原始采集保留。",
                 "Consecutive identical bilingual rows are compacted; raw capture is retained.", ""]
        for number, entry in enumerate(self._display_entries(), 1):
            lines.extend([f"### {number}", ""])
            source = entry.get("source")
            label = (" · ".join(f"{field}={source[field]}" for field in
                               ("platform", "device_id", "stream_id", "app", "method")
                               if source.get(field)) if isinstance(source, dict)
                     else "Legacy Android (source metadata unavailable)")
            lines.extend(["**来源 / Source:** " + " ".join(label.split()), ""])
            for side, label in (("original", "Original / 原文"),
                                ("translation", "Translation / 译文")):
                text = entry.get(side)
                if text:
                    lines.extend([f"**{label}:** {text}", ""])
        _atomic_write(self.markdown_path, "\n".join(lines))

    def update(self, caption_snapshot) -> bool:
        """Persist changed screen state; return whether cumulative text changed.

        Empty screens never clear history. A new collector run_id also does not
        reset it. Source changes reset only the visible occurrence mapping.
        Identical consecutive screens from one source do no alignment or writes.

        持久保存变化后的屏幕状态，并返回累计文字是否变化。
        空屏幕和新的采集 run_id 不清除历史；来源变化只重置可见行映射。
        同一来源的连续相同屏幕无需再次匹配或写入。
        """
        rows = _rows(caption_snapshot)
        source = caption_snapshot.get("source")
        source = dict(source) if isinstance(source, dict) else None
        source_key = _source_key(source)
        source_changed = source_key != self.source_key
        if source_changed:
            self.source_key = source_key
            self._visible = []
            self._source_start_id = self._next_id
        if not rows or [_key(row) for row in rows] == [_key(row) for row in self._visible]:
            if source_changed:
                _atomic_write(self.history_path, json.dumps(self.as_dict(), ensure_ascii=False,
                                                          separators=(",", ":")))
            return False
        matches = _align(self._visible, rows)
        candidates = self._visible
        if matches and self._visible:
            # Scroll/recycler frames can hide a row and show it again one frame
            # later. Recover it only while another active occurrence anchors the
            # screen; an unrelated later repetition must remain a new entry.
            # 滚动或列表动画可能隐藏某行并在下一帧重现；只有其他活跃行仍能锚定屏幕时才恢复原记录，之后无关联的重复必须成为新记录。
            active_ids = {row["entry_id"] for row in self._visible}
            positions = [i for i, entry in enumerate(self.entries) if entry["id"] in active_ids]
            start = max(0, min(positions) - len(rows)) if positions else 0
            candidates = [{"entry_id": entry["id"], **{side: entry.get(side) for side in SIDES}}
                          for entry in self.entries[start:]
                          if entry["id"] >= self._source_start_id
                          and _source_key(entry.get("source")) == self.source_key]
            matches = _align(candidates, rows, active_ids)
        by_id = {entry["id"]: entry for entry in self.entries}
        mapped = {new_index: candidates[old_index]["entry_id"]
                  for new_index, old_index in matches.items()}
        now = _now()
        changed = False
        visible = []
        for index, row in enumerate(rows):
            entry_id = mapped.get(index)
            if entry_id is None:
                entry_id = self._next_id
                self._next_id += 1
                entry = {"id": entry_id, **row, "first_seen": now, "updated_at": now,
                         "source": source, "source_key": source_key}
                # Preserve chronology if newly visible text precedes an anchor.
                # 新出现文字位于锚点之前时，仍保留其先后顺序。
                following = next((mapped[j] for j in range(index + 1, len(rows))
                                  if j in mapped), None)
                position = next((i for i, item in enumerate(self.entries)
                                 if item["id"] == following), len(self.entries))
                self.entries.insert(position, entry)
                by_id[entry_id] = entry
                changed = True
            else:
                entry = by_id[entry_id]
                # A clipped/missing node is not a request to erase captured text.
                # 节点被裁切或暂时缺失，不表示需要删除已采集文字。
                merged = {side: row[side] if row.get(side) else entry.get(side) for side in SIDES}
                if _key(entry) != _key(merged):
                    entry.update(merged)
                    entry["updated_at"] = now
                    changed = True
            visible.append({"entry_id": entry_id, **row})
        self._visible = visible
        if changed:
            self.updated_at = now
            self._write_markdown()
        # Persist the visible mapping even when only the scroll position changed.
        # 即使只改变滚动位置，也持久保存可见行映射。
        _atomic_write(self.history_path, json.dumps(self.as_dict(), ensure_ascii=False,
                                                  separators=(",", ":")))
        return changed
