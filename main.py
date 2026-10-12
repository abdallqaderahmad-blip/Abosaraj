import os
import requests
import json
from flask import Flask, request
from groq import Groq

# قراءة التوكنات بدون مسافات
TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

flask_app = Flask(__name__)
groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

def tele_api():
    return f"https://api.telegram.org/bot{TOKEN}"

def send_msg(chat_id, text):
    try:
        requests.post(f"{tele_api()}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print(e)

def send_photo(chat_id, path, caption=""):
    try:
        with open(path, 'rb') as f:
            requests.post(f"{tele_api()}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=80)
    except Exception as e:
        print(e)

def generate_hikma():
    fallback = {
        "ar_top": "لا تنتظر ما في أيدي الناس",
        "ar_bottom": "فما كتبه الله لك سيأتيك",
        "en": "Don't wait for what is in people's hands",
        "scene_top": "cute yellow blob character sitting alone sad looking at empty hands of crowd, simple doodle",
        "scene_bottom": "same yellow blob character happy, small gift box falling from sky",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"
    }
    if not groq_client:
        return fallback
    try:
        r = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role":"user","content":'Write JSON only: {"ar_top":"سطر عربي قصير","ar_bottom":"سطر ثاني","en":"english short","scene_top":"top scene yellow blob sad","scene_bottom":"bottom scene yellow blob happy","full":"الحكمة كاملة"}'}],
            response_format={"type":"json_object"}
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(e)
        return fallback

def gen_img(desc):
    prompt = f"hmisc style, cozy minimalist illustration, cute little yellow blob character with thin black outline and dot eyes, hand drawn watercolor on textured beige paper, {desc}"
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1280&model=turbo&nologo=true&enhance=true&seed={abs(hash(desc)) % 100000}"
    try:
        res = requests.get(url, timeout=60)
        if res.status_code!= 200:
            return None
        p = f"/tmp/{abs(hash(desc))}.jpg"
        with open(p, "wb") as f:
            f.write(res.content)
        return p
    except Exception as e:
        print(e)
        return None

@flask_app.route('/')
def home():
    if not TOKEN:
        return "❌ BOT_TOKEN غير موجود - روح Render > Environment وضيفه", 500
    return f"✅ Live - TOKEN: {TOKEN[:10]}... - افتح /setwebhook"

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN:
        return "❌ BOT_TOKEN missing in Render Environment", 500
    host = request.host_url.replace("http://", "https://")
    full_url = f"{host}{TOKEN}"
    r = requests.get(f"{tele_api()}/setWebhook?url={full_url}", timeout=15)
    return f"Webhook URL: {full_url}<br><br>Telegram: {r.text}<br><br>الان روح ابعت /start", 200

# هذا السطر يصلح مشكلة Exited with status 1
@flask_app.route('/<token>', methods=['POST'])
def webhook_handler(token):
    # نتحقق التوكن صحيح
    if token!= TOKEN:
        return "ok", 200
    try:
        data = request.get_json(force=True)
        if not data or "message" not in data:
            return "ok", 200

        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if "/start" in text:
            send_msg(chat_id, "🎨 Hmisc Factory شغال ✅\n\n/hikma - يصنع حكمة\n/idea - فكرة نصية")

        elif "/idea" in text:
            d = generate_hikma()
            send_msg(chat_id, f"{d['full']}\n\n{d['en']}")

        elif "/hikma" in text:
            send_msg(chat_id, "⏳ بصنع الفكرة... 20 ثانية")
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\n\n🎨 بصنع الصور...")
            p1 = gen_img(d['scene_top'])
            p2 = gen_img(d['scene_bottom'])
            if p1: send_photo(chat_id, p1, d['ar_top'])
            if p2: send_photo(chat_id, p2, d['ar_bottom'])
            if not p1 and not p2:
                send_msg(chat_id, "❌ فشل الصور جرب /hikma تاني")

    except Exception as e:
        print(f"Error: {e}")

    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting on port {port} - TOKEN set: {bool(TOKEN)}")
    flask_app.run(host='0.0.0.0', port=port)
