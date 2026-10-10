import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from PIL import Image
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS
if not hasattr(Image, 'BICUBIC'):
    Image.BICUBIC = Image.LANCZOS

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=15)
    except: pass

def tg_send_video(c,p,caption):
    try:
        with open(p,"rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":c,"caption":caption}, files={"video":f}, timeout=180)
    except Exception as e: tg_send(c,f"❌ رفع: {e}")

def get_keyword_smart(line, idx):
    l = line.lower()
    hope_words = ["أم", "أمل", "ابتسم", "نور", "شمس", "فرح", "ضحك", "حب", "دعاء", "الله", "أمان", "دفء"]
    rain_words = ["مطر", "نافذة", "دموع", "بارد", "شتاء", "ريح", "يبكي", "بكت"]
    is_hope = any(w in l for w in hope_words)
    if idx == 0:
        return "anime girl alone hill night stars cinematic"
    if idx == 1:
        return "anime girl rain window sad tears cinematic"
    if idx == 2:
        if is_hope:
            return "anime girl sunrise smile hope warm light"
        return "anime girl sunrise hope light smile happy ending"

async def make_male_voice(text, out):
    try:
        import edge_tts
        voice = "ar-SY-LaithNeural"
        communicate = edge_tts.Communicate(text, voice, rate="-15%", pitch="-10Hz", volume="+15%")
        await communicate.save(out)
        return True
    except Exception as e:
        print(f"edge-tts fail {e}")
        try:
            from gtts import gTTS
            gTTS(text=text, lang='ar', slow=False).save(out)
        except: pass
        return False

def build(chat_id, lines, en_translation=""):
    from PIL import Image as PILImage
    if not hasattr(PILImage, 'ANTIALIAS'):
        PILImage.ANTIALIAS = PILImage.LANCZOS
    from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt"]:
            try: os.remove(f)
            except: pass
        part_files=[]
        en_parts = [p.strip() for p in en_translation.split(".") if p.strip()] if en_translation else []
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; part=f"part_{i}.mp4"
            q = get_keyword_smart(line, i)
            tg_send(chat_id,f"🎬 مشهد {i+1}: {line[:35]}\n🔍 {q}")
            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=15&video_type=film"
                    data=requests.get(url,timeout=12).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"][:5])["videos"]["small"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
                    else: raise Exception("no hits")
                else: raise Exception("no key")
            except:
                fallbacks = [
                    "https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",
                    "https://cdn.pixabay.com/video/2020/07/30/45549-442790323_small.mp4",
                    "https://cdn.pixabay.com/video/2019/10/09/27834-365890983_small.mp4"
                ]
                open(v,"wb").write(requests.get(fallbacks[i%3],timeout=30).content)
            tg_send(chat_id,f"🎙️ راوي رجل غامض {i+1}...")
            asyncio.run(make_male_voice(line, a))
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=ac.duration+0.6
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)
            clips = [vc]
            if i < len(en_parts):
                try:
                    en_text = en_parts[i][:90]
                    txt = TextClip(en_text, fontsize=22, color='white', font='DejaVu-Sans', stroke_color='black', stroke_width=1.5, method='caption', size=(660, None))
                    txt = txt.set_position(('center', 0.88), relative=True).set_duration(dur)
                    clips.append(txt)
                except Exception as e:
                    print(f"txt fail {e}")
            final_vc = CompositeVideoClip(clips, size=(720,1280)) if len(clips)>1 else vc
            final_vc = final_vc.set_audio(ac)
            final_vc.write_videofile(part, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", threads=1, logger=None)
            vc.close(); ac.close(); final_vc.close()
            part_files.append(part)
            tg_send(chat_id,f"✅ مشهد {i+1} جاهز")
        tg_send(chat_id,"✂️ بجمع ريلز 9:16...")
        with open("list.txt","w") as f:
            for p in part_files: f.write(f"file '{p}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True)
        cap = "✅ V29.1 REELS متناسق\n🎙️ راوي رجل غامض\n📝 ترجمة تحت\n📱 9:16 شورتس"
        tg_send_video(chat_id,"FINAL.mp4", cap)
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:1300]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if "|" in raw:
        ar_text, en_text = raw.split("|",1)
    else:
        ar_text = raw
        en_text = ""
    if len(ar_text)<5:
        lines=["وقفت وحدها على التلة تحت سماء مليئة بالنجوم","المطر يلمس وجهها وهي تبكي بصمت","ثم تذكرت كلام أمها فابتسمت وظهر نور الشمس"]
        en_text="Alone on hill under stars. Rain on her face. Mother words brought sunrise and smile"
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', ar_text) if len(l.strip())>3][:3]
        if not en_text:
            en_text = ". ".join(lines)
    await update.message.reply_text(f"🎬 V29.1 ذكي متناسق\n🎙️ رجل غامض\n📝 ترجمة ريلز\n{len(lines)} مشاهد")
    threading.Thread(target=build, args=(update.effective_chat.id, lines, en_text), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V29.1 FIXED"

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
        try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=10)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
