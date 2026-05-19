# Second Brain Bot — Build Document v1.0

## File Structure

```
second-brain-bot/
├── bot.py                 # Entry point. Telegram handlers, conversation states, scheduler.
├── config.py              # Reads .env. Exports typed constants.
├── requirements.txt       # python-telegram-bot, groq, sqlite-vec, python-dotenv, fastembed
├── .env.example           # 3 keys: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, ALLOWED_USER_ID
├── .gitignore
├── README.md
├── core/
│   ├── __init__.py
│   ├── db.py              # SQLite + sqlite-vec. All 7 tables, CRUD per table, tx() context manager, backup
│   ├── embedder.py        # fastembed (nomic-embed-text-v1.5) locally. Sync + async (to_thread wrapper)
│   ├── memory.py          # Tier 1 assembly, Tier 2 retrieval, token budget, seed action
│   ├── retriever.py       # find_related() — embed query → sqlite-vec search
│   ├── converter.py       # /convert pipeline: parse time → assemble prompt → Groq → validate → write → regen Tier 2
│   ├── logger.py          # /log pipeline: Groq parses structured log from free text
│   ├── guardrails.py      # source_msg_id validation against real message IDs
│   ├── scheduler.py       # Wrapper for asyncio.create_task (minimal, mostly unused)
│   ├── health.py          # health_check(): DB connectivity, vec extension, Groq API, Telegram token
│   └── log_setup.py       # Rotating file handler + console logger
└── prompts/
    ├── __init__.py
    ├── system.py           # Base system prompt with 6 hard rules
    ├── convert.py          # /convert prompt template with JSON schema
    ├── log.py              # /log prompt template with layer failure diagnosis
    └── summarise.py        # Tier 2 compression prompt
```

## Configuration (config.py)

| Variable | Source | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `.env` | `""` |
| `GROQ_API_KEY` | `.env` | `""` |
| `ALLOWED_USER_ID` | `.env` | `0` |
| `DB_PATH` | `.env` or default | `data/brain.db` |
| `GROQ_COMPLETION_MODEL` | Hardcoded | `groq/compound` |
| `GROQ_FALLBACK_MODELS` | Hardcoded | `groq/compound, llama-3.3-70b-versatile, mixtral-8x7b-32768, llama-3.1-8b-instant` |
| `RATE_LIMITS` | Hardcoded | `/convert: 300s, /log: 300s` |

## Database Schema (core/db.py)

7 tables + 1 virtual table:

- **messages** — every inbox entry (content, source, session_id, intent, embedded flag, flag_status)
- **vectors** — sqlite-vec virtual table `FLOAT[768]` keyed on message_id
- **sessions** — one per /convert call (started_at, converted_at, task_board JSON, status: open/converted/logged)
- **summaries** — Tier 2 rolling summary (content, is_current boolean, session_id)
- **tasks** — generated task board rows (project, action, source_msg_id, estimated_mins, block_number, status)
- **logs** — shutdown entries (planned, shipped, failed, next_action, diagnosis, layer_failed, pattern_warning)
- **flagged_items** — unresolved items (message_id, reason, status, remind_at)

**Transaction safety:** `tx()` context manager with `BEGIN IMMEDIATE`, `busy_timeout=5000`, WAL mode. `/convert` DB writes wrapped in a single `with tx():` block.

**sqlite-vec loading:** Tries `sqlite_vec.load(conn)` first, falls back to `conn.load_extension(sqlite_vec.loadable_path())`. Wraps failures gracefully — vector search disabled if extension unavailable.

## Bot Commands (bot.py)

| Command | Handler | State | Notes |
|---|---|---|---|
| `/convert [time]` | `convert_command` | CONVERTING → AWAITING_FLAG_\* → END | Async lock on `_convert_lock` |
| `/log` | `log_command` | LOGGING → END | Free-form text parsing |
| `/recall [query]` | `recall_command` | None (no state) | Pure sqlite-vec search |
| `/status` | `status_command` | None | Current session stats |
| `/help` | `help_command` | None | List commands |
| Any non-command | `inbox_message` | None | Store + reply "got it." + background embed |

**ConversationHandler states:** 6 states (CONVERTING, AWAITING_FLAG_RESPONSE, AWAITING_FLAG_DETAIL, AWAITING_REMIND_TIME, LOGGING, CHECKIN). `per_message=False` on ConversationHandler.

## Conversation Flows

**Passive capture:** Text → `insert_message()` → reply "got it." → background `embed_inbox_message()` via `asyncio.create_task()`.

**/convert flow:** `parse_time_from_message()` → `get_tier1_with_ids()` → `get_tier2()` → `find_related()` → assemble CONVERT_PROMPT → `_call_groq()` (retries across fallback models on 429) → `_parse_json()` → `validate_task_board()` → `with tx():` write tasks/flagged/close/create → extract `summary` from JSON → `set_summary()`. Single Groq call for both board + summary. 2-min flag follow-up scheduled via `asyncio.create_task()`. Midpoint check-in scheduled.

**/log flow:** Parse text → assemble LOG_PROMPT → Groq → parse JSON → `insert_log()` → reply with diagnosis.

**Flag follow-up:** 3 inline keyboard options. Detail → new task. Remind → parse time → store. Drop → set `flag_status=dismissed`.

**Midpoint check-in:** Scheduled at `time_minutes * 60 / 2`. Inline keyboard: on track / drifted / done. Drift → 2 sub-options (timebox recovery or park task).

## Hardening (Phase 6)

1. **`tx()` context manager** (`core/db.py`): `BEGIN IMMEDIATE`, commit/rollback, connection always closed in `finally`
2. **Structured logging** (`core/log_setup.py`): Rotating files (5MB), error log (WARNING+), console, millisecond timestamps
3. **`health_check()`** (`core/health.py`): Tests DB connectivity + all 6 tables, sqlite-vec extension load, Groq API with `"respond with one word: ok"`, Telegram token presence. Runs at startup.
4. **Scheduled backups** (`core/db.py` + `bot.py`): Startup backup + 6-hourly interval, timestamped files, prunes to last 10
5. **Write serialization** (`bot.py`): `asyncio.Lock()` on `/convert` to prevent concurrent `run_convert()`
6. **Rate limiting** (`bot.py`): In-memory cooldown dict per command

## Known Issues / Things That Need Validation

1. **Groq rate limits** — `_call_groq()` retries across `GROQ_FALLBACK_MODELS` on 429. Rate limit info logged per model. If all models exhaust, returns `None` and `/convert` fails. Non-429 errors (auth, connectivity) fail immediately without retrying fallbacks.

2. **Sync Groq calls in async handlers** — `run_convert()` and `parse_log_input()` call `_call_groq()` which is synchronous. This blocks the asyncio event loop for 3-10 seconds. The spec says "build sync first, add async wrapper later." For a personal single-user bot this is acceptable but technically incorrect. **Not urgent.**

3. **In-memory scheduler not persistent** — Flag follow-ups (2-min delay) and midpoint check-ins are `asyncio.create_task()` closures. If the bot restarts, all pending schedules are lost. The spec's risk register acknowledges this.

4. **sqlite-vec `FLOAT[768]`** — nomic-embed-text v1 produces 768-dimensional embeddings. The vec0 table uses `FLOAT[768]`. This is correct for nomic-embed-text but if Groq's embedding model were to change dimensions, the schema would break.

5. **Vector search** (`core/retriever.py`): Calls `embed_text_sync()` inside `find_related()`, which is called from `run_convert()` during a `/convert` handler. This means the event loop blocks TWICE during convert: first for the related-thoughts embedding, second for the Groq completion call. Both are sync.

6. **Backup directory permission** — If `data/backups/` can't be created (permissions), `backup_db()` will fail silently for scheduled backups (the loop catches and logs the exception).

## Embedding

Local inference via **fastembed** (`fastembed.TextEmbedding`), model `nomic-embed-text-v1.5`. Runs ONNX runtime, ~150MB RAM, no GPU needed. 768-dimensional output matching the `vec0` schema. Async wrapper uses `asyncio.to_thread()`.
