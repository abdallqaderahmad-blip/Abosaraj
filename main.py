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
GROQ_MODEL = os.getenv("GROQ_MODEL","llama-3.3-70b-versatile")
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

def log(m): print(f"[V12-FIXED-AUDIO] {str(m)[:900]}", flush=True)

def send_error_report(cid, where, error, extra=""):
    import traceback
    tb=traceback.format_exc()[-1000:]
    if "موسيقى" in where or "music" in where.lower():
        log(f"music warn {where} {error}")
        return
    err_text=f"🚨 غلط\n📍 {where}\n❌ {str(error)[:600]}\n📝 {extra[:400]}\n📄 {tb[:500]}"
    if len(err_text)>3500: err_text=err_text[:3500]
    try:
        if cid: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":err_text}, timeout=20)
    except: pass
    log(f"ERROR {where}: {error}")

def load_memory():
    if MEMORY_FILE.exists():
        try: return json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
        except: return {"ideas":[],"scenes":[],"last_music":""}
    return {"ideas":[],"scenes":[],"last_music":""}
def save_memory(mem):
    mem["ideas"]=mem["ideas"][-300:]; mem["scenes"]=mem["scenes"][-300:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False), encoding='utf-8')
def is_dup(s, mem):
    for old in mem["scenes"][-150:]:
        if old[:12] in s[:12]: return True
    return False

def extract_url(j):
    import json as js; s=js.dumps(j)
    m=re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    return None
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
    if is_music:
        headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36","Referer":"https://pixabay.com/","Accept":"*/*"}
    else:
        headers={"User-Agent":"Mozilla/5.0"}
    with requests.get(url, timeout=120, stream=True, headers=headers, allow_redirects=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*256):
                if c: f.write(c)
    return path

def piapi_gen(prompt):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, writing, blurry, low quality, lowres, bad anatomy, bad hands, extra fingers, deformed, duplicate, cartoon, anime, female, woman, girl"
    base="same young man 28yo male arab short black hair light beard white t-shirt same face identical male"
    full=f"{base}, {prompt}, ultra clean sharp 8k, no watermark no text no logo, photorealistic sharp focus male"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":full[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":6.5,"num_inference_steps":45,"negative_prompt":negative}}
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
        if not path.exists(): return False, "no file"
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
    fallback={
        "action_ar":"تحفظهم ككلمة مرور",
        "reaction_ar":"فينسونك كإعلان",
        "action_en":"Save them as password",
        "reaction_en":"They forget you like ad",
        "character":"same young man 28yo male white t-shirt short black hair light beard same face",
        "location":"same small apartment evening warm light window left",
        "scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright",
        "scene_reaction_en":"lying on floor alone head in hands sad phone far away dark lonely totally different pose",
        "mood":"sad_deep","title":"Password Ad"
    }
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-60:]) or "none"
    hooks_sample = random.sample(SPECIAL_VIRAL_HOOKS, 3)
    for attempt in range(15):
        try:
            prompt=f"""Avoid: {recent}
Inspiration: {"; ".join(hooks_sample)}
Create viral unseen proverb 3-4 words each for Reels 9 sec hook. Modern: archive, password, mute, favorite
Return JSON ONLY: {{"action_ar":"فعل 3-4 كلمات","reaction_ar":"رد فعل 3-4 كلمات","action_en":"cause 3-4 words","reaction_en":"effect 3-4 words","character":"same young man 28yo male white t-shirt short black hair light beard same face - LOCKED","location":"same small apartment evening warm light window left - LOCKED","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright","scene_reaction_en":"TOTALLY DIFFERENT lying on floor head in hands crying phone far away dark","mood":"sad_deep","title":"two words"}}"""
            content=""
            for model_try in ["llama-3.3-70b-versatile","llama3-70b-8192","mixtral-8x7b-32768"]:
                try:
                    res=groq.chat.completions.create(model=model_try, temperature=0.9, max_completion_tokens=800,
                        messages=[{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt}])
                    content=res.choices[0].message.content
                    if content and len(content.strip())>20: break
                except: continue
            if not content or len(content.strip())<10: time.sleep(1); continue
            content=content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p:
                        content=p.replace("json","").strip(); break
            s=content.find("{"); e=content.rfind("}")
            if s==-1 or e==-1: continue
            content=content[s:e+1]
            j=json.loads(content)
            ar1=j.get("action_ar","").strip(); ar2=j.get("reaction_ar","").strip()
            if not ar1 or not ar2: continue
            if len(ar1.split())>4 or len(ar1.split())<2: continue
            if len(ar2.split())>4 or len(ar2.split())<2: continue
            if "جبال" in ar1 or "يتشمعين" in ar1: continue
            if ar1==ar2: continue
            if is_dup(ar1[:12], mem): continue
            mem["ideas"].append(j.get("title","")); mem["scenes"].append(ar1[:12]); save_memory(mem)
            log(f"HOOK OK {ar1} -> {ar2}")
            return j
        except: time.sleep(0.6); continue
    return fallback

def generate_two_in_one(idea, cid=None):
    char=idea["character"]; loc=idea["location"]
    p_action=f"{char}, {loc}, {idea['scene_action_en']}, sitting upright on sofa holding phone close to chest hopeful bright, photorealistic 8k sharp"
    p_reaction=f"{char}, {loc}, {idea['scene_reaction_en']}, SAME young man SAME face SAME white t-shirt BUT completely different pose lying on floor or sitting on floor back against wall head in hands crying phone far away dark, photorealistic 8k sharp"
    img1=piapi_gen(p_action)
    ok,msg=check_quality(img1)
    if not ok: Path(img1).unlink(missing_ok=True); raise RuntimeError(f"فعل {msg}")
    img2=piapi_gen(p_reaction)
    ok2,msg2=check_quality(img2)
    if not ok2: Path(img1).unlink(missing_ok=True); Path(img2).unlink(missing_ok=True); raise RuntimeError(f"رد فعل {msg2}")
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

TREND_MUSIC_BY_MOOD={
    "sad_deep": [
        "https://cdn.pixabay.com/audio/2022/06/07/audio_b9bd4170e8.mp3",
        "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3",
        "https://cdn.pixabay.com/audio/2022/10/30/audio_fbd82ff3ef.mp3",
    ],
}
def get_music(mood="sad_deep"):
    mem=load_memory()
    pool=TREND_MUSIC_BY_MOOD.get(mood, TREND_MUSIC_BY_MOOD["sad_deep"])
    return random.choice(pool)

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
    base=ImageClip(str(image_path), duration=duration).resize(lambda t: 1.0 + 0.015*t/duration).set_position('center')
    def make_box(ar_text, en_text, is_top=True):
        h=270
        bg=(245,233,201,255) if is_top else (18,18,18,255)
        col_ar=(8,8,8) if is_top else (245,233,201)
        col_en=(25,25,25) if is_top else (215,215,215)
        img=Image.new('RGBA',(W,h),bg)
        d=ImageDraw.Draw(img)
        try:
            f_ar=ImageFont.truetype(str(amiri_path), 64) if has_ar else ImageFont.truetype(str(dejavu_path), 50) if dejavu_path.exists() else ImageFont.load_default()
            f_en_b=ImageFont.truetype(str(dejavu_path), 35) if dejavu_path.exists() else ImageFont.load_default()
        except: f_ar=f_en_b=ImageFont.load_default()
        ar_fixed=fix_ar(ar_text); en_clean=en_text.strip()
        try:
            bbox_ar=d.textbbox((0,0), ar_fixed, font=f_ar)
            d.text(((W-(bbox_ar[2]-bbox_ar[0]))//2, 55), ar_fixed, font=f_ar, fill=col_ar)
            bbox_en=d.textbbox((0,0), en_clean, font=f_en_b)
            d.text(((W-(bbox_en[2]-bbox_en[0]))//2, 160), en_clean, font=f_en_b, fill=col_en, stroke_width=2, stroke_fill=(0,0,0))
        except: d.text((50,50), ar_fixed, fill=col_ar)
        p=Path(tempfile.gettempdir())/f"box_{random.randint(1,9999999)}.png"
        img.save(p); return p
    top_p=make_box(idea["action_ar"], idea["action_en"], is_top=True)
    bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"], is_top=False)
    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-270))
    final=CompositeVideoClip([base, top_c, bottom_c], size=(W,H)).set_duration(duration)

    # === حل غلط الموسيقى 3 ثواني ===
    mp=Path(tempfile.gettempdir())/f"mus_{random.randint(1,99999)}.mp3"
    audio_added=False
    for _ in range(5):
        try:
            music_url=get_music(idea.get("mood","sad_deep"))
            download_file(music_url, mp, is_music=True)
            if not mp.exists() or mp.stat().st_size < 15000:
                if mp.exists(): mp.unlink(missing_ok=True)
                continue

            full_audio = AudioFileClip(str(mp))
            dur = full_audio.duration
            log(f"music file dur={dur}s size={mp.stat().st_size//1024}KB url={music_url[:40]}")

            # لو الملف تالف أو قصير جدا <0.8 ثانية تخطاه
            if dur < 0.8:
                full_audio.close()
                mp.unlink(missing_ok=True)
                continue

            # حل الغلط: لو أقصر من 9 ثواني اعمل loop
            if dur < duration:
                try:
                    from moviepy.audio.fx.all import audio_loop
                    audio = audio_loop(full_audio, duration=duration)
                except:
                    # fallback قديم: كرر يدوي
                    from moviepy.editor import concatenate_audioclips
                    loops = int(duration // dur) + 1
                    clips = [full_audio]*loops
                    audio = concatenate_audioclips(clips).subclip(0, duration)
            else:
                # طويلة - قص 9 ثواني فقط
                audio = full_audio.subclip(0, duration)

            audio = audio.volumex(0.88).audio_fadein(0.4).audio_fadeout(0.7)
            final = final.set_audio(audio)
            audio_added=True
            log(f"music OK dur {dur} -> {duration}")
            break

        except Exception as e:
            log(f"music attempt fail {e}")
            try:
                if 'full_audio' in locals(): full_audio.close()
            except: pass
            if mp.exists(): mp.unlink(missing_ok=True)
            time.sleep(0.3)
            continue

    out=Path(tempfile.gettempdir())/f"reel9s_{int(time.time())}.mp4"
    # لو ما في صوت استخدم None عشان ما يعلق
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac' if audio_added else None, preset='ultrafast', bitrate="9000k", logger=None)
    for p in [top_p,bottom_p,mp]:
        try: Path(p).unlink(missing_ok=True)
        except: pass
    return out, audio_added

def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":t}, timeout=30)
    except: pass
def send_photo(cid,p):
    with open(p,"rb") as f:
        r=requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files={"photo":("final.jpg",f,"image/jpeg")}, timeout=180)
        if r.status_code!=200: raise RuntimeError(r.text[:600])
def send_video(cid,p):
    with open(p,"rb") as f:
        r=requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files={"video":("reel.mp4",f,"video/mp4")}, timeout=300)
        if r.status_code!=200: raise RuntimeError(r.text[:600])

processing=set(); plock=threading.Lock()
def process(cid, cinematic=False):
    if AUTOMATIC_ENABLED is False and cid is None: return
    combined=None; vid=None
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        send_text(cid,"✨ V12 FIXED AUDIO 9ث Special\n🎯 فعل ورد فعل غريب غير منتشر\n🎨 صورتين مختلفتين جذريا\n🎵 بدون علامة بدون راوي\n📱 1080x1920 ريلز + شورت")

        send_text(cid,"⏳ 1/3 بحث ذكي عن مثل hook...")
        idea=brain_generate_smart(cid)
        send_text(cid,f"✅ HOOK:\n🔹 {idea['action_ar']}\n🔸 {idea['reaction_ar']}\nEN: {idea['action_en']} -> {idea['reaction_en']}")

        send_text(cid,"⏳ 2/3 صورتين مختلفتين بدون علامة...")
        combined, qmsg=generate_two_in_one(idea, cid)
        send_text(cid,f"✅ صورتين | {qmsg}")

        if cinematic:
            send_text(cid,"⏳ 3/3 مونتاج 9 ثواني زوم حلو (مصلح غلط 3 ثواني)...")
            vid, has_audio=create_reels_video(combined, idea, duration=9, cid=cid)
            send_text(cid,f"✅ رفع 9ث {'🎵 بموسيقى' if has_audio else '🔇 بدون موسيقى'}...")
            send_video(cid, vid)
        else:
            send_photo(cid, combined)

        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        cnt=len(list(ARCHIVE_DIR.glob("*.jpg")))
        send_text(cid,f"✅ جاهز 9ث يجذب مشاهدات\n🔹 {idea['action_ar']}\n🔸 {idea['reaction_ar']}\n💾 {cnt}\n/cinema التالي")

    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        send_error_report(cid, "process", e)
        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        send_text(cid,"❌ فشل - شوف الغلط فوق 👆")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"): send_text(chat,"V12 FIXED AUDIO 9ث 🔒\n✅ فعل ورد فعل غريب 3-4 كلمات\n✅ بحث ذكي hook يجذب مشاهدات\n✅ صورتين مختلفتين جذريا\n✅ بدون علامة بدون راوي\n✅ 9ث ريلز + شورت مصلح غلط الموسيقى 3ث\n/hikma صورة\n/cinema فيديو 9ث"); return
        if text.startswith("/ping"): send_text(chat,f"✅ V12 {len(list(ARCHIVE_DIR.glob('*.jpg')))}"); return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    except Exception as e:
        try:
            cid=(upd.get("message") or {}).get("chat",{}).get("id")
            if cid: send_error_report(cid, "handle_update", e)
        except: log(f"fatal {e}")

@app.get("/")
def home(): return f"V12 FIXED AUDIO 9SEC {len(list(ARCHIVE_DIR.glob('*.jpg')))} AUTO=OFF",200
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
