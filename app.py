import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY") or os.getenv("PEXELS_KEY") or "YOUR_PIXABAY_KEY"
PEXELS_KEY = os.getenv("PEXELS_KEY") or os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

print("===== V58 FINAL WEB CODE - ALL FIXES =====")

def tg(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def setup():
    if not os.path.exists("Amiri.ttf"):
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=20)
            if r.status_code==200: open("Amiri.ttf","wb").write(r.content)
        except: pass

def download(url, out):
    try:
        r=requests.get(url, timeout=30, stream=True, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200: return False
        with open(out,'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        if os.path.getsize(out)<50000:
            os.remove(out); return False
        # check duration
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",out], capture_output=True, text=True, timeout=8)
        if p.stdout.strip():
            d=float(p.stdout.strip())
            if 2<=d<=30: return True
        os.remove(out)
    except Exception as e:
        print(f"dl err {e}")
    return False

# ===== محرك قصص ذكي - من كود ويب =====
def story_to_queries(text, part_index):
    text=text.lower()
    # قاموس كبير يغطي كل القصص
    if part_index==0: # بداية حزينة
        if any(w in text for w in ["فقير","مسكين","حزين","يبكي","سائق","عامل","طفل","يتيما","بائع","مناديل","نظافة"]):
            return ["poor sad man walking street","poor boy alone street","sad man alone"]
        return ["sad person alone","poor man street"]
    elif part_index==1: # وسط - لقاء
        if any(w in text for w in ["غنية","بنت","فتاة","سيدة","امرأة","عجوز","جميلة","اميرة"]):
            return ["beautiful woman helping man","rich woman smile office","kind woman helping"]
        return ["people meeting help","woman man meeting"]
    else: # نهاية نجاح
        if any(w in text for w in ["ملياردير","مليونير","شركة","مصنع","سيارة","يخت","قصر","نجح","طبيب","مهندس","دكتور","اشترى","تزوج","اميرة"]):
            return ["luxury success billionaire car","business success celebration","happy success man"]
        return ["happy success ending","celebration success"]

def fetch_real_video(queries):
    # 1 - Pexels (أفضل - ناس حقيقية)
    for q in queries:
        try:
            if PEXELS_KEY and len(PEXELS_KEY)>20:
                headers={"Authorization": PEXELS_KEY}
                r=requests.get(f"https://api.pexels.com/videos/search?query={q}&per_page=10&size=small&orientation=portrait", headers=headers, timeout=12)
                if r.status_code==200:
                    data=r.json()
                    vids=data.get("videos",[])
                    random.shuffle(vids)
                    for v in vids:
                        # خد فيديو عمودي او مربع
                        for f in sorted(v.get("video_files",[]), key=lambda x: x.get("width",0)):
                            if 500 <= f.get("width",0) <= 1300 and f.get("link","").startswith("https"):
                                print(f"PEXELS OK {q} -> {f['width']}")
                                return f["link"]
        except Exception as e:
            print(f"pexels err {e}")

    # 2 - Pixabay مع فلتر قوي ضد كوريا وانيميشن
    for q in queries:
        try:
            r=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=15&video_type=film", timeout=12).json()
            hits=r.get("hits",[])
            random.shuffle(hits)
            for h in hits:
                tags=(h.get("tags","")+h.get("type","")).lower()
                if any(b in tags for b in ["animation","cartoon","illustration","korea","timelapse","aerial","drone"]):
                    continue
                if 3 <= h.get("duration",0) <= 20:
                    return h["videos"]["small"]["url"]
        except: pass

    # 3 - Fallback مضمون ومتناسق - من كود ويب
    fb=[
        "https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4",
        "https://cdn.pixabay.com/video/2022/10/23/136272-763624442_small.mp4",
        "https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4",
        "https://cdn.pixabay.com/video/2020/05/25/40128-424930862_small.mp4",
    ]
    return random.choice(fb)

def make_arabic_png(text, out_path):
    # كود ويب مجرب - PIL + reshaper + bidi - عربي صحيح 100%
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        from PIL import Image, ImageDraw, ImageFont
        # قص النص 65 حرف
        txt=text[:68].strip()
        reshaped=arabic_reshaper.reshape(txt)
        bidi_text=get_display(reshaped)

        W,H=700,155
        img=Image.new('RGBA', (W,H), (0,0,0,185))
        draw=ImageDraw.Draw(img)
        try:
            font=ImageFont.truetype("Amiri.ttf", 41)
        except:
            font=ImageFont.load_default()

        # حساب الوسط
        bbox=draw.textbbox((0,0), bidi_text, font=font)
        tw=bbox[2]-bbox[0]; th=bbox[3]-bbox[1]
        x=(W-tw)//2; y=(H-th)//2
        # ظل + نص ابيض
        draw.text((x+2,y+2), bidi_text, font=font, fill=(0,0,0,255))
        draw.text((x,y), bidi_text, font=font, fill=(255,255,255,255))
        img.save(out_path)
        return True
    except Exception as e:
        print(f"arabic png err {e}")
        # صورة سوداء فاضية
        try:
            subprocess.run(f'ffmpeg -y -f lavfi -i color=c=black@0.7:s=700x155 -frames:v 1 {out_path}', shell=True, timeout=5)
            return True
        except: return False

async def viral_girl_voice(text, out_mp3):
    # نفس صوت شورت يوتيوب - بنت
    t=text.strip()
    # وقفات درامية
    t=t.replace(" كان "," كان... ").replace(" حتى "," حتى... ").replace(" لكن "," لكن... ").replace(" لم "," لم... ").replace("،","... ")
    try:
        import edge_tts
        # أصوات بنات فايرال من مايكروسوفت
        for voice in ["ar-SA-ZariyahNeural","ar-EG-SalmaNeural"]:
            try:
                communicate=edge_tts.Communicate(t, voice, rate="+12%", pitch="+2Hz", volume="+10%")
                await communicate.save(out_mp3)
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3)>2500:
                    fx=out_mp3.replace(".mp3","_f.mp3")
                    # مؤثرات الفايرال: تسريع خفيف + ضغط + صدى
                    subprocess.run(f'ffmpeg -y -i {out_mp3} -af "atempo=1.06,loudnorm=I=-14:TP=-1:LRA=11,acompressor=threshold=-20dB:ratio=3,aecho=0.8:0.88:7:0.28" -b:a 96k {fx}', shell=True, timeout=15)
                    if os.path.exists(fx) and os.path.getsize(fx)>2000:
                        os.replace(fx,out_mp3)
                    print(f"VOICE OK {voice}")
                    return True
            except Exception as e:
                print(f"voice {voice} err {e}")
                continue
    except Exception as e:
        print(f"edge err {e}")

    # fallback gTTS
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar', slow=False).save(out_mp3)
        return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","3",out_mp3], timeout=8)
        return True

def build(cid, parts):
    try:
        for f in glob.glob("s_*")+glob.glob("a_*")+glob.glob("p_*")+glob.glob("t_*")+["FINAL.mp4","list.txt"]:
            try: os.remove(f)
            except: pass
        setup()

        clips=[]; durs=[3,7,9]
        for i in range(3):
            sv=f"s_{i}.mp4"; sa=f"a_{i}.mp3"; st=f"t_{i}.png"; pv=f"p_{i}.mp4"
            tg(cid,f"🧠 {i+1}/3: {parts[i][:22]}...")

            qs=story_to_queries(parts[i], i)
            url=fetch_real_video(qs)
            tg(cid,f"🎬 فيديو حقيقي {i+1}/3")
            if not download(url, sv):
                # حاول ثاني
                url=fetch_real_video([qs[0]+" real people"])
                download(url, sv)

            tg(cid,f"🎙️ بنت فايرال {i+1}/3")
            loop2=asyncio.new_event_loop()
            asyncio.set_event_loop(loop2)
            loop2.run_until_complete(viral_girl_voice(parts[i], sa))
            loop2.close()

            tg(cid,f"📝 نص صحيح {i+1}/3")
            make_arabic_png(parts[i], st)

            # دمج - فيديو + صوت + نص كصورة (مش drawtext)
            cmd=f'ffmpeg -y -i {sv} -i {sa} -i {st} -filter_complex "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.08:saturation=1.22[bg];[bg][2]overlay=(W-w)/2:H-h-35:format=auto:shortest=1" -t {durs[i]} -c:v libx264 -preset ultrafast -crf 26 -b:v 1300k -c:a aac -b:a 96k -shortest {pv}'
            subprocess.run(cmd, shell=True, check=True, timeout=100)
            clips.append(pv)
            tg(cid,f"✅ {i+1}/3 جاهز")

        tg(cid,"🔗 دمج نهائي")
        with open("list.txt","w") as f:
            for c in clips: f.write(f"file '{c}'\n")
        subprocess.run("ffmpeg -y -f concat -safe 0 -i list.txt -c copy FINAL.mp4", shell=True, check=True, timeout=25)

        with open("FINAL.mp4","rb") as v:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V58 WEB FINAL\n✅ Pexels حقيقي - مش كوريا\n✅ عربي صحيح PIL\n🎙️ بنت فايرال Zariyah\n🧠 أي قصة"},
                files={"video":v}, timeout=130)
        tg(cid,"🏆 V58 جاهز - مش ربش ✅")

    except Exception as e:
        tg(cid,f"❌ خطأ: {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان طفل فقير يبكي في الشارع|ساعدته سيدة غنية وربته|أصبح طبيبا وعالجها"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text(f"🏆 V58 FINAL WEB\n🧠 يفهم أي قصة\n🎬 Pexels حقيقي\n📝 عربي صحيح\n🎙️ بنت فايرال\n\nأرسل: /ظل جزء1|جزء2|جزء3")
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
def home(): return "V58 FINAL - PEXELS+PIL+VIRAL"
@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data=request.get_json(force=True)
        upd=Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(upd))
    except: pass
    return 'ok'

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if WEBHOOK_URL:
        try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=10)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
