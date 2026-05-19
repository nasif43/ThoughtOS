import asyncio
import logging
from typing import Optional

from sentence_transformers import SentenceTransformer

from core.db import get_unembedded_messages, mark_message_embedded, insert_vector

logger = logging.getLogger(__name__)

_model: Optional[SentenceTransformer] = None
_model_lock = asyncio.Lock()


def _get_model() -> Optional[SentenceTransformer]:
    global _model
    if _model is None:
        try:
            _model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5")
            logger.info("Embedding model loaded (nomic-embed-text-v1.5)")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            return None
    return _model


def embed_text_sync(text: str) -> Optional[list[float]]:
    model = _get_model()
    if not model:
        return None
    try:
        return model.encode(text).tolist()
    except Exception as e:
        logger.error(f"Embed failed for text: {text[:80]} — {e}")
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
