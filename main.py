import os, sys, json, time, threading, re, tempfile, random, hashlib
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
AUTOMATIC_ENABLED = False

groq=None
try:
    from groq import Groq
    if GROQ_API_KEY: groq=Groq(api_key=GROQ_API_KEY)
except: groq=None

app=Flask(__name__)
PIAPI_BASE="https://api.piapi.ai/api/v1/task"
MEMORY_FILE=Path("/tmp/hmisc_brain_memory.json")
ARCHIVE_DIR=Path("/tmp/archive_images"); ARCHIVE_DIR.mkdir(exist_ok=True)
TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def log(m): print(f"[V16-CLEAN] {str(m)[:950]}", flush=True)

def send_error_report(cid, where, error, extra=""):
    import traceback
    tb=traceback.format_exc()[-800:]
    if "music" in where.lower():
        log(f"music warn {where} {error}")
        return
    err_text=f"🚨 غلط\n📍 {where}\n❌ {str(error)[:600]}\n📄 {tb[:500]}"
    try:
        if cid: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":err_text}, timeout=20)
    except: pass

def load_memory():
    if MEMORY_FILE.exists():
        try:
            mem=json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
            for k in ["music_history","ideas","scenes"]:
                if k not in mem: mem[k]=[]
            return mem
        except: pass
    return {"ideas":[],"scenes":[],"last_music":"","music_history":[]}

def save_memory(mem):
    mem["ideas"]=mem["ideas"][-300:]; mem["scenes"]=mem["scenes"][-300:]; mem["music_history"]=mem.get("music_history",[])[-15:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False), encoding='utf-8')

def is_dup(s, mem):
    return any(old[:12] in s[:12] for old in mem["scenes"][-150:])

def extract_url(j):
    import json as js; s=js.dumps(j)
    m=re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    return m.group(0) if m else None

def piapi_wait(tid):
    headers={"X-API-KEY": PIAPI_API_KEY}
    url=f"{PIAPI_BASE}/{tid}"
    for _ in range(150):
        r=requests.get(url, headers=headers, timeout=30)
        body=r.json(); data=body.get("data") or body
        status=str(data.get("status","")).lower()
        if status in ("completed","complete","success","succeeded"):
            u=extract_url(body)
            if u: return u
        if status in ("failed","error","canceled"): raise RuntimeError(str(body)[:800])
        time.sleep(3)
    raise TimeoutError("piapi timeout")

def download_file(url, path, is_music=False):
    path=Path(path)
    headers={"User-Agent":"Mozilla/5.0"} if not is_music else {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    with requests.get(url, timeout=120, stream=True, headers=headers, allow_redirects=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*256):
                if c: f.write(c)
    return path

def piapi_gen(prompt):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, blurry, low quality, cartoon, anime, female, woman, girl"
    base="same young man 28yo male arab short black hair light beard white t-shirt same face identical male"
    full=f"{base}, {prompt}, ultra clean sharp 8k, no watermark no text no logo, photorealistic"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":full[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":6.8,"num_inference_steps":45,"negative_prompt":negative}}
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    tid=r.json().get("data",{}).get("task_id")
    url=piapi_wait(tid)
    tmp=Path(tempfile.gettempdir())/f"g_{hashlib.md5(str(time.time()+random.random()).encode()).hexdigest()[:8]}.jpg"
    download_file(url, tmp)
    return tmp

def check_quality(p):
    from PIL import Image
    try:
        path=Path(p)
        kb=path.stat().st_size//1024
        if path.stat().st_size<40000: return False, f"صغير {kb}KB"
        im=Image.open(path); w,h=im.size
        if w<900 or h<900: return False, f"دقة {w}x{h}"
        return True, f"OK {w}x{h} {kb}KB"
    except Exception as e: return False, str(e)

SPECIAL_VIRAL_HOOKS = [
    "تحفظهم ككلمة مرور -> فينسونك كإعلان",
    "تضع قلبك على العام -> فيضعونك على الصامت",
    "تترك مشاعرك متصلة -> فيستهلكون بطاريتك ويرحلون",
    "تجعلهم المفضلة -> فيجعلونك سجل المكالمات",
    "تكتبهم في ملاحظاتك -> فيكتبونك في المنسيات",
]

def brain_generate_smart(cid=None):
    mem=load_memory()
    fallback={"action_ar":"تحفظهم ككلمة مرور","reaction_ar":"فينسونك كإعلان","action_en":"SAVE AS PASSWORD","reaction_en":"FORGOTTEN LIKE ADS","character":"same young man 28yo male white t-shirt short black hair light beard same face","location":"same small apartment evening warm light window left","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright","scene_reaction_en":"TOTALLY DIFFERENT lying on floor head in hands crying phone far away dark lonely","mood":"sad_deep","title":"Password Ad"}
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-80:]) or "none"
    for _ in range(12):
        try:
            prompt=f"""Avoid: {recent}
Create viral hook 3-4 words modern digital. Return JSON ONLY: {{"action_ar":"فعل 3-4","reaction_ar":"رد 3-4","action_en":"3 WORDS EN CAPS","reaction_en":"3 WORDS EN CAPS","character":"same young man 28yo male white t-shirt short black hair light beard same face - LOCKED","location":"same small apartment evening warm light window left - LOCKED","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright","scene_reaction_en":"TOTALLY DIFFERENT lying on floor head in hands crying phone far away dark","mood":"sad_deep","title":"two words"}}"""
            res=groq.chat.completions.create(model="llama-3.3-70b-versatile", temperature=0.9, max_completion_tokens=600, messages=[{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt}])
            content=res.choices[0].message.content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p: content=p.replace("json","").strip(); break
            s=content.find("{"); e=content.rfind("}")
            if s==-1: continue
            j=json.loads(content[s:e+1])
            ar1=j.get("action_ar","").strip(); ar2=j.get("reaction_ar","").strip()
            if not ar1 or not ar2 or len(ar1.split())>4 or len(ar2.split())>4: continue
            if is_dup(ar1[:12], mem): continue
            mem["ideas"].append(j.get("title","")); mem["scenes"].append(ar1[:12]); save_memory(mem)
            return j
        except: time.sleep(0.5); continue
    return fallback

def generate_two_in_one(idea, cid=None):
    char=idea["character"]; loc=idea["location"]
    p1=f"{char}, {loc}, {idea['scene_action_en']}, photorealistic 8k"
    p2=f"{char}, {loc}, {idea['scene_reaction_en']}, SAME face SAME shirt BUT different pose, photorealistic 8k"
    img1=piapi_gen(p1)
    ok,msg=check_quality(img1)
    if not ok: Path(img1).unlink(missing_ok=True); raise RuntimeError(msg)
    img2=piapi_gen(p2)
    ok2,msg2=check_quality(img2)
    if not ok2: Path(img1).unlink(missing_ok=True); Path(img2).unlink(missing_ok=True); raise RuntimeError(msg2)
    from PIL import Image, ImageDraw
    top=Image.open(img1).convert("RGB").resize((1080,960), Image.LANCZOS)
    bottom=Image.open(img2).convert("RGB").resize((1080,960), Image.LANCZOS)
    combined=Image.new("RGB",(1080,1920),(10,10,10))
    combined.paste(top,(0,0)); combined.paste(bottom,(0,960))
    ImageDraw.Draw(combined).rectangle([0,956,1080,964], fill=(255,255,255))
    out=Path(tempfile.gettempdir())/f"final_{int(time.time())}.jpg"
    combined.save(out, quality=98, subsampling=0)
    Path(img1).unlink(missing_ok=True); Path(img2).unlink(missing_ok=True)
    return out, f"{msg} | {msg2}"

def ensure_fonts():
    base=Path("/tmp/fonts"); base.mkdir(exist_ok=True)
    amiri=base/"Amiri-Bold.ttf"
    dejavu=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not amiri.exists() or amiri.stat().st_size<5000:
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=40)
            if r.status_code==200 and len(r.content)>5000: amiri.write_bytes(r.content)
        except: pass
    return amiri if amiri.exists() and amiri.stat().st_size>5000 else None, dejavu

# موسيقى مباشرة مضمونة - بدون?filename= - شغالة 100% من Render
REAL_MUSIC = [
    "https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",
    "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3",
    "https://cdn.pixabay.com/audio/2022/10/30/audio_fbd82ff3ef.mp3",
    "https://cdn.pixabay.com/audio/2022/03/24/audio_2a9aafb6cb.mp3",
    "https://cdn.pixabay.com/audio/2022/03/10/audio_c8c8a650f6.mp3",
    "https://cdn.pixabay.com/audio/2021/10/25/audio_51c0dbe73f.mp3",
    "https://cdn.pixabay.com/audio/2022/08/23/audio_5d9d78a6ff.mp3",
    "https://cdn.pixabay.com/audio/2022/01/18/audio_8b6a2e2c3f.mp3",
]

def get_music():
    mem=load_memory()
    hist=mem.get("music_history",[])
    avail=[m for m in REAL_MUSIC if m not in hist]
    if not avail: avail=REAL_MUSIC; hist=[]
    pick=random.choice(avail)
    hist.append(pick)
    mem["music_history"]=hist[-10:]
    save_memory(mem)
    return pick

def create_reels_video(image_path, idea, duration=9, cid=None):
    from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
    from PIL import Image, ImageDraw, ImageFont
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        HAS_AR=True
    except: HAS_AR=False

    amiri_path, dejavu_path = ensure_fonts()
    has_ar = amiri_path and amiri_path.exists() and HAS_AR
    def fix_ar(t):
        t=t.strip()
        if has_ar:
            try: return get_display(arabic_reshaper.reshape(t))
            except: return t
        return t

    W,H=1080,1920
    # زوم 18% واضح وما بقص الراس
    base=ImageClip(str(image_path), duration=duration).resize(lambda t: 1.0 + 0.18*t/duration).set_position('center')

    def make_box(ar_text, en_text, is_top=True):
        h=400
        bg=(242,230,195) if is_top else (14,14,14)
        img=Image.new('RGB',(W,h),bg) # RGB مش RGBA عشان ما يعمل خربشة
        d=ImageDraw.Draw(img)
        try:
            f_ar=ImageFont.truetype(str(amiri_path), 70) if has_ar else ImageFont.truetype(str(dejavu_path), 68) if dejavu_path.exists() else ImageFont.load_default()
            f_en=ImageFont.truetype(str(dejavu_path), 52) if dejavu_path.exists() else ImageFont.load_default()
        except: f_ar=f_en=ImageFont.load_default()

        ar_fixed=fix_ar(ar_text)
        en_clean=en_text.strip().upper() # إنجليزي كابيتال واضح

        # كلام نظيف بدون rounded_rectangle
        try:
            # عربي
            bbox_ar=d.textbbox((0,0), ar_fixed, font=f_ar)
            d.text(((W-(bbox_ar[2]-bbox_ar[0]))//2, 60), ar_fixed, font=f_ar, fill=(15,15,15) if is_top else (242,230,195))
            # إنجليزي - نفس الوضوح - بدون خلفية سوداء تخربش
            bbox_en=d.textbbox((0,0), en_clean, font=f_en)
            d.text(((W-(bbox_en[2]-bbox_en[0]))//2, 185), en_clean, font=f_en, fill=(50,50,50) if is_top else (210,210,210))
            # خط صغير
            f_s=ImageFont.truetype(str(dejavu_path), 20) if dejavu_path.exists() else ImageFont.load_default()
            sub="ARABIC + ENGLISH"
            bbox_s=d.textbbox((0,0), sub, font=f_s)
            d.text(((W-(bbox_s[2]-bbox_s[0]))//2, 300), sub, font=f_s, fill=(130,130,130) if is_top else (90,90,90))
        except Exception as ex:
            log(f"box fail {ex}")
            d.text((80,60), ar_fixed, fill=(0,0,0))
            d.text((80,180), en_clean, fill=(80,80,80))

        p=Path(tempfile.gettempdir())/f"b_{random.randint(1,9999999)}.png"
        img.save(p, quality=95)
        return p

    top_p=make_box(idea["action_ar"], idea["action_en"], True)
    bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"], False)
    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-400))
    final=CompositeVideoClip([base, top_c, bottom_c], size=(W,H)).set_duration(duration)

    # موسيقى مضمونة
    mp=Path(tempfile.gettempdir())/f"m_{random.randint(1,99999)}.mp3"
    audio_added=False
    for _ in range(8):
        try:
            url=get_music()
            log(f"music try {url[-35:]}")
            # تحميل مباشر بدون?filename
            r=requests.get(url, timeout=40, headers={"User-Agent":"Mozilla/5.0"}, stream=True)
            if r.status_code!=200: log(f"music http {r.status_code}"); continue
            with open(mp,'wb') as f:
                for ch in r.iter_content(8192): f.write(ch)
            if not mp.exists(): continue
            kb=mp.stat().st_size//1024
            log(f"music {kb}KB")
            if kb < 20: mp.unlink(missing_ok=True); continue # أقل من 20KB تالف

            full_audio=AudioFileClip(str(mp))
            dur=full_audio.duration
            log(f"music dur {dur:.1f}s")
            if dur < 0.8: full_audio.close(); mp.unlink(missing_ok=True); continue

            if dur < duration:
                try:
                    from moviepy.audio.fx.all import audio_loop
                    audio=audio_loop(full_audio, duration=duration)
                except:
                    from moviepy.editor import concatenate_audioclips
                    audio=concatenate_audioclips([full_audio]*3).subclip(0,duration)
            else:
                audio=full_audio.subclip(0,duration)

            audio=audio.volumex(0.92).audio_fadein(0.4).audio_fadeout(0.7)
            final=final.set_audio(audio)
            audio_added=True
            log("music OK")
            break
        except Exception as e:
            log(f"music err {e}")
            if mp.exists(): mp.unlink(missing_ok=True)
            continue

    out=Path(tempfile.gettempdir())/f"reel_{int(time.time())}.mp4"
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac' if audio_added else None, preset='ultrafast', bitrate="8000k", logger=None)
    for p in [top_p,bottom_p,mp]:
        try: Path(p).unlink(missing_ok=True)
        except: pass
    log(f"FINAL audio={audio_added} size={Path(out).stat().st_size//1024}KB")
    return out, audio_added

def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":t}, timeout=30)
    except: pass
def send_photo(cid,p):
    with open(p,"rb") as f: requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files={"photo":("f.jpg",f,"image/jpeg")}, timeout=180)
def send_video(cid,p):
    with open(p,"rb") as f:
        r=requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files={"video":("reel.mp4",f,"video/mp4")}, timeout=300)
        if r.status_code!=200: raise RuntimeError(r.text[:600])

processing=set(); plock=threading.Lock()
def process(cid, cinematic=False):
    combined=None; vid=None
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        mem=load_memory()
        send_text(cid,f"✨ V16 CLEAN 9ث\n🎥 زوم 18% واضح\n🔤 عربي 70px + إنجليزي 52px واضح\n🎵 موسيقى مضمونة {len(mem.get('music_history',[]))}/8")

        idea=brain_generate_smart(cid)
        send_text(cid,f"✅ {idea['action_ar']} -> {idea['reaction_ar']}\nEN {idea['action_en']} -> {idea['reaction_en']}")

        combined,qmsg=generate_two_in_one(idea, cid)
        send_text(cid,f"✅ صورتين | {qmsg}")

        if cinematic:
            vid,has_audio=create_reels_video(combined, idea, 9, cid)
            send_text(cid,f"✅ فيديو {'🎵 بموسيقى' if has_audio else '🔇 بدون'} | بدون خربشة")
            send_video(cid, vid)
        else:
            send_photo(cid, combined)

        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        send_text(cid,f"✅ جاهز\n{idea['action_ar']}\n{idea['reaction_ar']}\n/cinema")

    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        send_error_report(cid, "process", e)
        send_text(cid,"❌ فشل")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"): send_text(chat,"V16 CLEAN\n✅ بدون خربشة\n✅ عربي+إنجليزي واضح\n✅ زوم 18%\n✅ موسيقى مضمونة\n/hikma\n/cinema"); return
        if text.startswith("/ping"): send_text(chat,f"✅ V16 | {len(list(ARCHIVE_DIR.glob('*.jpg')))}"); return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    except Exception as e: log(f"handle {e}")

@app.get("/")
def home(): return f"V16 CLEAN {len(list(ARCHIVE_DIR.glob('*.jpg')))}",200
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
