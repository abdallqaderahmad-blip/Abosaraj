import os, re, sys, json, time, uuid, asyncio, logging, threading, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote
import requests
from flask import Flask, jsonify, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "10000"))
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
MAX_ACTIVE_JOBS = 1
GROQ_RETRIES = 4
GROQ_MAX_TOKENS = 4000
IMAGE_GENERATION_TIMEOUT = 180
HMISC_DURATION = 6.5

DATA_ROOT = Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
BASE_DIR = DATA_ROOT / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = BASE_DIR / "jobs_state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ZIL")
app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-V83-TRUE/1.0"})
JOBS = {}
JOBS_LOCK = threading.RLock()
STATE_LOCK = threading.RLock()
ACTIVE_JOBS = 0

def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def read_json(path, default=None):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except: return default if default is not None else {}

def load_state():
    global JOBS
    with STATE_LOCK:
        loaded = read_json(STATE_FILE, {})
        JOBS = loaded if isinstance(loaded, dict) else {}
        for job in JOBS.values():
            if job.get("status") in ("queued","running","uploading"):
                job["status"]="interrupted"
        atomic_json(STATE_FILE, JOBS)

def save_state_locked(): atomic_json(STATE_FILE, JOBS)

def update_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job: return
        job.update(fields); job["updated_at"]=time.time()
        with STATE_LOCK: save_state_locked()

def run_command(command, timeout=300):
    result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode!=0:
        error = (result.stderr or result.stdout or "")[-3000:]
        raise RuntimeError(f"ffmpeg failed {result.returncode}: {error}")
    return result

def probe_duration(path):
    try:
        r = run_command(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],30)
        return float(r.stdout.strip())
    except: return 0.0

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN: return
    try: HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":chat_id,"text":str(text)[:4000]}, timeout=30)
    except: pass

def telegram_send_video(chat_id, path, caption):
    with open(path,"rb") as media:
        HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":chat_id,"caption":caption[:1000]}, files={"video":(Path(path).name,media,"video/mp4")}, timeout=(30,600))

def extract_json(text):
    text=(text or "").strip()
    text=re.sub(r"^```(?:json)?\s*","",text,flags=re.I); text=re.sub(r"\s*```$","",text)
    try: return json.loads(text)
    except:
        s=text.find("{"); e=text.rfind("}")
        if s>=0 and e>s: return json.loads(text[s:e+1])
        raise

def groq_chat(messages):
    if not GROQ_API_KEY: raise RuntimeError("GROQ_API_KEY missing")
    headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}
    payload={"model":GROQ_MODEL,"messages":messages,"temperature":0.85,"max_tokens":GROQ_MAX_TOKENS,"response_format":{"type":"json_object"}}
    for attempt in range(GROQ_RETRIES):
        try:
            r=HTTP.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
            if r.status_code==429: time.sleep(5*(attempt+1)); continue
            r.raise_for_status()
            return extract_json(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            time.sleep(min(10,2**attempt))
    raise RuntimeError("Groq failed")

# ========= HMISC TRUE =========
def generate_hmisc_true_ideas(count=5):
    system = """You are hmisc creator. Return JSON {"ideas":[{ "text_ar": "سطرين عربي قصير مؤثر", "visual_top": "description top illusion English", "visual_bottom": "description bottom reality English", "emotion": "anxiety"}]}
    Style: yellow simple character #FFC83D, parchment #F5E6C8, split top/bottom illusion vs reality. Examples:
    1- drowning vs sitting shallow
    2- two carrots one big leaves small root vs opposite
    3- alone in dark vs surrounded by lights
    Keep text_ar 1-3 lines max, emotional Islamic wisdom."""
    user = f"Create {count} viral hmisc ideas, same style as Instagram hmisc"
    try:
        data=groq_chat([{"role":"system","content":system},{"role":"user","content":user}])
        ideas=data.get("ideas",[])
        if len(ideas)>=1: return ideas[:count]
    except Exception as e: log.warning(f"groq ideas fail {e}")

    return [
        {"text_ar": "لا تصنع هموماً\nلم يكتبها الله عليك", "visual_top":"yellow character drowning in deep ocean hands up panicking, water to neck illusion", "visual_bottom":"same yellow character sitting calm on sandy seabed shallow water only to knees, little bubbles", "emotion":"anxiety"},
        {"text_ar": "لا تنظر إلى ما في أيدي الناس،\nفالله أعلم بما يصلح لك،\nوما أخذه عنك لحكمة،\nوما كتبه لك سيأتيك", "visual_top":"two characters, two carrots with big green leaves above ground but roots hidden", "visual_bottom":"cross section underground, first carrot tiny root, second carrot huge orange root, surprise", "emotion":"comparison"},
        {"text_ar": "لست وحدك\nحتى في أحلك اللحظات", "visual_top":"yellow character alone in dark forest night, scared", "visual_bottom":"same character surrounded by fireflies lights forming hearts, warm", "emotion":"loneliness"},
        {"text_ar": "طريقك ليس متأخراً\nبل مختلفاً", "visual_top":"crowd rushing busy city street, stress", "visual_bottom":"one yellow character walking alone peaceful foggy mountain road, calm sunrise", "emotion":"patience"},
        {"text_ar": "كل هذا سيمر\nكما مر غيره", "visual_top":"stormy ocean big waves dark", "visual_bottom":"same ocean calm sunrise peaceful", "emotion":"hope"},
    ][:count]

def pollinations_generate_image(prompt, destination, seed):
    url = POLLINATIONS_IMAGE_URL + quote(prompt, safe="")
    params={"width":768,"height":1365,"seed":int(seed),"nologo":"true","safe":"true","model":"flux","enhance":"true"}
    r=HTTP.get(url, params=params, timeout=(20,IMAGE_GENERATION_TIMEOUT))
    r.raise_for_status()
    Path(destination).write_bytes(r.content)
    return destination

def build_true_prompt(idea):
    # prompt مطابق لـ hmisc
    top = idea.get("visual_top","")
    bottom = idea.get("visual_bottom","")
    return f"Split screen image top and bottom, top half: {top}, bottom half: {bottom}, soft watercolor illustration, parchment paper texture old #F5E6C8, yellow character #FFC83D simple cute round, storybook style, highly detailed, no text, no watermark, vertical 9:16"

def obtain_visual_true(idea, workdir, index, duration=6.5):
    raw = workdir / f"true_raw_{index}.jpg"
    clean = workdir / f"true_clean_{index}.png"
    video = workdir / f"true_video_{index}.mp4"
    if not clean.exists():
        if not raw.exists():
            prompt = build_true_prompt(idea)
            pollinations_generate_image(prompt, raw, seed=2000+index*137)
        # قص العلامة المائية pollinations.ai 90px من تحت + تكبير
        run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-vf","crop=iw:ih-90:0:0,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",str(clean)],60)
    if not video.exists():
        frames=int(duration*VIDEO_FPS)
        # زوم بطيء جدا مثل انستا
        vf=f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.00025,1.035))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=720x1280:fps=24,trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p"
        run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-framerate",str(VIDEO_FPS),"-i",str(clean),"-vf",vf,"-t",f"{duration:.3f}","-an","-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-r",str(VIDEO_FPS),str(video)],200)
    return video, clean

def add_text_overlay_true(source, text_ar, destination, workdir, index, duration):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    W,H=720,1280
    overlay=Image.new("RGBA",(W,H),(0,0,0,0))
    draw=ImageDraw.Draw(overlay)
    ar_bold="/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"
    try: font=ImageFont.truetype(ar_bold,40)
    except: font=ImageFont.load_default()
    def fix(t):
        try:
            return get_display(arabic_reshaper.reshape(t.strip()))
        except: return t
    lines=[l.strip() for l in text_ar.split("\n") if l.strip()]
    y=90
    for line in lines:
        txt=fix(line)
        bbox=draw.textbbox((0,0),txt,font=font)
        w=bbox[2]-bbox[0]
        # ظل خفيف ورا النص عشان يوضح
        draw.text(((W-w)//2+2, y+2), txt, font=font, fill=(245,230,200,180))
        draw.text(((W-w)//2, y), txt, font=font, fill=(35,22,10,255))
        y+=58
    png=workdir/f"text_{index}.png"
    overlay.save(png)
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-loop","1","-framerate",str(VIDEO_FPS),"-i",str(png),"-filter_complex","[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]","-map","[v]","-t",f"{duration:.3f}","-an","-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-r",str(VIDEO_FPS),str(destination)],120)
    return destination

def prepare_music_piano(destination, duration, emotion="anxiety"):
    # موسيقى بيانو هادئة مثل Ramin Djawadi Hold The Door
    # لو عندك ملف piano.mp3 حطه بجانب app.py واستخدمه، وإلا ولد صوت هادئ
    # توليد lofi piano بسيط
    if emotion=="anxiety":
        f1, f2 = 110, 220
    elif emotion=="hope":
        f1, f2 = 196, 247
    else:
        f1, f2 = 130, 196
    filt=f"[0:a]volume=0.18,lowpass=f=800,afade=t=in:d=0.7,afade=t=out:st={max(0.1,duration-1.2):.3f}:d=1.2,atrim=duration={duration:.3f}[out]"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"sine=frequency={f1}:duration={duration}","-filter_complex",filt,"-map","[out]","-ar","44100","-ac","2","-c:a","aac","-b:a","64k",str(destination)],60)
    return destination

def create_hmisc_video_true(job_id, idea, workdir, index):
    duration=HMISC_DURATION
    visual,_=obtain_visual_true(idea, workdir, index, duration)
    captioned=workdir/f"captioned_{index}.mp4"
    add_text_overlay_true(visual, idea.get("text_ar","رسالة"), captioned, workdir, index, duration)
    music=workdir/f"music_{index}.m4a"
    prepare_music_piano(music, duration, idea.get("emotion","anxiety"))
    final=workdir/f"HMISC_{index+1}.mp4"
    # دمج فيديو + موسيقى فقط (بدون تعليق)
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(captioned),"-i",str(music),"-c:v","copy","-map","0:v:0","-map","1:a:0","-shortest","-movflags","+faststart",str(final)],120)
    return final

def hmisc_job_worker(job_id, chat_id, user_id, ideas, series_id):
    global ACTIVE_JOBS
    try:
        update_job(job_id, status="running", stage="hmisc_true", progress=1, total=len(ideas))
        telegram_send_message(chat_id, f"✨ سلسلة hmisc الحقيقية: {len(ideas)} فيديو\nالسلسلة: {series_id}\nبدون علامة مائية، نص صحيح، موسيقى بيانو")
        outputs=[]
        for idx, idea in enumerate(ideas):
            workdir = BASE_DIR / f"{job_id}_{idx}"
            workdir.mkdir(parents=True, exist_ok=True)
            telegram_send_message(chat_id, f"🎨 {idx+1}/{len(ideas)}: {idea.get('text_ar','')[:40]}")
            final = create_hmisc_video_true(job_id, idea, workdir, idx)
            outputs.append(str(final))
            try:
                telegram_send_video(chat_id, final, f"{idea.get('text_ar','')}")
            except Exception as e:
                log.error(f"send fail {e}")
            update_job(job_id, progress=int((idx+1)/len(ideas)*95), outputs=outputs)
        update_job(job_id, status="completed", progress=100, outputs=outputs)
        telegram_send_message(chat_id, f"🎉 اكتملت السلسلة {len(ideas)} فيديو - مطابقة لـ hmisc")
    except Exception as exc:
        log.exception(f"hmisc true fail")
        update_job(job_id, status="failed", error=str(exc)[:2000])
        telegram_send_message(chat_id, f"❌ فشل: {exc}")
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS=max(0,ACTIVE_JOBS-1)
            with STATE_LOCK: save_state_locked()

async def launch_hmisc_series(update, context, count=5):
    global ACTIVE_JOBS
    msg=update.effective_message; chat=update.effective_chat; user=update.effective_user
    if not msg or not chat or not user: return
    with JOBS_LOCK:
        if ACTIVE_JOBS>=MAX_ACTIVE_JOBS:
            await msg.reply_text("⏳ يوجد عمل قيد التنفيذ"); return
        ACTIVE_JOBS+=1
        job_id=f"hmisc_{uuid.uuid4().hex[:8]}"
        series_id=f"series_{uuid.uuid4().hex[:6]}"
        JOBS[job_id]={"id":job_id,"user_id":user.id,"chat_id":chat.id,"status":"queued","progress":0,"created_at":time.time(),"updated_at":time.time(),"type":"hmisc_true","series_id":series_id,"count":count}
        with STATE_LOCK: save_state_locked()
    await msg.reply_text(f"✨ جاري تفكير العقل {count} أفكار بأسلوب hmisc الحقيقي\nالسلسلة: {series_id}\n(مقسم فوق وهم / تحت حقيقة)")
    def gen_and_start():
        global ACTIVE_JOBS
        try:
            ideas=generate_hmisc_true_ideas(count=count)
            threading.Thread(target=hmisc_job_worker, args=(job_id, chat.id, user.id, ideas, series_id), daemon=True).start()
        except Exception as e:
            with JOBS_LOCK: ACTIVE_JOBS=max(0,ACTIVE_JOBS-1)
            telegram_send_message(chat.id, f"❌ فشل توليد: {e}")
    threading.Thread(target=gen_and_start, daemon=True).start()

async def cmd_hmisc(update, context):
    args=context.args; count=5
    if args:
        try: count=int(args[0])
        except: pass
    count=max(1,min(10,count))
    await launch_hmisc_series(update, context, count=count)

async def cmd_start(update, context):
    await update.effective_message.reply_text("🎬 ZIL V83 TRUE HMISC\n/hmisc 5 = 5 فيديو مطابق لـ hmisc (مقسم فوق/تحت، نص صحيح، بدون علامة مائية، بيانو هادئ)")

async def error_handler(update, context):
    log.error(f"error {context.error}", exc_info=context.error)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(f"⚠️ خطأ: {context.error}")
    except: pass

@app.route("/")
def home(): return jsonify({"status":"ZIL V83 TRUE HMISC","hmisc_duration":HMISC_DURATION,"active":ACTIVE_JOBS})

@app.route("/health")
def health(): return jsonify({"ok":True,"groq":bool(GROQ_API_KEY),"bot":bool(BOT_TOKEN),"active":ACTIVE_JOBS})

def run_flask(): app.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    global ACTIVE_JOBS
    load_state(); ACTIVE_JOBS=0
    threading.Thread(target=run_flask, daemon=True).start()
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN missing")
    if not GROQ_API_KEY: raise RuntimeError("GROQ_API_KEY missing")
    application=(Application.builder().token(BOT_TOKEN).build())
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("hmisc", cmd_hmisc))
    application.add_error_handler(error_handler)
    log.info("ZIL V83 TRUE started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
