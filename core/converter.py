import asyncio
import json
import logging
import re
from typing import Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_FALLBACK_MODELS
from core.db import (
    get_open_session, get_session_tasks, create_session,
    set_summary, tx,
)
from core.memory import get_tier1_with_ids, get_tier2, get_seed_action, count_tokens, trim_to_budget
from core.retriever import find_related
from core.guardrails import validate_task_board
from prompts.system import SYSTEM_PROMPT
from prompts.convert import CONVERT_PROMPT
from prompts.compress import COMPRESS_PROMPT

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None

PROMPT_BUDGET = 5000
RESPONSE_BUDGET = 2000

# Overhead of system + convert prompt (without tier1/tier2/related/seed fill-ins)
# Measured via tiktoken: system=98, empty template=304 tokens
_PROMPT_OVERHEAD = 600


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


def _call_groq(messages: list[dict], max_tokens: int = RESPONSE_BUDGET) -> Optional[str]:
    models = GROQ_FALLBACK_MODELS
    last_error = None
    for model in models:
        try:
            response = _get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                logger.warning(f"Rate limited on {model}, waiting 2s before retry")
                import time
                time.sleep(2)
            elif "too large" in err_str.lower() or "reduce your message" in err_str.lower():
                logger.warning(f"Prompt too large for {model}, trying next model")
            else:
                logger.error(f"Groq call failed on {model}: {e}")
                return None
    logger.error(f"All fallback models exhausted. Last error: {last_error}")
    return None


def _compress_tier1(tier1_text: str) -> Optional[str]:
    budget_for_compress = PROMPT_BUDGET - count_tokens(COMPRESS_PROMPT.replace("{raw_text}", "")) - 100
    if count_tokens(tier1_text) > budget_for_compress:
        tier1_text = trim_to_budget(tier1_text, budget_for_compress, "messages")

    prompt = COMPRESS_PROMPT.format(raw_text=tier1_text)
    compressed = _call_groq(
        [{"role": "user", "content": prompt}],
        max_tokens=400,
    )
    if compressed:
        logger.info(f"Compressed {count_tokens(tier1_text)} tokens -> {count_tokens(compressed)} tokens")
    return compressed


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

    tier2_summary = get_tier2()
    seed_action = get_seed_action()
    related_thoughts = find_related(tier1_text[:500])
    related_text = ""
    if related_thoughts:
        lines = []
        for rt in related_thoughts:
            lines.append(f"- \"{rt['content'][:200]}\"")
        related_text = "\n".join(lines)

    context_tokens = count_tokens(
        (tier2_summary or "(no prior sessions)") + " "
        + (related_text or "(none)") + " "
        + (seed_action or "(none)")
    )

    tier1_budget = PROMPT_BUDGET - _PROMPT_OVERHEAD - context_tokens - 50

    if count_tokens(tier1_text) > tier1_budget:
        logger.info(f"Tier1 ({count_tokens(tier1_text)} tokens) exceeds budget ({tier1_budget}), compressing")
        compressed = _compress_tier1(tier1_text)
        if compressed:
            tier1_text = compressed
        else:
            tier1_text = trim_to_budget(tier1_text, tier1_budget, "messages")

    prompt = CONVERT_PROMPT.format(
        time_minutes=time_minutes,
        tier2_summary=tier2_summary or "(no prior sessions)",
        related_thoughts=related_text or "(none)",
        seed_action=seed_action or "(none)",
        tier1_messages=tier1_text,
    )

    if count_tokens(prompt) > PROMPT_BUDGET:
        logger.warning(f"Final prompt still over budget ({count_tokens(prompt)}), force-trimming")
        trim_target = PROMPT_BUDGET - _PROMPT_OVERHEAD - context_tokens - 50
        tier1_text = trim_to_budget(tier1_text, trim_target, "messages")
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

    with tx() as conn:
        for i, task in enumerate(valid_tasks):
            conn.execute(
                """INSERT INTO tasks (session_id, project, action, source_msg_id, estimated_mins, block_number, order_index)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, task.get("project", "Unknown"), task.get("action", ""),
                 task.get("source_msg_id", 0), task.get("estimated_mins", 30),
                 task.get("block", i + 1), i),
            )

        for fi in flagged_items:
            conn.execute(
                "INSERT INTO flagged_items (message_id, session_id, reason) VALUES (?, ?, ?)",
                (fi.get("message_id", 0), session_id, fi.get("reason", "Flagged by guardrails")),
            )

        conn.execute(
            "UPDATE sessions SET status = 'converted', converted_at = CURRENT_TIMESTAMP, task_board = ? WHERE id = ?",
            (json.dumps(parsed), session_id),
        )

        cur = conn.execute("INSERT INTO sessions (status) VALUES ('open')")
        new_session_id = cur.lastrowid

    summary = parsed.get("summary", "").strip()
    if summary:
        set_summary(session_id, summary)

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
