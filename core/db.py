import sqlite3
import os
import shutil
import struct
import time
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Iterator

import sqlite_vec

from config import DB_PATH

logger = logging.getLogger(__name__)

_VEC_LOADED = False

_BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", "backups")
_BACKUP_INTERVAL_SECONDS = 21600


def _get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    _load_vec(conn)
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _load_vec(conn: sqlite3.Connection) -> None:
    global _VEC_LOADED
    try:
        sqlite_vec.load(conn)
        _VEC_LOADED = True
        logger.info("sqlite-vec loaded via sqlite_vec.load()")
    except Exception as e1:
        try:
            conn.enable_load_extension(True)
            conn.load_extension(sqlite_vec.loadable_path())
            _VEC_LOADED = True
            logger.info("sqlite-vec loaded via direct extension load")
        except Exception as e2:
            logger.warning(f"sqlite-vec not available: {e1}; {e2}")
            logger.warning("Vector search disabled. /recall will not work.")


def init_db() -> None:
    conn = _get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content     TEXT NOT NULL,
            source      TEXT DEFAULT 'telegram',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            session_id  INTEGER REFERENCES sessions(id),
            intent      TEXT,
            embedded    INTEGER DEFAULT 0,
            flag_status TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            converted_at  DATETIME,
            task_board    TEXT,
            status        TEXT DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS summaries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER REFERENCES sessions(id),
            content     TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_current  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER REFERENCES sessions(id),
            project         TEXT NOT NULL,
            action          TEXT NOT NULL,
            source_msg_id   INTEGER REFERENCES messages(id),
            estimated_mins  INTEGER,
            block_number    INTEGER,
            status          TEXT DEFAULT 'todo',
            order_index     INTEGER
        );

        CREATE TABLE IF NOT EXISTS logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER REFERENCES sessions(id),
            planned         TEXT,
            shipped         TEXT,
            failed          TEXT,
            next_action     TEXT,
            diagnosis       TEXT,
            layer_failed    TEXT,
            pattern_warning TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS flagged_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  INTEGER REFERENCES messages(id),
            session_id  INTEGER REFERENCES sessions(id),
            reason      TEXT,
            status      TEXT DEFAULT 'pending',
            remind_at   DATETIME,
            resolved_at DATETIME,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        if _VEC_LOADED:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
                    message_id INTEGER PRIMARY KEY,
                    embedding  FLOAT[768]
                )
            """)
        conn.commit()
    finally:
        conn.close()


def is_vec_loaded() -> bool:
    return _VEC_LOADED


def _ensure_vec_table(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("SELECT 1 FROM vectors LIMIT 1")
    except sqlite3.OperationalError:
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
                    message_id INTEGER PRIMARY KEY,
                    embedding  FLOAT[768]
                )
            """)
            conn.commit()
        except Exception:
            pass


def backup_db() -> str:
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(_BACKUP_DIR, f"brain_{timestamp}.db")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, bak)
        logger.info(f"Backup created: {bak} ({os.path.getsize(bak)} bytes)")
    else:
        logger.warning("Backup skipped: database file does not exist")
    _prune_backups()
    return bak


def _prune_backups(keep: int = 10) -> None:
    if not os.path.isdir(_BACKUP_DIR):
        return
    backups = sorted(
        [f for f in os.listdir(_BACKUP_DIR) if f.startswith("brain_") and f.endswith(".db")],
        reverse=True,
    )
    for old in backups[keep:]:
        path = os.path.join(_BACKUP_DIR, old)
        os.remove(path)
        logger.info(f"Pruned old backup: {old}")


# --- Messages ---

def insert_message(content: str, session_id: int, source: str = "telegram", intent: Optional[str] = None) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO messages (content, source, session_id, intent) VALUES (?, ?, ?, ?)",
            (content, source, session_id, intent),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_session_messages(session_id: int) -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
        ).fetchall()
    finally:
        conn.close()


def update_message_flag(msg_id: int, flag_status: str) -> None:
    conn = _get_connection()
    try:
        conn.execute("UPDATE messages SET flag_status = ? WHERE id = ?", (flag_status, msg_id))
        conn.commit()
    finally:
        conn.close()


def mark_message_embedded(msg_id: int) -> None:
    conn = _get_connection()
    try:
        conn.execute("UPDATE messages SET embedded = 1 WHERE id = ?", (msg_id,))
        conn.commit()
    finally:
        conn.close()


def get_unembedded_messages() -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM messages WHERE embedded = 0 AND content != ''"
        ).fetchall()
    finally:
        conn.close()


# --- Vectors ---

def insert_vector(message_id: int, embedding: list[float]) -> None:
    if not _VEC_LOADED:
        return
    conn = _get_connection()
    try:
        _ensure_vec_table(conn)
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        conn.execute(
            "INSERT INTO vectors (message_id, embedding) VALUES (?, ?)",
            (message_id, blob),
        )
        conn.commit()
    finally:
        conn.close()


def search_vectors(query_embedding: list[float], limit: int = 5) -> list[sqlite3.Row]:
    if not _VEC_LOADED:
        return []
    conn = _get_connection()
    try:
        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
        return conn.execute(
            """
            SELECT v.message_id, v.distance, m.content, m.created_at, m.intent
            FROM vectors v
            JOIN messages m ON m.id = v.message_id
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (blob, limit),
        ).fetchall()
    finally:
        conn.close()


# --- Sessions ---

def create_session() -> int:
    conn = _get_connection()
    try:
        cur = conn.execute("INSERT INTO sessions (status) VALUES ('open')")
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_open_session() -> Optional[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM sessions WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


def close_session(session_id: int, task_board: str) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET status = 'converted', converted_at = CURRENT_TIMESTAMP, task_board = ? WHERE id = ?",
            (task_board, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: int) -> Optional[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()


# --- Summaries ---

def get_current_summary() -> Optional[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM summaries WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


def set_summary(session_id: int, content: str) -> None:
    conn = _get_connection()
    try:
        conn.execute("UPDATE summaries SET is_current = 0 WHERE is_current = 1")
        cur = conn.execute(
            "INSERT INTO summaries (session_id, content, is_current) VALUES (?, ?, 1)",
            (session_id, content),
        )
        conn.commit()
    finally:
        conn.close()


# --- Tasks ---

def insert_task(session_id: int, project: str, action: str, source_msg_id: int,
                estimated_mins: int, block_number: int, order_index: int) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO tasks (session_id, project, action, source_msg_id, estimated_mins, block_number, order_index)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project, action, source_msg_id, estimated_mins, block_number, order_index),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_session_tasks(session_id: int) -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? ORDER BY block_number ASC", (session_id,)
        ).fetchall()
    finally:
        conn.close()


def update_task_status(task_id: int, status: str) -> None:
    conn = _get_connection()
    try:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        conn.commit()
    finally:
        conn.close()


# --- Logs ---

def insert_log(session_id: int, planned: str, shipped: str, failed: str,
               next_action: str, diagnosis: str, layer_failed: str,
               pattern_warning: Optional[str]) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO logs (session_id, planned, shipped, failed, next_action, diagnosis, layer_failed, pattern_warning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, planned, shipped, failed, next_action, diagnosis, layer_failed, pattern_warning),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_latest_log() -> Optional[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()


def get_session_log(session_id: int) -> Optional[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute("SELECT * FROM logs WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    finally:
        conn.close()


# --- Flagged Items ---

def insert_flagged(message_id: int, session_id: int, reason: str) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO flagged_items (message_id, session_id, reason) VALUES (?, ?, ?)",
            (message_id, session_id, reason),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pending_flagged(session_id: int) -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM flagged_items WHERE session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()


def update_flagged_status(flag_id: int, status: str, remind_at: Optional[str] = None) -> None:
    conn = _get_connection()
    try:
        if remind_at:
            conn.execute(
                "UPDATE flagged_items SET status = ?, remind_at = ? WHERE id = ?",
                (status, remind_at, flag_id),
            )
        else:
            conn.execute(
                "UPDATE flagged_items SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, flag_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_due_reminders() -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM flagged_items WHERE status = 'pending' AND remind_at IS NOT NULL AND remind_at <= CURRENT_TIMESTAMP"
        ).fetchall()
    finally:
        conn.close()
