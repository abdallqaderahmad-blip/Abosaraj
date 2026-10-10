import os, requests, random, threading, re, traceback, subprocess, glob, asyncio, time
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY")
PEXELS_KEY=os.getenv("PEXELS_KEY")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

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
    d.text((W//2,H-46), txt, font=f, fill=col, anchor="mm", stroke_width=3 if bold else 2, stroke_fill="black")
    img.save("ar.png")
    from moviepy.editor import ImageClip
    return ImageClip("ar.png", duration=dur).set_position(('center',y), relative=True).set_start(start)

async def voice(t,o):
    import edge_tts
    await edge_tts.Communicate(t,"ar-SA-HamedNeural", rate="-3%", pitch="-12Hz", volume="+45%").save(o)

def get_video_pexels(q):
    try:
        if not PEXELS_KEY: return None
        headers={"Authorization": PEXELS_KEY}
        r=requests.get(f"https://api.pexels.com/videos/search?query={q}&per_page=6&orientation=portrait&size=small", headers=headers, timeout=9)
        data=r.json()
        if data.get("videos"):
            v=random.choice(data["videos"][:3])
            for f in v["video_files"]:
                if f["width"] in [720,640,540]: return f["link"]
            return v["video_files"][0]["link"]
    except: pass
    return None

def get_video_pixabay(q):
    try:
        if not PIXABAY_KEY: return None
        data=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=6",timeout=8).json()
        if data.get("hits"):
            return random.choice(data["hits"][:3])["videos"]["small"]["url"]
    except: pass
    return None

def build(cid, parts):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("p_*.mp4")+["FINAL.mp4","ar.png","bg.mp3","list.txt"]:
            try: os.remove(f)
            except: pass
        ensure_font()
        try: open("bg.mp3","wb").write(requests.get("https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",timeout=10).content)
        except: pass

        # بيانات الفيرال الذهبية - نفس الفيديو Driver Bana Billionaire
        q_pexels=["poor driver sad uniform office","rich business woman choosing employee office","billionaire ceo luxury success reveal"]
        q_pixabay=["poor driver sad laughing office","rich woman driver choosing","billionaire buying company shocking"]
        durs=[2.8, 6.7, 8.8] # 18.3 ثانية فيرال

        vids=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            dur=durs[i]
            tg_send(cid,f"🎬 {i+1}/3 ذهبي {dur}s")

            # جلب فيديو Pexels HD أول + Pixabay احتياطي - نفس مصدر الفيرال
            url=get_video_pexels(q_pexels[i]) if PEXELS_KEY else None
            src="Pexels HD" if url else ""
            if not url:
                url=get_video_pixabay(q_pixabay[i])
                src="Pixabay"
            if url:
                try:
                    r=requests.get(url,timeout=22)
                    open(v,"wb").write(r.content)
                    tg_send(cid,f"✅ {src} {i+1}")
                except: url=None
            if not url:
                tg_send(cid,f"⚠️ احتياطي {i+1}")
                open(v,"wb").write(requests.get("https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",timeout=18).content)

            # صوت
            try: asyncio.run(voice(parts[i],a))
            except: time.sleep(0.8); asyncio.run(voice(parts[i],a))

            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)

            # تناسق حركة الكاميرا الذهبي - نفس الفيرال
            if i==0:
                # هوك: 0.0-0.3s زوم سريع 100%->140% ثم ثبات - يوقف السكرول
                vc=vc.resize(lambda t: 1.0 + 0.4*min(t/0.3,1)).set_position(('center','center'))
            else:
                # باقي المشاهد: زوم ثابت 1.38x + بان بطيء -26px أفقي - تناسق
                vc=vc.resize(lambda t: 1.38).set_position(lambda t: (-26*t/dur, -5*t/dur))

            # صوت BG 36% + Voice 100% - نفس الفيرال
            audio_list=[ac]
            if os.path.exists("bg.mp3"):
                try: audio_list.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.36))
                except: pass
            final_audio=CompositeAudioClip(audio_list)
            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)

            # نصوص فيرال ذهبية - ألوان وتوقيت طبق الأصل
            if i==0:
                hook=ar_clip("كان مجرد سواق 😱", dur, 44, 0.14, '#FFD700', 215, 0, True) # أصفر ذهبي بولد
                txt=ar_clip(parts[i], dur, 33, 0.73, 'white', 188, 0.35)
                final=CompositeVideoClip([base, txt, hook], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            elif i==1:
                txt=ar_clip(parts[i], dur, 34, 0.72, 'white', 188, 0)
                sub=ar_clip("الكل ضحك عليه", 2.2, 30, 0.23, '#FFFFFF', 155, 0.4)
                final=CompositeVideoClip([base, txt, sub], size=(720,1280)).set_audio(final_audio).set_duration(dur)
            else:
                txt=ar_clip(parts[i], dur, 33, 0.69, 'white', 188, 0)
                cta=ar_clip("اشترى الشركة وطرد المدير.. الجزء 2؟ تابعني 👇", 4.8, 37, 0.88, '#FFD700', 218, dur-4.8, True)
                final=CompositeVideoClip([base, txt, cta], size=(720,1280)).set_audio(final_audio).set_duration(dur)

            # تصدير 900k + 24fps - نفس جودة الفيرال
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", bitrate="900k", logger=None)
            vids.append(p)

        # دمج ذهبي سلس - fade 0.6s فيديو + acrossfade 0.6s صوت - نفس الفيرال
        tg_send(cid,"🔗 دمج ذهبي فيرال 0.6s...")
        try:
            o1=durs[0]-0.6; o2=o1+durs[1]-0.6
            # فلتر ألوان سينمائي موحد + دمج سلس
            cmd=f"ffmpeg -y -i {vids[0]} -i {vids[1]} -i {vids[2]} -filter_complex \"[0:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v0c];[1:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v1c];[2:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v2c];[v0c][v1c]xfade=transition=fade:duration=0.6:offset={o1}[v01];[v01][v2c]xfade=transition=fade:duration=0.6:offset={o2}[v];[0:a][1:a]acrossfade=d=0.6[a01];[a01][2:a]acrossfade=d=0.6[a]\" -map \"[v]\" -map \"[a]\" -preset ultrafast -b:v 900k FINAL.mp4"
            subprocess.run(cmd, shell=True, check=True, timeout=90)
        except Exception as e:
            tg_send(cid,f"⚠️ دمج عادي: {e}")
            with open("list.txt","w") as f:
                for x in vids: f.write(f"file '{x}'\n")
            subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True, timeout=25)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V43 ذهبي نهائي - Driver Bana Billionaire\n✅ مصدر: Pexels HD + Pixabay - نفس الفيرال\n⏱️ 2.8s هوك أصفر + 6.7s + 8.8s CTA = 18.3s\n🎥 زوم 0.3s + بان 38% تناسق\n🎨 فلتر سينمائي contrast 1.08\n🔗 fade 0.6s + acrossfade 0.6s\n👇 الجزء 2؟ تابعني"},
                files={"video":f}, timeout=115)
        tg_send(cid,"🏆 ذهبي جاهز - نفس الفيرال 100%")

    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد سواق فقير الكل بضحك عليه|البنت الغنية اختارتو قدام الكل|ما بيعرفو انه ملياردير مخفي واشترى الشركة"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🏆 V43 ذهبي نهائي\n✅ Pexels+Pixabay HD - نفس مصدر الفيرال\n⏱️ 18.3s فيرال + دمج 0.6s\n🎨 فلتر سينمائي\n🚀 ما بعلق")
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
def home(): return "V43 GOLD FINAL - VIRAL CLONE"
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
