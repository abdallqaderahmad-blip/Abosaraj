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
    "hero": "Handsome real Arab man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, royal palace background, photorealistic consistent face cinematic",
    "princess": "Beautiful real Arab woman age 22 long black hair green eyes royal white gold dress palace, photorealistic consistent face",
    "king": "Old Arab king age 60 white beard crown royal red robe throne palace serious face cinematic",
    "wolf": "Small white wolf pup cute fluffy blue eyes snow forest night moonlight",
    "tiger": "GIANT TIGER enormous 4 meters orange black stripes angry roaring dark forest"
}

VOICE_MAP = {
    "hero": {"voice": "ar-SA-HamedNeural", "rate": "-18%", "pitch": "-3Hz"},
    "princess": {"voice": "ar-EG-SalmaNeural", "rate": "-12%", "pitch": "+2Hz"},
    "king": {"voice": "ar-SA-HamedNeural", "rate": "-22%", "pitch": "-10Hz"},
    "wolf": {"voice": "ar-EG-SalmaNeural", "rate": "-8%", "pitch": "+4Hz"},
    "tiger": {"voice": "ar-SA-HamedNeural", "rate": "-28%", "pitch": "-14Hz"}
}

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
EPISODE_COUNTER = {}

def log(m):
    with lock: print("[FINAL EFFECTS] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

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

# صور قصة حقيقية - كل مشهد مختلف seed مختلف
def gen_image(prompt, out, scene_no):
    clean = prompt[:380].replace("cross","").replace("church","").replace("\n"," ").strip()
    safe = urllib.parse.quote(clean + f" cinematic movie still scene {scene_no}, ultra detailed 8k, dramatic lighting, no text, no watermark")
    # seed مختلف كل مشهد عشان ما يتكرر
    seed = int(time.time()*1000) + scene_no*111

    for attempt in range(4):
        urls = [
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux&nologo=true&seed={seed+attempt}",
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&nologo=true&seed={seed+attempt+10}",
            f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux-realism&nologo=true&seed={seed+attempt+20}",
        ]
        for url in urls:
            try:
                log(f"IMG {scene_no} try {attempt+1}")
                r = requests.get(url, timeout=90)
                if r.status_code==200 and len(r.content)>35000:
                    Path(out).write_bytes(r.content)
                    log(f"IMG {scene_no} OK {len(r.content)}")
                    return Path(out)
            except: time.sleep(1.5)

    # fallback تدرج حسب المشهد - مش عشوائي
    colors = {1:"0x8a4a2a",2:"0x6a2a2a",3:"0x7a3a3a",4:"0x2a4a3a",5:"0x1a3a4a",6:"0x2a4a5a",7:"0x4a2a1a",8:"0x3a2a1a",9:"0x5a1a",10:"0x1a1a4a"}
    col = colors.get(scene_no,"0x2a2a4a")
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"color=c={col}:s=720x1280:d=1","-vf","eq=contrast=1.3:saturation=1.4,gblur=sigma=15","-frames:v","1",str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return Path(out)

async def _edge(text, voice, rate, pitch, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice(text, char_key, out, scene_no):
    try:
        # كل مشهد جملة مختلفة تماماً - ممنوع تكرار
        cfg = VOICE_MAP.get(char_key, {"voice":"ar-SA-HamedNeural","rate":"-15%","pitch":"-3Hz"})
        clean_text = text.strip()[:150]
        if not clean_text: clean_text = f"المشهد {scene_no}"
        asyncio.run(_edge(clean_text, cfg["voice"], cfg["rate"], cfg["pitch"], out))
        log(f"VOICE {scene_no} {char_key} OK: {clean_text[:30]}")
        return out if Path(out).exists() else None
    except Exception as e:
        log(f"Voice fail {e}")
        return None

# فيديو فيه كل التأثيرات + زوم يتحرك + موسيقى عالية
def gen_video_clip(img_path, scene, voice_path, out_path, scene_no):
    def clean(t): return (t or "").replace(":", " ").replace("'", "").replace('"',"").replace("%","").replace("\n"," ").strip()[:90]
    dia_ar = clean(scene.get("dialogue_ar",""))

    # زوم سينمائي قوي يتحرك بوضوح
    if scene_no==1: zoom="min(zoom+0.004,1.55)" # سريع أول مشهد
    elif scene_no==10: zoom="min(zoom+0.005,1.65)" # أسرع آخر مشهد
    elif scene_no in [3,7]: zoom="min(zoom+0.001,1.25)" # بطيء توتر
    else: zoom="min(zoom+0.002,1.40)"

    # تأثيرات سينمائية: vignette + تباين + حدة + زوم + ترجمة تحت فقط
    vf = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=24:s=720x1280,eq=contrast=1.22:saturation=1.4:brightness=0.02,unsharp=5:5:0.8:5:5:0.0,vignette=angle=PI/4:mode=forward,drawtext=fontfile={FONT}:text='{dia_ar}':fontcolor=white:fontsize=27:box=1:boxcolor=black@0.88:boxborderw=14:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-90"

    if voice_path and Path(voice_path).exists():
        try:
            p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=10)
            dur = float(p.stdout.strip() or "6")
            dur = max(5.8, min(dur+0.6, 6.5))
        except: dur = 6.0
        cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","128k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
    else:
        cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf,"-t","6","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]

    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    return out_path

# موسيقى مسموعة - 15% مش 3%
def gen_music(out, dur=63):
    try:
        # موسيقى حزينة مسموعة
        cmd = [
            "ffmpeg","-y",
            "-f","lavfi","-i","anullsrc=r=44100:cl=stereo",
            "-f","lavfi","-i",f"sine=frequency=110:duration={dur},sine=frequency=220:duration={dur}",
            "-filter_complex",
            "[1:a]volume=0.25,lowpass=f=800,atempo=0.8,acompressor=threshold=-20dB:ratio=4,lowpass=f=600[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map","[a]","-t",str(dur),"-c:a","aac","-b:a","96k",str(out)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        log(f"Music OK {dur}s volume 0.25 audible")
        return out
    except Exception as e:
        log(f"Music fail {e}")
        return None

def create_story(user_text, episode):
    # منع التكرار - كل مشهد جملة مختلفة
    prompt = f"""You are viral director 60s. User story: "{user_text}"
RULES: 10 scenes, each dialogue_ar MUST be DIFFERENT, no repetition.
Scene1 Hook "ماذا لو كان الفقير أقوى من الملك", Scene2 palace insult, Scene3 betrayal, Scene4 forest sad, Scene5 wolf rescue, Scene6 tiger attack, Scene7 power reveal, Scene8 princess sees, Scene9 victory, Scene10 cliffhanger "ولكن فجأة..."
Each scene_image_prompt detailed different location.
Return JSON: {{"title":"عنوان صادم","hook_text":"سؤال صادم","hashtags":"#قصص","summary":"ملخص","new_characters":[],"scenes":[{{"character_key":"hero","scene_image_prompt":"young Arab man entering royal palace golden lights cinematic closeup","dialogue_ar":"جملة مختلفة تماما عن الباقي","dialogue_en":"short"}}]}} User: {user_text} Make 10 UNIQUE dialogues, no repeat."""

    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.9, max_completion_tokens=8000, response_format={"type":"json_object"}, messages=[{"role":"system","content":prompt},{"role":"user","content":f"قصة: {user_text} 10 مشاهد جمل مختلفة"}])
            story=json.loads(res.choices[0].message.content.strip())
            scenes = story.get("scenes",[])
            if len(scenes)>=8:
                while len(scenes)<10: scenes.append(scenes[-1].copy())
                # تأكد كل جملة مختلفة
                seen=set()
                for i,s in enumerate(scenes):
                    s["scene_no"]=i+1
                    s["caption_big"]=""
                    txt=s.get("dialogue_ar","")
                    if txt in seen:
                        s["dialogue_ar"]=f"المشهد {i+1} {txt}"
                    seen.add(s.get("dialogue_ar",""))
                    if i==9 and "فجأة" not in s.get("dialogue_ar",""):
                        s["dialogue_ar"]="ولكن فجأة... "+s.get("dialogue_ar","")
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
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],90)
    return out

def final_mix(v_path, m_path, out_path):
    # موسيقى مسموعة 15% + صوت واضح 100%
    try:
        if m_path and Path(m_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(v_path),"-i",str(m_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.18[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k","-shortest",str(out_path)],60)
            log("Final mix with music 18% audible")
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
    work=Path(tempfile.mkdtemp(prefix="effects_"))
    try:
        if chat_id not in EPISODE_COUNTER: EPISODE_COUNTER[chat_id]=1
        else: EPISODE_COUNTER[chat_id]+=1
        ep = EPISODE_COUNTER[chat_id]
        send_text(chat_id, f"🎬 60ث تأثيرات + موسيقى مسموعة - {ep}\n🎙️ 10 جمل مختلفة - بدون تكرار\n🎥 زوم قوي + vignette + حدة\n🎵 موسيقى 18% مسموعة\n⏳ 4 دقايق")

        story=create_story(user_story_text, ep)
        videos=[]
        for idx, scene in enumerate(story["scenes"][:10]):
            no=idx+1
            char_key = scene.get("character_key","hero")
            char_desc = CHARACTER_BANK.get(char_key, CHARACTER_BANK["hero"])
            img=work / f"scene_{no}.jpg"
            voice=work / f"scene_{no}.mp3"
            vid=work / f"scene_{no}.mp4"

            gen_image(f"{char_desc}, {scene.get('scene_image_prompt','')}, consistent face, no cross", img, no)
            gen_voice(scene.get("dialogue_ar",""), char_key, voice, no)
            gen_video_clip(img, scene, voice if voice.exists() else None, vid, no)
            videos.append(vid)
            send_text(chat_id, f"✅ {no}/10 {scene.get('dialogue_ar','')[:30]}")

        if len(videos)!=10: raise RuntimeError(f"Only {len(videos)}/10")
        music=work / "music.mp3"
        gen_music(music, 63)
        raw=work / "raw.mp4"
        concat_videos(videos, raw)
        final=work / "final.mp4"
        final_mix(raw, music if music.exists() else None, final)

        p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)], stdout=subprocess.PIPE, text=True, timeout=10)
        try: dur=float(p.stdout.strip() or "0")
        except: dur=0

        send_text(chat_id, f"🎬 {story.get('title','')} - {int(dur)}ث\n\n✅ 10 جمل مختلفة - بدون تكرار\n✅ زوم قوي يتحرك + vignette + حدة\n✅ موسيقى حزينة مسموعة 18%\n✅ بس ترجمة تحت + صور من القصة")
        send_video(chat_id, final, f"EP {ep} {int(dur)}ث تأثيرات + موسيقى")
        send_text(chat_id, "✅ جاهز! تأثيرات + موسيقى مسموعة + بدون تكرار\n🔄 ابعت قصة جديدة")
    except Exception as e:
        log("ERROR " + repr(e))
        try: send_text(chat_id, "❌ " + str(e)[:2000])
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
        send_text(chat, "🎬 بوت 60ث تأثيرات كاملة\n\n✨ زوم قوي يتحرك + vignette + حدة\n🎵 موسيقى مسموعة 18%\n🎙️ 10 جمل مختلفة - بدون تكرار\n🚫 بس ترجمة تحت - بدون كلام بالنص\n🚫 بدون صليب + بدون علامة مائية\n📝 ابعت قصتك\n\n/test = جرب"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅"); return
    if text=="/ping":
        send_text(chat, f"🟢 Effects+Music Font={FONT}"); return

    user_story=text
    if text=="/test":
        user_story="شاب فقير يتظاهر بالضعف يدخل قصر الملك الملك يهينه ويطرده الأميرة تحبه وتدافع عنه يخرج حزين للغابة يلتقي ذئبة بيضاء صغيرة تنقذه من البرد نمر عملاق يهجم على الذئبة الشاب يكشف قوته الحقيقية ويهزم النمر بضربة واحدة الأميرة ترى قوته وتفرح"

    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, user_story), daemon=True).start()

@app.get("/")
def home(): return f"Effects+Music Bot Font={FONT}", 200
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
