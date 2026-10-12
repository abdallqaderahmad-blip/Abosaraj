import os, requests, json, time, random, math
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
    fb={"ar_top":"لا تنتظر ما في أيدي الناس","ar_bottom":"فما كتبه الله لك سيأتيك","en":"Don't wait for what is in people's hands","full":"لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك"}
    if not groq_client: return fb
    try:
        r=groq_client.chat.completions.create(model="llama3-70b-8192", messages=[{"role":"user","content":'JSON only {"ar_top":"سطر قصير 4 كلمات","ar_bottom":"سطر ثاني 4 كلمات","en":"short english","full":"كاملة"}'}], response_format={"type":"json_object"})
        return json.loads(r.choices[0].message.content)
    except: return fb

# رسم hmisc محلي محسّن جدا
def gen_hmisc(mood="sad", seed=0):
    W,H = 1080,1350
    random.seed(seed)
    bg = (242, 235, 222)
    img = Image.new("RGB", (W,H), bg)
    draw = ImageDraw.Draw(img)

    # texture ورق قوي
    for _ in range(8000):
        x,y = random.randint(0,W), random.randint(0,H)
        c = random.randint(220,235)
        draw.point((x,y), fill=(c, c-5, c-15))

    cx, cy = W//2, H//2
    # جسم مموج مو ellipse مثالي - زي رسم اليد
    points = []
    for i in range(60):
        angle = i * 6.28 / 60
        # تموج خفيف
        r = 1 + 0.06*math.sin(angle*3) + 0.04*random.random()
        rx = 200 * r
        ry = 270 * r
        x = cx + rx * math.cos(angle)
        y = cy + ry * math.sin(angle)
        points.append((x,y))

    # ظل
    shadow = [(x+12, y+35) for x,y in points]
    draw.polygon(shadow, fill=(200,190,170))
    # جسم اصفر
    yellow = (255, 229, 120)
    draw.polygon(points, fill=yellow, outline=(35,35,35), width=5)

    # اذنين
    for ox in [-90, 90]:
        ex, ey = cx+ox, cy-260
        draw.ellipse([ex-45, ey-35, ex+45, ey+45], fill=yellow, outline=(35,35,35), width=5)

    # وجه
    if mood=="sad":
        # عيون حزينة نايمة
        draw.ellipse([cx-75, cy-30, cx-35, cy-10], fill=(35,35,35))
        draw.ellipse([cx+35, cy-30, cx+75, cy-10], fill=(35,35,35))
        # فم مموج حزين
        draw.line([cx-25, cy+40, cx-10, cy+45, cx+10, cy+45, cx+25, cy+40], fill=(35,35,35), width=4, joint="curve")
        # دمعة؟
        # draw.ellipse([cx-85, cy, cx-75, cy+15], fill=(180,200,255))
    else:
        # عيون سعيدة
        draw.ellipse([cx-70, cy-35, cx-30, cy+5], fill=(35,35,35))
        draw.ellipse([cx+30, cy-35, cx+70, cy+5], fill=(35,35,35))
        # لمعة عين
        draw.ellipse([cx-60, cy-25, cx-45, cy-10], fill=(255,255,255))
        draw.ellipse([cx+40, cy-25, cx+55, cy-10], fill=(255,255,255))
        # خدود
        draw.ellipse([cx-105, cy+25, cx-75, cy+50], fill=(255, 180, 175))
        draw.ellipse([cx+75, cy+25, cx+105, cy+50], fill=(255, 180, 175))
        # ابتسامة
        draw.arc([cx-45, cy+15, cx+45, cy+75], 20, 160, fill=(35,35,35), width=4)

    path = f"/tmp/hmisc_{seed}_{mood}.jpg"
    img.save(path, "JPEG", quality=95)
    return path

@flask_app.route('/')
def home(): return "✅ Hmisc Local 100% Live"

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
            send_msg(chat_id,"🎨 Hmisc Factory شغال 100% محلي ✅\nسريع وما بفشل\n\n/hikma")

        elif "/hikma" in text:
            d=generate_hikma()
            send_msg(chat_id,f"💡 {d['full']}\n\n🎨 برسم...")
            p1=gen_hmisc("sad", seed=random.randint(1,999999))
            send_photo(chat_id,p1,d['ar_top'])
            time.sleep(1)
            p2=gen_hmisc("happy", seed=random.randint(1,999999))
            send_photo(chat_id,p2,f"{d['ar_bottom']}\n\n{d['en']}\n\n#hmisc")

    except Exception as e: print(e)
    return "ok",200

if __name__=="__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000)))
