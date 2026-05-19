import asyncio
import logging
import time
from typing import Optional

from config import NOMIC_API_KEY
from core.db import get_unembedded_messages, mark_message_embedded, insert_vector

logger = logging.getLogger(__name__)

_logged_in = False


def _ensure_login() -> None:
    global _logged_in
    if _logged_in:
        return
    if not NOMIC_API_KEY:
        logger.error("NOMIC_API_KEY not set — embeddings disabled")
        return
    try:
        from nomic import login
        login(token=NOMIC_API_KEY)
        _logged_in = True
        logger.info("Nomic login successful")
    except Exception as e:
        logger.error(f"Nomic login failed: {e}")
        _logged_in = False


def embed_text_sync(text: str) -> Optional[list[float]]:
    _ensure_login()
    if not _logged_in:
        return None
    for attempt in range(3):
        try:
            from nomic import embed
            output = embed.text(
                texts=[text],
                model="nomic-embed-text-v1.5",
                task_type="search_document",
            )
            return output["embeddings"][0]
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"Embed attempt {attempt + 1} failed: {e}. Retrying in {wait}s")
            time.sleep(wait)
    logger.error(f"Embed failed after 3 attempts for text: {text[:80]}")
    return None


async def embed_text(text: str) -> Optional[list[float]]:
    return await asyncio.to_thread(embed_text_sync, text)


async def embed_inbox_message(message_id: int, content: str) -> None:
    embedding = await embed_text(content)
    if embedding:
        insert_vector(message_id, embedding)
        mark_message_embedded(message_id)
    else:
        logger.error(f"Embed failed for message {message_id}, marked for retry")


async def embed_pending() -> None:
    unembedded = get_unembedded_messages()
    for msg in unembedded:
        if msg["content"].strip():
            await embed_inbox_message(msg["id"], msg["content"])
