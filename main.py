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
# ZIL V79 FREE CINEMA MERGED
# AI scene images + resumable jobs + diagnostics
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

FAL_KEY = (
    os.getenv("FAL_KEY", "").strip()
    or os.getenv("FAL_API_KEY", "").strip()
)

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

FREE_MODE = os.getenv(
    "FREE_MODE", "true"
).lower() in ("1", "true", "yes", "on")

ALLOW_PAID_APIS = os.getenv(
    "ALLOW_PAID_APIS", "false"
).lower() in ("1", "true", "yes", "on")

IMAGE_PROVIDER = os.getenv(
    "IMAGE_PROVIDER", "pollinations"
).strip().lower()

FAL_IMAGE_MODEL = os.getenv(
    "FAL_IMAGE_MODEL", "fal-ai/flux/schnell"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-20b"
).strip()

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

IMAGE_GENERATION_TIMEOUT = max(
    60, int(os.getenv("IMAGE_GENERATION_TIMEOUT", "240"))
)

IMAGE_GENERATION_RETRIES = max(
    1, min(2, int(os.getenv("IMAGE_GENERATION_RETRIES", "1")))
)

GROQ_RETRIES = max(
    1, int(os.getenv("GROQ_RETRIES", "4"))
)

GROQ_MAX_WAIT = max(
    5, int(os.getenv("GROQ_MAX_WAIT", "60"))
)

GROQ_MAX_TOKENS = max(
    1200, int(os.getenv("GROQ_MAX_TOKENS", "3500"))
)

TTS_VOICE = os.getenv(
    "TTS_VOICE", "ar-SA-HamedNeural"
).strip()

TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()

SUBTITLES_ENABLED = os.getenv(
    "SUBTITLES_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")

STORY_MUSIC_URL = os.getenv("STORY_MUSIC_URL", "").strip()

AI_MUSIC_ENABLED = os.getenv(
    "AI_MUSIC_ENABLED", "false"
).lower() in ("1", "true", "yes", "on")

FAL_MUSIC_MODEL = os.getenv(
    "FAL_MUSIC_MODEL", "fal-ai/ace-step"
).strip()

SCENE_TRANSITION_SECONDS = max(
    0.1,
    min(
        0.6,
        float(os.getenv("SCENE_TRANSITION_SECONDS", "0.35"))
    )
)

# =========================================================
# FIX: Define BASE_DIR before using it
# =========================================================

BASE_DIR = (
    Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
    / "zil_video_jobs"
)

BASE_DIR.mkdir(parents=True, exist_ok=True)

CHARACTER_MEMORY_FILE = BASE_DIR / "characters_memory.json"
STATE_FILE = BASE_DIR / "jobs_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            BASE_DIR / "zil.log",
            encoding="utf-8"
        ),
    ],
)

log = logging.getLogger("ZIL")
app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "ZIL-Video-Bot/5.0"
})

JOBS = {}
JOBS_LOCK = threading.RLock()
ACTIVE_JOBS = 0

EFFECTS = {
    "mystery",
    "castle",
    "steps",
    "romance",
    "tension",
    "storm",
    "beast",
    "fight",
    "impact",
    "magic",
    "cliffhanger",
}


# =========================================================
# State and job tracking
# =========================================================

def save_state_locked():
    try:
        safe = {}

        for job_id, job in JOBS.items():
            safe[job_id] = {
                k: v
                for k, v in job.items()
                if k not in {"narration"}
            }

        temp = STATE_FILE.with_suffix(".tmp")

        temp.write_text(
            json.dumps(safe, ensure_ascii=False),
            encoding="utf-8"
        )

        temp.replace(STATE_FILE)

    except Exception:
        log.exception("Could not save job state")


def load_state():
    if not STATE_FILE.exists():
        return

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return

        for job_id, job in data.items():
            if not isinstance(job, dict):
                continue

            if job.get("status") in (
                "running",
                "queued",
                "uploading"
            ):
                job["status"] = "interrupted"
                job["stage"] = "interrupted_after_restart"
                job["error"] = (
                    "The service restarted before the job finished."
                )

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
    result = subprocess.run(
        [str(x) for x in command],
        capture_output=True,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        details = (
            result.stderr
            or result.stdout
            or "Unknown error"
        )[-2500:]

        raise RuntimeError(
            f"Command failed ({result.returncode}): {details}"
        )

    return result


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def safe_query(text, maximum=120):
    text = re.sub(
        r"[^A-Za-z0-9 ,'-]",
        "",
        str(text or "")
    )

    return re.sub(r"\s+", " ", text).strip()[:maximum]


def extract_json(text):
    text = (text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.I
    ).strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError("AI did not return valid JSON.")


def probe_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], timeout=30)

    try:
        return float(result.stdout.strip())

    except (TypeError, ValueError):
        return None


def has_audio_stream(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], timeout=30)

    return "audio" in result.stdout.lower()


def download_media(url, destination, maximum_bytes):
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("Media URL must use HTTPS.")

    total = 0

    with requests.get(
        url,
        stream=True,
        timeout=(20, 90),
        headers={"User-Agent": "ZIL-Video-Bot/5.0"}
    ) as response:

        response.raise_for_status()

        content_type = response.headers.get(
            "content-type", ""
        ).lower()

        if any(
            bad in content_type
            for bad in ("text/html", "application/json")
        ):
            raise RuntimeError(
                "The URL did not return a media file."
            )

        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)

                if total > maximum_bytes:
                    raise RuntimeError(
                        "Media file exceeds the configured size limit."
                    )

                output.write(chunk)

    if total < 5000:
        raise RuntimeError("Downloaded media is too small.")

    return destination


# =========================================================
# Telegram notifications from worker threads
# =========================================================

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    response = requests.post(
        f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": str(text)[:4000]
        },
        timeout=(15, 45)
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram sendMessage failed: {data}"
        )


def telegram_send_video(chat_id, path, caption):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")

    with open(path, "rb") as video_file:
        response = requests.post(
            f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendVideo",
            data={
                "chat_id": chat_id,
                "caption": str(caption)[:1000],
                "supports_streaming": "true"
            },
            files={
                "video": (
                    Path(path).name,
                    video_file,
                    "video/mp4"
                )
            },
            timeout=(30, 240)
        )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram sendVideo failed: {data}"
        )


# =========================================================
# Groq API and rate-limit handling
# =========================================================

def groq_wait_seconds(response, attempt):
    try:
        message = str(
            response.json().get("error", {}).get("message", "")
        )

    except Exception:
        message = response.text or ""

    wait = None

    patterns = (
        r"try again in\s+([0-9.]+)\s*s",
        r"wait\s+([0-9.]+)\s*s",
        r"retry after\s+([0-9.]+)"
    )

    for pattern in patterns:
        match = re.search(pattern, message, re.I)

        if match:
            try:
                wait = float(match.group(1))
                break

            except ValueError:
                pass

    try:
        header_wait = float(
            response.headers.get("retry-after", "")
        )

        wait = max(wait or 0, header_wait)

    except (ValueError, TypeError):
        pass

    if wait is None:
        wait = min(
            8 * (2 ** attempt),
            GROQ_MAX_WAIT
        )

        return max(
            1,
            min(wait + 1, GROQ_MAX_WAIT)
        )

    return max(1, wait + 1)


def groq_chat(messages, max_tokens=None):
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing in Render."
        )

    last_error = "Unknown error"

    for attempt in range(GROQ_RETRIES):
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.25,
                    "max_tokens": max_tokens or GROQ_MAX_TOKENS
                },
                timeout=(20, 120)
            )

            if response.status_code == 429:
                last_error = (
                    f"Groq rate limit: {response.text[:800]}"
                )

                wait = groq_wait_seconds(response, attempt)

                log.warning(
                    "Groq rate limit; attempt %s/%s; waiting %.1fs",
                    attempt + 1,
                    GROQ_RETRIES,
                    wait
                )

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(
                        wait + random.uniform(0.2, 0.8)
                    )
                    continue

                break

            if response.status_code >= 400:
                last_error = (
                    f"Groq HTTP {response.status_code}: "
                    f"{response.text[:800]}"
                )

                if response.status_code in (400, 401, 403, 404):
                    raise RuntimeError(last_error)

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(
                        min(4 * (attempt + 1), 12)
                    )
                    continue

                break

            data = response.json()
            choices = data.get("choices") or []

            if not choices:
                raise RuntimeError(
                    "Groq returned no choices."
                )

            content = choices[0].get(
                "message", {}
            ).get("content", "")

            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                )

            if not str(content).strip():
                raise RuntimeError(
                    "Groq returned empty content."
                )

            return str(content)

        except requests.RequestException as exc:
            last_error = f"Groq connection error: {exc}"

            if attempt < GROQ_RETRIES - 1:
                time.sleep(
                    min(4 * (attempt + 1), 12)
                )

    raise RuntimeError(
        "Groq failed after retries. Last error: "
        + last_error
    )


# =========================================================
# Story generation and validation
# =========================================================

def load_character_memory():
    try:
        data = json.loads(
            CHARACTER_MEMORY_FILE.read_text(
                encoding="utf-8"
            )
        )

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def save_character_memory(memory):
    try:
        temp = CHARACTER_MEMORY_FILE.with_suffix(".tmp")

        temp.write_text(
            json.dumps(
                memory,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        temp.replace(CHARACTER_MEMORY_FILE)

    except Exception:
        log.exception(
            "Could not persist character memory"
        )


def create_story_package(idea):
    known_characters = load_character_memory()

    memory_hint = (
        json.dumps(known_characters, ensure_ascii=False)[:4500]
        if known_characters
        else "لا توجد شخصيات محفوظة بعد."
    )

    system_prompt = f"""
أنت كاتب سيناريو ومخرج فني لقصص عربية مشوقة ومناسبة لـ YouTube Shorts وInstagram Reels.
أنشئ {SCENE_COUNT} مشهدًا بالضبط لفيديو عمودي 9:16، بإيقاع سريع وفضول متصاعد، دون حشو.
أخرج JSON صالحًا فقط دون Markdown بالمفاتيح: title, story, visual_style, character_bible, scenes.
character_bible يجب أن يكون كائن JSON يربط اسم كل شخصية بوصف ثابت باللغة الإنجليزية: العمر التقريبي، الوجه، الشعر، الملابس، العلامات المميزة.
الشخصيات المحفوظة من أعمال سابقة (أعد استخدام أوصافها حرفيًا عند ظهور الشخصية نفسها، ولا تغيّر ملامحها أو ملابسها بلا سبب قصصي): {memory_hint}
كل مشهد يحتوي على narration (جملة عربية طبيعية مشوقة، قصيرة ومسموعة بوضوح)، visual_prompt (وصف إنجليزي دقيق لما يظهر في هذه اللحظة)، effect (واحد من: mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger).
قواعد إلزامية: التسلسل واضح، الصورة تطابق ما يقوله الراوي، وكل مشهد يمثل لحظة مختلفة من القصة. أظهر الشخصيات نفسها بملامح ثابتة عند عودتها. لا تكتب أي كلمات داخل الصورة. أسلوب photorealistic live-action fantasy, cinematic lighting, detailed natural faces, vertical composition, no text, no logo, no watermark.
اجعل السرد جذابًا وغير ممل: جمل قصيرة متغيرة الإيقاع، سؤال أو كشف أو خطر دوري، وخاتمة cliffhanger للجزء التالي دون إنهاء كل الأسرار.
"""

    user_prompt = (
        f"فكرة القصة أو الجزء التالي:\n"
        f"{str(idea)[:MAX_STORY_LENGTH]}\n"
        f"أنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    )

    last_error = None

    for attempt in range(
        max(2, min(3, GROQ_RETRIES))
    ):
        try:
            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]

            if attempt:
                messages.append({
                    "role": "user",
                    "content": (
                        "أعد المحاولة. أرسل JSON صالحًا فقط، "
                        f"{SCENE_COUNT} مشهدًا، "
                        "ولا تترك أي حقل فارغ."
                    )
                })

            raw = groq_chat(
                messages,
                max_tokens=min(GROQ_MAX_TOKENS, 2800)
            )

            data = extract_json(raw)
            scenes = data.get("scenes")

            if (
                not isinstance(scenes, list)
                or len(scenes) != SCENE_COUNT
            ):
                raise ValueError(
                    f"Expected exactly {SCENE_COUNT} scenes."
                )

            visual_style = re.sub(
                r"\s+",
                " ",
                str(
                    data.get(
                        "visual_style",
                        "photorealistic live-action fantasy, "
                        "cinematic lighting, realistic skin texture, "
                        "vertical composition"
                    )
                )
            ).strip()

            character_bible = data.get(
                "character_bible", {}
            )

            if isinstance(character_bible, dict):
                character_map = {
                    str(k)[:60]: re.sub(
                        r"\s+", " ", str(v)
                    ).strip()[:300]
                    for k, v in character_bible.items()
                    if str(k).strip() and str(v).strip()
                }

            else:
                character_map = {
                    "Main characters": re.sub(
                        r"\s+", " ", str(character_bible)
                    ).strip()[:900]
                }

            clean = []

            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(
                        f"Invalid scene {index + 1}"
                    )

                narration = re.sub(
                    r"\s+",
                    " ",
                    str(scene.get("narration", ""))
                ).strip()

                visual = re.sub(
                    r"\s+",
                    " ",
                    str(scene.get("visual_prompt", ""))
                ).strip()

                effect = str(
                    scene.get("effect", "mystery")
                ).lower().strip()

                if not narration:
                    raise ValueError(
                        f"Scene {index + 1} has no narration."
                    )

                if not visual:
                    visual = (
                        "Show the exact event described by "
                        f"this narration: {narration}."
                    )

                if effect not in EFFECTS:
                    effect = "mystery"

                clean.append({
                    "narration": narration[:260],
                    "visual_prompt": visual[:650],
                    "effect": effect
                })

            total_words = word_count(
                " ".join(
                    scene["narration"]
                    for scene in clean
                )
            )

            if not 90 <= total_words <= 390:
                raise ValueError(
                    f"Unexpected narration length: {total_words}; "
                    "expected 90-390 words."
                )

            memory = load_character_memory()

            for name, description in character_map.items():
                if name.lower() not in {
                    "main characters",
                    "characters",
                    "الشخصيات"
                }:
                    memory[name] = description

            save_character_memory(memory)

            bible_text = "; ".join(
                f"{name}: {desc}"
                for name, desc in character_map.items()
            )

            return {
                "title": str(
                    data.get("title", "حكاية ظل")
                )[:100],

                "story": str(
                    data.get("story", idea)
                )[:2000],

                "visual_style": visual_style[:350],

                "character_bible": (
                    bible_text
                    or "Keep same character faces, hair, "
                    "clothing and distinctive features across scenes."
                )[:1800],

                "character_map": character_map,
                "scenes": clean
            }

        except Exception as exc:
            last_error = exc

            log.warning(
                "Story generation/validation attempt %s failed: %s",
                attempt + 1,
                exc
            )

            if attempt < max(2, min(3, GROQ_RETRIES)) - 1:
                time.sleep(
                    min(2 ** (attempt + 1), 8)
                )

    raise RuntimeError(
        f"Could not create a valid story after retries: {last_error}"
    )


# =========================================================
# AI image generation
# =========================================================

def build_scene_image_prompt(scene, package, index):
    """Tie each image to narration and maintain character continuity."""

    scene_description = str(
        scene.get("visual_prompt", "")
    ).strip()

    narration = str(
        scene.get("narration", "")
    ).strip()

    style = str(
        package.get(
            "visual_style",
            "photorealistic live-action fantasy, cinematic lighting"
        )
    ).strip()

    bible = str(
        package.get(
            "character_bible",
            "Keep the same character identity in every scene."
        )
    ).strip()

    prompt = (
        f"Vertical 9:16 cinematic still for scene {index + 1} "
        "of one continuous fantasy drama. "
        f"VISUAL STYLE: {style}. "
        f"CHARACTER CONTINUITY: {bible}. "
        f"EXACT SCENE ACTION: {scene_description}. "
        "Narration context (depict this literally, do not write "
        f"these words in the image): {narration}. "
        "Photorealistic live-action actors, anatomically correct hands, "
        "believable expressions, detailed natural skin, cinematic "
        "composition, coherent medieval fantasy setting, realistic "
        "practical lighting, high detail. "
        "No text, no letters, no subtitles, no logo, no watermark, "
        "no collage, no split screen, no infographic, no unrelated "
        "animals or objects, no modern objects unless explicitly "
        "required by the scene."
    )

    return prompt[:3500]


def fal_generate_image(prompt, destination):
    if not FAL_KEY:
        raise RuntimeError("FAL_KEY is not configured.")

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json"
    }

    endpoint = (
        FAL_QUEUE_URL.rstrip("/")
        + "/"
        + FAL_IMAGE_MODEL.lstrip("/")
    )

    response = requests.post(
        endpoint,
        headers=headers,
        json={
            "prompt": prompt,
            "image_size": "portrait_16_9",
            "num_images": 1,
            "enable_safety_checker": True
        },
        timeout=(20, 90)
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Fal image request failed ({response.status_code}): "
            f"{response.text[:700]}"
        )

    data = response.json()

    if isinstance(data.get("images"), list) and data["images"]:
        image_url = data["images"][0].get("url")

    else:
        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")

        if not request_id or not status_url or not response_url:
            raise RuntimeError(
                f"Fal returned an unexpected response: {str(data)[:500]}"
            )

        deadline = time.time() + IMAGE_GENERATION_TIMEOUT

        while time.time() < deadline:
            status_response = requests.get(
                status_url,
                headers=headers,
                timeout=(15, 45)
            )

            status_response.raise_for_status()
            status_data = status_response.json()

            status = str(
                status_data.get("status", "")
            ).upper()

            if status == "COMPLETED":
                result_response = requests.get(
                    response_url,
                    headers=headers,
                    timeout=(15, 60)
                )

                result_response.raise_for_status()
                result_data = result_response.json()

                images = result_data.get("images") or []
                image_url = (
                    images[0].get("url")
                    if images else None
                )
                break

            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(
                    f"Fal image generation {status.lower()}: "
                    f"{str(status_data)[:500]}"
                )

            time.sleep(2.5)

        else:
            raise TimeoutError(
                "Fal image generation timed out."
            )

    if (
        not image_url
        or urlparse(image_url).scheme != "https"
    ):
        raise RuntimeError(
            "Fal did not return a valid HTTPS image URL."
        )

    save_generated_image(image_url, destination)

    return destination


def pollinations_generate_image(prompt, destination, seed):
    """Try public/free image endpoints only."""

    from urllib.parse import quote

    headers = {
        "User-Agent": "ZIL-Free-Image-Bot/1.0",
        "Accept": "image/*"
    }

    encoded = quote(prompt, safe="")

    attempts = [
        (
            "https://image.pollinations.ai/prompt/" + encoded,
            {
                "width": 768,
                "height": 1365,
                "seed": int(seed),
                "nologo": "true",
                "safe": "true",
                "model": "flux"
            }
        ),
        (
            "https://image.pollinations.ai/prompt/" + encoded,
            {
                "width": 768,
                "height": 1365,
                "seed": int(seed),
                "nologo": "true",
                "safe": "true"
            }
        )
    ]

    errors = []

    for url, params in attempts:
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=(20, IMAGE_GENERATION_TIMEOUT)
            )

            if response.status_code >= 400:
                errors.append(
                    f"HTTP {response.status_code}"
                )
                continue

            content_type = response.headers.get(
                "content-type", ""
            ).lower()

            if (
                content_type.startswith("image/")
                and len(response.content) >= 5000
            ):
                Path(destination).write_bytes(
                    response.content
                )
                return destination

            errors.append(
                f"non-image response ({content_type or 'unknown'})"
            )

        except Exception as exc:
            errors.append(str(exc)[:160])

    raise RuntimeError(
        "Public image generation unavailable: "
        + "; ".join(errors[-3:])
    )
    # =========================================================
# ZIL V79 - PART 2
# Image validation, narration, cinematic rendering,
# background jobs, Telegram commands and Flask health
# =========================================================

from PIL import Image, ImageOps, ImageEnhance, ImageFilter


# =========================================================
# Validate and save generated images
# =========================================================

def save_generated_image(url, destination):
    response = requests.get(
        url,
        timeout=(20, 90),
        headers={"User-Agent": "ZIL-Video-Bot/1.0"}
    )
    response.raise_for_status()

    if len(response.content) > MAX_SOURCE_IMAGE_BYTES:
        raise RuntimeError("Generated image exceeds size limit.")

    from io import BytesIO

    try:
        image = Image.open(BytesIO(response.content))
        image.verify()

        image = Image.open(BytesIO(response.content))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1200, 2000))

        if image.width < 256 or image.height < 256:
            raise RuntimeError("Generated image is too small.")

        image.save(destination, "JPEG", quality=90, optimize=True)

    except Exception as exc:
        raise RuntimeError(
            f"Invalid generated image: {exc}"
        ) from exc

    return destination


def generate_scene_image(scene, package, index, destination):
    prompt = build_scene_image_prompt(scene, package, index)

    errors = []

    # Free mode must never fall through to a paid provider.
    if FREE_MODE or not ALLOW_PAID_APIS:
        for attempt in range(2):
            try:
                return pollinations_generate_image(
                    prompt,
                    destination,
                    seed=10000 + index * 97 + attempt
                )
            except Exception as exc:
                errors.append(str(exc))
                log.warning(
                    "Free image attempt %s failed for scene %s: %s",
                    attempt + 1,
                    index + 1,
                    exc
                )
                time.sleep(1.5)

        raise RuntimeError(
            "Free image generation failed for scene "
            f"{index + 1}: {'; '.join(errors[-2:])}"
        )

    # Paid provider is reachable only when explicitly enabled.
    if IMAGE_PROVIDER == "fal" and ALLOW_PAID_APIS:
        return fal_generate_image(prompt, destination)

    raise RuntimeError(
        "No permitted image provider is available."
    )


# =========================================================
# Fallback image
# =========================================================

def create_fallback_visual(destination, index, title=""):
    """
    Create a cinematic abstract fallback only when needed.
    This is not an AI-generated story image.
    """
    width, height = 720, 1280

    base = Image.new(
        "RGB",
        (width, height),
        (12, 15, 27)
    )

    from PIL import ImageDraw

    draw = ImageDraw.Draw(base)

    rng = random.Random(index * 7919 + 101)

    for _ in range(100):
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        radius = rng.randint(2, 32)
        shade = rng.randint(18, 65)

        draw.ellipse(
            (
                x - radius,
                y - radius,
                x + radius,
                y + radius
            ),
            fill=(shade, shade, min(90, shade + 15))
        )

    base = base.filter(ImageFilter.GaussianBlur(18))
    base.save(destination, "JPEG", quality=85)

    return destination


# =========================================================
# Arabic narration with Edge TTS, then gTTS fallback
# =========================================================

def make_narration_audio(text, destination):
    import edge_tts

    async def edge_speech():
        communicate = edge_tts.Communicate(
            text=text,
            voice=TTS_VOICE,
            rate=TTS_RATE
        )
        await communicate.save(str(destination))

    try:
        asyncio.run(edge_speech())

        if (
            Path(destination).exists()
            and Path(destination).stat().st_size > 1000
        ):
            return destination

        raise RuntimeError("Edge TTS returned an empty audio file.")

    except Exception as edge_error:
        log.warning("Edge TTS failed: %s", edge_error)

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False
        ).save(str(destination))

        if (
            Path(destination).exists()
            and Path(destination).stat().st_size > 1000
        ):
            return destination

        raise RuntimeError("gTTS returned an empty audio file.")

    except Exception as gtts_error:
        raise RuntimeError(
            "Arabic narration generation failed. "
            f"Edge TTS: {edge_error if 'edge_error' in locals() else 'failed'}. "
            f"gTTS: {gtts_error}"
        ) from gtts_error


# =========================================================
# Audio utilities
# =========================================================

def create_silence_audio(destination, duration):
    run_command([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(max(1.0, duration)),
        "-c:a", "aac",
        "-b:a", "128k",
        str(destination)
    ], timeout=60)

    return destination


def fit_audio_duration(source, destination, duration):
    run_command([
        "ffmpeg", "-y",
        "-i", str(source),
        "-af",
        f"aresample=44100,apad,atrim=0:{duration}",
        "-t", str(duration),
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        str(destination)
    ], timeout=120)

    return destination


def create_ambient_audio(destination, duration):
    """
    Procedural ambient sound: does not require an external
    music service or paid API.
    """
    run_command([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i",
        "sine=frequency=72:sample_rate=44100",
        "-f", "lavfi",
        "-i",
        "anoisesrc=color=pink:amplitude=0.025:sample_rate=44100",
        "-filter_complex",
        (
            "[0:a]volume=0.025,lowpass=f=140[a0];"
            "[1:a]volume=0.22,lowpass=f=650[a1];"
            "[a0][a1]amix=inputs=2:duration=longest,"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(0.0, duration - 3):.2f}:d=3,"
            "volume=0.45[out]"
        ),
        "-map", "[out]",
        "-t", str(duration),
        "-c:a", "aac",
        "-b:a", "128k",
        str(destination)
    ], timeout=120)

    return destination


def create_effect_audio(effect, destination, duration=2.0):
    """
    Small procedural effects. These are synthetic sound effects,
    not recorded cinematic effects.
    """
    effect = str(effect or "mystery").lower()

    frequencies = {
        "mystery": 65,
        "castle": 95,
        "steps": 110,
        "romance": 330,
        "tension": 48,
        "storm": 75,
        "beast": 42,
        "fight": 90,
        "impact": 38,
        "magic": 440,
        "cliffhanger": 52
    }

    frequency = frequencies.get(effect, 65)

    run_command([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100",
        "-af",
        (
            "volume=0.30,"
            "aecho=0.8:0.7:80|160|280:0.30|0.18|0.08,"
            "afade=t=in:st=0:d=0.08,"
            f"afade=t=out:st={max(0.1, duration - 0.5):.2f}:d=0.5"
        ),
        "-t", str(duration),
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination)
    ], timeout=60)

    return destination


# =========================================================
# Subtitle creation
# =========================================================

def escape_subtitle_text(text):
    text = str(text or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("%", "\\%")
    text = text.replace("\n", " ")
    return text


def wrap_arabic_subtitle(text, maximum=32):
    words = str(text or "").split()
    lines = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()

        if len(candidate) > maximum and current:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    return "\n".join(lines)


def write_srt(scenes, destination, duration_per_scene):
    elapsed = 0.0

    def timestamp(seconds):
        milliseconds = int(round(seconds * 1000))
        hours = milliseconds // 3600000
        milliseconds %= 3600000
        minutes = milliseconds // 60000
        milliseconds %= 60000
        secs = milliseconds // 1000
        milliseconds %= 1000

        return (
            f"{hours:02}:{minutes:02}:"
            f"{secs:02},{milliseconds:03}"
        )

    entries = []

    for index, scene in enumerate(scenes, 1):
        start = elapsed
        end = elapsed + duration_per_scene
        elapsed = end

        text = wrap_arabic_subtitle(
            scene.get("narration", ""),
            maximum=32
        )

        entries.append(
            f"{index}\n"
            f"{timestamp(start)} --> {timestamp(end)}\n"
            f"{text}\n"
        )

    Path(destination).write_text(
        "\n".join(entries),
        encoding="utf-8"
    )

    return destination


# =========================================================
# Image animation
# =========================================================

def render_scene_clip(
    image_path,
    output_path,
    duration,
    index,
    subtitle_file=None
):
    frames = max(1, int(round(duration * VIDEO_FPS)))

    # Alternate slow zoom in/out for movement.
    if index % 2 == 0:
        zoom = (
            "scale=800:1422,"
            "zoompan=z='min(zoom+0.0007,1.10)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
            f"fps={VIDEO_FPS}"
        )
    else:
        zoom = (
            "scale=800:1422,"
            "zoompan=z='if(eq(on,1),1.10,max(zoom-0.0007,1.0))':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
            f"fps={VIDEO_FPS}"
        )

    filters = [
        zoom,
        "setsar=1",
        "format=yuv420p"
    ]

    command = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", ",".join(filters),
        "-r", str(VIDEO_FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ]

    run_command(command, timeout=180)

    return output_path


# =========================================================
# Build scene audio and combine narration
# =========================================================

def create_scene_audio(
    narration_text,
    effect,
    output_path,
    duration,
    work_dir,
    index
):
    voice_raw = work_dir / f"voice_raw_{index:02}.mp3"
    voice_fit = work_dir / f"voice_fit_{index:02}.m4a"
    effect_audio = work_dir / f"effect_{index:02}.m4a"
    silence = work_dir / f"silence_{index:02}.m4a"

    make_narration_audio(
        narration_text,
        voice_raw
    )

    fit_audio_duration(
        voice_raw,
        voice_fit,
        duration
    )

    try:
        create_effect_audio(
            effect,
            effect_audio,
            duration=min(duration, 2.0)
        )

        run_command([
            "ffmpeg", "-y",
            "-i", str(voice_fit),
            "-i", str(effect_audio),
            "-filter_complex",
            (
                "[0:a]volume=1.0[a0];"
                "[1:a]volume=0.15,apad[a1];"
                "[a0][a1]amix=inputs=2:"
                "duration=first:dropout_transition=1,"
                "loudnorm=I=-16:TP=-1.5:LRA=11[out]"
            ),
            "-map", "[out]",
            "-t", str(duration),
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path)
        ], timeout=120)

    except Exception as exc:
        log.warning(
            "Effect mixing failed for scene %s: %s",
            index + 1,
            exc
        )

        run_command([
            "ffmpeg", "-y",
            "-i", str(voice_fit),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-t", str(duration),
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path)
        ], timeout=120)

    return output_path


# =========================================================
# Render full vertical video
# =========================================================

def render_complete_video(
    package,
    job_id,
    work_dir,
    progress_callback=None
):
    scenes = package["scenes"]

    image_paths = []
    clip_paths = []
    audio_paths = []

    total_scenes = len(scenes)
    duration_per_scene = BASE_SCENE_DURATION

    for index, scene in enumerate(scenes):
        image_path = work_dir / f"scene_{index:02}.jpg"
        clip_path = work_dir / f"clip_{index:02}.mp4"
        audio_path = work_dir / f"audio_{index:02}.m4a"

        image_paths.append(image_path)
        clip_paths.append(clip_path)
        audio_paths.append(audio_path)

        if progress_callback:
            progress_callback(
                "images",
                int(index / total_scenes * 45),
                f"Generating image {index + 1}/{total_scenes}"
            )

        generate_scene_image(
            scene,
            package,
            index,
            image_path
        )

        render_scene_clip(
            image_path,
            clip_path,
            duration_per_scene,
            index
        )

        create_scene_audio(
            scene["narration"],
            scene.get("effect", "mystery"),
            audio_path,
            duration_per_scene,
            work_dir,
            index
        )

        if progress_callback:
            progress_callback(
                "scenes",
                45 + int((index + 1) / total_scenes * 35),
                f"Prepared scene {index + 1}/{total_scenes}"
            )

    # Concatenate video clips. All clips use identical dimensions/FPS.
    concat_file = work_dir / "concat.txt"

    concat_file.write_text(
        "\n".join(
            f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
            for path in clip_paths
        ),
        encoding="utf-8"
    )

    silent_video = work_dir / "silent_video.mp4"

    run_command([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        "-movflags", "+faststart",
        str(silent_video)
    ], timeout=600)

    if progress_callback:
        progress_callback(
            "audio_mix",
            84,
            "Combining narration and sound"
        )

    audio_concat = work_dir / "audio_concat.txt"

    audio_concat.write_text(
        "\n".join(
            f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
            for path in audio_paths
        ),
        encoding="utf-8"
    )

    narration_track = work_dir / "narration_track.m4a"

    run_command([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(audio_concat),
        "-c:a", "aac",
        "-b:a", "128k",
        str(narration_track)
    ], timeout=300)

    ambient_track = work_dir / "ambient.m4a"
    create_ambient_audio(
        ambient_track,
        total_scenes * duration_per_scene
    )

    final_video = work_dir / "final_video.mp4"

    run_command([
        "ffmpeg", "-y",
        "-i", str(silent_video),
        "-i", str(narration_track),
        "-i", str(ambient_track),
        "-filter_complex",
        (
            "[1:a]volume=1.0[narration];"
            "[2:a]volume=0.12[ambient];"
            "[narration][ambient]amix=inputs=2:"
            "duration=first:dropout_transition=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=11[audio]"
        ),
        "-map", "0:v:0",
        "-map", "[audio]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        str(final_video)
    ], timeout=600)

    if not final_video.exists() or final_video.stat().st_size < 50000:
        raise RuntimeError("Final video is missing or too small.")

    if progress_callback:
        progress_callback(
            "complete",
            100,
            "Video rendered"
        )

    return final_video


# =========================================================
# Job runner
# =========================================================

def run_job(job_id, chat_id, story_idea):
    global ACTIVE_JOBS

    with JOBS_LOCK:
        ACTIVE_JOBS += 1

    work_dir = BASE_DIR / f"job_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    def progress(stage, percent, message):
        update_job(
            job_id,
            stage=stage,
            progress=percent,
            message=message
        )

        try:
            telegram_send_message(
                chat_id,
                f"🎬 ظل ZIL\n{message}\nالتقدم: {percent}%"
            )
        except Exception:
            log.warning("Could not send progress update.")

    try:
        update_job(
            job_id,
            status="running",
            stage="story",
            progress=2,
            started_at=time.time(),
            error=None
        )

        progress(
            "story",
            3,
            "بدأت كتابة القصة وتجهيز المشاهد."
        )

        package = create_story_package(story_idea)

        update_job(
            job_id,
            title=package["title"],
            scene_count=len(package["scenes"])
        )

        progress(
            "story_ready",
            5,
            f"اكتملت القصة: {package['title']}"
        )

        final_video = render_complete_video(
            package,
            job_id,
            work_dir,
            progress_callback=progress
        )

        update_job(
            job_id,
            status="uploading",
            stage="uploading",
            progress=98,
            output_path=str(final_video),
            duration=probe_duration(final_video),
            size_bytes=final_video.stat().st_size
        )

        telegram_send_video(
            chat_id,
            final_video,
            f"🎬 {package['title']}\n"
            "صناعة ظل ZIL — نسخة تجريبية"
        )

        update_job(
            job_id,
            status="completed",
            stage="complete",
            progress=100,
            finished_at=time.time(),
            error=None
        )

        telegram_send_message(
            chat_id,
            "✅ اكتمل الفيديو وتم إرساله.\n"
            f"رقم العملية: {job_id}"
        )

    except Exception as exc:
        log.exception("Job %s failed", job_id)

        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:2500],
            finished_at=time.time()
        )

        try:
            telegram_send_message(
                chat_id,
                "❌ تعذّر إكمال الفيديو.\n"
                f"العملية: {job_id}\n"
                f"المرحلة: {JOBS.get(job_id, {}).get('stage', 'unknown')}\n"
                f"الخطأ: {str(exc)[:2500]}\n\n"
                "استخدم /last_error لمعرفة آخر خطأ."
            )
        except Exception:
            log.exception("Could not notify user of failure.")

    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)


# =========================================================
# Start a job
# =========================================================

def create_job(chat_id, idea):
    if not idea or not idea.strip():
        raise ValueError("أرسل فكرة القصة أولًا.")

    if len(idea) > MAX_STORY_LENGTH:
        raise ValueError(
            f"الفكرة طويلة جدًا. الحد {MAX_STORY_LENGTH} حرف."
        )

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            raise RuntimeError(
                "هناك عملية صناعة فيديو تعمل حاليًا. "
                "انتظر حتى تنتهي."
            )

        job_id = uuid.uuid4().hex[:10]

        JOBS[job_id] = {
            "job_id": job_id,
            "chat_id": chat_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "idea": idea,
            "created_at": time.time(),
            "updated_at": time.time(),
            "error": None
        }

        save_state_locked()

    thread = threading.Thread(
        target=run_job,
        args=(job_id, chat_id, idea),
        daemon=True,
        name=f"ZIL-{job_id}"
    )

    thread.start()

    return job_id


# =========================================================
# Telegram commands
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🎬 أهلًا بك في ظل ZIL V79 FREE CINEMA\n\n"
        "أرسل فكرة قصتك لأحوّلها إلى فيديو عمودي "
        "بصور متحركة وسرد عربي.\n\n"
        "الأوامر:\n"
        "/make فكرة القصة — إنشاء فيديو\n"
        "/test — تجربة قصة ثابتة\n"
        "/status — حالة النظام\n"
        "/job — حالة آخر عملية\n"
        "/last_error — آخر خطأ\n"
        "/diagnose — فحص الإعدادات\n"
        "/health — فحص الخدمة\n\n"
        "ملاحظة: توليد الصور المجاني يعتمد على توفر "
        "الخدمة الخارجية، ولا يمكن ضمان أنه غير محدود."
    )

    await update.effective_message.reply_text(message)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ffmpeg_ok = bool(shutil_which("ffmpeg"))
    ffprobe_ok = bool(shutil_which("ffprobe"))

    message = (
        "🔎 فحص ظل ZIL\n"
        f"FFmpeg: {'جاهز' if ffmpeg_ok else 'غير موجود'}\n"
        f"FFprobe: {'جاهز' if ffprobe_ok else 'غير موجود'}\n"
        f"Groq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}\n"
        f"Telegram: {'مهيأ' if BOT_TOKEN else 'غير مهيأ'}\n"
        f"وضع مجاني: {'مفعل' if FREE_MODE else 'غير مفعل'}\n"
        f"واجهات مدفوعة مسموحة: {'نعم' if ALLOW_PAID_APIS else 'لا'}\n"
        f"العمليات النشطة: {active_jobs()}"
    )

    await update.effective_message.reply_text(message)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "📊 حالة ظل ZIL V79\n"
        f"العمليات النشطة: {active_jobs()}\n"
        f"Groq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}\n"
        f"FFmpeg: {'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}\n"
        f"وضع مجاني: {'نعم' if FREE_MODE else 'لا'}\n"
        f"واجهات مدفوعة: {'مسموحة' if ALLOW_PAID_APIS else 'ممنوعة'}"
    )

    await update.effective_message.reply_text(message)


async def make_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = " ".join(context.args).strip()

    if not idea:
        await update.effective_message.reply_text(
            "استخدم الأمر هكذا:\n"
            "/make أمير غامض يخفي قوة خارقة داخل قصر الملك"
        )
        return

    try:
        job_id = create_job(
            update.effective_chat.id,
            idea
        )

        await update.effective_message.reply_text(
            "🚀 بدأت صناعة الفيديو.\n"
            f"رقم العملية: {job_id}\n"
            "سأرسل تحديثات التقدم هنا."
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            f"⚠️ {str(exc)}"
        )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    test_story = (
        "في مملكة بعيدة، وصل رجل غامض إلى بوابة القصر. "
        "ضحك الحراس عندما رأوا ملابسه البسيطة، لكن الأميرة "
        "لاحظت أن الذئب الأبيض الصغير الذي يرافقه لا يخاف أحدًا. "
        "أمر الملك الحراس بإبعاده، ثم ظهر وحش ضخم عند بوابة المدينة. "
        "هرب الجنود، بينما وقف الرجل أمام الوحش بهدوء. "
        "رفع يده، فتوقفت الأرض عن الاهتزاز. "
        "نظر الملك إليه بصدمة، ثم همس: مستحيل... لقد عاد. "
        "لكن الرجل أخفى قوته من جديد، لأن سرًا أخطر كان ينتظره "
        "داخل القصر."
    )

    try:
        job_id = create_job(
            update.effective_chat.id,
            test_story
        )

        await update.effective_message.reply_text(
            "🧪 بدأت تجربة ظل ZIL.\n"
            f"رقم العملية: {job_id}\n"
            "ستُنشأ القصة والمشاهد ثم يبدأ توليد الصور."
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            f"⚠️ {str(exc)}"
        )


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with JOBS_LOCK:
        matching = [
            job.copy()
            for job in JOBS.values()
            if str(job.get("chat_id")) == str(chat_id)
        ]

    if not matching:
        await update.effective_message.reply_text(
            "لا توجد عمليات محفوظة لهذه المحادثة."
        )
        return

    matching.sort(
        key=lambda job: job.get("created_at", 0),
        reverse=True
    )

    job = matching[0]

    await update.effective_message.reply_text(
        f"📋 حالة العملية {job.get('job_id')}\n"
        f"الحالة: {job.get('status')}\n"
        f"المرحلة: {job.get('stage')}\n"
        f"التقدم: {job.get('progress', 0)}%\n"
        f"الرسالة: {job.get('message', '')}\n"
        f"الخطأ: {job.get('error') or 'لا يوجد'}"
    )


async def last_error_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with JOBS_LOCK:
        matching = [
            job.copy()
            for job in JOBS.values()
            if str(job.get("chat_id")) == str(chat_id)
            and job.get("error")
        ]

    if not matching:
        await update.effective_message.reply_text(
            "لا يوجد خطأ محفوظ لهذه المحادثة."
        )
        return

    matching.sort(
        key=lambda job: job.get("updated_at", 0),
        reverse=True
    )

    job = matching[0]

    await update.effective_message.reply_text(
        f"❌ آخر خطأ في ظل ZIL\n"
        f"العملية: {job.get('job_id')}\n"
        f"المرحلة: {job.get('stage')}\n"
        f"الخطأ:\n{str(job.get('error'))[:3000]}"
    )


async def diagnose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    image_provider = (
        "Pollinations (مجاني)"
        if FREE_MODE or not ALLOW_PAID_APIS
        else IMAGE_PROVIDER
    )

    text = (
        "🩺 تشخيص ظل ZIL V79\n"
        f"Groq API: {'مهيأ' if GROQ_API_KEY else 'ناقص'}\n"
        f"Telegram token: {'مهيأ' if BOT_TOKEN else 'ناقص'}\n"
        f"FFmpeg: {'جاهز' if shutil_which('ffmpeg') else 'ناقص'}\n"
        f"FFprobe: {'جاهز' if shutil_which('ffprobe') else 'ناقص'}\n"
        f"مزود الصور: {image_provider}\n"
        f"الوضع المجاني: {FREE_MODE}\n"
        f"السماح بالمدفوع: {ALLOW_PAID_APIS}\n"
        f"عدد المشاهد: {SCENE_COUNT}\n"
        f"المدة الأساسية لكل مشهد: {BASE_SCENE_DURATION} ثوانٍ\n"
        f"مجلد البيانات: {BASE_DIR}\n"
        f"العمليات النشطة: {active_jobs()}"
    )

    await update.effective_message.reply_text(text)


async def unknown_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()

    if text.startswith("/"):
        return

    try:
        job_id = create_job(
            update.effective_chat.id,
            text
        )

        await update.effective_message.reply_text(
            "🎬 استلمت فكرتك وبدأت تجهيز الفيديو.\n"
            f"رقم العملية: {job_id}"
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            f"⚠️ لم أستطع بدء العملية: {str(exc)}"
        )


# =========================================================
# Flask endpoints
# =========================================================

@app.get("/")
def index():
    return jsonify({
        "service": "ZIL V79 FREE CINEMA",
        "status": "online",
        "active_jobs": active_jobs(),
        "free_mode": FREE_MODE
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ZIL V79 FREE CINEMA",
        "ffmpeg": bool(shutil_which("ffmpeg")),
        "ffprobe": bool(shutil_which("ffprobe")),
        "groq_configured": bool(GROQ_API_KEY),
        "telegram_configured": bool(BOT_TOKEN),
        "free_mode": FREE_MODE,
        "paid_apis_allowed": ALLOW_PAID_APIS,
        "active_jobs": active_jobs(),
        "timestamp": time.time()
    })


@app.get("/status")
def http_status():
    with JOBS_LOCK:
        jobs = [
            {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "stage": job.get("stage"),
                "progress": job.get("progress", 0)
            }
            for job in JOBS.values()
        ]

    return jsonify({
        "active_jobs": active_jobs(),
        "jobs": jobs[-20:]
    })


# =========================================================
# Startup
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add it in Render Environment."
        )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing. Add it in Render Environment."
        )

    if not shutil_which("ffmpeg"):
        raise RuntimeError(
            "FFmpeg is missing. Check the Dockerfile."
        )

    if not shutil_which("ffprobe"):
        raise RuntimeError(
            "FFprobe is missing. Check the Dockerfile."
        )

    load_state()

    # Run the web health endpoint in a separate thread.
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=PORT,
            debug=False,
            use_reloader=False
        ),
        daemon=True,
        name="ZIL-Flask"
    )

    flask_thread.start()

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )
    telegram_app.add_handler(
        CommandHandler("health", health_command)
    )
    telegram_app.add_handler(
        CommandHandler("status", status_command)
    )
    telegram_app.add_handler(
        CommandHandler("make", make_command)
    )
    telegram_app.add_handler(
        CommandHandler("test", test_command)
    )
    telegram_app.add_handler(
        CommandHandler("job", job_command)
    )
    telegram_app.add_handler(
        CommandHandler("last_error", last_error_command)
    )
    telegram_app.add_handler(
        CommandHandler("diagnose", diagnose_command)
    )

    from telegram.ext import MessageHandler, filters

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            unknown_text_handler
        )
    )

    log.info(
        "Starting ZIL V79 FREE CINEMA. "
        "Free mode=%s, paid APIs allowed=%s",
        FREE_MODE,
        ALLOW_PAID_APIS
    )

    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
