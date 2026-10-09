import os, time, shutil, tempfile, threading, subprocess, urllib.parse, asyncio
from pathlib import Path
import requests
from flask import Flask, request
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()

def log(m):
    with lock: print("[V6.5.2] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def get_font():
    font_dir = Path("/tmp/fonts")
    font_dir.mkdir(exist_ok=True)
    fp = font_dir / "Amiri-Regular.ttf"
    if not fp.exists():
        try:
            r = requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf", timeout=15)
            if r.status_code==200: fp.write_bytes(r.content)
        except: pass
    for f in [str(fp), "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(f).exists(): return f
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

FONT = get_font()

CHARACTER_BANK = {
    "hero": "Handsome real Arab man age 25 short black hair blue eyes dark cloak photorealistic consistent face",
    "princess": "Beautiful real Arab woman age 22 long black hair green eyes royal dress",
    "king": "Old Arab king age 60 white beard crown red robe throne",
    "wolf": "Small white wolf pup cute blue eyes snow",
    "tiger": "GIANT TIGER enormous 4 meters orange stripes roaring"
}

VOICE_MAP = {
    "hero": {"voice": "ar-SA-HamedNeural", "rate": "-16%", "pitch": "-2Hz"},
    "princess": {"voice": "ar-EG-SalmaNeural", "rate": "-10%", "pitch": "+3Hz"},
    "king": {"voice": "ar-SA-HamedNeural", "rate": "-24%", "pitch": "-9Hz"},
    "wolf": {"voice": "ar-EG-SalmaNeural", "rate": "-6%", "pitch": "+5Hz"},
    "tiger": {"voice": "ar-SA-HamedNeural", "rate": "-30%", "pitch": "-13Hz"}
}

def run_cmd(cmd, timeout=60):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0: raise RuntimeError(p.stderr[-1500:])
    return p

def gen_image_full(prompt_ar, out, scene_no, char_key):
    char_desc = CHARACTER_BANK.get(char_key, CHARACTER_BANK["hero"])
    t=prompt_ar
    if "قصر" in t and "يدخل" in t: en="Arab man entering golden palace"
    elif "يطرد" in t or "اطردوا" in t: en="Arab king shouting angry expel man throne"
    elif "الحراس" in t: en="Arab guards pushing sad man out palace"
    elif "يمشي" in t and "الغابة" in t: en="sad Arab man walking dark forest"
    elif "ذئبة" in t: en="small white wolf pup crying snow hugging"
    elif "نمر" in t and "يزأر" in t: en="giant tiger roaring attacking"
    elif "تلمع" in t: en="Arab hero eyes glowing power"
    elif "يضرب" in t: en="Arab hero punching giant tiger flying"
    elif "الأميرة" in t: en="beautiful Arab princess shocked"
    elif "سامحني" in t: en="old king crying kneeling sorry"
    else: en="Arab cinematic story"

    full = f"{char_desc}, {en}, photorealistic cinematic 8k vertical"
    safe = urllib.parse.quote(full[:340])
    seed = int(time.time()) + scene_no*19

    for _ in range(3):
        try:
            url = f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&seed={seed}&nologo=true"
            r = requests.get(url, timeout=30)
            if r.status_code==200 and len(r.content)>11000:
                Path(out).write_bytes(r.content)
                log(f"IMG {scene_no} OK")
                return Path(out)
            seed+=7
        except Exception as e:
            log(f"IMG {scene_no} try {e}")
            time.sleep(0.4)

    # PIL fallback - ما يعلق
    try:
        colors = [(42,58,74),(58,42,50),(32,55,45),(55,45,65),(40,65,75),(70,50,40),(45,55,70),(60,60,50),(50,40,60),(35,60,65)]
        c = colors[scene_no % len(colors)]
        Image.new('RGB', (720,1280), c).save(str(out), "JPEG", quality=85)
        log(f"IMG {scene_no} PIL {c}")
        return Path(out)
    except:
        Path(out).write_bytes(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00')
        return Path(out)

async def _edge(text, voice, rate, pitch, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice_full(text, char_key, out):
    try:
        cfg = VOICE_MAP.get(char_key, VOICE_MAP["hero"])
        asyncio.run(_edge(text.strip()[:110], cfg["voice"], cfg["rate"], cfg["pitch"], out))
        if Path(out).exists() and Path(out).stat().st_size>800:
            return out
    except Exception as e:
        log(f"VOICE {char_key} fail {e}")
    return None

def gen_video_full(img_path, scene_text, voice_path, out_path, scene_no, work_dir):
    def esc(t):
        s = str(t or "").replace("'","").replace('"',"").replace(":"," ").replace("\n"," ").replace("%"," ").replace("[","").replace("]","").replace("`","")
        return s[:55]

    raw = esc(scene_text)
    log(f"VIDEO {scene_no} START {raw[:20]}")

    # بدون zoompan - هو سبب التعليق - scale بسيط
    vf = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,drawtext=fontfile={FONT}:text='{raw}':fontcolor=white:fontsize=26:box=1:boxcolor=black@0.85:boxborderw=10:x=(w-text_w)/2:y=h-90"

    try:
        if voice_path and Path(voice_path).exists():
            p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=5)
            try: dur=float(p.stdout.strip() or "5")
            except: dur=5
            dur=max(4.5, min(dur+0.3, 5.8))
        else: dur=5.0
    except: dur=5.0

    try:
        if voice_path and Path(voice_path).exists() and Path(voice_path).stat().st_size>800:
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf,"-t",str(dur),"-r","24","-c:v","libx264","-preset","ultrafast","-crf","28","-c:a","aac","-b:a","96k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        else:
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf,"-t","5","-r","24","-c:v","libx264","-preset","ultrafast","-crf","28","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=40)
        if res.returncode!=0:
            log(f"VIDEO {scene_no} ERR {res.stderr[-600:]}")
            raise RuntimeError(res.stderr[-600:])
        log(f"VIDEO {scene_no} OK")
        return out_path
    except Exception as e:
        log(f"VIDEO {scene_no} FAIL {e} -> ultra simple")
        # بدون نص نهائيا
        cmd2 = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-t","5","-r","24","-c:v","libx264","-preset","ultrafast","-crf","28","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        log(f"VIDEO {scene_no} OK SIMPLE")
        return out_path

def concat_copy(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{v}'\n")
    try:
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c","copy","-movflags","+faststart",str(out)],40)
    except:
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","ultrafast","-crf","26","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],60)
    return out

TELEGRAM_API="https://api.telegram.org/bot" + BOT_TOKEN
def telegram(method, data=None, files=None, timeout=60):
    r=requests.post(TELEGRAM_API + "/" + method, data=data, files=files, timeout=timeout); r.raise_for_status(); return r.json()
def send_text(chat_id, text): return telegram("sendMessage", {"chat_id": chat_id, "text": text})
def send_video(chat_id, path, caption):
    with Path(path).open("rb") as f:
        return telegram("sendVideo", {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, {"video": ("final.mp4", f, "video/mp4")}, 600)

LONG_STORY = """شاب فقير يدخل قصر الملك الذهبي يبحث عن عمل
الملك يصرخ اطردوا هذا القذر من قصري
الحراس يطردون الشاب حزينا خارج القصر
الشاب يمشي وحيدا في الغابة المظلمة يقول الغابة أحن علي من البشر
يجد ذئبة بيضاء صغيرة تبكي من البرد فيحضنها لا تخافي صغيرتي
نمر عملاق يزأر ويهجم على الذئبة
عيون الشاب تلمع حان وقت الحقيقة
الشاب يضرب النمر بضربة أسطورية يطير بعيدا
الأميرة تقول يا إلهي ما هذه القوة العظيمة
الملك يبكي سامحني يا بني وتصبح انت الملك ولكن فجأة سمعنا صوتا من السماء"""

def process_full_fast(chat_id, user_text):
    work=Path(tempfile.mkdtemp(prefix="v652_"))
    try:
        lines = [l.strip() for l in user_text.split("\n") if l.strip()][:10]
        if len(lines)<8: lines = LONG_STORY.split("\n")
        send_text(chat_id, f"🎬 V6.5.2 - 10 مشاهد FIXED\n✅ بدون zoompan المعلق\n✅ PIL fallback\n⚡ 10 صور متوازي")

        def job_media(i_txt):
            i, txt = i_txt
            no=i+1
            key="hero"
            if "ملك" in txt and i<3: key="king"
            elif "الأميرة" in txt: key="princess"
            elif "ذئبة" in txt: key="wolf"
            elif "نمر" in txt: key="tiger"
            img=work / f"img_{no}.jpg"
            voice=work / f"voice_{no}.mp3"
            gen_image_full(txt, img, no, key)
            gen_voice_full(txt, key, voice)
            return no, img, voice, txt, key

        results={}
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures=[ex.submit(job_media,(i,txt)) for i,txt in enumerate(lines)]
            done=0
            for f in as_completed(futures):
                no, img, voice, txt, key = f.result()
                results[no]=(img,voice,txt,key)
                done+=1
                if done%2==0: send_text(chat_id, f"✅ {done}/10 صور+صوت [{key}]")

        send_text(chat_id, f"✅ كل الصور+أصوات جاهزة - نحول فيديو - 5ث لكل واحد")

        def job_video(item):
            no, (img, voice, txt, key) = item
            vid=work / f"vid_{no}.mp4"
            log(f"START vid {no}")
            gen_video_full(img, txt, voice if Path(voice).exists() else None, vid, no, work)
            log(f"DONE vid {no}")
            return no, vid

        videos_dict={}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures=[ex.submit(job_video, kv) for kv in results.items()]
            for f in as_completed(futures):
                no, vid = f.result()
                videos_dict[no]=vid
                send_text(chat_id, f"🎬 فيديو {no}/10 جاهز")

        videos=[videos_dict[i] for i in range(1,11)]
        raw=work / "raw.mp4"
        concat_copy(videos, raw)

        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(raw)], stdout=subprocess.PIPE, text=True, timeout=5)
        try: dur=float(p.stdout.strip() or "0")
        except: dur=0

        send_text(chat_id, f"🎬 FIXED {int(dur)}ث - 10 مشاهد كاملة بدون تعليق")
        send_video(chat_id, raw, f"V6.5.2 FIXED {int(dur)}ث - صوت+صور مطابقة")

    except Exception as e:
        log("ERR "+repr(e))
        try: send_text(chat_id, "❌ "+str(e)[:800])
        except: pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with plock: processing_chats.discard(chat_id)

def handle_update(update):
    msg=update.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    if text=="/start": send_text(chat, "🎬 V6.5.2 FIXED\n/full = 10 مشاهد 2 دقيقة بدون تعليق"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅ تم المسح"); return
    user_text=LONG_STORY if text=="/full" else (text if len(text.split("\n"))>=4 else LONG_STORY)
    with plock:
        if chat in processing_chats: send_text(chat, "⏳ شغال - /clear"); return
        processing_chats.add(chat)
    threading.Thread(target=process_full_fast, args=(chat, user_text), daemon=True).start()

@app.get("/")
def home(): return "V6.5.2", 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", data={"url": RENDER_EXTERNAL_URL + "/telegram/webhook", "drop_pending_updates": "true"}, timeout=10)
    except: pass

if __name__=="__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
