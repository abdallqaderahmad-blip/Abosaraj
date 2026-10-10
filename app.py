import os, requests, random, threading, re, traceback
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import *
from gtts import gTTS
from PIL import Image
Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

def tg_send(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":chat_id,"text":text}, timeout=15)

def tg_send_video(chat_id, path):
    with open(path,"rb") as f:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":"✅ V23 نظيف بدون كتابة - متناسق مع النص"}, files={"video":f}, timeout=180)

def get_keyword(t):
    t=t.lower()
    if "ليل" in t or "وحيدة" in t: return "dark night girl"
    if "مطر" in t or "نافذة" in t: return "rain window"
    if "أم" in t or "أمل" in t: return "sunlight hope"
    return "cinematic broll"

def build(chat_id, lines):
    try:
        tg_send(chat_id,f"⏳ ببلش {len(lines)} مشاهد...")
        clips=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"
            tg_send(chat_id,f"🎬 مشهد {i+1}: {line[:25]}...")
            
            # تحميل فيديو صغير مضمون
            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={get_keyword(line)}&per_page=10"
                    data=requests.get(url,timeout=10).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"])["videos"]["small"]["url"]
                        open(v, "wb").write(requests.get(vurl, timeout=30).content)
                    else: raise Exception("no hits")
                else: raise Exception("no key")
            except:
                # فيديو احتياطي صغير جدا 2MB
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/07/30/45549-442790323_small.mp4", timeout=30).content)
            
            tg_send(chat_id,f"🔊 صوت مشهد {i+1}...")
            # gTTS فقط - أسرع ومستقر
            gTTS(text=line, lang='ar', slow=False).save(a)
            
            ac=AudioFileClip(a)
            vc=VideoFileClip(v).resize((720,1280))
            # خليه بطول الصوت
            if vc.duration < ac.duration:
                vc=vc.loop(duration=ac.duration+0.3)
            else:
                vc=vc.subclip(0, ac.duration+0.3)
            vc=vc.set_audio(ac)
            clips.append(vc)
            tg_send(chat_id,f"✅ مشهد {i+1} جاهز")

        tg_send(chat_id,"✂️ بجمع الفيديو...")
        final=concatenate_videoclips(clips, method="compose")
        final.write_videofile("FINAL.mp4", fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", threads=1, logger=None)
        tg_send(chat_id,"📤 برفع...")
        tg_send_video(chat_id,"FINAL.mp4")
        for c in clips: c.close()
        final.close()
    except Exception as e:
        tg_send(chat_id,f"❌ خطأ: {e}\n{traceback.format_exc()[:1000]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(txt)<5:
        lines=["جلست وحيدة في الليل","المطر ينزل على النافذة","تذكرت كلام أمها ثم ابتسمت للأمل"]
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', txt) if len(l.strip())>3][:3]
    await update.message.reply_text(f"🎬 V23 خفيف - {len(lines)} مشاهد\nبدون كتابة - بدون علامة مائية - متناسق")
    threading.Thread(target=build, args=(update.effective_chat.id, lines), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

import asyncio
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize()); loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V23"

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
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}")
    web_app.run(host='0.0.0.0', port=port)
