import os, json, time, shutil, tempfile, threading, subprocess, urllib.parse, asyncio
from pathlib import Path
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except: groq = None

CHARACTER_BANK = {
    "hero": "Handsome real Arab man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, photorealistic consistent face cinematic",
    "princess": "Beautiful real Arab woman age 22 long black hair green eyes royal white gold dress palace, photorealistic consistent face",
    "king": "Old Arab king age 60 white beard crown royal red robe throne palace serious face cinematic",
    "wolf": "Small white wolf pup cute fluffy blue eyes snow forest night moonlight",
    "tiger": "GIANT TIGER enormous 4 meters orange black stripes angry roaring dark forest"
}

VOICE_MAP = {
    "hero": {"voice": "ar-SA-HamedNeural", "rate": "-16%", "pitch": "-2Hz"},
    "princess": {"voice": "ar-EG-SalmaNeural", "rate": "-10%", "pitch": "+3Hz"},
    "king": {"voice": "ar-SA-HamedNeural", "rate": "-24%", "pitch": "-9Hz"},
    "wolf": {"voice": "ar-EG-SalmaNeural", "rate": "-6%", "pitch": "+5Hz"},
    "tiger": {"voice": "ar-SA-HamedNeural", "rate": "-30%", "pitch": "-13Hz"}
}

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
EPISODE_COUNTER = {}

def log(m):
    with lock: print("[FINAL V5 ANTI-BORING] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def get_font():
    font_dir = Path("/tmp/fonts")
    font_dir.mkdir(exist_ok=True)
    fp = font_dir / "Amiri-Regular.ttf"
    if not fp.exists():
        try:
            r = requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf", timeout=30)
            if r.status_code==200: fp.write_bytes(r.content)
        except: pass
    for f in [str(fp), "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(f).exists(): return f
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

FONT = get_font()

def gen_image(prompt, out, scene_no):
    clean = prompt[:380].replace("cross","").replace("church","").replace("\n"," ").strip()
    safe = urllib.parse.quote(clean + f" cinematic movie still scene {scene_no}, ultra detailed 8k dramatic lighting no text no watermark")
    seed = int(time.time()*1000) + scene_no*133
    for attempt in range(4):
        urls = [
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux&nologo=true&seed={seed+attempt}",
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&nologo=true&seed={seed+attempt+11}",
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux-realism&nologo=true&seed={seed+attempt+22}",
        ]
        for url in urls:
            try:
                r = requests.get(url, timeout=90)
                if r.status_code==200 and len(r.content)>35000:
                    Path(out).write_bytes(r.content)
                    log(f"IMG {scene_no} OK")
                    return Path(out)
            except: time.sleep(1.5)
    col = {1:"0x8a4a2a",2:"0x6a2a2a",3:"0x7a3a3a",4:"0x2a4a3a",5:"0x1a3a4a",6:"0x2a4a5a",7:"0x4a2a1a",8:"0x3a2a1a",9:"0x5a1a2a",10:"0x1a1a4a"}.get(scene_no,"0x2a2a4a")
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"color=c={col}:s=720x1280:d=1","-vf","eq=contrast=1.3:saturation=1.4,gblur=sigma=15","-frames:v","1",str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return Path(out)

async def _edge(text, voice, rate, pitch, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice(text, char_key, out, scene_no):
    try:
        cfg = VOICE_MAP.get(char_key, {"voice":"ar-SA-HamedNeural","rate":"-15%","pitch":"-2Hz"})
        clean_text = text.strip()[:140]
        if not clean_text: clean_text = f"المشهد {scene_no}"
        asyncio.run(_edge(clean_text, cfg["voice"], cfg["rate"], cfg["pitch"], out))
        log(f"VOICE {scene_no} {char_key}")
        return out if Path(out).exists() else None
    except Exception as e:
        log(f"Voice fail {e}")
        return None

def gen_video_clip(img_path, scene, voice_path, out_path, scene_no):
    def esc(t):
        t = str(t or "").replace("\\"," ").replace(":"," ").replace("'","").replace('"',"").replace("%","").replace("\n"," ").strip()[:68]
        return t

    raw = esc(scene.get("dialogue_ar",""))

    # زوم متنوع ضد الملل - كل مزاج زوم مختلف
    if scene_no==1: # Hook صادم سريع
        zoom="min(zoom+0.006,1.60)"
        eq="eq=contrast=1.32:saturation=1.55:brightness=0.01"
    elif scene_no in [2,6]: # غضب وخطر متوسط + غامق
        zoom="min(zoom+0.0035,1.35)"
        eq="eq=contrast=1.38:saturation=1.25:brightness=-0.04"
    elif scene_no in [4,5]: # حزن ترثي بطيء جدا + باهت
        zoom="min(zoom+0.0007,1.18)"
        eq="eq=contrast=1.08:saturation=0.85:brightness=0.03"
    elif scene_no==7: # كشف قوة - زوم سريع
        zoom="min(zoom+0.005,1.50)"
        eq="eq=contrast=1.40:saturation=1.6:brightness=0.04"
    elif scene_no==10: # نهاية تشويق سريع جدا
        zoom="min(zoom+0.007,1.68)"
        eq="eq=contrast=1.35:saturation=1.50"
    else:
        zoom="min(zoom+0.0022,1.38)"
        eq="eq=contrast=1.22:saturation=1.40"

    vf_text = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=24:s=720x1280,{eq}:brightness=0.02,unsharp=5:5:0.85:5:5:0.0,vignette=angle=PI/4:mode=forward,drawtext=fontfile={FONT}:text='{raw}':fontcolor=white:fontsize=28:box=1:boxcolor=black@0.88:boxborderw=12:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-88"
    vf_no_text = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=24:s=720x1280,{eq},unsharp=5:5:0.85:5:5:0.0,vignette=angle=PI/4:mode=forward"

    try:
        if voice_path and Path(voice_path).exists():
            p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=10)
            dur = float(p.stdout.strip() or "6")
            dur = max(5.5, min(dur+0.45, 6.2))
        else: dur=6.0
    except: dur=6.0

    try:
        if voice_path and Path(voice_path).exists():
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf_text,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","128k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        else:
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf_text,"-t","6","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    except:
        if voice_path and Path(voice_path).exists():
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf_no_text,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        else:
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf_no_text,"-t","6","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    return out_path

def gen_music(out, dur=63):
    try:
        cmd = ["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo","-f","lavfi","-i",f"sine=frequency=110:duration={dur},sine=frequency=220:duration={dur}","-filter_complex","[1:a]volume=0.26,lowpass=f=750,atempo=0.82,acompressor=threshold=-20dB:ratio=4,lowpass=f=600[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=0[a]","-map","[a]","-t",str(dur),"-c:a","aac","-b:a","96k",str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        log("Music OK 26% audible")
        return out
    except Exception as e:
        log(f"Music fail {e}")
        return None

def create_story(user_text, episode):
    lines = [l.strip() for l in user_text.split("\n") if l.strip()]
    if len(lines) >= 8:
        log(f"USER MODE {len(lines)} lines")
        scenes=[]
        for i in range(10):
            txt = lines[i] if i < len(lines) else lines[-1]
            if ":" in txt[:6]: txt = txt.split(":",1)[-1].strip()
            txt = txt.lstrip("0123456789.-) ").strip()
            if i==9 and "فجأة" not in txt: txt = txt + " ولكن فجأة..."
            key="hero"
            if "ملك" in txt: key="king"
            elif "أميرة" in txt or "ليان" in txt: key="princess"
            elif "ذئبة" in txt or "ذئب" in txt: key="wolf"
            elif "نمر" in txt: key="tiger"
            scenes.append({"scene_no": i+1, "character_key": key, "scene_image_prompt": txt + " cinematic movie still photorealistic", "dialogue_ar": txt[:120], "dialogue_en": txt[:30], "caption_big": ""})
        return {"title": "قصة المستخدم", "hashtags":"#قصص", "scenes": scenes}

    prompt = f"""You are viral director tragic story. User: "{user_text}" 10 scenes each character talks different, tragic style, not boring. Short sentences. Return JSON: {{"title":"عنوان صادم","hashtags":"#قصص","scenes":[{{"character_key":"hero","scene_image_prompt":"detailed different location","dialogue_ar":"جملة قصيرة مختلفة"}}]}} Need 10 UNIQUE short."""
    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.95, max_completion_tokens=8000, response_format={"type":"json_object"}, messages=[{"role":"system","content":prompt},{"role":"user","content":f"قصة: {user_text} 10 مشاهد ترثي"}])
            story=json.loads(res.choices[0].message.content.strip())
            scenes=story.get("scenes",[])
            while len(scenes)<10: scenes.append(scenes[-1].copy())
            for i,s in enumerate(scenes):
                s["scene_no"]=i+1
                s["caption_big"]=""
                if i==9 and "فجأة" not in s.get("dialogue_ar",""): s["dialogue_ar"]="ولكن فجأة... "+s.get("dialogue_ar","")
            story["scenes"]=scenes[:10]
            return story
        except Exception as e:
            log(f"Groq fail {e}")
            time.sleep(1)
    raise RuntimeError("Story fail")

def run_cmd(cmd, timeout=120):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0: raise RuntimeError(p.stderr[-2000:])
    return p

def concat_videos(videos, out):
    if len(videos)==1:
        shutil.copy(videos[0], out)
        return out
    try:
        inputs=[]
        for v in videos: inputs.extend(["-i", str(v)])
        filters=[]
        for i in range(len(videos)):
            filters.append(f"[{i}:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setpts=PTS-STARTPTS[v{i}];[{i}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
        last_v="v0"
        last_a="a0"
        for i in range(1, len(videos)):
            offset = i*6 - i*0.4
            new_v=f"vx{i}"
            new_a=f"ax{i}"
            filters.append(f"[{last_v}][v{i}]xfade=transition=fade:duration=0.4:offset={offset}[{new_v}];[{last_a}][a{i}]acrossfade=d=0.4[{new_a}]")
            last_v=new_v
            last_a=new_a
        fc=";".join(filters)
        cmd=["ffmpeg","-y"]+inputs+["-filter_complex",fc,"-map",f"[{last_v}]","-map",f"[{last_a}]","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
        run_cmd(cmd, 120)
        log(f"Concat xfade OK")
        return out
    except Exception as e:
        log(f"xfade fail {e} fallback")
        lf=out.parent / "concat.txt"
        with lf.open("w", encoding="utf-8") as f:
            for v in videos: f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],90)
        return out

def final_mix(v_path, m_path, out_path):
    try:
        if m_path and Path(m_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(v_path),"-i",str(m_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.18[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k","-shortest",str(out_path)],60)
            return out_path
        else:
            shutil.copy(v_path, out_path)
            return out_path
    except Exception as e:
        log(f"Mix fail {e}")
        shutil.copy(v_path, out_path)
        return out_path

TELEGRAM_API="https://api.telegram.org/bot" + BOT_TOKEN
def telegram(method, data=None, files=None, timeout=60):
    r=requests.post(TELEGRAM_API + "/" + method, data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()
def send_text(chat_id, text): return telegram("sendMessage", {"chat_id": chat_id, "text": text})
def send_video(chat_id, path, caption):
    with Path(path).open("rb") as f:
        return telegram("sendVideo", {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, {"video": ("episode.mp4", f, "video/mp4")}, 600)

def process_story(chat_id, user_story_text):
    work=Path(tempfile.mkdtemp(prefix="finalv5_"))
    try:
        if chat_id not in EPISODE_COUNTER: EPISODE_COUNTER[chat_id]=1
        else: EPISODE_COUNTER[chat_id]+=1
        ep=EPISODE_COUNTER[chat_id]
        is_user = len([l for l in user_story_text.split("\n") if l.strip()]) >=8
        mode = "✍️ قصتك 100%" if is_user else "🤖 تأليف"
        send_text(chat_id, f"🎬 EP {ep} - {mode}\n🎭 ترثي + شخصيات تتحدث - ضد الملل\n🎥 زوم متنوع حزين/غضب/تشويق\n🎞️ دمج fade 0.4s متناسق\n🎵 موسيقى 18% مسموعة\n⏳ 4 دقايق")

        story=create_story(user_story_text, ep)
        videos=[]
        for idx, scene in enumerate(story["scenes"][:10]):
            no=idx+1
            char_key=scene.get("character_key","hero")
            char_desc=CHARACTER_BANK.get(char_key, CHARACTER_BANK["hero"])
            img=work / f"scene_{no}.jpg"
            voice=work / f"scene_{no}.mp3"
            vid=work / f"scene_{no}.mp4"
            gen_image(f"{char_desc}, {scene.get('scene_image_prompt','')}, consistent face no cross", img, no)
            gen_voice(scene.get("dialogue_ar",""), char_key, voice, no)
            gen_video_clip(img, scene, voice if voice.exists() else None, vid, no)
            videos.append(vid)
            send_text(chat_id, f"✅ {no}/10 [{char_key}] {scene.get('dialogue_ar','')[:32]}")

        music=work / "music.mp3"
        gen_music(music, 63)
        raw=work / "raw.mp4"
        concat_videos(videos, raw)
        final=work / "final.mp4"
        final_mix(raw, music if music.exists() else None, final)

        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)], stdout=subprocess.PIPE, text=True, timeout=10)
        try: dur=float(p.stdout.strip() or "0")
        except: dur=0

        send_text(chat_id, f"🎬 {story.get('title','')} - {int(dur)}ث\n✅ ترثي + شخصيات تتحدث\n✅ زوم متنوع ضد الملل\n✅ دمج fade متناسق\n✅ بس ترجمة تحت")
        send_video(chat_id, final, f"EP {ep} {int(dur)}ث - ضد الملل")
        send_text(chat_id, "✅ جاهز!\nابعت 10 أسطر قصتك:\n1:...\n2:...\nاو سطر واحد وهو يألف")
    except Exception as e:
        log("ERROR "+repr(e))
        try: send_text(chat_id, "❌ "+str(e)[:2000])
        except: pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with plock: processing_chats.discard(chat_id)

def handle_update(update):
    msg=update.get("message") or {}
    chat=(msg.get("chat") or {}).get("id")
    text=(msg.get("text") or "").strip()
    if not chat: return
    if text=="/start":
        send_text(chat, "🎬 بوت 60ث - ضد الملل V5\n\n✍️ ابعت 10 أسطر:\n1: شاب يدخل القصر\n2: الملك يهينه (غضب)\n3: يطردوه (حزن)\n4: يمشي بالغابة (ترثي)\n5: يجد ذئبة (حنان)\n6: نمر يهجم (خطر)\n7: يكشف قوته (صدمة)\n8: يهزم النمر (قوة)\n9: الأميرة ترى (حب)\n10: يصبح ملك ولكن فجأة...\n\n✅ كل مشهد شخصية تتحدث\n✅ زوم بطيء للحزن سريع للغضب\n✅ دمج fade متناسق\n✅ موسيقى ترثي\n\n/test = جرب"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅"); return
    if text=="/ping":
        send_text(chat, f"🟢 V5 Anti-Boring Font={FONT}"); return
    user_story=text
    if text=="/test":
        user_story="1: شاب فقير يدخل قصر الملك الذهبي يبحث عن عمل\n2: الملك يصرخ اطردوا هذا القذر من قصري\n3: الحراس يطردون الشاب حزينا خارج القصر\n4: الشاب يمشي وحيدا في الغابة المظلمة يقول الغابة أحن علي من البشر\n5: يجد ذئبة بيضاء صغيرة تبكي من البرد فيحضنها لا تخافي صغيرتي\n6: نمر عملاق يزأر ويهجم على الذئبة\n7: عيون الشاب تلمع حان وقت الحقيقة\n8: الشاب يضرب النمر بضربة أسطورية يطير بعيدا\n9: الأميرة تقول يا إلهي ما هذه القوة العظيمة\n10: الملك يبكي سامحني يا بني وتصبح انت الملك ولكن فجأة سمعنا صوتا من السماء"
    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, user_story), daemon=True).start()

@app.get("/")
def home(): return f"V5 Anti-Boring Bot Font={FONT}", 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if os.getenv("TEST_MODE","").lower() in ("1","true"): return
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    url=RENDER_EXTERNAL_URL + "/telegram/webhook"
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", data={"url": url, "drop_pending_updates": "true"}, timeout=10)
    except: pass

if __name__=="__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
