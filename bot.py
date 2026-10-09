import os, time, shutil, tempfile, threading, subprocess, urllib.parse, asyncio
from pathlib import Path
import requests
from flask import Flask, request
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()

def log(m):
    with lock: print("[V6.5.1 FIXED] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

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
    "hero": "Handsome real Arab man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, photorealistic consistent face cinematic",
    "princess": "Beautiful real Arab woman age 22 long black hair green eyes royal white gold dress palace, photorealistic consistent face",
    "king": "Old Arab king age 60 white beard crown royal red robe throne palace serious face cinematic",
    "wolf": "Small white wolf pup cute fluffy blue eyes snow forest night moonlight",
    "tiger": "GIANT TIGER enormous 4 meters orange black stripes angry roaring dark forest"
}

VOICE_MAP = {
    "hero": {"voice": "ar-SA-HamedNeural", "rate": "-16%", "pitch": "-2Hz"},
    "princess": {"voice": "ar-EG-SalmaNeural", "rate": "-10%", "pitch": "+3Hz"},
    "king": {"voice": "ar-SA-HamedNeural", "rate": "-24%", "pitch": "-9Hz"},
    "wolf": {"voice": "ar-EG-SalmaNeural", "rate": "-6%", "pitch": "+5Hz"},
    "tiger": {"voice": "ar-SA-HamedNeural", "rate": "-30%", "pitch": "-13Hz"}
}

def run_cmd(cmd, timeout=120):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0: raise RuntimeError(p.stderr[-2000:])
    return p

def gen_image_full(prompt_ar, out, scene_no, char_key):
    char_desc = CHARACTER_BANK.get(char_key, CHARACTER_BANK["hero"])
    t=prompt_ar
    if "قصر" in t and "يدخل" in t: en="Arab man entering golden palace job interview"
    elif "يطرد" in t or "اطردوا" in t: en="Arab king shouting angry expel man throne room"
    elif "الحراس يطردون" in t: en="Arab guards pushing sad Arab man out palace"
    elif "يمشي" in t and "الغابة" in t: en="sad Arab man walking alone dark forest"
    elif "ذئبة" in t: en="small white wolf pup crying cold snow Arab man hugging"
    elif "نمر" in t and "يزأر" in t: en="giant tiger roaring attacking forest"
    elif "تلمع" in t: en="Arab hero eyes glowing power epic"
    elif "يضرب" in t: en="Arab hero punching giant tiger flying superpower"
    elif "الأميرة" in t: en="beautiful Arab princess shocked oh my god power"
    elif "سامحني" in t: en="old Arab king crying kneeling sorry forgive son you king"
    else: en="Arab cinematic story"

    full = f"{char_desc}, {en}, photorealistic consistent face cinematic 8k dramatic vertical"
    safe = urllib.parse.quote(full[:350])
    seed = int(time.time()) + scene_no*19

    for _ in range(4):
        try:
            url = f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&seed={seed}&nologo=true&enhance=false"
            r = requests.get(url, timeout=35)
            if r.status_code==200 and len(r.content)>12000:
                Path(out).write_bytes(r.content)
                log(f"IMG {scene_no} OK {len(r.content)}")
                return Path(out)
            seed+=11
            time.sleep(0.5)
        except Exception as e:
            log(f"IMG {scene_no} try fail {e}")
            time.sleep(0.5)

    # FALLBACK PIL - بدون ffmpeg - ما يعلق
    try:
        colors = [(42,58,74),(58,42),(32,55,45),(55,45,65),(40,65,75),(70,50,40),(45,55,70),(60,60,50),(50,40,60),(35,60,65)]
        c = colors[scene_no % len(colors)]
        img = Image.new('RGB', (720,1280), c)
        img.save(str(out), "JPEG", quality=85)
        log(f"IMG {scene_no} FALLBACK PIL {c}")
        return Path(out)
    except Exception as e:
        log(f"PIL fail {e}")
        Path(out).write_bytes(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00')
        return Path(out)

async def _edge(text, voice, rate, pitch, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice_full(text, char_key, out):
    try:
        cfg = VOICE_MAP.get(char_key, {"voice":"ar-SA-HamedNeural","rate":"-15%","pitch":"-2Hz"})
        clean = text.strip()[:120]
        asyncio.run(_edge(clean, cfg["voice"], cfg["rate"], cfg["pitch"], out))
        return out if Path(out).exists() and Path(out).stat().st_size>1000 else None
    except Exception as e:
        log(f"Voice fail {e}")
        return None

def gen_ambient(text, out, dur=5):
    low=text.lower()
    if "قصر" in low or "ملك" in low: filt="anoisesrc=d=2:c=brown:r=22050:a=0.025,lowpass=f=350,volume=0.22"
    elif "غابة" in low: filt="anoisesrc=d=2:c=brown:r=22050:a=0.05,highpass=f=700,lowpass=f=2500,volume=0.26"
    elif "ذئبة" in low: filt="anoisesrc=d=2:c=white:r=22050:a=0.018,lowpass=f=1100,volume=0.22"
    elif "نمر" in low: filt="anoisesrc=d=2:c=brown:r=22050:a=0.10,lowpass=f=180,volume=0.38"
    elif "ضرب" in low or "يهجم" in low: filt="anoisesrc=d=2:c=brown:r=22050:a=0.12,lowpass=f=280,volume=0.42"
    elif "يبكي" in low or "سامحني" in low: filt="anoisesrc=d=2:c=pink:r=22050:a=0.03,lowpass=f=600,volume=0.18"
    else: filt="anullsrc=r=22050:cl=stereo:d=2,volume=0.05"
    try:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",filt,"-t","2","-c:a","aac","-b:a","32k",str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        return out if Path(out).exists() else None
    except:
        return None

def gen_video_full(img_path, scene_text, voice_path, out_path, scene_no, work_dir):
    def esc(t): return str(t or "").replace("'","").replace('"',"").replace(":","").replace("\n"," ").replace("%"," ")[:65]
    raw = esc(scene_text)

    if scene_no==1: zoom="min(zoom+0.006,1.60)"; eq="eq=contrast=1.32:saturation=1.55"
    elif scene_no in [2,6]: zoom="min(zoom+0.0035,1.35)"; eq="eq=contrast=1.38:saturation=1.25:brightness=-0.04"
    elif scene_no in [4,5]: zoom="min(zoom+0.0007,1.18)"; eq="eq=contrast=1.08:saturation=0.85:brightness=0.03"
    elif scene_no==7: zoom="min(zoom+0.005,1.50)"; eq="eq=contrast=1.40:saturation=1.6"
    elif scene_no==10: zoom="min(zoom+0.007,1.68)"; eq="eq=contrast=1.35:saturation=1.50"
    else: zoom="min(zoom+0.0022,1.38)"; eq="eq=contrast=1.22:saturation=1.40"

    vf_text = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=24:s=720x1280,{eq},unsharp=5:5:0.85:5:5:0.0,vignette=angle=PI/4:mode=forward,drawtext=fontfile={FONT}:text='{raw}':fontcolor=white:fontsize=28:box=1:boxcolor=black@0.88:boxborderw=12:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-88"

    try:
        if voice_path and Path(voice_path).exists():
            p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=5)
            dur=float(p.stdout.strip() or "6"); dur=max(5.0, min(dur+0.4, 6.0))
        else: dur=5.5
    except: dur=5.5

    ambient_path = work_dir / f"amb_{scene_no}.mp3"
    gen_ambient(scene_text, ambient_path, dur)

    try:
        if voice_path and Path(voice_path).exists() and Path(ambient_path).exists():
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-i",str(ambient_path),"-filter_complex","[1:a]volume=1.0[vox];[2:a]volume=0.24[amb];[vox][amb]amix=inputs=2:duration=first:weights=1 0.3[mix]","-vf",vf_text,"-t",str(dur),"-r","24","-map","0:v","-map","[mix]","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","128k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        elif voice_path and Path(voice_path).exists():
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf_text,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        else:
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf_text,"-t","5.5","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except Exception as e:
        log(f"Video {scene_no} fallback {e}")
        subprocess.run(["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf_text,"-t","5.5","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    return out_path

def gen_music(out, dur=60):
    try:
        cmd = ["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo","-f","lavfi","-i",f"sine=f=110:duration={dur},sine=f=220:duration={dur}","-filter_complex","[1:a]volume=0.12,lowpass=f=700,atempo=0.82[a1];[0:a][a1]amix=inputs=2:duration=first[a]","-map","[a]","-t",str(dur),"-c:a","aac","-b:a","64k",str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return out if Path(out).exists() else None
    except: return None

def concat_copy(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{v}'\n")
    try:
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c","copy","-movflags","+faststart",str(out)],60)
        return out
    except:
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","ultrafast","-crf","24","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],120)
        return out

def final_mix(v_path, m_path, out_path):
    try:
        if m_path and Path(m_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(v_path),"-i",str(m_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.10[a1];[a0][a1]amix=inputs=2:duration=first[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k","-shortest",str(out_path)],60)
            return out_path
    except: pass
    shutil.copy(v_path, out_path); return out_path

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
    work=Path(tempfile.mkdtemp(prefix="fullfixed_"))
    try:
        lines = [l.strip() for l in user_text.split("\n") if l.strip()][:10]
        if len(lines)<8: lines = LONG_STORY.split("\n")
        send_text(chat_id, f"🎬 V6.5.1 FIXED - 10 مشاهد كامل\n✅ PIL fallback بدون ffmpeg color\n🎧 صوت+بيئة+زوم+ترجمة\n⚡ متوازي 10 = 2 دقيقة")

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
                if done%2==0: send_text(chat_id, f"✅ {done}/10 [{key}]")

        send_text(chat_id, f"✅ كل الصور+أصوات جاهزة - نحول فيديو")

        def job_video(item):
            no, (img, voice, txt, key) = item
            vid=work / f"vid_{no}.mp4"
            gen_video_full(img, txt, voice if Path(voice).exists() else None, vid, no, work)
            return no, vid

        videos_dict={}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures=[ex.submit(job_video, kv) for kv in results.items()]
            for f in as_completed(futures):
                no, vid = f.result()
                videos_dict[no]=vid
                send_text(chat_id, f"🎬 فيديو {no}/10")

        videos=[videos_dict[i] for i in range(1,11)]
        music=work / "music.mp3"
        gen_music(music, 58)
        raw=work / "raw.mp4"
        concat_copy(videos, raw)
        final=work / "final.mp4"
        final_mix(raw, music if music.exists() else None, final)

        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)], stdout=subprocess.PIPE, text=True, timeout=5)
        try: dur=float(p.stdout.strip() or "0")
        except: dur=0

        send_text(chat_id, f"🎬 FIXED {int(dur)}ث - كلشي موجود")
        send_video(chat_id, final, f"V6.5.1 FIXED {int(dur)}ث")

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
    if text=="/start": send_text(chat, "🎬 V6.5.1 FIXED\n/full للتجربة الطويلة الكاملة - 10 مشاهد\n✅ PIL بدون ffmpeg color"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅ تم المسح"); return
    user_text=LONG_STORY if text=="/full" else (text if len(text.split("\n"))>=4 else LONG_STORY)
    with plock:
        if chat in processing_chats: send_text(chat, "⏳ شغال - /clear"); return
        processing_chats.add(chat)
    threading.Thread(target=process_full_fast, args=(chat, user_text), daemon=True).start()

@app.get("/")
def home(): return "V6.5.1 FIXED", 200
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
