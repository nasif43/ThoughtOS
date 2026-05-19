# Second Brain Bot — User Manual

## Bot Commands (Telegram)

| Command | Description |
|---------|-------------|
| `send any text` | Captured as an inbox thought. Bot replies "got it." |
| `/convert [time]` | Builds today's task board from inbox. Optional time: `/convert 2 hours` or `/convert 30 min`. Defaults to 50 min. |
| `/log` | Evening shutdown log. Free-form text, bot parses it into structured fields. |
| `/recall [query]` | Semantic search across past thoughts. E.g. `/recall java assignment` |
| `/status` | Shows current session stats (message count, task count). |
| `/help` | Lists all commands. |

### /convert Output

Bot replies with a task board grouped by project, each task numbered ①–⑤ with estimated time. If any inbox items are too vague, they appear as "flagged" and the bot follows up after 2 minutes with inline buttons:

- **① give details now** — type more info, it gets added as a task
- **② remind me later** — specify when (e.g. "in 3 days")
- **③ drop it** — dismisses it permanently

### Midpoint Check-in

At half your available time, the bot sends a check-in with 3 options:

- **① yes, on track** — acknowledges, keeps going
- **② no, I drifted** — offers timebox recovery (10 min to get back) or park the task
- **③ done, ready for next** — move to the next block

---

## Dev Commands (VPS)

### Start / Stop / Restart

```bash
# Start in background (survives SSH disconnect)
nohup python bot.py > logs/brain.log 2>&1 &

# Check if running
ps aux | grep bot.py

# Follow live logs
tail -f logs/brain.log

# Stop the bot
pkill -f "python bot.py"
```

### Reset Database (fresh start)

```bash
pkill -f "python bot.py"
rm -f data/brain.db
rm -rf data/backups/
rm -f logs/brain.log logs/error.log
git pull
nohup python bot.py > logs/brain.log 2>&1 &
```

### Update Bot (pull latest code)

```bash
pkill -f "python bot.py"
git pull
nohup python bot.py > logs/brain.log 2>&1 &
```

### Check Health on Startup

Look for these lines in `logs/brain.log`:

```
Health check: ALL PASSED
Starting Second Brain Bot...
```

If any health check fails, the bot will log a warning like:

```
Health check: FAILED — ['embeddings']
```

### Logs

| File | Contents |
|------|----------|
| `logs/brain.log` | Full log (all levels, rotated at 5MB) |
| `logs/error.log` | Warnings and errors only |

### Backups

Automatic backups to `data/backups/`:
- On every startup
- Every 6 hours while running
- Last 10 backups kept, older ones pruned

To restore from a backup:

```bash
cp data/backups/brain_20260519_153727.db data/brain.db
```

---

## Requirements

- Python 3.10+
- Linux x86_64 VPS (512MB RAM is enough)
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Groq API key (from [console.groq.com](https://console.groq.com))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))

## Quick Start

```bash
git clone https://github.com/nasif43/ThoughtOS
cd ThoughtOS
cp .env.example .env
# Edit .env with your tokens
nano .env
python -m venv env
source env/bin/activate
pip install -r requirements.txt
nohup python bot.py > logs/brain.log 2>&1 &
```
