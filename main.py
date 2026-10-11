import os
import re
import sys
import json
import time
import uuid
import asyncio
import logging
import threading
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
import requests
from flask import Flask, jsonify, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip() or os.getenv("PIXABAY_API_KEY", "").strip()

FREE_MODE = True
ALLOW_PAID_APIS = False
PORT = int(os.getenv("PORT", "10000"))
SCENE_COUNT = 18
MAX_EPISODES = 20
HMISC_VIDEO_DURATION = 8.0
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
BASE_SCENE_DURATION = 5.0
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 4000
MAX_VIDEO_SIZE = 49 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 20 * 1024 * 1024
GROQ_RETRIES = 4
GROQ_MAX_TOKENS = 5000
GROQ_MAX_WAIT = 60
IMAGE_GENERATION_TIMEOUT = 180
TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural").strip()
TTS_VOICE_EN = os.getenv("TTS_VOICE_EN", "en-US-GuyNeural").strip()
TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()
SUBTITLES_ENABLED = True
SCENE_TRANSITION_SECONDS = 0.25
VOICE_VOLUME = 1.0
SFX_VOLUME = 0.09
MUSIC_VOLUME = 0.16

DATA_ROOT = Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
BASE_DIR = DATA_ROOT / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = BASE_DIR / "jobs_state.json"
STORY_PROGRESS_FILE = BASE_DIR / "story_progress.json"
CHARACTER_MEMORY_FILE = BASE_DIR / "characters_memory.json"
THUMBNAIL_DIRNAME = "thumbnails"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(BASE_DIR / "zil.log", encoding="utf-8")])
log = logging.getLogger("ZIL")

app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-V81-HMISC/9.0"})

JOBS = {}
JOBS_LOCK = threading.RLock()
STATE_LOCK = threading.RLock()
STORY_LOCK = threading.RLock()
ACTIVE_JOBS = 0

EFFECTS = {"mystery", "castle", "steps", "romance", "tension", "storm", "beast", "fight", "impact", "magic", "cliffhanger"}
HMISC_EMOTION_MUSIC = {
    "anxiety": {"freq1": 55, "freq2": 110, "volume": 0.14},
    "hope": {"freq1": 196, "freq2": 247, "volume": 0.16},
    "loneliness": {"freq1": 82, "freq2": 165, "volume": 0.13},
    "comparison": {"freq1": 65, "freq2": 130, "volume": 0.15},
    "patience": {"freq1": 72, "freq2": 144, "volume": 0.15},
}

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except:
        return default if default is not None else {}

def load_state():
    global JOBS
    with STATE_LOCK:
        loaded = read_json(STATE_FILE, {})
        if not isinstance(loaded, dict):
            loaded = {}
        JOBS = loaded
        for job in JOBS.values():
            if job.get("status") in ("queued", "running", "uploading"):
                job["status"] = "interrupted"
                job["stage"] = "interrupted"
                job["message"] = "انقطع التنفيذ بسبب إعادة تشغيل الخدمة"
        atomic_json(STATE_FILE, JOBS)

def save_state_locked():
    atomic_json(STATE_FILE, JOBS)

def update_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()
        with STATE_LOCK:
            save_state_locked()

def load_story_progress():
    data = read_json(STORY_PROGRESS_FILE, {})
    return data if isinstance(data, dict) else {}

def load_character_memory():
    data = read_json(CHARACTER_MEMORY_FILE, {})
    return data if isinstance(data, dict) else {}

def get_user_story(user_id):
    progress = load_story_progress()
    return progress.get(str(user_id))

def update_story_after_episode(user_id, episode, package):
    key = str(user_id)
    with STORY_LOCK:
        progress = load_story_progress()
        memory = load_character_memory()
        previous = progress.get(key, {})
        old_story = previous.get("full_story_so_far", "")
        episode_story = str(package.get("story", ""))
        if episode_story not in old_story:
            combined = (old_story + "\n\n" + episode_story).strip()
        else:
            combined = old_story
        combined = combined[-MAX_STORY_LENGTH:]
        progress[key] = {"episode": episode, "series_title": package.get("series_title", "ظل"), "full_story_so_far": combined, "last_episode_text": episode_story[-1200:], "last_cliffhanger": str(package.get("cliffhanger", ""))[:1000], "updated_at": time.time()}
        old_memory = memory.get(key, {})
        character_memory = package.get("character_memory", old_memory.get("characters", {}))
        memory[key] = {"characters": character_memory, "updated_at": time.time()}
        atomic_json(STORY_PROGRESS_FILE, progress)
        atomic_json(CHARACTER_MEMORY_FILE, memory)

def run_command(command, timeout=300):
    result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode!= 0:
        error = (result.stderr or result.stdout or "")[-4000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {error}")
    return result

def probe_duration(path):
    result = run_command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 60)
    try:
        return float(result.stdout.strip())
    except:
        return 0.0

def safe_duration(path, fallback=BASE_SCENE_DURATION):
    duration = probe_duration(path)
    return duration if duration > 0 else fallback

def verify_dependencies():
    missing = [name for name in ("ffmpeg", "ffprobe") if not __import__("shutil").which(name)]
    if missing:
        raise RuntimeError("Missing system dependencies: " + ", ".join(missing))

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN:
        return
    HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": str(text)[:4000]}, timeout=30)

def telegram_send_video(chat_id, path, caption):
    with open(path, "rb") as media:
        HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id": chat_id, "caption": caption[:1000]}, files={"video": (Path(path).name, media, "video/mp4")}, timeout=(30, 600))

def telegram_send_photo(chat_id, path, caption=""):
    with open(path, "rb") as media:
        HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data={"chat_id": chat_id, "caption": caption[:1000]}, files={"photo": (Path(path).name, media, "image/jpeg")}, timeout=(20, 120))

def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise RuntimeError("Groq did not return valid JSON")

def groq_chat(messages):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing")
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.8, "max_tokens": GROQ_MAX_TOKENS, "response_format": {"type": "json_object"}}
    for attempt in range(GROQ_RETRIES):
        try:
            response = HTTP.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
            if response.status_code == 429:
                time.sleep(min(GROQ_MAX_WAIT, 5 * (attempt + 1)))
                continue
            response.raise_for_status()
            data = response.json()
            return extract_json(data["choices"][0]["message"]["content"])
        except Exception:
            time.sleep(min(10, 2 ** attempt))
    raise RuntimeError("Groq failed")

def normalize_package(package, episode, idea):
    scenes = package.get("scenes")
    if len(scenes)!= SCENE_COUNT:
        raise RuntimeError(f"Expected {SCENE_COUNT} scenes")
    normalized = []
    for scene in scenes:
        effect = str(scene.get("effect", "mystery")).lower()
        if effect not in EFFECTS:
            effect = "mystery"
        normalized.append({"narration": str(scene.get("narration", ""))[:1200], "action": str(scene.get("action", ""))[:1500], "effect": effect})
    package["scenes"] = normalized
    package["episode"] = episode
    package["title"] = str(package.get("title", f"الحلقة {episode}"))[:200]
    package["series_title"] = str(package.get("series_title", "ظل"))[:100]
    package["story"] = str(package.get("story", idea))[:6000]
    package["cliffhanger"] = str(package.get("cliffhanger", ""))[:1000] or normalized[-1]["narration"]
    return package

def create_story_package(idea, user_id, episode, previous_story=None):
    memory_all = load_character_memory()
    memory = memory_all.get(str(user_id), {}).get("characters", {})
    previous_story = previous_story or {}
    system_prompt = f"You are ZIL writer. Return JSON. Episode {episode} with EXACTLY {SCENE_COUNT} scenes. Effects {sorted(EFFECTS)}."
    user_prompt = f"IDEA: {idea} PREV: {previous_story.get('last_cliffhanger','')} MEMORY: {json.dumps(memory, ensure_ascii=False)}"
    package = groq_chat([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
    return normalize_package(package, episode, idea)

def generate_hmisc_series_ideas(count=10, theme="رسائل نفسية"):
    system_prompt = "You are hmisc writer. Return JSON ideas: ar1, ar2 short Arabic, en1, en2 English, bg_prompt parchment illustration split view, bg_keyword, emotion anxiety/hope/loneliness/comparison/patience, narration_ar, narration_en"
    user_prompt = f"Create {count} ideas theme {theme} JSON {{ideas:[...]}}"
    try:
        payload = groq_chat([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
        ideas = payload.get("ideas", payload if isinstance(payload, list) else [])
        if len(ideas) >= 1:
            return ideas[:count]
    except:
        pass
    fallback = [
        {"ar1": "لا تصنع هموماً", "en1": "Don't create worries", "ar2": "لم يكتبها الله عليك", "en2": "God didn't write for you", "bg_prompt": "yellow character drowning in shallow water illusion split view top drowning bottom sitting sand parchment", "bg_keyword": "shallow water", "emotion": "anxiety", "narration_ar": "أحياناً نغرق في هموم صنعناها والحقيقة أنها ماء يصل لركبتينا", "narration_en": "Sometimes we drown in worries we made but it's only water up to knees"},
        {"ar1": "لا تنظر لما في أيدي الناس", "en1": "Don't look at others have", "ar2": "فما كتبه الله لك أعظم", "en2": "What God wrote for you is greater", "bg_prompt": "two carrots one small leaves big root underground parchment", "bg_keyword": "carrot field", "emotion": "comparison", "narration_ar": "لا تنظر لما عند الناس فما عندك قد يكون أعظم", "narration_en": "Don't look at others, what you have might be greater"},
        {"ar1": "لست وحدك", "en1": "You are not alone", "ar2": "فقط لم ترى من يحبك", "en2": "You just didn't see who loves you", "bg_prompt": "lone yellow character dark forest fireflies heart parchment", "bg_keyword": "forest fireflies", "emotion": "loneliness", "narration_ar": "تظن أنك وحيد والحقيقة قلوب تحيط بك", "narration_en": "You think alone but hearts surround you"},
        {"ar1": "طريقك ليس متأخراً", "en1": "Your path isn't late", "ar2": "بل مختلفاً", "en2": "It's just different", "bg_prompt": "crowded european street vs foggy mountain lone walker parchment", "bg_keyword": "foggy mountain", "emotion": "patience", "narration_ar": "طريقك ليس متأخراً بل مختلفاً", "narration_en": "Your path isn't late it's different"},
        {"ar1": "كل هذا سيمر", "en1": "All this will pass", "ar2": "كما مر غيره", "en2": "Like everything before", "bg_prompt": "ocean waves storm top calm bottom parchment", "bg_keyword": "ocean waves", "emotion": "hope", "narration_ar": "كل حزن يمر وكل ضيق يزول", "narration_en": "Every sadness passes"},
    ]
    return fallback[:count]

def build_hmisc_image_prompt(idea):
    base = "Soft hand-drawn illustration old parchment #F5E6C8 yellow character #FFC83D brown ink split-screen top illusion bottom reality emotional psychological vertical 9:16 no text"
    return f"{idea.get('bg_prompt','person alone')}, {base}"

def build_scene_image_prompt(scene, package, index):
    memory = package.get("character_memory", {})
    return f"Photorealistic fantasy vertical 9:16 cinematic {scene['action']} continuity {json.dumps(memory, ensure_ascii=False)}"

def pollinations_generate_image(prompt, destination, seed):
    url = POLLINATIONS_IMAGE_URL + quote(prompt, safe="")
    params = {"width": 768, "height": 1365, "seed": int(seed), "nologo": "true", "safe": "true", "model": "flux"}
    response = HTTP.get(url, params=params, timeout=(20, IMAGE_GENERATION_TIMEOUT))
    response.raise_for_status()
    Path(destination).write_bytes(response.content)
    return destination

def pixabay_generate_image(prompt, destination, index):
    key = PIXABAY_API_KEY
    if not key:
        raise RuntimeError("Pixabay key missing")
    words = re.findall(r"[A-Za-z]{3,}", prompt.lower())[:7]
    query = " ".join(words) or "fantasy"
    response = HTTP.get("https://pixabay.com/api/", params={"key": key, "q": query, "image_type": "photo", "orientation": "vertical", "safesearch": "true", "per_page": 10}, timeout=30)
    response.raise_for_status()
    hits = response.json().get("hits", [])
    if not hits:
        raise RuntimeError("No Pixabay images")
    image_url = hits[0].get("largeImageURL")
    with requests.get(image_url, stream=True, timeout=20) as r:
        Path(destination).write_bytes(r.content)
    return destination

def generate_scene_image(prompt, destination, index):
    for attempt in range(2):
        try:
            return pollinations_generate_image(prompt, destination, seed=7600+index*101+attempt)
        except:
            time.sleep(1)
    return pixabay_generate_image(prompt, destination, index)

def animate_still_image(source, destination, duration, index):
    frames = max(1, int(float(duration) * VIDEO_FPS))
    vf = f"scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800,zoompan=z='min(zoom+0.001,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(source), "-vf", vf, "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 300)
    return destination

def animate_hmisc_image(source, destination, duration=8.0):
    frames = max(1, int(float(duration) * VIDEO_FPS))
    vf = f"scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800,zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.0008,1.18))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=720x1280:fps=24,trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(source), "-vf", vf, "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 300)
    return destination

def obtain_scene_visual(scene, package, workdir, index, duration):
    raw_image = workdir / f"ai_scene_{index+1}.jpg"
    normalized_image = workdir / f"ai_scene_{index+1}.png"
    normalized_video = workdir / f"visual_{index+1}.mp4"
    if not normalized_image.exists():
        if not raw_image.exists():
            generate_scene_image(build_scene_image_prompt(scene, package, index), raw_image, index)
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_image), "-frames:v", "1", "-vf", "scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800", str(normalized_image)], 120)
    if not normalized_video.exists():
        animate_still_image(normalized_image, normalized_video, duration, index)
    return normalized_video, normalized_image

def obtain_hmisc_visual(idea, workdir, index, duration):
    raw_image = workdir / f"hmisc_raw_{index}.jpg"
    normalized_image = workdir / f"hmisc_{index}.png"
    normalized_video = workdir / f"hmisc_visual_{index}.mp4"
    if not normalized_image.exists():
        if not raw_image.exists():
            generate_scene_image(build_hmisc_image_prompt(idea), raw_image, index+100)
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_image), "-frames:v", "1", "-vf", "scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800", str(normalized_image)], 120)
    if not normalized_video.exists():
        animate_hmisc_image(normalized_image, normalized_video, duration)
    return normalized_video, normalized_image

def add_arabic_caption(source, caption, destination, workdir, index, duration):
    return add_bilingual_nice_caption(source, str(caption), "", "", "", destination, workdir, index, duration)

def add_bilingual_nice_caption(source, ar1, en1, ar2, en2, destination, workdir, index, duration):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
        overlay_h = 520
        overlay = Image.new("RGBA", (VIDEO_WIDTH, overlay_h), (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle([(20,0),(VIDEO_WIDTH-20, overlay_h)], radius=18, fill=(245,230,200,170))
        def find_font(cands):
            return next((p for p in cands if Path(p).exists()), None)
        ar_bold = find_font(["/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"])
        ar_reg = find_font(["/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
        en_bold = find_font(["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"])
        en_reg = find_font(["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
        font_ar1 = ImageFont.truetype(ar_bold,44) if ar_bold else ImageFont.load_default()
        font_en1 = ImageFont.truetype(en_bold,26) if en_bold else ImageFont.load_default()
        font_ar2 = ImageFont.truetype(ar_reg,34) if ar_reg else ImageFont.load_default()
        font_en2 = ImageFont.truetype(en_reg,22) if en_reg else ImageFont.load_default()
        def reshaped(t):
            if not t:
                return ""
            if any('\u0600' <= c <= '\u06FF' for c in t):
                try:
                    return get_display(arabic_reshaper.reshape(t))
                except:
                    return t
            return t
        y=28
        if ar1:
            txt=reshaped(ar1)
            bbox=draw.textbbox((0,0),txt,font=font_ar1)
            draw.text(((VIDEO_WIDTH-(bbox[2]-bbox[0]))//2,y),txt,font=font_ar1,fill=(44,30,19,255))
            y+=bbox[3]-bbox[1]+6
        if en1:
            txt=reshaped(en1)
            bbox=draw.textbbox((0,0),txt,font=font_en1)
            draw.text(((VIDEO_WIDTH-(bbox[2]-bbox[0]))//2,y),txt,font=font_en1,fill=(107,90,70,255))
            y+=bbox[3]-bbox[1]+18
        if ar2:
            txt=reshaped(ar2)
            bbox=draw.textbbox((0,0),txt,font=font_ar2)
            draw.text(((VIDEO_WIDTH-(bbox[2]-bbox[0]))//2,y),txt,font=font_ar2,fill=(44,30,19,255))
            y+=bbox[3]-bbox[1]+6
        if en2:
            txt=reshaped(en2)
            bbox=draw.textbbox((0,0),txt,font=font_en2)
            draw.text(((VIDEO_WIDTH-(bbox[2]-bbox[0]))//2,y),txt,font=font_en2,fill=(107,90,70,255))
            y+=bbox[3]-bbox[1]+10
        cta_font = ImageFont.truetype(en_reg,20) if en_reg else ImageFont.load_default()
        cta = reshaped("شاركه مع من يحتاجه 🤍 Share")
        bbox=draw.textbbox((0,0),cta,font=cta_font)
        draw.text(((VIDEO_WIDTH-(bbox[2]-bbox[0]))//2,overlay_h-36),cta,font=cta_font,fill=(90,70,50,220))
        png = workdir / f"caption_bilingual_{index+1}.png"
        overlay.save(png)
        run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-loop","1","-framerate",str(VIDEO_FPS),"-i",str(png),"-filter_complex",f"[0:v][1:v]overlay=0:(H-h)/2:format=auto[v]","-map","[v]","-t",f"{duration:.3f}","-an","-c:v","libx264","-preset","ultrafast","-crf","24","-pix_fmt","yuv420p","-r",str(VIDEO_FPS),str(destination)],240)
        if destination.exists():
            return destination
    except Exception:
        log.exception("caption failed")
    return source

def create_narration(text, destination):
    if destination.exists() and destination.stat().st_size>1000:
        return destination
    destination.unlink(missing_ok=True)
    try:
        import edge_tts
        async def gen():
            c=edge_tts.Communicate(text,voice=TTS_VOICE,rate=TTS_RATE)
            await c.save(str(destination))
        asyncio.run(gen())
        if destination.exists():
            return destination
    except:
        pass
    from gtts import gTTS
    gTTS(text=text,lang="ar",slow=False).save(str(destination))
    return destination

def create_bilingual_narration(ar_text, en_text, destination, workdir, idx):
    ar_file = workdir / f"nar_ar_{idx}.mp3"
    en_file = workdir / f"nar_en_{idx}.mp3"
    merged = workdir / f"nar_merged_{idx}.mp3"
    try:
        import edge_tts
        async def gen_ar():
            c=edge_tts.Communicate(ar_text,voice=TTS_VOICE,rate=TTS_RATE)
            await c.save(str(ar_file))
        async def gen_en():
            c=edge_tts.Communicate(en_text,voice=TTS_VOICE_EN,rate=TTS_RATE)
            await c.save(str(en_file))
        if ar_text:
            asyncio.run(gen_ar())
        if en_text:
            asyncio.run(gen_en())
    except:
        if ar_text:
            create_narration(ar_text, ar_file)
    if not ar_file.exists() and en_file.exists():
        return en_file
    if ar_file.exists() and not en_file.exists():
        return ar_file
    list_file = workdir / f"nar_list_{idx}.txt"
    with open(list_file,"w",encoding="utf-8") as f:
        f.write(f"file '{ar_file.resolve().as_posix()}'\nfile '{en_file.resolve().as_posix()}'\n")
    try:
        run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(list_file),"-c","copy",str(merged)],60)
        if merged.exists():
            return merged
    except:
        pass
    return ar_file

def prepare_synced_voice(source, destination):
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-af","aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS","-ar","44100","-ac","2","-c:a","aac","-b:a","96k",str(destination)],150)
    return probe_duration(destination)

def fit_audio_to_duration(source, destination, duration):
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(source),"-af",f"aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS","-ar","44100","-ac","2","-c:a","aac","-b:a","96k",str(destination)],150)
    return destination

def make_scene_sfx(kind, destination, duration):
    source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration:.3f}"
    af = "lowpass=f=700,volume=0.10"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",source,"-af",f"{af},afade=t=in:d=0.25,afade=t=out:st={max(0.1,duration-0.5):.3f}:d=0.45,aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo","-t",f"{duration:.3f}","-ar","44100","-ac","2","-c:a","aac","-b:a","48k",str(destination)],150)
    return destination

def prepare_background_music(destination, duration, workdir, package=None):
    emotion = package.get("emotion","anxiety") if package else "anxiety"
    cfg = HMISC_EMOTION_MUSIC.get(emotion, HMISC_EMOTION_MUSIC["anxiety"])
    f1=cfg["freq1"]; f2=cfg["freq2"]
    filt = f"[0:a]volume=0.45,lowpass=f=180[a0];[1:a]volume=0.30,lowpass=f=650[a1];[2:a]volume=0.15,highpass=f=180,lowpass=f=6500[a2];[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,alimiter=limit=0.85,afade=t=in:d=2,afade=t=out:st={max(0.1,duration-4):.3f}:d=4,atrim=duration={duration:.3f}[out]"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"sine=frequency={f1}:sample_rate=44100:duration={duration}","-f","lavfi","-i",f"sine=frequency={f2}:sample_rate=44100:duration={duration}","-f","lavfi","-i",f"anoisesrc=color=pink:sample_rate=44100:duration={duration}","-filter_complex",filt,"-map","[out]","-ar","44100","-ac","2","-c:a","aac","-b:a","80k",str(destination)],300)
    return destination

def prepare_hmisc_music(destination, duration, emotion="anxiety"):
    cfg = HMISC_EMOTION_MUSIC.get(emotion, HMISC_EMOTION_MUSIC["anxiety"])
    f1=cfg["freq1"]; f2=cfg["freq2"]; vol=cfg["volume"]
    filt = f"[0:a]volume={vol},lowpass=f=900,tremolo=f=0.18:d=0.35,afade=t=in:d=1.5,afade=t=out:st={max(0.1,duration-2.5):.3f}:d=2.5[a0];[1:a]volume={vol*0.6},lowpass=f=600[a1];[a0][a1]amix=inputs=2:duration=longest:normalize=0,atrim=duration={duration:.3f}[out]"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"sine=frequency={f1}:sample_rate=44100:duration={duration}","-f","lavfi","-i",f"sine=frequency={f2}:sample_rate=44100:duration={duration}","-filter_complex",filt,"-map","[out]","-ar","44100","-ac","2","-c:a","aac","-b:a","64k",str(destination)],200)
    return destination

def write_concat_list(paths, destination):
    with open(destination,"w",encoding="utf-8") as o:
        for p in paths:
            o.write(f"file '{Path(p).resolve().as_posix()}'\n")

def concatenate_video(paths, destination, workdir):
    listing = workdir / "video_list.txt"
    write_concat_list(paths, listing)
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(listing),"-an","-c:v","libx264","-preset","ultrafast","-crf","27","-pix_fmt","yuv420p","-r",str(VIDEO_FPS),"-movflags","+faststart",str(destination)],1200)
    return destination

def concatenate_audio(paths, destination, workdir, label):
    listing = workdir / f"{label}_list.txt"
    write_concat_list(paths, listing)
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(listing),"-vn","-ar","44100","-ac","2","-c:a","aac","-b:a","96k",str(destination)],900)
    return destination

def mix_final_audio(video, voice, sfx, music, destination):
    duration = probe_duration(video)
    graph = f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume={VOICE_VOLUME},apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,asplit=2[voice_sc][voice_mix];[2:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume={SFX_VOLUME},apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[sfx_mix];[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume={MUSIC_VOLUME},apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[music_raw];[music_raw][voice_sc]sidechaincompress=threshold=0.025:ratio=5:attack=25:release=300[music_ducked];[voice_mix][sfx_mix][music_ducked]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,alimiter=limit=0.92,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[aout]"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(video),"-i",str(voice),"-i",str(sfx),"-i",str(music),"-filter_complex",graph,"-map","0:v:0","-map","[aout]","-t",f"{duration:.3f}","-c:v","copy","-c:a","aac","-b:a","128k","-movflags","+faststart",str(destination)],900)
    return destination

def mix_hmisc_audio(video, voice, music, destination):
    duration = probe_duration(video)
    graph = f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume={VOICE_VOLUME},apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,asplit=2[voice_sc][voice_mix];[2:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume=0.18,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[music_raw];[music_raw][voice_sc]sidechaincompress=threshold=0.03:ratio=4:attack=30:release=250[music_ducked];[voice_mix][music_ducked]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,alimiter=limit=0.90,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[aout]"
    run_command(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(video),"-i",str(voice),"-i",str(music),"-filter_complex",graph,"-map","0:v:0","-map","[aout]","-t",f"{duration:.3f}","-c:v","copy","-c:a","aac","-b:a","96k","-movflags","+faststart",str(destination)],600)
    return destination

def create_thumbnails(workdir):
    try:
        from PIL import Image
        thumb_dir = workdir / THUMBNAIL_DIRNAME
        thumb_dir.mkdir(parents=True, exist_ok=True)
        created=[]
        for num, name in [(1,"hero.jpg"),(9,"transformation.jpg"),(18,"cliffhanger.jpg")]:
            src = workdir / f"ai_scene_{num}.png"
            if not src.exists():
                src = workdir / f"ai_scene_{num}.jpg"
            if not src.exists():
                continue
            with Image.open(src) as im:
                im=im.convert("RGB")
                im.thumbnail((1280,1280))
                dst=thumb_dir/name
                im.save(dst,"JPEG",quality=93)
                created.append(dst)
        return created
    except:
        return []

def create_video(job_id, idea, user_id, episode):
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    def progress(stage, percent, message):
        update_job(job_id, stage=stage, progress=percent, message=message)
        try:
            chat_id = JOBS.get(job_id, {}).get("chat_id")
            if chat_id:
                telegram_send_message(chat_id, f"{percent}% — {message}")
        except:
            pass
    package_file = workdir / "story_package.json"
    package = read_json(package_file, None) if package_file.exists() else None
    if not package or package.get("episode")!=episode:
        progress("story_generation",2,f"كتابة الحلقة {episode}")
        package = create_story_package(idea, user_id, episode, get_user_story(user_id))
        atomic_json(package_file, package)
    scenes=package["scenes"]
    video_paths=[]; voice_paths=[]; sfx_paths=[]; durations=[]
    for idx, scene in enumerate(scenes):
        progress("scene_generation", int(5+idx/len(scenes)*65), f"الحلقة {episode} مشهد {idx+1}")
        raw_voice = workdir / f"voice_raw_{idx+1}.mp3"
        conv_voice = workdir / f"voice_converted_{idx+1}.m4a"
        aligned = workdir / f"voice_{idx+1}.m4a"
        if not aligned.exists():
            if not raw_voice.exists():
                create_narration(scene["narration"], raw_voice)
            prepare_synced_voice(raw_voice, conv_voice)
            dur = safe_duration(conv_voice, BASE_SCENE_DURATION)
            dur = max(BASE_SCENE_DURATION, dur+0.2)
            fit_audio_to_duration(conv_voice, aligned, dur)
        else:
            dur=safe_duration(aligned,BASE_SCENE_DURATION)
        durations.append(dur)
        visual = workdir / f"visual_{idx+1}.mp4"
        captioned = workdir / f"captioned_{idx+1}.mp4"
        if not captioned.exists():
            if not visual.exists():
                visual,_=obtain_scene_visual(scene,package,workdir,idx,dur)
            add_arabic_caption(visual, scene["narration"], captioned, workdir, idx, dur)
            visual=captioned
        video_paths.append(visual)
        voice_paths.append(aligned)
        sfx_file = workdir / f"sfx_{idx+1}.m4a"
        if not sfx_file.exists():
            make_scene_sfx(scene["effect"], sfx_file, dur)
        sfx_paths.append(sfx_file)
    progress("concatenating",75,"تجميع")
    video_only=workdir/"video_only.mp4"; voice_all=workdir/"voice_all.m4a"; sfx_all=workdir/"sfx_all.m4a"; music=workdir/"music.m4a"; final=workdir/"ZIL_final.mp4"
    concatenate_video(video_paths, video_only, workdir)
    concatenate_audio(voice_paths, voice_all, workdir, "voice")
    concatenate_audio(sfx_paths, sfx_all, workdir, "sfx")
    prepare_background_music(music, sum(durations), workdir, package)
    mix_final_audio(video_only, voice_all, sfx_all, music, final)
    thumbnails=create_thumbnails(workdir)
    update_story_after_episode(user_id, episode, package)
    update_job(job_id, thumbnails=[str(p) for p in thumbnails], output=str(final))
    progress("completed",100,f"اكتملت الحلقة {episode}")
    return final, package, thumbnails

def create_hmisc_video(job_id, idea_dict, user_id, index_in_series=0):
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    def progress(stage, percent, message):
        update_job(job_id, stage=stage, progress=percent, message=message)
        try:
            chat_id = JOBS.get(job_id, {}).get("chat_id")
            if chat_id:
                telegram_send_message(chat_id, f"{percent}% — {message}")
        except:
            pass
    progress("hmisc_image",10,f"توليد صورة {index_in_series+1}")
    visual_mp4,_=obtain_hmisc_visual(idea_dict, workdir, index_in_series, HMISC_VIDEO_DURATION)
    ar_nar = idea_dict.get("narration_ar","")
    en_nar = idea_dict.get("narration_en","")
    nar_raw = workdir / f"hmisc_nar_raw_{index_in_series}.mp3"
    nar_converted = workdir / f"hmisc_nar_{index_in_series}.m4a"
    merged_nar = create_bilingual_narration(ar_nar, en_nar, nar_raw, workdir, index_in_series)
    voice_dur = prepare_synced_voice(merged_nar, nar_converted)
    final_duration = max(HMISC_VIDEO_DURATION, voice_dur+0.5)
    if voice_dur>HMISC_VIDEO_DURATION:
        visual_mp4.unlink(missing_ok=True)
        visual_mp4,_=obtain_hmisc_visual(idea_dict, workdir, index_in_series, final_duration)
    progress("hmisc_caption",60,"ترجمة لغتين")
    captioned = workdir / f"hmisc_captioned_{index_in_series}.mp4"
    captioned_video = add_bilingual_nice_caption(visual_mp4, idea_dict.get("ar1",""), idea_dict.get("en1",""), idea_dict.get("ar2",""), idea_dict.get("en2",""), captioned, workdir, index_in_series, final_duration)
    progress("hmisc_music",80,f"موسيقى {idea_dict.get('emotion','')}")
    music_file = workdir / f"hmisc_music_{index_in_series}.m4a"
    prepare_hmisc_music(music_file, final_duration, emotion=idea_dict.get("emotion","anxiety"))
    final_output = workdir / f"HMISC_{index_in_series+1}.mp4"
    mix_hmisc_audio(captioned_video, nar_converted, music_file, final_output)
    update_job(job_id, output=str(final_output), title=f"{idea_dict.get('ar1','')} {idea_dict.get('ar2','')}")
    progress("completed",100,"اكتمل")
    return final_output, idea_dict

def job_worker(job_id, chat_id, user_id, idea, episode):
    global ACTIVE_JOBS
    try:
        update_job(job_id, status="running", stage="starting", progress=1, error="")
        telegram_send_message(chat_id, f"🎬 بدأ إنتاج الحلقة {episode}.\nالعملية: {job_id}")
        output, package, thumbnails = create_video(job_id, idea, user_id, episode)
        update_job(job_id, status="uploading", stage="uploading", progress=98)
        telegram_send_message(chat_id, f"✅ اكتملت الحلقة {episode}: {package['title']}")
        telegram_send_video(chat_id, output, f"🎬 {package['series_title']}\nالحلقة {episode}: {package['title']}")
        update_job(job_id, status="completed", stage="completed", progress=100, output=str(output), title=package["title"], error="")
    except Exception as exc:
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:3000])
        try:
            telegram_send_message(chat_id, f"❌ فشل {job_id}\n{str(exc)[:1000]}")
        except:
            pass
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)
            with STATE_LOCK:
                save_state_locked()

def hmisc_job_worker(job_id, chat_id, user_id, ideas, series_id):
    global ACTIVE_JOBS
    try:
        update_job(job_id, status="running", stage="hmisc_series", progress=1, series_id=series_id, total=len(ideas))
        telegram_send_message(chat_id, f"✨ سلسلة hmisc: {len(ideas)} فيديو\nالسلسلة: {series_id}")
        outputs=[]
        for idx, idea in enumerate(ideas):
            update_job(job_id, current_video=idx+1, current_idea=idea)
            telegram_send_message(chat_id, f"🎨 {idx+1}/{len(ideas)}: {idea.get('ar1','')} {idea.get('ar2','')}")
            output,_=create_hmisc_video(job_id+f"_{idx}", idea, user_id, idx)
            outputs.append(str(output))
            try:
                telegram_send_video(chat_id, output, f"✨ {idea.get('ar1','')} {idea.get('ar2','')}\n{idea.get('en1','')} {idea.get('en2','')}")
            except:
                pass
            update_job(job_id, progress=int((idx+1)/len(ideas)*95), outputs=outputs)
        atomic_json(BASE_DIR / f"{series_id}.json", {"series_id": series_id, "ideas": ideas, "outputs": outputs, "created_at": time.time(), "user_id": user_id})
        update_job(job_id, status="completed", stage="completed", progress=100, outputs=outputs)
        telegram_send_message(chat_id, f"🎉 اكتملت السلسلة {len(ideas)} فيديو")
    except Exception as exc:
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:3000])
        try:
            telegram_send_message(chat_id, f"❌ فشلت السلسلة {job_id}\n{str(exc)[:1000]}")
        except:
            pass
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)
            with STATE_LOCK:
                save_state_locked()

async def launch_job(update, context, idea, episode):
    global ACTIVE_JOBS
    message = update.effective_message; chat = update.effective_chat; user = update.effective_user
    if not message or not chat or not user:
        return
    idea = (idea or "").strip()
    if len(idea)<5:
        await message.reply_text("اكتب فكرة أطول")
        return
    with JOBS_LOCK:
        if ACTIVE_JOBS>=MAX_ACTIVE_JOBS:
            await message.reply_text("⏳ يوجد عمل قيد التنفيذ")
            return
        ACTIVE_JOBS+=1
        job_id=f"zil_{uuid.uuid4().hex[:8]}"
        workdir=BASE_DIR/job_id; workdir.mkdir(parents=True, exist_ok=True)
        JOBS[job_id]={"id":job_id,"user_id":user.id,"chat_id":chat.id,"idea":idea,"episode":episode,"status":"queued","stage":"queued","progress":0,"created_at":time.time(),"updated_at":time.time()}
        with STATE_LOCK:
            save_state_locked()
    await message.reply_text(f"🎬 تم بدء الحلقة {episode}\nالعملية: {job_id}")
    threading.Thread(target=job_worker, args=(job_id, chat.id, user.id, idea, episode), daemon=True).start()

async def launch_hmisc_series(update, context, count=5, theme="رسائل نفسية"):
    global ACTIVE_JOBS
    message = update.effective_message; chat = update.effective_chat; user = update.effective_user
    if not message or not chat or not user:
        return
    with JOBS_LOCK:
        if ACTIVE_JOBS>=MAX_ACTIVE_JOBS:
            await message.reply_text("⏳ يوجد عمل قيد التنفيذ")
            return
        ACTIVE_JOBS+=1
        job_id=f"hmisc_{uuid.uuid4().hex[:8]}"
        series_id=f"series_{uuid.uuid4().hex[:6]}"
        workdir=BASE_DIR/job_id; workdir.mkdir(parents=True, exist_ok=True)
        JOBS[job_id]={"id":job_id,"user_id":user.id,"chat_id":chat.id,"idea":theme,"episode":0,"status":"queued","stage":"queued","progress":0,"created_at":time.time(),"updated_at":time.time(),"type":"hmisc_series","series_id":series_id,"count":count}
        with STATE_LOCK:
            save_state_locked()
    await message.reply_text(f"✨ جاري تفكير العقل {count} أفكار عن: {theme}\nالسلسلة: {series_id}")
    def gen_and_start():
        try:
            ideas=generate_hmisc_series_ideas(count=count, theme=theme)
            threading.Thread(target=hmisc_job_worker, args=(job_id, chat.id, user.id, ideas, series_id), daemon=True).start()
        except Exception as e:
            with JOBS_LOCK:
                ACTIVE_JOBS=max(0,ACTIVE_JOBS-1)
            telegram_send_message(chat.id, f"❌ فشل توليد الأفكار: {e}")
    threading.Thread(target=gen_and_start, daemon=True).start()

async def cmd_test(update, context):
    idea = " ".join(context.args).strip() or "رجل غامض يصل مملكة قديمة علامة متوهجة قوة سحرية"
    await launch_job(update, context, idea, 1)

async def cmd_next(update, context):
    story=get_user_story(update.effective_user.id)
    if not story:
        await update.effective_message.reply_text("اعمل /test أولًا")
        return
    episode=int(story.get("episode",0))+1
    idea=f"تابع الحلقة {episode-1} ملخص {story.get('full_story_so_far','')[-2000:]} نهاية {story.get('last_cliffhanger','')}"
    await launch_job(update, context, idea, episode)

async def cmd_reset(update, context):
    user_id=str(update.effective_user.id)
    with STORY_LOCK:
        progress=load_story_progress(); memory=load_character_memory()
        progress.pop(user_id,None); memory.pop(user_id,None)
        atomic_json(STORY_PROGRESS_FILE, progress); atomic_json(CHARACTER_MEMORY_FILE, memory)
    await update.effective_message.reply_text("تم المسح")

async def cmd_status(update, context):
    story=get_user_story(update.effective_user.id)
    if not story:
        await update.effective_message.reply_text("ما عندك مسلسل")
        return
    await update.effective_message.reply_text(f"الحلقة: {story.get('episode')}\n{story.get('last_cliffhanger','')[:300]}")

async def cmd_jobs(update, context):
    user_id=update.effective_user.id
    with JOBS_LOCK:
        user_jobs=[j.copy() for j in JOBS.values() if j.get("user_id")==user_id]
    if not user_jobs:
        await update.effective_message.reply_text("لا توجد عمليات")
        return
    lines=["📋 عمليات ZIL:"]
    for job in user_jobs[:8]:
        lines.append(f"{job.get('id')} | {job.get('status')} | {job.get('progress',0)}%")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_resume(update, context):
    if not context.args:
        await update.effective_message.reply_text("استخدم /resume ID")
        return
    job_id=context.args[0].strip()
    with JOBS_LOCK:
        job=JOBS.get(job_id)
        if not job:
            await update.effective_message.reply_text("غير موجودة")
            return
        episode=int(job.get("episode",1)); idea=job.get("idea","")
    await launch_job(update, context, idea, episode)

async def cmd_hmisc(update, context):
    args=context.args; count=5; theme="رسائل نفسية فانتازيا"
    if args:
        try:
            count=int(args[0]); theme=" ".join(args[1:]) if len(args)>1 else theme
        except ValueError:
            theme=" ".join(args)
    count=max(1,min(10,count))
    await launch_hmisc_series(update, context, count=count, theme=theme)

async def cmd_start(update, context):
    await update.effective_message.reply_text("🎬 ZIL V81 HMISC\n/test بدء حلقة\n/next متابعة\n/hmisc 5 سلسلة نفسية بألوان ورقية وزوم متحرك ولغتين وموسيقى وCTA شير")

@app.route("/")
def home():
    return jsonify({"status":"ZIL V81 HMISC running","episodes":MAX_EPISODES,"scenes":SCENE_COUNT,"hmisc":True})

@app.route("/health")
def health():
    return jsonify({"ok":True,"groq_configured":bool(GROQ_API_KEY),"bot_configured":bool(BOT_TOKEN),"active_jobs":ACTIVE_JOBS})

@app.route("/generate_hmisc_series", methods=["POST"])
def generate_hmisc_series_endpoint():
    data=request.get_json(silent=True) or {}
    count=int(data.get("count",5)); theme=data.get("theme","رسائل نفسية")
    count=max(1,min(10,count))
    try:
        ideas=generate_hmisc_series_ideas(count=count, theme=theme)
        return jsonify({"ok":True,"count":len(ideas),"ideas":ideas})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    global ACTIVE_JOBS
    load_state()
    verify_dependencies()
    ACTIVE_JOBS=0
    threading.Thread(target=run_flask, daemon=True).start()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    application=(Application.builder().token(BOT_TOKEN).build())
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("test", cmd_test))
    application.add_handler(CommandHandler("next", cmd_next))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("jobs", cmd_jobs))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("hmisc", cmd_hmisc))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
