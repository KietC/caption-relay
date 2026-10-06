"""Extract Xiaomi caption text without guessing speakers or finalized sentences.

Pairing means adjacent source/translation views in one snapshot, not a proven
linguistic match. An absent translation is never borrowed from another row.
"""
import hashlib
import json

PACKAGE = "com.xiaomi.aiasst.vision"
ROLES = {
    PACKAGE + ":id/message_body": "original",
    PACKAGE + ":id/tv_dest_message": "translation",
}
SENTENCE_ID = PACKAGE + ":id/sentence_id"
CONTAINER_ROLES = {
    PACKAGE + ":id/recyclerView_source": "original",
    PACKAGE + ":id/recyclerView_dest": "translation",
}


def caption_role(node):
    """Use observed Xiaomi view ancestry, never the text's apparent language.

    Compact overlays reuse sentence_id on both sides; only the exact known
    source/destination container identifies their roles. Legacy expanded rows
    remain readable without container metadata.
    """
    if node.get("package") != PACKAGE:
        return None
    node_id = node.get("id")
    if not isinstance(node_id, str):
        return None
    container = node.get("container_id")
    if "container_id" in node and (not isinstance(container, str) or container not in CONTAINER_ROLES):
        return None
    if node_id == SENTENCE_ID:
        return CONTAINER_ROLES.get(container)
    role = ROLES.get(node_id)
    if container is not None and CONTAINER_ROLES[container] != role:
        return None
    return role


def _bounds(node):
    value = node.get("bounds")
    if (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(x, (int, float)) for x in value)):
        return value
    return None


def _can_pair(source, translation):
    a, b = _bounds(source), _bounds(translation)
    # Clipped/inverted bounds occur in the real sample. Keep those unpaired.
    return bool(a and b and a[2] > a[0] and a[3] > a[1]
                and b[2] > b[0] and b[3] > b[1]
                and b[1] >= a[3] and min(a[2], b[2]) > max(a[0], b[0]))


def content_signature(result):
    """Ignore geometry, clocks, window IDs and pairing; retain text multiplicity."""
    content = [[item["role"], item["text"]] for item in result["items"]]
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract(snapshot):
    """Return caption paragraphs in per-window visual order, plus raw text items.

    Missing sides are None; an explicitly observed empty text remains "".
    items preserves every occurrence. Compare signature across consecutive
    snapshots only: this is not a global sentence deduplicator or a transcript.
    """
    result = {"type": "caption_snapshot", "timestamp": snapshot.get("timestamp"),
              "received_at": snapshot.get("received_at"), "items": [],
              "paragraphs": [], "pairing_basis": "adjacent_ui_nodes_only",
              "speaker": None, "is_final": None}
    for window_index, window in enumerate(snapshot.get("windows", [])):
        if window.get("package") != PACKAGE:
            continue
        items = []
        for node_index, node in enumerate(window.get("nodes", [])):
            role = caption_role(node)
            if role is None:
                continue
            value = node.get("text")
            item = {"role": role, "text": value if isinstance(value, str) else "",
                    "window_index": window_index, "window_id": window.get("id"),
                    "node_index": node_index, "node_id": node["id"], "bounds": _bounds(node)}
            if "container_id" in node:
                item["container_id"] = node["container_id"]
            items.append(item)
        # Accessibility traversal can list rows in reverse order in this app.
        # If any coordinates are unavailable, do not partially reorder the tree.
        if all(item["bounds"] is not None for item in items):
            items.sort(key=lambda item: (item["bounds"][1], item["bounds"][0], item["node_index"]))
        result["items"].extend(items)
        index = 0
        while index < len(items):
            item = items[index]
            following = items[index + 1] if index + 1 < len(items) else None
            pair = (item["role"] == "original" and following is not None
                    and following["role"] == "translation" and _can_pair(item, following))
            paragraph = {"original": None, "translation": None,
                         "pairing": "ui_adjacency" if pair else "unpaired",
                         "window_index": window_index, "window_id": window.get("id"),
                         "node_indices": [item["node_index"]]}
            paragraph[item["role"]] = item["text"]
            if pair:
                paragraph["translation"] = following["text"]
                paragraph["node_indices"].append(following["node_index"])
            result["paragraphs"].append(paragraph)
            index += 2 if pair else 1
    result["original_count"] = sum(item["role"] == "original" for item in result["items"])
    result["translation_count"] = sum(item["role"] == "translation" for item in result["items"])
    result["paired_count"] = sum(p["pairing"] == "ui_adjacency" for p in result["paragraphs"])
    result["unpaired_count"] = len(result["paragraphs"]) - result["paired_count"]
    result["has_captions"] = any(item["text"].strip() for item in result["items"])
    result["signature"] = content_signature(result)
    return result


def format_text(result):
    """Human-readable current screen; never claim a caller identity or finality."""
    lines = ["手机字幕快照（原样保留；可能修订；不判断说话人或最终句）",
             "原文/译文仅按同一界面相邻节点配对，不保证语言内容对应。"]
    if not result["items"]:
        return "\n".join(lines + ["当前未发现字幕节点。", ""])
    for number, paragraph in enumerate(result["paragraphs"], 1):
        label = "界面相邻" if paragraph["pairing"] == "ui_adjacency" else "未配对"
        lines.append(f"\n[{number} | {label}]")
        for key, name in (("original", "原文"), ("translation", "译文")):
            text = paragraph[key]
            if text is not None:
                lines.append(name + "：" + (text if text else "（节点暂为空）"))
    return "\n".join(lines) + "\n"
