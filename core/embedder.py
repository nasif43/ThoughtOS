import asyncio
import logging
import time
from typing import Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_EMBED_MODEL
from core.db import get_unembedded_messages, mark_message_embedded, insert_vector

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def embed_text_sync(text: str) -> Optional[list[float]]:
    for attempt in range(3):
        try:
            client = _get_client()
            response = client.embeddings.create(
                model=GROQ_EMBED_MODEL,
                input=text,
            )
            return response.data[0].embedding
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
