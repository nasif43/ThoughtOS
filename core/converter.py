import json
import logging
import time
import re
from typing import Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_COMPLETION_MODEL
from core.db import (
    get_open_session, create_session, close_session,
    insert_task, insert_flagged, get_session_tasks,
    set_summary, tx,
)
from core.memory import get_tier1_with_ids, get_tier2, get_seed_action, count_tokens, trim_to_budget
from core.retriever import find_related
from core.guardrails import validate_task_board
from prompts.system import SYSTEM_PROMPT
from prompts.convert import CONVERT_PROMPT
from prompts.summarise import SUMMARISE_PROMPT

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None

TOKEN_BUDGET = 6000
MAX_TIER1_TOKENS = 3000


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def parse_time_from_message(text: str) -> int:
    text = text.lower()
    patterns = [
        (r"(\d+)\s*(?:hours?|hrs?|h)\b", lambda m: int(m.group(1)) * 60),
        (r"(\d+)\s*(?:minutes?|mins?|m)\b", lambda m: int(m.group(1))),
    ]
    for pat, handler in patterns:
        m = re.search(pat, text)
        if m:
            val = handler(m)
            if val > 0:
                return val
    return 50


def _call_groq(messages: list[dict]) -> Optional[str]:
    try:
        response = _get_client().chat.completions.create(
            model=GROQ_COMPLETION_MODEL,
            messages=messages,
            temperature=0.1,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq call failed: {e}")
        return None


def _parse_json(raw: str) -> Optional[dict]:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.removeprefix("```json").removeprefix("```").strip()
        if clean.endswith("```"):
            clean = clean.removesuffix("```").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}\nRaw: {raw[:500]}")
        return None


def run_convert(user_text: str) -> Optional[dict]:
    time_minutes = parse_time_from_message(user_text)
    open_session = get_open_session()
    if not open_session:
        session_id = create_session()
    else:
        session_id = open_session["id"]

    tier1_text, valid_ids = get_tier1_with_ids(session_id)
    if not tier1_text:
        return {"error": "inbox empty", "session_id": session_id}

    if count_tokens(tier1_text) > MAX_TIER1_TOKENS:
        tier1_text = trim_to_budget(tier1_text, MAX_TIER1_TOKENS, "messages")

    tier2_summary = get_tier2()
    seed_action = get_seed_action()
    related_thoughts = find_related(tier1_text[:500])
    related_text = ""
    if related_thoughts:
        lines = []
        for rt in related_thoughts:
            lines.append(f"- \"{rt['content'][:200]}\"")
        related_text = "\n".join(lines)

    prompt = CONVERT_PROMPT.format(
        time_minutes=time_minutes,
        tier2_summary=tier2_summary or "(no prior sessions)",
        related_thoughts=related_text or "(none)",
        seed_action=seed_action or "(none)",
        tier1_messages=tier1_text,
    )

    raw = _call_groq([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    if not raw:
        return {"error": "groq call failed", "session_id": session_id}

    parsed = _parse_json(raw)
    if not parsed:
        return {"error": "json parse failed", "raw": raw, "session_id": session_id}

    valid_tasks, flagged_items = validate_task_board(parsed, valid_ids)

    for i, task in enumerate(valid_tasks):
        insert_task(
            session_id=session_id,
            project=task.get("project", "Unknown"),
            action=task.get("action", ""),
            source_msg_id=task.get("source_msg_id", 0),
            estimated_mins=task.get("estimated_mins", 30),
            block_number=task.get("block", i + 1),
            order_index=i,
        )

    for fi in flagged_items:
        insert_flagged(
            message_id=fi.get("message_id", 0),
            session_id=session_id,
            reason=fi.get("reason", "Flagged by guardrails"),
        )

    close_session(session_id, json.dumps(parsed))
    new_session_id = create_session()
    _regenerate_tier2(session_id, tier1_text)

    tasks = get_session_tasks(session_id)
    return {
        "session_id": session_id,
        "new_session_id": new_session_id,
        "time_minutes": time_minutes,
        "tasks": [dict(t) for t in tasks],
        "flagged": flagged_items,
        "related_surfaced": parsed.get("related_surfaced", []),
        "raw_board": parsed,
    }


def _regenerate_tier2(session_id: int, tier1_text: str) -> None:
    previous = get_tier2()
    prompt = SUMMARISE_PROMPT.format(
        previous_summary=previous or "(no prior sessions)",
        session_messages=tier1_text,
    )
    raw = _call_groq([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    if raw:
        set_summary(session_id, raw.strip())
    else:
        logger.warning("Tier 2 regeneration failed")
