import os, requests, json, time, random
from flask import Flask, request
from PIL import Image, ImageDraw

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
GROQ_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
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
        with open(p,'rb') as f: requests.post(f"{tele_api()}/sendPhoto", data={"chat_id":c,"caption":cap}, files={"photo":f}, timeout=80)
    except Exception as e: print(e)

def generate_hikma():
    fb={"ar_top":"لا تنتظر ما في أيدي الناس","ar_bottom":"فما كتبه الله لك سيأتيك","en":"Don't wait","scene_top":"sad","scene_bottom":"happy","full":"لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"}
    if not groq_client: return fb
    try:
        r=groq_client.chat.completions.create(model="llama3-70b-8192", messages=[{"role":"user","content":'JSON only {"ar_top":"سطر قصير","ar_bottom":"سطر ثاني","en":"english","scene_top":"sad","scene_bottom":"happy","full":"كاملة"}'}], response_format={"type":"json_object"})
        return json.loads(r.choices[0].message.content)
    except: return fb

# رسم محلي ستايل hmisc اذا Pollinations فشل
def gen_local_hmisc(mood="sad", seed=None):
    try:
        W,H = 1024,1280
        bg = (245, 239, 230) # ورق بيج
        img = Image.new("RGB", (W,H), bg)
        draw = ImageDraw.Draw(img)
        # texture ورق خفيف
        for _ in range(3000):
            x,y=random.randint(0,W), random.randint(0,H)
            draw.point((x,y), fill=(235,225,210))
        # جسم بلوب اصفر
        cx, cy = W//2, H//2 - 50
        bw, bh = 380, 480
        # ظل
        draw.ellipse([cx-bw//2+20, cy+bh//2-30, cx+bw//2+20, cy+bh//2+40], fill=(210,200,180))
        # جسم
        yellow = (255, 228, 115)
        draw.ellipse([cx-bw//2, cy-bh//2, cx+bw//2, cy+bh//2], fill=yellow, outline=(40,40,40), width=4)
        # اذنين صغار
        draw.ellipse([cx-bw//2+40, cy-bh//2-20, cx-bw//2+140, cy-bh//2+80], fill=yellow, outline=(40,40,40), width=4)
        draw.ellipse([cx+bw//2-140, cy-bh//2-20, cx+bw//2-40, cy-bh//2+80], fill=yellow, outline=(40,40,40), width=4)
        # عيون ووجه حسب المزاج
        if mood=="sad":
            # عيون نايمة حزينة
            draw.ellipse([cx-70, cy-20, cx-30, cy+10], fill=(40,40,40))
            draw.ellipse([cx+30, cy-20, cx+70, cy+10], fill=(40,40,40))
            # فم حزين
            draw.arc([cx-40, cy+30, cx+40, cy+80], 20, 160, fill=(40,40,40), width=4)
        else:
            # عيون سعيدة
            draw.ellipse([cx-80, cy-30, cx-40, cy+10], fill=(40,40,40))
            draw.ellipse([cx+40, cy-30, cx+80, cy+10], fill=(40,40,40))
            # خدين
            draw.ellipse([cx-110, cy+20, cx-80, cy+45], fill=(255,180,160))
            draw.ellipse([cx+80, cy+20, cx+110, cy+45], fill=(255,180,160))
            # فم مبتسم
            draw.arc([cx-50, cy+10, cx+50, cy+70], 10, 170, fill=(40,40,40), width=4)

        # اضافة عناصر بسيطة حسب المزاج
        if mood=="sad":
            # ايادي فارغة
            pass
        else:
            # هدية صغيرة فوق
            draw.rectangle([cx-40, cy-bh//2-120, cx+40, cy-bh//2-60], fill=(255,180,180), outline=(40,40,40), width=3)

        path = f"/tmp/local_{seed or random.randint(1,999999)}_{mood}.jpg"
        img.save(path, "JPEG", quality=92)
        return path
    except Exception as e:
        print(f"local gen error {e}")
        return None

def gen_img_pollinations(desc):
    try:
        prompt = f"hmisc style flat 2d doodle cute yellow blob thin outline beige paper {desc}"
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1280&nologo=true&seed={random.randint(1,999999)}"
        r = requests.get(url, timeout=25)
        if r.status_code==200 and len(r.content)>15000:
            p=f"/tmp/{random.randint(1,999999)}.jpg"
            with open(p,"wb") as f: f.write(r.content)
            return p
    except Exception as e:
        print(f"pollinations fail {e}")
    return None

def gen_img_smart(desc, mood):
    # جرب Pollinations مرة واحدة سريعة (25 ثانية) اذا فشل ارسم محلي فورا
    p = gen_img_pollinations(desc)
    if p: return p
    print("Fallback to local drawing")
    return gen_local_hmisc(mood, seed=abs(hash(desc)))

@flask_app.route('/')
def home(): return "✅ Live - local fallback enabled"

@flask_app.route('/setwebhook')
def setwebhook():
    host=request.host_url.replace("http://","https://")
    full=f"{host}{TOKEN}"
    r=requests.get(f"{tele_api()}/setWebhook?url={full}", timeout=15)
    return f"Webhook {full}<br>{r.text}"

@flask_app.route('/<token>', methods=['POST'])
def webhook(token):
    if token!=TOKEN: return "ok",200
    try:
        data=request.get_json(force=True)
        if not data or "message" not in data: return "ok",200
        chat_id=data["message"]["chat"]["id"]
        text=data["message"].get("text","")

        if "/start" in text:
            send_msg(chat_id,"🎨 شغال ✅ مع رسم محلي\n/hikma")

        elif "/hikma" in text:
            send_msg(chat_id,"⏳ بصنع... 10 ثواني ⏳")
            d=generate_hikma()
            send_msg(chat_id,f"💡 {d['full']}\n{d['en']}\n\n🎨 برسم 1/2...")
            p1=gen_img_smart(d['scene_top'],"sad")
            if p1: send_photo(chat_id,p1,d['ar_top'])
            time.sleep(1)
            send_msg(chat_id,"🎨 برسم 2/2...")
            p2=gen_img_smart(d['scene_bottom'],"happy")
            if p2: send_photo(chat_id,p2,f"{d['ar_bottom']}\n\n{d['en']}")

    except Exception as e: print(e)
    return "ok",200

if __name__=="__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000)))
