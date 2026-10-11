import os
import re
import sys
import json
import time
import uuid
import random
import asyncio
import logging
import threading
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from shutil import which

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL V75 - Complete Telegram Video Bot
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_IMAGE_URL = "https://pixabay.com/api/"
TELEGRAM_API = "https://api.telegram.org"
PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 18
BASE_SCENE_DURATION = 5.0
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024
MAX_SOURCE_VIDEO_BYTES = 100 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024
GROQ_RETRIES = max(1, int(os.getenv("GROQ_RETRIES", "4")))
GROQ_MAX_WAIT = max(5, int(os.getenv("GROQ_MAX_WAIT", "60")))
# A smaller default reduces the chance of hitting Groq TPM limits.
GROQ_MAX_TOKENS = max(1200, int(os.getenv("GROQ_MAX_TOKENS", "3500")))
TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural").strip()
TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()
SUBTITLES_ENABLED = os.getenv("SUBTITLES_ENABLED", "true").lower() in ("1", "true", "yes", "on")
STORY_MUSIC_URL = os.getenv("STORY_MUSIC_URL", "").strip()

BASE_DIR = Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir())) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = BASE_DIR / "jobs_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(BASE_DIR / "zil.log", encoding="utf-8")],
)
log = logging.getLogger("ZIL")
app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-Video-Bot/5.0"})
JOBS = {}
JOBS_LOCK = threading.RLock()
ACTIVE_JOBS = 0
EFFECTS = {"mystery", "castle", "steps", "romance", "tension", "storm", "beast", "fight", "impact", "magic", "cliffhanger"}


# =========================================================
# State and job tracking
# =========================================================

def save_state_locked():
    try:
        safe = {}
        for job_id, job in JOBS.items():
            safe[job_id] = {k: v for k, v in job.items() if k not in {"idea", "narration"}}
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        temp.replace(STATE_FILE)
    except Exception:
        log.exception("Could not save job state")


def load_state():
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for job_id, job in data.items():
            if not isinstance(job, dict):
                continue
            if job.get("status") in ("running", "queued", "uploading"):
                job["status"] = "interrupted"
                job["stage"] = "interrupted_after_restart"
                job["error"] = "The service restarted before the job finished."
            JOBS[job_id] = job
    except Exception:
        log.exception("Could not load job state")


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id not in JOBS:
            return
        JOBS[job_id].update(kwargs)
        JOBS[job_id]["updated_at"] = time.time()
        save_state_locked()


def active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


def shutil_which(name):
    return which(name)


# =========================================================
# Utilities
# =========================================================

def run_command(command, timeout=240):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "Unknown error")[-2500:]
        raise RuntimeError(f"Command failed ({result.returncode}): {details}")
    return result


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def safe_query(text, maximum=120):
    text = re.sub(r"[^A-Za-z0-9 ,'-]", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("AI did not return valid JSON.")


def probe_duration(path):
    result = run_command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=30)
    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        return None


def has_audio_stream(path):
    result = run_command(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=30)
    return "audio" in result.stdout.lower()


def download_media(url, destination, maximum_bytes):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Media URL must use HTTPS.")
    total = 0
    with requests.get(url, stream=True, timeout=(20, 90), headers={"User-Agent": "ZIL-Video-Bot/5.0"}) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if any(bad in content_type for bad in ("text/html", "application/json")):
            raise RuntimeError("The URL did not return a media file.")
        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum_bytes:
                    raise RuntimeError("Media file exceeds the configured size limit.")
                output.write(chunk)
    if total < 5000:
        raise RuntimeError("Downloaded media is too small.")
    return destination


# =========================================================
# Telegram notifications from worker threads
# Using the Bot API over requests avoids sharing async event loops.
# =========================================================

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN:
        return
    response = requests.post(
        f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": str(text)[:4000]},
        timeout=(15, 45),
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {data}")


def telegram_send_video(chat_id, path, caption):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    with open(path, "rb") as video_file:
        response = requests.post(
            f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendVideo",
            data={"chat_id": chat_id, "caption": str(caption)[:1000], "supports_streaming": "true"},
            files={"video": (Path(path).name, video_file, "video/mp4")},
            timeout=(30, 240),
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendVideo failed: {data}")


# =========================================================
# Groq API and rate-limit handling
# =========================================================

def groq_wait_seconds(response, attempt):
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except Exception:
        message = response.text or ""
    wait = None
    for pattern in (r"try again in\s+([0-9.]+)\s*s", r"wait\s+([0-9.]+)\s*s", r"retry after\s+([0-9.]+)"):
        match = re.search(pattern, message, re.I)
        if match:
            try:
                wait = float(match.group(1))
                break
            except ValueError:
                pass
    try:
        header_wait = float(response.headers.get("retry-after", ""))
        wait = max(wait or 0, header_wait)
    except (ValueError, TypeError):
        pass
    if wait is None:
        wait = min(8 * (2 ** attempt), GROQ_MAX_WAIT)
    return max(1, min(wait + 1, GROQ_MAX_WAIT))


def groq_chat(messages, max_tokens=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing in Render.")
    last_error = "Unknown error"
    for attempt in range(GROQ_RETRIES):
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.25, "max_tokens": max_tokens or GROQ_MAX_TOKENS},
                timeout=(20, 120),
            )
            if response.status_code == 429:
                last_error = f"Groq rate limit: {response.text[:800]}"
                wait = groq_wait_seconds(response, attempt)
                log.warning("Groq rate limit; attempt %s/%s; waiting %.1fs", attempt + 1, GROQ_RETRIES, wait)
                if attempt < GROQ_RETRIES - 1:
                    time.sleep(wait + random.uniform(0.2, 0.8))
                    continue
                break
            if response.status_code >= 400:
                last_error = f"Groq HTTP {response.status_code}: {response.text[:800]}"
                if response.status_code in (400, 401, 403, 404):
                    raise RuntimeError(last_error)
                if attempt < GROQ_RETRIES - 1:
                    time.sleep(min(4 * (attempt + 1), 12))
                    continue
                break
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("Groq returned no choices.")
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not str(content).strip():
                raise RuntimeError("Groq returned empty content.")
            return str(content)
        except requests.RequestException as exc:
            last_error = f"Groq connection error: {exc}"
            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(4 * (attempt + 1), 12))
    raise RuntimeError("Groq failed after retries. Last error: " + last_error)


# =========================================================
# Story generation and validation
# =========================================================

def create_story_package(idea):
    system_prompt = f"""
أنت كاتب سيناريو محترف لقصص عربية مشوقة. اكتب قصة مترابطة من {SCENE_COUNT} مشهدًا بالضبط.
كل مشهد يحتوي: narration جملة عربية قصيرة، visual_prompt وصف بصري إنجليزي سينمائي واقعي، search_query كلمات إنجليزية للبحث عن فيديو أو صورة، effect نوع واحد من القائمة.
الأنواع المسموحة: mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger
القواعد: حافظ على تسلسل القصة وهوية الشخصيات. افتح بخطاف قوي، صعّد الخطر تدريجيًا، نوّع المشاهد، اجعل السرد واضحًا وقصيرًا، واختم بتشويق للجزء التالي. لا تضع نصوصًا داخل visual_prompt. أخرج JSON صحيحًا فقط دون Markdown.
المفاتيح: title, story, scenes. كل مشهد: narration, visual_prompt, search_query, effect.
"""
    user_prompt = f"فكرة القصة:\n{str(idea)[:MAX_STORY_LENGTH]}\nأنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    last_error = None
    for attempt in range(2):
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        if attempt:
            messages.append({"role": "user", "content": f"أعد JSON صالحًا يحتوي على {SCENE_COUNT} مشهدًا بالضبط، مع سرد عربي قصير في كل مشهد."})
        raw = groq_chat(messages)
        data = extract_json(raw)
        try:
            scenes = data.get("scenes")
            if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
                raise ValueError(f"Expected exactly {SCENE_COUNT} scenes.")
            clean = []
            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(f"Invalid scene {index + 1}")
                narration = re.sub(r"\s+", " ", str(scene.get("narration", ""))).strip()
                visual = re.sub(r"\s+", " ", str(scene.get("visual_prompt", ""))).strip()
                query = safe_query(scene.get("search_query", ""))
                effect = str(scene.get("effect", "mystery")).lower().strip()
                if not narration:
                    raise ValueError(f"Scene {index + 1} has no narration.")
                if not visual:
                    visual = "cinematic realistic dramatic scene"
                if len(query.split()) < 3:
                    query = safe_query(" ".join(re.findall(r"[A-Za-z0-9]+", visual)[:8]))
                if len(query.split()) < 3:
                    query = "cinematic dramatic scene"
                if effect not in EFFECTS:
                    effect = "mystery"
                clean.append({"narration": narration[:180], "visual_prompt": visual[:350], "search_query": query[:120], "effect": effect})
            total_words = word_count(" ".join(s["narration"] for s in clean))
            if not 20 <= total_words <= 300:
                raise ValueError(f"Unexpected narration length: {total_words}")
            return {"title": str(data.get("title", "حكاية ظل"))[:100], "story": str(data.get("story", idea))[:2000], "scenes": clean}
        except Exception as exc:
            last_error = exc
            log.warning("Story validation attempt %s failed: %s", attempt + 1, exc)
    raise RuntimeError(f"Story validation failed: {last_error}")


# =========================================================
# Pixabay search
# =========================================================

def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")
    response = requests.get(PIXABAY_VIDEO_URL, params={"key": PIXABAY_API_KEY, "q": query, "per_page": 10, "safesearch": "true"}, timeout=(15, 35))
    response.raise_for_status()
    for hit in response.json().get("hits", []):
        videos = hit.get("videos", {})
        for quality in ("medium", "large", "small", "tiny"):
            url = videos.get(quality, {}).get("url", "")
            if url and urlparse(url).scheme == "https":
                return url
    raise RuntimeError(f"No video found for query: {query}")


def search_pixabay_image(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")
    response = requests.get(PIXABAY_IMAGE_URL, params={"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "vertical", "safesearch": "true", "per_page": 15}, timeout=(15, 35))
    response.raise_for_status()
    for hit in response.json().get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if url and urlparse(url).scheme == "https":
            return url
    raise RuntimeError(f"No image found for query: {query}")


# =========================================================
# Scene rendering
# =========================================================

def normalize_video_scene(source, destination, duration):
    duration = max(1.0, float(duration))
    vf = f"scale=900:1600:force_original_aspect_ratio=increase,crop=900:1600,fps={VIDEO_FPS},scale=720:1280,setsar=1,format=yuv420p"
    # Loop short source clips so each scene lasts for the narration duration.
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(source), "-vf", vf, "-an", "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 300)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Video normalization failed.")
    return destination


def animate_still_image(source, destination, duration, index):
    duration = max(1.0, float(duration))
    total_frames = max(1, int(duration * VIDEO_FPS))
    x_expr = "iw/2-(iw/zoom/2)+(iw/zoom/10)*sin(on/40)" if index % 2 else "iw/2-(iw/zoom/2)-(iw/zoom/10)*sin(on/40)"
    vf = f"scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800,zoompan=z='min(zoom+0.001,1.12)':x='{x_expr}':y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},trim=end_frame={total_frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(source), "-vf", vf, "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 300)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Image animation failed.")
    return destination


def create_fallback_visual(destination, duration, index):
    colors = ["0x172238", "0x28203D", "0x26343A", "0x38232B", "0x1D2A42"]
    color = colors[index % len(colors)]
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}:d={duration}", "-vf", "format=yuv420p", "-t", str(duration), "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", str(destination)], 120)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Could not create fallback visual.")
    return destination


def obtain_scene_visual(scene, workdir, index, duration):
    number = index + 1
    query = scene["search_query"]
    visual_query = safe_query(" ".join(re.findall(r"[A-Za-z0-9]+", scene["visual_prompt"])[:8]))
    raw_video = workdir / f"raw_video_{number}.mp4"
    raw_image = workdir / f"raw_image_{number}.jpg"
    normalized = workdir / f"visual_{number}.mp4"
    queries = [query]
    if visual_query and visual_query.lower() != query.lower():
        queries.append(visual_query)
    for candidate in queries:
        try:
            url = search_pixabay_video(candidate)
            download_media(url, raw_video, MAX_SOURCE_VIDEO_BYTES)
            normalize_video_scene(raw_video, normalized, duration)
            log.info("Scene %s: video source selected", number)
            return normalized, "video"
        except Exception as exc:
            log.warning("Scene %s video search failed: %s", number, exc)
    for candidate in queries:
        try:
            url = search_pixabay_image(candidate)
            download_media(url, raw_image, MAX_SOURCE_IMAGE_BYTES)
            animate_still_image(raw_image, normalized, duration, index)
            log.info("Scene %s: animated image selected", number)
            return normalized, "image"
        except Exception as exc:
            log.warning("Scene %s image search failed: %s", number, exc)
    log.warning("Scene %s: using abstract fallback background", number)
    create_fallback_visual(normalized, duration, index)
    return normalized, "fallback"


# =========================================================
# Optional Arabic subtitles
# =========================================================

def add_arabic_caption(source, caption, destination, workdir, index, duration):
    if not SUBTITLES_ENABLED:
        return source
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning("Subtitle libraries missing; continuing without subtitles")
        return source
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(font_path, 30) if Path(font_path).exists() else ImageFont.load_default()
        overlay = Image.new("RGBA", (VIDEO_WIDTH, 150), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        words, lines, current = (caption or "").split(), [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            shaped = get_display(arabic_reshaper.reshape(candidate))
            box = draw.textbbox((0, 0), shaped, font=font)
            if current and box[2] - box[0] > VIDEO_WIDTH - 80:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        lines = lines[:2]
        draw.rounded_rectangle((18, 8, VIDEO_WIDTH - 18, 142), radius=16, fill=(0, 0, 0, 160))
        for line_index, line in enumerate(lines):
            shaped = get_display(arabic_reshaper.reshape(line))
            box = draw.textbbox((0, 0), shaped, font=font)
            x = max(10, (VIDEO_WIDTH - (box[2] - box[0])) // 2)
            draw.text((x, 20 + line_index * 48), shaped, font=font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
        png = workdir / f"caption_{index}.png"
        overlay.save(png)
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(png), "-filter_complex", "[0:v][1:v]overlay=0:H-150:format=auto[v]", "-map", "[v]", "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 180)
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Subtitle render failed; continuing without captions")
    return source


# =========================================================
# Arabic TTS and scene-length audio padding
# =========================================================

def create_narration(text, destination):
    destination.unlink(missing_ok=True)
    try:
        import edge_tts
        async def generate():
            communicate = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
            await communicate.save(str(destination))
        asyncio.run(generate())
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Edge TTS failed")
    try:
        from gtts import gTTS
        gTTS(text=text, lang="ar", slow=False).save(str(destination))
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("gTTS fallback failed")
    raise RuntimeError("Arabic text-to-speech failed.")


def prepare_synced_voice(source, destination):
    duration = probe_duration(source)
    if not duration or duration <= 0:
        raise RuntimeError("Could not determine voice duration.")
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-af", "aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)], 120)
    actual = probe_duration(destination)
    if not actual or actual <= 0:
        raise RuntimeError("Voice conversion failed.")
    return actual


def fit_audio_to_duration(source, destination, duration):
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-af", f"aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)], 120)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Could not fit narration to scene duration.")
    return destination


# =========================================================
# Sound effects and background music
# =========================================================

def make_scene_sfx(kind, destination, duration):
    duration = max(1.0, float(duration))
    if kind == "beast":
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={duration}"
        af = "highpass=f=45,lowpass=f=850,tremolo=f=5:d=0.75,volume=0.22"
    elif kind in ("fight", "impact"):
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        af = "highpass=f=50,lowpass=f=1600,volume=0.22,afade=t=in:d=0.02," + f"afade=t=out:st={max(0.1, duration-0.8)}:d=0.7"
    elif kind == "magic":
        source = f"sine=frequency=95:sample_rate=44100:duration={duration}"
        af = "tremolo=f=3:d=0.75,volume=0.12"
    elif kind == "storm":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        af = "lowpass=f=420,volume=0.10,tremolo=f=0.25:d=0.4"
    elif kind in ("tension", "mystery", "cliffhanger"):
        source = f"sine=frequency=72:sample_rate=44100:duration={duration}"
        af = "tremolo=f=2:d=0.55,lowpass=f=500,volume=0.08"
    elif kind == "romance":
        source = f"sine=frequency=440:sample_rate=44100:duration={duration}"
        af = "lowpass=f=1200,volume=0.025"
    else:
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        af = "lowpass=f=1000,volume=0.045"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source, "-af", af, "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "64k", str(destination)], 120)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Could not create SFX: {kind}")
    return destination


def prepare_background_music(destination, duration, workdir):
    if STORY_MUSIC_URL:
        try:
            source = workdir / "custom_music_source.mp3"
            download_media(STORY_MUSIC_URL, source, 35 * 1024 * 1024)
            run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}", "-af", "volume=0.18,afade=t=in:d=2," + f"afade=t=out:st={max(0.1, duration-4)}:d=4", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)], 240)
            if destination.exists() and destination.stat().st_size > 1000:
                return destination
        except Exception:
            log.exception("Custom music failed; using generated ambience")
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=55:sample_rate=44100:duration={duration}", "-af", "volume=0.025,tremolo=f=0.18:d=0.25,afade=t=in:d=2," + f"afade=t=out:st={max(0.1, duration-4)}:d=4", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "64k", str(destination)], 180)
    return destination


# =========================================================
# Concatenation and final mix
# =========================================================

def write_concat_list(paths, destination):
    with open(destination, "w", encoding="utf-8") as output:
        for path in paths:
            resolved = Path(path).resolve().as_posix()
            if "'" in resolved:
                raise RuntimeError("A media path contains an unsupported apostrophe.")
            output.write(f"file '{resolved}'\n")


def concatenate_video(paths, destination, workdir):
    listing = workdir / "video_list.txt"
    write_concat_list(paths, listing)
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-an", "-vf", "setsar=1,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 600)
    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation failed.")
    return destination


def concatenate_audio(paths, destination, workdir, label):
    listing = workdir / f"{label}_list.txt"
    write_concat_list(paths, listing)
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vn", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)], 600)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Audio concatenation failed: {label}")
    return destination


def mix_final_audio(video, voice, sfx, music, destination):
    for label, path in {"video": video, "voice": voice, "sfx": sfx, "music": music}.items():
        if not Path(path).exists() or Path(path).stat().st_size < 1000:
            raise RuntimeError(f"Missing mix input: {label}")
    for label, path in (("voice", voice), ("sfx", sfx), ("music", music)):
        if not has_audio_stream(path):
            raise RuntimeError(f"No audio stream in {label}")
    duration = probe_duration(video)
    if not duration or duration <= 0:
        raise RuntimeError("Could not determine video duration.")
    graph = (
        "[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume=1.0,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[v];"
        "[2:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume=0.22,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[s];"
        "[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume=0.12,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[m];"
        "[v][s][m]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,alimiter=limit=0.92,"
        f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[aout]"
    )
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(voice), "-i", str(sfx), "-i", str(music), "-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]", "-t", f"{duration:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(destination)], 600)
    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Final video file is missing or too small.")
    return destination


# =========================================================
# Main production pipeline
# =========================================================

def create_video(job_id, idea):
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    def progress(stage, percent, message):
        update_job(job_id, stage=stage, progress=percent, message=message)
        try:
            chat_id = JOBS.get(job_id, {}).get("chat_id")
            if chat_id:
                telegram_send_message(chat_id, f"{percent}% — {message}")
        except Exception:
            log.exception("Could not send progress update")

    package = create_story_package(idea)
    scenes = package["scenes"]
    update_job(job_id, title=package["title"], story=package["story"], scene_count=len(scenes))
    video_paths, voice_paths, sfx_paths, durations = [], [], [], []

    for index, scene in enumerate(scenes):
        number = index + 1
        progress("scene_generation", int(5 + (index / SCENE_COUNT) * 65), f"تجهيز المشهد {number}/{SCENE_COUNT}")
        raw_voice = workdir / f"voice_raw_{number}.mp3"
        voice_converted = workdir / f"voice_converted_{number}.m4a"
        voice_aligned = workdir / f"voice_{number}.m4a"
        create_narration(scene["narration"], raw_voice)
        voice_duration = prepare_synced_voice(raw_voice, voice_converted)
        scene_duration = max(BASE_SCENE_DURATION, voice_duration + 0.20)
        durations.append(scene_duration)
        # Pad each narration to its own scene duration to prevent later scenes drifting out of sync.
        fit_audio_to_duration(voice_converted, voice_aligned, scene_duration)
        visual, visual_type = obtain_scene_visual(scene, workdir, index, scene_duration)
        update_job(job_id, current_scene=number, current_visual=visual_type)
        captioned = workdir / f"captioned_{number}.mp4"
        final_visual = add_arabic_caption(visual, scene["narration"], captioned, workdir, index, scene_duration)
        video_paths.append(final_visual)
        voice_paths.append(voice_aligned)
        sfx_file = workdir / f"sfx_{number}.m4a"
        try:
            make_scene_sfx(scene["effect"], sfx_file, scene_duration)
        except Exception:
            log.exception("SFX failed in scene %s; using mystery ambience", number)
            make_scene_sfx("mystery", sfx_file, scene_duration)
        sfx_paths.append(sfx_file)

    progress("concatenating", 75, "تجميع المشاهد والصوت...")
    final_video_only = workdir / "video_only.mp4"
    final_voice = workdir / "voice_all.m4a"
    final_sfx = workdir / "sfx_all.m4a"
    final_music = workdir / "music.m4a"
    final_output = workdir / "ZIL_final.mp4"
    concatenate_video(video_paths, final_video_only, workdir)
    concatenate_audio(voice_paths, final_voice, workdir, "voice")
    concatenate_audio(sfx_paths, final_sfx, workdir, "sfx")
    total_duration = sum(durations)
    prepare_background_music(final_music, total_duration, workdir)
    progress("audio_mix", 90, "موازنة الصوت والموسيقى والمؤثرات...")
    mix_final_audio(final_video_only, final_voice, final_sfx, final_music, final_output)

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        progress("compressing", 95, "ضغط الفيديو ليتناسب مع إرسال تيليغرام...")
        compressed = workdir / "ZIL_compressed.mp4"
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(final_output), "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-vf", "scale=540:960", "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart", str(compressed)], 600)
        if compressed.exists() and compressed.stat().st_size < final_output.stat().st_size:
            final_output = compressed
    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        raise RuntimeError("Final video is still too large for the configured Telegram limit. Reduce scene count or resolution.")
    progress("completed", 100, "اكتمل الفيديو!")
    return final_output, package


# =========================================================
# Job worker
# ACTIVE_JOBS is reserved once in launch_job and released here.
# =========================================================

def job_worker(job_id, chat_id, idea):
    global ACTIVE_JOBS
    try:
        update_job(job_id, status="running", stage="starting", progress=1)
        try:
            telegram_send_message(chat_id, f"🎬 بدأ إنتاج الفيديو.\nرقم العملية: {job_id}\nعدد المشاهد: {SCENE_COUNT}\nقد تستغرق العملية وقتًا بحسب توفر الوسائط.")
        except Exception:
            log.exception("Could not send job-start message")
        output, package = create_video(job_id, idea)
        update_job(job_id, status="uploading", stage="uploading", progress=98)
        if not output.exists():
            raise RuntimeError("Final video was not created.")
        if output.stat().st_size > MAX_VIDEO_SIZE:
            raise RuntimeError("Video exceeds the Telegram upload limit after compression.")
        telegram_send_message(chat_id, f"✅ اكتمل الفيديو: {package['title']}\nجارٍ إرساله الآن...")
        telegram_send_video(chat_id, output, f"🎬 {package['title']}\nصناعة: ظل ZIL")
        update_job(job_id, status="completed", stage="completed", progress=100, output=str(output), title=package["title"])
        telegram_send_message(chat_id, f"🎉 انتهى الإنتاج بنجاح!\nالعملية: {job_id}")
    except Exception as exc:
        log.exception("Video job failed: %s", job_id)
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:2500])
        try:
            telegram_send_message(chat_id, f"❌ فشل إنتاج الفيديو.\nالعملية: {job_id}\nالسبب: {str(exc)[:1000]}\n\nاستخدم /job {job_id} لمراجعة الحالة وافحص Runtime Logs في Render.")
        except Exception:
            log.exception("Could not send job-failure message")
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)
            save_state_locked()


# =========================================================
# Telegram commands
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "🎬 أهلًا بك في ظل ZIL V75\n\nأرسل فكرة أو قصة لتحويلها إلى فيديو رأسي.\n\n"
        "الأوامر:\n/start - تشغيل البوت\n/make فكرتك - إنشاء فيديو\n/test - تجربة قصة ثابتة\n/status - حالة النظام والعمليات\n/job رقم_العملية - تفاصيل عملية\n/health - فحص الخدمات\n\n"
        f"الفيديو يستهدف {SCENE_COUNT} مشهدًا، مع سرد عربي ومؤثرات وموسيقى."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    with JOBS_LOCK:
        jobs = list(JOBS.items())
        active = ACTIVE_JOBS
    if not jobs:
        await update.effective_message.reply_text(
            f"حالة ظل ZIL\nالعمليات النشطة: {active}\nGroq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}\nPixabay: {'مهيأ' if PIXABAY_API_KEY else 'غير مهيأ'}\nFFmpeg: {'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}"
        )
        return
    lines = ["📊 حالة ظل ZIL", f"العمليات النشطة: {active}", ""]
    for job_id, job in reversed(jobs[-5:]):
        lines.append(f"{job_id} | {job.get('status', 'unknown')} | {job.get('stage', '-')} | {job.get('progress', 0)}%")
    await update.effective_message.reply_text("\n".join(lines))


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "🩺 فحص ظل ZIL\n"
        f"BOT_TOKEN: {'مهيأ' if BOT_TOKEN else 'مفقود'}\n"
        f"GROQ_API_KEY: {'مهيأ' if GROQ_API_KEY else 'مفقود'}\n"
        f"PIXABAY_API_KEY: {'مهيأ' if PIXABAY_API_KEY else 'مفقود'}\n"
        f"FFmpeg: {'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}\n"
        f"FFprobe: {'جاهز' if shutil_which('ffprobe') else 'غير موجود'}\n"
        f"المهام النشطة: {active_jobs()}"
    )


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    if not context.args:
        await update.effective_message.reply_text("استخدم الأمر هكذا:\n/job رقم_العملية")
        return
    job_id = context.args[0].strip()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job = dict(job) if job else None
    if not job:
        await update.effective_message.reply_text("لم أجد هذه العملية.")
        return
    await update.effective_message.reply_text(
        f"العملية: {job_id}\nالحالة: {job.get('status', '-')}\nالمرحلة: {job.get('stage', '-')}\nالتقدم: {job.get('progress', 0)}%\nالمشهد: {job.get('current_scene', '-')}\nالخطأ: {job.get('error', 'لا يوجد')}"
    )


async def launch_job(update: Update, context: ContextTypes.DEFAULT_TYPE, idea: str):
    global ACTIVE_JOBS
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    idea = (idea or "").strip()
    if len(idea) < 5:
        await update.effective_message.reply_text("اكتب فكرة أو قصة أطول قليلًا.")
        return
    idea = idea[:MAX_STORY_LENGTH]
    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.effective_message.reply_text("⏳ يوجد إنتاج آخر قيد التنفيذ. انتظر حتى ينتهي قبل بدء فيديو جديد.")
            return
        job_id = uuid.uuid4().hex[:10]
        ACTIVE_JOBS += 1  # Reserve atomically; worker only releases it.
        JOBS[job_id] = {
            "status": "queued", "stage": "queued", "progress": 0,
            "created_at": time.time(), "updated_at": time.time(),
            "chat_id": update.effective_chat.id, "user_id": update.effective_user.id,
        }
        save_state_locked()
    await update.effective_message.reply_text(f"🚀 تم تسجيل طلبك.\nرقم العملية: {job_id}\nسأبدأ تجهيز القصة والمشاهد.")
    worker = threading.Thread(target=job_worker, args=(job_id, update.effective_chat.id, idea), daemon=True)
    try:
        worker.start()
    except Exception as exc:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)
        update_job(job_id, status="failed", stage="thread_start_failed", error=str(exc))
        log.exception("Could not start worker thread")
        await update.effective_message.reply_text("تعذر تشغيل المهمة. أعد المحاولة بعد قليل.")


async def make_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = " ".join(context.args).strip()
    if not idea:
        if update.effective_message:
            await update.effective_message.reply_text("استخدم الأمر هكذا:\n/make أمير غامض يواجه وحشًا لإنقاذ الأميرة")
        return
    await launch_job(update, context, idea)


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = (
        "قصة خيالية سينمائية: رجل غامض يصل إلى مملكة قديمة، وتقع الأميرة في حبه. "
        "يرفض الملك علاقتهما لأنه يخفي سرًا خطيرًا عن قوة الرجل. يقتحم وحش ضخم ساحة القصر، "
        "ويتظاهر البطل بالضعف قبل أن يكشف جزءًا من قوته الحقيقية. ينقذ الأميرة لكنه يكتشف أن "
        "الوحش كان يهرب من خطر أكبر، وتنتهي القصة بظهور علامة غامضة على ذراعه."
    )
    await launch_job(update, context, idea)


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if isinstance(error, Conflict) or "conflict" in str(error).lower():
        log.error("Telegram Conflict detected. Another bot process may be polling with this token. Stop duplicate deployments/processes; an error handler cannot resolve this by itself.")
    else:
        log.error("Unhandled Telegram application error", exc_info=(type(error), error, error.__traceback__) if error else None)


# =========================================================
# Flask endpoints
# =========================================================

@app.get("/")
def index():
    return jsonify({"service": "ZIL V75", "status": "online", "active_jobs": active_jobs()})


@app.get("/health")
def health():
    return jsonify({
        "status": "ok", "service": "ZIL V75",
        "groq_configured": bool(GROQ_API_KEY),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "ffmpeg_available": bool(shutil_which("ffmpeg")),
        "ffprobe_available": bool(shutil_which("ffprobe")),
        "active_jobs": active_jobs(),
    })


def start_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing in Render environment variables.")
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing in Render environment variables.")
    if not shutil_which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed.")
    if not shutil_which("ffprobe"):
        raise RuntimeError("FFprobe is not installed.")
    load_state()
    threading.Thread(target=start_flask, daemon=True).start()
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("health", health_command))
    telegram_app.add_handler(CommandHandler("job", job_command))
    telegram_app.add_handler(CommandHandler("make", make_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_error_handler(telegram_error_handler)
    log.info("Starting ZIL V75 Telegram bot; model=%s; scenes=%s", GROQ_MODEL, SCENE_COUNT)
    # Keep only ONE running instance of this bot token. Conflict means another getUpdates poller exists.
    telegram_app.run_polling(drop_pending_updates=False, allowed_updates=["message"])


if __name__ == "__main__":
    main()
