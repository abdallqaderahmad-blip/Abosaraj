import os, sys, json, time, threading, re, tempfile, random, hashlib
from pathlib import Path
import requests
from flask import Flask, request

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
except: pass

try:
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, 'ANTIALIAS'):
        _PILImage.ANTIALIAS = _PILImage.LANCZOS
    try: _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS
    except: pass
except: pass

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
PIAPI_API_KEY = (os.getenv("PIAPI_API_KEY","") or "").strip()
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

groq = None
try:
    from groq import Groq
    if GROQ_API_KEY: groq = Groq(api_key=GROQ_API_KEY)
except: groq = None

app = Flask(__name__)
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"
MEMORY_FILE = Path("/tmp/hmisc_brain_memory.json")

def log(m):
    try: print(f"[MANUAL] {str(m)[:500]}", flush=True)
    except: pass

def load_memory():
    if MEMORY_FILE.exists():
        try: return json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
        except: return {"ideas":[],"scenes":[]}
    return {"ideas":[],"scenes":[]}

def save_memory(mem):
    mem["ideas"] = mem["ideas"][-100:]
    mem["scenes"] = mem["scenes"][-100:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False), encoding='utf-8')

def is_dup(new_scene, mem):
    for s in mem["scenes"]:
        if s[:30] in new_scene or new_scene[:30] in s: return True
    return False

# ---------------- BRAIN حقيقي بدون قوالب ----------------
def brain_generate():
    mem = load_memory()
    fallback = {
        "action_ar":"تخفي دمعتك بصمت",
        "reaction_ar":"يصرخ قلبك بالوجع",
        "action_en":"You hide tears silently",
        "reaction_en":"Your heart screams loudly",
        "scene_action_en":"small yellow bean hiding tears dark room top panel",
        "scene_reaction_en":"same yellow bean heart screaming light breaking bottom panel same room",
        "mood":"sad_deep","title":"Hidden Tears"
    }
    if not groq: return fallback
    recent = ", ".join(mem["ideas"][-12:]) or "none"
    system_msg = "You are viral reels writer. Create SHORT action-reaction. Real Arabic poetic sentences, not placeholders."
    for _ in range(6):
        try:
            user_prompt = f"""Avoid titles: {recent}
Generate JSON only REAL content:
{{
"action_ar": "جملة عربية حقيقية 5 كلمات فعل حزين مثل تتجاهل قلبك كل يوم",
"reaction_ar": "جملة عربية حقيقية 5 كلمات رد فعل مثل ينكسر بصمت مؤلم",
"action_en": "real english action 5 words like You ignore heart daily",
"reaction_en": "real english reaction 5 words like It breaks in silence",
"scene_action_en": "top panel small yellow bean ignoring heart dark",
"scene_reaction_en": "bottom panel same bean heart breaking light same place",
"mood": "sad_deep",
"title": "unique 2 words title"
}}
Must be real sentences, no words '5 كلمات' or '5 words'.
"""
            res = groq.chat.completions.create(
                model=GROQ_MODEL, temperature=1.35,
                max_completion_tokens=800,
                messages=[{"role":"system","content": system_msg},{"role":"user","content": user_prompt}]
            )
            content = res.choices[0].message.content.strip()
            if "```" in content:
                parts = content.split("```")
                for p in parts:
                    if "{" in p and "}" in p:
                        content = p
                        if content.startswith("json"): content = content[4:]
                        break
                content = content.strip()
            j = json.loads(content)
            if "5 كلمات" in j.get("action_ar","") or "5 كلمات" in j.get("reaction_ar",""): continue
            if "5 words" in j.get("action_en","").lower(): continue
            if len(j.get("action_ar","").split()) < 3: continue
            combined = j.get("scene_action_en","")+j.get("scene_reaction_en","")
            if not is_dup(combined, mem):
                mem["ideas"].append(j.get("title",""))
                mem["scenes"].append(combined)
                save_memory(mem)
                return j
        except Exception as e:
            log(f"brain {repr(e)[:400]}")
            time.sleep(0.5)
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

def generate_pro_image(action_en, reaction_en):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    prompt=f"SAME CHARACTER CONSISTENCY vertical two-panel comic SAME small yellow bean character thin black stick arms legs yellow body expressive face BOTH panels TOP PANEL: {action_en} BOTTOM PANEL: same character same location transformed {reaction_en} perfect alignment split line middle hmisc vintage beige paper watercolor ink crosshatch masterpiece NO TEXT 9:16"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":prompt[:1500],"width":832,"height":1216,"num_images":1,"guidance_scale":3.8,"num_inference_steps":34}}
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
    if not bold.exists() or bold.stat().st_size < 5000:
        try:
            rr=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=40)
            if rr.status_code==200 and len(rr.content)>5000:
                bold.write_bytes(rr.content)
        except: return None
    return bold if bold.exists() and bold.stat().st_size>5000 else None

def add_text_action_reaction(image_path, action_ar, reaction_ar, action_en, reaction_en):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    font_file=ensure_arabic_font()
    has_arabic = font_file and font_file.exists()
    img=Image.open(image_path).convert("RGB")
    W,H=img.size
    def fix_ar(t):
        try:
            if not has_arabic or not t: return ""
            return get_display(arabic_reshaper.reshape(t))
        except: return ""
    try:
        if has_arabic:
            f_big=ImageFont.truetype(str(font_file), 44)
            f_med=ImageFont.truetype(str(font_file), 36)
        else: f_big=None; f_med=None
        f_en_big=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        f_en_med=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        f_label=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        f_big=None; f_med=None
        f_en_big=ImageFont.load_default(); f_en_med=f_en_big; f_label=f_en_big
        has_arabic=False
    overlay = Image.new('RGBA', (W, H), (0,0,0,0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle([0, 0, W, 95], fill=(245,233,201,230))
    mid = H//2
    odraw.rectangle([0, mid-45, W, mid+45], fill=(20,20,20,160))
    odraw.rectangle([0, H-85, W, H], fill=(245,233,201,230))
    img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
    draw = ImageDraw.Draw(img)
    try:
        if has_arabic and f_big:
            draw.text((W//2, 30), fix_ar(action_ar), font=f_big, fill="#1a1a1a", anchor="mm")
        draw.text((W//2, 70), action_en, font=f_en_big, fill="#333", anchor="mm")
        draw.text((W//2, mid), "فعل → رد فعل | ACTION → REACTION", font=f_label, fill="white", anchor="mm")
        if has_arabic and f_med:
            draw.text((W//2, H-55), fix_ar(reaction_ar), font=f_med, fill="#1a1a1a", anchor="mm")
        draw.text((W//2, H-22), reaction_en, font=f_en_med, fill="#2a2a2a", anchor="mm")
    except Exception as e: log(f"draw {e}")
    img.save(image_path, quality=98)
    return image_path

def create_cinematic_with_music(image_path, mood, duration=10):
    from moviepy.editor import ImageClip, AudioFileClip
    MUSIC={
        "hopeful_joy":"https://cdn.pixabay.com/download/audio/2022/03/10/audio_4e2c7bb6f3.mp3",
        "calm_healing":"https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
        "reflective":"https://cdn.pixabay.com/download/audio/2022/10/30/audio_8b64a1c5e7.mp3",
        "sad_deep":"https://cdn.pixabay.com/download/audio/2024/09/19/audio_8bfa6c1f48.mp3",
    }
    url = MUSIC.get(mood, MUSIC["hopeful_joy"])
    music_path = Path(tempfile.gettempdir())/f"m_{random.randint(1,9999)}.mp3"
    try: download_file(url, music_path)
    except: music_path = None
    clip = ImageClip(str(image_path), duration=duration)
    clip = clip.resize(lambda t: 1.20 + 0.30*t/duration).set_position(lambda t: ('center', f"{-5 - 70*t/duration}%"))
    if music_path and music_path.exists():
        try:
            audio = AudioFileClip(str(music_path)).subclip(0, duration).volumex(0.95).audio_fadein(0.3).audio_fadeout(1.0)
            clip = clip.set_audio(audio)
        except: pass
    out = Path(tempfile.gettempdir())/f"cin_{int(time.time())}_{random.randint(100,999)}.mp4"
    clip.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
    return out

TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":t}, timeout=30)
    except: pass

def send_photo(cid,p):
    try:
        with open(p,"rb") as f:
            files={"photo": ("hmisc.jpg", f, "image/jpeg")}
            requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files=files, timeout=180)
    except Exception as e: log(f"photo {e}")

def send_video(cid,p):
    try:
        with open(p,"rb") as f:
            files={"video": ("hmisc.mp4", f, "video/mp4")}
            requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files=files, timeout=300)
    except Exception as e: log(f"video {e}")

processing=set(); plock=threading.Lock()

def process(cid, cinematic=False):
    try:
        with plock:
            if cid in processing:
                send_text(cid,"⏳ قاعد يولد... انتظر خلص")
                return
            processing.add(cid)
        send_text(cid,"🧠 فعل ورد فعل حقيقي...\n🎨 صورة واحدة 0.08$ تطابق 100%...\n🎬 موسيقى ترند عالية بدون راوي...")
        idea=brain_generate()
        img=generate_pro_image(idea["scene_action_en"], idea["scene_reaction_en"])
        add_text_action_reaction(img, idea["action_ar"], idea["reaction_ar"], idea["action_en"], idea["reaction_en"])
        if cinematic:
            vid=create_cinematic_with_music(img, idea["mood"], duration=10)
            send_video(cid, vid)
            Path(vid).unlink(missing_ok=True)
        else:
            send_photo(cid, img)
        Path(img).unlink(missing_ok=True)
        txt=f"🔹 فعل: {idea['action_ar']}\n{idea['action_en']}\n\n🔸 رد فعل: {idea['reaction_ar']}\n{idea['reaction_en']}"
        send_text(cid, txt)
    except Exception as e:
        import traceback
        log(f"ERR {traceback.format_exc()[-800:]}")
        send_text(cid,f"Error: {repr(e)[:700]}")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    # يدوي فقط - ما بيعمل شي اوتوماتيك
    if text.startswith("/start"):
        send_text(chat,"يدوي فقط - ما في اوتوماتيك\n/hikma صورة\n/cinema فيديو ترند\n/brain عدد الافكار"); return
    if text.startswith("/brain"):
        mem=load_memory(); send_text(chat,f"سلسلة: {len(mem['ideas'])}"); return
    if text.startswith("/ping"):
        send_text(chat,"OK manual only - no auto next"); return
    if "/cinema" in text:
        threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
    if "/hikma" in text:
        threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    # اي رسالة عادية = يتجاهلها - ما بيعمل التالي اوتوماتيك
    return

@app.get("/")
def home(): return f"MANUAL ONLY {len(load_memory()['ideas'])}",200

@app.post("/telegram/webhook")
def wh():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK",200

def setup_wh():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try: requests.post(f"{TELEGRAM_API}/setWebhook", json={"url":RENDER_EXTERNAL_URL+"/telegram/webhook"}, timeout=20)
    except: pass

if __name__=="__main__": setup_wh(); app.run(host="0.0.0.0", port=PORT, threaded=True)
