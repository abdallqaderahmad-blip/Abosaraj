import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY") or os.getenv("PEXELS_KEY")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

print("===== V54 VIRAL FINAL - FEMALE + REAL + PRODUCTION =====")

def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def ensure_font():
    if not os.path.exists("Amiri.ttf"):
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=20)
            open("Amiri.ttf","wb").write(r.content)
        except: pass

def dl_video(url, path):
    try:
        r=requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200 or len(r.content)<80000:
            return False
        open(path,"wb").write(r.content)
        out=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",path], capture_output=True, text=True, timeout=6)
        if out.stdout.strip() and float(out.stdout.strip())>=3.0:
            return True
        os.remove(path)
    except: pass
    return False

def get_real_video(query):
    try:
        j=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&per_page=10", timeout=10).json()
        candidates=[]
        for hit in j.get("hits",[]):
            dur=hit.get("duration",0)
            tags=hit.get("tags","").lower()
            if 4<=dur<=18 and "animation" not in tags and "cartoon" not in tags:
                candidates.append(hit["videos"]["medium"]["url"] if "medium" in hit["videos"] else hit["videos"]["small"]["url"])
        if candidates:
            return random.choice(candidates)
    except: pass
    return None

async def viral_female_voice(text, out_path):
    # تحويل لنص فايرال مع وقفات
    t=text.replace(" كان "," كان... ").replace(" لكن "," لكن... ").replace(" حتى "," حتى... ").replace(" لم "," لم يعلموا... ").strip()

    # Edge-TTS - بنت فايرال
    try:
        import edge_tts
        voices=["ar-SA-ZariyahNeural","ar-EG-SalmaNeural","ar-AE-FatimaNeural"]
        for voice in voices:
            try:
                comm=edge_tts.Communicate(t, voice, rate="+12%", pitch="+3Hz", volume="+15%")
                await comm.save(out_path)
                if os.path.exists(out_path) and os.path.getsize(out_path)>3000:
                    # مؤثرات الفايرال: تسريع + ضغط + صدى استوديو
                    fx=out_path.replace(".mp3","_fx.mp3")
                    subprocess.run(f'ffmpeg -y -i {out_path} -af "atempo=1.05,loudnorm=I=-13:TP=-1:LRA=10,acompressor=threshold=-18dB:ratio=2.5,aecho=0.8:0.88:10:0.25" -b:a 96k {fx}', shell=True, timeout=15)
                    if os.path.exists(fx) and os.path.getsize(fx)>2000:
                        os.replace(fx,out_path)
                    print(f"✅ VIRAL VOICE {voice}")
                    return True
            except Exception as e:
                print(f"voice {voice} fail {e}")
                continue
    except Exception as e:
        print(f"edge import fail {e}")

    # Fallback gTTS
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar', slow=False).save(out_path)
        fx=out_path.replace(".mp3","_fx.mp3")
        subprocess.run(f'ffmpeg -y -i {out_path} -af "atempo=1.1,loudnorm" -b:a 96k {fx}', shell=True, timeout=10)
        if os.path.exists(fx): os.replace(fx,out_path)
        return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","3",out_path], timeout=8)
        return True

def build(cid, parts):
    try:
        for f in glob.glob("s_*")+glob.glob("a_*")+glob.glob("p_*")+["FINAL.mp4","list.txt","text.txt"]:
            try: os.remove(f)
            except: pass
        ensure_font()

        queries=[
            "sad poor man street real people",
            "rich business woman office real people",
            "billionaire luxury car yacht real people success"
        ]
        backups=[
            "https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4",
            "https://cdn.pixabay.com/video/2022/10/23/136272-763624442_small.mp4",
            "https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4"
        ]
        durs=[3,7,9]
        vids=[]

        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            tg_send(cid,f"🎬 {i+1}/3 انتاج فايرال")

            # فيديو حقيقي
            ok=False
            url=get_real_video(queries[i])
            if url and dl_video(url, v): ok=True
            if not ok:
                for b in backups:
                    if dl_video(b, v): ok=True; break
            if not ok: raise Exception(f"فشل فيديو {i+1}")

            # صوت بنت فايرال
            tg_send(cid,f"🎙️ راوية بنت {i+1}/3")
            loop2=asyncio.new_event_loop()
            asyncio.set_event_loop(loop2)
            loop2.run_until_complete(viral_female_voice(parts[i], a))
            loop2.close()

            # نص عربي
            txt=parts[i][:80]
            try:
                import arabic_reshaper
                from bidi.algorithm import get_display
                txt=get_display(arabic_reshaper.reshape(txt))
            except: pass
            open("text.txt","w",encoding="utf-8").write(txt)

            # مونتاج + نص + الوان فايرال
            if os.path.exists("Amiri.ttf"):
                vf="scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.1:brightness=0.03:saturation=1.25,drawbox=x=0:y=ih-200:w=iw:h=160:color=black@0.70:t=fill,drawtext=fontfile=Amiri.ttf:textfile=text.txt:fontcolor=white:fontsize=42:x=(w-text_w)/2:y=h-145:box=0:shadowcolor=black:shadowx=3:shadowy=3"
            else:
                vf="scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.1:saturation=1.25"

            cmd=f'ffmpeg -y -i {v} -i {a} -vf "{vf}" -t {durs[i]} -c:v libx264 -preset ultrafast -crf 25 -b:v 1200k -c:a aac -b:a 96k -shortest {p}'
            subprocess.run(cmd, shell=True, check=True, timeout=90)
            vids.append(p)
            tg_send(cid,f"✅ {i+1}/3 جاهز - بنت + حقيقي")

        # دمج
        tg_send(cid,"🔗 دمج نهائي فايرال")
        with open("list.txt","w") as f:
            for x in vids: f.write(f"file '{x}'\n")
        subprocess.run("ffmpeg -y -f concat -safe 0 -i list.txt -c copy FINAL.mp4", shell=True, check=True, timeout=20)

        with open("FINAL.mp4","rb") as vid:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V54 VIRAL FINAL\n🎙️ راوية بنت - صوت يوتيوب شورت\n🎥 ناس حقيقية 100%\n📝 نص احترافي\n⚡ الوان فايرال\n⏱️ 19s"},
                files={"video":vid}, timeout=120)
        tg_send(cid,"🏆 V54 فايرال جاهز - نفس صوت الشورت ✅")

    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد سائق فقير يستهزئ به الجميع|حتى اختارته الفتاة الغنية أمام الجميع وصدمتهم|لم يعلموا أنه ملياردير متخفي اشترى الشركة كلها"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V54 VIRAL\n🎙️ بنت فايرال\n🎥 حقيقي\n📝 نص")
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
def home(): return "V54 VIRAL FINAL - FEMALE REAL PRODUCTION"
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
