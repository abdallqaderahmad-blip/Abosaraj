import os
import requests
import json
import time
import random
from flask import Flask, request

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

flask_app = Flask(__name__)

groq_client = None
if GROQ_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_KEY)
    except: groq_client = None

def tele_api(): return f"https://api.telegram.org/bot{TOKEN}"

def send_msg(chat_id, text):
    try: requests.post(f"{tele_api()}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except: pass

def send_photo(chat_id, path, caption=""):
    try:
        with open(path, 'rb') as f:
            requests.post(f"{tele_api()}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=80)
    except Exception as e: print(e)

def generate_hikma():
    fallback = {
        "ar_top": "لا تنتظر ما في أيدي الناس",
        "ar_bottom": "فما كتبه الله لك سيأتيك",
        "en": "Don't wait for what is in people's hands",
        "scene_top": "small yellow blob character sitting alone sad, crowd empty hands",
        "scene_bottom": "same yellow blob character happy looking at sky",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"
    }
    if not groq_client: return fallback
    try:
        r = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role":"user","content":'JSON only: {"ar_top":"سطر قصير","ar_bottom":"سطر ثاني","en":"english","scene_top":"yellow blob sad","scene_bottom":"yellow blob happy","full":"كاملة"}'}],
            response_format={"type":"json_object"}
        )
        return json.loads(r.choices[0].message.content)
    except: return fallback

def gen_img_robust(desc):
    # جرب 3 مرات مع موديلات مختلفة
    prompts = [
        f"hmisc style, flat 2d doodle, cute simple yellow blob character thin black outline dot eyes, soft watercolor paper texture, minimal cozy, NOT 3d, NOT realistic, {desc}",
        f"cute yellow blob character, hmisc art, simple hand drawn doodle, beige paper, {desc}",
        f"minimalist yellow blob, hmisc, watercolor illustration, {desc}"
    ]
    models = ["flux", "turbo", ""] # يجرب flux بعدين turbo بعدين default

    for attempt in range(3):
        for model in models:
            try:
                prompt = prompts[min(attempt,2)]
                # سيد عشوائي عشان ما يعلق
                seed = random.randint(1, 9999999)
                if model:
                    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1280&model={model}&nologo=true&seed={seed}"
                else:
                    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1280&nologo=true&seed={seed}"

                print(f"Try {attempt+1} model={model} - {desc[:30]}")
                res = requests.get(url, timeout=90)
                if res.status_code == 200 and len(res.content) > 10000:
                    p = f"/tmp/{seed}.jpg"
                    with open(p, "wb") as f: f.write(res.content)
                    print(f"✅ Image OK {len(res.content)} bytes")
                    return p
                else:
                    print(f"Fail status {res.status_code} len {len(res.content) if res.content else 0}")
            except Exception as e:
                print(f"Attempt {attempt} error: {e}")
                time.sleep(2)
        time.sleep(3)
    return None

@flask_app.route('/')
def home(): return f"✅ Live - /setwebhook"

@flask_app.route('/setwebhook')
def setwebhook():
    host = request.host_url.replace("http://", "https://")
    full_url = f"{host}{TOKEN}"
    r = requests.get(f"{tele_api()}/setWebhook?url={full_url}", timeout=15)
    return f"Webhook: {full_url}<br>{r.text}", 200

@flask_app.route('/<token>', methods=['POST'])
def webhook_handler(token):
    if token!= TOKEN: return "ok", 200
    try:
        data = request.get_json(force=True)
        if not data or "message" not in data: return "ok", 200
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if "/start" in text:
            send_msg(chat_id, "🎨 شغال ✅\n/hikma - حكمة")

        elif "/hikma" in text:
            send_msg(chat_id, "⏳ بصنع الفكرة... 20 ثانية ⏳")
            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['full']}\nEN: {d['en']}\n\n🎨 بصنع الصورة 1/2...")

            p1 = gen_img_robust(d['scene_top'])
            if p1:
                send_photo(chat_id, p1, d['ar_top'])
            else:
                send_msg(chat_id, "⚠️ الصورة 1 علقت، بعمل الثانية...")

            send_msg(chat_id, "🎨 بصنع الصورة 2/2...")
            p2 = gen_img_robust(d['scene_bottom'])
            if p2:
                send_photo(chat_id, p2, f"{d['ar_bottom']}\n\n{d['en']}")

            if not p1 and not p2:
                send_msg(chat_id, "❌ Pollinations معلق هلا، جرب /hikma بعد دقيقة - السيرفر مجاني بعلق احيانا")

    except Exception as e:
        print(e)
    return "ok", 200

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
