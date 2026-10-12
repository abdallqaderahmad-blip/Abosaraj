import os, json, time, threading, re, tempfile, random, math, hashlib
from pathlib import Path
import requests
from flask import Flask, request

# ===== FIX ANTIALIAS - لازم قبل اي شي =====
try:
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, 'ANTIALIAS'):
        _PILImage.ANTIALIAS = _PILImage.LANCZOS
    # ل moviepy
    try:
        _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS
    except: pass
except: pass

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
PIAPI_API_KEY = (os.getenv("PIAPI_API_KEY","") or "").strip()
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

groq=None
try:
    from groq import Groq
    if GROQ_API_KEY: groq=Groq(api_key=GROQ_API_KEY)
except: groq=None

app=Flask(__name__)
PIAPI_BASE="https://api.piapi.ai/api/v1/task"
MEMORY_FILE=Path("/tmp/hmisc_brain_memory.json")

def log(m): print(f"[FINAL] {m}", flush=True)

def load_memory():
    if MEMORY_FILE.exists():
        try: return json.loads(MEMORY_FILE.read_text())
        except: return {"ideas":[],"scenes":[]}
    return {"ideas":[],"scenes":[]}

def save_memory(mem):
    mem["ideas"]=mem["ideas"][-100:]
    mem["scenes"]=mem["scenes"][-100:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False))

def is_dup(new_scene, mem):
    for s in mem["scenes"]:
        if s[:30] in new_scene or new_scene[:30] in s: return True
    return False

def brain_generate():
    mem=load_memory()
    fallback={
        "hikma_ar":"لا تحمل هم الغد فغدك بيد الله",
        "full_ar":"ما تخافش من بكرة، رزق بكرة على رب بكرة، عيش يومك وتوكل",
        "hikma_en":"Don't carry tomorrow's worry",
        "full_en":"Don't fear tomorrow, tomorrow's provision is on tomorrow's Lord",
        "scene_en":"small yellow bean carrying huge heavy bag on back walking on thin rope over abyss, two panels top struggling bottom calm",
        "mood":"hopeful_joy","title":"هم الغد"
    }
    if not groq: return fallback
    recent="\n".join(mem["ideas"][-10:])
    for _ in range(4):
        try:
            prompt=f"""You are hmisc brain. Memory: {recent}
Generate ONE NEW viral idea NEVER repeat. New scenarios: cliff, heavy bag, stars, maze, planting seed, broken chain, hourglass, lighthouse, bridge lantern, empty jar, mirror.
JSON only: {{"hikma_ar":"6 كلمات عربية","full_ar":"25 كلمة عربية","hikma_en":"6 words english","full_en":"25 words english","scene_en":"detailed two panels top bottom yellow bean character","mood":"hopeful_joy or calm_healing or sad_deep or anxious_fear or reflective","title":"short"}}"""
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=1.0, max_completion_tokens=2000, response_format={"type":"json_object"}, messages=[{"role":"user","content":prompt}])
            j=json.loads(res.choices[0].message.content)
            if not is_dup(j.get("scene_en",""), mem):
                mem["ideas"].append(j.get("title","")+" "+j.get("hikma_ar",""))
                mem["scenes"].append(j.get("scene_en",""))
                save_memory(mem)
                log(f"NEW IDEA {j.get('title')} {j.get('mood')}")
                return j
        except Exception as e:
            log(f"brain retry {e}")
    return fallback

def extract_url(j):
    import json as js; s=js.dumps(j)
    m=re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    data=j.get("data",{}) or {}; out=data.get("output",{}) or {}
    if isinstance(out, dict):
        for k in ("image_url","url","image"):
            v=out.get(k)
            if isinstance(v, list) and v: return v[0]
            if isinstance(v, str) and v.startswith("http"): return v
    return None

def piapi_wait(tid):
    headers={"X-API-KEY": PIAPI_API_KEY}
    url=f"{PIAPI_BASE}/{tid}"
    for _ in range(120):
        r=requests.get(url, headers=headers, timeout=30)
        body=r.json(); data=body.get("data") or body
        status=str(data.get("status","")).lower()
        if status in ("completed","complete","success","succeeded"): return extract_url(body)
        if status in ("failed","error","canceled"): raise RuntimeError(str(body)[:800])
        time.sleep(3)
    raise TimeoutError

def download_file(url, path):
    path=Path(path)
    with requests.get(url, timeout=120, stream=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*1024):
                if c: f.write(c)
    return path

def generate_pro_image(scene_en):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    prompt=f"hmisc original style, vintage beige paper texture brown stains, two-panel vertical comic watercolor ink crosshatch highly detailed, small yellow bean character thin black stick arms legs yellow watercolor body expressive face, {scene_en}, NO TEXT no words no letters no typography completely blank top bottom pure illustration masterpiece"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":prompt[:1500],"width":832,"height":1216,"num_images":1,"guidance_scale":3.5,"num_inference_steps":32}}
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    tid=r.json().get("data",{}).get("task_id")
    url=piapi_wait(tid)
    tmp=Path(tempfile.gettempdir())/f"pro_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}.jpg"
    download_file(url, tmp)
    return tmp

def ensure_arabic_font():
    base=Path("/tmp/fonts"); base.mkdir(exist_ok=True)
    bold=base/"Amiri-Bold.ttf"
    if not bold.exists():
        try:
            rr=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=30)
            bold.write_bytes(rr.content)
            log("Amiri downloaded")
        except Exception as e:
            log(f"font fail {e}"); return None
    return bold

def add_text_with_share(image_path, ar_short, ar_full, en_short, en_full):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    font_file=ensure_arabic_font()
    img=Image.open(image_path).convert("RGB")
    W,H=img.size
    draw=ImageDraw.Draw(img)
    beige=(245,233,201)
    draw.rectangle([0,0,W,195], fill=beige)
    draw.rectangle([0,H-155,W,H], fill=beige)
    def fix_ar(t):
        try: return get_display(arabic_reshaper.reshape(t))
        except: return t
    try:
        if font_file and font_file.exists():
            f_ar_big=ImageFont.truetype(str(font_file), 56)
            f_ar_small=ImageFont.truetype(str(font_file), 34)
        else:
            f_ar_big=ImageFont.load_default(); f_ar_small=f_ar_big
        f_en_big=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        f_en_small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        f_share=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        f_ar_big=ImageFont.load_default(); f_ar_small=f_ar_big; f_en_big=f_ar_big; f_en_small=f_ar_big; f_share=f_ar_big

    draw.text((W//2, 85), fix_ar(ar_short), font=f_ar_big, fill="#0f0f0f", anchor="mm", align="center")
    draw.text((W//2, 148), en_short, font=f_en_big, fill="#222", anchor="mm")
    draw.text((W//2, H-108), fix_ar(ar_full), font=f_ar_small, fill="#111", anchor="mm", align="center")
    draw.text((W//2, H-58), en_full, font=f_en_small, fill="#2a2a2a", anchor="mm", align="center")
    draw.text((W-20, H-16), "share ♻️ hmisc", font=f_share, fill="#8a7a5a", anchor="rb")
    img.save(image_path, quality=98)
    return image_path

def create_cinematic_with_music(image_path, mood, duration=10):
    from moviepy.editor import ImageClip, AudioFileClip
    MUSIC={
        "hopeful_joy":"https://cdn.pixabay.com/download/audio/2022/03/10/audio_4e2c7bb6f3.mp3",
        "calm_healing":"https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
        "sad_deep":"https://cdn.pixabay.com/download/audio/2024/09/19/audio_8bfa6c1f48.mp3",
        "anxious_fear":"https://cdn.pixabay.com/download/audio/2022/06/07/audio_b9bd4170e8.mp3",
        "reflective":"https://cdn.pixabay.com/download/audio/2022/10/30/audio_8b64a1c5e7.mp3",
    }
    music_url=MUSIC.get(mood, MUSIC["hopeful_joy"])
    music_path=Path(tempfile.gettempdir())/f"m_{mood}_{random.randint(1,9999)}.mp3"
    try:
        download_file(music_url, music_path)
    except Exception as e:
        log(f"music dl fail {e}"); music_path=None

    montage=random.choice(["slow_in","slow_out","pan","breathe"])
    log(f"Montage {montage} mood {mood}")
    clip=ImageClip(str(image_path), duration=duration)

    if montage=="slow_in":
        clip=clip.resize(lambda t: 1.0+0.32*t/duration).set_position(lambda t: ('center', f"{52-3*t/duration}%"))
    elif montage=="slow_out":
        clip=clip.resize(lambda t: 1.35-0.28*t/duration).set_position('center')
    elif montage=="pan":
        clip=clip.resize(lambda t: 1.28).set_position(lambda t: (f"{-12+24*t/duration}%", 'center'))
    else:
        clip=clip.resize(lambda t: 1.12+0.09*math.sin(t*0.9)).set_position('center')

    if music_path and music_path.exists():
        try:
            audio=AudioFileClip(str(music_path)).subclip(0, duration).volumex(0.90).audio_fadein(0.6).audio_fadeout(1.8)
            clip=clip.set_audio(audio)
        except Exception as e:
            log(f"audio attach {e}")

    out=Path(tempfile.gettempdir())/f"cin_{int(time.time())}_{random.randint(100,999)}.mp4"
    clip.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
    log(f"Video {out} {out.stat().st_size//1024}KB")
    return out

TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", data={"chat_id":cid,"text":t}, timeout=20)
    except: pass
def send_photo(cid,p,c=""):
    try:
        with open(p,"rb") as f: requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":cid,"caption":c}, files={"photo":f}, timeout=120)
    except Exception as e: log(f"photo {e}")
def send_video(cid,p,c=""):
    try:
        with open(p,"rb") as f: requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":cid,"caption":c,"supports_streaming":True}, files={"video":f}, timeout=180)
    except Exception as e: log(f"video {e}")

processing=set(); plock=threading.Lock()
def process(cid, cinematic=False):
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ العقل لسه بيفكر..."); return
            processing.add(cid)
        send_text(cid,"🧠 عقل بيولد فكرة جديدة...\n🎨 صورة احترافية...\n🎬 مونتاج مختلف + موسيقى ترند عالية...")
        idea=brain_generate()
        img=generate_pro_image(idea["scene_en"])
        add_text_with_share(img, idea["hikma_ar"], idea["full_ar"], idea["hikma_en"], idea["full_en"])
        if cinematic:
            vid=create_cinematic_with_music(img, idea["mood"], duration=10)
            cap=f"{idea['hikma_ar']}\n{idea['hikma_en']}\n\n{idea['full_ar']}\n{idea['full_en']}\n\n🎬 {idea['title']} | 🎵 {idea['mood']} | ♻️ SHARE"
            send_video(cid, vid, cap)
            Path(vid).unlink(missing_ok=True)
        else:
            cap=f"{idea['hikma_ar']} | {idea['hikma_en']}\n\n{idea['full_ar']}\n{idea['full_en']}\n\n🧠 {idea['title']} | ♻️ share"
            send_photo(cid, img, cap)
        Path(img).unlink(missing_ok=True)
    except Exception as e:
        log(f"ERR {repr(e)}"); send_text(cid,f"❌ {str(e)[:900]}")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    if text.startswith("/start"): send_text(chat,"🧠 Hmisc Brain Final\n/hikma - صورة فكرة جديدة\n/cinema - فيديو زوم حي + موسيقى عالية + فكرة جديدة\n/brain - كم فكرة ولدنا"); return
    if text.startswith("/brain"):
        mem=load_memory(); send_text(chat,f"🧠 ولدنا {len(mem['ideas'])} فكرة\nآخر 3:\n"+"\n".join(mem['ideas'][-3:])); return
    if text.startswith("/ping"): send_text(chat,f"🟢 Final OK | {len(load_memory()['ideas'])} ideas | PiAPI {len(PIAPI_API_KEY)}"); return
    if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
    if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return

@app.get("/")
def home(): return f"Final Brain {len(load_memory()['ideas'])}",200
@app.post("/telegram/webhook")
def wh(): upd=request.get_json(silent=True) or {}; threading.Thread(target=handle_update, args=(upd,), daemon=True).start(); return "OK",200
def setup_wh():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try: requests.post(f"{TELEGRAM_API}/setWebhook", data={"url":RENDER_EXTERNAL_URL+"/telegram/webhook"}, timeout=20)
    except: pass
if __name__=="__main__": setup_wh(); app.run(host="0.0.0.0", port=PORT, threaded=True)
