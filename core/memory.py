from typing import Optional

from tiktoken import get_encoding

from core.db import get_session_messages, get_current_summary, get_latest_log

_ENCODING = None

def _get_encoding():
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = get_encoding("cl100k_base")
    return _ENCODING


def count_tokens(text: str) -> int:
    return len(_get_encoding().encode(text or ""))


def trim_to_budget(text: str, max_tokens: int, item_label: str = "messages") -> str:
    enc = _get_encoding()
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    trimmed_tokens = tokens[-max_tokens:]
    omitted = len(tokens) - max_tokens
    trimmed_text = enc.decode(trimmed_tokens)
    return "...[truncated \u2014 " + str(omitted) + " " + item_label + " omitted]\n" + trimmed_text


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
