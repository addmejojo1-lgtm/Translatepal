import os
import openai
from flask import Flask, request, jsonify
import logging
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters
)
import threading
import asyncio
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENVIRONMENT VARIABLES ---
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PUBLIC_URL = os.environ["REPLIT_DOMAINS"]  # Use your Render app domain
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
PORT = int(os.getenv("PORT", 10000))

# Validate webhook secret
if not re.match(r'^[A-Za-z0-9_]{1,256}$', TELEGRAM_WEBHOOK_SECRET):
    invalid_chars = ''.join(set(c for c in TELEGRAM_WEBHOOK_SECRET if not re.match(r'[A-Za-z0-9_]', c)))
    raise ValueError(f"TELEGRAM_WEBHOOK_SECRET contains invalid characters: '{invalid_chars}'. Only A-Z, a-z, 0-9, underscore allowed.")

# OpenAI client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
application = None
event_loop = None
background_thread = None

USER_LANGUAGE = {}
SUPPORTED_LANGUAGES = {
    "fa": ("Persian (Farsi)", "فارسی", "🇮🇷"),
    "fr": ("French", "Français", "🇫🇷"),
    "de": ("German", "Deutsch", "🇩🇪"),
    "es": ("Spanish", "Español", "🇪🇸"),
    "it": ("Italian", "Italiano", "🇮🇹"),
    "tr": ("Turkish", "Türkçe", "🇹🇷"),
    "ru": ("Russian", "Русский", "🇷🇺"),
    "ar": ("Arabic", "العربية", "🇸🇦"),
    "zh": ("Chinese", "中文", "🇨🇳"),
    # Add more as needed
}
DEFAULT_LANGUAGE = "fa"

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    await update.message.reply_text(
        "👋 Hello! I'm your AI translation assistant.\n\n"
        "Send any message in any language, and I'll translate it for you!\n\n"
        "Use /language to set your preferred target language for translations."
    )

async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    row = []
    for code, (en_name, native_name, emoji) in SUPPORTED_LANGUAGES.items():
        row.append(
            InlineKeyboardButton(
                f"{emoji} {en_name}",
                callback_data=f"setlang|{code}"
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Please select your preferred language for translations:",
        reply_markup=markup
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("setlang|"):
        return
    lang_code = query.data.split("|")[1]
    user_id = query.from_user.id
    USER_LANGUAGE[user_id] = lang_code
    lang_name, native_name, emoji = SUPPORTED_LANGUAGES.get(lang_code, ("Unknown", "", ""))
    await query.edit_message_text(
        f"Your preferred language has been set to: {emoji} {lang_name} ({native_name})"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    user_message = str(update.message.text)
    user_id = update.effective_user.id if update.effective_user else None
    target_lang = USER_LANGUAGE.get(user_id, DEFAULT_LANGUAGE)
    system_prompt = f"""
You are a professional translator bot.
When a user sends a message in English, translate it into '{target_lang}' using fluent, natural, native-level language—never literal. When a user sends a message in any other language, translate it into fluent, native-sounding English. Always adapt numbers, expressions, and cultural context to fit naturally. Never say anything else. Only reply with the translation—no explanations or comments.
"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    try:
        chat_response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages
        )
        reply_content = chat_response.choices[0].message.content
        if reply_content:
            await update.message.reply_text(reply_content)
        else:
            await update.message.reply_text("❌ Sorry, I couldn't generate a response.")
    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        await update.message.reply_text(error_msg)
        logger.error(f"Error processing message: {e}")

def run_event_loop():
    global event_loop, application
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)

    async def init_application():
        global application
        application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("language", language_menu))
        application.add_handler(CallbackQueryHandler(set_language, pattern=r"^setlang\|"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        await application.initialize()
        await application.start()
        webhook_url = f"https://{PUBLIC_URL.rstrip('/')}/webhook"
        logger.info(f"Setting up webhook: {webhook_url}")
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=TELEGRAM_WEBHOOK_SECRET,
            drop_pending_updates=True
        )
        logger.info("Webhook configured successfully")

    event_loop.run_until_complete(init_application())
    try:
        event_loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Background event loop stopped")
    finally:
        event_loop.close()

def start_telegram_bot():
    global background_thread
    background_thread = threading.Thread(target=run_event_loop, daemon=True)
    background_thread.start()
    import time
    time.sleep(2)
    logger.info("Telegram bot started in background")

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "bot_running": application is not None}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        secret_header = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
        if secret_header != TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret received")
            return jsonify({"error": "Invalid secret"}), 403
        json_data = request.get_json()
        if not json_data:
            return jsonify({"error": "No JSON data"}), 400
        if application and event_loop:
            update = Update.de_json(json_data, application.bot)
            if update:
                asyncio.run_coroutine_threadsafe(
                    application.process_update(update),
                    event_loop
                )
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    start_telegram_bot()
    logger.info(f"🤖 Bot starting on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
