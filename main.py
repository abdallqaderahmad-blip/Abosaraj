import os, sys, json, time, threading, re, tempfile, random, hashlib, base64
from pathlib import Path
import requests
from flask import Flask, request

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
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
TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def log(m): print(f"[FINAL] {str(m)[:700]}", flush=True)

# ========== أداة كشف الأخطاء - تظهر بالبوت مباشرة ==========
def send_error_report(cid, where, error):
    import traceback
    tb = traceback.format_exc()[-1500:]
    err_text = f"""🚨 خطأ بالنظام
📍 المكان: {where}
❌ الخطأ: {str(error)[:600]}

📄 التفاصيل:
{tb}

🕒 {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
    if len(err_text) > 4000: err_text = err_text[:4000] + "\n... truncated"
    try:
        if cid: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":err_text}, timeout=30)
    except: pass
    log(f"ERROR {where}: {error}\n{tb}")

def load_memory():
    if MEMORY_FILE.exists():
        try: return json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
        except: return {"ideas":[],"scenes":[],"last_music":""}
    return {"ideas":[],"scenes":[],"last_music":""}

def save_memory(mem):
    mem["ideas"]=mem["ideas"][-200:]; mem["scenes"]=mem["scenes"][-200:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False), encoding='utf-8')

def is_dup(s, mem):
    for old in mem["scenes"]:
        if old[:40] in s or s[:40] in old: return True
    return False

# ========== 1. عقل فعل ورد فعل سلسلة ==========
def brain_generate():
    mem=load_memory()
    fallback={
        "action_ar":"تترك يدك ترتجف وحيدة",
        "reaction_ar":"فيسقط قلبك بصمت ثقيل",
        "action_en":"You leave hand shaking alone",
        "reaction_en":"Your heart falls silently heavy",
        "character":"same young man 25yo, black hoodie, short beard, dark hair, same face",
        "location":"same small dark room window light from left",
        "scene_action_en":"sitting alone hand shaking head down sad",
        "scene_reaction_en":"same sitting heart glowing broken tears hopeful",
        "mood":"sad_deep","title":"Shaking Hand"
    }
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-15:]) or "none"
    for _ in range(7):
        try:
            prompt=f"""Avoid: {recent}
JSON ONLY:
{{
"action_ar":"فعل حقيقي 5-6 كلمات شاعرية مثل تترك يدك ترتجف وحيدة",
"reaction_ar":"رد فعل حقيقي 5-6 كلمات مثل فيسقط قلبك بصمت ثقيل",
"action_en":"real action 5-6 words like You leave hand shaking alone",
"reaction_en":"real reaction 5-6 words like Your heart falls silently",
"character":"LOCKED same person: e.g. same young woman 23yo white shirt long hair same face",
"location":"LOCKED same place: e.g. same bedroom soft light left",
"scene_action_en":"action visual: sitting hand shaking sad",
"scene_reaction_en":"reaction visual: same sitting heart glowing hopeful",
"mood":"sad_deep",
"title":"unique title"
}} Real Arabic, lock character/location.
"""
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=1.4, max_completion_tokens=900, messages=[{"role":"system","content":"You are viral action-reaction director. Lock small details. Real Arabic no placeholders."},{"role":"user","content":prompt}])
            content=res.choices[0].message.content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p: content=p.replace("json","").strip(); break
            j=json.loads(content)
            if "5 كلمات" in j.get("action_ar",""): continue
            if "5 words" in j.get("action_en","").lower(): continue
            if len(j.get("action_ar","").split())<4: continue
            combined=j["character"]+j["location"]+j["scene_action_en"]
            if not is_dup(combined, mem):
                mem["ideas"].append(j.get("title","")); mem["scenes"].append(combined); save_memory(mem)
                return j
        except Exception as e: log(f"brain {e}"); time.sleep(0.5)
    return fallback

# ========== 2. PIAPI بدون شوائب وعلامات مائية ==========
def extract_url(j):
    import json as js; s=js.dumps(j)
    m=re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    return None

def piapi_wait(tid):
    headers={"X-API-KEY": PIAPI_API_KEY}
    url=f"{PIAPI_BASE}/{tid}"
    for _ in range(140):
        r=requests.get(url, headers=headers, timeout=30)
        body=r.json(); data=body.get("data") or body
        status=str(data.get("status","")).lower()
        if status in ("completed","complete","success","succeeded"):
            u=extract_url(body)
            if u: return u
        if status in ("failed","error","canceled"): raise RuntimeError(str(body)[:800])
        time.sleep(3)
    raise TimeoutError("piapi timeout")

def download_file(url, path):
    path=Path(path)
    with requests.get(url, timeout=120, stream=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_generate(prompt, ref_path=None, strength=0.42):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, blurry, low quality, artifacts, stripes, noise, deformed, extra fingers, glitch, dirty"
    full_prompt=f"{prompt}, ultra clean sharp 8k, no watermark, no text, no logo, no artifacts, masterpiece"
    if ref_path and Path(ref_path).exists():
        with open(ref_path,"rb") as f: b64=base64.b64encode(f.read()).decode()
        payload={"model":"Qubico/flux1-dev","task_type":"img2img","input":{"prompt":full_prompt[:1500],"image":b64,"strength":strength,"guidance_scale":4.5,"num_inference_steps":36,"width":1024,"height":1024}}
    else:
        payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":full_prompt[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":4.5,"num_inference_steps":38,"negative_prompt":negative}}
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=70)
    r.raise_for_status()
    tid=r.json().get("data",{}).get("task_id")
    url=piapi_wait(tid)
    tmp=Path(tempfile.gettempdir())/f"gen_{hashlib.md5(str(time.time()+random.random()).encode()).hexdigest()[:8]}.jpg"
    download_file(url, tmp)
    return tmp

def generate_locked_pair(idea):
    char=idea["character"]; loc=idea["location"]
    p1=f"{char}, {loc}, {idea['scene_action_en']}, photorealistic cinematic portrait, same clothes same face, ultra clean, 1:1 square"
    img1=piapi_generate(p1, None)
    p2=f"{char}, {loc}, {idea['scene_reaction_en']}, SAME face SAME clothes SAME background SAME lighting direction, only emotion changed, photorealistic cinematic portrait, ultra clean, 1:1 square"
    img2=piapi_generate(p2, img1, strength=0.42)
    from PIL import Image, ImageFilter, ImageDraw
    im1=Image.open(img1).convert("RGB").resize((1080,960), Image.LANCZOS)
    im2=Image.open(img2).convert("RGB").resize((1080,960), Image.LANCZOS)
    im1=im1.filter(ImageFilter.MedianFilter(size=3))
    im2=im2.filter(ImageFilter.MedianFilter(size=3))
    combined=Image.new("RGB",(1080,1920),(10,10,10))
    combined.paste(im1,(0,0)); combined.paste(im2,(0,960))
    d=ImageDraw.Draw(combined); d.rectangle([0,956,1080,964], fill=(20,20,20))
    out=Path(tempfile.gettempdir())/f"final_{int(time.time())}_{random.randint(1000,9999)}.jpg"
    combined.save(out, quality=98, subsampling=0)
    Path(img1).unlink(missing_ok=True); Path(img2).unlink(missing_ok=True)
    return out

# ========== 3. خط عربي صحيح ==========
def ensure_font():
    base=Path("/tmp/fonts"); base.mkdir(exist_ok=True)
    bold=base/"Amiri-Bold.ttf"
    if not bold.exists() or bold.stat().st_size<5000:
        try:
            rr=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=40)
            if rr.status_code==200 and len(rr.content)>5000: bold.write_bytes(rr.content)
        except: return None
    return bold if bold.exists() and bold.stat().st_size>5000 else None

TREND_MUSIC=[
    "https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",
    "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8a650a6.mp3",
    "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3",
    "https://cdn.pixabay.com/audio/2022/10/30/audio_8b64a1c5e7.mp3",
    "https://cdn.pixabay.com/audio/2023/01/01/audio_816821e627.mp3",
    "https://cdn.pixabay.com/audio/2022/06/25/audio_69f4b7fbc2.mp3",
    "https://cdn.pixabay.com/audio/2023/06/01/audio_15d2c4c0e7.mp3",
]
def get_music():
    mem=load_memory(); last=mem.get("last_music","")
    choices=[m for m in TREND_MUSIC if m!=last]
    pick=random.choice(choices) if choices else random.choice(TREND_MUSIC)
    mem["last_music"]=pick; save_memory(mem); return pick

# ========== 4. فيديو ريلز 9ث - زوم للصورة فقط ==========
def create_reels_video(image_path, idea, duration=9):
    from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
    from PIL import Image, ImageDraw, ImageFont
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        HAS_AR_LIB=True
    except: HAS_AR_LIB=False
    font_file=ensure_font(); has_ar=font_file and font_file.exists() and HAS_AR_LIB
    def fix_ar(t):
        try:
            if not has_ar or not t: return t
            return get_display(arabic_reshaper.reshape(t))
        except: return t
    base=ImageClip(str(image_path), duration=duration)
    base=base.resize(lambda t: 1.0 + 0.10*t/duration)
    base=base.set_position(lambda t: ('center', f"{- (t/duration)*100}"))
    W,H=1080,1920
    def make_box(ar, en, h=185):
        img=Image.new('RGBA',(W,h),(245,233,201,248))
        d=ImageDraw.Draw(img)
        try:
            f_ar=ImageFont.truetype(str(font_file), 46) if has_ar else ImageFont.load_default()
            f_en=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        except: f_ar=f_en=ImageFont.load_default()
        d.text((W//2, h//2-20), fix_ar(ar), font=f_ar, fill="#0a0a0a", anchor="mm", align="center")
        d.text((W//2, h//2+32), en, font=f_en, fill="#222222", anchor="mm", align="center")
        p=Path(tempfile.gettempdir())/f"box_{random.randint(1,999999)}.png"; img.save(p); return p
    def make_mid():
        img=Image.new('RGBA',(W,68),(0,0,0,200))
        d=ImageDraw.Draw(img)
        try: f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except: f=ImageFont.load_default()
        d.text((W//2,34), "فعل → رد فعل | ACTION → REACTION", font=f, fill="white", anchor="mm")
        p=Path(tempfile.gettempdir())/f"mid_{random.randint(1,9999)}.png"; img.save(p); return p
    top_p=make_box(idea["action_ar"], idea["action_en"])
    bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"])
    mid_p=make_mid()
    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    mid_c=ImageClip(str(mid_p), duration=duration).set_position((0, H//2-34))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-185))
    bg=Image.new('RGB',(W,H),(12,12,12))
    bg_p=Path(tempfile.gettempdir())/f"bg_{random.randint(1,9999)}.jpg"; bg.save(bg_p)
    bg_c=ImageClip(str(bg_p), duration=duration)
    final=CompositeVideoClip([bg_c, base, top_c, mid_c, bottom_c], size=(W,H)).set_duration(duration)
    music_url=get_music()
    mp=Path(tempfile.gettempdir())/f"mus_{random.randint(1,99999)}.mp3"
    try:
        download_file(music_url, mp)
        audio=AudioFileClip(str(mp)).subclip(0,duration).volumex(0.90).audio_fadein(0.4).audio_fadeout(0.9)
        final=final.set_audio(audio)
    except Exception as e: log(f"music {e}")
    out=Path(tempfile.gettempdir())/f"reel_{int(time.time())}.mp4"
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac', preset='ultrafast', bitrate="5000k", logger=None)
    for p in [top_p,bottom_p,mid_p,bg_p,mp]: Path(p).unlink(missing_ok=True)
    return out

# ========== Telegram مع أداة كشف ==========
def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":t}, timeout=30)
    except: pass
def send_photo(cid,p):
    try:
        with open(p,"rb") as f:
            r=requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files={"photo":("final.jpg",f,"image/jpeg")}, timeout=180)
            if r.status_code!=200: raise RuntimeError(r.text[:600])
    except Exception as e:
        send_error_report(cid, "send_photo", e); raise
def send_video(cid,p):
    try:
        with open(p,"rb") as f:
            r=requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files={"video":("reel.mp4",f,"video/mp4")}, timeout=300)
            if r.status_code!=200: raise RuntimeError(r.text[:600])
    except Exception as e:
        send_error_report(cid, "send_video", e); raise

processing=set(); plock=threading.Lock()
def process(cid, cinematic=False):
    combined=None; vid=None
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        send_text(cid,"🧠 فعل ورد فعل متكامل...\n🎨 صورتين جديدة حسب الكلام...\n🎵 موسيقى ترند مختلفة...")

        try: idea=brain_generate()
        except Exception as e: send_error_report(cid, "brain_generate", e); raise

        try: combined=generate_locked_pair(idea)
        except Exception as e: send_error_report(cid, "generate_locked_pair (PIAPI)", e); raise

        if cinematic:
            try:
                vid=create_reels_video(combined, idea, duration=9)
                send_video(cid, vid)
            except Exception as e: send_error_report(cid, "create_reels_video", e); raise
        else:
            send_photo(cid, combined)

        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)

        send_text(cid,f"✅ تم\n🔹 فعل: {idea['action_ar']}\n{idea['action_en']}\n\n🔸 رد فعل: {idea['reaction_ar']}\n{idea['reaction_en']}\n\n/cinema للفيديو التالي - يدوي")

    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        send_text(cid,f"❌ فشل - تفاصيل الخطأ فوق 👆\nجرب /cinema مرة ثانية")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"): send_text(chat,"نموذج فعل ورد فعل متكامل\n✅ كشف أخطاء مباشر\n✅ صورتين جديدة كل فيديو حسب الكلام\n✅ تطابق تفاصيل + بدون شوائب\n✅ ترجمة صحيحة + موسيقى مختلفة\n/hikma صورة\n/cinema فيديو 9ث ريلز - يدوي"); return
        if text.startswith("/ping"): send_text(chat,"✅ model + error reporting ON"); return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    except Exception as e:
        try:
            cid=(upd.get("message") or {}).get("chat",{}).get("id")
            if cid: send_error_report(cid, "handle_update", e)
        except: log(f"fatal {e}")

@app.get("/")
def home(): return f"FINAL MODEL + ERROR TOOL {len(load_memory()['ideas'])}",200
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
