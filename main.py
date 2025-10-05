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

# ——— Environment Variables ———
BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET    = os.environ["TELEGRAM_WEBHOOK_SECRET"]
RENDER_DOMAIN     = os.environ["REPLIT_DOMAINS"]       # e.g. translatepal.onrender.com
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
PORT              = int(os.getenv("PORT", 10000))

if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY
app = Flask(__name__)

# ——— In-memory user prefs ———
# chat_id → language code (e.g. "es","de","it", etc.)
USER_LANGUAGE = {}

SUPPORTED_LANGUAGES = {
    "fa": ("🇮🇷 Persian (Farsi)", "Persian"),
    "pt": ("🇵🇹 Portuguese",      "Portuguese"),
    "es": ("🇪🇸 Spanish",         "Spanish"),
    "fr": ("🇫🇷 French",          "French"),
    "de": ("🇩🇪 German",          "German"),
    "it": ("🇮🇹 Italian",         "Italian"),
    "tr": ("🇹🇷 Turkish",         "Turkish"),
    "ru": ("🇷🇺 Russian",         "Russian"),
    "ar": ("🇸🇦 Arabic",          "Arabic"),
    "zh": ("🇨🇳 Chinese",         "Chinese"),
}

# ——— Helpers ———
def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
    if not r.ok:
        logger.error(f"sendMessage failed {r.status_code}: {r.text}")

def answer_callback(query_id):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
        json={"callback_query_id": query_id}
    )

# ——— Webhook ———
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) Verify Telegram secret header
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token","") != WEBHOOK_SECRET:
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Update: {update}")

    # 2) Handle callback_query for /language menu
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data","")
        chat_id = cq["message"]["chat"]["id"]
        if data.startswith("lang|"):
            code = data.split("|",1)[1]
            if code in SUPPORTED_LANGUAGES:
                USER_LANGUAGE[chat_id] = code
                label = SUPPORTED_LANGUAGES[code][0]
                send_message(chat_id, f"Language set to {label}")
            else:
                send_message(chat_id, "Unknown language.")
        answer_callback(cq["id"])
        return jsonify({"status":"ok"}), 200

    # 3) Handle normal messages
    msg = update.get("message",{})
    text = msg.get("text","")
    chat = msg.get("chat",{})
    chat_id = chat.get("id")
    if not text or not chat_id:
        return jsonify({"status":"ignored"}), 200

    # 4) /language command → show inline menu
    if text.strip().lower().startswith("/language"):
        keyboard, row = [], []
        for code,(label,_) in SUPPORTED_LANGUAGES.items():
            row.append({"text": label, "callback_data": f"lang|{code}"})
            if len(row)==2:
                keyboard.append(row); row=[]
        if row: keyboard.append(row)
        reply_markup = {"inline_keyboard": keyboard}
        send_message(chat_id, "Please select a language:", reply_markup)
        return jsonify({"status":"ok"}), 200

    # 5) Decide translation direction
    src_lang = msg.get("from",{}).get("language_code","en").lower()

    if src_lang.startswith("en"):
        # English→X: must have previously selected or auto-selected via last non-English
        if chat_id not in USER_LANGUAGE:
            send_message(chat_id,
                "❗ Please send me a message in your target language first, or use /language to select one.")
            return jsonify({"status":"ok"}), 200
        target_code = USER_LANGUAGE[chat_id]
        target_name = SUPPORTED_LANGUAGES[target_code][1]
        direction = f"When a user sends a message in English, translate it into {target_name}."
    else:
        # Non-English→English: always translate to English
        # AND auto-select this src_lang as future target
        direction = ("When a user sends a message in any language other than English, "
                     "translate it into fluent, understandable English.")
        # remember their language
        USER_LANGUAGE[chat_id] = src_lang[:2]  # e.g. "it", "es", "fa"

    system_prompt = f"""
You are a world-class translator.

{direction}

Always ensure the translations are natural, culturally adapted, and not word-for-word.

Never add any explanations or extra comments—only return the translated text.
""".strip()

    # 6) Call OpenAI
    try:
        resp = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system", "content": system_prompt},
                {"role":"user",   "content": text}
            ]
        )
        translation = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        translation = "❌ Sorry, I couldn’t translate that."

    # 7) Reply
    send_message(chat_id, translation)
    return jsonify({"status":"ok"}), 200

# ——— Health Check ———
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
