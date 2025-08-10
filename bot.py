import os, time, random, logging
from datetime import datetime, timedelta, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, CallbackContext, MessageHandler, Filters, CallbackQueryHandler
)
from telegram.error import RetryAfter

# ====== ЗАМЕНИ или ЗАДАЙ через ENV ======
# Вариант 1 (рекомендуется): задать в Render → Environment:
#   BOT_TOKEN, MAIN_CHANNEL_ID, APP_URL, PYTHON_VERSION=3.12.6
# Вариант 2: оставить как здесь, но ПЕРЕД коммитом в GitHub замени на свои и НЕ светим реальный токен!

BOT_TOKEN = os.getenv("BOT_TOKEN") or "8031543924:AAF0o3la2YVvUTRTbT3jmBxb3mKXNM7ZFQE"  # <— ЗАМЕНИ на свой
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID") or "-1002767513265")                  # <— ЗАМЕНИ на свой
APP_URL = (os.getenv("APP_URL") or "https://your-service-name.onrender.com").rstrip("/") # <— ЗАМЕНИ на свой Render-URL

# Параметры капчи/ссылки
CAPTCHA_TTL_SEC     = int(os.getenv("CAPTCHA_TTL_SEC", "120"))   # секунд на капчу
INVITE_EXPIRE_MIN   = int(os.getenv("INVITE_EXPIRE_MIN", "5"))   # жизнь инвайта (мин)
INVITE_MEMBER_LIMIT = int(os.getenv("INVITE_MEMBER_LIMIT", "1")) # одноразовая ссылка

# ====== LOG ======
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("gateway-bot")

# user_id -> {answer:int, expires:int}
CAPTCHA = {}

WELCOME = ("👋 Welcome!\n\n"
           "To enter the main channel, please pass a quick captcha.\n"
           "You have *{ttl}* seconds. Tap the correct answer below 👇")

def _check_env():
    if not BOT_TOKEN or "YOUR" in BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (replace in code or via Render Environment).")
    if not MAIN_CHANNEL_ID:
        raise RuntimeError("MAIN_CHANNEL_ID is not set.")
    if not APP_URL or "your-service-name" in APP_URL:
        raise RuntimeError("APP_URL is not set correctly (use your Render URL, e.g. https://gateway-bot1.onrender.com).")

def _make_captcha():
    a = random.randint(10, 49)
    b = random.randint(10, 49)
    op = random.choice(["+", "-"])
    ans = a + b if op == "+" else a - b
    text = f"Solve: {a} {op} {b} = ?"
    # 4 варианта ответа
    opts = {ans}
    while len(opts) < 4:
        opts.add(ans + random.randint(-7, 7))
    buttons = list(opts)
    random.shuffle(buttons)
    return text, ans, buttons

def start(update: Update, context: CallbackContext):
    args = context.args or []
    # deep-link ?start=service → сразу капча
    if len(args) == 1 and args[0].lower() == "service":
        return send_captcha(update, context)
    # обычный /start → показать кнопку deep-link
    bot_username = context.bot.username
    url = f"https://t.me/{bot_username}?start=service"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Service", url=url)]])
    if update.message:
        update.message.reply_text("Tap the button to start verification:", reply_markup=kb)

def send_captcha(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text, ans, options = _make_captcha()

    # клавиатура с вариантами
    rows, row = [], []
    for val in options:
        row.append(InlineKeyboardButton(str(val), callback_data=f"cap:{val}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    kb = InlineKeyboardMarkup(rows)

    if update.message:
        update.message.reply_text(WELCOME.format(ttl=CAPTCHA_TTL_SEC), parse_mode=ParseMode.MARKDOWN)
        update.message.reply_text(f"🧩 {text}", reply_markup=kb)
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text=f"🧩 {text}", reply_markup=kb)

    CAPTCHA[user_id] = {"answer": ans, "expires": int(time.time()) + CAPTCHA_TTL_SEC}

def on_callback(update: Update, context: CallbackContext):
    q = update.callback_query
    user_id = q.from_user.id
    data = q.data or ""
    if not data.startswith("cap:"):
        q.answer(); return

    state = CAPTCHA.get(user_id)
    now = int(time.time())
    if not state or now > state["expires"]:
        CAPTCHA.pop(user_id, None)
        q.answer("⏳ Time is up. Please /start again.", show_alert=True)
        try: q.edit_message_text("⌛ Time is up. Please /start again.")
        except: pass
        return

    try:
        chosen = int(data.split(":", 1)[1])
    except:
        q.answer(); return

    if chosen == state["answer"]:
        CAPTCHA.pop(user_id, None)
        q.answer("✅ Verified!")
        try: q.edit_message_text("✅ Verified! Generating your invite link…")
        except: pass
        send_invite_link(update, context)
    else:
        q.answer("❌ Wrong answer, try again", show_alert=False)

def send_invite_link(update: Update, context: CallbackContext):
    """Создаёт одноразовую инвайт-ссылку в основной канал."""
    try:
        expire_ts = int((datetime.now(timezone.utc) + timedelta(minutes=INVITE_EXPIRE_MIN)).timestamp())
        link = context.bot.create_chat_invite_link(
            chat_id=int(MAIN_CHANNEL_ID),
            expire_date=expire_ts,
            member_limit=INVITE_MEMBER_LIMIT,
            name=f"Gateway for {update.effective_user.id}"
        )
        context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"🔗 Your invite link (valid {INVITE_EXPIRE_MIN} min, {INVITE_MEMBER_LIMIT} use):\n{link.invite_link}"
        )
        context.bot.send_message(
            chat_id=update.effective_user.id,
            text="If the link expired, just /start again."
        )
    except RetryAfter as e:
        wait_for = int(getattr(e, "retry_after", 5)) + 1
        log.warning("Rate limited. Waiting %s seconds", wait_for)
        time.sleep(wait_for)
        return send_invite_link(update, context)
    except Exception:
        log.exception("Failed to create invite link")
        context.bot.send_message(
            chat_id=update.effective_user.id,
            text="⚠️ Could not create invite link. Please try again later."
        )

def help_cmd(update: Update, context: CallbackContext):
    if update.message:
        update.message.reply_text("/start – begin verification\n/help – this help")

def main():
    _check_env()
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start, Filters.chat_type.private))
    dp.add_handler(CommandHandler("help", help_cmd, Filters.chat_type.private))
    dp.add_handler(CallbackQueryHandler(on_callback))
    dp.add_handler(MessageHandler(Filters.chat_type.private & Filters.text & ~Filters.command, send_captcha))

    # --- WEBHOOK MODE for Render Web Service ---
    PORT = int(os.environ.get("PORT", "10000"))
    updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{APP_URL}/{BOT_TOKEN}",
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    log.info("Bot is running (webhook)…")
    updater.idle()

if __name__ == "__main__":
    main()
