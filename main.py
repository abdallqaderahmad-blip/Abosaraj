import os, requests, json, random, time
from flask import Flask, request

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
REPLICATE_KEY = (os.getenv("REPLICATE_API_TOKEN") or "").strip()

flask_app = Flask(__name__)

groq_client = None
if GROQ_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_KEY)
    except: pass

def tele_api(): return f"https://api.telegram.org/bot{TOKEN}"
def send_msg(c,t):
    try: requests.post(f"{tele_api()}/sendMessage", json={"chat_id":c,"text":t}, timeout=20)
    except: pass
def send_photo(c,p,cap=""):
    try:
        with open(p,'rb') as f: requests.post(f"{tele_api()}/sendPhoto", data={"chat_id":c,"caption":cap}, files={"photo":f}, timeout=120)
    except Exception as e: print(e)

def generate_hikma():
    fb = {"hikma":"لا تصنع هموماً لم يكتبها الله عليك","full":"لا تنتظر ما في أيدي الناس، فالله أعلم بما يصلح لك، وما أخره عنك لحكمة، وما كتبه لك سيأتيك، فاطمئن، إن رزقك لن يخطئ طريقه إليك"}
    if not groq_client: return fb
    try:
        r=groq_client.chat.completions.create(model="llama3-70b-8192", messages=[{"role":"user","content":'JSON only {"hikma":"حكمة قصيرة 7 كلمات","full":"نص 30 كلمة"}'}], response_format={"type":"json_object"})
        j=json.loads(r.choices[0].message.content)
        return {"hikma":j.get("hikma",fb["hikma"]), "full":j.get("full",fb["full"])}
    except: return fb

def gen_with_replicate(hikma_text):
    if not REPLICATE_KEY:
        return None, "❌ ضيف REPLICATE_API_TOKEN في Render Env"

    # Prompt السحري اللي بيجيب hmisc بالضبط
    prompt = f"""
    hmisc style exact, two panel vertical comic, vintage beige paper texture watercolor,
    small yellow bean character with thin black stick limbs, crosshatch ink shading,
    top: yellow character drowning hands up water ripples panic,
    bottom: same character sitting calm on sandy seabed underwater bubbles seaweed,
    Arabic text at top '{hikma_text}',
    masterpiece, soft watercolor, ink outline, detailed, cozy melancholic
    """

    try:
        # نطلب من replicate
        resp = requests.post(
            "https://api.replicate.com/v1/predictions",
            headers={"Authorization": f"Token {REPLICATE_KEY}", "Content-Type":"application/json"},
            json={
                "version": "black-forest-labs/flux-1.1-pro",
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": "4:5",
                    "output_format": "jpg",
                    "safety_tolerance": 2
                }
            },
            timeout=20
        )
        data = resp.json()
        get_url = data["urls"]["get"]

        # ننتظر النتيجة
        for _ in range(30):
            time.sleep(2)
            r = requests.get(get_url, headers={"Authorization": f"Token {REPLICATE_KEY}"}, timeout=10).json()
            if r["status"] == "succeeded":
                img_url = r["output"]
                if isinstance(img_url, list): img_url = img_url[0]
                img_data = requests.get(img_url, timeout=60).content
                path = f"/tmp/{random.randint(1,999999)}.jpg"
                with open(path,"wb") as f: f.write(img_data)
                return path, None
            if r["status"] == "failed":
                return None, f"Replicate failed: {r.get('error')}"
        return None, "Timeout"
    except Exception as e:
        return None, str(e)

@flask_app.route('/')
def home(): return "✅ Hmisc Pro - Replicate"

@flask_app.route('/setwebhook')
def setwebhook():
    host=request.host_url.replace("http://","https://")
    full=f"{host}{TOKEN}"
    r=requests.get(f"{tele_api()}/setWebhook?url={full}", timeout=15)
    return f"{full}<br>{r.text}"

@flask_app.route('/<token>', methods=['POST'])
def webhook(token):
    if token!=TOKEN: return "ok",200
    try:
        data=request.get_json(force=True)
        if not data or "message" not in data: return "ok",200
        chat_id=data["message"]["chat"]["id"]
        text=data["message"].get("text","")

        if "/start" in text:
            send_msg(chat_id,"🎨 Hmisc Pro شغال ✅\n/hikma - ستايل أصلي 100%")

        elif "/hikma" in text:
            if not REPLICATE_KEY:
                send_msg(chat_id,"❌ لازم تضيف REPLICATE_API_TOKEN في Render > Environment\nروح replicate.com/account/api-tokens واعمل توكن")
                return "ok",200

            send_msg(chat_id,"⏳ بصنع بستايل hmisc الأصلي... 20 ثانية ⏳")
            d=generate_hikma()
            path, err = gen_with_replicate(d["hikma"])
            if path:
                send_photo(chat_id, path, f"{d['hikma']}\n\n{d['full']}\n\n#hmisc")
            else:
                send_msg(chat_id,f"❌ {err}")
    except Exception as e: print(e)
    return "ok",200

if __name__=="__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000)))
