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

def log(m): print(f"[V20.4] {str(m)[:900]}", flush=True)

def load_memory():
    if MEMORY_FILE.exists():
        try:
            mem=json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
            for k in ["music_history","ideas","scenes"]:
                if k not in mem: mem[k]=[]
            return mem
        except: pass
    return {"ideas":[],"scenes":[],"music_history":[]}

def save_memory(mem):
    mem["ideas"]=mem["ideas"][-300:]; mem["scenes"]=mem["scenes"][-300:]; mem["music_history"]=mem.get("music_history",[])[-15:]
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

def brain_generate_smart():
    mem=load_memory()
    fallback={"action_ar":"تحفظهم ككلمة مرور","reaction_ar":"فينسونك كإعلان","action_en":"SAVE AS PASSWORD","reaction_en":"FORGOTTEN LIKE ADS","character":"same young man 28yo male white t-shirt short black hair light beard same face","location":"same small apartment evening warm light window left","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright","scene_reaction_en":"TOTALLY DIFFERENT lying on floor head in hands crying phone far away dark lonely","title":"Password Ad"}
    if not groq: return fallback
    recent=", ".join(mem["ideas"][-80:]) or "none"
    for _ in range(10):
        try:
            prompt=f"""Avoid: {recent}
Return JSON ONLY: {{"action_ar":"فعل 3-4 كلمات","reaction_ar":"رد 3-4 كلمات","action_en":"3 WORDS EN CAPS","reaction_en":"3 WORDS EN CAPS","character":"same young man 28yo male white t-shirt short black hair light beard same face - LOCKED","location":"same small apartment evening warm light window left - LOCKED","scene_action_en":"sitting upright on sofa holding phone close to chest hopeful bright","scene_reaction_en":"TOTALLY DIFFERENT lying on floor head in hands crying phone far away dark","mood":"sad_deep","title":"two words"}}"""
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
    try: shutil.copy(out, LAST_IMAGE_PATH)
    except: pass
    return out, f"{msg} | {msg2}"

def generate_two_in_one(idea, force_new=False):
    if not force_new and LAST_IMAGE_PATH.exists() and LAST_IMAGE_PATH.stat().st_size>50000:
        log("CACHED 0$")
        return LAST_IMAGE_PATH, "CACHED 0$ - مجاني"
    return generate_two_in_one_original(idea)

def ensure_fonts():
    base=Path("/tmp/fonts"); base.mkdir(exist_ok=True)
    amiri=base/"Amiri-Bold.ttf"
    dejavu=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    dejavu_extra=base/"DejaVuSans-Bold.ttf"
    # حمل DejaVu لو مش موجود
    if not dejavu.exists() and not dejavu_extra.exists():
        try:
            r=requests.get("https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf", timeout=30)
            if r.status_code==200: dejavu_extra.write_bytes(r.content)
        except: pass
    if not amiri.exists() or amiri.stat().st_size<10000:
        for url in ["https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf"]:
            try:
                r=requests.get(url, timeout=30)
                if r.status_code==200 and len(r.content)>10000:
                    amiri.write_bytes(r.content); break
            except: continue
    d_path = dejavu if dejavu.exists() else dejavu_extra if dejavu_extra.exists() else None
    a_path = amiri if amiri.exists() and amiri.stat().st_size>10000 else None
    return a_path, d_path

REAL_MUSIC = [
    "https://assets.mixkit.co/music/preview/mixkit-sad-piano-1071.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-reflections-09-124.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-lost-in-dreams-159.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-sad-moment-1213.mp3",
]

def get_music():
    mem=load_memory()
    hist=mem.get("music_history",[])
    avail=[m for m in REAL_MUSIC if m not in hist]
    if not avail: avail=REAL_MUSIC; hist=[]
    pick=random.choice(avail)
    hist.append(pick)
    mem["music_history"]=hist[-12:]
    save_memory(mem)
    return pick

def create_reels_video(image_path, idea, duration=9):
    from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, AudioClip
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        HAS_AR=True
    except: HAS_AR=False
    amiri_path, dejavu_path = ensure_fonts()
    has_ar = amiri_path and amiri_path.exists() and HAS_AR

    def fix_ar(t):
        t=str(t).strip()
        if has_ar:
            try: return get_display(arabic_reshaper.reshape(t))
            except: return t
        return t

    W,H=1080,1920
    # زوم 40% واضح
    base=ImageClip(str(image_path), duration=duration).resize(lambda t: 1.0 + 0.40*t/duration).set_position('center')

    def make_box(ar_text, en_text, is_top=True):
        h=620 # اكبر عشان الانجليزي
        bg=(242,230,195) if is_top else (14,14,14)
        img=Image.new('RGB',(W,h),bg)
        d=ImageDraw.Draw(img)
        try:
            # AR 90px EN 72px كبير جدا - زي الصورة اللي فوق
            if amiri_path and amiri_path.exists():
                f_ar=ImageFont.truetype(str(amiri_path), 92)
            elif dejavu_path and Path(dejavu_path).exists():
                f_ar=ImageFont.truetype(str(dejavu_path), 78)
            else:
                f_ar=ImageFont.load_default()

            if dejavu_path and Path(dejavu_path).exists():
                f_en=ImageFont.truetype(str(dejavu_path), 72)
                f_share=ImageFont.truetype(str(dejavu_path), 36)
            else:
                f_en=ImageFont.load_default()
                f_share=ImageFont.load_default()
        except:
            f_ar=f_en=f_share=ImageFont.load_default()

        ar_fixed=fix_ar(ar_text)
        en_clean=str(en_text).strip().upper()

        try:
            # عربي فوق كبير
            bbox_ar=d.textbbox((0,0), ar_fixed, font=f_ar)
            ar_x=(W-(bbox_ar[2]-bbox_ar[0]))//2
            d.text((ar_x, 85), ar_fixed, font=f_ar, fill=(15,15,15) if is_top else (242,230,195))

            # انجليزي تحت - كبير جدا 72px - زي الصورة
            bbox_en=d.textbbox((0,0), en_clean, font=f_en)
            en_x=(W-(bbox_en[2]-bbox_en[0]))//2
            d.text((en_x, 285), en_clean, font=f_en, fill=(20,20,20) if is_top else (245,245,245))

            d.text((W-210, 22), "> SHARE", font=f_share, fill=(90,90,90) if is_top else (130,130,130))
        except Exception as ex:
            log(f"box fail {ex}")
            d.text((80,100), str(ar_text), fill=(0,0,0))
            d.text((80,300), en_clean, fill=(20,20,20))

        p=Path(tempfile.gettempdir())/f"b_{random.randint(1,9999999)}.png"
        img.save(p, quality=98)
        return p

    top_p=make_box(idea["action_ar"], idea["action_en"], True)
    bottom_p=make_box(idea["reaction_ar"], idea["reaction_en"], False)
    top_c=ImageClip(str(top_p), duration=duration).set_position((0,0))
    bottom_c=ImageClip(str(bottom_p), duration=duration).set_position((0, H-620))
    final=CompositeVideoClip([base, top_c, bottom_c], size=(W,H)).set_duration(duration)

    mp=Path(tempfile.gettempdir())/f"m_{random.randint(1,99999)}.mp3"
    audio_added=False

    # محاولة موسيقى حقيقية
    for _ in range(8):
        try:
            url=get_music()
            log(f"music try {url[-40:]}")
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://mixkit.co/","Accept":"*/*"}
            r=requests.get(url, timeout=50, headers=headers, stream=True)
            if r.status_code!=200: continue
            with open(mp,'wb') as f:
                for ch in r.iter_content(32768):
                    if ch: f.write(ch)
            size=mp.stat().st_size if mp.exists() else 0
            if size < 30000:
                if mp.exists(): mp.unlink(missing_ok=True)
                continue
            full_audio=AudioFileClip(str(mp))
            if full_audio.duration < 1.5:
                full_audio.close(); mp.unlink(missing_ok=True); continue
            if full_audio.duration > duration:
                start = max(0, (full_audio.duration-duration)/2.5)
                audio=full_audio.subclip(start, start+duration)
            else:
                from moviepy.audio.fx.all import audio_loop
                try: audio=audio_loop(full_audio, duration=duration)
                except:
                    from moviepy.editor import concatenate_audioclips
                    audio=concatenate_audioclips([full_audio]*5).subclip(0,duration)
            audio=audio.volumex(0.75).audio_fadein(0.9).audio_fadeout(1.4)
            final=final.set_audio(audio)
            audio_added=True
            log("music OK REAL")
            break
        except Exception as e:
            log(f"music err {e}")
            if mp.exists(): mp.unlink(missing_ok=True)
            continue

    # Fallback بيانو Lo-fi حقيقي - مصلح ambiguous
    if not audio_added:
        try:
            log("FALLBACK Sad Piano")
            def _single(tt):
                cycle = tt % 9.0
                pos = cycle % 2.25
                if cycle < 2.25: freqs=[220.0,261.63,329.63]
                elif cycle < 4.5: freqs=[174.61,220.0,261.63]
                elif cycle < 6.75: freqs=[261.63,329.63,392.0]
                else: freqs=[196.0,246.94,293.66]
                decay = max(0.05, float(np.exp(-pos*1.8)))
                val=0.0
                for i,f in enumerate(freqs):
                    amp=[0.5,0.3,0.2][i]
                    note = amp*np.sin(2*np.pi*f*tt)*decay
                    note += amp*0.4*np.sin(2*np.pi*f*2*tt)*decay*0.5
                    val+=note
                val*=0.38
                return val

            def sad_frame(t):
                # مصلح: يتعامل مع array و single
                if isinstance(t, (int,float,np.floating)):
                    v=_single(float(t))
                    return np.array([v, v], dtype=np.float32)
                else:
                    arr=np.atleast_1d(t).astype(float)
                    vals=np.array([_single(float(x)) for x in arr], dtype=np.float32)
                    return np.column_stack([vals, vals])

            fallback_audio=AudioClip(sad_frame, duration=duration).volumex(0.85).audio_fadein(0.8).audio_fadeout(1.6)
            final=final.set_audio(fallback_audio)
            audio_added=True
            log("FALLBACK OK")
        except Exception as e:
            import traceback
            log(f"fallback fail {e}")

    out=Path(tempfile.gettempdir())/f"reel_{int(time.time())}.mp4"
    final.write_videofile(str(out), fps=30, codec='libx264', audio_codec='aac' if audio_added else None, preset='ultrafast', bitrate="8000k", logger=None)
    for p in [top_p,bottom_p,mp]:
        try: Path(p).unlink(missing_ok=True)
        except: pass
    return out, audio_added

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
            send_text(cid,"✨ V20.4 NEW\nEN 72px AR 92px كبير\n🎵 حزينة تريند | زوم 40%")
            idea=brain_generate_smart()
            send_text(cid,f"✅ {idea['action_ar']} -> {idea['reaction_ar']}\nEN {idea['action_en']} -> {idea['reaction_en']}")
            combined,qmsg=generate_two_in_one(idea, force_new=True)
        else:
            if not LAST_IMAGE_PATH.exists() or LAST_IMAGE_PATH.stat().st_size<50000:
                send_text(cid,"⚠️ ما في cache - بولد جديد مرة وحدة")
                idea=brain_generate_smart()
                combined,qmsg=generate_two_in_one(idea, force_new=True)
            else:
                idea={"action_ar":"تحفظهم ككلمة مرور","reaction_ar":"فينسونك كإعلان","action_en":"SAVE AS PASSWORD","reaction_en":"FORGOTTEN LIKE ADS"}
                combined=LAST_IMAGE_PATH
                qmsg="CACHED 0$ - مجاني"
                send_text(cid,"🧪 TEST مجاني 0$ | EN 72px كبير | موسيقى حزينة")
        send_text(cid,f"✅ صور | {qmsg}")
        if cinematic:
            vid,has_audio=create_reels_video(combined, idea, 9)
            send_text(cid,f"✅ فيديو {'🎵 حزينة' if has_audio else '🔇'} | 9ث | EN كبير")
            send_video(cid, vid)
            if vid and Path(vid).exists() and Path(vid)!=LAST_IMAGE_PATH:
                Path(vid).unlink(missing_ok=True)
        else:
            send_photo(cid, combined)
        send_text(cid,"✅ جاهز")
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
            send_text(chat,f"V20.4 FINAL ✅\nEN 72px AR 92px - كبير زي الصورة\n🎵 حزينة تريند - مصلح صامت\nزوم 40%\n\n/cinema جديد\n/test_video مجاني {'✅' if has else '⚠️ اول مرة بفلوس'}\n/ping")
            return
        if text.startswith("/ping"): send_text(chat,f"V20.4 | cache:{'نعم' if LAST_IMAGE_PATH.exists() else 'لا'}"); return
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
def home(): return f"V20.4 FINAL cached={LAST_IMAGE_PATH.exists()}",200
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
