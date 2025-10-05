import os
import logging
import re
import requests
import openai
from flask import Flask, request, jsonify

# ——— Logging ———
import sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ——— Env Vars ———
BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET    = os.environ["TELEGRAM_WEBHOOK_SECRET"]
RENDER_DOMAIN     = os.environ["REPLIT_DOMAINS"]  # yourapp.onrender.com
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
DEFAULT_LANGUAGE  = "fa"

if not re.match(r'^[A-Za-z0-9_]{1,256}$', WEBHOOK_SECRET):
    raise ValueError("Invalid TELEGRAM_WEBHOOK_SECRET format")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)

# ——— Translation Logic ———
def translate_text(text: str, src_lang: str, target_lang: str) -> str:
    # Build the system prompt
    if src_lang.lower() == "en":
        prompt = f"You are a professional translator. Translate the following English into {target_lang} naturally:\n\n\"\"\"\n{text}\n\"\"\""
    else:
        prompt = f"You are a professional translator. Translate the following {src_lang} into English naturally:\n\n\"\"\"\n{text}\n\"\"\""
    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role":"system", "content": prompt}
        ]
    )
    return resp.choices[0].message.content.strip()

# ——— Webhook Endpoint ———
@app.route("/webhook", methods=["POST"])
def webhook():
    # 1) Verify secret header
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if token != WEBHOOK_SECRET:
        logger.warning("Invalid secret token")
        return jsonify({"error":"forbidden"}), 403

    update = request.get_json(force=True)
    logger.info(f"Incoming update: {update}")

    msg = update.get("message")
    if not msg or "text" not in msg:
        return jsonify({"status":"ignored"}), 200

    chat_id  = msg["chat"]["id"]
    text     = msg["text"]
    src_lang = msg["from"].get("language_code","en")

    # 2) Determine target
    if src_lang.lower() == "en":
        target = DEFAULT_LANGUAGE
    else:
        target = "English"

    # 3) Translate
    try:
        translation = translate_text(text, src_lang, target)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        translation = "❌ Translation error."

    # 4) Send back
    send_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": translation}
    r = requests.post(send_url, json=payload)
    logger.info(f"Replied with {r.status_code}: {translation[:30]!r}")
    return jsonify({"status":"ok"}), 200

# ——— Health Check ———
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200

if __name__ == "__main__":
    # Just for local debug; on Render, gunicorn runs it.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)))
