import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def health_check() -> dict:
    results = {
        "database": False,
        "vec_extension": False,
        "groq_api": False,
        "telegram_token": False,
    }

    results["database"] = _check_db()
    results["vec_extension"] = _check_vec()
    results["groq_api"] = _check_groq()
    results["telegram_token"] = _check_telegram()

    all_ok = all(results.values())
    if all_ok:
        logger.info("Health check: ALL PASSED")
    else:
        failed = [k for k, v in results.items() if not v]
        logger.warning(f"Health check: FAILED — {failed}")

    return results


def _check_db() -> bool:
    try:
        from core.db import _get_connection
        conn = _get_connection()
        conn.execute("SELECT 1")
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        required = {"messages", "sessions", "summaries", "tasks", "logs", "flagged_items"}
        missing = required - table_names
        conn.close()
        if missing:
            logger.error(f"Health: DB missing tables: {missing}")
            return False
        logger.info(f"Health: DB OK ({len(tables)} tables)")
        return True
    except Exception as e:
        logger.error(f"Health: DB check failed: {e}")
        return False


def _check_vec() -> bool:
    try:
        import sqlite_vec
        conn = sqlite3.connect(":memory:")
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _health_vec USING vec0(message_id INTEGER PRIMARY KEY, embedding FLOAT[768])"
        )
        conn.close()
        logger.info("Health: sqlite-vec OK")
        return True
    except Exception as e:
        logger.warning(f"Health: sqlite-vec not available: {e}")
        return False


def _check_groq() -> bool:
    try:
        from config import GROQ_API_KEY
        if not GROQ_API_KEY:
            logger.error("Health: GROQ_API_KEY is empty")
            return False
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model="groq/compound",
            messages=[{"role": "user", "content": "respond with one word: ok"}],
            max_tokens=10,
        )
        reply = response.choices[0].message.content.strip().lower()
        logger.info(f"Health: Groq API OK (responded: {reply})")
        return True
    except Exception as e:
        logger.error(f"Health: Groq API check failed: {e}")
        return False


def _check_telegram() -> bool:
    try:
        from config import TELEGRAM_BOT_TOKEN
        if not TELEGRAM_BOT_TOKEN:
            logger.error("Health: TELEGRAM_BOT_TOKEN is empty")
            return False
        logger.info("Health: Telegram token present")
        return True
    except Exception as e:
        logger.error(f"Health: Telegram token check failed: {e}")
        return False
