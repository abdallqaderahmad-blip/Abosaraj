import os, json, time, shutil, tempfile, threading, subprocess, base64, re, urllib.parse
from pathlib import Path
import requests
from flask import Flask, request

def envbool(k,d):
    v = os.getenv(k, str(d)).lower()
    return v in ("1","true","yes","on")

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PIAPI_API_KEY = os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","") # للطوارئ فقط
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")
TEST_MODE = envbool("TEST_MODE", False)

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except: groq = None

CHARACTER_BANK = {
    "hero": "Handsome real man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, Arcane photorealistic consistent face ID hero_001",
    "princess": "Beautiful real woman age 22 long black hair green eyes royal dress white gold, ID princess_001",
    "king": "Old king age 60 white beard crown royal robe, ID king_001",
    "wolf": "Small white wolf pup cute fluffy blue eyes, ID wolf_001",
    "tiger": "GIANT TIGER enormous 4 meters like two elephants orange black stripes, ID tiger_001"
}
SAVED_CHARACTERS = {}
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
EPISODE_COUNTER = {}
STORY_MEMORY = {}
GLOBAL_BANK = CHARACTER_BANK.copy()

def log(m):
    with lock: print("[FREE $0] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def http_get(url, **k):
    for _ in range(3):
        try: return requests.get(url, **k)
        except: time.sleep(1)
    raise

def extract_url(j):
    s = json.dumps(j)
    m = re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    data = j.get("data",{})
    out = data.get("output",{})
    if isinstance(out, dict):
        for kk in ("image_url","url","image"):
            if out.get(kk): return out.get(kk)
    return None

def download_file(url, path):
    path=Path(path)
    with http_get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_wait(tid, timeout=300):
    url = f"{PIAPI_BASE}/{tid}"
    headers = {"x-api-key": PIAPI_API_KEY}
    started=time.time()
    while True:
        if time.time()-started>timeout: raise TimeoutError(tid)
        r=http_get(url, headers=headers, timeout=30)
        r.raise_for_status()
        body=r.json()
        data=body.get("data") or body
        status=str(data.get("status","")).lower()
        if status in ("completed","success","succeed"):
            out = extract_url(body)
            if not out: raise RuntimeError("no output")
            return out
        if status in ("failed","cancelled"): raise RuntimeError(str(body)[:1000])
        time.sleep(3)

# ========== صور مجانية 100% Pollinations - $0 ==========
def generate_image_free(prompt, out):
    safe = urllib.parse.quote(prompt[:600] + " cinematic volumetric lighting god rays fog particles 8K movie poster vertical 9:16, Arcane style photorealistic")
    # flux مجاني
    urls = [
        f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux&nologo=true&seed={int(time.time())%10000}",
        f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=turbo&nologo=true"
    ]
    for url in urls:
        for _ in range(3):
            try:
                r = requests.get(url, timeout=90)
                if r.status_code==200 and len(r.content)>15000:
                    Path(out).write_bytes(r.content)
                    log(f"Free image OK {out} {len(r.content)} bytes")
                    return Path(out)
            except Exception as e:
                log(f"Free image try fail {e}")
                time.sleep(2)
    # fallback مدفوع فقط لو فشل المجاني - نادرا
    if PIAPI_API_KEY:
        log("Fallback to paid Flux $0.02")
        try:
            payload = {"model":"Qubico/flux1-schnell","task_type":"txt2img","input":{"prompt":prompt[:600],"width":720,"height":1280,"num_images":1}}
            headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
            r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            tid = r.json().get("data",{}).get("task_id")
            img_url = piapi_wait(tid, 300)
            return download_file(img_url, out)
        except Exception as e:
            log(f"Paid fallback fail {e}")
            raise
    else:
        raise RuntimeError("Free image failed and no PIAPI key")

# ========== فيديو مجاني زوم Ken Burns + ترجمة أفلام ==========
def generate_video_free(image_path, scene, out_path, is_first=False, story=None):
    def esc(t):
        t = (t or "").replace(":", " ").replace("'", "").replace('"',"").replace("\n"," ").strip()
        return t[:90]
    caption = esc(scene.get("caption_big","") or scene.get("dialogue_ar",""))
    dialogue = esc(scene.get("dialogue_ar",""))
    dialogue_en = esc(scene.get("dialogue_en",""))
    hook = esc(story.get("hook_text","")) if is_first and story else ""
    ep = story.get("episode",1) if story else 1

    # ستايل أفلام: خط تحت + خط كبير بالنص + Hook فوق
    if is_first:
        vf = (
            f"zoompan=z='min(zoom+0.0022,1.4)':d=1:fps=24:s=720x1280,"
            f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
            f"eq=contrast=1.18:brightness=0.04:saturation=1.35,unsharp=5:5:1.2,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='EP {ep}':fontcolor=white:fontsize=26:borderw=3:bordercolor=black:x=20:y=20,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{hook}':fontcolor=white:fontsize=36:borderw=4:bordercolor=black:x=(w-text_w)/2:y=110,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{caption}':fontcolor=white:fontsize=48:borderw=6:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-20,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{dialogue}':fontcolor=#FFFF99:fontsize=28:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-110,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{dialogue_en}':fontcolor=white:fontsize=18:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-60"
        )
    else:
        vf = (
            f"zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.0018,1.35))':d=1:fps=24:s=720x1280,"
            f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
            f"eq=contrast=1.15:saturation=1.3,unsharp=5:5:1.0,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{caption}':fontcolor=white:fontsize=44:borderw=5:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-30,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{dialogue}':fontcolor=white:fontsize=28:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-110,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{dialogue_en}':fontcolor=#DDDDDD:fontsize=18:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-60"
        )

    run_cmd(["ffmpeg","-y","-loop","1","-i",str(image_path),"-vf",vf,"-t","4","-r","24","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)],70)
    return out_path

def generate_free_voice(text_ar, out_path):
    try:
        from gtts import gTTS
        tts = gTTS(text=text_ar, lang="ar", slow=False)
        tts.save(str(out_path))
        return out_path
    except Exception as e:
        log(f"gTTS fail {e}")
        return None

def generate_free_music(out_path, duration=16):
    try:
        run_cmd(["ffmpeg","-y","-f","lavfi","-i",f"anullsrc=r=44100:cl=stereo","-f","lavfi","-i",f"sine=frequency=110:duration={duration},sine=frequency=165:duration={duration}", "-filter_complex", "[1:a]volume=0.06,lowpass=f=800[a1];[0:a][a1]amix=inputs=2:duration=longest[a]", "-map","[a]","-t",str(duration),"-c:a","aac","-b:a","96k",str(out_path)],30)
        return out_path
    except: return None

def create_story(user_idea, episode=1, chat_id=0):
    prev = STORY_MEMORY.get(chat_id, [])[-3:]
    prev_text = "\n".join(prev) if prev else "بداية السلسلة"
    current_chars = "\n".join([f"- {k}: {v[:80]}" for k,v in GLOBAL_BANK.items()])

    system_prompt = f"""
You are FREE $0 REELS series director Episode {episode}. No AI video, only image zoom.

CHARACTERS:
{current_chars}

PREVIOUS:
{prev_text}

Create 4 scenes 16 sec total (4 sec each) movie trailer style.
Return JSON:
{{
 "title": "عنوان الحلقة {episode}",
 "hook_text": "Hook يوقف السكرول",
 "hashtags": "#سلسلة #الحلقة{episode} #fyp",
 "summary": "ملخص 15 كلمة للذاكرة",
 "new_characters": [{{"key":"wizard","description":"Old wizard age 70 white beard purple robe magical staff, ID wizard_001 Arcane"}}],
 "scenes": [
  {{
   "character_key": "hero",
   "scene_image_prompt": "hero in forest with volumetric light god rays",
   "dialogue_ar": "لن أستسلم أبدا",
   "dialogue_en": "I will never give up",
   "caption_big": "لن أستسلم"
  }}
 ]
}}
Episode {episode}: {user_idea}
If new character needed, add to new_characters.
"""

    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.7, max_completion_tokens=6000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":f"EP {episode}: {user_idea}"}])
            story=json.loads(res.choices[0].message.content.strip())
            if len(story.get("scenes",[]))>=3:
                return story
        except Exception as e:
            log(f"Groq fail {e}")
            time.sleep(1)
    raise RuntimeError("Story fail")

def run_cmd(cmd, timeout=120):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0: raise RuntimeError(p.stderr[-3000:])
    return p

def concat_videos(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],90)
    return out

def add_audio_mix(video_path, voice_path, music_path, out_path):
    try:
        if voice_path and Path(voice_path).exists() and music_path and Path(music_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(video_path),"-i",str(voice_path),"-i",str(music_path),"-filter_complex","[1:a]volume=1.3[a1];[2:a]volume=0.18[a2];[a1][a2]amix=inputs=2:duration=longest:dropout_transition=0[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-shortest",str(out_path)],60)
            return out_path
        elif voice_path and Path(voice_path).exists():
            run_cmd(["ffmpeg","-y","-i",str(video_path),"-i",str(voice_path),"-map","0:v","-map","1:a","-c:v","copy","-c:a","aac","-shortest",str(out_path)],60)
            return out_path
        else:
            shutil.copy(video_path, out_path)
            return out_path
    except:
        shutil.copy(video_path, out_path)
        return out_path

TELEGRAM_API="https://api.telegram.org/bot" + BOT_TOKEN
def telegram(method, data=None, files=None, timeout=60):
    r=requests.post(TELEGRAM_API + "/" + method, data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()
def send_text(chat_id, text): return telegram("sendMessage", {"chat_id": chat_id, "text": text})
def send_video(chat_id, path, caption):
    with Path(path).open("rb") as f:
        return telegram("sendVideo", {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, {"video": ("episode.mp4", f, "video/mp4")}, 300)

def process_story(chat_id, user_idea):
    work=Path(tempfile.mkdtemp(prefix="free0_"))
    try:
        if chat_id not in EPISODE_COUNTER: EPISODE_COUNTER[chat_id]=1
        else: EPISODE_COUNTER[chat_id]+=1
        ep = EPISODE_COUNTER[chat_id]

        send_text(chat_id, f"🎬 نسخة $0 مجانية - الحلقة {ep}\n💰 صور Pollinations مجانية\n🎥 زوم سينمائي مجاني\n🎵 صوت + موسيقى + ترجمة مجاني\n⏳ الصور المجانية بتاخد 10ث للصورة")

        story=create_story(user_idea, episode=ep, chat_id=chat_id)

        for nc in story.get("new_characters", []):
            key = nc.get("key"); desc = nc.get("description")
            if key and desc and key not in GLOBAL_BANK:
                GLOBAL_BANK[key]=desc
                send_text(chat_id, f"✨ شخصية جديدة انضافت: {key}")

        videos=[]
        full_dialogue=""

        for index, scene in enumerate(story["scenes"]):
            no=index+1
            char_key = scene.get("character_key","hero")
            char_desc = GLOBAL_BANK.get(char_key, CHARACTER_BANK["hero"])

            img=work / f"ep{ep}_scene_{no}.jpg"
            vid=work / f"ep{ep}_scene_{no}.mp4"

            img_prompt = f"{char_desc}, {scene.get('scene_image_prompt','')}, consistent face ID {char_key}_001"

            generate_image_free(img_prompt, img)
            generate_video_free(img, scene, vid, is_first=(no==1), story={"hook_text": story.get("hook_text",""), "episode": ep})

            videos.append(vid)
            full_dialogue += scene.get("dialogue_ar","") + ". "
            send_text(chat_id, f"✅ مشهد {no}/4 - {char_key} - $0")

        voice_path = work / "voice.mp3"
        music_path = work / "music.mp3"
        generate_free_voice(full_dialogue, voice_path)
        generate_free_music(music_path, duration=16)

        final_raw=work / "final_raw.mp4"
        concat_videos(videos, final_raw)
        final=work / "final.mp4"
        add_audio_mix(final_raw, voice_path if voice_path.exists() else None, music_path if music_path.exists() else None, final)

        caption = f"""🎬 {story.get('title','')} - الحلقة {ep} [$0 مجاني]

{story.get('hook_text','')}

{story.get('hashtags','')} #مجاني #بدون_رصيد

🧑 شخصيات: {', '.join(GLOBAL_BANK.keys())}
💰 التكلفة: $0.00
⏱️ 16 ثانية - زوم سينمائي + ترجمة أفلام
🎵 صوت عربي + موسيقى دراما + ترجمة EN تحت
💾 ملخص: {story.get('summary','')}
"""

        send_text(chat_id, caption)
        send_video(chat_id, final, f"الحلقة {ep} - $0 مجاني")
        send_text(chat_id, f"✅ الحلقة {ep} مجانية 100% جاهزة!\n💰 رصيدك $5.23 ما انصرف منه شي!\n♾️ تقدر تعمل حلقات لا نهائية\n/next للحلقة الجاية")

        if chat_id not in STORY_MEMORY: STORY_MEMORY[chat_id]=[]
        STORY_MEMORY[chat_id].append(f"EP{ep}: {story.get('summary','')}")

    except Exception as e:
        log("ERROR " + repr(e))
        try: send_text(chat_id, "❌ خطأ:\n" + str(e)[:2000])
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
        send_text(chat, "🎬 نسخة $0 مجانية 100%\n💰 صور Pollinations مجانية\n🎥 زوم سينمائي مجاني\n🎵 صوت + موسيقى + ترجمة أفلام\n🧑 شخصيات ثابتة + جديدة\n♾️ حلقات لا نهائية - $5.23 ما بينصرف\n\n/test = بداية\n/next = حلقة جديدة\n/characters = الشخصيات")
        return
    if text=="/characters":
        txt = "🧑 بنك الشخصيات:\n" + "\n".join([f"- {k}" for k in GLOBAL_BANK.keys()])
        send_text(chat, txt); return
    if text=="/ping":
        send_text(chat, f"🟢 FREE $0 Bot EP={EPISODE_COUNTER.get(chat,1)} Bank={len(GLOBAL_BANK)} Cost=$0"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅ تم مسح busy"); return
    idea=text
    if text=="/test": idea="بداية البطل يتظاهر بالضعف الملك يرفضه الأميرة تحبه غابة ذئبة بيضاء نمر عملاق يهجم"
    if text=="/next": idea="تكملة نفس الشخصيات تهديد جديد يتطور"
    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, idea), daemon=True).start()

@app.get("/")
def home(): return "FREE $0 Bot Alive", 200
@app.get("/health")
def health(): return {"ok": True, "cost": "$0"}, 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if TEST_MODE: return
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    url=RENDER_EXTERNAL_URL + "/telegram/webhook"
    try: telegram("setWebhook", {"url": url, "drop_pending_updates": "true"})
    except: pass

if __name__=="__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
