import os
import requests
import json
from flask import Flask, request
from groq import Groq

TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()

flask_app = Flask(__name__)
groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None
TELE_API = f"https://api.telegram.org/bot{TOKEN}"

def send_msg(chat_id, text):
    requests.post(f"{TELE_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)

def send_photo(chat_id, photo_path, caption=""):
    with open(photo_path, 'rb') as f:
        requests.post(f"{TELE_API}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=60)

def generate_hikma():
    default = {
        "ar_top": "لا تنتظر ما في أيدي الناس",
        "ar_bottom": "فما كتبه الله لك سيأتيك",
        "en": "Don't wait for what is in people's hands",
        "scene_top": "yellow blob looking at empty hands sad, hmisc style watercolor",
        "scene_bottom": "yellow blob happy gift falling from sky",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"
    }
    if not groq_client:
        return default
    try:
        res = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": 'اكتب حكمة JSON فقط: {"ar_top":"سطر1","ar_bottom":"سطر2","en":"english","scene_top":"scene top yellow blob","scene_bottom":"scene bottom peaceful","full":"كاملة"}'}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(e)
        return default

def gen_img(desc):
    base = "hmisc style, hand-drawn watercolor on beige vintage paper, cute yellow blob character, minimalist soft, "
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(base+desc)}?width=1080&height=1350&model=flux&nologo=true"
    try:
        r = requests.get(url, timeout=50)
        path = f"/tmp/{abs(hash(desc))}.jpg"
        with open(path, "wb") as f: f.write(r.content)
        return path
    except: return None

@flask_app.route('/')
def home():
    return "Live ✅"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        print("UPDATE:", json.dumps(data)[:500])
        if "message" not in data: return "ok"
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            send_msg(chat_id, "🎨 Hmisc Factory شغال ✅\n\n/hikma - يصنع حكمة\n/idea - فكرة نصية")

        elif text.startswith("/idea"):
            d = generate_hikma()
            send_msg(chat_id, f"{d['full']}\n\n{d['en']}")

        elif text.startswith("/hikma"):
            send_msg(chat_id, "⏳ بصنع الفكرة... 20 ثانية ⏳")
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\nEN: {d['en']}\n\nبصنع الصور...")
            p1 = gen_img(d['scene_top'])
            p2 = gen_img(d['scene_bottom'])
            if p1: send_photo(chat_id, p1, d['ar_top'])
            if p2: send_photo(chat_id, p2, f"{d['ar_bottom']}\n\n{d['en']}")
            else: send_msg(chat_id, "❌ فشل الصور جرب /hikma مرة تانية")

        else:
            send_msg(chat_id, "ابعت /hikma")

    except Exception as e:
        print(f"Error: {e}")
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    host = request.host_url.replace("http://", "https://")
    url = f"{host}{TOKEN}".rstrip('/')
    r = requests.get(f"{TELE_API}/setWebhook?url={url}", timeout=15)
    return f"Webhook: {url}<br><br>Result: {r.text}<br><br>الان ابعت /start في تيليجرام", 200

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
