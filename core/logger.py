import json
import logging
from typing import Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_COMPLETION_MODEL
from core.db import get_session_tasks, insert_log, get_open_session
from prompts.system import SYSTEM_PROMPT
from prompts.log import LOG_PROMPT

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def parse_log_input(session_id: int, raw_log_text: str) -> Optional[dict]:
    tasks = get_session_tasks(session_id)
    task_lines = []
    for t in tasks:
        task_lines.append(f"[Block {t['block_number']}] {t['project']}: {t['action']} — {t['status']}")
    task_board_str = "\n".join(task_lines) if task_lines else "(no task board)"

    prompt = LOG_PROMPT.format(
        task_board_json=task_board_str,
        raw_log_text=raw_log_text,
    )

    try:
        response = _get_client().chat.completions.create(
            model=GROQ_COMPLETION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
    except Exception as e:
        logger.error(f"Groq log call failed: {e}")
        return None

    raw = response.choices[0].message.content
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
