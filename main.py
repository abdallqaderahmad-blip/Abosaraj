import os, json, time, threading, re, tempfile, random, hashlib
from pathlib import Path
import requests
from flask import Flask, request

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

def log(m): print(f"[BRAIN] {m}", flush=True)

# ========== العقل - ذاكرة الأفكار عشان ما نكرر ==========
def load_memory():
    if MEMORY_FILE.exists():
        try: return json.loads(MEMORY_FILE.read_text())
        except: return {"ideas": [], "scenes": []}
    return {"ideas": [], "scenes": []}

def save_memory(mem):
    # نحتفظ بآخر 100 فكرة بس
    mem["ideas"]=mem["ideas"][-100:]
    mem["scenes"]=mem["scenes"][-100:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False))

def is_duplicate(new_scene, mem):
    # نتأكد الصورة مش مكررة
    for s in mem["scenes"]:
        if s in new_scene or new_scene in s: return True
    return False

# ========== العقل المولد - يفكر بأفكار جديدة على نفس المحتوى ==========
BRAIN_PROMPT = """
You are hmisc brain. Generate ONE new viral wisdom idea.
You have memory of previous ideas: {memory}

Rules:
- NEVER repeat same scenario (drowning, carrot, hole already used)
- Think of NEW deep human scenarios: fear, hope, patience, rizq, envy, loneliness, trust in God, time, effort
- Each idea must be visually drawable with yellow bean character
- Be creative: bean on cliff, bean with heavy bag, bean looking at stars, bean in maze, bean planting seed, bean with broken chain, bean with hourglass
- Output JSON only
"""

def brain_generate():
    mem=load_memory()
    fallback={
        "hikma_ar":"لا تحمل هم الغد فغدك بيد الله",
        "full_ar":"ما تخافش من بكرة، رزق بكرة على رب بكرة، عيش يومك وتوكل",
        "hikma_en":"Don't carry tomorrow's worry, tomorrow is in God's hands",
        "full_en":"Don't fear tomorrow, tomorrow's provision is on tomorrow's Lord, live today and trust",
        "scene_en":"small yellow bean character carrying huge heavy bag on back walking on thin rope over abyss, below safe ground, two panels",
        "mood":"anxious_fear",
        "title":"هم الغد"
    }
    if not groq: return fallback

    # نجيب آخر 10 أفكار عشان ما نكرر
    recent = "\n".join(mem["ideas"][-10:])
    for attempt in range(4): # 4 محاولات لحد ما نجيب فكرة جديدة
        try:
            res=groq.chat.completions.create(
                model=GROQ_MODEL, temperature=1.0, max_completion_tokens=2000,
                response_format={"type":"json_object"},
                messages=[{"role":"system","content":BRAIN_PROMPT.format(memory=recent)},
                          {"role":"user","content":"Generate new viral hmisc idea, totally different scenario, JSON: {hikma_ar, full_ar, hikma_en, full_en, scene_en, mood, title}"}]
            )
            j=json.loads(res.choices[0].message.content)
            if not is_duplicate(j.get("scene_en",""), mem):
                # فكرة جديدة - خزنها
                mem["ideas"].append(j.get("title","")+" "+j.get("hikma_ar",""))
                mem["scenes"].append(j.get("scene_en",""))
                save_memory(mem)
                log(f"NEW IDEA: {j.get('title')} mood {j.get('mood')}")
                return j
        except Exception as e:
            log(f"brain retry {e}")
    return fallback

# ========== PiAPI - صور احترافية فقط ==========
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
    prompt=f"hmisc original style, vintage beige paper texture with brown age stains, two-panel vertical comic, watercolor ink crosshatch highly detailed, small yellow bean character thin black stick arms legs yellow watercolor body expressive simple face, {scene_en}, NO TEXT, no letters, no watermark, blank top, ultra detailed masterpiece 8k"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":prompt[:1500],"width":832,"height":1216,"num_images":1,"guidance_scale":3.5,"num_inference_steps":32}}
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    tid=r.json().get("data",{}).get("task_id")
    url=piapi_wait(tid)
    tmp=Path(tempfile.gettempdir())/f"pro_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}.jpg"
    download_file(url, tmp)
    return tmp

# ========== نص + كلمة share صغيرة + عربي انجليزي ==========
def add_text_with_share(image_path, ar_short, ar_full, en_short, en_full):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper
    from bidi.algorithm import get_display

    img=Image.open(image_path).convert("RGB")
    W,H=img.size
    draw=ImageDraw.Draw(img)

    def fix_ar(t):
        try: return get_display(arabic_reshaper.reshape(t))
        except: return t

    try:
        f_ar_big=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 46)
        f_ar_small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
        f_en_big=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
        f_en_small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        f_share=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except:
        f_ar_big=ImageFont.load_default(); f_ar_small=f_ar_big; f_en_big=f_ar_big; f_en_small=f_ar_big; f_share=f_ar_big

    # نص عربي فوق
    draw.text((W//2, 90), fix_ar(ar_short), font=f_ar_big, fill="#111", anchor="mm", align="center", stroke_width=1, stroke_fill="#f5e9c9")
    # انجليزي تحته
    draw.text((W//2, 142), en_short, font=f_en_big, fill="#222", anchor="mm")

    # النص الطويل تحت
    draw.text((W//2, H-115), fix_ar(ar_full), font=f_ar_small, fill="#1e1e1e", anchor="mm", align="center")
    draw.text((W//2, H-70), en_full, font=f_en_small, fill="#333", anchor="mm", align="center")

    # كلمة share صغيرة في الزاوية - بدون علامة مائية كبيرة
    draw.text((W-18, H-18), "share ♻️ hmisc", font=f_share, fill="#8a7a5a", anchor="rb")

    img.save(image_path, quality=96)
    return image_path

# ========== زوم سينمائي + موسيقى حسب النص ==========
MUSIC_LIBRARY = {
    "calm_healing": ["https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3", 0.32],
    "sad_deep": ["https://cdn.pixabay.com/audio/2024/09/19/audio_8bfa6c1f48.mp3", 0.30],
    "hopeful_joy": ["https://cdn.pixabay.com/audio/2022/10/30/audio_8b64a1c5e7.mp3", 0.35],
    "anxious_fear": ["https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3", 0.28],
    "reflective": ["https://cdn.pixabay.com/audio/2021/11/25/audio_8b64a1c5e7.mp3", 0.33],
    "motivational": ["https://cdn.pixabay.com/audio/2022/03/24/audio_8b64a1c5e7.mp3", 0.38],
}

def create_cinematic_with_music(image_path, mood, duration=10):
    from moviepy.editor import ImageClip, AudioFileClip
    music_url, vol = MUSIC_LIBRARY.get(mood, MUSIC_LIBRARY["calm_healing"])
    music_path=Path(tempfile.gettempdir())/f"{mood}.mp3"
    if not music_path.exists():
        try: download_file(music_url, music_path)
        except: music_path=None

    # زوم سينمائي ذكي - يبدأ من 1.0 الى 1.18 + حركة بسيطة يمين
    clip=ImageClip(str(image_path), duration=duration)
    def zoom(t): return 1 + 0.18*(t/duration)
    clip=clip.resize(zoom).set_position(lambda t: ('center', 'center'))

    if music_path and music_path.exists():
        try:
            audio=AudioFileClip(str(music_path)).subclip(0, duration).volumex(vol).audio_fadein(1.2).audio_fadeout(2)
            clip=clip.set_audio(audio)
        except: pass

    out=Path(tempfile.gettempdir())/f"final_{int(time.time())}.mp4"
    clip.write_videofile(str(out), fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
    return out

# ========== تليجرام ==========
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
        send_text(cid,"🧠 العقل بيولد فكرة جديدة غير مكررة...\n🎨 صورة احترافية جديدة...\n🎵 موسيقى مختلفة حسب المشاعر...")
        idea=brain_generate()
        img=generate_pro_image(idea["scene_en"])
        add_text_with_share(img, idea["hikma_ar"], idea["full_ar"], idea["hikma_en"], idea["full_en"])
        if cinematic:
            vid=create_cinematic_with_music(img, idea["mood"], duration=10)
            cap=f"{idea['hikma_ar']}\n{idea['hikma_en']}\n\n{idea['full_ar']}\n{idea['full_en']}\n\n🧠 فكرة #{len(load_memory()['ideas'])} | 🎵 {idea['mood']} | ♻️ share"
            send_video(cid, vid, cap)
            Path(vid).unlink(missing_ok=True)
        else:
            cap=f"{idea['hikma_ar']} | {idea['hikma_en']}\n\n{idea['full_ar']}\n{idea['full_en']}\n\n🧠 {idea['title']} | 🎵 {idea['mood']} | ♻️ share - بدون علامة مائية"
            send_photo(cid, img, cap)
        Path(img).unlink(missing_ok=True)
    except Exception as e:
        log(f"ERR {repr(e)}"); send_text(cid,f"❌ {str(e)[:900]}")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    if text.startswith("/start"): send_text(chat,"🧠 Hmisc Brain - عقل يولد أفكار ما بتخلص\n/hikma - صورة جديدة فكرة جديدة\n/cinema - فيديو زوم سينمائي + موسيقى مختلفة + share\n/brain - شوف كم فكرة ولدنا\n/ping"); return
    if text.startswith("/brain"):
        mem=load_memory(); send_text(chat,f"🧠 العقل ولد {len(mem['ideas'])} فكرة\nآخر 3:\n"+"\n".join(mem['ideas'][-3:])); return
    if text.startswith("/ping"): send_text(chat,f"🟢 Brain OK | {len(load_memory()['ideas'])} ideas | PiAPI {len(PIAPI_API_KEY)}"); return
    if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
    if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return

@app.get("/")
def home(): return f"Brain {len(load_memory()['ideas'])} ideas",200
@app.post("/telegram/webhook")
def wh(): upd=request.get_json(silent=True) or {}; threading.Thread(target=handle_update, args=(upd,), daemon=True).start(); return "OK",200
def setup_wh():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try: requests.post(f"{TELEGRAM_API}/setWebhook", data={"url":RENDER_EXTERNAL_URL+"/telegram/webhook"}, timeout=20)
    except: pass
if __name__=="__main__": setup_wh(); app.run(host="0.0.0.0", port=PORT, threaded=True)
