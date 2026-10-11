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
# ZIL V78 CLEAN CINEMA MERGED - AI scene images + resumable jobs + diagnostics
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
# AI image generation replaces random stock videos. FAL is preferred; Pollinations
# can be used as an optional fallback. No Pixabay media is used in this pipeline.
FAL_KEY = (os.getenv("FAL_KEY", "").strip() or os.getenv("FAL_API_KEY", "").strip())
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "auto").strip().lower()
FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
FAL_QUEUE_URL = "https://queue.fal.run/"
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
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
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_GENERATION_TIMEOUT = max(60, int(os.getenv("IMAGE_GENERATION_TIMEOUT", "240")))
IMAGE_GENERATION_RETRIES = max(1, min(2, int(os.getenv("IMAGE_GENERATION_RETRIES", "1"))))
GROQ_RETRIES = max(1, int(os.getenv("GROQ_RETRIES", "4")))
GROQ_MAX_WAIT = max(5, int(os.getenv("GROQ_MAX_WAIT", "60")))
# A smaller default reduces the chance of hitting Groq TPM limits.
GROQ_MAX_TOKENS = max(1200, int(os.getenv("GROQ_MAX_TOKENS", "3500")))
TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural").strip()
TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()
SUBTITLES_ENABLED = os.getenv("SUBTITLES_ENABLED", "false").lower() in ("1", "true", "yes", "on")
STORY_MUSIC_URL = os.getenv("STORY_MUSIC_URL", "").strip()
AI_MUSIC_ENABLED = os.getenv("AI_MUSIC_ENABLED", "true").lower() in ("1", "true", "yes", "on")
FAL_MUSIC_MODEL = os.getenv("FAL_MUSIC_MODEL", "fal-ai/ace-step").strip()
SCENE_TRANSITION_SECONDS = max(0.1, min(0.6, float(os.getenv("SCENE_TRANSITION_SECONDS", "0.35"))))

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
            safe[job_id] = {k: v for k, v in job.items() if k not in {"narration"}}
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
    # Respect provider-requested waits even when they exceed our fallback backoff cap.
    return max(1, wait + 1)


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
أنت كاتب سيناريو ومخرج فني لقصص عربية مشوقة. أنشئ {SCENE_COUNT} مشهدًا بالضبط لفيديو عمودي واقعي سينمائي.
أخرج JSON صالحًا فقط دون Markdown بالمفاتيح:
title, story, visual_style, character_bible, scenes.
visual_style: وصف إنجليزي قصير موحد للون والإضاءة والأسلوب الواقعي.
character_bible: وصف إنجليزي قصير ثابت للشخصيات الرئيسية (الوجه، الشعر، العمر التقريبي، الملابس، العلامات المميزة). اجعله موجزًا، ولا تضف شخصيات لا تحتاجها القصة.
كل مشهد يحتوي على narration (جملة عربية مشوقة من 12 إلى 18 كلمة تقريبًا، مناسبة لنحو 4 إلى 6 ثوانٍ من الإلقاء)، visual_prompt (وصف إنجليزي لما يحدث بصريًا في هذا المشهد فقط)، effect (نوع واحد).
الأنواع المسموحة: mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger.
قواعد إلزامية: تسلسل القصة واضح؛ كل visual_prompt يصف نفس الحدث الذي يقوله narration؛ لا تستخدم استعارات بصرية لا علاقة لها بالجملة؛ لا تضع نصوصًا أو شعارات أو كتابة داخل الصورة؛ صف مكان الحدث والشخصيات الظاهرة وحركتهم وتعبير الوجه وزاوية الكاميرا. كرر أوصاف الشخصيات الثابتة في character_bible ولا تغيّر الملابس والملامح بلا سبب قصصي. أسلوب photorealistic live-action fantasy, cinematic lighting, detailed faces, vertical composition, no text, no logo, no watermark.
ابدأ بخطاف قوي، صعّد الخطر تدريجيًا، واختم بتشويق للجزء التالي.
"""
    user_prompt = f"فكرة القصة:\n{str(idea)[:MAX_STORY_LENGTH]}\nأنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    last_error = None
    for attempt in range(2):
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        if attempt:
            messages.append({"role": "user", "content": f"أعد JSON صالحًا فقط، وتأكد من وجود {SCENE_COUNT} مشهدًا وvisual_style وcharacter_bible."})
        raw = groq_chat(messages)
        data = extract_json(raw)
        try:
            scenes = data.get("scenes")
            if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
                raise ValueError(f"Expected exactly {SCENE_COUNT} scenes.")
            visual_style = re.sub(r"\s+", " ", str(data.get("visual_style", "photorealistic live-action fantasy, cinematic lighting, realistic skin texture, vertical composition"))).strip()
            character_bible = data.get("character_bible", "")
            if isinstance(character_bible, dict):
                character_bible = "; ".join(f"{str(k)[:40]}: {str(v)[:180]}" for k, v in character_bible.items())
            character_bible = re.sub(r"\s+", " ", str(character_bible)).strip()
            if not character_bible:
                character_bible = "Keep the same main character faces, hair, clothing, age, and distinctive features across every scene."
            clean = []
            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(f"Invalid scene {index + 1}")
                narration = re.sub(r"\s+", " ", str(scene.get("narration", ""))).strip()
                visual = re.sub(r"\s+", " ", str(scene.get("visual_prompt", ""))).strip()
                effect = str(scene.get("effect", "mystery")).lower().strip()
                if not narration:
                    raise ValueError(f"Scene {index + 1} has no narration.")
                if not visual:
                    visual = f"Show the exact event described by this narration: {narration}."
                if effect not in EFFECTS:
                    effect = "mystery"
                clean.append({"narration": narration[:260], "visual_prompt": visual[:500], "effect": effect})
            total_words = word_count(" ".join(s["narration"] for s in clean))
            if not 150 <= total_words <= 420:
                raise ValueError(f"Unexpected narration length: {total_words}; expected 150-420 words for continuous, engaging narration.")
            return {
                "title": str(data.get("title", "حكاية ظل"))[:100],
                "story": str(data.get("story", idea))[:2000],
                "visual_style": visual_style[:350],
                "character_bible": character_bible[:900],
                "scenes": clean,
            }
        except Exception as exc:
            last_error = exc
            log.warning("Story validation attempt %s failed: %s", attempt + 1, exc)
    raise RuntimeError(f"Story validation failed: {last_error}")


# =========================================================
# AI image generation (no random stock footage)
# =========================================================

def build_scene_image_prompt(scene, package, index):
    """Tie the generated image directly to narration and keep character descriptions stable."""
    scene_description = str(scene.get("visual_prompt", "")).strip()
    narration = str(scene.get("narration", "")).strip()
    style = str(package.get("visual_style", "photorealistic live-action fantasy, cinematic lighting")).strip()
    bible = str(package.get("character_bible", "Keep the same character identity in every scene.")).strip()
    prompt = (
        f"Vertical 9:16 cinematic still for scene {index + 1} of one continuous fantasy drama. "
        f"VISUAL STYLE: {style}. CHARACTER CONTINUITY: {bible}. "
        f"EXACT SCENE ACTION: {scene_description}. "
        f"Narration context (depict this literally, do not write these words in the image): {narration}. "
        "Photorealistic live-action actors, anatomically correct hands, believable expressions, detailed natural skin, "
        "cinematic composition, coherent medieval fantasy setting, realistic practical lighting, high detail. "
        "No text, no letters, no subtitles, no logo, no watermark, no collage, no split screen, no infographic, "
        "no unrelated animals or objects, no modern objects unless explicitly required by the scene."
    )
    return prompt[:3500]


def fal_generate_image(prompt, destination):
    if not FAL_KEY:
        raise RuntimeError("FAL_KEY is not configured.")
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    endpoint = FAL_QUEUE_URL.rstrip("/") + "/" + FAL_IMAGE_MODEL.lstrip("/")
    response = requests.post(
        endpoint,
        headers=headers,
        json={"prompt": prompt, "image_size": "portrait_16_9", "num_images": 1, "enable_safety_checker": True},
        timeout=(20, 90),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Fal image request failed ({response.status_code}): {response.text[:700]}")
    data = response.json()
    if isinstance(data.get("images"), list) and data["images"]:
        image_url = data["images"][0].get("url")
    else:
        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")
        if not request_id or not status_url or not response_url:
            raise RuntimeError(f"Fal returned an unexpected response: {str(data)[:500]}")
        deadline = time.time() + IMAGE_GENERATION_TIMEOUT
        while time.time() < deadline:
            status_response = requests.get(status_url, headers=headers, timeout=(15, 45))
            status_response.raise_for_status()
            status_data = status_response.json()
            status = str(status_data.get("status", "")).upper()
            if status == "COMPLETED":
                result_response = requests.get(response_url, headers=headers, timeout=(15, 60))
                result_response.raise_for_status()
                result_data = result_response.json()
                images = result_data.get("images") or []
                image_url = images[0].get("url") if images else None
                break
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Fal image generation {status.lower()}: {str(status_data)[:500]}")
            time.sleep(2.5)
        else:
            raise TimeoutError("Fal image generation timed out.")
    if not image_url or urlparse(image_url).scheme != "https":
        raise RuntimeError("Fal did not return a valid HTTPS image URL.")
    save_generated_image(image_url, destination)
    return destination


def pollinations_generate_image(prompt, destination, seed):
    # Pollinations availability and quotas can change; this is only an optional fallback.
    from urllib.parse import quote
    url = POLLINATIONS_IMAGE_URL + quote(prompt, safe="")
    headers = {"User-Agent": "ZIL-Video-Bot/6.0"}
    if POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {POLLINATIONS_API_KEY}"
    response = requests.get(
        url,
        params={"width": 768, "height": 1365, "model": "flux", "seed": int(seed), "nologo": "true", "safe": "true"},
        headers=headers,
        timeout=(25, IMAGE_GENERATION_TIMEOUT),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Pollinations image request failed ({response.status_code}): {response.text[:400]}")
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/") or len(response.content) < 5000:
        raise RuntimeError("Pollinations did not return a valid image. Configure FAL_KEY or check provider availability.")
    Path(destination).write_bytes(response.content)
    return destination


def save_generated_image(url, destination):
    response = requests.get(url, stream=True, timeout=(20, 90), headers={"User-Agent": "ZIL-Video-Bot/6.0"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/"):
        raise RuntimeError(f"Image URL returned non-image content ({content_type or 'unknown'}).")
    total = 0
    with open(destination, "wb") as output:
        for chunk in response.iter_content(256 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_SOURCE_IMAGE_BYTES:
                raise RuntimeError("Generated image exceeds configured size limit.")
            output.write(chunk)
    if total < 5000:
        raise RuntimeError("Generated image is too small or invalid.")
    return destination


def generate_scene_image(prompt, destination, index):
    provider = IMAGE_PROVIDER
    last_error = None
    if provider in ("auto", "fal") and FAL_KEY:
        for attempt in range(IMAGE_GENERATION_RETRIES):
            try:
                return fal_generate_image(prompt, destination)
            except Exception as exc:
                last_error = exc
                log.warning("Fal image generation attempt %s failed for scene %s: %s", attempt + 1, index + 1, exc)
                if attempt + 1 < IMAGE_GENERATION_RETRIES:
                    time.sleep(min(3 * (attempt + 1), 8))
        if provider == "fal":
            raise RuntimeError(f"Fal image generation failed for scene {index + 1}: {last_error}")
    if provider in ("auto", "pollinations"):
        try:
            return pollinations_generate_image(prompt, destination, seed=7600 + index)
        except Exception as exc:
            last_error = exc
    if not FAL_KEY and not POLLINATIONS_API_KEY and provider == "fal":
        raise RuntimeError("IMAGE_PROVIDER=fal requires FAL_KEY in Render environment variables.")
    raise RuntimeError(
        "تعذر توليد صورة الذكاء الاصطناعي للمشهد " + str(index + 1) + ". "
        "تأكد من FAL_KEY أو توفر Pollinations. لم نستخدم Pixabay عشوائيًا حتى لا تتكرر مشكلة عدم تطابق الصورة مع الراوي. "
        + (f"التفاصيل: {last_error}" if last_error else "")
    )


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


def obtain_scene_visual(scene, package, workdir, index, duration):
    number = index + 1
    raw_image = workdir / f"ai_scene_{number}.jpg"
    normalized = workdir / f"visual_{number}.mp4"
    prompt = build_scene_image_prompt(scene, package, index)
    generate_scene_image(prompt, raw_image, index)
    # Re-encode to a predictable JPEG format; some image APIs return PNG/WebP bytes.
    normalized_image = workdir / f"ai_scene_{number}.png"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw_image), "-frames:v", "1", "-vf", "scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800", str(normalized_image)], 90)
    if not normalized_image.exists() or normalized_image.stat().st_size < 5000:
        raise RuntimeError(f"Generated image for scene {number} could not be decoded.")
    animate_still_image(normalized_image, normalized, duration, index)
    log.info("Scene %s: AI-generated image animated; provider=%s", number, IMAGE_PROVIDER)
    return normalized, "ai_image"


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
        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        font_path = next((candidate for candidate in font_candidates if Path(candidate).exists()), None)
        font = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default()
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
        for line_index, line in enumerate(lines):
            shaped = get_display(arabic_reshaper.reshape(line))
            box = draw.textbbox((0, 0), shaped, font=font)
            x = max(10, (VIDEO_WIDTH - (box[2] - box[0])) // 2)
            draw.text((x, 20 + line_index * 48), shaped, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 230))
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
    """Create a silent compatibility track: this version intentionally uses no synthetic SFX."""
    duration = max(1.0, float(duration))
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration:.3f}",
        "-t", f"{duration:.3f}", "-c:a", "aac", "-b:a", "32k", str(destination)
    ], 120)
    if not destination.exists() or destination.stat().st_size < 500:
        raise RuntimeError("Could not create silent compatibility audio track.")
    return destination


def generate_ai_background_music(destination, duration, workdir, package):
    """Ask Fal's ACE-Step endpoint for an instrumental score tailored to the story arc."""
    if not (AI_MUSIC_ENABLED and FAL_KEY):
        raise RuntimeError("AI music disabled or FAL_KEY missing.")
    story = re.sub(r"\s+", " ", str(package.get("story", ""))).strip()[:900]
    title = re.sub(r"\s+", " ", str(package.get("title", "fantasy drama"))).strip()[:100]
    effects = [str(scene.get("effect", "mystery")) for scene in package.get("scenes", [])]
    mood_map = {
        "romance": "brief emotional romantic strings", "mystery": "mysterious low strings and distant choir",
        "tension": "rising suspense pulses", "storm": "dark rumbling orchestral percussion",
        "beast": "massive ominous low brass", "fight": "urgent cinematic percussion",
        "impact": "dramatic orchestral hit", "magic": "ethereal shimmering choir",
        "cliffhanger": "unresolved suspenseful final chord", "castle": "grand medieval orchestral atmosphere",
        "steps": "subtle rhythmic tension"
    }
    arc = ", then ".join(mood_map.get(e, "dark cinematic strings") for e in effects[:8])
    prompt = (
        "Instrumental soundtrack only, no singing, no speech, no lyrics. Original high-quality cinematic fantasy drama score. "
        f"Story title: {title}. Story context: {story}. Emotional progression: {arc}. "
        "Epic medieval fantasy orchestra, deep cello ostinato, restrained low brass, emotional strings, atmospheric choir pads, "
        "subtle cinematic percussion, mysterious romantic tension, gradual build toward a suspenseful unresolved ending. "
        "Professional film score, coherent musical development, not a loop of one note, no sound effects, no vocals."
    )
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    endpoint = FAL_QUEUE_URL.rstrip("/") + "/" + FAL_MUSIC_MODEL.lstrip("/")
    payload = {"prompt": prompt, "duration": int(max(10, min(240, duration))), "audio_duration": int(max(10, min(240, duration))), "instrumental": True}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=(20, 90))
    if response.status_code >= 400:
        raise RuntimeError(f"Fal AI music request failed ({response.status_code}): {response.text[:500]}")
    data = response.json()

    def find_audio_url(obj):
        if isinstance(obj, dict):
            for key in ("audio", "audio_file", "audio_url", "file", "output"):
                value = obj.get(key)
                if isinstance(value, str) and value.startswith("https://") and any(x in key for x in ("audio", "file", "output")):
                    return value
                if isinstance(value, dict):
                    for subkey in ("url", "audio_url", "download_url"):
                        candidate = value.get(subkey)
                        if isinstance(candidate, str) and candidate.startswith("https://"):
                            return candidate
            for value in obj.values():
                found = find_audio_url(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = find_audio_url(value)
                if found:
                    return found
        return None

    audio_url = find_audio_url(data)
    if not audio_url:
        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")
        if request_id and status_url and response_url:
            deadline = time.time() + max(120, IMAGE_GENERATION_TIMEOUT)
            while time.time() < deadline:
                status_response = requests.get(status_url, headers=headers, timeout=(15, 45))
                status_response.raise_for_status()
                status_data = status_response.json()
                status = str(status_data.get("status", "")).upper()
                if status == "COMPLETED":
                    result_response = requests.get(response_url, headers=headers, timeout=(15, 60))
                    result_response.raise_for_status()
                    audio_url = find_audio_url(result_response.json())
                    break
                if status in ("FAILED", "CANCELLED"):
                    raise RuntimeError(f"Fal AI music generation {status.lower()}: {str(status_data)[:400]}")
                time.sleep(3)
    if not audio_url:
        raise RuntimeError("Fal music endpoint returned no recognizable audio URL.")
    source = workdir / "ai_music_source"
    download_media(audio_url, source, 40 * 1024 * 1024)
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vn", "-af", f"aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},afade=t=in:d=2,afade=t=out:st={max(0.1, duration-4)}:d=4,volume=0.18",
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)
    ], 300)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("AI-generated soundtrack could not be normalized.")
    return destination


def prepare_background_music(destination, duration, workdir, package=None):
    """Prefer story-aware AI instrumental music; use custom URL, then a layered cinematic fallback."""
    if STORY_MUSIC_URL:
        try:
            source = workdir / "custom_music_source.mp3"
            download_media(STORY_MUSIC_URL, source, 35 * 1024 * 1024)
            run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}", "-af", "volume=0.18,afade=t=in:d=2," + f"afade=t=out:st={max(0.1, duration-4)}:d=4", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination)], 240)
            if destination.exists() and destination.stat().st_size > 1000:
                return destination
        except Exception:
            log.exception("Custom music failed; trying story-aware AI music")
    try:
        return generate_ai_background_music(destination, duration, workdir, package or {})
    except Exception:
        log.exception("AI music generation failed; using generated cinematic fallback")
    # Local-only fallback: layered low drones, slow chord movement and filtered noise, not a single flat sine tone.
    music_filter = (
        "[0:a]volume=0.025,lowpass=f=180,afade=t=in:d=2[a0];"
        "[1:a]volume=0.018,lowpass=f=650,tremolo=f=0.12:d=0.25[a1];"
        "[2:a]volume=0.008,highpass=f=1800,lowpass=f=6500[a2];"
        "[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,"
        f"afade=t=in:d=2,afade=t=out:st={max(0.1, duration-4)}:d=4,atrim=duration={duration:.3f},alimiter=limit=0.85[out]"
    )
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=82.41:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=44100:duration={duration}",
        "-filter_complex", music_filter, "-map", "[out]", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "80k", str(destination)
    ], 240)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Could not create background music.")
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
    if not paths:
        raise RuntimeError("No scene videos to concatenate.")
    if len(paths) == 1:
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(paths[0]), "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 300)
        return destination
    durations = []
    for path in paths:
        d = probe_duration(path)
        if not d or d <= SCENE_TRANSITION_SECONDS + 0.1:
            raise RuntimeError(f"Invalid scene duration for transition: {path}")
        durations.append(d)
    transition = min(SCENE_TRANSITION_SECONDS, max(0.1, min(durations) / 5))
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in paths:
        command += ["-i", str(path)]
    filters = []
    for i in range(len(paths)):
        # Extend each scene except the final one by the transition length so xfade does not shorten the full story.
        extension = f",tpad=stop_mode=clone:stop_duration={transition:.3f}" if i < len(paths) - 1 else ""
        filters.append(f"[{i}:v]settb=1/{VIDEO_FPS},setpts=PTS-STARTPTS,fps={VIDEO_FPS},setsar=1,format=yuv420p{extension}[v{i}]")
    previous = "v0"
    elapsed = durations[0]
    for i in range(1, len(paths)):
        out_label = f"x{i}"
        filters.append(f"[{previous}][v{i}]xfade=transition=fade:duration={transition:.3f}:offset={elapsed:.3f}[{out_label}]")
        previous = out_label
        elapsed += durations[i]
    graph = ";".join(filters)
    command += ["-filter_complex", graph, "-map", f"[{previous}]", "-an", "-t", f"{sum(durations):.3f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)]
    run_command(command, 900)
    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation with smooth transitions failed.")
    actual = probe_duration(destination)
    if not actual or abs(actual - sum(durations)) > 1.0:
        log.warning("Transitioned video duration differs from expected: expected %.2f, actual %.2f", sum(durations), actual or 0)
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
        "[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,volume=0.16,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[m];"
        "[v][m]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,alimiter=limit=0.92,"
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

    package_file = workdir / "story_package.json"
    package = None
    if package_file.exists():
        try:
            cached = json.loads(package_file.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and isinstance(cached.get("scenes"), list) and len(cached["scenes"]) == SCENE_COUNT:
                package = cached
                log.info("Reusing cached story package for job %s", job_id)
        except Exception:
            log.exception("Cached story package is invalid; regenerating it")
    if package is None:
        package = create_story_package(idea)
        temp_package = package_file.with_suffix(".tmp")
        temp_package.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        temp_package.replace(package_file)
    scenes = package["scenes"]
    update_job(job_id, title=package["title"], story=package["story"], scene_count=len(scenes))
    video_paths, voice_paths, sfx_paths, durations = [], [], [], []

    for index, scene in enumerate(scenes):
        number = index + 1
        progress("scene_generation", int(5 + (index / SCENE_COUNT) * 65), f"تجهيز المشهد {number}/{SCENE_COUNT}")
        raw_voice = workdir / f"voice_raw_{number}.mp3"
        voice_converted = workdir / f"voice_converted_{number}.m4a"
        voice_aligned = workdir / f"voice_{number}.m4a"
        # Reuse existing valid narration assets when resuming a partially completed job.
        aligned_voice_is_cached = voice_aligned.exists() and voice_aligned.stat().st_size > 1000
        if aligned_voice_is_cached:
            # The aligned file already includes padding; don't add padding a second time on resume.
            scene_duration = max(BASE_SCENE_DURATION, probe_duration(voice_aligned) or BASE_SCENE_DURATION)
        else:
            if not (raw_voice.exists() and raw_voice.stat().st_size > 1000):
                create_narration(scene["narration"], raw_voice)
            if not (voice_converted.exists() and voice_converted.stat().st_size > 1000):
                voice_duration = prepare_synced_voice(raw_voice, voice_converted)
            else:
                voice_duration = probe_duration(voice_converted) or BASE_SCENE_DURATION
            scene_duration = max(BASE_SCENE_DURATION, voice_duration + 0.20)
            fit_audio_to_duration(voice_converted, voice_aligned, scene_duration)
        durations.append(scene_duration)

        visual = workdir / f"visual_{number}.mp4"
        raw_image = workdir / f"ai_scene_{number}.jpg"
        captioned = workdir / f"captioned_{number}.mp4"
        if SUBTITLES_ENABLED and captioned.exists() and captioned.stat().st_size > 1000:
            final_visual = captioned
            visual_type = "ai_image_cached_with_subtitles"
        elif visual.exists() and visual.stat().st_size > 1000:
            visual_type = "ai_image_cached"
            final_visual = add_arabic_caption(visual, scene["narration"], captioned, workdir, index, scene_duration) if SUBTITLES_ENABLED else visual
        else:
            visual, visual_type = obtain_scene_visual(scene, package, workdir, index, scene_duration)
            final_visual = add_arabic_caption(visual, scene["narration"], captioned, workdir, index, scene_duration)
        update_job(job_id, current_scene=number, current_visual=visual_type)
        video_paths.append(final_visual)
        voice_paths.append(voice_aligned)
        sfx_file = workdir / f"sfx_{number}.m4a"
        if not (sfx_file.exists() and sfx_file.stat().st_size > 1000):
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
    if not (final_music.exists() and final_music.stat().st_size > 1000):
        prepare_background_music(final_music, total_duration, workdir, package)
    progress("audio_mix", 90, "موازنة الراوي مع الموسيقى الدرامية...")
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
        "🎬 أهلًا بك في ظل ZIL V78 CLEAN CINEMA\n\nأرسل فكرة أو قصة لتحويلها إلى فيديو رأسي.\n\n"
        "الأوامر:\n/start - تشغيل البوت\n/make فكرتك - إنشاء فيديو\n/test - تجربة قصة ثابتة\n/status - حالة النظام والعمليات\n/job رقم_العملية - تفاصيل عملية\n/resume [رقم_العملية] - استئناف مهمة متوقفة\n/last_error - آخر خطأ\n/diagnose - تشخيص النظام\n/health - فحص الخدمات\n\n"
        f"الفيديو يستهدف {SCENE_COUNT} مشهدًا بصور مولدة بالذكاء الاصطناعي وانتقالات ناعمة، وسرد عربي متصل وموسيقى خلفية درامية. لا توجد ترجمة ظاهرة افتراضيًا."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    with JOBS_LOCK:
        jobs = list(JOBS.items())
        active = ACTIVE_JOBS
    if not jobs:
        await update.effective_message.reply_text(
            f"حالة ظل ZIL V76\nالعمليات النشطة: {active}\nGroq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}\nمولد الصور: {'Fal AI' if FAL_KEY else 'Pollinations (قد لا يتوفر دائمًا)'}\nFFmpeg: {'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}"
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
        f"FAL_KEY: {'مهيأ' if FAL_KEY else 'غير مهيأ'}\n"
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
            "chat_id": update.effective_chat.id, "user_id": update.effective_user.id, "idea": idea,
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



async def last_error_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    with JOBS_LOCK:
        failed = [(job_id, dict(job)) for job_id, job in JOBS.items() if job.get("error")]
    if not failed:
        await update.effective_message.reply_text("لا يوجد خطأ محفوظ. إذا حدثت إعادة تشغيل، افحص Runtime Logs في Render.")
        return
    job_id, job = failed[-1]
    await update.effective_message.reply_text(
        f"آخر خطأ في ظل ZIL\nالعملية: {job_id}\nالمرحلة: {job.get('stage', '-')}\nالخطأ:\n{str(job.get('error', 'غير معروف'))[:2500]}"
    )


async def diagnose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    with JOBS_LOCK:
        interrupted = sum(1 for j in JOBS.values() if j.get("status") == "interrupted")
    lines = [
        "تشخيص ظل ZIL V78 CLEAN CINEMA",
        f"Python: {sys.version.split()[0]}",
        f"Groq key: {'OK' if GROQ_API_KEY else 'MISSING'}",
        f"Groq model: {GROQ_MODEL}",
        f"Groq max tokens: {GROQ_MAX_TOKENS}",
        f"Fal key: {'OK' if FAL_KEY else 'MISSING'}",
        f"Image provider: {IMAGE_PROVIDER}",
        f"FFmpeg: {bool(shutil_which('ffmpeg'))}",
        f"FFprobe: {bool(shutil_which('ffprobe'))}",
        f"Scenes: {SCENE_COUNT}; base duration: {BASE_SCENE_DURATION}s each",
        f"Data directory: {BASE_DIR}",
        f"State file exists: {STATE_FILE.exists()}",
        f"Interrupted jobs: {interrupted}",
        "تنبيه: استخدم قرصًا دائمًا في Render واضبط ZIL_DATA_DIR لمسار القرص حتى تبقى الملفات بعد إعادة التشغيل.",
    ]
    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ACTIVE_JOBS
    if not update.effective_message:
        return
    requested_id = context.args[0].strip() if context.args else ""
    with JOBS_LOCK:
        candidates = []
        if requested_id:
            if requested_id in JOBS:
                candidates = [(requested_id, JOBS[requested_id])]
        else:
            candidates = list(reversed(list(JOBS.items())))
        selected = None
        for job_id, job in candidates:
            if job.get("status") in ("interrupted", "failed") and job.get("idea") and job.get("chat_id"):
                selected = (job_id, dict(job))
                break
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.effective_message.reply_text("يوجد إنتاج نشط حاليًا؛ انتظر حتى ينتهي.")
            return
        if not selected:
            await update.effective_message.reply_text("لم أجد مهمة متوقفة قابلة للاستئناف. يجب أن تحتوي بياناتها المحفوظة على الفكرة الأصلية.")
            return
        job_id, job = selected
        ACTIVE_JOBS += 1
        JOBS[job_id].update(status="queued", stage="resuming", error="", updated_at=time.time())
        save_state_locked()
    await update.effective_message.reply_text(
        f"♻️ جارٍ استئناف العملية {job_id}. ستُعاد الاستفادة من القصة والمشاهد المحفوظة حيثما أمكن."
    )
    threading.Thread(target=job_worker, args=(job_id, job["chat_id"], job["idea"]), daemon=True).start()


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
    return jsonify({"service": "ZIL V78 CLEAN CINEMA MERGED", "status": "online", "active_jobs": active_jobs()})


@app.get("/health")
def health():
    return jsonify({
        "status": "ok", "service": "ZIL V78 CLEAN CINEMA MERGED",
        "groq_configured": bool(GROQ_API_KEY),
        "fal_configured": bool(FAL_KEY),
        "image_provider": IMAGE_PROVIDER,
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
    telegram_app.add_handler(CommandHandler("resume", resume_command))
    telegram_app.add_handler(CommandHandler("last_error", last_error_command))
    telegram_app.add_handler(CommandHandler("diagnose", diagnose_command))
    telegram_app.add_handler(CommandHandler("make", make_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_error_handler(telegram_error_handler)
    log.info("Starting ZIL V78 CLEAN CINEMA MERGED bot; model=%s; scenes=%s; image_provider=%s; fal_configured=%s", GROQ_MODEL, SCENE_COUNT, IMAGE_PROVIDER, bool(FAL_KEY))
    # Keep only ONE running instance of this bot token. Conflict means another getUpdates poller exists.
    telegram_app.run_polling(drop_pending_updates=False, allowed_updates=["message"])


if __name__ == "__main__":
    main()
