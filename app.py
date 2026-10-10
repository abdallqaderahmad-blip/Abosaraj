import os, requests, random, threading, re, traceback, subprocess, glob, asyncio, time
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from PIL import Image
if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY") or os.getenv("PEXELS_KEY") # لو حطيتو بمكان Pexels باخدو تلقائي
PEXELS_KEY=os.getenv("PEXELS_KEY") if os.getenv("PEXELS_KEY") and len(os.getenv("PEXELS_KEY"))>40 else None
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

print(f"===== V48 PIXABAY ONLY GOLD =====")
if PEXELS_KEY and PEXELS_KEY.startswith("57885775"):
    print("⚠️ اكتشفت مفتاح Pixabay بمكان Pexels - بصلح تلقائي")
    PIXABAY_KEY=PEXELS_KEY
    PEXELS_KEY=None

print(f"🔑 PEXELS: {'OK '+PEXELS_KEY[:10]+'...' if PEXELS_KEY else '❌ OFF - بيشتغل Pixabay بس'}")
print(f"🔑 PIXABAY: {'OK '+PIXABAY_KEY[:10]+'...' if PIXABAY_KEY else '❌ MISSING'} len={len(PIXABAY_KEY) if PIXABAY_KEY else 0}")

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

async def voice_edge(t,o):
    import edge_tts
    await edge_tts.Communicate(t,"ar-SA-HamedNeural", rate="-3%", pitch="-12Hz", volume="+45%").save(o)

def voice_gtts(t,o):
    from gtts import gTTS
    gTTS(text=t, lang='ar', slow=False).save(o)

def make_voice(t,o):
    try:
        asyncio.run(voice_edge(t,o))
        return True
    except:
        voice_gtts(t,o)
        return True

def download_valid(url, path):
    for attempt in range(3):
        try:
            print(f"⬇️ {path} attempt {attempt+1} {url[:60]}")
            r=requests.get(url, timeout=30)
            if r.status_code!=200 or len(r.content)<70000:
                print(f"⚠️ صغير {len(r.content)} او {r.status_code}")
                time.sleep(1)
                continue
            open(path,"wb").write(r.content)
            probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",path], capture_output=True, text=True, timeout=8)
            if probe.stdout.strip():
                dur=float(probe.stdout.strip())
                if dur>0.5:
                    print(f"✅ {path} OK {len(r.content)} bytes dur={dur}")
                    return True
        except Exception as e:
            print(f"❌ dl err {e}")
    return False

def get_video_pixabay(q):
    if not PIXABAY_KEY:
        print("❌ PIXABAY_KEY missing")
        return None
    try:
        url=f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=10&safesearch=true&order=popular"
        data=requests.get(url,timeout=10).json()
        print(f"Pixabay '{q}' -> {len(data.get('hits',[]))} hits")
        if data.get("hits"):
            random.shuffle(data["hits"])
            for hit in data["hits"][:5]:
                try:
                    vurl=hit["videos"]["small"]["url"]
                    if vurl: return vurl
                except: continue
    except Exception as e:
        print(f"Pixabay err {e}")
    return None

def get_video_pexels(q_list):
    if not PEXELS_KEY: return None, None
    headers={"Authorization": PEXELS_KEY}
    for q in q_list:
        try:
            r=requests.get(f"https://api.pexels.com/videos/search?query={q}&per_page=8&orientation=portrait", headers=headers, timeout=10)
            print(f"Pexels '{q}' -> {r.status_code}")
            if r.status_code!=200: continue
            vids=r.json().get("videos",[])
            if vids:
                v=random.choice(vids[:3])
                for f in sorted(v["video_files"], key=lambda x: x["width"], reverse=True):
                    if 360 <= f["width"] <= 720:
                        return f["link"], q
        except: continue
    return None, None

def build(cid, parts):
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip
    try:
        for f in glob.glob("s_*.mp4")+glob.glob("a_*.mp3")+glob.glob("p_*.mp4")+["FINAL.mp4","ar.png","bg.mp3","list.txt"]:
            try: os.remove(f)
            except: pass
        ensure_font()
        try: open("bg.mp3","wb").write(requests.get("https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",timeout=10).content)
        except: pass

        pexels_q=[["poor man uniform","sad driver"],["rich woman office","business woman"],["billionaire luxury","rich man success"]]
        pixabay_q=["poor man driver sad","rich woman office boss","billionaire luxury success"]
        backups=[
            "https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4",
            "https://cdn.pixabay.com/video/2019/11/03/28976-370981083_small.mp4",
            "https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4",
            "https://cdn.pixabay.com/video/2020/05/25/40244-423361133_small.mp4"
        ]
        durs=[2.8, 6.7, 8.8]
        vids=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            dur=durs[i]
            tg_send(cid,f"🎬 {i+1}/3 ذهبي {dur}s")

            url=None; src=""
            # جرب Pexels لو موجود
            if PEXELS_KEY:
                url, fq = get_video_pexels(pexels_q[i])
                if url: src=f"Pexels {fq}"
            # جرب Pixabay
            if not url:
                url=get_video_pixabay(pixabay_q[i])
                if url: src=f"Pixabay {pixabay_q[i]}"

            ok=False
            if url:
                if download_valid(url, v):
                    tg_send(cid,f"✅ {src} {i+1}")
                    ok=True
                else:
                    print(f"❌ فشل {src}")

            if not ok:
                tg_send(cid,f"⚠️ احتياطي {i+1}")
                random.shuffle(backups)
                for b in backups:
                    if download_valid(b, v):
                        ok=True
                        break
                if not ok:
                    raise Exception(f"فشل تحميل فيديو {i+1} - كل الروابط فشلت")

            make_voice(parts[i],a)
            vc=VideoFileClip(v).resize((720,1280))
            ac=AudioFileClip(a)
            if vc.duration < dur: vc=vc.loop(duration=dur)
            else: vc=vc.subclip(0,dur)
            if i==0: vc=vc.resize(lambda t: 1.0 + 0.4*min(t/0.3,1)).set_position(('center','center'))
            else: vc=vc.resize(lambda t: 1.38).set_position(lambda t: (-26*t/dur, -5*t/dur))
            audio_list=[ac]
            if os.path.exists("bg.mp3"):
                try: audio_list.append(AudioFileClip("bg.mp3").subclip(0,dur).volumex(0.36))
                except: pass
            final_audio=CompositeAudioClip(audio_list)
            base=CompositeVideoClip([vc], size=(720,1280)).set_duration(dur)
            if i==0:
                hook=ar_clip("كان مجرد سواق 😱", dur, 44, 0.14, '#FFD700', 215, 0, True)
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
            final.write_videofile(p, fps=24, preset="ultrafast", codec="libx264", audio_codec="aac", bitrate="900k", logger=None)
            vids.append(p)

        tg_send(cid,"🔗 دمج ذهبي 0.6s...")
        try:
            o1=durs[0]-0.6; o2=o1+durs[1]-0.6
            cmd=f"ffmpeg -y -i {vids[0]} -i {vids[1]} -i {vids[2]} -filter_complex \"[0:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v0c];[1:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v1c];[2:v]eq=contrast=1.08:brightness=0.02:saturation=1.15[v2c];[v0c][v1c]xfade=transition=fade:duration=0.6:offset={o1}[v01];[v01][v2c]xfade=transition=fade:duration=0.6:offset={o2}[v];[0:a][1:a]acrossfade=d=0.6[a01];[a01][2:a]acrossfade=d=0.6[a]\" -map \"[v]\" -map \"[a]\" -preset ultrafast -b:v 900k FINAL.mp4"
            subprocess.run(cmd, shell=True, check=True, timeout=90)
        except:
            with open("list.txt","w") as f:
                for x in vids: f.write(f"file '{x}'\n")
            subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"], check=True, timeout=25)

        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V48 ذهبي\n✅ Pixabay HD FIXED\n🎙️ مزدوج\n⏱️ 18.3s"},
                files={"video":f}, timeout=115)
        tg_send(cid,"🏆 V48 جاهز ✅")
    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:900]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد سواق فقير الكل بضحك عليه|البنت الغنية اختارتو قدام الكل|ما بيعرفو انه ملياردير مخفي واشترى الشركة"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🏆 V48 ذهبي\n🔑 Pixabay: {'✅' if PIXABAY_KEY else '❌'}\n🔑 Pexels: {'✅' if PEXELS_KEY else 'OFF'}")
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
def home(): return f"V48 GOLD - PIXABAY {'OK' if PIXABAY_KEY else 'MISSING'}"
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
