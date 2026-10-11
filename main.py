import os, re, json, time, uuid, logging, threading, subprocess, tempfile, shutil
from pathlib import Path
from urllib.parse import quote
import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN","").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY","").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-20b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POLL_IMG = "https://image.pollinations.ai/prompt/"
PORT = int(os.getenv("PORT","10000"))

DATA = Path(tempfile.gettempdir()) / "zil"
DATA.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ZIL")
flask_app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent":"ZIL-V85"})
ACTIVE = 0
HAS_FFMPEG = shutil.which("ffmpeg") is not None

def sh(cmd, t=300):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=t)
    if r.returncode!=0:
        raise RuntimeError((r.stderr or r.stdout)[-2000:])
    return r

def tg_msg(cid, txt):
    try: HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":cid,"text":txt[:4000]}, timeout=30)
    except: pass

def tg_photo(cid, path, cap):
    try:
        with open(path,"rb") as f:
            HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id":cid,"caption":cap[:1000]}, files={"photo":(Path(path).name,f,"image/png")}, timeout=180)
    except Exception as e: log.error(f"photo fail {e}")

def extract_json(s):
    s = re.sub(r"^```.*?\n","",s.strip(),flags=re.S); s=re.sub(r"\n```$","",s.strip())
    try: return json.loads(s)
    except: return json.loads(s[s.find("{"):s.rfind("}")+1])

def groq(msgs):
    if not GROQ_API_KEY: raise RuntimeError("GROQ_API_KEY missing")
    h={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}
    p={"model":GROQ_MODEL,"messages":msgs,"temperature":0.9,"max_tokens":2500,"response_format":{"type":"json_object"}}
    r=HTTP.post(GROQ_URL,headers=h,json=p,timeout=90); r.raise_for_status()
    return extract_json(r.json()["choices"][0]["message"]["content"])

def get_ideas(n=3):
    sys_msg='Return JSON {"ideas":[{"text_ar":"حكمة عربية قصيرة سطرين","top":"top illusion English","bottom":"bottom reality English","emotion":"anxiety"}]} Style yellow #FFC83D parchment #F5E6C8 split top/bottom.'
    try:
        d=groq([{"role":"system","content":sys_msg},{"role":"user","content":f"create {n} ideas"}])
        if d.get("ideas"): return d["ideas"][:n]
    except Exception as e: log.warning(e)
    return [{"text_ar":"لا تصنع هموماً\nلم يكتبها الله عليك","top":"yellow character drowning deep ocean panicking","bottom":"same character sitting shallow water calm","emotion":"anxiety"}]*n

def gen_image(prompt,dest,seed):
    url=POLL_IMG+quote(prompt,safe="")
    r=HTTP.get(url,params={"width":768,"height":1365,"seed":seed,"nologo":"true","model":"flux","enhance":"true"},timeout=180); r.raise_for_status()
    Path(dest).write_bytes(r.content)

def make_image_hmisc(idea, wd, i):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    raw=wd/f"raw_{i}.jpg"; final=wd/f"HMISC_{i+1}.png"
    prompt=f"Split screen top bottom, top:{idea['top']}, bottom:{idea['bottom']}, soft watercolor parchment paper texture #F5E6C8, yellow cute round character #FFC83D, storybook, no text, vertical 9:16 highly detailed"
    if not final.exists():
        if not raw.exists():
            gen_image(prompt,raw,1000+i*137)
        try:
            img=Image.open(raw).convert("RGBA")
            # قص شريط pollinations السفلي 90px
            w,h=img.size
            if h>90: img=img.crop((0,0,w,h-90))
            img=img.resize((720,1280), Image.LANCZOS)
            # اضافة نص عربي صحيح
            draw=ImageDraw.Draw(img)
            try: font=ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",38)
            except:
                try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",32)
                except: font=ImageFont.load_default()
            def fix(t):
                try: return get_display(arabic_reshaper.reshape(t.strip()))
                except: return t
            y=70
            for line in idea["text_ar"].split("\n"):
                if not line.strip(): continue
                tx=fix(line)
                bbox=draw.textbbox((0,0),tx,font=font); ww=bbox[2]-bbox[0]
                # ظل
                draw.text(((720-ww)//2+2,y+2),tx,font=font,fill=(0,0,0,120))
                draw.text(((720-ww)//2,y),tx,font=font,fill=(30,15,5,255))
                y+=50
            img.save(final,"PNG")
        except Exception as e:
            log.error(f"pil fail {e}")
            Path(raw).rename(final) if Path(raw).exists() else None
    return final

def worker(jid,cid,ideas):
    global ACTIVE
    try:
        tg_msg(cid,f"✨ hmisc V85 - {len(ideas)} صور بدون علامة مائية (وضع بدون ffmpeg)")
        for i,idea in enumerate(ideas):
            wd=DATA/f"{jid}_{i}"; wd.mkdir(parents=True,exist_ok=True)
            tg_msg(cid,f"🎨 {i+1}/{len(ideas)} {idea['text_ar'][:40]}")
            img=make_image_hmisc(idea,wd,i)
            tg_photo(cid,img,idea["text_ar"])
            time.sleep(1)
        tg_msg(cid,"🎉 اكتمل - هذه نسخة الصور. لما نحل ffmpeg نرجع فيديو 6.5ث")
    except Exception as e:
        log.exception(e); tg_msg(cid,f"❌ {e}")
    finally: ACTIVE=max(0,ACTIVE-1)

async def cmd_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 ZIL V85 NATIVE\nشغال بدون Docker\n/hmisc 1\n/hmisc 3\n/hmisc 5")

async def cmd_hmisc(update:Update, context:ContextTypes.DEFAULT_TYPE):
    global ACTIVE
    if ACTIVE>=1:
        await update.message.reply_text("⏳ في شغل حاليا انتظر"); return
    n=1
    if context.args:
        try: n=int(context.args[0])
        except: pass
    n=max(1,min(10,n)); jid=uuid.uuid4().hex[:8]; ACTIVE+=1
    await update.message.reply_text(f"✨ توليد {n} فكرة hmisc...")
    def run(): worker(jid,update.effective_chat.id,get_ideas(n))
    threading.Thread(target=run,daemon=True).start()

@flask_app.route("/")
def home(): return jsonify({"status":"ZIL V85 NATIVE","active":ACTIVE,"ffmpeg":HAS_FFMPEG})

@flask_app.route("/health")
def hl(): return jsonify({"ok":True,"bot":bool(BOT_TOKEN),"groq":bool(GROQ_API_KEY),"ffmpeg":HAS_FFMPEG,"active":ACTIVE})

def run_flask(): flask_app.run(host="0.0.0.0",port=PORT,use_reloader=False,threaded=True)

def main():
    threading.Thread(target=run_flask,daemon=True).start(); time.sleep(2)
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing - flask only");
        while True: time.sleep(3600)
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start)); app.add_handler(CommandHandler("hmisc",cmd_hmisc))
    log.info(f"Bot + Flask on {PORT} ffmpeg={HAS_FFMPEG}"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
