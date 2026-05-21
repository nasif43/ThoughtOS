import json
import logging
import time
from typing import Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_FALLBACK_MODELS
from core.db import get_session_tasks, get_last_converted_session_tasks, insert_log
from prompts.system import SYSTEM_PROMPT
from prompts.log import LOG_PROMPT

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _call_groq(messages: list[dict]) -> Optional[str]:
    models = GROQ_FALLBACK_MODELS
    last_error = None
    for model in models:
        try:
            response = _get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=1500,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                logger.warning(f"Rate limited on {model}, waiting 2s before retry")
                time.sleep(2)
            elif "too large" in err_str.lower() or "reduce your message" in err_str.lower():
                logger.warning(f"Prompt too large for {model}, trying next model")
            else:
                logger.error(f"Groq log call failed on {model}: {e}")
                return None
    logger.error(f"All fallback models exhausted. Last error: {last_error}")
    return None


def _get_task_board(session_id: int) -> str:
    tasks = get_session_tasks(session_id)
    if not tasks:
        tasks = get_last_converted_session_tasks()
    if not tasks:
        return "(no task board)"
    lines = []
    for t in tasks:
        lines.append(f"[Block {t['block_number']}] {t['project']}: {t['action']} — {t['status']}")
    return "\n".join(lines)


def parse_log_input(session_id: int, raw_log_text: str) -> Optional[dict]:
    task_board_str = _get_task_board(session_id)

    prompt = LOG_PROMPT.format(
        task_board_json=task_board_str,
        raw_log_text=raw_log_text,
    )

    raw = _call_groq([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    if not raw:
        return None

    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.removeprefix("```json").removeprefix("```").strip()
        if clean.endswith("```"):
            clean = clean.removesuffix("```").strip()

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.error(f"Log JSON parse failed: {e}\nRaw: {raw[:500]}")
        return None

    insert_log(
        session_id=session_id,
        planned=parsed.get("planned", ""),
        shipped=parsed.get("shipped", ""),
        failed=parsed.get("failed", ""),
        next_action=parsed.get("next_action", ""),
        diagnosis=parsed.get("diagnosis", ""),
        layer_failed=parsed.get("layer_failed", "none"),
        pattern_warning=parsed.get("pattern_warning"),
    )

    return parsed
