# ThoughtOS

A personal Telegram bot that acts as a second brain. You send it raw thoughts throughout the day — fragments, tasks, blockers, observations — and at the end of the day you run `/convert` and it turns everything into a structured task board. Built to reduce the friction between thinking and planning.

## How it works

**Passive capture:** Any message you send to the bot is stored and embedded in the background. You don't categorise it, you don't structure it. You just send it and get "got it." back.

**Active conversion (`/convert`):** When you're ready to plan, run `/convert [time available]`. The bot:
1. Pulls your recent inbox (Tier 1 memory) and a rolling compressed summary of older context (Tier 2)
2. Runs a vector search over your messages to surface related thoughts you might have forgotten
3. Sends everything to Groq in a single prompt and gets back a structured task board with estimated times, flagged blockers, and a compressed summary for next time
4. Writes all of this to the database in one transaction

**Flag follow-up:** After conversion, the bot schedules a 2-minute follow-up on any flagged items — things it flagged as unclear or unresolved. You can action them, defer them, or drop them.

**Midpoint check-in:** Halfway through your stated work time, the bot checks in. On track, drifted, or done? If you've drifted, it offers two options: timebox recovery or park the task.

**Recall (`/recall [query]`):** Search your captured thoughts by meaning, not keywords. Runs a vector similarity search over everything you've sent.

## Architecture

```
second-brain-bot/
├── bot.py              # Telegram handlers, conversation states, scheduler
├── config.py           # Typed constants from .env
├── core/
│   ├── db.py           # SQLite + sqlite-vec, 7 tables, tx() context manager, WAL mode
│   ├── embedder.py     # fastembed (nomic-embed-text-v1.5), local inference, async wrapper
│   ├── memory.py       # Tier 1 assembly, Tier 2 retrieval, token budget
│   ├── retriever.py    # find_related() — embed query → sqlite-vec ANN search
│   ├── converter.py    # /convert pipeline end-to-end
│   ├── logger.py       # /log pipeline
│   ├── guardrails.py   # source_msg_id validation
│   ├── health.py       # startup health check (DB, vec extension, Groq, Telegram)
│   └── log_setup.py    # rotating file handler + console
└── prompts/
    ├── system.py       # base system prompt
    ├── convert.py      # /convert prompt with JSON schema
    ├── log.py          # /log prompt
    └── summarise.py    # Tier 2 compression prompt
```

## Design decisions worth noting

**Local embeddings.** Embeddings run locally via fastembed (ONNX runtime, `nomic-embed-text-v1.5`, 768 dimensions, ~150MB RAM). No embedding API calls, no cost, no latency on capture.

**Single Groq call for `/convert`.** The task board and the Tier 2 summary are generated in one prompt. The JSON schema includes a `summary` field — the bot extracts it and stores it without a second round trip.

**Fallback model chain on rate limits.** If Groq returns a 429, the bot retries across a fallback list: `groq/compound → llama-3.3-70b-versatile → mixtral-8x7b-32768 → llama-3.1-8b-instant`. Each model's rate limit info is logged.

**WAL mode + `tx()` context manager.** All `/convert` writes (tasks, flagged items, session close, new session, summary) happen inside a single `BEGIN IMMEDIATE` transaction with `busy_timeout=5000`. Either everything commits or nothing does.

**Write serialisation.** An `asyncio.Lock()` on `/convert` prevents concurrent runs from corrupting the session state.

**Scheduled backups.** The database is backed up at startup and every 6 hours. Timestamped files, pruned to the last 10.

## Tech stack

| | |
|---|---|
| LLM | Groq (`groq/compound` with fallback chain) |
| Embeddings | fastembed (`nomic-embed-text-v1.5`), local ONNX |
| Vector search | sqlite-vec (`FLOAT[768]` virtual table) |
| Database | SQLite with WAL mode |
| Bot framework | python-telegram-bot |

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):
```
TELEGRAM_BOT_TOKEN=
GROQ_API_KEY=
ALLOWED_USER_ID=
DB_PATH=data/brain.db   # optional, defaults to this
```

```bash
python bot.py
```

The bot runs a health check on startup and reports any issues with the DB, vector extension, Groq API, or Telegram token before entering the main loop.

## Commands

| Command | What it does |
|---|---|
| `/convert [time]` | Converts your inbox into a structured task board |
| `/log` | Logs a work session — what shipped, what failed, diagnosis |
| `/recall [query]` | Semantic search over everything you've captured |
| `/status` | Current session stats |
| Any other message | Captured and embedded in the background |
