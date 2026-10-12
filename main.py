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
    try:
        requests.post(f"{TELE_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
    except: pass

def send_photo(chat_id, photo_path, caption=""):
    try:
        with open(photo_path, 'rb') as f:
            requests.post(f"{TELE_API}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=70)
    except Exception as e:
        print(f"send_photo error: {e}")

def generate_hikma():
    default = {
        "ar_top": "لا تنتظر ما في أيدي الناس",
        "ar_bottom": "فما كتبه الله لك سيأتيك",
        "en": "Don't wait for what is in people's hands",
        "scene_top": "a small cute yellow blob character sitting alone looking at empty hands of crowd, sad, simple doodle",
        "scene_bottom": "same small yellow blob character happy, a small light gift box falling from sky to him",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"
    }
    if not groq_client:
        return default
    try:
        res = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": 'You are Hmisc writer. Write short wisdom JSON only: {"ar_top":"سطر عربي قصير 5 كلمات","ar_bottom":"سطر ثاني 5 كلمات","en":"english translation short","scene_top":"english description for top image, hmisc yellow blob sad","scene_bottom":"english description for bottom image, hmisc yellow blob peaceful happy","full":"الحكمة كاملة"}'}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"Groq error: {e}")
        return default

def gen_img(desc):
    # ستايل Hmisc الحقيقي
    base_prompt = f"hmisc art style, minimalist cozy illustration, cute little yellow blob character with simple black dots eyes, hand drawn doodle with thin black outline, soft flat watercolor, textured off-white vintage paper background, emotional storytelling, {desc}"
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(base_prompt)}?width=1024&height=1280&model=turbo&nologo=true&enhance=true&seed={abs(hash(desc)) % 999999}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code!= 200:
            print(f"Image fail {r.status_code}")
            return None
        path = f"/tmp/{abs(hash(desc))}.jpg"
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    except Exception as e:
        print(f"gen_img error: {e}")
        return None

@flask_app.route('/')
def home():
    return "Hmisc Factory Live ✅"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if "message" not in data:
            return "ok"
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            send_msg(chat_id, "🎨 Hmisc Factory شغال ✅\n\n/hikma - يصنع حكمة بصورتين\n/idea - فكرة نصية فقط")

        elif text.startswith("/idea"):
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\n\nEN: {d['en']}")

        elif text.startswith("/hikma"):
            send_msg(chat_id, "⏳ بصنع الفكرة... 20 ثانية ⏳")
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\nEN: {d['en']}\n\n🎨 بصنع الصور...")

            p1 = gen_img(d['scene_top'])
            p2 = gen_img(d['scene_bottom'])

            if p1:
                send_photo(chat_id, p1, d['ar_top'])
            if p2:
                send_photo(chat_id, p2, f"{d['ar_bottom']}\n\n{d['en']}\n\n#حكمة")

            if not p1 and not p2:
                send_msg(chat_id, "❌ فشل الصور، جرب /hikma مرة ثانية")

        else:
            send_msg(chat_id, "ابعت /hikma")

    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    host = request.host_url.replace("http://", "https://")
    url = f"{host}{TOKEN}".rstrip('/')
    r = requests.get(f"{TELE_API}/setWebhook?url={url}", timeout=15)
    return f"✅ Webhook set<br>URL: {url}<br><br>Result: {r.text}", 200

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
