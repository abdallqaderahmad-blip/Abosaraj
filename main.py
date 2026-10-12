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

def log(m): print(f"[V9-9SEC-HOOK] {str(m)[:900]}", flush=True)

def send_error_report(cid, where, error, extra=""):
    import traceback
    tb=traceback.format_exc()[-1500:]
    err_text=f"🚨 غلط مباشر\n📍 {where}\n❌ {str(error)[:700]}\n📝 {extra[:500]}\n📄 {tb[:800]}\n⏰ {time.strftime('%H:%M:%S')}"
    if len(err_text)>4000: err_text=err_text[:4000]
    try:
        if cid: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":err_text}, timeout=30)
    except: pass
    log(f"ERROR {where}: {error} {extra}")

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
    headers={"User-Agent":"Mozilla/5.0","Accept":"*/*"} if is_music else {"User-Agent":"Mozilla/5.0"}
    with requests.get(url, timeout=120, stream=True, headers=headers, allow_redirects=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*256):
                if c: f.write(c)
    return path

def piapi_gen(prompt, ref_path=None, strength=0.60):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, writing, blurry, low quality, lowres, bad anatomy, bad hands, extra fingers, deformed, disfigured, ugly, artifacts, stripes, noise, duplicate, cartoon, anime, painting, female, woman"
    base_positive="same young man 28yo male arab short black hair light beard white t-shirt same face identical male"
    full=f"{base_positive}, {prompt}, ultra clean sharp 8k, no watermark no text no logo, photorealistic sharp focus male"
    payload={
        "model":"Qubico/flux1-dev","task_type":"txt2img",
        "input":{"prompt":full[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":6.5,"num_inference_steps":45,"negative_prompt":negative}
    }
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    tid=r.json().get("data",{}).get("task_id")
    url=piapi_wait(tid)
    tmp=Path(tempfile.gettempdir())/f"g_{hashlib.md5(str(time.time()+random.random()).encode()).hexdigest()[:8]}.jpg"
    download_file(url, tmp)
    return tmp

def check_quality(p, cid=None):
    from PIL import Image
    try:
        path=Path(p)
        if not path.exists():
            if cid: send_error_report(cid, "check_quality", "الملف مش موجود", str(p))
            return False, "no file"
        kb=path.stat().st_size//1024
        if path.stat().st_size<40000:
            if cid: send_error_report(cid, "حجم صغير", f"{kb}KB - الصورة تعبانة", str(p))
            return False, f"صغير {kb}KB"
        im=Image.open(path); w,h=im.size
        if w<900 or h<900:
            if cid: send_error_report(cid, "دقة ضعيفة", f"{w}x{h} - لازم 1024", str(p))
            return False, f"دقة ضعيفة {w}x{h}"
        return True, f"OK {w}x{h} {kb}KB"
    except Exception as e:
        if cid: send_error_report(cid, "check_quality error", e, str(p))
        return False, str(e)

# ========== عقل ذكي مدفوع يجيب مثل يجذب مشاهدات ==========
SPECIAL_VIRAL_HOOKS = [
    "تحفظهم ككلمة مرور -> فينسونك كإعلان",
    "تضع قلبك على العام -> فيضعونك على الصامت",
    "تترك مشاعرك متصلة -> فيستهلكون بطاريتك ويرحلون",
    "تجعلهم المفضلة -> فيجعلونك سجل المكالمات",
    "تكتبهم في ملاحظاتك -> فيكتبونك في المنسيات",
    "تفتح لهم نافذة قلبك -> فيفتحون نافذة أخرى بجانبك",
    "تضعهم في الواجهة -> فيضعونك في الخلفية",
    "تحفظهم في السحابة -> فيحذفونك من الذاكرة"
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
    if not groq:
        if cid: send_error_report(cid, "brain", "GROQ_API_KEY فاضي - العقل مش شغال")
        return fallback
    recent=", ".join(mem["ideas"][-70:]) or "none"
    hooks_sample = random.sample(SPECIAL_VIRAL_HOOKS, 5)
    BANNED=["تسامح كثيرا","تظل متاحا","تحضر جبال","يتشمعين","جبال داخل","تثق سريعا"]

    for attempt in range(35):
        try:
            # بحث ذكي مدفوع - يجيب مثل hook يجذب مشاهدات ريلز
            prompt=f"""You are viral hook creator for Reels & YouTube Shorts 9 sec.
Goal: make people stop scrolling and watch + share.

Avoid recent: {recent}
BANNED (never use): {", ".join(BANNED)}
Viral inspiration (create NEW like these but more hook): {"; ".join(hooks_sample)}

Create ULTRA VIRAL unseen proverb that grabs views in first 1 second:
- Must be 3-4 words ONLY each part - short readable in 9 sec
- First part = action people do (relatable)
- Second part = painful twist (makes them say هذا أنا)
- Modern objects: archive, password, wifi, battery, mute, favorite, airplane mode, cloud, notes, call log
- Must be clear, not nonsense - people must understand instantly while scrolling
- Hook: starts with verb يجذب: تحفظهم, تضع, تترك, تجعلهم, تكتبهم, تفتح
- Must be special - never seen before - exclusive page

BAD (never): تحضر جبال داخل البطارية (nonsense no one understands)
GOOD: تحفظهم ككلمة مرور -> فينسونك كإعلان (instant hook, modern, clear twist)

Return JSON ONLY:
{{
"action_ar":"فعل غريب يجذب 3-4 كلمات واضح",
"reaction_ar":"رد فعل twist موجع 3-4 كلمات نفس الموضوع",
"action_en":"viral hook cause 3-4 words",
"reaction_en":"same topic painful twist 3-4 words",
"character":"same young man 28yo male white t-shirt short black hair light beard same face - LOCKED",
"location":"same small apartment evening warm light window left - LOCKED",
"scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright smile",
"scene_reaction_en":"TOTALLY DIFFERENT - lying on floor on side or sitting on floor back against wall head in hands crying phone far away dark lonely",
"mood":"sad_deep",
"title":"two words hook"
}}
Must be hook for 9 sec Reels.
"""
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=1.05, max_completion_tokens=1100,
                messages=[
                    {"role":"system","content":"You are viral Reels Shorts hook creator. 9 seconds. Create unseen Arabic proverbs 3-4 words that stop scrolling. Modern: archive, password, mute, favorite. Clear, relatable, painful twist. Never nonsense. Must be exclusive special page."},
                    {"role":"user","content":prompt}
                ])
            content=res.choices[0].message.content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p: content=p.replace("json","").strip(); break
            j=json.loads(content)
            ar1=j.get("action_ar","").strip()
            ar2=j.get("reaction_ar","").strip()
            # فحص مباشر يبين بالبوت لو في غلط
            if len(ar1.split())>4 or len(ar1.split())<2:
                if cid: send_error_report(cid, "brain حجم كلام", f"action_ar طويل {len(ar1.split())} كلمات: {ar1}", "")
                continue
            if len(ar2.split())>4 or len(ar2.split())<2:
                if cid: send_error_report(cid, "brain حجم كلام", f"reaction_ar طويل {len(ar2.split())} كلمات: {ar2}", "")
                continue
            if any(b in ar1 or b in ar2 for b in BANNED):
                if cid: send_error_report(cid, "brain كلام ممنوع", f"فيه كلمة ممنوعة: {ar1} -> {ar2}")
                continue
            if "جبال" in ar1 or "جبال" in ar2 or "يتشمعين" in ar1:
                if cid: send_error_report(cid, "brain خربطة", f"كلام خربان: {ar1} -> {ar2}")
                continue
            if ar1==ar2:
                if cid: send_error_report(cid, "brain تكرار", f"نفس الكلام: {ar1}")
                continue
            if is_dup(ar1[:12], mem): continue
            mem["ideas"].append(j.get("title","")); mem["scenes"].append(ar1[:12]); save_memory(mem)
            log(f"V9 HOOK {ar1} -> {ar2}")
            return j
        except Exception as e:
            if cid: send_error_report(cid, "brain_generate_smart", e, f"محاولة {attempt}")
            time.sleep(0.5)
    if cid: send_error_report(cid, "brain fallback", "استخدم fallback بعد 35 محاولة")
    return fallback

def generate_two_in_one(idea, cid=None):
    char=idea["character"]; loc=idea["location"]
    p_action=f"{char}, {loc}, {idea['scene_action_en']}, sitting upright on sofa holding phone close to chest hopeful bright, photorealistic 8k sharp"
    p_reaction=f"{char}, {loc}, {idea['scene_reaction_en']}, SAME young man SAME face SAME white t-shirt BUT completely different pose lying on floor or sitting on floor back against wall head in hands crying phone far away dark, photorealistic 8k sharp"

    try:
        img1=piapi_gen(p_action)
        ok,msg=check_quality(img1, cid)
        if not ok:
            Path(img1).unlink(missing_ok=True)
            raise RuntimeError(f"فعل تعبان: {msg}")
    except Exception as e:
        if cid: send_error_report(cid, "generate فعل", e)
        raise

    try:
        img2=piapi_gen(p_reaction)
        ok2,msg2=check_quality(img2, cid)
        if not ok2:
            Path(img1).unlink(missing_ok=True)
            Path(img2).unlink(missing_ok=True)
            raise RuntimeError(f"رد فعل تعبان: {msg2}")
    except Exception as e:
        if cid: send_error_report(cid, "generate رد فعل", e)
        Path(img1).unlink(missing_ok=True)
        raise

    try:
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
    except Exception as e:
        if cid: send_error_report(cid, "دمج الصورتين", e)
        raise

def ensure_fonts(cid=None):
    base=Path("/tmp/fonts"); base.mkdir(exist_ok=True)
    amiri=base/"Amiri-Bold.ttf"
    dejavu=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not amiri.exists() or amiri.stat().st_size<5000:
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf", timeout=40)
            if r.status_code==200 and len(r.content)>5000: amiri.write_bytes(r.content)
            else:
                if cid: send_error_report(cid, "تحميل خط عربي", f"فشل status {r.status_code}")
        except Exception as e:
            if cid: send_error_report(cid, "تحميل خط", e)
    return amiri if amiri.exists() and amiri.stat().st_size>5000 else None, dejavu

TREND_MUSIC_BY_MOOD={
    "sad_deep": [
        "https://cdn.pixabay.com/download/audio/2022/10/30/audio_fbd82ff3ef.mp3?filename=sad-piano-112199.mp3",
        "https://cdn.pixabay.com/download/audio/2021/11/25/audio_39628d1d4f.mp3?filename=sad-background-113831.mp3",
        "https://cdn.pixabay.com/download/audio/2022/03/24/audio_2a9aafb6cb.mp3?filename=soft-piano-118571.mp3"
    ],
}
def get_music(mood="sad_deep"):
    mem=load_memory(); last=mem.get("last_music","")
    pool=TREND_MUSIC_BY_MOOD.get(mood, TREND_MUSIC_BY_MOOD["sad_deep"])
    choices=[m for m in pool if m!=last] or pool
    pick=random.choice(choices)
    mem["last_music"]=pick; save_memory(mem); return pick

def create_reels_video(image_path, idea, duration=9, cid=None):
    from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
    from PIL import Image, ImageDraw, ImageFont
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        HAS_AR=True
    except Exception as e:
        HAS_AR=False
        if cid: send_error_report(cid, "مكتبة عربي", e, "arabic_reshaper مش موجود")

    amiri_path, dejavu_path = ensure_fonts(cid)
    has_ar = amiri_path and amiri_path.exists() and HAS_AR
    if not has_ar and cid:
        send_error_report(cid, "خط عربي", "Amiri-Bold مش موجود - الكتابة رح تنقلب", f"has_ar={HAS_AR} path={amiri_path}")

    def fix_ar(t):
        if not t: return ""
        t=t.strip()
        if has_ar:
            try: return get_display(arabic_reshaper.reshape(t))
            except Exception as e:
                if cid: send_error_report(cid, "تشكيل عربي", e, t)
                return t
        return t

    W,H=1080,1920
    base=ImageClip(str(image_path), duration=duration)
    base=base.resize(lambda t: 1.0 + 0.012*t/duration).set_position('center')

    def make_box(ar_text, en_text, is_top=True):
        h=270
        bg=(245,233,201,255) if is_top else (18,18,18,255)
        col_ar=(8,8,8) if is_top else (245,233,201)
        col_en=(25,25,25) if is_top else (215,215,215)
        img=Image.new('RGBA',(W,h),bg)
        d=ImageDraw.Draw(img)
        try:
            f_ar=ImageFont.truetype(str(amiri_path), 64) if has_ar else ImageFont.truetype(str(dejavu_path), 50) if dejavu_path.exists() else ImageFont.load_default()
            f_en=ImageFont.truetype(str(dejavu_path), 33) if dejavu_path.exists() else ImageFont.load_default()
            f_en_b=ImageFont.truetype(str(dejavu_path), 35) if dejavu_path.exists() else f_en
        except Exception as e:
            if cid: send_error_report(cid, "تحميل خطوط المونتاج", e)
            f_ar=f_en=f_en_b=ImageFont.load_default()

        ar_fixed=fix_ar(ar_text)
        en_clean=en_text.strip()

        # فحص حجم النص للمشاهد
        if len(ar_text)>25 and cid:
            send_error_report(cid, "حجم نص كبير", f"النص العربي طويل {len(ar_text)} حرف ما رح يبين: {ar_text}")

        try:
            bbox_ar=d.textbbox((0,0), ar_fixed, font=f_ar)
            w_ar=bbox_ar[2]-bbox_ar[0]
            if w_ar > W-40 and cid:
                send_error_report(cid, "نص عريض", f"العربي عريض {w_ar}px أكبر من الشاشة {W}px: {ar_text}")
            d.text(((W-w_ar)//2, 55), ar_fixed, font=f_ar, fill=col_ar)
            bbox_en=d.textbbox((0,0), en_clean, font=f_en_b)
            x_en=(W-(bbox_en[2]-bbox_en[0]))//2
            d.text((x_en, 160), en_clean, font=f_en_b, fill=col_en, stroke_width=2, stroke_fill=(0,0,0) if is_top else (0,0,0))
        except Exception as e:
            if cid: send_error_report(cid, "كتابة النص", e, f"{ar_text} / {en_text}")
            d.text((50,50), ar_fixed, fill=col_ar)
        p=Path(tempfile.gettempdir())/f"box_{random.randint(1,9999999)}.png"
        img.save(p); return p

    try:
        top_p=make_box(idea["action_ar"], idea["action_en"], is_top=True)
        bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"], is_top=False)
    except Exception as e:
        if cid: send_error_report(cid, "make_box", e)
        raise

    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-270))
    final=CompositeVideoClip([base, top_c, bottom_c], size=(W,H)).set_duration(duration)

    mp=Path(tempfile.gettempdir())/f"mus_{random.randint(1,99999)}.mp3"
    audio_added=False
    for _ in range(3):
        music_url=get_music(idea.get("mood","sad_deep"))
        try:
            download_file(music_url, mp, is_music=True)
            if mp.exists() and mp.stat().st_size>20000:
                audio=AudioFileClip(str(mp)).subclip(0,duration).volumex(0.92).audio_fadein(0.5).audio_fadeout(0.8)
                final=final.set_audio(audio)
                audio_added=True
                break
        except Exception as e:
            if cid: send_error_report(cid, "تحميل موسيقى", e, music_url)
            mp.unlink(missing_ok=True)
            time.sleep(0.3)
    if not audio_added and cid:
        send_error_report(cid, "موسيقى", "ما قدر يحمل موسيقى - الفيديو بدون صوت")

    out=Path(tempfile.gettempdir())/f"reel9s_{int(time.time())}.mp4"
    try:
        final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac', preset='ultrafast', bitrate="9000k", logger=None)
    except Exception as e:
        if cid: send_error_report(cid, "تصدير فيديو 9ث", e)
        raise
    for p in [top_p,bottom_p,mp]: Path(p).unlink(missing_ok=True)
    return out

def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":t}, timeout=30)
    except: pass
def send_photo(cid,p):
    try:
        with open(p,"rb") as f:
            r=requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files={"photo":("final.jpg",f,"image/jpeg")}, timeout=180)
            if r.status_code!=200: raise RuntimeError(r.text[:600])
    except Exception as e: send_error_report(cid, "send_photo", e); raise
def send_video(cid,p):
    try:
        with open(p,"rb") as f:
            r=requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files={"video":("reel.mp4",f,"video/mp4")}, timeout=300)
            if r.status_code!=200: raise RuntimeError(r.text[:600])
    except Exception as e: send_error_report(cid, "send_video", e); raise

processing=set(); plock=threading.Lock()
def process(cid, cinematic=False):
    if AUTOMATIC_ENABLED is False and cid is None:
        log("Blocked automatic"); return
    combined=None; vid=None
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        send_text(cid,"✨ V9 9ثواني HOOK Special\n🎯 مثل غريب يجذب مشاهدات\n🎨 صورتين مختلفتين جذريا\n🎵 بدون علامة مائية بدون راوي\n📱 ريلز + شورت يوتيوب 1080x1920")

        send_text(cid,"⏳ 1/3 بحث ذكي مدفوع عن مثل hook...")
        idea=brain_generate_smart(cid)
        send_text(cid,f"✅ HOOK:\n🔹 {idea['action_ar']}\n🔸 {idea['reaction_ar']}\nEN: {idea['action_en']} -> {idea['reaction_en']}")

        send_text(cid,"⏳ 2/3 صورتين مختلفتين بدون علامة...")
        combined, qmsg=generate_two_in_one(idea, cid)
        send_text(cid,f"✅ صورتين | {qmsg}")

        if cinematic:
            send_text(cid,"⏳ 3/3 مونتاج 9 ثواني ريلز...")
            vid=create_reels_video(combined, idea, duration=9, cid=cid)
            send_text(cid,"✅ رفع 9ث...")
            send_video(cid, vid)
        else:
            send_photo(cid, combined)

        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        cnt=len(list(ARCHIVE_DIR.glob("*.jpg")))
        send_text(cid,f"✅ 9ث جاهز - يجذب مشاهدات\n🔹 {idea['action_ar']}\n🔸 {idea['reaction_ar']}\n💾 {cnt}\n/cinema التالي")

    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        send_error_report(cid, "process عام", e, "فشل عام")
        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        send_text(cid,"❌ فشل - شوف تقرير الغلط فوق 👆")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"): send_text(chat,"V9 9ث HOOK 🔒\n✅ فعل ورد فعل غريب غير منتشر\n✅ 3-4 كلمات فقط - يجذب مشاهدات\n✅ بحث ذكي مدفوع عن hook\n✅ صورتين مختلفتين جذريا\n✅ بدون علامة مائية بدون راوي\n✅ 9 ثواني ريلز + شورت يوتيوب\n✅ أي غلط ببين بالبوت مباشرة\n/hikma صورة\n/cinema فيديو 9ث"); return
        if text.startswith("/ping"): send_text(chat,f"✅ V9 9ث {len(list(ARCHIVE_DIR.glob('*.jpg')))}"); return
        if text.startswith("/archive"): send_text(chat,f"💾 أرشيف {len(list(ARCHIVE_DIR.glob('*.jpg')))}"); return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    except Exception as e:
        try:
            cid=(upd.get("message") or {}).get("chat",{}).get("id")
            if cid: send_error_report(cid, "handle_update", e)
        except: log(f"fatal {e}")

@app.get("/")
def home(): return f"V9 9SEC HOOK {len(list(ARCHIVE_DIR.glob('*.jpg')))} AUTO=OFF",200
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
