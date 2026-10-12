import os
import requests
import json
import time
from flask import Flask, request

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

flask_app = Flask(__name__)

groq_client = None
if GROQ_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_KEY)
        print("✅ Groq OK")
    except Exception as e:
        print(f"⚠️ Groq fail: {e}")
        groq_client = None

def tele_api():
    return f"https://api.telegram.org/bot{TOKEN}"

def send_msg(chat_id, text):
    try:
        requests.post(f"{tele_api()}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except: pass

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
        "scene_top": "small yellow blob character sitting alone sad, crowd empty hands, minimal doodle",
        "scene_bottom": "same yellow blob character looking at sky peaceful, gift from sky",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"
    }
    if not groq_client:
        return fallback
    try:
        r = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role":"user","content":'Write JSON only: {"ar_top":"سطر عربي قصير 5 كلمات","ar_bottom":"سطر ثاني 5 كلمات","en":"english short","scene_top":"top scene description simple yellow blob sad","scene_bottom":"bottom scene description same yellow blob happy","full":"الحكمة كاملة عربية"}'}],
            response_format={"type":"json_object"}
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(e)
        return fallback

def gen_img(desc):
    # ستايل Hmisc الحقيقي المسطح 2D - مو 3D
    prompt = f"hmisc illustration style, flat 2d doodle, cute simple yellow blob character with tiny black dot eyes and thin black outline, hand drawn childish doodle, soft watercolor flat colors, textured off-white paper background, minimal cozy story, NOT 3d, NOT clay, NOT realistic, NOT shiny, NOT plastic, {desc}, by hmisc"
    # flux يعطي رسم مسطح - turbo يعطي 3d
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1280&model=flux&nologo=true&seed={abs(hash(desc)) % 999999}"
    try:
        print(f"Generating: {desc[:50]}")
        res = requests.get(url, timeout=90)
        if res.status_code!= 200:
            print(f"Image failed {res.status_code}")
            return None
        p = f"/tmp/{abs(hash(desc))}.jpg"
        with open(p, "wb") as f:
            f.write(res.content)
        return p
    except Exception as e:
        print(f"gen_img error: {e}")
        return None

@flask_app.route('/')
def home():
    if not TOKEN: return "❌ BOT_TOKEN missing", 500
    return f"✅ Live - Groq: {bool(groq_client)} - /setwebhook"

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN: return "❌ No token", 500
    host = request.host_url.replace("http://", "https://")
    full_url = f"{host}{TOKEN}"
    r = requests.get(f"{tele_api()}/setWebhook?url={full_url}", timeout=15)
    return f"Webhook: {full_url}<br>Result: {r.text}", 200

@flask_app.route('/<token>', methods=['POST'])
def webhook_handler(token):
    if token!= TOKEN: return "ok", 200
    try:
        data = request.get_json(force=True)
        if not data or "message" not in data: return "ok", 200
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if "/start" in text:
            send_msg(chat_id, "🎨 Hmisc Factory شغال ✅\n\n/hikma - يصنع حكمة بصورتين\n/idea - فكرة نصية")

        elif "/idea" in text:
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\n\n{d['en']}")

        elif "/hikma" in text:
            send_msg(chat_id, "⏳ بصنع الفكرة... 20 ثانية ⏳")
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\nEN: {d['en']}\n\n🎨 بصنع الصورتين ستايل hmisc الأصلي...")

            p1 = gen_img(d['scene_top'])
            if p1:
                send_photo(chat_id, p1, d['ar_top'])
                time.sleep(2)

            p2 = gen_img(d['scene_bottom'])
            if p2:
                send_photo(chat_id, p2, f"{d['ar_bottom']}\n\n{d['en']}\n\n#حكمة")

            if not p1 and not p2:
                send_msg(chat_id, "❌ فشل الصور جرب /hikma تاني")

    except Exception as e:
        print(f"Error: {e}")
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
