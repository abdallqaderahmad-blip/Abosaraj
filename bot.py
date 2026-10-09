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
    "hero": "Handsome real Arab man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, photorealistic consistent face, cinematic",
    "princess": "Beautiful real Arab woman age 22 long black hair green eyes royal white gold dress, photorealistic consistent face",
    "king": "Old Arab king age 60 white beard crown royal red robe serious face cinematic",
    "wolf": "Small white wolf pup cute fluffy blue eyes forest snow photorealistic",
    "tiger": "GIANT TIGER enormous 4 meters orange black stripes angry roaring forest"
}

# صوت بشري طبيعي هادئ - مش مزعج
VOICE_MAP = {
    "hero": {"voice": "ar-SA-HamedNeural", "rate": "-20%", "pitch": "-4Hz"},
    "princess": {"voice": "ar-EG-SalmaNeural", "rate": "-15%", "pitch": "+2Hz"},
    "king": {"voice": "ar-SA-HamedNeural", "rate": "-25%", "pitch": "-10Hz"},
    "wolf": {"voice": "ar-EG-SalmaNeural", "rate": "-10%", "pitch": "+5Hz"},
    "tiger": {"voice": "ar-SA-HamedNeural", "rate": "-30%", "pitch": "-15Hz"}
}

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
EPISODE_COUNTER = {}
GLOBAL_BANK = CHARACTER_BANK.copy()

def log(m):
    with lock: print("[FINAL 60S] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

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

# صور - مستحيل شاشة سودة
def gen_image(prompt, out):
    clean = prompt[:350].replace("\n"," ").strip()
    safe = urllib.parse.quote(clean + " cinematic movie still ultra detailed 8k beautiful lighting")
    urls = [
        f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux&nologo=true&seed={int(time.time()*1000)%9999}",
        f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&nologo=true&seed={int(time.time()*1000)%9999}",
        f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux-realism&nologo=true",
    ]
    for url in urls:
        for _ in range(2):
            try:
                r = requests.get(url, timeout=90)
                if r.status_code==200 and len(r.content)>25000:
                    Path(out).write_bytes(r.content)
                    log(f"IMG OK {len(r.content)}")
                    return Path(out)
                time.sleep(1)
            except Exception as e:
                log(f"IMG fail {e}")
                time.sleep(1.5)
    # fallback - صورة جميلة مش سودة
    try:
        r = requests.get(f"https://picsum.photos/720/1280?random={int(time.time())}", timeout=15)
        if r.status_code==200:
            Path(out).write_bytes(r.content)
            return Path(out)
    except: pass
    # تدرج جميل
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i","color=c=0x3a2a6a:s=720x1280:d=1","-frames:v","1",str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return Path(out)

async def _edge(text, voice, rate, pitch, out):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice(text, char_key, out):
    try:
        cfg = VOICE_MAP.get(char_key, {"voice":"ar-SA-HamedNeural","rate":"-18%","pitch":"-4Hz"})
        # جملة قصيرة بطيئة جذابة
        asyncio.run(_edge(text[:180], cfg["voice"], cfg["rate"], cfg["pitch"], out))
        log(f"VOICE {char_key} OK {cfg['voice']}")
        return out if Path(out).exists() else None
    except Exception as e:
        log(f"Voice fail {e}")
        return None

# فيديو 60 ث - زوم سينمائي حقيقي يشتغل + تأثيرات
def gen_video_clip(img_path, scene, voice_path, out_path, scene_no, is_first, ep, hook):
    def clean(t): return (t or "").replace(":", " ").replace("'", "").replace('"',"").replace("%","").replace("\n"," ").strip()[:90]
    dia_ar = clean(scene.get("dialogue_ar",""))
    cap = clean(scene.get("caption_big",""))

    # زوم سينمائي - كل مشهد مختلف
    if scene_no==1: zoom="min(zoom+0.0025,1.45)"; eq="contrast=1.25:saturation=1.5:brightness=0.04"
    elif scene_no==10: zoom="min(zoom+0.003,1.55)"; eq="contrast=1.35:saturation=1.6:brightness=0.06"
    elif scene_no in [3,7]: zoom="min(zoom+0.0005,1.18)"; eq="contrast=1.12:saturation=1.25"
    else: zoom="min(zoom+0.0012,1.32)"; eq="contrast=1.18:saturation=1.38"

    # تأثيرات + ترجمة طرف تحت خالص y=h-85
    base_vf = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=24:s=720x1280,eq={eq},unsharp=5:5:0.7:5:5:0.0"

    if is_first:
        vf = f"{base_vf},drawtext=fontfile={FONT}:text='EP {ep}':fontcolor=yellow:fontsize=22:box=1:boxcolor=black@0.7:boxborderw=6:borderw=2:bordercolor=black:x=20:y=20,drawtext=fontfile={FONT}:text='{clean(hook)}':fontcolor=white:fontsize=28:box=1:boxcolor=red@0.6:boxborderw=8:borderw=3:bordercolor=black:x=(w-text_w)/2:y=65,drawtext=fontfile={FONT}:text='{cap}':fontcolor=yellow:fontsize=52:borderw=6:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-80,drawtext=fontfile={FONT}:text='{dia_ar}':fontcolor=white:fontsize=26:box=1:boxcolor=black@0.85:boxborderw=12:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-85"
    elif scene_no==10:
        vf = f"{base_vf},drawtext=fontfile={FONT}:text='{cap}':fontcolor=red:fontsize=62:box=1:boxcolor=white@0.9:boxborderw=12:borderw=6:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-60,drawtext=fontfile={FONT}:text='{dia_ar}':fontcolor=white:fontsize=27:box=1:boxcolor=red@0.7:boxborderw=14:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-95,drawtext=fontfile={FONT}:text='يتبع الحلقة {ep+1} >>':fontcolor=yellow:fontsize=19:box=1:boxcolor=black@0.75:boxborderw=6:x=(w-text_w)/2:y=h-35"
    else:
        vf = f"{base_vf},drawtext=fontfile={FONT}:text='{cap}':fontcolor=white:fontsize=46:borderw=5:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-70,drawtext=fontfile={FONT}:text='{dia_ar}':fontcolor=white:fontsize=26:box=1:boxcolor=black@0.82:boxborderw=12:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-85"

    # مدة كل مشهد 6 ثواني ثابتة عشان يطلع 60 ثانية
    if voice_path and Path(voice_path).exists():
        try:
            p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=10)
            dur = float(p.stdout.strip() or "4")
            dur = max(5.5, min(dur+0.5, 6.5)) # كل مشهد 6 ثواني تقريباً
        except: dur = 6.0
        cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-b:a","128k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
    else:
        cmd = ["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf,"-t","6","-r","24","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]

    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    return out_path

def gen_music(out, dur=62):
    try:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo","-f","lavfi","-i",f"sine=frequency=80:duration={dur}", "-filter_complex", "[1:a]volume=0.04,lowpass=f=400,atempo=0.85[a1];[0:a][a1]amix=inputs=2[a]", "-map","[a]","-t",str(dur),"-c:a","aac",str(out)], check=True, timeout=20)
        return out
    except: return None

def create_story(user_text, episode):
    chars = ", ".join(GLOBAL_BANK.keys())
    prompt = f"""You are viral director 60 seconds. User story: "{user_text}" Characters: {chars}
MUST: 10 scenes, each 6 seconds. Scene1 Hook "ماذا لو...", Scene3 betrayal, Scene7 reveal power, Scene10 cliffhanger "ولكن فجأة..."
Dialogue max 12 words Arabic slow attractive, not annoying.
Return JSON: {{"title":"عنوان","hook_text":"سؤال صادم","hashtags":"#قصص","summary":"ملخص","new_characters":[],"scenes":[{{"character_key":"hero","scene_image_prompt":"hero in palace cinematic","dialogue_ar":"جملة قصيرة بطيئة","dialogue_en":"short english","caption_big":"صدمة!"}}]}}
User story: {user_text}"""
    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.85, max_completion_tokens=7000, response_format={"type":"json_object"}, messages=[{"role":"system","content":prompt},{"role":"user","content":f"قصة: {user_text} 10 مشاهد"}])
            story=json.loads(res.choices[0].message.content.strip())
            if len(story.get("scenes",[]))>=8:
                while len(story["scenes"])<10: story["scenes"].append(story["scenes"][-1])
                fixes=["صدمة!","طرده!","خيانة!","هرب!","الغابة!","الذئبة!","الوحش!","انكشف!","قوة!","يتبع..."]
                for i,s in enumerate(story["scenes"]):
                    s["scene_no"]=i+1
                    if i < len(fixes): s["caption_big"]=fixes[i]
                    if i==9: s["dialogue_ar"]="ولكن فجأة... "+s.get("dialogue_ar","")
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
    try:
        if m_path and Path(m_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(v_path),"-i",str(m_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.10[a1];[a0][a1]amix=inputs=2:duration=first[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-shortest",str(out_path)],60)
            return out_path
        else:
            shutil.copy(v_path, out_path)
            return out_path
    except:
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
    work=Path(tempfile.mkdtemp(prefix="final60_"))
    try:
        if chat_id not in EPISODE_COUNTER: EPISODE_COUNTER[chat_id]=1
        else: EPISODE_COUNTER[chat_id]+=1
        ep = EPISODE_COUNTER[chat_id]
        send_text(chat_id, f"🎬 60 ثانية حقيقية - الحلقة {ep}\n📝 {user_story_text[:70]}...\n🎙️ صوت بشري طبيعي هادئ - مش مزعج\n🎥 زوم سينمائي + تأثيرات + موسيقى\n⏳ 4 دقايق - 10 مشاهد كاملة")

        story=create_story(user_story_text, ep)
        videos=[]
        for idx, scene in enumerate(story["scenes"][:10]):
            no=idx+1
            char_key = scene.get("character_key","hero")
            char_desc = GLOBAL_BANK.get(char_key, CHARACTER_BANK["hero"])
            img=work / f"scene_{no}.jpg"
            voice=work / f"scene_{no}.mp3"
            vid=work / f"scene_{no}.mp4"

            gen_image(f"{char_desc}, {scene.get('scene_image_prompt','')}, consistent face dramatic background", img)
            gen_voice(scene.get("dialogue_ar",""), char_key, voice)
            gen_video_clip(img, scene, voice if voice.exists() else None, vid, no, no==1, ep, story.get("hook_text",""))
            videos.append(vid)
            send_text(chat_id, f"✅ {no}/10 {scene.get('caption_big','')} - {char_key}")

        # لازم 10 فيديوهات = 60 ثانية
        if len(videos)!=10:
            raise RuntimeError(f"Only {len(videos)}/10 videos generated")

        music=work / "music.mp3"
        gen_music(music, 62)
        raw=work / "raw.mp4"
        concat_videos(videos, raw)
        final=work / "final.mp4"
        final_mix(raw, music if music.exists() else None, final)

        # فحص المدة
        try:
            p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)], stdout=subprocess.PIPE, text=True, timeout=10)
            dur = float(p.stdout.strip() or "0")
            log(f"Final duration {dur}s")
        except: dur=0

        cap = f"""🎬 {story.get('title','')} - {ep} - {int(dur)}ث

{story.get('hook_text','')}

{story.get('hashtags','')}
⏱️ 60 ثانية كاملة - 10 مشاهد
🎙️ صوت بشري طبيعي هادئ
🎥 زوم سينمائي + تأثيرات + موسيقى
📍 ترجمة طرف تحت
🚫 بدون شاشة سودة
"""
        send_text(chat_id, cap)
        send_video(chat_id, final, f"EP {ep} - 60ث كاملة")
        send_text(chat_id, f"✅ جاهز 60 ثانية! {int(dur)} ث\n🎥 زوم سينمائي يشتغل\n🎙️ صوت طبيعي هادئ مش مزعج\n🔄 ابعت قصة جديدة")

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
        send_text(chat, "🎬 60 ثانية كاملة - بدون شاشة سودة\n\n📝 ابعت قصتك كتابة\n\n✅ 60ث = 10 مشاهد × 6ث\n🎙️ صوت بشري طبيعي هادئ (مش مزعج)\n🎥 زوم سينمائي يشتغل + تأثيرات\n🎵 موسيقى + ترجمة طرف تحت\n\n/test = جرب"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅"); return
    if text=="/ping":
        send_text(chat, f"🟢 60S Fixed Font={FONT}"); return

    user_story=text
    if text=="/test":
        user_story="شاب فقير يتظاهر بالضعف يدخل قصر الملك الملك يهينه ويطرده الأميرة تحبه وتدافع عنه يخرج حزين للغابة يلتقي ذئبة بيضاء صغيرة تنقذه من البرد نمر عملاق يهجم على الذئبة الشاب يكشف قوته الحقيقية ويهزم النمر بضربة واحدة الأميرة ترى قوته وتفرح"

    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, user_story), daemon=True).start()

@app.get("/")
def home(): return f"60S Final Fixed Font={FONT}", 200
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
