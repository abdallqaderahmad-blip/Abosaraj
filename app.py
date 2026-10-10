import os, requests, random, threading, re, traceback, subprocess, glob, asyncio, time
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from PIL import Image
if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS
BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def ensure_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try: open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=15).content)
        except: pass

def ar_clip(text,dur,size=36,y=0.72,col='white'):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt=get_display(arabic_reshaper.reshape(text[:80]))
    except: txt=text[:80]
    ensure_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=720,160
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    d=ImageDraw.Draw(img)
    try: f=ImageFont.truetype("Amiri-Regular.ttf", size)
    except: f=ImageFont.load_default()
    d.rectangle([8,H-86,W-8,H], fill=(0,0,0,185))
    d.text((W//2,H-43), txt, font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("ar.png")
    from moviepy.editor import ImageClip
    return ImageClip("ar.png", duration=dur).set_position(('center',y), relative=True)

async def voice(t,o):
    import edge_tts
    await edge_tts.Communicate(t,"ar-SA-HamedNeural", rate="-2%", pitch="-10Hz", volume="+40%").save(o)

def build(cid, parts):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("p_*.mp4")+["FINAL.mp4","ar.png","bg.mp3","list.txt"]:
            try: os.remove(f)
            except: pass
        ensure_font()

        # باك جراوند خفيف ما بعلق
        try: open("bg.mp3","wb").write(requests.get("https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",timeout=10).content)
        except: pass

        # فيديوهات قصيرة 10 ثواني بس - ما بعلق
        queries=["woman office boss firing sad","sad girl hospital mother sick","shocked woman decision closeup"]

        vids=[]; durs=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            q=queries[i]
            tg_send(cid,f"🎬 {i+1}/3: {q}")

            # تحميل سريع مع timeout قصير
            try:
                if PIXABAY_KEY:
                    data=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=8",timeout=8).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"][:2])["videos"]["small"]["url"]
                        r=requests.get(vurl,timeout=20)
                        open(v,"wb").write(r.content)
                    else: raise Exception()
                else: raise Exception()
            except:
                # فيديو احتياطي صغير جدا
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=20).content)

            asyncio.run(voice(parts[i],a))

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=min(max(ac.duration+0.3, 6.0), 8.0) # كل مشهد 6-8 ثواني بس - ما بعلق
            durs.append(dur)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # زوم متحرك V35 + V38 مع بعض - 38% خفيف ما بعلق
            vc=vc.resize(lambda t: 1.38).set_position(lambda t: (-25*t/dur, -5*t/dur))

            audio_list=[ac]
            if os.path.exists("bg.mp3"):
                try: audio_list.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.36))
                except: pass
            final_audio=CompositeAudioClip(audio_list)

            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)

            if i==0:
                # V38 هوك + V35 تناسق
                hook=ar_clip("انطردت قدام الكل 😱", 2.6, 40, 0.15, 'yellow')
                txt=ar_clip(parts[i], dur, 34, 0.71)
                final=CompositeVideoClip([base, txt, hook], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            elif i==2:
                txt=ar_clip(parts[i], dur, 33, 0.68)
                cta=ar_clip("القرار صدمني.. الجزء 2؟ تابعني 👇", 3.8, 36, 0.88, 'yellow').set_start(dur-3.8)
                final=CompositeVideoClip([base, txt, cta], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            else:
                txt=ar_clip(parts[i], dur, 34, 0.71)
                final=CompositeVideoClip([base, txt], size=(720,1280)).set_audio(final_audio).set_duration(dur)

            # كتابة سريعة ultrafast + crf 28 خفيف
            final.write_videofile(p, fps=23, preset="ultrafast", codec="libx264", audio_codec="aac", bitrate="800k", logger=None)
            vids.append(p)
            time.sleep(0.5)

        # دمج V35 السلس - بس ب concat السريع لو crossfade بعلق
        tg_send(cid,"🔗 دمج نهائي سلس...")
        try:
            # جرب crossfade 0.6s
            o1=durs[0]-0.6; o2=o1+durs[1]-0.6
            cmd=f"ffmpeg -y -i {vids[0]} -i {vids[1]} -i {vids[2]} -filter_complex \"[0:v][1:v]xfade=transition=fade:duration=0.6:offset={o1}[v01];[v01][2:v]xfade=transition=fade:duration=0.6:offset={o2}[v];[0:a][1:a]acrossfade=d=0.6[a01];[a01][2:a]acrossfade=d=0.6[a]\" -map \"[v]\" -map \"[a]\" -preset ultrafast FINAL.mp4"
            subprocess.run(cmd, shell=True, check=True, timeout=90)
        except:
            # لو علق - دمج سريع بدون crossfade
            with open("list.txt","w") as f:
                for x in vids: f.write(f"file '{x}'\n")
            subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True, timeout=30)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":"✅ V39 مايكرو دراما + سلس\n🎥 زوم 38% متحرك\n🔊 BG 36% عالي\n🔗 crossfade 0.6s\n👇 تابعني للجزء 2"}, files={"video":f}, timeout=120)
        tg_send(cid,"✅ خلص - ما علق")

    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:600]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="المدير طردها قدام الكل عشان تأخرت 5 دقايق|ما بيعرف انها كانت بالمستشفى مع أمها المريضة|القرار اللي أخذتو بعدها صدمني"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🎬 V39 يجمع الاتنين\n✅ V35 سلس + V38 ترند\n⏱️ 18-22 ثانية\n🚀 ما بعلق")
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
def home(): return "V39 COMBINED NO-HANG"
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
