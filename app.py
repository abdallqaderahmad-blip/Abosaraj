import os, asyncio, requests, random, threading, re
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import *
import edge_tts
from PIL import Image
Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

def tg_send(chat_id, text):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":chat_id,"text":text}, timeout=15)
    except: pass

def tg_send_video(chat_id, path, caption=""):
    try:
        with open(path,"rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":caption}, files={"video":f}, timeout=180)
    except Exception as e: tg_send(chat_id, f"❌ إرسال فشل: {e}")

def get_keyword(t):
    t=t.lower()
    if any(w in t for w in ["ليل","ظلام","وحيدة"]): return "dark night"
    if any(w in t for w in ["مطر","دموع"]): return "rain window"
    if any(w in t for w in ["أم","نور","دعاء","كلام"]): return "sunlight hope"
    return "sad girl cinematic"

async def make_voice(text,out):
    try:
        comm=edge_tts.Communicate(text,"ar-SA-ZaydNeural",rate="-20%",pitch="-12Hz")
        await comm.save(out)
    except:
        from gtts import gTTS
        gTTS(text=text,lang='ar',slow=True).save(out)
    return out

def download_video(kw,fn):
    try:
        if PIXABAY_KEY:
            url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={kw}&per_page=15"
            r=requests.get(url,timeout=10).json()
            if r.get("hits"):
                vurl=random.choice(r["hits"])["videos"]["medium"]["url"]
                data=requests.get(vurl,timeout=40).content
                open(fn,"wb").write(data)
                return fn
    except Exception as e: print(e)
    url="https://cdn.pixabay.com/video/2020/07/30/45549-442790323_large.mp4"
    open(fn,"wb").write(requests.get(url,timeout=40).content)
    return fn

def build(chat_id, lines):
    try:
        tg_send(chat_id,f"⏳ ببلش تحميل {len(lines)} مشاهد...")
        clips=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"
            tg_send(chat_id,f"🎬 مشهد {i+1}: {line[:30]}...")
            download_video(get_keyword(line), v)
            asyncio.run(make_voice(line,a))
            
            ac=AudioFileClip(a)
            dur = ac.duration + 0.5 # الفيديو بطول الصوت
            # لو الفيديو قصير - يعمل loop
            vc=VideoFileClip(v).resize((720,1280))
            if vc.duration < dur:
                vc = vc.loop(duration=dur)
            else:
                vc = vc.subclip(0,dur)
            vc=vc.set_duration(dur).fx(vfx.colorx,0.88)
            vc=vc.set_audio(ac)
            clips.append(vc)

        tg_send(chat_id,"✂️ بقص وبجمع...")
        final=concatenate_videoclips(clips,method="compose")
        tg_send(chat_id,f"💾 بكتب الفيديو النهائي {final.duration:.1f} ثانية...")
        final.write_videofile("FINAL.mp4",fps=24,preset="ultrafast",codec="libx264",audio_codec="aac",threads=1,logger=None)
        
        tg_send(chat_id,"📤 برفع الفيديو...")
        tg_send_video(chat_id,"FINAL.mp4","✅ V22 نظيف - بدون كتابة - ألوان سينمائية")
        
        # تنظيف
        for c in clips: c.close()
        final.close()
    except Exception as e:
        import traceback
        err=traceback.format_exc()
        print(err)
        tg_send(chat_id,f"❌ {e}\n{err[:1000]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    txt=re.sub(r'^ظل\s*','',txt).strip()
    if not txt or len(txt)<5:
        lines=["جلست وحيدة في الليل","المطر ينزل على النافذة","تذكرت كلام أمها بدعاء يشبه النور"]
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', txt) if len(l.strip())>3][:3]
    await update.message.reply_text(f"🎬 V22 - {len(lines)} مشاهد متناسقة مع النص\nبدون كتابة + راوي غامض")
    threading.Thread(target=build, args=(update.effective_chat.id, lines), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize()); loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V22"

@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data=request.get_json(force=True)
        update=Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(update))
    except: pass
    return 'ok'

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if WEBHOOK_URL:
        url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={url}")
    web_app.run(host='0.0.0.0', port=port)
