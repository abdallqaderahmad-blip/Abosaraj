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

def log(m): print(f"[FINAL] {str(m)[:800]}", flush=True)

# ========== أداة كشف أي خطأ بالسيستم يظهر بالبوت مباشرة ==========
def send_error_report(cid, where, error):
    import traceback
    tb=traceback.format_exc()[-1800:]
    err_text=f"🚨 خطأ\n📍 {where}\n❌ {str(error)[:600]}\n📄 {tb}\n🕒 {time.strftime('%Y-%m-%d %H:%M:%S')}"
    if len(err_text)>4000: err_text=err_text[:4000]
    try:
        if cid: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":err_text}, timeout=30)
    except: pass
    log(f"ERROR {where}: {error}")

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

# ========== 1. عقل سلسلة فعل ورد فعل - يدوي ==========
def brain_generate():
    mem=load_memory()
    fallback={"action_ar":"تترك يدك ترتجف وحيدة","reaction_ar":"فيسقط قلبك بصمت ثقيل","action_en":"You leave hand shaking alone","reaction_en":"Your heart falls silently heavy","character":"same young man 25yo black hoodie short beard dark hair same face","location":"same small dark room window light from left","scene_action_en":"sitting alone hand shaking head down sad cinematic","scene_reaction_en":"same sitting heart glowing broken tears hopeful cinematic","mood":"sad_deep","title":"Shaking Hand"}
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-15:]) or "none"
    for _ in range(10):
        try:
            prompt=f"""Avoid titles: {recent}
Return JSON ONLY:
{{
"action_ar":"فعل حقيقي 5-6 كلمات شاعرية مثل تترك يدك ترتجف وحيدة",
"reaction_ar":"رد فعل حقيقي منطقي 5-6 كلمات نتيجة الفعل مثل فيسقط قلبك بصمت ثقيل",
"action_en":"real action 5-6 words like You leave hand shaking alone",
"reaction_en":"real reaction logical result 5-6 words like Your heart falls silently",
"character":"LOCKED same person detailed e.g. same young woman 23yo white shirt long hair same face",
"location":"LOCKED same place e.g. same bedroom soft light left",
"scene_action_en":"action visual: sitting hand shaking sad cinematic portrait",
"scene_reaction_en":"reaction visual: same sitting heart glowing hopeful cinematic portrait",
"mood":"sad_deep",
"title":"unique 2 words"
}} Action must cause reaction. Lock character/location.
"""
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=1.4, max_completion_tokens=900, messages=[{"role":"system","content":"You are viral action-reaction director. Real Arabic. Lock small details. Series."},{"role":"user","content":prompt}])
            content=res.choices[0].message.content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p: content=p.replace("json","").strip(); break
            j=json.loads(content)
            if "5 كلمات" in j.get("action_ar",""): continue
            if "5 words" in j.get("action_en","").lower(): continue
            if j.get("action_ar")==j.get("reaction_ar"): continue
            if len(j.get("action_ar","").split())<4: continue
            combined=j["character"]+j["location"]+j["scene_action_en"]
            if not is_dup(combined, mem):
                mem["ideas"].append(j.get("title","")); mem["scenes"].append(combined); save_memory(mem)
                return j
        except Exception as e: log(f"brain {e}"); time.sleep(0.5)
    return fallback

# ========== 2. PIAPI صورتين بصورة - وصف الجملتين فعل ورد فعل ==========
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

def piapi_gen(prompt, ref_path=None, strength=0.38):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, blurry, low quality, artifacts, stripes, noise, deformed, extra fingers, glitch, dirty, lowres"
    full=f"{prompt}, ultra clean sharp 8k, no watermark, no text, no logo, no artifacts, masterpiece photorealistic"
    if ref_path and Path(ref_path).exists():
        with open(ref_path,"rb") as f: b64=base64.b64encode(f.read()).decode()
        payload={"model":"Qubico/flux1-dev","task_type":"img2img","input":{"prompt":full[:1500],"image":b64,"strength":strength,"guidance_scale":4.5,"num_inference_steps":36,"width":1024,"height":1024}}
    else:
        payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":full[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":4.5,"num_inference_steps":38,"negative_prompt":negative}}
    r=requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=70)
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
        if path.stat().st_size<45000: return False, f"صغير {kb}KB"
        im=Image.open(path); w,h=im.size
        if w<900 or h<900: return False, f"دقة ضعيفة {w}x{h}"
        if im.convert("L").getextrema()[1]-im.convert("L").getextrema()[0]<25: return False, "باهتة"
        return True, f"OK {w}x{h} {kb}KB"
    except Exception as e: return False, str(e)

def generate_two_in_one(idea):
    char=idea["character"]; loc=idea["location"]
    # وصف كل جملة يبحث وينتج صورتها
    p_action=f"{char}, {loc}, {idea['scene_action_en']}, {idea['action_en']}, {idea['mood']} mood, action portrait"
    p_reaction=f"{char}, {loc}, {idea['scene_reaction_en']}, {idea['reaction_en']}, SAME face SAME clothes SAME background SAME lighting, reaction portrait"

    # صورة فعل
    for _ in range(2):
        img1=piapi_gen(p_action, None)
        ok,msg=check_quality(img1)
        if ok: break
        Path(img1).unlink(missing_ok=True)
    else:
        raise RuntimeError(f"جودة فعل فشلت {msg}")

    # صورة رد فعل مبنية على الأولى لتطابق تفاصيل صغيرة
    for _ in range(2):
        img2=piapi_gen(p_reaction, img1, strength=0.38)
        ok2,msg2=check_quality(img2)
        if ok2: break
        Path(img2).unlink(missing_ok=True)
    else:
        Path(img1).unlink(missing_ok=True)
        raise RuntimeError(f"جودة رد فعل فشلت {msg2}")

    # حفظ نسخة من كل صورة بكل فيديو خالص
    try:
        from PIL import Image
        Image.open(img1).save(ARCHIVE_DIR/f"{int(time.time())}_action_{idea['title'][:10]}.jpg", quality=95)
        Image.open(img2).save(ARCHIVE_DIR/f"{int(time.time())}_reaction_{idea['title'][:10]}.jpg", quality=95)
    except: pass

    from PIL import Image, ImageFilter, ImageDraw
    top=Image.open(img1).convert("RGB").resize((1080,960), Image.LANCZOS).filter(ImageFilter.MedianFilter(size=3))
    bottom=Image.open(img2).convert("RGB").resize((1080,960), Image.LANCZOS).filter(ImageFilter.MedianFilter(size=3))
    combined=Image.new("RGB",(1080,1920),(10,10,10))
    combined.paste(top,(0,0)); combined.paste(bottom,(0,960))
    ImageDraw.Draw(combined).rectangle([0,956,1080,964], fill=(20,20,20))
    out=Path(tempfile.gettempdir())/f"final_{int(time.time())}.jpg"
    combined.save(out, quality=98, subsampling=0)
    Path(img1).unlink(missing_ok=True); Path(img2).unlink(missing_ok=True)
    return out, f"{msg} | {msg2}"

# ========== 3. خط عربي يصلح latin-1 + ترجمة صحيحة ==========
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

# ========== 4. فيديو ريلز - زوم للصورة مش للكلام - بدون راوي - إصلاح latin-1 ==========
def create_reels_video(image_path, idea, duration=9):
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
        if not t: return ""
        try:
            if has_ar:
                reshaped=arabic_reshaper.reshape(t)
                return get_display(reshaped)
            return t
        except: return t

    # زوم بطيء للصورة فقط - النص ثابت
    base=ImageClip(str(image_path), duration=duration)
    base=base.resize(lambda t: 1.0 + 0.12*t/duration)
    base=base.set_position(lambda t: ('center', f"{- (t/duration)*120}"))

    W,H=1080,1920

    def make_box(ar_text, en_text, h=190):
        img=Image.new('RGBA',(W,h),(245,233,201,250))
        d=ImageDraw.Draw(img)
        # خط عربي صحيح - يصلح latin-1
        try:
            if has_ar:
                f_ar=ImageFont.truetype(str(amiri_path), 44)
            else:
                f_ar=ImageFont.truetype(str(dejavu_path), 34) if dejavu_path.exists() else ImageFont.load_default()
            f_en=ImageFont.truetype(str(dejavu_path), 26) if dejavu_path.exists() else ImageFont.load_default()
        except Exception as e:
            log(f"font load fail {e}")
            f_ar=f_en=ImageFont.load_default()

        ar_fixed=fix_ar(ar_text)
        en_fixed=en_text

        # رسم بدون anchor لتجنب getmask2 error - استخدم textbbox
        try:
            # عربي
            bbox_ar=d.textbbox((0,0), ar_fixed, font=f_ar)
            w_ar=bbox_ar[2]-bbox_ar[0]; h_ar=bbox_ar[3]-bbox_ar[1]
            d.text(((W-w_ar)//2, (h//2-28)-h_ar//2), ar_fixed, font=f_ar, fill=(10,10,10))
            # انجليزي
            bbox_en=d.textbbox((0,0), en_fixed, font=f_en)
            w_en=bbox_en[2]-bbox_en[0]
            d.text(((W-w_en)//2, (h//2+22)), en_fixed, font=f_en, fill=(40,40,40))
        except Exception as e:
            log(f"text draw fallback {e}")
            d.text((20,20), ar_fixed[:30], font=f_ar, fill=(0,0,0))
            d.text((20,80), en_fixed[:40], font=f_en, fill=(0,0,0))

        p=Path(tempfile.gettempdir())/f"box_{random.randint(1,9999999)}.png"
        img.save(p)
        return p

    def make_mid():
        img=Image.new('RGBA',(W,68),(0,0,0,210))
        d=ImageDraw.Draw(img)
        try:
            f=ImageFont.truetype(str(dejavu_path), 20) if dejavu_path.exists() else ImageFont.load_default()
            txt="فعل → رد فعل | ACTION → REACTION"
            bbox=d.textbbox((0,0), txt, font=f)
            d.text(((W-(bbox[2]-bbox[0]))//2, (68-(bbox[3]-bbox[1]))//2), txt, font=f, fill=(255,255,255))
        except: pass
        p=Path(tempfile.gettempdir())/f"mid_{random.randint(1,9999)}.png"; img.save(p); return p

    top_p=make_box(idea["action_ar"], idea["action_en"], h=190)
    bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"], h=190)
    mid_p=make_mid()

    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    mid_c=ImageClip(str(mid_p), duration=duration).set_position((0, H//2-34))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-190))

    bg=Image.new('RGB',(W,H),(12,12,12))
    bg_p=Path(tempfile.gettempdir())/f"bg_{random.randint(1,9999)}.jpg"; bg.save(bg_p)
    bg_c=ImageClip(str(bg_p), duration=duration)

    final=CompositeVideoClip([bg_c, base, top_c, mid_c, bottom_c], size=(W,H)).set_duration(duration)

    # موسيقى ترند عالية مختلفة كل فيديو - بدون راوي
    music_url=get_music()
    mp=Path(tempfile.gettempdir())/f"mus_{random.randint(1,99999)}.mp3"
    try:
        download_file(music_url, mp)
        audio=AudioFileClip(str(mp)).subclip(0,duration).volumex(0.92).audio_fadein(0.3).audio_fadeout(0.8)
        final=final.set_audio(audio)
    except Exception as e: log(f"music {e}")

    out=Path(tempfile.gettempdir())/f"reel_{int(time.time())}.mp4"
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac', preset='ultrafast', bitrate="6000k", logger=None)
    for p in [top_p,bottom_p,mid_p,bg_p,mp]: Path(p).unlink(missing_ok=True)
    return out

# ========== Telegram ==========
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
    combined=None; vid=None
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        send_text(cid,"🧠 فعل ورد فعل متكامل...\n🎨 صورتين بصورة - كل جملة صورتها - بحث متطابق...\n🎵 موسيقى ترند عالية مختلفة...\n🔍 فحص جودة + حفظ نسخة...")

        send_text(cid,"⏳ 1/3 عقل سلسلة...")
        try: idea=brain_generate()
        except Exception as e: send_error_report(cid, "brain_generate", e); raise
        send_text(cid,f"✅ فعل: {idea['action_ar']}\nرد فعل: {idea['reaction_ar']}")

        send_text(cid,"⏳ 2/3 توليد صورتين بصورة من المدفوع - كل جملة صورتها...")
        try: combined, qmsg=generate_two_in_one(idea)
        except Exception as e: send_error_report(cid, "generate_two_in_one PIAPI", e); raise
        send_text(cid,f"✅ صورتين بصورة جاهزة | {qmsg}\n💾 حفظ نسخة أرشيف")

        if cinematic:
            send_text(cid,"⏳ 3/3 مونتاج ريلز 9ث - زوم للصورة فقط...")
            try: vid=create_reels_video(combined, idea, duration=9)
            except Exception as e: send_error_report(cid, "create_reels_video مونتاج", e); raise
            send_text(cid,"✅ مونتاج جاهز - رفع...")
            send_video(cid, vid)
        else:
            send_photo(cid, combined)

        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        cnt=len(list(ARCHIVE_DIR.glob("*.jpg")))
        send_text(cid,f"✅ تم\n🔹 {idea['action_ar']} | {idea['action_en']}\n🔸 {idea['reaction_ar']} | {idea['reaction_en']}\n💾 أرشيف {cnt}\n/cinema التالي - يدوي")

    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        if combined and Path(combined).exists(): Path(combined).unlink(missing_ok=True)
        if vid and Path(vid).exists(): Path(vid).unlink(missing_ok=True)
        send_text(cid,"❌ فشل - التفاصيل فوق 👆 جرب /cinema")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"): send_text(chat,"نموذج فعل ورد فعل متكامل\n✅ صورتين بصورة - فعل فوق رد فعل تحت\n✅ وصف الجملتين يبحث وينتج صور متطابقة\n✅ تطابق تفاصيل صغيرة نفس الوجه لبس مكان\n✅ زوم للصورة فقط - نص ثابت بلغتين\n✅ موسيقى ترند عالية مختلفة كل فيديو\n✅ بدون راوي - 9ث ريلز شورت\n✅ فحص جودة + حفظ نسخة + كشف أخطاء\n✅ عقل سلسلة يدوي\n/hikma صورة\n/cinema فيديو\n/archive الأرشيف"); return
        if text.startswith("/ping"): send_text(chat,f"✅ FINAL {len(list(ARCHIVE_DIR.glob('*.jpg')))} archived"); return
        if text.startswith("/archive"): send_text(chat,f"💾 أرشيف {len(list(ARCHIVE_DIR.glob('*.jpg')))} صورة"); return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False), daemon=True).start(); return
    except Exception as e:
        try:
            cid=(upd.get("message") or {}).get("chat",{}).get("id")
            if cid: send_error_report(cid, "handle_update", e)
        except: log(f"fatal {e}")

@app.get("/")
def home(): return f"FINAL 2in1 FIXED {len(list(ARCHIVE_DIR.glob('*.jpg')))}",200
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
