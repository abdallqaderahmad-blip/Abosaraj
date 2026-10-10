import os, re, uuid, shutil, logging, asyncio, threading, subprocess
from pathlib import Path
import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zil")
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PEXELS_KEY = (os.getenv("PEXELS_KEY") or "").strip()
PORT = int(os.getenv("PORT", "10000"))
SCENES, SECONDS, W, H, FPS = 6, 5, 720, 1280, 25
TMP = Path("/tmp/zil")
TMP.mkdir(parents=True, exist_ok=True)
busy, busy_lock = set(), threading.Lock()
flask_app = Flask(__name__)
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

def tg(method, data=None, files=None, timeout=30):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", data=data, files=files, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")
    return payload

def say(chat_id, text):
    try: tg("sendMessage", {"chat_id": chat_id, "text": text}, timeout=15)
    except Exception: log.exception("Telegram message failed")

def ffmpeg(args, timeout=60):
    p = subprocess.run(list(map(str, args)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode: raise RuntimeError("FFmpeg: " + p.stderr[-700:])
    return p

def get_font():
    for p in [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
        if p.exists() and p.stat().st_size > 10000: return str(p)
    p = Path("/tmp/Amiri-Bold.ttf")
    try:
        r = requests.get("https://raw.githubusercontent.com/google/fonts/main/ofl/amiri/Amiri-Bold.ttf", timeout=15)
        if r.status_code == 200 and len(r.content) > 10000:
            p.write_bytes(r.content); return str(p)
    except Exception: log.exception("Font download failed")
    return None

def parse_story(raw):
    parts = [re.sub(r"\s+", " ", x).strip() for x in raw.split("|")]
    parts = [x for x in parts if x]
    if len(parts) < 2: raise ValueError("اكتب القصة بثلاثة أجزاء وافصل بينها بعلامة |")
    parts = parts[:3]
    while len(parts) < 3: parts.append(parts[-1])
    return parts

def scene_texts(parts):
    words = "، ".join(parts).split() or ["قصة", "غامضة"]
    out = []
    for i in range(SCENES):
        a, b = round(i * len(words) / SCENES), round((i + 1) * len(words) / SCENES)
        out.append(" ".join(words[a:b]) or words[-1])
    return out

def search_terms(text, i):
    t, q = text.lower(), []
    if any(x in t for x in ["ملك","قصر","امير","أمير","اميرة","أميرة","عرش","مملكة"]): q += ["medieval castle cinematic", "royal throne room", "medieval warrior dramatic"]
    if any(x in t for x in ["نمر","اسد","أسد","وحش","ذئب","قتال","معركة","خطر"]): q += ["tiger close up wildlife", "wild animal dramatic", "warrior fighting cinematic"]
    if any(x in t for x in ["حب","تحب","اميرة","أميرة","فتاة","امرأة","بنت"]): q += ["woman dramatic portrait", "elegant woman cinematic", "man and woman dramatic scene"]
    if any(x in t for x in ["قوة","قوي","سر","غامض","خارق","بطل"]): q += ["mysterious man cinematic portrait", "strong man dramatic close up", "man silhouette smoke"]
    if any(x in t for x in ["فقير","يتيم","جائع","خبز","شارع","عامل"]): q += ["lonely man city street cinematic", "poor man walking street", "emotional man portrait"]
    q += [["cinematic mysterious man", "dark castle cinematic"], ["cinematic woman portrait", "foggy forest cinematic"], ["dramatic man portrait", "stormy sky cinematic"]][i % 3]
    return list(dict.fromkeys(q))[:6]

def pexels_search(queries):
    if not PEXELS_KEY: return None
    for q in queries:
        try:
            r = requests.get("https://api.pexels.com/videos/search", params={"query":q,"per_page":8,"orientation":"portrait","size":"small"}, headers={"Authorization":PEXELS_KEY}, timeout=15)
            if r.status_code != 200:
                log.warning("Pexels status %s", r.status_code); continue
            for video in r.json().get("videos", []):
                files = sorted(video.get("video_files", []), key=lambda x: abs((x.get("width") or 720)-720))
                for f in files:
                    if f.get("link", "").startswith("https://") and 320 <= (f.get("width") or 0) <= 1920: return f["link"]
        except Exception: log.exception("Pexels search failed")
    return None

def download_clip(url, out, limit=25_000_000):
    tmp, total = out.with_suffix(".part"), 0
    try:
        with requests.get(url, stream=True, timeout=(10,35), headers={"User-Agent":"Mozilla/5.0"}) as r:
            if r.status_code != 200: return False
            with tmp.open("wb") as f:
                for chunk in r.iter_content(65536):
                    if not chunk: continue
                    total += len(chunk)
                    if total > limit: break
                    f.write(chunk)
        if total <= limit and tmp.exists() and tmp.stat().st_size > 80000:
            tmp.replace(out); return True
    except Exception: log.exception("Clip download failed")
    tmp.unlink(missing_ok=True); return False

def fallback_clip(out, i):
    colors = ["0x101827","0x21152b","0x17242b","0x2b1b1b","0x101a2b","0x251c31"]
    ffmpeg(["ffmpeg","-y","-f","lavfi","-i",f"color=c={colors[i%len(colors)]}:s={W}x{H}:r={FPS}:d={SECONDS}","-vf","format=yuv420p","-t",str(SECONDS),"-r",str(FPS),"-an","-c:v","libx264","-preset","ultrafast","-crf","28",str(out)],25)

def caption_png(text, out, font):
    from PIL import Image, ImageDraw, ImageFont
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        text = get_display(arabic_reshaper.reshape(text[:100]))
    except Exception: text = text[:100]
    wp, hp = 680, 230
    try: f = ImageFont.truetype(font, 35) if font else ImageFont.load_default()
    except Exception: f = ImageFont.load_default()
    img = Image.new("RGBA", (wp,hp), (0,0,0,0)); d = ImageDraw.Draw(img)
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if d.textlength(trial, font=f) > wp-30 and line: lines.append(line); line = word
        else: line = trial
    if line: lines.append(line)
    lines = lines[:3] or [text]
    ph = len(lines)*47+25
    panel = Image.new("RGBA", (wp,ph), (0,0,0,175)); pd = ImageDraw.Draw(panel)
    for n, line in enumerate(lines):
        box = pd.textbbox((0,0), line, font=f, stroke_width=1); x = (wp-(box[2]-box[0]))//2
        pd.text((x,10+n*47), line, font=f, fill="white", stroke_width=1, stroke_fill="black")
    img.alpha_composite(panel, (0,hp-ph)); img.save(out)

async def make_voice(text, out):
    try:
        import edge_tts
        await edge_tts.Communicate(text, "ar-SA-ZariyahNeural", rate="-8%", pitch="+0Hz").save(str(out))
        if out.exists() and out.stat().st_size > 1500: return
    except Exception: log.exception("Edge TTS failed")
    try:
        from gtts import gTTS
        gTTS(text=text, lang="ar").save(str(out))
        if out.exists() and out.stat().st_size > 1000: return
    except Exception: log.exception("gTTS failed")
    raise RuntimeError("تعذر إنشاء الصوت العربي. تحقق من اتصال الخدمة.")

def build_scene(raw, png, out, i):
    scaled = out.with_name(f"scaled_{i}.mp4")
    ffmpeg(["ffmpeg","-y","-i",str(raw),"-vf",f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=contrast=1.08:saturation=0.92:brightness=-0.02,format=yuv420p","-t",str(SECONDS),"-r",str(FPS),"-an","-c:v","libx264","-preset","ultrafast","-crf","25",str(scaled)],45)
    ffmpeg(["ffmpeg","-y","-i",str(scaled),"-i",str(png),"-filter_complex","[0:v][1:v]overlay=(W-w)/2:H-h-70:format=auto","-t",str(SECONDS),"-an","-c:v","libx264","-preset","ultrafast","-crf","25","-pix_fmt","yuv420p",str(out)],45)
    scaled.unlink(missing_ok=True)

def make_video(chat_id, parts):
    wd = TMP / str(uuid.uuid4()); wd.mkdir(parents=True, exist_ok=True)
    try:
        say(chat_id,"🎬 ظل بدأ تجهيز فيديو عمودي من 6 مشاهد باستخدام الأدوات المربوطة حاليًا فقط.")
        font = get_font(); captions = scene_texts(parts); clips = []
        for i, caption in enumerate(captions):
            say(chat_id, f"🔎 تجهيز المشهد {i+1}/{SCENES}…")
            raw, png, out = wd/f"raw{i}.mp4", wd/f"caption{i}.png", wd/f"scene{i}.mp4"
            beat = parts[min(i//2,2)]; url = pexels_search(search_terms(beat+" "+caption,i))
            if not (url and download_clip(url,raw)): fallback_clip(raw,i)
            caption_png(caption,png,font); build_scene(raw,png,out,i); clips.append(out)
        concat = wd/"concat.txt"
        with concat.open("w",encoding="utf-8") as f:
            for p in clips: f.write("file '"+str(p.resolve())+"'\n")
        silent = wd/"silent.mp4"
        ffmpeg(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c:v","libx264","-preset","ultrafast","-crf","25","-pix_fmt","yuv420p","-r",str(FPS),"-an",str(silent)],90)
        voices=[]
        for i, txt in enumerate(captions):
            voice=wd/f"voice{i}.mp3"; asyncio.run(make_voice(txt,voice)); voices.append(voice)
        audio_list=wd/"audio.txt"
        with audio_list.open("w",encoding="utf-8") as f:
            for p in voices: f.write("file '"+str(p.resolve())+"'\n")
        audio=wd/"narration.mp3"
        ffmpeg(["ffmpeg","-y","-f","concat","-safe","0","-i",str(audio_list),"-c:a","libmp3lame","-b:a","128k",str(audio)],60)
        final=wd/"ZIL_FINAL.mp4"
        ffmpeg(["ffmpeg","-y","-i",str(silent),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","128k","-t",str(SCENES*SECONDS),"-movflags","+faststart",str(final)],60)
        if not final.exists() or final.stat().st_size < 80000: raise RuntimeError("ملف الفيديو النهائي غير صالح.")
        with final.open("rb") as v:
            tg("sendVideo",data={"chat_id":chat_id,"caption":("🎬 ظل | Micro Drama\n"+" | ".join(parts))[:900],"supports_streaming":"true"},files={"video":("zil_microdrama.mp4",v,"video/mp4")},timeout=180)
        say(chat_id,"✅ انتهى الفيديو: 30 ثانية، عمودي 9:16، 6 مشاهد، صوت عربي ونص عربي.")
        if not PEXELS_KEY: say(chat_id,"ℹ️ لم يتم ضبط PEXELS_KEY في Render؛ لذلك ستظهر خلفيات بديلة بدل لقطات Pexels.")
    except Exception as e:
        log.exception("Video job failed"); say(chat_id,"❌ فشل إنتاج الفيديو: "+str(e)[:600])
    finally:
        shutil.rmtree(wd,ignore_errors=True)
        with busy_lock: busy.discard(chat_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🎬 أهلاً بك في ظل.\n\nأرسل قصة من 3 مراحل:\n/ظل بداية القصة|تصاعد الأحداث|المفاجأة أو الخطر\n\nمثال:\n/ظل دخل رجل غامض إلى قصر الملك|رفض الملك زواجه من الأميرة|ظهر نمر ضخم فتقدم الرجل وكشف جزءاً من قوته\n\nينتج فيديو 30 ثانية من لقطات Pexels أو خلفيات بديلة، مع صوت عربي ونصوص على الشاشة. هذه النسخة لا تولّد ممثلين جددًا ولا توفر مزامنة شفاه.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"ظل يعمل.\nPexels key: {'موجود' if PEXELS_KEY else 'غير موجود'}\nالفيديو: 30 ثانية / 6 مشاهد\nالتوليد السينمائي بالذكاء الاصطناعي غير متاح في الأدوات الحالية.")

async def launch(update, parts):
    chat_id=update.effective_chat.id
    with busy_lock:
        if chat_id in busy:
            await update.effective_message.reply_text("⏳ يوجد فيديو قيد التجهيز لهذه المحادثة. انتظر حتى ينتهي."); return
        busy.add(chat_id)
    await update.effective_message.reply_text("🚀 استلمت القصة، بدأ تجهيز الفيديو.")
    threading.Thread(target=make_video,args=(chat_id,parts),daemon=True).start()

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await launch(update,["دخل رجل غامض إلى ساحة القصر وظن الجميع أنه ضعيف","سخر الملك منه ورفض أن يقترب من الأميرة التي أحبته","ظهر نمر هائل أمام الحراس فتقدم الرجل بهدوء وكأنه يخفي قوة مرعبة"])

async def zil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r"^/(?:ظل|zil)(?:@\w+)?\s*","",update.effective_message.text or "",flags=re.I).strip()
    try: parts=parse_story(raw)
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}\nالصيغة: /ظل جزء أول|جزء ثاني|جزء ثالث"); return
    await launch(update,parts)

bot=Application.builder().token(BOT_TOKEN).build()
bot.add_handler(CommandHandler("start",start)); bot.add_handler(CommandHandler("help",start))
bot.add_handler(CommandHandler("status",status)); bot.add_handler(CommandHandler("test",test))
bot.add_handler(CommandHandler(["zil","ظل"],zil))

@flask_app.get("/")
def home(): return "ZIL Micro Drama service is running."

@flask_app.get("/health")
def health():
    return jsonify({"status":"ok","project":"ZIL","version":"1.0","pexels_key":bool(PEXELS_KEY),"scenes":SCENES,"scene_seconds":SECONDS,"ai_video_generation":False})

def telegram_polling():
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",data={"drop_pending_updates":"true"},timeout=15)
    except Exception: log.exception("Could not clear old webhook")
    bot.run_polling(drop_pending_updates=True,close_loop=True)

if __name__ == "__main__":
    threading.Thread(target=telegram_polling,daemon=True,name="telegram-polling").start()
    flask_app.run(host="0.0.0.0",port=PORT,threaded=True,use_reloader=False)
