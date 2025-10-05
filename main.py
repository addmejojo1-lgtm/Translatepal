import os
from flask import Flask, request
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("DATA:", data)
    if 'message' in data and 'text' in data['message']:
        chat_id = data['message']['chat']['id']
        text = data['message']['text']
        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={'chat_id': chat_id, 'text': f'You said: {text}'}
        )
    return {"ok": True}

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)), debug=True)
