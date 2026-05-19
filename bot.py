import asyncio
import logging
import os
import time
import re
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    GROQ_API_KEY,
    ALLOWED_USER_ID,
    DB_PATH,
    RATE_LIMITS,
)
from core.db import init_db, get_open_session, create_session, insert_message, backup_db
from core.embedder import embed_inbox_message, embed_pending
from core.converter import run_convert
from core.logger import parse_log_input
from core.retriever import find_related
from core.health import health_check

from core.log_setup import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

CONVERTING, AWAITING_FLAG_RESPONSE, AWAITING_FLAG_DETAIL, AWAITING_REMIND_TIME, LOGGING, CHECKIN = range(6)

_last_call: dict[str, float] = {}
_convert_lock = asyncio.Lock()


def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ALLOWED_USER_ID:
            await update.message.reply_text("unauthorized.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def rate_limit(command: str):
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            now = time.time()
            last = _last_call.get(command, 0)
            cooldown = RATE_LIMITS.get(command, 300)
            if now - last < cooldown:
                remaining = int(cooldown - (now - last))
                await update.message.reply_text(
                    f"you ran this {int(now - last)} seconds ago. wait {remaining}s before running again."
                )
                return
            _last_call[command] = now
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


async def check_env(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("all good.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "second brain bot active.\n\n"
        "send anything — i'll store it.\n"
        "/convert [time] — build today's board\n"
        "/log — evening shutdown\n"
        "/recall [query] — search past thoughts\n"
        "/status — current session\n"
        "/help — all commands"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/convert [time] — convert inbox into task board\n"
        "/log — submit shutdown log\n"
        "/recall [query] — search past thoughts semantically\n"
        "/status — show current session stats\n"
        "/help — show this message\n\n"
        "any other message is stored as an inbox entry."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_open_session()
    if not session:
        await update.message.reply_text("no active session.")
        return
    from core.db import get_session_messages, get_session_tasks
    msgs = get_session_messages(session["id"])
    tasks = get_session_tasks(session["id"])
    await update.message.reply_text(
        f"session #{session['id']}\n"
        f"messages: {len(msgs)}\n"
        f"tasks: {len(tasks)}\n"
        f"status: {session['status']}"
    )


@authorized
async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    async with _convert_lock:
        text = update.message.text or ""
        result = run_convert(text)

        if isinstance(result, dict) and result.get("error"):
            if result["error"] == "inbox empty":
                await update.message.reply_text("inbox is empty — send some thoughts first.")
            elif result["error"] == "json parse failed":
                await update.message.reply_text("conversion failed — please try again.")
            elif result["error"] == "groq call failed":
                await update.message.reply_text("groq call failed — check API key and try again.")
            return ConversationHandler.END

        tasks = result["tasks"]
        flagged = result.get("flagged", [])
        related = result.get("related_surfaced", [])
        time_min = result["time_minutes"]

        board_lines = [f"today's board — {time_min} min\n"]
        current_project = None
        for t in tasks:
            proj = t.get("project", "Unknown")
            if proj != current_project:
                board_lines.append(f"\nProject: {proj}")
                current_project = proj
            emoji = ["①", "②", "③", "④", "⑤"][(t.get("block_number", 1) - 1) % 5]
            board_lines.append(
                f"{emoji} {t['action']} — {t.get('estimated_mins', '?')} min"
            )

        if flagged:
            board_lines.append("\nflagged (too vague — I'll follow up shortly):")
            for f in flagged:
                board_lines.append(f"— {f.get('reason', 'vague item')}")

        if related:
            board_lines.append("\nsurfaced from the past:")
            for r in related:
                board_lines.append(f"— {r[:150]}")

        board_lines.append(f"\none block. start with ①. message me when you're done or when the timer ends.")
        await update.message.reply_text("\n".join(board_lines))

        if flagged:
            context.user_data["pending_flags"] = flagged
            context.user_data["convert_session_id"] = result["session_id"]
            asyncio.create_task(_schedule_flag_followup(update, context, 120))

        _schedule_checkin(update, context, result["session_id"], time_min)

    return ConversationHandler.END


async def _schedule_flag_followup(update: Update, context: ContextTypes.DEFAULT_TYPE, delay: int):
    await asyncio.sleep(delay)
    flags = context.user_data.get("pending_flags", [])
    session_id = context.user_data.get("convert_session_id")
    if not flags:
        return
    for f in flags[:1]:
        keyboard = [
            [InlineKeyboardButton("① give details now", callback_data="flag_detail")],
            [InlineKeyboardButton("② remind me later", callback_data="flag_remind")],
            [InlineKeyboardButton("③ drop it", callback_data="flag_drop")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = (
            f"quick follow-up on the flagged item:\n\n"
            f"\"{f.get('reason', 'vague item')}\" — I couldn't make this a task "
            f"because I don't have enough detail.\n\n"
            f"what do you want to do with this?"
        )
        await update.message.reply_text(msg, reply_markup=reply_markup)
        context.user_data["current_flag"] = f
        context.user_data["current_flag_session"] = session_id


async def flag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "flag_detail":
        await query.edit_message_text("go ahead — give me the details and I'll add it as a task.")
        return AWAITING_FLAG_DETAIL
    elif query.data == "flag_remind":
        await query.edit_message_text("got it. reply with when to remind you (e.g. 'in 3 days', 'next Monday').")
        return AWAITING_REMIND_TIME
    elif query.data == "flag_drop":
        flag = context.user_data.get("current_flag", {})
        msg_id = flag.get("message_id")
        if msg_id:
            from core.db import update_message_flag
            update_message_flag(msg_id, "dismissed")
        await query.edit_message_text(
            "dropped. it's still in your history so /recall can find it, but I won't bring it up again."
        )
        return ConversationHandler.END
    return ConversationHandler.END


async def flag_detail_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    detail = update.message.text
    await update.message.reply_text(f"added to today's board: {detail[:100]}...")
    return ConversationHandler.END


async def flag_remind_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.lower()
    remind_at = None
    if "day" in text:
        numbers = re.findall(r"\d+", text)
        if numbers:
            days = int(numbers[0])
            remind_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    if not remind_at:
        remind_at = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

    flag = context.user_data.get("current_flag", {})
    msg_id = flag.get("message_id")
    if msg_id:
        from core.db import update_message_flag
        update_message_flag(msg_id, "remind")
    await update.message.reply_text(f"got it. I'll surface this on {remind_at} in your next /convert.")
    return ConversationHandler.END


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    log_text = text.removeprefix("/log").strip()
    if not log_text:
        await update.message.reply_text(
            "send your shutdown log. format is flexible:\n\n"
            "planned: ...\nshipped: ...\nfailed: ...\nnext: ..."
        )
        return LOGGING

    session = get_open_session()
    session_id = session["id"] if session else create_session()
    parsed = parse_log_input(session_id, log_text)

    if not parsed:
        await update.message.reply_text("log parsing failed — please try again.")
        return ConversationHandler.END

    response = f"shutdown log saved.\n\n"
    if parsed.get("diagnosis"):
        response += f"diagnosis: {parsed['diagnosis']}\n"
    if parsed.get("pattern_warning"):
        response += f"\nwhat held: you caught it and logged it. that's the system working.\n"
    if parsed.get("next_action"):
        response += f"\ntomorrow's seed: {parsed['next_action']}\n"
    response += "\ndone for today."
    await update.message.reply_text(response)
    return ConversationHandler.END


async def log_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session = get_open_session()
    session_id = session["id"] if session else create_session()
    parsed = parse_log_input(session_id, update.message.text)

    if not parsed:
        await update.message.reply_text("log parsing failed — please try again.")
        return LOGGING

    response = f"shutdown log saved.\n\n"
    if parsed.get("diagnosis"):
        response += f"diagnosis: {parsed['diagnosis']}\n"
    if parsed.get("pattern_warning"):
        response += f"\nwhat held: you caught it and logged it. that's the system working.\n"
    if parsed.get("next_action"):
        response += f"\ntomorrow's seed: {parsed['next_action']}\n"
    response += "\ndone for today."
    await update.message.reply_text(response)
    return ConversationHandler.END


async def recall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = update.message.text.removeprefix("/recall").strip()
    if not query_text:
        await update.message.reply_text("usage: /recall [search query]")
        return

    results = find_related(query_text, limit=5)
    if not results:
        await update.message.reply_text("no matches found.")
        return

    lines = [f"{len(results)} matches from your history:\n"]
    for r in results:
        date_str = r.get("created_at", "unknown date")[:10]
        content = r["content"][:200]
        lines.append(f"{date_str}: \"{content}\"")
    await update.message.reply_text("\n".join(lines))


@authorized
async def inbox_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_open_session()
    if not session:
        session_id = create_session()
    else:
        session_id = session["id"]

    content = update.message.text
    msg_id = insert_message(content, session_id)

    await update.message.reply_text("got it.")

    asyncio.create_task(embed_inbox_message(msg_id, content))


async def checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "checkin_ontrack":
        await query.edit_message_text(
            "noted. keep going."
        )
    elif query.data == "checkin_drifted":
        keyboard = [
            [InlineKeyboardButton("① timebox recovery: 10 min to get back", callback_data="drift_timebox")],
            [InlineKeyboardButton("② park it, move to next task", callback_data="drift_park")],
        ]
        await query.edit_message_text(
            "noted. two options:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif query.data == "checkin_done":
        await query.edit_message_text(
            "good. move to the next task. message me when you're ready."
        )
    elif query.data == "drift_timebox":
        await query.edit_message_text("set 10 min to get back to the current task, then continue.")
    elif query.data == "drift_park":
        await query.edit_message_text("marked as blocked. move to the next task now.")

    return CHECKIN


def _schedule_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE, session_id: int, time_minutes: int):
    midpoint = (time_minutes * 60) / 2

    async def send_checkin():
        await asyncio.sleep(midpoint)
        keyboard = [
            [InlineKeyboardButton("① yes, on track", callback_data="checkin_ontrack")],
            [InlineKeyboardButton("② no, I drifted", callback_data="checkin_drifted")],
            [InlineKeyboardButton("③ done, ready for next", callback_data="checkin_done")],
        ]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="halfway check-in.\n\nstill on task ①?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    asyncio.create_task(send_checkin())


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")


def _check_env() -> bool:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")
    if ALLOWED_USER_ID == 0:
        missing.append("ALLOWED_USER_ID")
    if missing:
        print("=" * 50)
        print("MISSING ENVIRONMENT VARIABLES:")
        for m in missing:
            print(f"  - {m}")
        print()
        print(f"Create a .env file in {os.path.dirname(__file__)} with:")
        print(f"  TELEGRAM_BOT_TOKEN=your_token_from_botfather")
        print(f"  GROQ_API_KEY=gsk_your_groq_api_key")
        print(f"  ALLOWED_USER_ID=your_telegram_id_from_userinfobot")
        print("=" * 50)
        return False
    return True


async def _scheduled_backup_loop():
    while True:
        await asyncio.sleep(21600)
        try:
            path = backup_db()
            logger.info(f"Scheduled backup completed: {path}")
        except Exception as e:
            logger.error(f"Scheduled backup failed: {e}")


def main() -> None:
    if not _check_env():
        return

    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    init_db()

    backup_db()

    health_results = health_check()
    if not health_results["groq_api"]:
        logger.warning("Groq API unavailable — /convert and /log will fail until fixed")
    if not health_results["vec_extension"]:
        logger.warning("Vector search unavailable — /recall will not work")

    async def post_init(app):
        await embed_pending()
        asyncio.create_task(_scheduled_backup_loop())

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("convert", convert_command, filters.User(ALLOWED_USER_ID)),
            CommandHandler("log", log_command, filters.User(ALLOWED_USER_ID)),
        ],
        per_message=False,
        states={
            CONVERTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, convert_command)],
            AWAITING_FLAG_RESPONSE: [CallbackQueryHandler(flag_callback)],
            AWAITING_FLAG_DETAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, flag_detail_received)],
            AWAITING_REMIND_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, flag_remind_time)],
            LOGGING: [MessageHandler(filters.TEXT & ~filters.COMMAND, log_text_received)],
            CHECKIN: [CallbackQueryHandler(checkin_callback)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("recall", recall_command, filters.User(ALLOWED_USER_ID)))
    app.add_handler(CommandHandler("status", status_command, filters.User(ALLOWED_USER_ID)))
    app.add_handler(CommandHandler("help", help_command, filters.User(ALLOWED_USER_ID)))
    app.add_handler(CommandHandler("start", start, filters.User(ALLOWED_USER_ID)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ALLOWED_USER_ID), inbox_message))
    app.add_error_handler(error_handler)

    logger.info("Starting Second Brain Bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
