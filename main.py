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
BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET    = os.environ["TELEGRAM_WEBHOOK_SECRET"]
RENDER_DOMAIN     = os.environ["REPLIT_DOMAINS"]
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
PORT              = int(os.getenv("PORT", 10000))

if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)

# ——— Persistence Layer ———
PREFS_FILE = "user_language.json"
if os.path.exists(PREFS_FILE):
    with open(PREFS_FILE, "r") as f:
        USER_LANGUAGE = json.load(f)
else:
    USER_LANGUAGE = {}

def save_prefs():
    with open(PREFS_FILE, "w") as f:
        json.dump(USER_LANGUAGE, f)

# ——— Language Configuration ———
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
        logger.error(f"sendMessage error {r.status_code}: {r.text}")

def answer_callback(query_id):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
        json={"callback_query_id": query_id}
    )

def is_english(text: str) -> bool:
    try:
        return detect(text) == "en"
    except Exception:
        return bool(re.fullmatch(r"[A-Za-z0-9\s\.\,\!\?\'\"\-\(\)]+", text))

# ——— Webhook Endpoint ———
@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token","") != WEBHOOK_SECRET:
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Incoming update: {update}")

    # Language-selection callback
    if "callback_query" in update:
        cq = update["callback_query"]
        code = cq["data"].split("|",1)[1]
        chat_id = cq["message"]["chat"]["id"]
        if code in SUPPORTED_LANGUAGES:
            USER_LANGUAGE[str(chat_id)] = code
            save_prefs()
            send_message(chat_id, f"Language set to {SUPPORTED_LANGUAGES[code][0]}")
        else:
            send_message(chat_id, "Unknown language.")
        answer_callback(cq["id"])
        return jsonify({"status":"ok"}), 200

    # Normal message
    msg = update.get("message",{})
    text = msg.get("text","")
    chat_id = msg.get("chat",{}).get("id")
    if not text or not chat_id:
        return jsonify({"status":"ignored"}), 200

    # /language command: show menu
    if text.strip().lower().startswith("/language"):
        keyboard, row = [], []
        for code,(label,_) in SUPPORTED_LANGUAGES.items():
            row.append({"text": label, "callback_data": f"lang|{code}"})
            if len(row)==2:
                keyboard.append(row); row=[]
        if row: keyboard.append(row)
        send_message(chat_id, "Please select a language:", {"inline_keyboard": keyboard})
        return jsonify({"status":"ok"}), 200

    # Decide direction
    if is_english(text):
        prefs = USER_LANGUAGE.get(str(chat_id))
        if not prefs:
            send_message(
                chat_id,
                "❗ Please send me a non-English message first, or use /language to select one."
            )
            return jsonify({"status":"ok"}), 200
        target_name = SUPPORTED_LANGUAGES[prefs][1]
        direction = f"When a user sends a message in English, translate it into {target_name}."
    else:
        direction = ("When a user sends a message in any language other than English, "
                     "translate it into fluent, understandable English.")
        # Auto-select this language for future
        try:
            detected = detect(text)
            if detected in SUPPORTED_LANGUAGES:
                USER_LANGUAGE[str(chat_id)] = detected
                save_prefs()
        except:
            pass

    # Build prompt
    system_prompt = f"""
You are a world-class translator.

{direction}

Always ensure the translations are natural, culturally adapted, and not word-for-word.

Never add any explanations or extra comments—only return the translated text.
""".strip()

    # Call OpenAI
    try:
        resp = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system",  "content": system_prompt},
                {"role":"user",    "content": text}
            ]
        )
        translation = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        translation = "❌ Sorry, I couldn’t translate that."

    send_message(chat_id, translation)
    return jsonify({"status":"ok"}), 200

# ——— Health Check ———
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
