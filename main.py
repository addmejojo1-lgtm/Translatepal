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

# Validate secret token format
if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)

# ——— In‐memory user prefs ———
# chat_id → language code (e.g. "fa","es","de")
USER_LANGUAGE = {}
# Supported options:
SUPPORTED_LANGUAGES = {
    "fa": ("🇮🇷 Persian (Farsi)","fa"),
    "es": ("🇪🇸 Spanish","es"),
    "fr": ("🇫🇷 French","fr"),
    "de": ("🇩🇪 German","de"),
    "it": ("🇮🇹 Italian","it"),
    "tr": ("🇹🇷 Turkish","tr"),
    "ru": ("🇷🇺 Russian","ru"),
    "ar": ("🇸🇦 Arabic","ar"),
    "zh": ("🇨🇳 Chinese","zh"),
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

def translate_text(text: str, src_lang: str, target_lang: str) -> str:
    # Strict system prompt as you specified
    system_prompt = """
You are a world-class translator.

When a user sends a message in any language other than English, translate it into fluent, understandable English.

When a user sends a message in English, translate it into the user’s selected language, or the most-recently used language if they’ve chosen one.

Always ensure the translations are natural, culturally adapted, and not word-for-word.

Never add any explanations or extra comments—only return the translated text.
""".strip()
    # Build messages
    messages = [
        {"role":"system","content":system_prompt},
        {"role":"user","content":text}
    ]
    resp = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    return resp.choices[0].message.content.strip()

# ——— Webhook ———

@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) verify secret
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token","") != WEBHOOK_SECRET:
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Update: {update}")

    # 2) handle callback_query (button presses)
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data","")
        chat_id = cq["message"]["chat"]["id"]
        # expected format: "lang|<code>"
        if data.startswith("lang|"):
            code = data.split("|",1)[1]
            if code in SUPPORTED_LANGUAGES:
                USER_LANGUAGE[chat_id] = code
                send_message(chat_id, f"Language set to {SUPPORTED_LANGUAGES[code][0]}")
            else:
                send_message(chat_id, "Unknown language selection.")
        answer_callback(cq["id"])
        return jsonify({"status":"ok"}), 200

    # 3) handle normal messages
    msg = update.get("message",{})
    text = msg.get("text","")
    chat_id = msg.get("chat",{}).get("id")
    if not text or not chat_id:
        return jsonify({"status":"ignored"}), 200

    # 4) /language command: show inline menu
    if text.strip().lower().startswith("/language"):
        # build inline keyboard
        keyboard = []
        row = []
        for code,(label,_) in SUPPORTED_LANGUAGES.items():
            row.append({"text": label, "callback_data": f"lang|{code}"})
            if len(row)==2:
                keyboard.append(row); row=[]
        if row: keyboard.append(row)
        reply_markup = {"inline_keyboard": keyboard}
        send_message(chat_id, "Please select a language:", reply_markup)
        return jsonify({"status":"ok"}), 200

    # 5) translation flow
    src_lang = msg.get("from",{}).get("language_code","en").lower()
    # determine target
    if src_lang.startswith("en"):
        target = USER_LANGUAGE.get(chat_id, "fa")
    else:
        target = "en"
    try:
        result = translate_text(text, src_lang, target)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        result = "❌ Sorry, I couldn’t translate that."

    send_message(chat_id, result)
    return jsonify({"status":"ok"}), 200

# ——— Health ———
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
