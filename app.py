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
        try:
            open("Amiri-Regular.ttf","wb").write(requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=12).content)
        except: pass

def ar_clip(text,dur,size=34,y=0.72,col='white',alpha=185,start=0):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        txt=get_display(arabic_reshaper.reshape(text[:85]))
    except:
        txt=text[:85]
    ensure_font()
    from PIL import Image as PILImage, ImageDraw, ImageFont
    W,H=720,165
    img=PILImage.new('RGBA',(W,H),(0,0,0,0))
    d=ImageDraw.Draw(img)
    try: f=ImageFont.truetype("Amiri-Regular.ttf", size)
    except: f=ImageFont.load_default()
    d.rectangle([10,H-88,W-10,H], fill=(0,0,0,alpha))
    d.text((W//2,H-44), txt, font=f, fill=col, anchor="mm", stroke_width=2, stroke_fill="black")
    img.save("ar.png")
    from moviepy.editor import ImageClip
    return ImageClip("ar.png", duration=dur).set_position(('center',y), relative=True).set_start(start)

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
        try:
            open("bg.mp3","wb").write(requests.get("https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",timeout=10).content)
        except: pass

        # مصحح لقصة السواق - نفس فيديو Driver Bana Billionaire الفيرال
        queries=[
            "poor driver uniform sad people laughing",
            "rich woman choosing driver office laughing",
            "billionaire ceo buying company shocking reveal"
        ]

        vids=[]
        durs=[2.8, 6.7, 8.8] # نفس توقيت الفيرال 18.3 ثانية

        for i in range(3):
            v=f"s_{i}.mp4"
            a=f"a_{i}.mp3"
            p=f"p_{i}.mp4"
            q=queries[i]

            tg_send(cid,f"🎬 {i+1}/3: {q} | {durs[i]}s")

            # تحميل سريع ما بعلق
            try:
                if PIXABAY_KEY:
                    data=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=6",timeout=8).json()
                    if data.get("hits"):
                        vurl=random.choice(data["hits"][:2])["videos"]["small"]["url"]
                        r=requests.get(vurl,timeout=18)
                        open(v,"wb").write(r.content)
                    else:
                        raise Exception("No hits")
                else:
                    raise Exception("No key")
            except Exception as e:
                tg_send(cid,f"⚠️ احتياطي {i+1}")
                # فيديو احتياطي صغير جدا
                try:
                    open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=18).content)
                except:
                    continue

            # صوت
            try:
                asyncio.run(voice(parts[i],a))
            except:
                time.sleep(1)
                asyncio.run(voice(parts[i],a))

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            dur=durs[i]

            if vc.duration < dur:
                vc=vc.loop(duration=dur)
            else:
                vc=vc.subclip(0,dur)

            # حركة كاميرا فيرال مصححة
            if i==0:
                # هوك زوم سريع 0.3 ثانية
                vc=vc.resize(lambda t: 1.0 + 0.4*min(t/0.3,1)).set_position(('center','center'))
            else:
                # بان بطيء 38%
                vc=vc.resize(lambda t: 1.38).set_position(lambda t: (-26*t/dur, -5*t/dur))

            audio_list=[ac]
            if os.path.exists("bg.mp3"):
                try:
                    audio_list.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.36))
                except: pass

            final_audio=CompositeAudioClip(audio_list)
            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)

            if i==0:
                hook=ar_clip("كان مجرد سواق 😱", dur, 42, 0.15, 'yellow', 210, 0)
                txt=ar_clip(parts[i], dur, 32, 0.72, 'white', 185, 0.4)
                final=CompositeVideoClip([base, txt, hook], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            elif i==1:
                txt=ar_clip(parts[i], dur, 33, 0.71, 'white', 185, 0)
                laugh=ar_clip("الكل ضحك عليه", 2.0, 30, 0.22, 'white', 150, 0.5)
                final=CompositeVideoClip([base, txt, laugh], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            else:
                txt=ar_clip(parts[i], dur, 32, 0.68, 'white', 185, 0)
                cta=ar_clip("اشترى الشركة وطرد المدير.. الجزء 2؟ تابعني 👇", 4.5, 36, 0.88, 'yellow', 210, dur-4.5)
                final=CompositeVideoClip([base, txt, cta], size=(720,1280)).set_audio(final_audio).set_duration(dur)

            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", bitrate="850k", logger=None)
            vids.append(p)
            time.sleep(0.3)

        if len(vids)<3:
            tg_send(cid,"❌ فشل تحميل فيديو")
            return

        tg_send(cid,"🔗 دمج نهائي فيرال 0.6s...")
        try:
            o1=durs[0]-0.6
            o2=o1+durs[1]-0.6
            cmd=f"ffmpeg -y -i {vids[0]} -i {vids[1]} -i {vids[2]} -filter_complex \"[0:v][1:v]xfade=transition=fade:duration=0.6:offset={o1}[v01];[v01][2:v]xfade=transition=fade:duration=0.6:offset={o2}[v];[0:a][1:a]acrossfade=d=0.6[a01];[a01][2:a]acrossfade=d=0.6[a]\" -map \"[v]\" -map \"[a]\" -preset ultrafast FINAL.mp4"
            subprocess.run(cmd, shell=True, check=True, timeout=80)
        except Exception as e:
            tg_send(cid,f"⚠️ دمج عادي: {e}")
            with open("list.txt","w") as f:
                for x in vids: f.write(f"file '{x}'\n")
            subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True, timeout=25)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🔥 V41 مصحح Driver Bana Billionaire\n⏱️ 2.8s هوك + 6.7s ضحك + 8.8s صدمة = 18.3s\n🎥 زوم فيرال 0.3s + بان 38%\n👇 الجزء 2 تابعني"},
                files={"video":f}, timeout=110)

        tg_send(cid,"✅ خلص - فيرال جاهز")

    except Exception as e:
        tg_send(cid,f"❌ خطأ: {e}\n{traceback.format_exc()[:700]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد سواق فقير الكل بضحك عليه|البنت الغنية اختارتو قدام الكل|ما بيعرفو انه ملياردير مخفي واشترى الشركة"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3:
        parts.append(parts[-1])

    await update.message.reply_text(f"🎬 V41 مصحح فيرال\n✅ قصة سواق فقير - Driver\n⏱️ 2.8s+6.7s+8.8s=18.3s\n🚀 ما بعلق - زوم فيرال")
    threading.Thread(target=build, args=(update.effective_chat.id, parts), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

loop=asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home():
    return "V41 DRIVER CORRECTED - READY"

@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data=request.get_json(force=True)
        update=Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(update))
    except Exception as e:
        print(f"webhook error: {e}")
    return 'ok'

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if WEBHOOK_URL:
        try:
            requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=8)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
