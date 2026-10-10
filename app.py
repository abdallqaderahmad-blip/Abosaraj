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
def tg_send_video(c,p):
    try:
        with open(p,"rb") as f:
            cap="✅ V32 CINEMATIC\n🎙️ راوي عربي فصيح\n👧 شخصية ظاهرة\n🎥 زوم سينمائي\n🔊 مؤثرات صوتية"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":c,"caption":cap}, files={"video":f}, timeout=180)
    except Exception as e: tg_send(c,f"❌ {e}")

# خط عربي يحل مشكلة المربعات
def ensure_arabic_font():
    if not os.path.exists("Amiri-Regular.ttf"):
        try:
            url="https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf"
            open("Amiri-Regular.ttf","wb").write(requests.get(url,timeout=20).content)
        except: pass
    return "Amiri-Regular.ttf" if os.path.exists("Amiri-Regular.ttf") else None

def make_arabic_clip(text, duration, fontsize=36, y_pos=0.78):
    """يعمل صورة نص عربي بدون مربعات"""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        bidi_text = get_display(reshaped)
    except:
        bidi_text = text

    font_path = ensure_arabic_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W, H = 700, 200
    img = PILImage.new('RGBA', (W, H), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    try:
        if font_path:
            font = ImageFont.truetype(font_path, fontsize)
        else:
            font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    # خلفية سوداء شفافة للنص
    draw.rectangle([0, H-90, W, H], fill=(0,0,0,140))
    draw.text((W//2, H-45), bidi_text, font=font, fill="white", anchor="mm", stroke_width=2, stroke_fill="black")

    img.save("arabic_temp.png")
    from moviepy.editor import ImageClip
    clip = ImageClip("arabic_temp.png", duration=duration)
    clip = clip.set_position(('center', y_pos), relative=True)
    return clip

async def make_authentic_voice(text, out):
    import edge_tts
    # Hamed صوت سعودي فصيح عميق أصيل - أفضل صوت عربي رجل غامض
    voice = "ar-SA-HamedNeural"
    communicate = edge_tts.Communicate(text, voice, rate="-8%", pitch="-15Hz", volume="+25%")
    await communicate.save(out)

def build(chat_id, lines, en=""):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("part_*.mp4")+["FINAL.mp4","list.txt","arabic_temp.png"]:
            try: os.remove(f)
            except: pass

        ensure_arabic_font()
        parts=[]
        # مؤثرات صوتية لكل مشهد
        sfx_urls = {
            0: "https://cdn.pixabay.com/audio/2022/03/10/audio_c8c8a650f6.mp3", # wind night
            1: "https://cdn.pixabay.com/audio/2022/03/24/audio_1718a6d2a8.mp3", # rain
            2: "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3" # waves sunrise
        }

        for i,line in enumerate(lines[:3]):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; sfx=f"sfx_{i}.mp3"; p=f"part_{i}.mp4"

            # بحث مع شخصية ظاهرة - هذا يحل مشكلة ما في شخصيات
            if i==0: q="anime girl silhouette mountain night stars lake"
            if i==1: q="anime girl rain window night bokeh sad"
            if i==2: q="anime girl sunrise sea beach smile hope"

            tg_send(chat_id,f"🎬 مشهد {i+1}: شخصية + {q[:30]}")

            try:
                if PIXABAY_KEY:
                    url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=20&video_type=film"
                    data=requests.get(url,timeout=12).json()
                    if data.get("hits"):
                        best = random.choice(data["hits"][:5])
                        vurl = best["videos"]["medium"]["url"]
                        open(v,"wb").write(requests.get(vurl,timeout=30).content)
                    else: raise Exception()
                else: raise Exception()
            except:
                fallbacks=["https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4"]*3
                open(v,"wb").write(requests.get(fallbacks[0],timeout=30).content)

            # صوت عربي أصيل
            tg_send(chat_id,f"🎙️ راوي عربي فصيح {i+1}...")
            asyncio.run(make_authentic_voice(line, a))

            # تحميل مؤثر صوتي
            try: open(sfx,"wb").write(requests.get(sfx_urls[i],timeout=10).content)
            except: pass

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=max(ac.duration+1.0, 8.0)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # زوم سينمائي حقيقي 25% + بان
            vc = vc.resize(lambda t: 1 + 0.25*t/dur).set_position(('center','center'))

            # تركيب صوت: راوي + مؤثر خفيف 20%
            audio_clips=[ac]
            if os.path.exists(sfx):
                try:
                    sfx_clip=AudioFileClip(sfx).subclip(0,dur).volumex(0.18)
                    audio_clips.append(sfx_clip)
                except: pass

            final_audio = CompositeAudioClip(audio_clips) if len(audio_clips)>1 else ac

            # ترجمة عربية بخط عربي أصيل - بدون مربعات
            arabic_clip = make_arabic_clip(line, dur, fontsize=34, y_pos=0.75)

            # ترجمة انجليزية صغيرة تحت
            clips=[vc, arabic_clip]
            if en:
                en_parts=en.split(".")
                if i < len(en_parts) and en_parts[i].strip():
                    try:
                        from moviepy.editor import TextClip
                        en_txt=TextClip(en_parts[i].strip()[:80], fontsize=18, color='white', font='DejaVu-Sans', stroke_color='black', stroke_width=1.2, method='caption', size=(640,None))
                        en_txt=en_txt.set_position(('center',0.90), relative=True).set_duration(dur)
                        clips.append(en_txt)
                    except: pass

            # هوك أول مشهد
            if i==0:
                try:
                    from moviepy.editor import TextClip
                    hook=TextClip("POV: ظنت ان الليل لن ينتهي...", fontsize=26, color='yellow', font='DejaVu-Sans-Bold', stroke_color='black', stroke_width=2, method='caption', size=(600,None))
                    clips.append(hook.set_position(('center',0.12)).set_duration(2.8))
                except: pass

            final=CompositeVideoClip(clips, size=(720,1280)).set_audio(final_audio)
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", logger=None)
            parts.append(p)
            tg_send(chat_id,f"✅ مشهد {i+1} جاهز - زوم + شخصية + صوت")

        with open("list.txt","w") as f:
            for x in parts: f.write(f"file '{x}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True)
        tg_send_video(chat_id,"FINAL.mp4")
    except Exception as e:
        tg_send(chat_id,f"❌ {e}\n{traceback.format_exc()[:1200]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if "|" in raw: ar,en=raw.split("|",1)
    else: ar,en=raw,""
    if len(ar)<5:
        lines=["وقفت وحدها على التلة تحت سماء مليئة بالنجوم","المطر يلمس وجهها وهي تبكي بصمت","ثم تذكرت كلام أمها فابتسمت وظهر نور الشمس"]
        en="Alone under stars. Rain on her face. Mother words brought sunrise"
    else:
        lines=[l.strip() for l in re.split(r'[.!؟\n]+', ar) if len(l.strip())>3][:3]
        if not en: en=". ".join(lines)
    await update.message.reply_text(f"🎬 V32 FINAL\n👧 شخصية ظاهرة\n🎙️ عربي أصيل Hamed\n🎥 زوم 25%\n🔊 مؤثرات\n📝 خط عربي بدون مربعات")
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
def home(): return "V32 FINAL"
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
