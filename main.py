import os, requests, json, time, random
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
        print(f"Groq fail: {e}")

def tele_api(): return f"https://api.telegram.org/bot{TOKEN}"

def send_msg(chat_id, text):
    try:
        requests.post(f"{tele_api()}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except: pass

def send_photo(chat_id, path, caption=""):
    try:
        with open(path, 'rb') as f:
            requests.post(f"{tele_api()}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=120)
    except Exception as e:
        print(f"send_photo error: {e}")

def generate_hikma():
    fallback = {
        "hikma": "لا تصنع هموماً لم يكتبها الله عليك",
        "full": "لا تنتظر ما في أيدي الناس، فالله أعلم بما يصلح لك، وما أخره عنك لحكمة، وما كتبه لك سيأتيك، فاطمئن، إن رزقك لن يخطئ طريقه إليك",
        "scene": "top: yellow bean character drowning with hands up above water surface, bottom: same character sitting peacefully on sandy seabed underwater, water bubbles, seaweed"
    }
    if not groq_client:
        return fallback
    try:
        r = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role":"user","content":'أنت كاتب حكم مثل hmisc. اكتب JSON فقط: {"hikma":"حكمة قصيرة مؤثرة 7 كلمات","full":"نص طويل مؤثر 30 كلمة عن نفس الحكمة","scene":"وصف مشهدين بالانجليزي للمشهد top و bottom مع شخصية صفراء"}'}],
            response_format={"type":"json_object"}
        )
        data = json.loads(r.choices[0].message.content)
        return {"hikma": data.get("hikma","لا تصنع هموما لم يكتبها الله عليك"), "full": data.get("full",""), "scene": data.get("scene","yellow character in water")}
    except Exception as e:
        print(e)
        return fallback

def gen_hmisc_exact(hikma_short, hikma_long, scene_desc):
    """
    هذا هو السحر - نفس ستايل hmisc بالضبط
    """
    prompt = f"""
    hmisc style exact replica, masterpiece,
    vintage textured beige paper with old stains and grain,
    two-panel vertical comic, watercolor and ink illustration,
    small yellow bean-shaped character with thin black stick arms and legs,
    yellow body with soft watercolor shading and crosshatch,
    top panel: {scene_desc} - drowning struggle,
    bottom panel: same yellow character sitting calm underwater on sand,
    Arabic calligraphy text at top: '{hikma_short}',
    highly detailed, crosshatch shading, ink outline, water ripples, bubbles, seaweed, sand texture,
    cozy melancholic deep meaning, children's book illustration by hmisc,
    NOT 3d, NOT clay, NOT plastic, NOT simple blob, NOT cartoon, watercolor painting
    """

    # نجرب 3 مرات
    for attempt in range(3):
        try:
            seed = random.randint(1000000, 9999999)
            # flux هو الوحيد اللي بيعرف يرسم hmisc
            encoded = requests.utils.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1350&model=flux&nologo=true&enhance=true&seed={seed}"

            print(f"🎨 Generating attempt {attempt+1} seed {seed}...")
            res = requests.get(url, timeout=90)

            print(f"Status {res.status_code} size {len(res.content)}")
            if res.status_code == 200 and len(res.content) > 20000:
                path = f"/tmp/hmisc_{seed}.jpg"
                with open(path, "wb") as f:
                    f.write(res.content)
                print(f"✅ Image saved {path}")
                return path
            else:
                print(f"❌ Failed attempt {attempt+1}")
                time.sleep(3)
        except Exception as e:
            print(f"Error attempt {attempt+1}: {e}")
            time.sleep(4)

    return None

@flask_app.route('/')
def home():
    return f"✅ Hmisc Factory - Flux Exact Style - Live"

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN: return "❌ No token", 500
    host = request.host_url.replace("http://", "https://")
    full_url = f"{host}{TOKEN}"
    r = requests.get(f"{tele_api()}/setWebhook?url={full_url}", timeout=15)
    return f"Webhook: {full_url}<br>Result: {r.text}"

@flask_app.route('/<token>', methods=['POST'])
def webhook(token):
    if token!= TOKEN:
        return "ok", 200
    try:
        data = request.get_json(force=True)
        if not data or "message" not in data:
            return "ok", 200

        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if "/start" in text:
            send_msg(chat_id, "🎨 Hmisc Factory الأصلي ✅\n\nستايل vintage مائي حقيقي\n\n/hikma - يصنع حكمة بلوحتين مثل الفيديو")

        elif "/hikma" in text or "/Hikma" in text:
            send_msg(chat_id, "⏳ بصنع حكمة hmisc... 30-40 ثانية ⏳\n(flux بطيء بس ستايل أصلي)")

            d = generate_hikma()
            send_msg(chat_id, f"💡 {d['hikma']}\n\n{d['full'][:200]}\n\n🎨 برسم اللوحة...")

            img_path = gen_hmisc_exact(d['hikma'], d['full'], d['scene'])

            if img_path:
                caption = f"{d['hikma']}\n\n{d['full']}\n\n#hmisc #حكمة"
                send_photo(chat_id, img_path, caption)
                send_msg(chat_id, "✅ جاهزة للنشر! اذا بدك واحدة ثانية /hikma")
            else:
                send_msg(chat_id, "❌ Flux معلق هلا (السيرفر المجاني بعلق احيانا)\nجرب /hikma بعد دقيقة")

    except Exception as e:
        print(f"Webhook error: {e}")

    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
