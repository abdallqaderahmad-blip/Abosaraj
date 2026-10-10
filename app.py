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
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":chat_id,"text":text}, timeout=10)
    except: pass

def tg_send_video(chat_id, path, caption=""):
    try:
        with open(path,"rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":caption}, files={"video":f}, timeout=120)
    except Exception as e: tg_send(chat_id, f"❌ إرسال فيديو فشل: {e}")

def get_keyword(t):
    t=t.lower()
    if any(w in t for w in ["ليل","ظلام","وحيدة"]): return "dark night"
    if any(w in t for w in ["مطر","دموع"]): return "rain window"
    if any(w in t for w in ["أم","نور","دعاء"]): return "sunlight hope"
    return "sad girl cinematic"

async def make_voice(text,out):
    try:
        comm=edge_tts.Communicate(text,"ar-SA-ZaydNeural",rate="-20%",pitch="-12Hz")
        await comm.save(out); return out
    except:
        from gtts import gTTS
        gTTS(text=text,lang='ar',slow=True).save(out); return out

def download_video(kw,fn):
    try:
        if PIXABAY_KEY:
            url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={kw}&per_page=10"
            r=requests.get(url,timeout=10).json()
            if r.get("hits"):
                vurl=random.choice(r["hits"])["videos"]["medium"]["url"]
                with requests.get(vurl,stream=True,timeout=30) as resp:
                    open(fn,"wb").write(resp.content)
                return fn
    except: pass
    url="https://cdn.pixabay.com/video/2020/07/30/45549-442790323_large.mp4"
    open(fn,"wb").write(requests.get(url,timeout=30).content)
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
            vc=VideoFileClip(v).subclip(0,5).resize((720,1280)).set_duration(5).fx(vfx.colorx,0.9)
            ac=AudioFileClip(a)
            vc=vc.set_audio(ac)
            clips.append(vc)
        final=concatenate_videoclips(clips,method="compose")
        final.write_videofile("FINAL.mp4",fps=24,preset="ultrafast",logger=None,threads=1)
        tg_send_video(chat_id,"FINAL.mp4","✅ V21 - نظيف بدون كتابة + راوي غامض")
    except Exception as e:
        import traceback
        tg_send(chat_id,f"❌ خطأ: {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    txt=re.sub(r'^ظل\s*','',txt).strip()
    if not txt or len(txt)<5:
        lines=["جلست ظل وحيدة في الليل","المطر على النافذة","ثم جاء النور بدعاء أمها"]
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', txt) if len(l.strip())>3][:3]
    await update.message.reply_text(f"🎬 V21 - {len(lines)} مشاهد 720p نظيف بدون كتابة\nبياخد دقيقة...")
    threading.Thread(target=build, args=(update.effective_chat.id, lines), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize()); loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V21 fixed loop"

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
