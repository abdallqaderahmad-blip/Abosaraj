import os, sys, json, time, threading, re, tempfile, random, hashlib, shutil
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

groq=None
try:
    from groq import Groq
    if GROQ_API_KEY: groq=Groq(api_key=GROQ_API_KEY)
except: groq=None

app=Flask(__name__)
PIAPI_BASE="https://api.piapi.ai/api/v1/task"
MEMORY_FILE=Path("/tmp/hmisc_brain_memory.json")
LAST_IMAGE_PATH=Path("/tmp/last_final.jpg")
TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

def log(m): print(f"[V22] {str(m)[:900]}", flush=True)

def load_memory():
    if MEMORY_FILE.exists():
        try:
            mem=json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
            for k in ["ideas","scenes"]:
                if k not in mem: mem[k]=[]
            return mem
        except: pass
    return {"ideas":[],"scenes":[]}

def save_memory(mem):
    mem["ideas"]=mem["ideas"][-300:]; mem["scenes"]=mem["scenes"][-300:]
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False), encoding='utf-8')

def is_dup(s, mem): return any(old[:12] in s[:12] for old in mem["scenes"][-150:])

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

def download_file(url, path):
    path=Path(path)
    with requests.get(url, timeout=120, stream=True, headers={"User-Agent":"Mozilla/5.0"}, allow_redirects=True) as rr:
        rr.raise_for_status()
        with path.open("wb") as f:
            for c in rr.iter_content(1024*256):
                if c: f.write(c)
    return path

def piapi_gen(prompt):
    headers={"X-API-KEY": PIAPI_API_KEY, "Content-Type":"application/json"}
    negative="watermark, logo, text, letters, words, blurry, low quality, cartoon, anime, female, woman, girl, deformed hands, bad fingers, extra fingers, ugly, distorted"
    base="same young man 28yo male arab short black hair light beard white t-shirt same face identical male, perfect hands 5 fingers clear watch sharp focus"
    full=f"{base}, {prompt}, ultra clean sharp 8k photorealistic no watermark no text"
    payload={"model":"Qubico/flux1-dev","task_type":"txt2img","input":{"prompt":full[:1500],"width":1024,"height":1024,"num_images":1,"guidance_scale":7.5,"num_inference_steps":50,"negative_prompt":negative}}
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
        if path.stat().st_size<45000: return False, f"صغير {kb}KB"
        im=Image.open(path); w,h=im.size
        if w<900 or h<900: return False, f"دقة {w}x{h}"
        return True, f"OK {w}x{h} {kb}KB"
    except Exception as e: return False, str(e)

def brain_generate_smart():
    mem=load_memory()
    fallback={"action_ar":"تحفظهم ككلمة مرور","reaction_ar":"فينسونك كإعلان","action_en":"SAVE AS PASSWORD","reaction_en":"FORGOTTEN LIKE ADS","character":"same young man 28yo male white t-shirt short black hair light beard same face","location":"same small apartment evening warm light window left","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright face visible","scene_reaction_en":"lying on floor head in hands crying phone far away dark lonely side view","title":"Password Ad"}
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-80:]) or "none"
    for _ in range(10):
        try:
            prompt=f"""Avoid: {recent}
Return JSON ONLY: {{"action_ar":"فعل 3-4 كلمات","reaction_ar":"رد 3-4 كلمات","action_en":"3 WORDS EN CAPS","reaction_en":"3 WORDS EN CAPS","character":"same young man 28yo male white t-shirt short black hair light beard same face - LOCKED","location":"same small apartment evening warm light window left - LOCKED","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright face visible","scene_reaction_en":"lying on floor head in hands crying phone far away dark","mood":"sad_deep","title":"two words"}}"""
            res=groq.chat.completions.create(model="llama-3.3-70b-versatile", temperature=0.9, max_completion_tokens=600, messages=[{"role":"system","content":"Return JSON only."},{"role":"user","content":prompt}])
            content=res.choices[0].message.content.strip()
            if "```" in content:
                for p in content.split("```"):
                    if "{" in p and "}" in p: content=p.replace("json","").strip(); break
            s=content.find("{"); e=content.rfind("}")
            if s==-1: continue
            j=json.loads(content[s:e+1])
            ar1=j.get("action_ar","").strip()
            if not ar1 or len(ar1.split())>4: continue
            if is_dup(ar1[:12], mem): continue
            mem["ideas"].append(j.get("title","")); mem["scenes"].append(ar1[:12]); save_memory(mem)
            return j
        except: time.sleep(0.5); continue
    return fallback

def generate_two_in_one_original(idea):
    char=idea["character"]; loc=idea["location"]
    p1=f"{char}, {loc}, {idea['scene_action_en']}, photorealistic 8k perfect hands"
    p2=f"{char}, {loc}, {idea['scene_reaction_en']}, SAME face SAME shirt BUT different pose photorealistic 8k"
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
    try: shutil.copy(out, LAST_IMAGE_PATH)
    except: pass
    return out, f"{msg} | {msg2}"

def generate_two_in_one(idea, force_new=False):
    if not force_new and LAST_IMAGE_PATH.exists() and LAST_IMAGE_PATH.stat().st_size>50000:
        log("CACHED 0$")
        return LAST_IMAGE_PATH, "CACHED 0$ - مجاني"
    return generate_two_in_one_original(idea)

def get_font(size):
    # يبحث عن اي خط شغال - مستحيل يفشل
    from PIL import ImageFont
    candidates=[
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except: continue
    # اخر حل
    return ImageFont.load_default()

def create_reels_video(image_path, idea, duration=9):
    from moviepy.editor import ImageClip, CompositeVideoClip, AudioClip
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        HAS_AR=True
    except: HAS_AR=False

    def fix_ar(t):
        t=str(t).strip()
        if HAS_AR:
            try: return get_display(arabic_reshaper.reshape(t))
            except: return t
        return t

    W,H=1080,1920
    BOX_H=380
    IMG_H=600

    from PIL import Image as PILImage
    full=PILImage.open(image_path).convert("RGB")
    top_img = full.crop((0,0,1080,960)).resize((W,IMG_H), PILImage.LANCZOS)
    bottom_img = full.crop((0,960,1080,1920)).resize((W,IMG_H), PILImage.LANCZOS)
    top_tmp=Path(tempfile.gettempdir())/f"top_{random.randint(1,999999)}.jpg"
    bottom_tmp=Path(tempfile.gettempdir())/f"bot_{random.randint(1,999999)}.jpg"
    top_img.save(top_tmp, quality=98)
    bottom_img.save(bottom_tmp, quality=98)

    top_clip=ImageClip(str(top_tmp), duration=duration).resize(lambda t: 1.0 + 0.06*t/duration).set_position(('center', BOX_H))
    bottom_clip=ImageClip(str(bottom_tmp), duration=duration).resize(lambda t: 1.0 + 0.06*t/duration).set_position(('center', BOX_H+IMG_H+6))

    def make_box(ar_text, en_text, is_top=True):
        bg=(242,230,195) if is_top else (18,18,18)
        img=Image.new('RGB',(W,BOX_H),bg)
        d=ImageDraw.Draw(img)
        # خط كبير مضمون 100%
        f_ar = get_font(78) # عربي 78
        f_en = get_font(62) # انجليزي 62 كبير واضح + stroke
        f_share = get_font(28)

        ar_fixed=fix_ar(ar_text)
        en_clean=str(en_text).strip().upper()

        # عربي
        try:
            bbox=d.textbbox((0,0), ar_fixed, font=f_ar)
            x=(W-(bbox[2]-bbox[0]))//2
            d.text((x, 55), ar_fixed, font=f_ar, fill=(10,10,10) if is_top else (245,230,190), stroke_width=1, stroke_fill=(0,0,0) if is_top else None)
        except:
            d.text((100, 55), ar_text, fill=(0,0,0))

        # انجليزي - كبير وواضح مع Stroke
        try:
            bbox2=d.textbbox((0,0), en_clean, font=f_en)
            x2=(W-(bbox2[2]-bbox2[0]))//2
            # خلفية للنص الانجليزي عشان يبين
            # stroke اسود للبوكس الفاتح وابيض للغامق
            if is_top:
                d.text((x2, 210), en_clean, font=f_en, fill=(20,20,20), stroke_width=2, stroke_fill=(255,255,255))
            else:
                d.text((x2, 210), en_clean, font=f_en, fill=(255,255,255), stroke_width=3, stroke_fill=(0,0,0))
        except Exception as e:
            log(f"en draw {e}")
            d.text((W//2-150, 210), en_clean, fill=(255,0,0))

        try:
            d.text((W-175, 20), "> SHARE", font=f_share, fill=(100,100,100) if is_top else (120,120,120))
        except: pass

        p=Path(tempfile.gettempdir())/f"b_{random.randint(1,9999999)}.png"
        img.save(p, quality=98)
        return p

    top_box_p=make_box(idea["action_ar"], idea["action_en"], True)
    bottom_box_p=make_box(idea["reaction_ar"], idea["reaction_en"], False)
    top_box=ImageClip(str(top_box_p), duration=duration).set_position((0,0))
    bottom_box=ImageClip(str(bottom_box_p), duration=duration).set_position((0, BOX_H+IMG_H*2+12))

    final=CompositeVideoClip([top_clip, bottom_clip, top_box, bottom_box], size=(W,H)).set_duration(duration)

    # موسيقى جديدة - Sad Lo-fi Piano Arpeggio + Bass + Echo
    try:
        log("Music NEW sad arpeggio")
        SR=44100
        def piano_note(freq, dur, t_offset):
            # ADSR بيانو
            env = np.exp(-3.5*t_offset) * (1 - np.exp(-20*t_offset)) if t_offset>=0 else 0
            # harmonics بيانو
            v = 0.6*np.sin(2*np.pi*freq*t_offset)
            v+= 0.3*np.sin(2*np.pi*freq*2*t_offset)*np.exp(-t_offset*2)
            v+= 0.15*np.sin(2*np.pi*freq*3*t_offset)*np.exp(-t_offset*3)
            v+= 0.08*np.sin(2*np.pi*freq*4*t_offset)*np.exp(-t_offset*4)
            return v*env*0.8

        # progression حزينة بطيئة Am - F - C - G كل 2.25 ثانية
        chords = [
            [220.00, 261.63, 329.63, 440.00], # Am
            [174.61, 220.00, 261.63, 349.23], # F
            [130.81, 164.81, 196.00, 261.63], # C
            [98.00, 123.47, 146.83, 196.00], # G low
        ]

        def make_frame(t):
            if isinstance(t, (int,float,np.floating)):
                tt=float(t)
                chord_idx = int((tt % 9.0) // 2.25)
                chord = chords[chord_idx]
                pos_in_chord = tt % 2.25
                # arpeggio - كل نوتة 0.45 ثانية
                v=0.0
                for i, f in enumerate(chord):
                    note_start = i*0.38
                    if pos_in_chord >= note_start:
                        v+= piano_note(f, 2.0, pos_in_chord - note_start)* [1.0,0.7,0.5,0.35][i]
                    # bass طويل
                    if i==0:
                        v+= piano_note(f*0.5, 2.25, pos_in_chord)*0.45
                # echo بسيط
                if pos_in_chord>0.6:
                    v+= v*0.25*np.exp(-(pos_in_chord-0.6)*2)
                return np.array([v*1.2, v*1.2], dtype=np.float32)
            else:
                arr=np.atleast_1d(t).astype(float)
                out=[]
                for x in arr:
                    out.append(make_frame(float(x))[0])
                vals=np.array(out, dtype=np.float32)
                return np.column_stack([vals, vals])

        music=AudioClip(make_frame, duration=duration).volumex(1.8).audio_fadein(0.5).audio_fadeout(1.5)
        final=final.set_audio(music)
        has_audio=True
        log("Music OK new loud")
    except Exception as e:
        import traceback
        log(f"music fail {e} {traceback.format_exc()[-300:]}")
        has_audio=False

    out=Path(tempfile.gettempdir())/f"reel_{int(time.time())}.mp4"
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac' if has_audio else None, preset='ultrafast', bitrate="9000k", logger=None)
    for p in [top_tmp,bottom_tmp,top_box_p,bottom_box_p]:
        try: Path(p).unlink(missing_ok=True)
        except: pass
    return out, has_audio

def send_text(cid,t):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id":cid,"text":str(t)}, timeout=30)
    except: pass
def send_photo(cid,p):
    with open(p,"rb") as f: requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":str(cid)}, files={"photo":("f.jpg",f,"image/jpeg")}, timeout=180)
def send_video(cid,p):
    with open(p,"rb") as f:
        r=requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id":str(cid),"supports_streaming":True}, files={"video":("reel.mp4",f,"video/mp4")}, timeout=300)
        if r.status_code!=200: raise RuntimeError(r.text[:600])

processing=set(); plock=threading.Lock()

def process(cid, cinematic=False, force_new=False):
    try:
        with plock:
            if cid in processing: send_text(cid,"⏳ يولد..."); return
            processing.add(cid)
        if force_new:
            send_text(cid,"✨ V22 NEW\nEN 62px + Stroke واضح | 🎵 بيانو Arpeggio جديد عالي")
            idea=brain_generate_smart()
            send_text(cid,f"✅ {idea['action_ar']} -> {idea['reaction_ar']}\nEN {idea['action_en']} -> {idea['reaction_en']}")
            combined,qmsg=generate_two_in_one(idea, force_new=True)
        else:
            if not LAST_IMAGE_PATH.exists() or LAST_IMAGE_PATH.stat().st_size<50000:
                send_text(cid,"⚠️ ما في cache - بولد جديد")
                idea=brain_generate_smart()
                combined,qmsg=generate_two_in_one(idea, force_new=True)
            else:
                idea={"action_ar":"تحفظهم ككلمة مرور","reaction_ar":"فينسونك كإعلان","action_en":"SAVE AS PASSWORD","reaction_en":"FORGOTTEN LIKE ADS"}
                combined=LAST_IMAGE_PATH
                qmsg="CACHED 0$"
                send_text(cid,"🧪 TEST مجاني V22")
        send_text(cid,f"✅ صور | {qmsg}")
        if cinematic:
            vid,has_audio=create_reels_video(combined, idea, 9)
            send_text(cid,f"✅ فيديو {'🎵 Arpeggio عالي واضح' if has_audio else '🔇'} | EN واضح + Stroke")
            send_video(cid, vid)
            if vid and Path(vid).exists() and Path(vid)!=LAST_IMAGE_PATH:
                Path(vid).unlink(missing_ok=True)
        else:
            send_photo(cid, combined)
        send_text(cid,"✅ جاهز V22")
    except Exception as e:
        import traceback; log(traceback.format_exc()[-900:])
        send_text(cid,f"❌ فشل {str(e)[:300]}")
    finally:
        with plock: processing.discard(cid)

def handle_update(upd):
    try:
        msg=upd.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
        if not chat: return
        if text.startswith("/start"):
            has=LAST_IMAGE_PATH.exists() and LAST_IMAGE_PATH.stat().st_size>50000
            send_text(chat,f"V22 FINAL ✅\nEN 62px + Stroke واضح جدا\nAR 78px\n🎵 بيانو Arpeggio حزين جديد عالي 1.8\nبوكس 380 + صورة 600 واضحة\nزوم 6% خفيف\n\n/cinema جديد\n/test_video مجاني {'✅' if has else '⚠️'}\n/ping")
            return
        if text.startswith("/ping"): send_text(chat,f"V22 | cache:{'نعم' if LAST_IMAGE_PATH.exists() else 'لا'}"); return
        if "/test_video" in text:
            if LAST_IMAGE_PATH.exists() and LAST_IMAGE_PATH.stat().st_size>50000:
                threading.Thread(target=process, args=(chat, True, False), daemon=True).start()
            else:
                threading.Thread(target=process, args=(chat, True, True), daemon=True).start()
            return
        if "/cinema" in text: threading.Thread(target=process, args=(chat, True, True), daemon=True).start(); return
        if "/hikma" in text: threading.Thread(target=process, args=(chat, False, True), daemon=True).start(); return
    except Exception as e: log(f"handle {e}")

@app.get("/")
def home(): return f"V22 FINAL cached={LAST_IMAGE_PATH.exists()}",200
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
