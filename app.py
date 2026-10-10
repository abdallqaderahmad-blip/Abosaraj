import os, requests, random, threading, re, traceback, subprocess, glob, asyncio, time
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY") or os.getenv("PEXELS_KEY")
PEXELS_KEY=os.getenv("PEXELS_KEY") if os.getenv("PEXELS_KEY") and len(os.getenv("PEXELS_KEY"))>40 else None
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

print(f"===== V49 FAST GOLD =====")
if PEXELS_KEY and PEXELS_KEY.startswith("57885775"):
    PIXABAY_KEY=PEXELS_KEY; PEXELS_KEY=None
print(f"🔑 PIXABAY OK: {bool(PIXABAY_KEY)}")

def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def ensure_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try: open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=12).content)
        except: pass

def ar_clip(text,dur,size=34,y=0.72,col='white',alpha=185,start=0,bold=False):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt=get_display(arabic_reshaper.reshape(text[:90]))
    except: txt=text[:90]
    ensure_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=720,170
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    d=ImageDraw.Draw(img)
    try: f=ImageFont.truetype("Amiri-Regular.ttf", size+ (4 if bold else 0))
    except: f=ImageFont.load_default()
    d.rectangle([12,H-92,W-12,H], fill=(0,0,0,alpha))
    d.text((W//2,H-46), txt, font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("ar.png")
    from moviepy.editor import ImageClip
    return ImageClip("ar.png", duration=dur).set_position(('center',y), relative=True).set_start(start)

def make_voice_fast(t,o):
    # gTTS فقط - أسرع وبدون تعليق
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar', slow=False).save(o)
        print(f"✅ gTTS {o}")
        return True
    except Exception as e:
        print(f"❌ gTTS fail {e}")
        # fallback صوت فارغ 3 ثواني
        try:
            subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","3","-q:a","9","-acodec","libmp3lame",o], timeout=10)
            return True
        except: return False

def download_valid(url, path):
    for attempt in range(2):
        try:
            r=requests.get(url, timeout=20)
            if r.status_code!=200 or len(r.content)<50000: continue
            open(path,"wb").write(r.content)
            probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",path], capture_output=True, text=True, timeout=6)
            if probe.stdout.strip() and float(probe.stdout.strip())>0.5:
                return True
        except: pass
    return False

def get_video_pixabay(q):
    try:
        data=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=6",timeout=8).json()
        hits=data.get("hits",[])
        if hits:
            return random.choice(hits)["videos"]["small"]["url"]
    except: pass
    return None

def build(cid, parts):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("p_*.mp4")+["FINAL.mp4","ar.png","bg.mp3","list.txt"]:
            try: os.remove(f)
            except: pass
        ensure_font()

        pixabay_q=["poor man sad","rich woman office","billionaire success"]
        backups=[
            "https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",
            "https://cdn.pixabay.com/video/2019/11/03/28976-370981083_small.mp4",
            "https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4"
        ]
        durs=[2.8, 6.7, 8.8]
        vids=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            dur=durs[i]
            tg_send(cid,f"🎬 {i+1}/3 سريع {dur}s")

            url=get_video_pixabay(pixabay_q[i])
            ok=False
            if url and download_valid(url, v): ok=True
            if not ok:
                for b in backups:
                    if download_valid(b, v):
                        ok=True; break
            if not ok: raise Exception(f"فشل فيديو {i+1}")

            tg_send(cid,f"🎙️ صوت {i+1}/3")
            if not make_voice_fast(parts[i], a):
                raise Exception(f"فشل صوت {i+1}")

            # مونتاج خفيف وسريع - بدون resize معقد
            tg_send(cid,f"✂️ مونتاج {i+1}/3")
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)
            vc=vc.set_duration(dur)
            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)
            txt=ar_clip(parts[i], dur, 32, 0.72, 'white', 188, 0)
            final=CompositeVideoClip([base, txt], size=(720,1280)).set_audio(CompositeAudioClip([ac])).set_duration(dur)
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", bitrate="700k", logger=None)
            # تنظيف الذاكرة
            vc.close(); ac.close(); final.close(); base.close()
            vids.append(p)
            tg_send(cid,f"✅ {i+1}/3 جاهز")

        tg_send(cid,"🔗 دمج نهائي...")
        with open("list.txt","w") as f:
            for x in vids: f.write(f"file '{x}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True, timeout=20)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V49 سريع\n✅ Pixabay HD\n⏱️ 18.3s"},
                files={"video":f}, timeout=100)
        tg_send(cid,"🏆 V49 جاهز ✅")
    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد سواق فقير الكل بضحك عليه|البنت الغنية اختارتو قدام الكل|ما بيعرفو انه ملياردير مخفي واشترى الشركة"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🏆 V49 سريع\n🔑 Pixabay ✅")
    threading.Thread(target=build, args=(update.effective_chat.id, parts), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())
@web_app.route('/')
def home(): return f"V49 FAST"
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
        try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=8)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
