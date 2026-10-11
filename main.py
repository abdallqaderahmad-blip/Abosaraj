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
from urllib.parse import urlparse, quote
from shutil import which

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL V79 FREE CINEMA FIXED
# Free-first | Resumable | Persistent checkpoints | No paid API
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

FREE_MODE = os.getenv("FREE_MODE", "true").lower() in ("1", "true", "yes", "on")
ALLOW_PAID_APIS = os.getenv("ALLOW_PAID_APIS", "false").lower() in ("1", "true", "yes", "on")

# Paid services remain blocked unless BOTH switches explicitly permit them.
FAL_KEY = os.getenv("FAL_KEY", "").strip() or os.getenv("FAL_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "pollinations").strip().lower()
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
FAL_QUEUE_URL = "https://queue.fal.run/"
FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell").strip()

PORT = int(os.getenv("PORT", "10000"))
SCENE_COUNT = 18
BASE_SCENE_DURATION = 5.0
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 20 * 1024 * 1024

GROQ_RETRIES = max(1, min(5, int(os.getenv("GROQ_RETRIES", "4"))))
GROQ_MAX_TOKENS = max(1500, int(os.getenv("GROQ_MAX_TOKENS", "3500")))
GROQ_MAX_WAIT = max(5, int(os.getenv("GROQ_MAX_WAIT", "60")))
IMAGE_GENERATION_TIMEOUT = max(45, int(os.getenv("IMAGE_GENERATION_TIMEOUT", "180")))
IMAGE_GENERATION_RETRIES = max(1, min(2, int(os.getenv("IMAGE_GENERATION_RETRIES", "1"))))

TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural").strip()
TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()
SUBTITLES_ENABLED = os.getenv("SUBTITLES_ENABLED", "true").lower() in ("1", "true", "yes", "on")
SCENE_TRANSITION_SECONDS = max(0.1, min(0.5, float(os.getenv("SCENE_TRANSITION_SECONDS", "0.25"))))

STORY_MUSIC_URL = os.getenv("STORY_MUSIC_URL", "").strip()
AI_MUSIC_ENABLED = os.getenv("AI_MUSIC_ENABLED", "false").lower() in ("1", "true", "yes", "on")
FAL_MUSIC_MODEL = os.getenv("FAL_MUSIC_MODEL", "fal-ai/ace-step").strip()

DATA_ROOT = Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
BASE_DIR = DATA_ROOT / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = BASE_DIR / "jobs_state.json"
CHARACTER_MEMORY_FILE = BASE_DIR / "characters_memory.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "zil.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ZIL")
app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-V79-Free-Cinema/7.9"})

JOBS = {}
JOBS_LOCK = threading.RLock()
ACTIVE_JOBS = 0

EFFECTS = {
    "mystery", "castle", "steps", "romance", "tension",
    "storm", "beast", "fight", "impact", "magic", "cliffhanger"
}


# =========================================================
# Persistent job state
# =========================================================

def save_state_locked():
    try:
        safe = {}
        for job_id, job in JOBS.items():
            safe[job_id] = {
                k: v for k, v in job.items()
                if k not in {"narration"}
            }

        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        temp.replace(STATE_FILE)
    except Exception:
        log.exception("Could not save state")


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
                job["error"] = "Service restarted before completion. Use /resume."

            JOBS[job_id] = job

    except Exception:
        log.exception("Could not load state")


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


def command_exists(name):
    return bool(which(name))


# =========================================================
# Utilities
# =========================================================

def run_command(command, timeout=240):
    result = subprocess.run(
        [str(x) for x in command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "Unknown error")[-2500:]
        raise RuntimeError(f"Command failed ({result.returncode}): {details}")

    return result


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError("AI did not return valid JSON")


def probe_duration(path):
    result = run_command([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], timeout=30)

    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return None


def has_audio_stream(path):
    result = run_command([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], timeout=30)

    return "audio" in result.stdout.lower()


def atomic_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def download_media(url, destination, maximum_bytes):
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Media URL must use HTTPS")

    total = 0

    with requests.get(
        url,
        stream=True,
        timeout=(20, 90),
        headers={"User-Agent": "ZIL-V79-Free-Cinema/7.9"},
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        if any(x in content_type for x in ("text/html", "application/json")):
            raise RuntimeError("URL did not return media")

        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)

                if total > maximum_bytes:
                    raise RuntimeError("Media exceeds size limit")

                output.write(chunk)

    if total < 5000:
        raise RuntimeError("Downloaded media is too small")

    return destination


# =========================================================
# Telegram Bot API helpers
# =========================================================

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": chat_id, "text": str(text)[:4000]},
        timeout=(15, 45),
    )
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {data}")


def telegram_send_video(chat_id, path, caption):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    with open(path, "rb") as video_file:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
            data={
                "chat_id": chat_id,
                "caption": str(caption)[:1000],
                "supports_streaming": "true",
            },
            files={
                "video": (Path(path).name, video_file, "video/mp4")
            },
            timeout=(30, 300),
        )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendVideo failed: {data}")


# =========================================================
# Groq with rate-limit backoff
# =========================================================

def groq_wait_seconds(response, attempt):
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except Exception:
        message = response.text or ""

    wait = None

    for pattern in (
        r"try again in\s+([0-9.]+)\s*s",
        r"wait\s+([0-9.]+)\s*s",
        r"retry after\s+([0-9.]+)",
    ):
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

    return max(1, wait + 1)


def groq_chat(messages, max_tokens=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing")

    last_error = "Unknown error"

    for attempt in range(GROQ_RETRIES):
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.25,
                    "max_tokens": max_tokens or GROQ_MAX_TOKENS,
                },
                timeout=(20, 120),
            )

            if response.status_code == 429:
                last_error = f"Groq rate limit: {response.text[:700]}"
                wait = groq_wait_seconds(response, attempt)

                log.warning(
                    "Groq rate limit, attempt %s/%s, wait %.1fs",
                    attempt + 1, GROQ_RETRIES, wait,
                )

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(wait + random.uniform(0.2, 0.8))
                    continue

                break

            if response.status_code >= 400:
                last_error = f"Groq HTTP {response.status_code}: {response.text[:700]}"

                if response.status_code in (400, 401, 403, 404):
                    raise RuntimeError(last_error)

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(min(4 * (attempt + 1), 12))
                    continue

                break

            data = response.json()
            choices = data.get("choices") or []

            if not choices:
                raise RuntimeError("Groq returned no choices")

            content = choices[0].get("message", {}).get("content", "")

            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                )

            if not str(content).strip():
                raise RuntimeError("Groq returned empty content")

            return str(content)

        except requests.RequestException as exc:
            last_error = f"Groq connection error: {exc}"

            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(4 * (attempt + 1), 12))

    raise RuntimeError("Groq failed after retries: " + last_error)


# =========================================================
# Persistent character memory
# =========================================================

def load_character_memory():
    try:
        data = json.loads(CHARACTER_MEMORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_character_memory(memory):
    try:
        atomic_json(CHARACTER_MEMORY_FILE, memory)
    except Exception:
        log.exception("Could not save character memory")


# =========================================================
# Story generation and validation
# =========================================================

def create_story_package(idea):
    memory = load_character_memory()
    memory_hint = json.dumps(memory, ensure_ascii=False)[:3000] if memory else "لا توجد شخصيات محفوظة."

    system_prompt = f"""
أنت كاتب سيناريو عربي محترف لدراما خيالية سينمائية.

أنشئ {SCENE_COUNT} مشهدًا بالضبط.
أرسل JSON صالحًا فقط بالمفاتيح:
title, story, visual_style, character_bible, scenes.

character_bible كائن JSON يربط أسماء الشخصيات بأوصاف ثابتة باللغة الإنجليزية:
العمر التقريبي، الوجه، الشعر، الملابس، العلامات المميزة.

الشخصيات المحفوظة:
{memory_hint}

كل مشهد يحتوي على:
narration: جملة عربية طبيعية ومشوقة.
visual_prompt: وصف إنجليزي لما يظهر في هذه اللحظة بالضبط.
effect: واحد من:
mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger.

قواعد:
- أحداث متتابعة وواضحة، وكل صورة تمثل لحظة مختلفة.
- اجعل الصورة تطابق السرد.
- ثبات وجوه الشخصيات وملابسها عبر المشاهد.
- لا تضع كلمات داخل الصورة.
- أسلوب photorealistic live-action fantasy.
- إضاءة سينمائية ووجوه بشرية واقعية.
- نهاية مشوقة تمهد للجزء التالي.
- لا تنهِ كل الأسرار.
- نوّع طول الجمل والإيقاع.
- تجنب الحشو.
"""

    user_prompt = (
        f"فكرة القصة أو الجزء التالي:\n{str(idea)[:MAX_STORY_LENGTH]}\n"
        f"أنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    )

    last_error = None

    for attempt in range(3):
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if attempt:
                messages.append({
                    "role": "user",
                    "content": f"صحح النتيجة وأرسل JSON صالحًا يحتوي {SCENE_COUNT} مشهدًا بالضبط.",
                })

            raw = groq_chat(messages, max_tokens=min(GROQ_MAX_TOKENS, 3000))
            data = extract_json(raw)
            scenes = data.get("scenes")

            if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
                raise ValueError(f"Expected {SCENE_COUNT} scenes")

            raw_bible = data.get("character_bible", {})

            if isinstance(raw_bible, dict):
                character_map = {
                    str(name)[:60]: re.sub(r"\s+", " ", str(description)).strip()[:300]
                    for name, description in raw_bible.items()
                    if str(name).strip() and str(description).strip()
                }
            else:
                character_map = {}

            clean_scenes = []

            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(f"Invalid scene {index + 1}")

                narration = re.sub(
                    r"\s+", " ", str(scene.get("narration", ""))
                ).strip()

                visual = re.sub(
                    r"\s+", " ", str(scene.get("visual_prompt", ""))
                ).strip()

                effect = str(scene.get("effect", "mystery")).lower().strip()

                if not narration:
                    raise ValueError(f"Scene {index + 1} has empty narration")

                if not visual:
                    visual = (
                        "Show the exact event described by this narration: "
                        + narration
                    )

                if effect not in EFFECTS:
                    effect = "mystery"

                clean_scenes.append({
                    "narration": narration[:260],
                    "visual_prompt": visual[:650],
                    "effect": effect,
                })

            total_words = word_count(
                " ".join(scene["narration"] for scene in clean_scenes)
            )

            if not 70 <= total_words <= 390:
                raise ValueError(f"Unexpected narration length: {total_words}")

            for name, description in character_map.items():
                if name.lower() not in {
                    "main characters", "characters", "الشخصيات"
                }:
                    memory[name] = description

            save_character_memory(memory)

            bible_text = "; ".join(
                f"{name}: {description}"
                for name, description in character_map.items()
            )

            return {
                "title": str(data.get("title", "حكاية ظل"))[:100],
                "story": str(data.get("story", idea))[:2000],
                "visual_style": str(
                    data.get(
                        "visual_style",
                        "photorealistic live-action fantasy, cinematic lighting",
                    )
                )[:350],
                "character_bible": bible_text[:1800],
                "character_map": character_map,
                "scenes": clean_scenes,
            }

        except Exception as exc:
            last_error = exc
            log.warning("Story attempt %s failed: %s", attempt + 1, exc)

            if attempt < 2:
                time.sleep(min(2 ** (attempt + 1), 8))

    raise RuntimeError(f"Could not create valid story: {last_error}")


# =========================================================
# Free-first image generation
# =========================================================

def build_scene_image_prompt(scene, package, index):
    return (
        f"Vertical 9:16 cinematic still, scene {index + 1} of one continuous drama. "
        f"STYLE: {package.get('visual_style', 'photorealistic live-action fantasy')}. "
        f"CHARACTER CONTINUITY: {package.get('character_bible', '')}. "
        f"EXACT SCENE ACTION: {scene.get('visual_prompt', '')}. "
        f"NARRATION CONTEXT: {scene.get('narration', '')}. "
        "Depict the action literally. Photorealistic human actors, realistic skin, "
        "natural facial expressions, anatomically correct hands, coherent medieval "
        "fantasy environment, dramatic cinematic lighting, high detail. "
        "No text, no letters, no subtitles, no logo, no watermark, no collage, "
        "no split screen, no unrelated objects."
    )[:3500]


def save_generated_image(url, destination):
    response = requests.get(
        url,
        stream=True,
        timeout=(20, 90),
        headers={"User-Agent": "ZIL-V79-Free-Cinema/7.9"},
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()

    if not content_type.startswith("image/"):
        raise RuntimeError(f"Non-image response: {content_type}")

    total = 0

    with open(destination, "wb") as output:
        for chunk in response.iter_content(256 * 1024):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_SOURCE_IMAGE_BYTES:
                raise RuntimeError("Image exceeds size limit")

            output.write(chunk)

    if total < 5000:
        raise RuntimeError("Image is too small")

    return destination


def pollinations_generate_image(prompt, destination, seed):
    encoded = quote(prompt, safe="")
    headers = {
        "User-Agent": "ZIL-V79-Free-Cinema/7.9",
        "Accept": "image/*",
    }

    url = POLLINATIONS_IMAGE_URL + encoded
    params = {
        "width": 768,
        "height": 1365,
        "seed": int(seed),
        "nologo": "true",
        "safe": "true",
        "model": "flux",
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=(20, IMAGE_GENERATION_TIMEOUT),
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Pollinations HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "").lower()

    if not content_type.startswith("image/") or len(response.content) < 5000:
        raise RuntimeError("Pollinations returned no valid image")

    Path(destination).write_bytes(response.content)
    return destination


def pixabay_generate_image(prompt, destination, index):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is not configured")

    match = re.search(
        r"EXACT SCENE ACTION:\s*(.*?)(?:NARRATION CONTEXT|Photorealistic human actors|$)",
        prompt,
        re.I | re.S,
    )

    query_text = match.group(1) if match else prompt
    query_text = re.sub(r"[^A-Za-z0-9 ,'-]", " ", query_text)

    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", query_text.lower())
    stop = {
        "the", "and", "with", "from", "into", "this", "that",
        "scene", "cinematic", "realistic", "fantasy", "show",
        "exact", "moment", "their", "while", "where",
    }

    useful = []

    for word in words:
        if word not in stop and word not in useful:
            useful.append(word)

        if len(useful) >= 7:
            break

    query = " ".join(useful) or "medieval castle dramatic portrait"

    response = requests.get(
        "https://pixabay.com/api/",
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "image_type": "photo",
            "orientation": "vertical",
            "safesearch": "true",
            "per_page": 15,
        },
        timeout=(15, 35),
    )
    response.raise_for_status()

    hits = response.json().get("hits") or []

    if not hits:
        raise RuntimeError(f"No Pixabay results for {query}")

    hit = hits[(index * 3) % len(hits)]
    image_url = hit.get("largeImageURL") or hit.get("webformatURL")

    if not image_url:
        raise RuntimeError("Pixabay image URL missing")

    return save_generated_image(image_url, destination)


def fal_generate_image(prompt, destination):
    if FREE_MODE or not ALLOW_PAID_APIS:
        raise RuntimeError("Paid Fal image calls are disabled")

    if not FAL_KEY:
        raise RuntimeError("FAL_KEY is missing")

    endpoint = FAL_QUEUE_URL.rstrip("/") + "/" + FAL_IMAGE_MODEL.lstrip("/")
    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        endpoint,
        headers=headers,
        json={
            "prompt": prompt,
            "image_size": "portrait_16_9",
            "num_images": 1,
            "enable_safety_checker": True,
        },
        timeout=(20, 90),
    )
    response.raise_for_status()

    data = response.json()
    images = data.get("images") or []

    if images:
        image_url = images[0].get("url")
    else:
        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")

        if not request_id or not status_url or not response_url:
            raise RuntimeError("Unexpected Fal response")

        deadline = time.time() + IMAGE_GENERATION_TIMEOUT
        image_url = None

        while time.time() < deadline:
            status_response = requests.get(
                status_url, headers=headers, timeout=(15, 45)
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            status = str(status_data.get("status", "")).upper()

            if status == "COMPLETED":
                result = requests.get(
                    response_url, headers=headers, timeout=(15, 60)
                )
                result.raise_for_status()
                images = result.json().get("images") or []
                image_url = images[0].get("url") if images else None
                break

            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Fal task {status.lower()}")

            time.sleep(3)

        if not image_url:
            raise RuntimeError("Fal image task timed out or returned no image")

    if urlparse(image_url).scheme != "https":
        raise RuntimeError("Invalid image URL")

    return save_generated_image(image_url, destination)


def generate_scene_image(prompt, destination, index):
    errors = []

    # Pixabay is a free stock-image fallback, if configured.
    if PIXABAY_API_KEY:
        try:
            return pixabay_generate_image(prompt, destination, index)
        except Exception as exc:
            errors.append(f"Pixabay: {exc}")
            log.warning("Pixabay failed: %s", exc)

    # Pollinations is best-effort; public availability is not guaranteed.
    for attempt in range(2):
        try:
            return pollinations_generate_image(
                prompt, destination, 7600 + index * 11 + attempt
            )
        except Exception as exc:
            errors.append(f"Pollinations: {exc}")
            log.warning("Pollinations failed for scene %s: %s", index + 1, exc)

            if attempt == 0:
                time.sleep(1.5)

    # Paid provider is deliberately blocked in the default free configuration.
    if not FREE_MODE and ALLOW_PAID_APIS and FAL_KEY and IMAGE_PROVIDER == "fal":
        try:
            return fal_generate_image(prompt, destination)
        except Exception as exc:
            errors.append(f"Fal: {exc}")

    raise RuntimeError("No image provider succeeded: " + "; ".join(errors[-3:]))


# =========================================================
# Video scene creation and fallback
# =========================================================

def animate_still_image(source, destination, duration, index):
    duration = max(1.0, float(duration))
    frames = max(1, int(duration * VIDEO_FPS))

    if index % 2:
        x_expr = "iw/2-(iw/zoom/2)+(iw/zoom/12)*sin(on/45)"
    else:
        x_expr = "iw/2-(iw/zoom/2)-(iw/zoom/12)*sin(on/45)"

    vf = (
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800,"
        f"zoompan=z='min(zoom+0.001,1.12)':x='{x_expr}':"
        f"y='ih/2-(ih/zoom/2)':d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"fps={VIDEO_FPS},trim=end_frame={frames},setpts=PTS-STARTPTS,"
        "setsar=1,format=yuv420p"
    )

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(source),
        "-vf", vf, "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "27", "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS), str(destination),
    ], 240)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Image animation failed")

    return destination


def create_fallback_visual(destination, duration, index):
    colors = [
        "0x172238", "0x28203D", "0x26343A",
        "0x38232B", "0x1D2A42",
    ]
    color = colors[index % len(colors)]

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"color=c={color}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}:d={duration}",
        "-vf", "noise=alls=8:allf=t+u,eq=contrast=1.15:saturation=1.25,vignette=PI/5,format=yuv420p",
        "-t", str(duration), "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", str(destination),
    ], 120)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Could not create fallback visual")

    return destination


def obtain_scene_visual(scene, package, workdir, index, duration):
    number = index + 1
    raw_image = workdir / f"ai_scene_{number}.jpg"
    normalized_image = workdir / f"ai_scene_{number}.png"
    normalized_video = workdir / f"visual_{number}.mp4"

    # Checkpoint: keep an existing valid generated image after a restart.
    if not normalized_image.exists() or normalized_image.stat().st_size < 5000:
        if not raw_image.exists() or raw_image.stat().st_size < 5000:
            prompt = build_scene_image_prompt(scene, package, index)
            generate_scene_image(prompt, raw_image, index)

        run_command([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_image), "-frames:v", "1",
            "-vf", "scale=1000:1800:force_original_aspect_ratio=increase,crop=1000:1800",
            str(normalized_image),
        ], 90)

        if not normalized_image.exists() or normalized_image.stat().st_size < 5000:
            raise RuntimeError(f"Image {number} could not be decoded")

    if not normalized_video.exists() or normalized_video.stat().st_size < 1000:
        animate_still_image(normalized_image, normalized_video, duration, index)

    return normalized_video, "ai_image"


# =========================================================
# Arabic subtitles
# =========================================================

def add_arabic_caption(source, caption, destination, workdir, index, duration):
    if not SUBTITLES_ENABLED:
        return source

    if destination.exists() and destination.stat().st_size > 1000:
        return destination

    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning("Subtitle libraries unavailable")
        return source

    try:
        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]

        font_path = next(
            (p for p in font_candidates if Path(p).exists()), None
        )

        font = ImageFont.truetype(font_path, 31) if font_path else ImageFont.load_default()

        overlay = Image.new("RGBA", (VIDEO_WIDTH, 170), (0, 0, 0, 190))
        draw = ImageDraw.Draw(overlay)

        words = (caption or "").split()
        lines = []
        current = ""

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

            draw.text(
                (x, 20 + line_index * 48),
                shaped,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 255),
            )

        png = workdir / f"caption_{index + 1}.png"
        overlay.save(png)

        run_command([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(png),
            "-filter_complex", "[0:v][1:v]overlay=0:H-170:format=auto[v]",
            "-map", "[v]", "-t", f"{duration:.3f}", "-an",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "27", "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS), str(destination),
        ], 180)

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception("Subtitle render failed; using original video")

    return source


# =========================================================
# Narration and audio
# =========================================================

def create_narration(text, destination):
    if destination.exists() and destination.stat().st_size > 1000:
        return destination

    destination.unlink(missing_ok=True)

    try:
        import edge_tts

        async def generate():
            communicate = edge_tts.Communicate(
                text, voice=TTS_VOICE, rate=TTS_RATE
            )
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

    raise RuntimeError("Arabic TTS failed")


def prepare_synced_voice(source, destination):
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af", "aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS",
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k",
        str(destination),
    ], 120)

    duration = probe_duration(destination)

    if not duration or duration <= 0:
        raise RuntimeError("Voice conversion failed")

    return duration


def fit_audio_to_duration(source, destination, duration):
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af",
        f"aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k",
        str(destination),
    ], 120)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Voice padding failed")

    return destination


def make_scene_sfx(kind, destination, duration):
    duration = max(1.0, float(duration))
    kind = str(kind or "mystery").lower()

    if kind in ("storm", "beast", "fight", "impact"):
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration:.3f}"
        af = "lowpass=f=260,highpass=f=35,tremolo=f=0.22:d=0.7,volume=0.22"
    elif kind in ("magic", "cliffhanger"):
        source = f"sine=frequency=420:sample_rate=44100:duration={duration:.3f}"
        af = "tremolo=f=4:d=0.65,lowpass=f=2200,volume=0.10"
    elif kind == "steps":
        source = f"anoisesrc=color=white:sample_rate=44100:duration={duration:.3f}"
        af = "highpass=f=650,lowpass=f=2800,tremolo=f=2.2:d=0.7,volume=0.09"
    elif kind == "romance":
        source = f"sine=frequency=196:sample_rate=44100:duration={duration:.3f}"
        af = "tremolo=f=0.35:d=0.3,lowpass=f=800,volume=0.06"
    else:
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration:.3f}"
        af = "lowpass=f=700,highpass=f=70,tremolo=f=0.15:d=0.4,volume=0.10"

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", source,
        "-af",
        f"{af},afade=t=in:d=0.25,"
        f"afade=t=out:st={max(0.1, duration-0.5):.3f}:d=0.45,"
        "aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo",
        "-t", f"{duration:.3f}", "-ar", "44100", "-ac", "2",
        "-c:a", "aac", "-b:a", "48k", str(destination),
    ], 120)

    if not destination.exists() or destination.stat().st_size < 500:
        raise RuntimeError("SFX generation failed")

    return destination


def prepare_background_music(destination, duration, workdir, package=None):
    # Free local soundtrack, with no external paid music API calls.
    music_filter = (
        "[0:a]volume=0.025,lowpass=f=180,afade=t=in:d=2[a0];"
        "[1:a]volume=0.018,lowpass=f=650,tremolo=f=0.12:d=0.25[a1];"
        "[2:a]volume=0.008,highpass=f=1800,lowpass=f=6500[a2];"
        "[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,"
        f"afade=t=in:d=2,afade=t=out:st={max(0.1, duration-4)}:d=4,"
        f"atrim=duration={duration:.3f},alimiter=limit=0.85[out]"
    )

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i",
        f"sine=frequency=82.41:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i",
        f"anoisesrc=color=pink:sample_rate=44100:duration={duration}",
        "-filter_complex", music_filter,
        "-map", "[out]", "-ar", "44100", "-ac", "2",
        "-c:a", "aac", "-b:a", "80k", str(destination),
    ], 240)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Could not create background music")

    return destination


# =========================================================
# Concatenation and mixing
# =========================================================

def write_concat_list(paths, destination):
    with open(destination, "w", encoding="utf-8") as output:
        for path in paths:
            resolved = Path(path).resolve().as_posix()

            if "'" in resolved:
                raise RuntimeError("Unsupported apostrophe in media path")

            output.write(f"file '{resolved}'\n")


def concatenate_video(paths, destination, workdir):
    if not paths:
        raise RuntimeError("No scene videos")

    if destination.exists() and destination.stat().st_size > 10000:
        return destination

    if len(paths) == 1:
        run_command([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(paths[0]), "-an",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "27", "-pix_fmt", "yuv420p",
            str(destination),
        ], 300)
        return destination

    # Reliable concat demuxer avoids expensive xfade processing on small servers.
    listing = workdir / "video_list.txt"
    write_concat_list(paths, listing)

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "27", "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS), "-movflags", "+faststart",
        str(destination),
    ], 900)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation failed")

    return destination


def concatenate_audio(paths, destination, workdir, label):
    if destination.exists() and destination.stat().st_size > 1000:
        return destination

    listing = workdir / f"{label}_list.txt"
    write_concat_list(paths, listing)

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vn", "-ar", "44100", "-ac", "2",
        "-c:a", "aac", "-b:a", "96k", str(destination),
    ], 600)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Audio concatenation failed: {label}")

    return destination


def mix_final_audio(video, voice, sfx, music, destination):
    if destination.exists() and destination.stat().st_size > 10000:
        return destination

    for label, path in {
        "video": video, "voice": voice, "sfx": sfx, "music": music
    }.items():
        if not Path(path).exists() or Path(path).stat().st_size < 500:
            raise RuntimeError(f"Missing mix input: {label}")

    duration = probe_duration(video)

    if not duration or duration <= 0:
        raise RuntimeError("Could not determine final video duration")

    graph = (
        "[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=1.0,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f}[v];"
        "[2:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.28,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f}[s];"
        "[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.16,"
        f"apad=whole_dur={duration:.3f},atrim=duration={duration:.3f}[m];"
        "[m][v]sidechaincompress=threshold=0.025:ratio=5:attack=25:release=300[ducked];"
        "[v][s][ducked]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,"
        "alimiter=limit=0.92,"
        f"atrim=duration={duration:.3f}[aout]"
    )

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(voice), "-i", str(sfx), "-i", str(music),
        "-filter_complex", graph,
        "-map", "0:v:0", "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(destination),
    ], 600)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Final mix failed")

    return destination


# =========================================================
# Main production pipeline with resumable checkpoints
# =========================================================

def create_video(job_id, idea):
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    def progress(stage, percent, message):
        update_job(
            job_id,
            stage=stage,
            progress=percent,
            message=message,
        )

        try:
            chat_id = JOBS.get(job_id, {}).get("chat_id")
            if chat_id:
                telegram_send_message(chat_id, f"{percent}% — {message}")
        except Exception:
            log.exception("Progress message failed")

    package_file = workdir / "story_package.json"
    package = None

    if package_file.exists():
        try:
            cached = json.loads(package_file.read_text(encoding="utf-8"))

            if (
                isinstance(cached, dict)
                and isinstance(cached.get("scenes"), list)
                and len(cached["scenes"]) == SCENE_COUNT
            ):
                package = cached

        except Exception:
            log.exception("Cached story package invalid")

    if package is None:
        progress("story_generation", 2, "كتابة القصة والتحقق منها...")
        package = create_story_package(idea)
        atomic_json(package_file, package)

    scenes = package["scenes"]

    update_job(
        job_id,
        title=package["title"],
        story=package["story"],
        scene_count=len(scenes),
    )

    video_paths = []
    voice_paths = []
    sfx_paths = []
    durations = []

    for index, scene in enumerate(scenes):
        number = index + 1

        progress(
            "scene_generation",
            int(5 + index / SCENE_COUNT * 65),
            f"تجهيز المشهد {number}/{SCENE_COUNT}",
        )

        raw_voice = workdir / f"voice_raw_{number}.mp3"
        converted_voice = workdir / f"voice_converted_{number}.m4a"
        aligned_voice = workdir / f"voice_{number}.m4a"

        if aligned_voice.exists() and aligned_voice.stat().st_size > 1000:
            scene_duration = probe_duration(aligned_voice) or BASE_SCENE_DURATION
        else:
            if not raw_voice.exists() or raw_voice.stat().st_size < 1000:
                create_narration(scene["narration"], raw_voice)

            if not converted_voice.exists() or converted_voice.stat().st_size < 1000:
                voice_duration = prepare_synced_voice(raw_voice, converted_voice)
            else:
                voice_duration = probe_duration(converted_voice) or BASE_SCENE_DURATION

            scene_duration = max(BASE_SCENE_DURATION, voice_duration + 0.20)
            fit_audio_to_duration(converted_voice, aligned_voice, scene_duration)

        durations.append(scene_duration)

        visual = workdir / f"visual_{number}.mp4"
        captioned = workdir / f"captioned_{number}.mp4"

        if SUBTITLES_ENABLED and captioned.exists() and captioned.stat().st_size > 1000:
            final_visual = captioned
        else:
            if not visual.exists() or visual.stat().st_size < 1000:
                visual, visual_type = obtain_scene_visual(
                    scene, package, workdir, index, scene_duration
                )
            else:
                visual_type = "cached_visual"

            final_visual = add_arabic_caption(
                visual,
                scene["narration"],
                captioned,
                workdir,
                index,
                scene_duration,
            ) if SUBTITLES_ENABLED else visual

        update_job(
            job_id,
            current_scene=number,
            current_visual=visual_type if "visual_type" in locals() else "cached_visual",
        )

        video_paths.append(final_visual)
        voice_paths.append(aligned_voice)

        sfx_file = workdir / f"sfx_{number}.m4a"

        if not sfx_file.exists() or sfx_file.stat().st_size < 1000:
            try:
                make_scene_sfx(scene["effect"], sfx_file, scene_duration)
            except Exception:
                log.exception("SFX failed; using mystery cue")
                make_scene_sfx("mystery", sfx_file, scene_duration)

        sfx_paths.append(sfx_file)

    progress("concatenating", 75, "تجميع المشاهد والصوت...")

    video_only = workdir / "video_only.mp4"
    voice_all = workdir / "voice_all.m4a"
    sfx_all = workdir / "sfx_all.m4a"
    music = workdir / "music.m4a"
    final_output = workdir / "ZIL_final.mp4"

    concatenate_video(video_paths, video_only, workdir)
    concatenate_audio(voice_paths, voice_all, workdir, "voice")
    concatenate_audio(sfx_paths, sfx_all, workdir, "sfx")

    total_duration = sum(durations)

    if not music.exists() or music.stat().st_size < 1000:
        prepare_background_music(music, total_duration, workdir, package)

    progress("audio_mix", 90, "موازنة الراوي مع الموسيقى والمؤثرات...")

    mix_final_audio(video_only, voice_all, sfx_all, music, final_output)

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        progress("compressing", 95, "ضغط الفيديو لإرساله عبر تيليغرام...")

        compressed = workdir / "ZIL_compressed.mp4"

        run_command([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(final_output),
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "32", "-vf", "scale=540:960",
            "-c:a", "aac", "-b:a", "80k",
            "-movflags", "+faststart", str(compressed),
        ], 600)

        if compressed.exists() and compressed.stat().st_size < final_output.stat().st_size:
            final_output = compressed

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        raise RuntimeError("Video exceeds Telegram size limit after compression")

    progress("completed", 100, "اكتمل الفيديو!")
    return final_output, package


# =========================================================
# Job worker
# =========================================================

def job_worker(job_id, chat_id, idea):
    global ACTIVE_JOBS

    try:
        update_job(
            job_id,
            status="running",
            stage="starting",
            progress=1,
            error="",
        )

        try:
            telegram_send_message(
                chat_id,
                f"🎬 بدأ إنتاج الفيديو.\nالعملية: {job_id}\n"
                f"عدد المشاهد: {SCENE_COUNT}\n"
                "يتم حفظ المشاهد المكتملة للاستفادة منها عند الاستئناف.",
            )
        except Exception:
            log.exception("Start notification failed")

        output, package = create_video(job_id, idea)

        update_job(job_id, status="uploading", stage="uploading", progress=98)

        telegram_send_message(
            chat_id,
            f"✅ اكتمل الفيديو: {package['title']}\nجارٍ إرساله..."
        )

        telegram_send_video(chat_id, output, f"🎬 {package['title']}\nظل ZIL")

        update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            output=str(output),
            title=package["title"],
            error="",
        )

        telegram_send_message(chat_id, f"🎉 انتهى الإنتاج بنجاح!\nالعملية: {job_id}")

    except Exception as exc:
        log.exception("Job failed: %s", job_id)

        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:2500],
        )

        try:
            telegram_send_message(
                chat_id,
                f"❌ تعذر إكمال الفيديو.\nالعملية: {job_id}\n"
                f"السبب: {str(exc)[:900]}\n\n"
                f"يمكنك تجربة /resume {job_id} للاستئناف.",
            )
        except Exception:
            log.exception("Failure notification failed")

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
        "🎬 أهلًا بك في ظل ZIL V79 FREE CINEMA\n\n"
        "أرسل فكرة أو قصة لإنتاج فيديو رأسي.\n\n"
        "/make فكرتك — إنشاء فيديو\n"
        "/test — تجربة قصة ثابتة\n"
        "/status — حالة العمليات\n"
        "/job رقم_العملية — تفاصيل عملية\n"
        "/resume [رقم_العملية] — استئناف مهمة\n"
        "/last_error — آخر خطأ\n"
        "/diagnose — تشخيص النظام\n"
        "/health — فحص الخدمات\n\n"
        f"المشروع يستهدف {SCENE_COUNT} مشهدًا مع سرد عربي ومؤثرات وموسيقى محلية. "
        "الوضع المجاني يمنع استدعاءات Fal المدفوعة."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    with JOBS_LOCK:
        jobs = list(JOBS.items())
        active = ACTIVE_JOBS

    lines = [
        "📊 حالة ظل ZIL V79",
        f"العمليات النشطة: {active}",
        f"Groq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}",
        f"وضع المجاني: {FREE_MODE}",
        "",
    ]

    for job_id, job in reversed(jobs[-6:]):
        lines.append(
            f"{job_id} | {job.get('status', '?')} | "
            f"{job.get('stage', '-')} | {job.get('progress', 0)}%"
        )

    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "🩺 فحص ظل ZIL\n"
        f"BOT_TOKEN: {'مهيأ' if BOT_TOKEN else 'مفقود'}\n"
        f"GROQ_API_KEY: {'مهيأ' if GROQ_API_KEY else 'مفقود'}\n"
        f"Pixabay: {'مهيأ' if PIXABAY_API_KEY else 'غير مهيأ'}\n"
        f"FFmpeg: {'جاهز' if command_exists('ffmpeg') else 'غير موجود'}\n"
        f"FFprobe: {'جاهز' if command_exists('ffprobe') else 'غير موجود'}\n"
        f"المهام النشطة: {active_jobs()}\n"
        f"مسار البيانات: {BASE_DIR}"
    )


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    if not context.args:
        await update.effective_message.reply_text("استخدم /job رقم_العملية")
        return

    job_id = context.args[0].strip()

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job = dict(job) if job else None

    if not job:
        await update.effective_message.reply_text("لم أجد هذه العملية.")
        return

    await update.effective_message.reply_text(
        f"العملية: {job_id}\n"
        f"الحالة: {job.get('status', '-')}\n"
        f"المرحلة: {job.get('stage', '-')}\n"
        f"التقدم: {job.get('progress', 0)}%\n"
        f"المشهد: {job.get('current_scene', '-')}\n"
        f"الخطأ: {job.get('error', 'لا يوجد')}"
    )


async def launch_job(update: Update, context: ContextTypes.DEFAULT_TYPE, idea):
    global ACTIVE_JOBS

    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return

    idea = (idea or "").strip()

    if len(idea) < 5:
        await update.effective_message.reply_text("اكتب فكرة أطول قليلًا.")
        return

    idea = idea[:MAX_STORY_LENGTH]

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.effective_message.reply_text(
                "⏳ يوجد إنتاج نشط. انتظر حتى ينتهي قبل بدء فيديو جديد."
            )
            return

        job_id = uuid.uuid4().hex[:10]
        ACTIVE_JOBS += 1

        JOBS[job_id] = {
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
            "chat_id": update.effective_chat.id,
            "user_id": update.effective_user.id,
            "idea": idea,
        }

        save_state_locked()

    await update.effective_message.reply_text(
        f"🚀 تم تسجيل طلبك.\nالعملية: {job_id}\nسأبدأ الإنتاج الآن."
    )

    worker = threading.Thread(
        target=job_worker,
        args=(job_id, update.effective_chat.id, idea),
        daemon=True,
    )

    try:
        worker.start()
    except Exception as exc:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

        update_job(
            job_id,
            status="failed",
            stage="thread_start_failed",
            error=str(exc),
        )

        log.exception("Could not start worker")


async def make_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = " ".join(context.args).strip()

    if not idea:
        if update.effective_message:
            await update.effective_message.reply_text(
                "استخدم:\n/make أمير غامض يواجه وحشًا لإنقاذ الأميرة"
            )
        return

    await launch_job(update, context, idea)


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = (
        "قصة خيالية سينمائية: رجل غامض يصل إلى مملكة قديمة، وتقع الأميرة في حبه. "
        "يرفض الملك العلاقة لأنه يخفي سرًا خطيرًا عن قوة الرجل. يقتحم وحش ضخم ساحة "
        "القصر، ويتظاهر البطل بالضعف قبل أن يكشف جزءًا من قوته الحقيقية. ينقذ الأميرة، "
        "لكنه يكتشف أن الوحش كان يهرب من خطر أكبر. تنتهي القصة بظهور علامة غامضة على ذراعه."
    )

    await launch_job(update, context, idea)


async def last_error_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    with JOBS_LOCK:
        failed = [
            (job_id, dict(job))
            for job_id, job in JOBS.items()
            if job.get("error")
        ]

    if not failed:
        await update.effective_message.reply_text("لا يوجد خطأ محفوظ.")
        return

    job_id, job = failed[-1]

    await update.effective_message.reply_text(
        f"آخر خطأ في ظل ZIL\n"
        f"العملية: {job_id}\n"
        f"المرحلة: {job.get('stage', '-')}\n"
        f"الخطأ:\n{str(job.get('error', 'غير معروف'))[:2500]}"
    )


async def diagnose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    with JOBS_LOCK:
        interrupted = sum(
            1 for job in JOBS.values()
            if job.get("status") == "interrupted"
        )

    lines = [
        "تشخيص ظل ZIL V79 FREE CINEMA",
        f"Python: {sys.version.split()[0]}",
        f"Groq key: {'OK' if GROQ_API_KEY else 'MISSING'}",
        f"Groq model: {GROQ_MODEL}",
        f"Fal configured: {bool(FAL_KEY)}",
        f"Pixabay configured: {bool(PIXABAY_API_KEY)}",
        f"Image provider: {IMAGE_PROVIDER}",
        f"Free mode: {FREE_MODE}",
        f"Paid APIs allowed: {ALLOW_PAID_APIS}",
        f"FFmpeg: {command_exists('ffmpeg')}",
        f"FFprobe: {command_exists('ffprobe')}",
        f"Scenes: {SCENE_COUNT}",
        f"Data directory: {BASE_DIR}",
        f"State file exists: {STATE_FILE.exists()}",
        f"Interrupted jobs: {interrupted}",
        "للاحتفاظ بالملفات بعد إعادة تشغيل Render، اربط Persistent Disk واضبط ZIL_DATA_DIR على مساره.",
    ]

    await update.effective_message.reply_text("\n".join(lines)[:3900])


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ACTIVE_JOBS

    if not update.effective_message:
        return

    requested_id = context.args[0].strip() if context.args else ""

    with JOBS_LOCK:
        if requested_id:
            candidates = [
                (requested_id, JOBS[requested_id])
            ] if requested_id in JOBS else []
        else:
            candidates = list(reversed(list(JOBS.items())))

        selected = None

        for job_id, job in candidates:
            if (
                job.get("status") in ("interrupted", "failed")
                and job.get("idea")
                and job.get("chat_id")
            ):
                selected = (job_id, dict(job))
                break

        if not selected:
            await update.effective_message.reply_text(
                "لم أجد مهمة متوقفة قابلة للاستئناف."
            )
            return

        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.effective_message.reply_text(
                "يوجد إنتاج نشط حاليًا؛ انتظر حتى ينتهي."
            )
            return

        job_id, job = selected
        ACTIVE_JOBS += 1

        JOBS[job_id].update(
            status="queued",
            stage="resuming",
            error="",
            updated_at=time.time(),
        )

        save_state_locked()

    await update.effective_message.reply_text(
        f"♻️ جارٍ استئناف العملية {job_id} من الملفات المحفوظة."
    )

    worker = threading.Thread(
        target=job_worker,
        args=(job_id, job["chat_id"], job["idea"]),
        daemon=True,
    )

    try:
        worker.start()
    except Exception as exc:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

        update_job(
            job_id,
            status="failed",
            stage="resume_thread_failed",
            error=str(exc),
        )


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error

    if isinstance(error, Conflict) or "conflict" in str(error).lower():
        log.error(
            "Telegram Conflict: another process may be polling with this token. "
            "Stop duplicate deployments/processes."
        )
    else:
        log.error(
            "Unhandled Telegram error",
            exc_info=(type(error), error, error.__traceback__) if error else None,
        )


# =========================================================
# Flask health endpoints
# =========================================================

@app.get("/")
def index():
    return jsonify({
        "service": "ZIL V79 FREE CINEMA",
        "status": "online",
        "active_jobs": active_jobs(),
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ZIL V79 FREE CINEMA",
        "groq_configured": bool(GROQ_API_KEY),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "image_provider": IMAGE_PROVIDER,
        "free_mode": FREE_MODE,
        "paid_apis_allowed": ALLOW_PAID_APIS,
        "ffmpeg_available": command_exists("ffmpeg"),
        "ffprobe_available": command_exists("ffprobe"),
        "active_jobs": active_jobs(),
    })


def start_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


# =========================================================
# Main
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing in Render environment variables")

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing in Render environment variables")

    if not command_exists("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed")

    if not command_exists("ffprobe"):
        raise RuntimeError("FFprobe is not installed")

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

    log.info(
        "Starting ZIL V79 FREE CINEMA; model=%s; scenes=%s; free=%s; paid=%s",
        GROQ_MODEL, SCENE_COUNT, FREE_MODE, ALLOW_PAID_APIS,
    )

    # Run exactly one Telegram polling process for this token.
    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()
