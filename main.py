import os
import re
import sys
import json
import logging

import requests
import openai
from flask import Flask, request, jsonify
from langdetect import detect, DetectorFactory

# ——— langdetect seed for consistency ———
DetectorFactory.seed = 0

# ——— Logging ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ——— Environment Variables ———
BOT_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET  = os.environ["TELEGRAM_WEBHOOK_SECRET"]
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
PORT            = int(os.getenv("PORT", 10000))

# Validate secret format
if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET")

openai.api_key = OPENAI_API_KEY
app = Flask(__name__)

# ——— Persistence Layer ———
PREFS_FILE = "user_language.json"
if os.path.exists(PREFS_FILE):
    with open(PREFS_FILE, "r") as f:
        USER_LANGUAGE = json.load(f)
else:
    USER_LANGUAGE = {}  # chat_id (str) → language code

def save_prefs():
    with open(PREFS_FILE, "w") as f:
        json.dump(USER_LANGUAGE, f)

# ——— Supported Languages ———
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
    logger.info(f"send_message → {chat_id}: {text}")
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload
    )
    if not r.ok:
        logger.error(f"sendMessage error {r.status_code}: {r.text}")

def answer_callback_query(callback_query_id: str, text: str):
    """Show a Telegram toast banner for callback queries."""
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
        json={
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False
        }
    )

def is_english(text: str) -> bool:
    try:
        return detect(text) == "en"
    except Exception:
        return False

# ——— Webhook ———
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) Verify secret header
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token","") != WEBHOOK_SECRET:
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Incoming update: {update}")

    # 2) Callback query → explicit /language selection
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data","")
        chat_id = str(cq["message"]["chat"]["id"])
        cq_id   = cq["id"]
        if data.startswith("lang|"):
            code = data.split("|",1)[1]
            if code in SUPPORTED_LANGUAGES:
                USER_LANGUAGE[chat_id] = code
                save_prefs()
                label = SUPPORTED_LANGUAGES[code][0]
                answer_callback_query(cq_id, f"Selected: {label}")
            else:
                answer_callback_query(cq_id, "Unknown language.")
        return jsonify({"status":"ok"}), 200

    # 3) Normal messages
    msg = update.get("message",{})
    text = msg.get("text","")
    chat = msg.get("chat",{})
    chat_id = str(chat.get("id",""))
    if not text or not chat_id:
        return jsonify({"status":"ignored"}), 200

    # 4) /language command → show menu
    if text.strip().lower().startswith("/language"):
        keyboard, row = [], []
        for code,(label,_) in SUPPORTED_LANGUAGES.items():
            row.append({"text":label,"callback_data":f"lang|{code}"})
            if len(row)==2:
                keyboard.append(row)
                row=[]
        if row: keyboard.append(row)
        send_message(chat_id, "Please select a language:", {"inline_keyboard":keyboard})
        return jsonify({"status":"ok"}), 200

    # 5) Decide direction
    english = is_english(text)
    logger.info(f"is_english={english} text={text!r}")

    if english:
        # English → user’s selected language
        if chat_id not in USER_LANGUAGE:
            send_message(chat_id,
                "❗ Please send me a non-English message first, or use /language to choose a language."
            )
            return jsonify({"status":"ok"}), 200
        target_code = USER_LANGUAGE[chat_id]
        target_name = SUPPORTED_LANGUAGES[target_code][1]
        direction = f"When a user sends a message in English, translate it into {target_name}."
    else:
        # Non-English → English
        direction = (
            "When a user sends a message in any language other than English, "
            "translate it into fluent, understandable English."
        )
        # Auto-switch user’s language for future English→X
        try:
            detected = detect(text)
            if detected in SUPPORTED_LANGUAGES:
                USER_LANGUAGE[chat_id] = detected
                save_prefs()
                label = SUPPORTED_LANGUAGES[detected][0]
                send_message(chat_id, f"🔄 Language auto-switched to {label}")
        except Exception:
            pass

    # 6) Strict system prompt
    system_prompt = f"""
You are a world-class translator.

{direction}

Always produce a translation. Never return the original text, even a single word.
Ensure translations are natural, culturally adapted, and never word-for-word.
Never add explanations or extra comments—only return the translated text.
""".strip()
    logger.info(f"System prompt: {system_prompt}")

    # 7) Call OpenAI
    try:
        resp = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user","content":text}
            ]
        )
        translation = resp.choices[0].message.content.strip()
        logger.info(f"OpenAI→ {translation}")
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        translation = "❌ Sorry, I couldn’t translate that."

    # 8) Send back translation
    send_message(chat_id, translation)
    return jsonify({"status":"ok"}), 200

# Health check
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
