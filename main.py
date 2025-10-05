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

# Validate webhook secret format
if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)

# ——— In-memory user prefs ———
# chat_id → language code (e.g. "fa","es","de","it")
USER_LANGUAGE = {}
SUPPORTED_LANGUAGES = {
    "fa": ("🇮🇷 Persian (Farsi)", "Persian"),
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

def send_message(chat_id: int, text: str, reply_markup: dict = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
    if not r.ok:
        logger.error(f"sendMessage error {r.status_code}: {r.text}")

def answer_callback(query_id: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
        json={"callback_query_id": query_id}
    )

# ——— Translation ———

def translate_text(text: str) -> str:
    """
    Build a strict, dynamic system prompt based on USER_LANGUAGE and source language.
    """
    # Extract language codes
    # Note: In webhook we pass src_lang and chat_id globally for prompt
    # but here we reconstruct inside webhook itself.
    raise RuntimeError("translate_text should not be called directly")

# ——— Webhook Endpoint ———

@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) verify secret
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token","") != WEBHOOK_SECRET:
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Update: {update}")

    # 2) handle callback_query for language selection
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

    # 3) handle normal messages
    msg = update.get("message",{})
    text = msg.get("text","")
    chat = msg.get("chat",{})
    chat_id = chat.get("id")
    if not text or not chat_id:
        return jsonify({"status":"ignored"}), 200

    # 4) /language command → show inline keyboard
    if text.strip().lower().startswith("/language"):
        keyboard = []
        row = []
        for code,(label,_) in SUPPORTED_LANGUAGES.items():
            row.append({"text": label, "callback_data": f"lang|{code}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        reply_markup = {"inline_keyboard": keyboard}
        send_message(chat_id, "Please select a language:", reply_markup)
        return jsonify({"status":"ok"}), 200

    # 5) translation flow
    src_lang = msg.get("from",{}).get("language_code","en").lower()

    # Determine prompt direction
    if src_lang.startswith("en"):
        code = USER_LANGUAGE.get(chat_id, "fa")
        lang_name = SUPPORTED_LANGUAGES.get(code, ("", "Farsi"))[1]
        direction = f"When a user sends a message in English, translate it into {lang_name}."
    else:
        direction = "When a user sends a message in any language other than English, translate it into fluent, understandable English."

    # Build strict system prompt
    system_prompt = f"""
You are a world-class translator.

{direction}

Always ensure the translations are natural, culturally adapted, and not word-for-word.

Never add any explanations or extra comments—only return the translated text.
""".strip()

    # Call OpenAI v1.x API
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

    send_message(chat_id, translation)
    return jsonify({"status":"ok"}), 200

# ——— Health Endpoint ———

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
