from typing import Optional

from core.embedder import embed_text_sync
from core.db import search_vectors


def find_related(query_text: str, limit: int = 5) -> list[dict]:
    embedding = embed_text_sync(query_text)
    if not embedding:
        return []
    results = search_vectors(embedding, limit=limit)
    related = []
    for row in results:
        related.append({
            "message_id": row["message_id"],
            "content": row["content"],
            "created_at": row["created_at"],
            "distance": row["distance"],
        })
    return related
