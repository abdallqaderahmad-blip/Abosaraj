import os, requests, random, threading, re, traceback, subprocess, glob
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import VideoFileClip, AudioFileClip
from gtts import gTTS
from PIL import Image
Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

def tg_send(chat_id, text):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":chat_id,"text":text}, timeout=15)
    except: pass

def tg_send_video(chat_id, path):
    try:
        with open(path,"rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":"✅ بدون كتابة - بدون علامة - متناسق مع النص - راوي غامض"}, files={"video":f}, timeout=180)
    except Exception as e: tg_send(chat_id,f"❌ رفع فشل: {e}")

def get_keyword(t):
    t=t.lower()
    if "ليل" in t or "وحيدة" in t: return "dark night girl"
    if "مطر" in t or "نافذة" in t: return "rain window"
    if "أم" in t or "أمل" in t: return "sunlight hope"
    return "cinematic night"

def build(chat_id, lines):
    try:
        # تنظيف قديم
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt"]:
            try: os.remove(f)
            except: pass

        tg_send(chat_id,f"⏳ ببلش {len(lines)} مشاهد...")
        part_files=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; part=f"part_{i}.mp4"
            tg_send(chat_id,f"🎬 مشهد {i+1}: {line[:30]}...")
            
            # تحميل
            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={get_keyword(line)}&per_page=10"
                    data=requests.get(url,timeout=10).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"])["videos"]["small"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
                    else: raise Exception()
                else: raise Exception()
            except:
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/07/30/45549-442790323_small.mp4",timeout=30).content)

            gTTS(text=line, lang='ar', slow=False).save(a)
            
            # اعمل كل مشهد لحاله - أخف على الرام
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=ac.duration+0.4
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)
            
            # ألوان سينمائية غامقة بدون كتابة
            vc=vc.fx(lambda gf, t: gf(t)*0.88) # تعتيم خفيف
            
            vc.set_audio(ac).write_videofile(part, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", threads=1, logger=None)
            vc.close(); ac.close()
            part_files.append(part)
            tg_send(chat_id,f"✅ مشهد {i+1} جاهز")

        tg_send(chat_id,"✂️ بجمع بطريقة خفيفة...")
        # تجميع خفيف ب ffmpeg - ما بيستهلك رام
        with open("list.txt","w") as f:
            for p in part_files: f.write(f"file '{p}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True)

        tg_send(chat_id,"📤 برفع الفيديو النهائي...")
        tg_send_video(chat_id,"FINAL.mp4")
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:1200]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(txt)<5:
        lines=["جلست وحيدة في الليل","المطر ينزل على النافذة","تذكرت كلام أمها ثم ابتسمت للأمل"]
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', txt) if len(l.strip())>3][:3]
    await update.message.reply_text(f"🎬 V24 خفيف جدا - {len(lines)} مشاهد\nبدون كتابة - بدون علامة - ألوان سينمائية")
    threading.Thread(target=build, args=(update.effective_chat.id, lines), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

import asyncio
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize()); loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V24"

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
