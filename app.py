import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
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
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=15)
    except: pass

def ensure_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try: open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=20).content)
        except: pass

def ar_clip(text,dur,size=36,y=0.72,col='white',bg_alpha=185):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt=get_display(arabic_reshaper.reshape(text))
    except: txt=text
    ensure_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=720,180
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    dr=ImageDraw.Draw(img)
    try: f=ImageFont.truetype("Amiri-Regular.ttf", size)
    except: f=ImageFont.load_default()
    dr.rectangle([10,H-92,W-10,H], fill=(0,0,0,bg_alpha))
    dr.text((W//2,H-46), txt, font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("ar.png")
    from moviepy.editor import ImageClip
    return ImageClip("ar.png", duration=dur).set_position(('center',y), relative=True)

async def voice(t,o):
    import edge_tts
    await edge_tts.Communicate(t,"ar-SA-HamedNeural", rate="-2%", pitch="-11Hz", volume="+40%").save(o)

def build(cid, script_parts):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("p_*.mp4")+["FINAL.mp4","ar.png","bg.mp3"]:
            try: os.remove(f)
            except: pass
        ensure_font()
        # باك جراوند ترند - درامي مشوق
        open("bg.mp3","wb").write(requests.get("https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",timeout=15).content)

        # نفس فيديوهات الترند - بنت بمكتب + مستشفى + صدمة
        queries=["woman office boss firing sad cinematic 4k","sad girl hospital mother sick cinematic","shocked woman revenge decision closeup cinematic"]

        vids=[]; durs=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            q=queries[i]
            tg_send(cid,f"🎬 فصل {i+1}/3 مايكرو دراما: {q}")

            try:
                if PIXABAY_KEY:
                    data=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=12",timeout=12).json()
                    vurl=random.choice(data["hits"][:3])["videos"]["medium"]["url"]
                    open(v,"wb").write(requests.get(vurl,timeout=30).content)
                else: raise Exception()
            except:
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=30).content)

            asyncio.run(voice(script_parts[i],a))
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=max(ac.duration+0.4, 6.5) # كل فصل 6.5-8 ثواني = 20-24 ثانية توتال - مثالي للخوارزمية
            durs.append(dur)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # زوم متحرك مايكرو دراما - نفس الترند 40%
            vc=vc.resize(lambda t: 1.42).set_position(lambda t: (-30*t/dur, -6*t/dur))

            bg=AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.38) # باك جراوند عالي شوي 38%
            final_audio=CompositeAudioClip([ac, bg])

            # === تركيبة الترند بالضبط ===
            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)
            if i==0:
                # هوك ترند: أصفر + أحمر
                hook1=ar_clip("انطردت قدام الكل 😱", 2.8, 42, 0.14, 'yellow', 210)
                hook2=ar_clip(script_parts[i], dur, 34, 0.70)
                final=CompositeVideoClip([base, hook2, hook1], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            elif i==1:
                mid=ar_clip(script_parts[i], dur, 34, 0.70)
                hint=ar_clip("ما بيعرف الحقيقة...", 2.2, 30, 0.20, 'white', 160).set_start(0.8)
                final=CompositeVideoClip([base, mid, hint], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            else:
                txt=ar_clip(script_parts[i], dur, 34, 0.65)
                cta=ar_clip("القرار صدمني... الجزء 2؟ تابعني 👇", 4.5, 38, 0.88, 'yellow', 210).set_start(dur-4.5)
                final=CompositeVideoClip([base, txt, cta], size=(720,1280)).set_audio(final_audio).set_duration(dur)

            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", logger=None)
            vids.append(p)

        # دمج سلس بدون تقطيع - نفس الترند
        tg_send(cid,"🔗 دمج سلس مايكرو دراما...")
        o1=durs[0]-0.65; o2=o1+durs[1]-0.65
        cmd=f"ffmpeg -y -i {vids[0]} -i {vids[1]} -i {vids[2]} -filter_complex \"[0:v][1:v]xfade=transition=fade:duration=0.65:offset={o1}[v01];[v01][2:v]xfade=transition=fade:duration=0.65:offset={o2}[v];[0:a][1:a]acrossfade=d=0.65[a01];[a01][2:a]acrossfade=d=0.65[a]\" -map \"[v]\" -map \"[a]\" FINAL.mp4"
        subprocess.run(cmd, shell=True, check=True)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":"🎬 MICRODRAMA - نفس الترند\n⏱️ 21s سلس\n🎥 زوم 42% متحرك\n🔊 BG 38% عالي\n👇 الجزء 2 تابعني"}, files={"video":f}, timeout=180)
    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<10:
        raw="المدير طردها قدام الكل عشان تأخرت 5 دقايق|ما بيعرف انها كانت بالمستشفى مع أمها المريضة|القرار اللي أخذتو بعدها صدمني"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🎬 V38 مايكرو دراما ترند\n📖 {parts[0][:30]}...\n✅ نفس فورمات 6.5B")
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
def home(): return "V38 MICRODRAMA TREND"
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
