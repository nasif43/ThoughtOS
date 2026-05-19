from typing import Optional

from core.db import get_session_messages, get_current_summary, get_latest_log


def count_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3))


def trim_to_budget(text: str, max_tokens: int, item_label: str = "messages") -> str:
    if count_tokens(text) <= max_tokens:
        return text
    words = text.split()
    target_words = int(max_tokens / 1.3)
    trimmed_words = words[-target_words:]
    omitted = len(words) - target_words
    return "...[truncated — " + str(omitted) + " " + item_label + " omitted]\n" + " ".join(trimmed_words)


def get_tier1(session_id: int) -> str:
    messages = get_session_messages(session_id)
    if not messages:
        return ""
    lines = []
    for msg in messages:
        lines.append(f"[ID:{msg['id']}] {msg['content']}")
    return "\n".join(lines)


def get_tier2() -> str:
    summary = get_current_summary()
    if summary:
        return summary["content"]
    return ""


def get_seed_action() -> str:
    log = get_latest_log()
    if log and log["next_action"]:
        return log["next_action"]
    return ""


def get_tier1_with_ids(session_id: int) -> tuple[str, list[int]]:
    messages = get_session_messages(session_id)
    if not messages:
        return "", []
    lines = []
    ids = []
    for msg in messages:
        lines.append(f"[ID:{msg['id']}] {msg['content']}")
        ids.append(msg["id"])
    return "\n".join(lines), ids
