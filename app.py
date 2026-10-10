import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from PIL import Image
if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS
BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=15)
    except: pass

SFX_LIB = {
    "مطر": "https://cdn.pixabay.com/audio/2022/03/24/audio_1718a6d2a8.mp3",
    "مستشفى": "https://cdn.pixabay.com/audio/2022/03/15/audio_b9bd4170e8.mp3",
    "مكتب": "https://cdn.pixabay.com/audio/2022/10/30/audio_8fa18444f0.mp3",
    "صدمة": "https://cdn.pixabay.com/audio/2022/03/10/audio_c8c8a650f6.mp3",
    "bg": "https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3"
}

def get_video_query(line, idx):
    l=line.lower()
    # تناسق 100% مع معنى الكلام - مش عشوائي
    if any(w in l for w in ["مستشفى","أم","مريضة","دكتور"]): return "sad girl hospital mother sick cinematic"
    if any(w in l for w in ["مدير","طرد","مكتب","شغل","شركة"]): return "girl fired office boss crying cinematic"
    if any(w in l for w in ["ليل","نجوم","وحدة","تبكي","حزينة"]): return "lonely girl night city lights crying"
    if any(w in l for w in ["محفظة","مصاري","فلوس","شارع"]): return "girl finding wallet street cinematic"
    if any(w in l for w in ["صدم","قرار","مفاجأة"]): return "girl shocked face decision cinematic"
    if any(w in l for w in ["شمس","أمل","ابتسم","صباح"]): return "girl sunrise smile hope cinematic"
    return ["girl night sad cinematic","girl crying rain window","girl hope sunrise"][idx]

def get_sfx(line):
    l=line.lower()
    if "مستشفى" in l or "مريضة" in l: return SFX_LIB["مستشفى"]
    if "مكتب" in l or "طرد" in l: return SFX_LIB["مكتب"]
    if "صدم" in l or "قرار" in l: return SFX_LIB["صدمة"]
    return SFX_LIB["مطر"]

def ensure_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try: open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=20).content)
        except: pass

def make_arabic(text, dur, size=34, y=0.75, col='white'):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt=get_display(arabic_reshaper.reshape(text))
    except: txt=text
    ensure_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=720,160
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    d=ImageDraw.Draw(img)
    try: f=ImageFont.truetype("Amiri-Regular.ttf", size)
    except: f=ImageFont.load_default()
    d.rectangle([10,H-85,W-10,H], fill=(0,0,0,170))
    d.text((W//2,H-42), txt, font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("arabic_temp.png")
    from moviepy.editor import ImageClip
    return ImageClip("arabic_temp.png", duration=dur).set_position(('center',y), relative=True)

async def voice(text,out):
    import edge_tts
    await edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-5%", pitch="-12Hz", volume="+30%").save(out)

def build(chat_id, lines):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt","arabic_temp.png","bg.mp3","sfx_*.mp3"]:
            try: os.remove(f)
            except: pass
        ensure_font()
        try: open("bg.mp3","wb").write(requests.get(SFX_LIB["bg"],timeout=15).content)
        except: pass

        parts=[]
        durs=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; sfx_f=f"sfx_{i}.mp3"; p=f"part_{i}.mp4"
            q=get_video_query(line,i)
            tg_send(chat_id,f"🎬 مشهد {i+1} متناسق: {q}")

            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=15"
                    data=requests.get(url,timeout=12).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"][:4])["videos"]["medium"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
                    else: raise Exception()
            except:
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=30).content)

            asyncio.run(voice(line,a))
            try: open(sfx_f,"wb").write(requests.get(get_sfx(line),timeout=10).content)
            except: pass

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=max(ac.duration+0.6, 7.0)
            durs.append(dur)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # زوم سينمائي متحرك غير ممل
            vc=vc.resize(lambda t: 1+0.28*t/dur).set_position(lambda t: (-20*t/dur, -10*t/dur))

            audio_layers=[ac]
            if os.path.exists(sfx_f):
                try: audio_layers.append(AudioFileClip(sfx_f).subclip(0,dur).volumex(0.40))
                except: pass
            if os.path.exists("bg.mp3"):
                try: audio_layers.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.32)) # باك جراوند عالي
                except: pass

            final_audio=CompositeAudioClip(audio_layers)
            arabic_clip=make_arabic(line, dur, 34, 0.72)

            clips=[CompositeVideoClip([vc], size=(720,1280)).set_duration(dur), arabic_clip]

            if i==0:
                clips.append(make_arabic("POV: طردها وما بيعرف الحقيقة 😱", 2.8, 38, 0.14, 'yellow'))
            if i==2:
                # نهاية مشوقة + متابعة
                end1=make_arabic(line, dur, 34, 0.72)
                end2=make_arabic("القرار اللي أخذتو صدمني... الجزء 2؟ تابعني 👇", 4.0, 36, 0.88, 'yellow')
                clips=[CompositeVideoClip([vc], size=(720,1280)).set_duration(dur), end1, end2.set_start(dur-4.0)]

            final=CompositeVideoClip(clips, size=(720,1280)).set_audio(final_audio).set_duration(dur)
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", logger=None)
            parts.append(p)

        # === دمج سلس بدون تقطيع - crossfade 0.8 ثانية ===
        tg_send(chat_id,"🔗 بدمج بسلاسة بدون تقطيع...")
        # بناء فلتر xfade
        # part0 7s, part1 7s, part2 7s مع overlap 0.8
        filter_complex=""
        inputs=""
        for idx in range(3):
            inputs+=f"-i {parts[idx]} "

        # حساب offsets
        o1=durs[0]-0.8
        o2=o1+durs[1]-0.8
        cmd=f"ffmpeg -y {inputs} -filter_complex \"[0:v][1:v]xfade=transition=fade:duration=0.8:offset={o1}[v01];[v01][2:v]xfade=transition=fade:duration=0.8:offset={o2}[v];[0:a][1:a]acrossfade=d=0.8[a01];[a01][2:a]acrossfade=d=0.8[a]\" -map \"[v]\" -map \"[a]\" FINAL.mp4"
        subprocess.run(cmd, shell=True, check=True)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":"✅ V35 سلس 100%\n🎬 تناسق صورة مع كلام\n🎥 زوم متحرك\n🔊 باك جراوند 32%\n👇 نهاية مشوقة - تابعني للجزء 2"}, files={"video":f}, timeout=180)
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:900]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<5:
        raw="المدير طردها قدام الكل عشان تأخرت 5 دقايق. ما بيعرف انها كانت بالمستشفى مع أمها المريضة. القرار اللي أخذتو بعدها صدمني"
    lines=[l.strip() for l in re.split(r'[.!؟\n]+', raw) if len(l.strip())>3][:3]
    await update.message.reply_text(f"🎬 V35 سلس بدون تقطيع\n🔗 crossfade 0.8s\n🎯 تناسق 100%")
    threading.Thread(target=build, args=(update.effective_chat.id, lines), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())
@web_app.route('/')
def home(): return "V35 SEAMLESS"
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
