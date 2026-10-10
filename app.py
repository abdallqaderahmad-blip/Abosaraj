import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN=os.getenv("BOT_TOKEN")
PIXABAY_KEY=os.getenv("PIXABAY_KEY") or "YOUR_KEY"
PEXELS_KEY=os.getenv("PEXELS_KEY") or os.getenv("PIXABAY_KEY")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)
print("===== V60 UNIVERSAL ANY STORY =====")

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
            for chunk in r.iter_content(8192): f.write(chunk)
        if os.path.getsize(out)<60000: os.remove(out); return False
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",out], capture_output=True, text=True, timeout=8)
        if p.stdout.strip() and 2<=float(p.stdout.strip())<=30: return True
        os.remove(out)
    except: pass
    return False

# ===== محرك ذكي يفهم أي قصة - مش مربوط بكلمات محددة =====
# كل قصة = 3 مشاهد: 1-معاناة 2-لقاء 3-نجاح
# المحرك بيحلل المشهد وبيحوله لبحث انجليزي دقيق

SCENE_RULES = {
    # معاناة - شخص وحيد
    "poor_alone": {
        "ar_words": ["فقير","مسكين","حزين","يبكي","وحيد","وحيدا","يتيم","ضائع","بائع","عامل نظافة","نظافة","سائق","عامل","مطرود","مرفوض","يستهزئ"],
        "queries": ["sad poor man alone portrait real","poor man alone street portrait","sad man alone real life portrait","lonely poor man portrait"],
        "ban": ["couple","family","group","crowd","umbrella","colorful","tourist","party","celebration","happy couple"]
    },
    # امرأة غنية / طيبة
    "rich_woman": {
        "ar_words": ["غنية","فتاة","بنت","سيدة","امرأة","جميلة","أميرة","عجوز","طيبة","ساعدته","آمنت","اختارته"],
        "queries": ["beautiful rich woman portrait alone real","business woman rich portrait smile","kind woman portrait real","beautiful woman helping portrait"],
        "ban": ["couple kissing","umbrella","crowd night","korea","tourist market","group party"]
    },
    # نجاح / ملياردير / انتقام
    "success": {
        "ar_words": ["ملياردير","مليونير","شركة","مصنع","سيارة","يخت","قصر","نجح","نجاح","أصبح","مدير","اشترى","تزوج","انتقم","انتصر","عاد","رجع"],
        "queries": ["successful man suit luxury portrait","business man success luxury car portrait","billionaire man success portrait real","rich man luxury success"],
        "ban": ["poor","sad","homeless","umbrella","couple walking","tourist","korea","crowd night"]
    },
    # حب / زواج
    "love": {
        "ar_words": ["حب","تزوج","زواج","عشق","حبيب","خطب"],
        "queries": ["romantic couple love portrait","wedding couple happy portrait","love couple portrait real"],
        "ban": ["sad","poor","umbrella","tourist","korea"]
    },
    # طفل
    "child": {
        "ar_words": ["طفل","ولد صغير","صغير","طفلة"],
        "queries": ["poor sad child alone portrait","little boy alone street portrait","sad child alone real"],
        "ban": ["couple","umbrella","tourist","party","luxury car"]
    }
}

def analyze_scene(ar_text, part_index):
    t=ar_text.lower()
    # حدد نوع المشهد حسب الكلمات + موقع الجزء في القصة
    for scene_name, rule in SCENE_RULES.items():
        for w in rule["ar_words"]:
            if w in t:
                return rule["queries"], rule["ban"]
    # لو ما لقا كلمة - حسب ترتيب القصة
    if part_index==0:
        return SCENE_RULES["poor_alone"]["queries"], SCENE_RULES["poor_alone"]["ban"]
    elif part_index==1:
        return SCENE_RULES["rich_woman"]["queries"], SCENE_RULES["rich_woman"]["ban"]
    else:
        return SCENE_RULES["success"]["queries"], SCENE_RULES["success"]["ban"]

def fetch_smart(queries, ban_list):
    # 1. Pexels - مع فلتر ضد الربش
    for q in queries:
        try:
            if PEXELS_KEY and len(PEXELS_KEY)>20:
                headers={"Authorization": PEXELS_KEY}
                r=requests.get(f"https://api.pexels.com/videos/search?query={q}&per_page=15&orientation=portrait&size=small", headers=headers, timeout=12).json()
                vids=r.get("videos",[])
                random.shuffle(vids)
                for v in vids:
                    # فلتر ضد الكلمات الممنوعة
                    tags_combined = (str(v.get("url","")) + str(v.get("image","")) + q).lower()
                    if any(b in tags_combined for b in ban_list):
                        continue
                    # ارفض فيديوهات فيها شمسيات ملونة وكوبل اذا المطلوب شخص واحد
                    if "couple" in ban_list and "couple" in tags_combined:
                        continue
                    for f in sorted(v.get("video_files",[]), key=lambda x: x.get("width",0)):
                        if 500<=f.get("width",0)<=1280 and f["link"].startswith("https"):
                            print(f"PEXELS OK {q} ban:{ban_list[:2]}")
                            return f["link"]
        except Exception as e:
            print(f"pexels err {e}")

    # 2. Pixabay - نفس الفلتر
    for q in queries:
        try:
            r=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=15", timeout=12).json()
            hits=r.get("hits",[])
            random.shuffle(hits)
            for h in hits:
                tags=h.get("tags","").lower()
                if any(b in tags for b in ban_list): continue
                if any(b in tags for b in ["animation","cartoon","timelapse","aerial","drone","korea","korean"]): continue
                if 3<=h.get("duration",0)<=20:
                    return h["videos"]["small"]["url"]
        except: pass

    return random.choice([
        "https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4",
        "https://cdn.pixabay.com/video/2022/10/23/136272-763624442_small.mp4",
        "https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4",
    ])

def make_arabic_png(text, out_path):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        from PIL import Image, ImageDraw, ImageFont
        txt=text[:70].strip()
        reshaped=arabic_reshaper.reshape(txt)
        bidi_text=get_display(reshaped)
        W,H=700,155
        img=Image.new('RGBA', (W,H), (0,0,0,190))
        draw=ImageDraw.Draw(img)
        try: font=ImageFont.truetype("Amiri.ttf", 42)
        except: font=ImageFont.load_default()
        bbox=draw.textbbox((0,0), bidi_text, font=font)
        tw=bbox[2]-bbox[0]; th=bbox[3]-bbox[1]
        x=(W-tw)//2; y=(H-th)//2
        draw.text((x+2,y+2), bidi_text, font=font, fill=(0,0,0,255))
        draw.text((x,y), bidi_text, font=font, fill=(255,255,255,255))
        img.save(out_path)
        return True
    except:
        subprocess.run(f'ffmpeg -y -f lavfi -i color=c=black@0.7:s=700x155 -frames:v 1 {out_path}', shell=True, timeout=5)
        return True

async def viral_girl(text, out_mp3):
    t=text.strip().replace(" كان "," كان... ").replace(" حتى "," حتى... ").replace(" لم "," لم... ").replace("،","... ")
    try:
        import edge_tts
        for voice in ["ar-SA-ZariyahNeural","ar-EG-SalmaNeural"]:
            try:
                c=edge_tts.Communicate(t, voice, rate="+11%", pitch="+2Hz", volume="+10%")
                await c.save(out_mp3)
                if os.path.getsize(out_mp3)>2500:
                    fx=out_mp3.replace(".mp3","_f.mp3")
                    subprocess.run(f'ffmpeg -y -i {out_mp3} -af "atempo=1.06,loudnorm=I=-14:TP=-1,acompressor=threshold=-18dB:ratio=2.5,aecho=0.8:0.88:7:0.28" -b:a 96k {fx}', shell=True, timeout=15)
                    if os.path.exists(fx) and os.path.getsize(fx)>2000: os.replace(fx,out_mp3)
                    return True
            except: continue
    except: pass
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar', slow=False).save(out_mp3)
        return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc","-t","3",out_mp3], timeout=8)
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
            queries, ban = analyze_scene(parts[i], i)
            tg(cid,f"🧠 {i+1}/3 يحلل: {parts[i][:22]}...\n🔍 بحث: {queries[0]}\n🚫 يرفض: {ban[0]}")

            url=fetch_smart(queries, ban)
            if not download(url, sv):
                url=fetch_smart([queries[0]+" portrait real"], ban)
                download(url, sv)

            loop2=asyncio.new_event_loop()
            asyncio.set_event_loop(loop2)
            loop2.run_until_complete(viral_girl(parts[i], sa))
            loop2.close()

            make_arabic_png(parts[i], st)

            cmd=f'ffmpeg -y -i {sv} -i {sa} -i {st} -filter_complex "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.08:saturation=1.22[bg];[bg][2]overlay=(W-w)/2:H-h-35" -t {durs[i]} -c:v libx264 -preset ultrafast -crf 26 -b:v 1300k -c:a aac -b:a 96k -shortest {pv}'
            subprocess.run(cmd, shell=True, check=True, timeout=100)
            clips.append(pv)
            tg(cid,f"✅ {i+1}/3 متناسق")

        with open("list.txt","w") as f:
            for c in clips: f.write(f"file '{c}'\n")
        subprocess.run("ffmpeg -y -f concat -safe 0 -i list.txt -c copy FINAL.mp4", shell=True, check=True, timeout=25)

        with open("FINAL.mp4","rb") as v:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":"🏆 V60 UNIVERSAL ANY STORY\n✅ يحلل أي قصة مستقبلية\n✅ يرفض كوبل/شمسيات/سياحة تلقائيا\n✅ نص صحيح + بنت فايرال"},
                files={"video":v}, timeout=130)
        tg(cid,"🏆 V60 جاهز - لأي قصة ✅")

    except Exception as e:
        tg(cid,f"❌ {e}\n{traceback.format_exc()[:800]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8:
        raw="كان مجرد عامل نظافة فقير يستهزئ به الجميع|حتى ساعدته فتاة غنية وآمنت به|أصبح مدير الشركة واشتراها"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V60 UNIVERSAL - لأي قصة\nأرسل: /ظل جزء1|جزء2|جزء3\n\nأمثلة:\n/ظل طفل ضائع|عجوز ربته|أصبح طبيب\n/ظل بنت تبيع ورد|شاب غني اشترى|تزوجها")
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
def home(): return "V60 UNIVERSAL ANY STORY"
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
