"""Extract Xiaomi caption text without guessing speakers or finalized sentences.

Pairing means adjacent source/translation views in one snapshot, not a proven
linguistic match. An absent translation is never borrowed from another row.

提取小米字幕文本，不猜测说话人或句子是否已经识别完成。
配对仅表示同一快照中相邻的原文和译文视图，不证明语言学对应关系。
缺失的译文绝不从另一行借用。
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

    根据已观察到的小米视图祖先确定角色，不根据文字看起来属于哪种语言判断。
    紧凑浮窗的两侧使用相同 sentence_id，必须由已知的原文或译文容器区分。
    旧版展开行不依赖容器元数据仍可读取。
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
    # Clipped or inverted bounds can occur in accessibility snapshots; keep those items unpaired.
    # 裁切或倒置的坐标可能出现在输入中；这些项保持未配对。
    return bool(a and b and a[2] > a[0] and a[3] > a[1]
                and b[2] > b[0] and b[3] > b[1]
                and b[1] >= a[3] and min(a[2], b[2]) > max(a[0], b[0]))


def content_signature(result):
    """Ignore geometry, clocks, window IDs and pairing; retain text multiplicity.

    忽略几何位置、时间、窗口编号和配对，保留文字的出现次数。
    """
    content = [[item["role"], item["text"]] for item in result["items"]]
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract(snapshot):
    """Return caption paragraphs in per-window visual order, plus raw text items.

    Missing sides are None; an explicitly observed empty text remains "".
    items preserves every occurrence. Compare signature across consecutive
    snapshots only: this is not a global sentence deduplicator or a transcript.

    按各窗口中的视觉顺序返回字幕段落，同时保留所有原始文字项。
    缺失一侧表示为 None；明确观察到的空文本仍为 ""。
    items 保留每次出现，只比较连续快照的签名；这里不是全局去重器或完整转写引擎。
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
        # 该应用的无障碍遍历可能倒序列出行；只要有坐标缺失，就不对树的一部分进行重排。
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
    """Human-readable current screen; never claim a caller identity or finality.

    生成当前屏幕的人类可读文本，不声明来电者身份或识别终稿。
    """
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
