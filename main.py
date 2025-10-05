import os
import re
import sys
import logging

import requests
import openai
from flask import Flask, request, jsonify

# ——— Logging ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ——— Env Vars ———
BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET    = os.environ["TELEGRAM_WEBHOOK_SECRET"]
RENDER_DOMAIN     = os.environ["REPLIT_DOMAINS"]       # e.g. translatepal.onrender.com
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
PORT              = int(os.getenv("PORT", 10000))

if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY

# ——— In‐memory user language state ———
# Maps chat_id -> ISO code or language name, e.g. "es", "fa", "French", etc.
USER_LANGUAGE = {}

app = Flask(__name__)

# ——— Endpoint to set/change a user’s target language ———
@app.route("/set_language", methods=["POST"])
def set_language():
    j = request.get_json(force=True)
    chat_id = j.get("chat_id")
    lang    = j.get("language")
    if not chat_id or not lang:
        return jsonify({"error":"chat_id and language required"}), 400
    USER_LANGUAGE[int(chat_id)] = lang
    return jsonify({"status":"ok"}), 200

# ——— Core Webhook ———
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) Verify Telegram secret header
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token","")
    if token != WEBHOOK_SECRET:
        logger.warning("Invalid webhook secret")
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Incoming update: {update}")

    msg = update.get("message") or update.get("edited_message")
    if not msg or "text" not in msg:
        return jsonify({"status":"ignored"}), 200

    chat_id       = msg["chat"]["id"]
    text          = msg["text"]
    src_lang_code = msg.get("from", {}).get("language_code","en").lower()

    # 2) Determine target language
    # If user previously set a language, use that. Otherwise default to English or Farsi.
    if src_lang_code == "en":
        target = USER_LANGUAGE.get(chat_id, "fa")   # default to Farsi if none chosen
    else:
        target = "English"

    # 3) Build the strict system prompt exactly as you specified
    system_prompt = """
You are a world-class translator.

When a user sends a message in any language other than English, translate it into fluent, understandable English.

When a user sends a message in English, translate it into the user’s selected language, or the most-recently used language if they’ve chosen one.

Always ensure the translations are natural, culturally adapted, and not word-for-word.

Never add any explanations or extra comments—only return the translated text.
""".strip()

    # 4) Call OpenAI with correct v1.x API
    try:
        chat = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system", "content": system_prompt},
                {"role":"user",   "content": text}
            ]
        )
        translation = chat.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        translation = "❌ Sorry, I couldn’t translate that."

    # 5) Reply to Telegram
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id":chat_id, "text":translation}
    )
    if not resp.ok:
        logger.error(f"sendMessage failed: {resp.status_code} {resp.text}")
    else:
        logger.info(f"Replied to {chat_id}")

    return jsonify({"status":"ok"}), 200

# ——— Health Check ———
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__ == "__main__":
    # Local testing
    app.run(host="0.0.0.0", port=PORT, debug=False)
