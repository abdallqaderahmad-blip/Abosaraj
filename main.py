import os, re, sys, json, time, uuid, logging, threading, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote
import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POLL_IMG = "https://image.pollinations.ai/prompt/"
PORT = int(os.getenv("PORT", "10000"))
FPS = 24
DURATION = 6.5
W, H = 720, 1280

DATA = Path(tempfile.gettempdir()) / "zil_jobs"
DATA.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ZIL")
flask_app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-V84"})
ACTIVE = 0
JOBS = {}

def run_cmd(cmd, timeout=300):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if r.returncode!= 0:
        raise RuntimeError((r.stderr or r.stdout)[-2000:])
    return r

def tg_msg(chat_id, text):
    if not BOT_TOKEN: return
    try:
        HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]}, timeout=30)
    except: pass

def tg_video(chat_id, path, cap):
    try:
        with open(path, "rb") as f:
            HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id": chat_id, "caption": cap[:1000]}, files={"video": (Path(path).name, f, "video/mp4")}, timeout=600)
    except Exception as e:
        log.error(f"send video fail {e}")

def extract_json(txt):
    txt = re.sub(r"^```.*?\n", "", txt.strip(), flags=re.S)
    txt = re.sub(r"\n```$", "", txt.strip())
    try:
        return json.loads(txt)
    except:
        s = txt.find("{")
        e = txt.rfind("}")
        return json.loads(txt[s:e+1])

def groq_chat(msgs):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": GROQ_MODEL, "messages": msgs, "temperature": 0.9, "max_tokens": 3000, "response_format": {"type": "json_object"}}
    r = HTTP.post(GROQ_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    return extract_json(r.json()["choices"][0]["message"]["content"])

def get_ideas(count=3):
    system = """You are hmisc. Return JSON {"ideas": [{"text_ar": "سطر عربي حكمة 1-3 أسطر", "top": "top illusion English", "bottom": "bottom reality English", "emotion": "anxiety"}]}
    Style yellow #FFC83D character parchment #F5E6C8 split top/bottom.
    Example: text_ar "لا تصنع هموما لم يكتبها الله عليك" top "yellow character drowning deep ocean panicking" bottom "same character sitting shallow water to knees calm"
    Keep text short viral."""
    try:
        data = groq_chat([{"role": "system", "content": system}, {"role": "user", "content": f"create {count} ideas"}])
        if data.get("ideas"):
            return data["ideas"][:count]
    except Exception as e:
        log.warning(f"groq fail {e}")
    return [
        {"text_ar": "لا تصنع هموماً\nلم يكتبها الله عليك", "top": "yellow character drowning in deep ocean hands up scared deep water illusion", "bottom": "same yellow character sitting calm on sandy bottom shallow water only to knees", "emotion": "anxiety"},
        {"text_ar": "لا تنظر إلى ما في أيدي الناس\nفما كتبه الله لك سيأتيك", "top": "two carrots above ground, big green leaves, roots hidden", "bottom": "underground cross section, first tiny root second huge root", "emotion": "comparison"},
        {"text_ar": "كل هذا سيمر\nكما مر غيره", "top": "stormy dark ocean big waves night", "bottom": "same ocean calm sunrise peaceful orange light", "emotion": "hope"},
    ][:count]

def gen_image(prompt, dest, seed):
    url = POLL_IMG + quote(prompt, safe="")
    params = {"width": 768, "height": 1365, "seed": seed, "nologo": "true", "model": "flux", "enhance": "true", "safe": "true"}
    r = HTTP.get(url, params=params, timeout=180)
    r.raise_for_status()
    Path(dest).write_bytes(r.content)

def make_visual(idea, workdir, idx):
    raw = workdir / f"raw_{idx}.jpg"
    clean = workdir / f"clean_{idx}.png"
    video = workdir / f"vis_{idx}.mp4"
    prompt = f"Split screen top and bottom, top: {idea['top']}, bottom: {idea['bottom']}, soft watercolor illustration, parchment paper texture #F5E6C8 old paper, yellow cute character #FFC83D simple round, storybook style, no text, no watermark, vertical 9:16 highly detailed"
    if not clean.exists():
        if not raw.exists():
            gen_image(prompt, raw, seed=1000+idx*137)
        # قص علامة pollinations.ai
        run_cmd(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-vf", "crop=iw:ih-90:0:0,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(clean)], 60)
    if not video.exists():
        frames = int(DURATION*FPS)
        vf = f"zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.0003,1.04))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p"
        run_cmd(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(clean), "-vf", vf, "-t", f"{DURATION}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(FPS), str(video)], 120)
    return video

def add_text(src, text_ar, dst, workdir, idx):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()
    def fix(t):
        try: return get_display(arabic_reshaper.reshape(t.strip()))
        except: return t
    y = 85
    for line in text_ar.split("\n"):
        line = line.strip()
        if not line: continue
        txt = fix(line)
        bbox = d.textbbox((0,0), txt, font=font)
        ww = bbox[2]-bbox[0]
        d.text(((W-ww)//2+2, y+2), txt, font=font, fill=(245,230,200,180))
        d.text(((W-ww)//2, y), txt, font=font, fill=(28,18,8,255))
        y += 56
    png = workdir / f"txt_{idx}.png"
    overlay.save(png)
    run_cmd(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),"-loop","1","-framerate",str(FPS),"-i",str(png),"-filter_complex","[0:v][1:v]overlay=0:0:shortest=1[v]","-map","[v]","-t",f"{DURATION}","-an","-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-r",str(FPS),str(dst)], 60)
    return dst

def add_music(src, dst, emotion):
    # بيانو هادئ مثل Hold The Door
    if emotion == "anxiety": freq = 110
    elif emotion == "hope": freq = 196
    else: freq = 130
    music = src.parent / f"m_{src.stem}.m4a"
    run_cmd(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"sine=frequency={freq}:duration={DURATION}","-af",f"volume=0.14,lowpass=f=900,afade=t=in:d=0.6,afade=t=out:st={DURATION-1}:d=1,atrim=duration={DURATION}","-ar","44100","-ac","2","-c:a","aac","-b:a","64k",str(music)], 30)
    run_cmd(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),"-i",str(music),"-c:v","copy","-map","0:v:0","-map","1:a:0","-shortest","-movflags","+faststart",str(dst)], 30)
    return dst

def job_worker(job_id, chat_id, ideas):
    global ACTIVE
    try:
        tg_msg(chat_id, f"✨ hmisc الحقيقي: {len(ideas)} فيديو - 6.5 ثانية، بدون علامة مائية، نص صحيح")
        outs = []
        for i, idea in enumerate(ideas):
            workdir = DATA / f"{job_id}_{i}"
            workdir.mkdir(parents=True, exist_ok=True)
            tg_msg(chat_id, f"🎨 {i+1}/{len(ideas)}: {idea['text_ar'][:40]}")
            vis = make_visual(idea, workdir, i)
            cap = workdir / f"cap_{i}.mp4"
            add_text(vis, idea["text_ar"], cap, workdir, i)
            final = workdir / f"HMISC_{i+1}.mp4"
            add_music(cap, final, idea.get("emotion","anxiety"))
            outs.append(str(final))
            tg_video(chat_id, final, idea["text_ar"])
        tg_msg(chat_id, f"🎉 اكتمل {len(outs)} فيديو")
    except Exception as e:
        log.exception("job fail")
        tg_msg(chat_id, f"❌ فشل: {e}")
    finally:
        ACTIVE = max(0, ACTIVE-1)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 ZIL V84 TRUE HMISC\n/hmisc 1 = فيديو واحد\n/hmisc 5 = 5 فيديو\nالمدة 6.5 ثانية، مقسم وهم/حقيقة، بدون علامة مائية، بيانو هادئ")

async def cmd_hmisc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ACTIVE
    if ACTIVE >= 1:
        await update.message.reply_text("⏳ في عمل شغال")
        return
    count = 1
    if context.args:
        try: count = int(context.args[0])
        except: pass
    count = max(1, min(10, count))
    job_id = uuid.uuid4().hex[:8]
    ACTIVE += 1
    await update.message.reply_text(f"✨ جاري توليد {count} أفكار hmisc...")
    def run():
        ideas = get_ideas(count)
        job_worker(job_id, update.effective_chat.id, ideas)
    threading.Thread(target=run, daemon=True).start()

@flask_app.route("/")
def home():
    return jsonify({"status": "ZIL V84 TRUE", "active": ACTIVE, "duration": DURATION})

@flask_app.route("/health")
def health():
    return jsonify({"ok": True, "bot": bool(BOT_TOKEN), "groq": bool(GROQ_API_KEY), "active": ACTIVE})

def start_flask():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)

def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing - only flask will run")
        start_flask()
        return
    # Flask في ثريد
    threading.Thread(target=start_flask, daemon=True).start()
    time.sleep(2)
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", cmd_start))
    app_bot.add_handler(CommandHandler("hmisc", cmd_hmisc))
    log.info(f"Bot polling + Flask on {PORT}")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
