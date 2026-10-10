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

# === مكتبة مؤثرات حسب القصة ===
SFX_LIB = {
    "مطر": "https://cdn.pixabay.com/audio/2022/03/24/audio_1718a6d2a8.mp3",
    "بكي": "https://cdn.pixabay.com/audio/2022/10/30/audio_fbd67a1b6b.mp3",
    "مستشفى": "https://cdn.pixabay.com/audio/2022/03/15/audio_b9bd4170e8.mp3",
    "مكتب": "https://cdn.pixabay.com/audio/2022/10/30/audio_8fa18444f0.mp3",
    "مصاري": "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3",
    "قلب": "https://cdn.pixabay.com/audio/2022/03/10/audio_c8c8a650f6.mp3",
    "بحر": "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3",
    "ليل": "https://cdn.pixabay.com/audio/2022/03/10/audio_2a9c7d8a7e.mp3",
    "default_bg": "https://cdn.pixabay.com/audio/2022/10/30/audio_8fa18444f0.mp3" # موسيقى خلفية درامية
}

def get_sfx_for_line(line):
    line=line.lower()
    sfx=[]
    if any(w in line for w in ["مطر","دموع","بكي","تبكي"]): sfx.append(SFX_LIB["مطر"])
    if any(w in line for w in ["مستشفى","أمها","مريضة"]): sfx.append(SFX_LIB["مستشفى"])
    if any(w in line for w in ["مكتب","مدير","طرد","شغل"]): sfx.append(SFX_LIB["مكتب"])
    if any(w in line for w in ["مصاري","محفظة","دولار","فلوس"]): sfx.append(SFX_LIB["مصاري"])
    if any(w in line for w in ["قلب","خوف","صدم"]): sfx.append(SFX_LIB["قلب"])
    if not sfx: sfx=[SFX_LIB["ليل"]]
    return sfx[0]

def ensure_arabic_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try: open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=20).content)
        except: pass
    return "Amiri-Regular.ttf" if os.path.exists("Amiri-Regular.ttf") else None

def make_arabic_clip(text, duration, fontsize=36, y_pos=0.78, color='white'):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        bidi_text=get_display(arabic_reshaper.reshape(text))
    except: bidi_text=text
    font_path=ensure_arabic_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=700,160
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    draw=ImageDraw.Draw(img)
    try: font=ImageFont.truetype(font_path, fontsize) if font_path else ImageFont.load_default()
    except: font=ImageFont.load_default()
    draw.rectangle([0, H-85, W, H], fill=(0,0,0,160))
    draw.text((W//2, H-42), bidi_text, font=font, fill=color, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("arabic_temp.png")
    from moviepy.editor import ImageClip
    return ImageClip("arabic_temp.png", duration=duration).set_position(('center', y_pos), relative=True)

async def make_voice(text, out):
    import edge_tts
    voice="ar-SA-HamedNeural"
    await edge_tts.Communicate(text, voice, rate="-5%", pitch="-12Hz", volume="+30%").save(out)

def build(chat_id, lines, en=""):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt","arabic_temp.png","bg.mp3","sfx_*.mp3"]:
            try: os.remove(f)
            except: pass
        ensure_arabic_font()
        # باك جراوند عالي شوي - 35%
        try: open("bg.mp3","wb").write(requests.get(SFX_LIB["default_bg"],timeout=15).content)
        except: pass

        parts=[]
        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; sfx_file=f"sfx_{i}.mp3"; p=f"part_{i}.mp4"
            # فيديو مع شخصية ظاهرة دائما
            queries=["girl alone night city cinematic 4k","girl crying office sad cinematic","girl sunrise hope smile cinematic"]
            q=queries[i]

            tg_send(chat_id,f"🎬 {i+1}: {q}\n🔊 تأثير: {get_sfx_for_line(line)[:30]}")

            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=20"
                    data=requests.get(url,timeout=12).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"][:5])["videos"]["medium"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
            except: open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=30).content)

            asyncio.run(make_voice(line, a))
            try: open(sfx_file,"wb").write(requests.get(get_sfx_for_line(line),timeout=10).content)
            except: pass

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=max(ac.duration+0.8, 7.5) # غير ممل - 7.5 ثانية سريع
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # === زوم سينمائي متحرك غير ممل ===
            # زوم 30% + حركة بان يمين-يسار + اهتزاز خفيف
            def zoom_func(t):
                return 1 + 0.30*t/dur + 0.03*random.random()
            vc_moving = vc.resize(zoom_func)

            # حركة بان - غير ممل
            def pos_func(t):
                x = -25 * (t/dur) + random.uniform(-3,3) # حركة مستمرة
                y = -10 * (t/dur)
                return (x,y)
            vc_moving = vc_moving.set_position(pos_func)

            # صوت 3 طبقات: راوي + مؤثر حسب القصة + باك جراوند عالي 35%
            audio_layers=[ac]
            if os.path.exists(sfx_file):
                try: audio_layers.append(AudioFileClip(sfx_file).subclip(0,dur).volumex(0.45)) # تأثير 45% واضح
                except: pass
            if os.path.exists("bg.mp3"):
                try: audio_layers.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.35)) # باك جراوند 35% عالي شوي
                except: pass

            final_audio=CompositeAudioClip(audio_layers) if len(audio_layers)>1 else ac

            arabic_clip=make_arabic_clip(line, dur, 34, 0.74)
            clips=[CompositeVideoClip([vc_moving], size=(720,1280)).set_duration(dur), arabic_clip]

            if i==0:
                hook=make_arabic_clip("اللي صار بعدها صدمني 😱", 2.8, 38, 0.15, 'yellow')
                clips.append(hook)
            if i==2:
                cta=make_arabic_clip("الجزء الثاني؟ تابعني 👇", 3.5, 36, 0.88, 'yellow')
                clips[-1]=clips[-1] # keep arabic
                clips.append(cta.set_start(dur-3.5))

            final=CompositeVideoClip(clips, size=(720,1280)).set_audio(final_audio).set_duration(dur)
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", logger=None)
            parts.append(p)
            tg_send(chat_id,f"✅ مشهد {i+1} - زوم متحرك + صوت عالي")

        with open("list.txt","w") as f:
            for x in parts: f.write(f"file '{x}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True)
        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":"🔥 V34 غير ممل\n🎥 زوم متحرك\n🔊 مؤثرات حسب القصة + باك جراوند 35%\n👇 تابعني للجزء الثاني"}, files={"video":f}, timeout=180)
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if "|" in raw: ar,en=raw.split("|",1)
    else: ar,en=raw,""
    if len(ar)<5:
        lines=["المدير طردها قدام الكل عشان تأخرت 5 دقايق","ما بيعرف انها كانت بالمستشفى مع أمها المريضة","شو عملت بعدها؟ القرار صدمني"]
        en="Boss fired her for 5 min late. In hospital with sick mom. What she did shocked me"
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', ar) if len(l.strip())>3][:3]
        if not en: en=". ".join(lines)
    await update.message.reply_text(f"🎬 V34 غير ممل\n🎥 زوم متحرك سينمائي\n🔊 مؤثرات + باك جراوند عالي")
    threading.Thread(target=build, args=(update.effective_chat.id, lines, en), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())
@web_app.route('/')
def home(): return "V34 NON-BORING"
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
