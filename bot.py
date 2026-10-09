import os, json, time, shutil, tempfile, threading, subprocess, re, urllib.parse
from pathlib import Path
import requests
from flask import Flask, request

def envbool(k,d): return os.getenv(k, str(d)).lower() in ("1","true","yes","on")
BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")
TEST_MODE = envbool("TEST_MODE", False)

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except: groq = None

CHARACTER_BANK = {
    "hero": "Handsome real man age 25 short black hair sharp jaw blue eyes athletic dark blue cloak, Arcane photorealistic consistent face",
    "princess": "Beautiful real woman age 22 long black hair green eyes royal dress white gold",
    "king": "Old king age 60 white beard crown royal robe serious",
    "wolf": "Small white wolf pup cute fluffy blue eyes",
    "tiger": "GIANT TIGER enormous 4 meters orange black stripes"
}

# أصوات الشخصيات - كل واحد ستايل
CHARACTER_VOICE = {
    "hero": {"pitch": 0.95, "speed": 0.88, "desc": "رجل شاب عميق بطيء جذاب"},
    "princess": {"pitch": 1.25, "speed": 0.92, "desc": "فتاة رقيقة ناعمة"},
    "king": {"pitch": 0.82, "speed": 0.80, "desc": "رجل عجوز حكيم بطيء جدا"},
    "wolf": {"pitch": 1.35, "speed": 1.0, "desc": "صغير لطيف"},
    "tiger": {"pitch": 0.70, "speed": 0.75, "desc": "عملاق مخيف عميق"}
}

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
EPISODE_COUNTER = {}
STORY_MEMORY = {}
GLOBAL_BANK = CHARACTER_BANK.copy()

def log(m):
    with lock: print("[VOICE GOLD $0] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def get_arabic_font():
    font_dir = Path("/tmp/fonts")
    font_dir.mkdir(exist_ok=True)
    font_path = font_dir / "Amiri-Regular.ttf"
    if not font_path.exists():
        try:
            r = requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf", timeout=30)
            if r.status_code==200: font_path.write_bytes(r.content)
        except: pass
    for f in [str(font_path), "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if f and Path(f).exists(): return f
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ARABIC_FONT = get_arabic_font()

def generate_image_free(prompt, out):
    safe = urllib.parse.quote(prompt[:500] + " cinematic volumetric lighting god rays 8K vertical movie poster")
    url = f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model=flux&nologo=true&seed={int(time.time()*1000)%9999}"
    for _ in range(4):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code==200 and len(r.content)>12000:
                Path(out).write_bytes(r.content)
                return Path(out)
        except: time.sleep(2)
    raise RuntimeError("free image fail")

# صوت لكل شخصية - بطيء جذاب
def generate_voice_for_character(text_ar, character_key, out_path):
    try:
        from gtts import gTTS
        # gTTS بطيء جذاب
        tts = gTTS(text=text_ar, lang="ar", slow=True)
        tts.save(str(out_path))

        # تعديل الصوت حسب الشخصية - pitch + speed
        voice_cfg = CHARACTER_VOICE.get(character_key, {"pitch": 1.0, "speed": 0.88})
        pitch = voice_cfg["pitch"]
        speed = voice_cfg["speed"]

        # بطيء وجذاب + تغيير نبرة
        temp = out_path.parent / f"temp_{character_key}.mp3"
        # atempo <1 = أبطأ، asetrate لتغيير النبرة
        # نحول pitch عبر تغيير السرعة ثم إرجاع المدة
        filtered = out_path.parent / f"filtered_{character_key}.mp3"

        # سلسلة: تبطيء + تغيير نبرة
        cmd = [
            "ffmpeg","-y","-i",str(out_path),
            "-filter_complex",
            f"[0:a]atempo={speed},asetrate=44100*{pitch},aresample=44100,atempo={1/pitch}[a]",
            "-map","[a]",
            str(filtered)
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            shutil.move(filtered, out_path)
        except:
            # لو فشل الفلتر، خليه بطيء فقط
            pass

        if temp.exists(): temp.unlink(missing_ok=True)
        if filtered.exists(): filtered.unlink(missing_ok=True)

        log(f"Voice {character_key} pitch={pitch} speed={speed} OK")
        return out_path
    except Exception as e:
        log(f"Voice {character_key} fail {e}")
        return None

def generate_video_with_voice(image_path, scene, voice_path, out_path, is_first=False, story=None):
    def clean(t):
        t = (t or "").replace(":", " ").replace("'", "").replace('"',"").replace("%","").replace("\n"," ")[:80]
        return t
    dialogue_ar = clean(scene.get("dialogue_ar",""))
    dialogue_en = clean(scene.get("dialogue_en",""))
    caption_big = clean(scene.get("caption_big",""))
    hook = clean(story.get("hook_text","")) if is_first and story else ""
    ep = story.get("episode",1) if story else 1
    char_key = scene.get("character_key","hero")
    font = ARABIC_FONT

    # فيديو + صوت الشخصية بمكانها - الترجمة تحت
    if is_first:
        vf = f"zoompan=z='min(zoom+0.0008,1.25)':d=1:fps=24:s=720x1280,scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.15:brightness=0.03:saturation=1.3,unsharp=5:5:0.8,drawtext=fontfile={font}:text='EP {ep} - {char_key}':fontcolor=white:fontsize=18:borderw=2:bordercolor=black:x=15:y=15,drawtext=fontfile={font}:text='{hook}':fontcolor=yellow:fontsize=28:borderw=3:bordercolor=black:x=(w-text_w)/2:y=55,drawtext=fontfile={font}:text='{caption_big}':fontcolor=white:fontsize=40:borderw=5:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-40,drawtext=fontfile={font}:text='{dialogue_ar}':fontcolor=white:fontsize=26:borderw=3:bordercolor=black:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=h-135,drawtext=fontfile={font}:text='{dialogue_en}':fontcolor=#CCCCCC:fontsize=15:borderw=1:bordercolor=black:x=(w-text_w)/2:y=h-65"
    else:
        vf = f"zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.0006,1.22))':d=1:fps=24:s=720x1280,scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.12:saturation=1.25,drawtext=fontfile={font}:text='{char_key}':fontcolor=#AAAAAA:fontsize=16:borderw=1:bordercolor=black:x=15:y=15,drawtext=fontfile={font}:text='{caption_big}':fontcolor=white:fontsize=38:borderw=4:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-50,drawtext=fontfile={font}:text='{dialogue_ar}':fontcolor=white:fontsize=26:borderw=3:bordercolor=black:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=h-135,drawtext=fontfile={font}:text='{dialogue_en}':fontcolor=#CCCCCC:fontsize=15:borderw=1:bordercolor=black:x=(w-text_w)/2:y=h-65"

    # دمج صورة + صوت الشخصية - مدة الفيديو = مدة الصوت + 1 ثانية
    if voice_path and Path(voice_path).exists():
        # خد مدة الصوت
        try:
            probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=10)
            dur = float(probe.stdout.strip() or "3")
            dur = max(3.5, min(dur+1.2, 8)) # بين 3.5 و 8 ثواني
        except: dur = 6

        cmd = ["ffmpeg","-y","-loop","1","-i",str(image_path),"-i",str(voice_path),"-vf",vf,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
    else:
        cmd = ["ffmpeg","-y","-loop","1","-i",str(image_path),"-vf",vf,"-t","6","-r","24","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]

    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)
    return out_path

def generate_free_music(out_path, duration=65):
    try:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo","-f","lavfi","-i",f"sine=frequency=110:duration={duration},sine=frequency=138:duration={duration}", "-filter_complex", "[1:a]volume=0.03,lowpass=f=500,atempo=0.85[a1];[0:a][a1]amix=inputs=2[a]", "-map","[a]","-t",str(duration),"-c:a","aac",str(out_path)], check=True, timeout=30)
        return out_path
    except: return None

def create_story_from_user_text(user_story_text, episode=1, chat_id=0):
    current_chars = ", ".join(GLOBAL_BANK.keys())
    voice_desc = "\n".join([f"{k}: {v['desc']}" for k,v in CHARACTER_VOICE.items()])
    system_prompt = f"""
You are director for 60 sec video with voice per character. User story: "{user_story_text}"
Characters: {current_chars}
Voices:
{voice_desc}

Split user story into exactly 10 scenes (6 sec each = 60 sec).
Each scene must have character_key that SPEAKS in that scene.
Return JSON:
{{
 "title": "عنوان",
 "hook_text": "Hook",
 "hashtags": "#قصة #الحلقة{episode}",
 "summary": "ملخص",
 "new_characters": [{{"key":"wizard","description":"..."}}],
 "scenes": [10 scenes each {{"character_key":"hero","scene_image_prompt":"hero action cinematic","dialogue_ar":"جملة بطيئة جذابة","dialogue_en":"English","caption_big":"كلمتين"}}]
}}
User story must be told: {user_story_text}
Each dialogue_ar should be slow attractive style.
"""
    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.6, max_completion_tokens=7000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":f"قصة المستخدم: {user_story_text} قسمها 10 مشاهد كل مشهد صوت شخصية مختلفة بطيء جذاب"}])
            story=json.loads(res.choices[0].message.content.strip())
            if len(story.get("scenes",[]))>=8:
                while len(story["scenes"])<10: story["scenes"].append(story["scenes"][-1])
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

def final_mix_with_music(video_path, music_path, out_path):
    try:
        if music_path and Path(music_path).exists():
            # موسيقى خلفية خفيفة جدا مع أصوات الشخصيات الأصلية
            run_cmd(["ffmpeg","-y","-i",str(video_path),"-i",str(music_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.10[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-shortest",str(out_path)],60)
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
        return telegram("sendVideo", {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, {"video": ("episode.mp4", f, "video/mp4")}, 600)

def process_story(chat_id, user_story_text):
    work=Path(tempfile.mkdtemp(prefix="gold60_"))
    try:
        if chat_id not in EPISODE_COUNTER: EPISODE_COUNTER[chat_id]=1
        else: EPISODE_COUNTER[chat_id]+=1
        ep = EPISODE_COUNTER[chat_id]

        send_text(chat_id, f"🎬 دقيقة بأصوات الشخصيات - الحلقة {ep}\n📝 قصتك: {user_story_text[:90]}...\n🎙️ كل شخصية صوتها بمكانها بطيء جذاب\n⏳ 10 مشاهد - بياخد 3 دقايق")

        story=create_story_from_user_text(user_story_text, episode=ep, chat_id=chat_id)

        for nc in story.get("new_characters", []):
            k=nc.get("key"); d=nc.get("description")
            if k and d and k not in GLOBAL_BANK:
                GLOBAL_BANK[k]=d
                # صوت افتراضي للشخصية الجديدة
                if k not in CHARACTER_VOICE:
                    CHARACTER_VOICE[k] = {"pitch": 1.0, "speed": 0.88, "desc": "صوت جديد"}

        videos=[]
        for index, scene in enumerate(story["scenes"][:10]):
            no=index+1
            char_key = scene.get("character_key","hero")
            char_desc = GLOBAL_BANK.get(char_key, CHARACTER_BANK["hero"])
            img=work / f"ep{ep}_scene_{no}.jpg"
            voice=work / f"ep{ep}_scene_{no}_voice.mp3"
            vid=work / f"ep{ep}_scene_{no}.mp4"

            img_prompt = f"{char_desc}, {scene.get('scene_image_prompt','')}, consistent face"
            generate_image_free(img_prompt, img)

            # صوت الشخصية بمكانها - بطيء جذاب
            dialogue = scene.get("dialogue_ar","")
            generate_voice_for_character(dialogue, char_key, voice)

            # فيديو + صوت الشخصية مدمج بمكانه
            generate_video_with_voice(img, scene, voice if voice.exists() else None, vid, is_first=(no==1), story={"hook_text": story.get("hook_text",""), "episode": ep})

            videos.append(vid)
            send_text(chat_id, f"✅ مشهد {no}/10 - {char_key} 🎙️ صوته {CHARACTER_VOICE.get(char_key,{}).get('desc','')}")

        # موسيقى خلفية خفيفة للكل
        music_path = work / "music.mp3"
        generate_free_music(music_path, duration=65)

        final_raw=work / "final_raw.mp4"
        concat_videos(videos, final_raw)

        final=work / "final.mp4"
        final_mix_with_music(final_raw, music_path if music_path.exists() else None, final)

        caption = f"""🎬 {story.get('title','')} - {ep} [60ث بأصوات]

{story.get('hook_text','')}

🎙️ كل شخصية صوتها الخاص:
{chr(10).join([f"• {k}: {v['desc']}" for k,v in CHARACTER_VOICE.items() if k in [s.get('character_key') for s in story['scenes']]])}

{story.get('hashtags','')} #أصوات_شخصيات

⏱️ 60 ثانية - 10 مشاهد كل مشهد صوته بمكانه
💰 $0 مجاني
🎵 بطيء وجذاب + موسيقى خلفية خفيفة
📝 قصتك: {user_story_text[:120]}
"""

        send_text(chat_id, caption)
        send_video(chat_id, final, f"الحلقة {ep} - 60ث بأصوات")
        send_text(chat_id, f"✅ دقيقة كاملة بأصوات الشخصيات جاهزة!\n🎙️ كل شخصية تكلمت بصوتها بمكانها بالفيديو\n🐢 بطيء وجذاب\n🔄 ابعت قصة جديدة")

        if chat_id not in STORY_MEMORY: STORY_MEMORY[chat_id]=[]
        STORY_MEMORY[chat_id].append(f"EP{ep}: {story.get('summary','')}")

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
        send_text(chat, "🎬 بوت دقيقة بأصوات الشخصيات $0\n\n📝 ابعت قصتك كتابة وهو يحولها:\n\nمثال:\nالبطل يدخل القصر الملك يطرده الأميرة تدافع عنه يخرج للغابة ذئبة بيضاء تنقذه نمر عملاق يهجم يكشف قوته\n\n🎙️ كل شخصية صوتها بمكانها بطيء جذاب\n📍 الترجمة تحت خالص + خط عربي\n\n/test = مثال\n/next = تكملة"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅"); return
    if text=="/characters":
        txt = "🧑 الشخصيات وأصواتها:\n" + "\n".join([f"- {k}: {v['desc']}" for k,v in CHARACTER_VOICE.items()])
        send_text(chat, txt); return

    user_story = text
    if text=="/test":
        user_story = "شاب فقير يتظاهر بالضعف يدخل قصر الملك الملك يهينه ويطرده الأميرة تحبه وتدافع عنه يخرج حزين للغابة يلتقي ذئبة بيضاء صغيرة تنقذه من البرد نمر عملاق يهجم على الذئبة الشاب يكشف قوته الحقيقية ويهزم النمر بضربة واحدة الأميرة ترى قوته وتفرح"
    elif text=="/next":
        user_story = "تكملة الحلقة السابقة نفس الشخصيات يواجهون تهديد جديد أكبر في الغابة الملك يحاول الانتقام"

    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج دقيقة بأصوات - انتظر"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, user_story), daemon=True).start()

@app.get("/")
def home(): return f"Gold Voice 60s Bot Font={ARABIC_FONT} Alive", 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if TEST_MODE: return
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    url=RENDER_EXTERNAL_URL + "/telegram/webhook"
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", data={"url": url, "drop_pending_updates": "true"}, timeout=10)
    except: pass

if __name__=="__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
