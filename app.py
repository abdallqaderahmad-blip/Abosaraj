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
# ZIL V77 HYBRID
# Pixabay video first -> strict filtering -> FAL AI image
# Cinematic camera movement for both video and still images
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)

FAL_KEY = (
    os.getenv("FAL_KEY", "").strip()
    or os.getenv("FAL_API_KEY", "").strip()
)

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "auto").strip().lower()
FAL_IMAGE_MODEL = os.getenv(
    "FAL_IMAGE_MODEL",
    "fal-ai/flux/schnell"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b"
).strip()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
FAL_QUEUE_URL = "https://queue.fal.run/"
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"
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
MAX_SOURCE_VIDEO_BYTES = 40 * 1024 * 1024

IMAGE_GENERATION_TIMEOUT = max(
    60,
    int(os.getenv("IMAGE_GENERATION_TIMEOUT", "240"))
)

IMAGE_GENERATION_RETRIES = max(
    1,
    min(2, int(os.getenv("IMAGE_GENERATION_RETRIES", "1")))
)

GROQ_RETRIES = max(1, int(os.getenv("GROQ_RETRIES", "4")))
GROQ_MAX_WAIT = max(5, int(os.getenv("GROQ_MAX_WAIT", "60")))
GROQ_MAX_TOKENS = max(
    1200,
    int(os.getenv("GROQ_MAX_TOKENS", "3500"))
)

PIXABAY_TIMEOUT = max(
    10,
    int(os.getenv("PIXABAY_TIMEOUT", "30"))
)

TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural").strip()
TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()

SUBTITLES_ENABLED = os.getenv(
    "SUBTITLES_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")

STORY_MUSIC_URL = os.getenv("STORY_MUSIC_URL", "").strip()

BASE_DIR = (
    Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
    / "zil_video_jobs"
)

BASE_DIR.mkdir(parents=True, exist_ok=True)
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
    "User-Agent": "ZIL-Video-Bot/7.7"
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

# Words that usually indicate an unwanted overlay or outro.
FORBIDDEN_TAG_PHRASES = {
    "thank you",
    "thank you for watching",
    "subscribe",
    "subscribe now",
    "end screen",
    "video outro",
    "channel logo",
    "brand logo",
    "watermark",
    "advertisement",
    "commercial",
    "text overlay",
    "title screen",
    "closing screen",
}

# These terms are rejected when they are unrelated to the scene.
ANIMAL_TERMS = {
    "cow",
    "cattle",
    "bull",
    "calf",
    "mantis",
    "insect",
    "bug",
    "beetle",
    "cockroach",
    "grasshopper",
    "cricket",
    "butterfly",
    "mosquito",
    "spider",
    "ant",
    "bee",
    "wasp",
    "fish",
    "fishing",
    "fisherman",
    "angler",
    "shark",
    "dolphin",
    "whale",
    "octopus",
    "squid",
    "pig",
    "sheep",
    "goat",
    "chicken",
    "hen",
    "rooster",
    "duck",
    "goose",
    "horse",
    "donkey",
    "deer",
    "rabbit",
    "mouse",
    "rat",
    "snake",
    "lizard",
    "frog",
    "turtle",
    "eagle",
    "owl",
    "parrot",
    "monkey",
    "gorilla",
    "elephant",
    "zebra",
    "giraffe",
    "camel",
    "dog",
    "puppy",
    "cat",
    "kitten",
    "bear",
    "wolf",
    "tiger",
    "lion",
}

STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "onto",
    "over", "under", "near", "behind", "beside", "while",
    "where", "when", "what", "this", "that", "these",
    "those", "their", "there", "then", "than", "they",
    "them", "his", "her", "she", "him", "who", "which",
    "have", "has", "had", "are", "was", "were", "been",
    "being", "will", "would", "could", "should", "can",
    "not", "but", "into", "about", "scene", "cinematic",
    "cinema", "photorealistic", "realistic", "style",
    "camera", "shot", "closeup", "close", "wide",
    "vertical", "composition", "lighting", "high",
    "quality", "detailed", "detail", "beautiful",
    "dramatic", "dramatically", "very", "extremely",
    "show", "shows", "shown", "depict", "depicts",
    "image", "video", "still", "frame", "moment",
    "character", "characters", "person", "people",
    "man", "woman", "young", "old", "standing",
    "walking", "looking", "appears", "appearing",
    "moves", "moving", "looks", "wearing",
    "wears", "face", "expression", "expressions",
    "real", "live", "action", "fantasy", "medieval",
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
                if k not in {"idea", "narration"}
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
# General utilities
# =========================================================

def run_command(command, timeout=240):
    result = subprocess.run(
        [str(x) for x in command],
        capture_output=True,
        text=True,
        timeout=timeout,
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


def safe_query(text, maximum=100):
    text = re.sub(
        r"[^A-Za-z0-9 ,'-]",
        " ",
        str(text or "")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maximum].strip(" ,-'")


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
        str(path),
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
        str(path),
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
        headers={"User-Agent": "ZIL-Video-Bot/7.7"},
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
            "text": str(text)[:4000],
        },
        timeout=(15, 45),
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
                "supports_streaming": "true",
            },
            files={
                "video": (
                    Path(path).name,
                    video_file,
                    "video/mp4",
                )
            },
            timeout=(30, 240),
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
        header_wait = float(
            response.headers.get("retry-after", "")
        )
        wait = max(wait or 0, header_wait)
    except (ValueError, TypeError):
        pass

    if wait is None:
        wait = min(8 * (2 ** attempt), GROQ_MAX_WAIT)

    return max(1, min(wait + 1, GROQ_MAX_WAIT))


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
                last_error = (
                    f"Groq rate limit: {response.text[:800]}"
                )

                wait = groq_wait_seconds(response, attempt)

                log.warning(
                    "Groq rate limit; attempt %s/%s; waiting %.1fs",
                    attempt + 1,
                    GROQ_RETRIES,
                    wait,
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
                raise RuntimeError("Groq returned no choices.")

            content = (
                choices[0]
                .get("message", {})
                .get("content", "")
            )

            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                )

            if not str(content).strip():
                raise RuntimeError("Groq returned empty content.")

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

def create_story_package(idea):
    system_prompt = f"""
أنت كاتب سيناريو ومخرج فني لقصص عربية مشوقة.
أنشئ {SCENE_COUNT} مشهدًا بالضبط لفيديو عمودي واقعي سينمائي.

أخرج JSON صالحًا فقط دون Markdown بالمفاتيح:
title, story, visual_style, character_bible, scenes.

visual_style:
وصف إنجليزي قصير موحد للون والإضاءة والأسلوب الواقعي.

character_bible:
وصف إنجليزي موجز وثابت للشخصيات الرئيسية، يتضمن الوجه والشعر
والعمر التقريبي والملابس والعلامات المميزة.

كل مشهد يحتوي على:
narration: جملة عربية قصيرة.
visual_prompt: وصف إنجليزي واضح لما يظهر بصريًا في المشهد.
effect: نوع واحد من الأنواع المسموحة.

الأنواع المسموحة:
mystery, castle, steps, romance, tension, storm, beast,
fight, impact, magic, cliffhanger.

قواعد إلزامية:
- يجب أن يصف visual_prompt الحدث نفسه الذي يقوله narration.
- لا تستخدم استعارات بصرية لا علاقة لها بالجملة.
- صف مكان الحدث والشخصيات الظاهرة وحركتهم وتعبيراتهم.
- لا تضف حيوانات أو عناصر لا تحتاجها القصة.
- حافظ على ثبات الوجوه والشعر والملابس.
- لا تضع كتابة أو شعارات داخل الصورة.
- ابدأ بخطاف قوي وصعّد الخطر تدريجيًا.
- اختم بتشويق للجزء التالي.

الأسلوب:
photorealistic live-action fantasy, cinematic lighting,
detailed faces, vertical composition, no text, no logo,
no watermark.
"""

    user_prompt = (
        f"فكرة القصة:\n{str(idea)[:MAX_STORY_LENGTH]}\n"
        f"أنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    )

    last_error = None

    for attempt in range(2):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if attempt:
            messages.append({
                "role": "user",
                "content": (
                    f"أعد JSON صالحًا فقط، وتأكد من وجود "
                    f"{SCENE_COUNT} مشهدًا وvisual_style "
                    "وcharacter_bible."
                ),
            })

        raw = groq_chat(messages)
        data = extract_json(raw)

        try:
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
                        "vertical composition",
                    )
                ),
            ).strip()

            character_bible = data.get(
                "character_bible", ""
            )

            if isinstance(character_bible, dict):
                character_bible = "; ".join(
                    f"{str(k)[:40]}: {str(v)[:180]}"
                    for k, v in character_bible.items()
                )

            character_bible = re.sub(
                r"\s+", " ", str(character_bible)
            ).strip()

            if not character_bible:
                character_bible = (
                    "Keep the same main character faces, hair, "
                    "clothing, age, and distinctive features "
                    "across every scene."
                )

            clean = []

            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(
                        f"Invalid scene {index + 1}"
                    )

                narration = re.sub(
                    r"\s+",
                    " ",
                    str(scene.get("narration", "")),
                ).strip()

                visual = re.sub(
                    r"\s+",
                    " ",
                    str(scene.get("visual_prompt", "")),
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
                    "narration": narration[:180],
                    "visual_prompt": visual[:500],
                    "effect": effect,
                })

            total_words = word_count(
                " ".join(
                    scene["narration"] for scene in clean
                )
            )

            if not 20 <= total_words <= 300:
                raise ValueError(
                    f"Unexpected narration length: {total_words}"
                )

            return {
                "title": str(
                    data.get("title", "حكاية ظل")
                )[:100],
                "story": str(
                    data.get("story", idea)
                )[:2000],
                "visual_style": visual_style[:350],
                "character_bible": character_bible[:900],
                "scenes": clean,
            }

        except Exception as exc:
            last_error = exc
            log.warning(
                "Story validation attempt %s failed: %s",
                attempt + 1,
                exc,
            )

    raise RuntimeError(
        f"Story validation failed: {last_error}"
    )


# =========================================================
# Hybrid visual matching utilities
# =========================================================

def extract_keywords(text, maximum=12):
    text = str(text or "").lower()
    words = re.findall(r"[a-z][a-z'-]{2,}", text)

    result = []
    seen = set()

    for word in words:
        word = word.strip("'-")

        if len(word) < 4:
            continue

        if word in STOP_WORDS or word in seen:
            continue

        seen.add(word)
        result.append(word)

        if len(result) >= maximum:
            break

    return result


def normalize_tags(tags):
    if isinstance(tags, list):
        text = " ".join(str(item) for item in tags)
    else:
        text = str(tags or "")

    text = text.lower()
    text = re.sub(r"[^a-z0-9 ,'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def contains_forbidden_overlay(tags):
    tags = normalize_tags(tags)

    return any(
        phrase in tags
        for phrase in FORBIDDEN_TAG_PHRASES
    )


def scene_mentions_animal(scene_text, animal):
    text = str(scene_text or "").lower()

    # Exact term or common plural forms.
    variants = {
        animal,
        animal + "s",
        animal + "es",
    }

    return any(
        re.search(
            r"\b" + re.escape(variant) + r"\b",
            text,
        )
        for variant in variants
    )


def has_unrelated_animal(tags, scene_text):
    tag_text = normalize_tags(tags)
    scene_text = str(scene_text or "").lower()

    for animal in ANIMAL_TERMS:
        if not re.search(
            r"\b" + re.escape(animal) + r"\b",
            tag_text,
        ):
            continue

        if not scene_mentions_animal(scene_text, animal):
            return True

    return False


def tag_match_count(tags, scene_keywords):
    tag_text = normalize_tags(tags)
    tag_words = set(
        re.findall(r"[a-z][a-z'-]{2,}", tag_text)
    )

    matches = 0

    for keyword in scene_keywords:
        if keyword in tag_words:
            matches += 1
            continue

        # Also allow simple partial matching for longer words.
        if len(keyword) >= 6 and any(
            word.startswith(keyword[:5])
            or keyword.startswith(word[:5])
            for word in tag_words
            if len(word) >= 5
        ):
            matches += 1

    return matches


def build_pixabay_queries(scene):
    """
    Create up to three targeted English queries from visual_prompt.
    The code does not replace the description with a generic query.
    """
    visual = str(
        scene.get("visual_prompt", "")
    ).strip()

    keywords = extract_keywords(visual, maximum=12)

    queries = []

    # Attempt 1: first high-value terms from the full visual prompt.
    if keywords:
        queries.append(" ".join(keywords[:6]))

    # Attempt 2: a shorter set of distinct terms.
    if len(keywords) >= 4:
        queries.append(" ".join(keywords[2:8]))

    # Attempt 3: scene-specific phrase, not generic cinematic filler.
    if len(keywords) >= 3:
        queries.append(" ".join(keywords[-5:]))

    cleaned = []
    seen = set()

    for query in queries:
        query = safe_query(query, 100).strip()

        if query and query.lower() not in seen:
            seen.add(query.lower())
            cleaned.append(query)

    return cleaned[:3]


def choose_pixabay_video_variant(hit):
    videos = hit.get("videos") or {}

    # Prefer the highest-quality available variant.
    candidates = []

    for name in ("large", "medium", "small", "tiny"):
        item = videos.get(name)

        if not isinstance(item, dict):
            continue

        url = item.get("url")
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        size = int(item.get("size") or 0)

        if not url:
            continue

        # Require at least 720p on both dimensions.
        if min(width, height) < 720:
            continue

        candidates.append({
            "name": name,
            "url": url,
            "width": width,
            "height": height,
            "size": size,
        })

    if not candidates:
        return None

    # Already ordered by quality preference.
    return candidates[0]


def search_pixabay_video(scene, index, workdir):
    """
    Search Pixabay at most three times.
    Accept a result only when:
      - the video is at least 720p,
      - tags overlap with the actual visual prompt,
      - forbidden overlay tags are absent,
      - unrelated animal tags are absent.
    """
    if not PIXABAY_API_KEY:
        log.info(
            "Scene %s: PIXABAY_API_KEY missing; skipping stock video.",
            index + 1,
        )
        return None

    visual = str(
        scene.get("visual_prompt", "")
    ).strip()

    queries = build_pixabay_queries(scene)

    if not queries:
        log.info(
            "Scene %s: no usable keywords for Pixabay.",
            index + 1,
        )
        return None

    keywords = extract_keywords(visual, maximum=12)
    scene_number = index + 1

    for attempt, query in enumerate(queries, start=1):
        try:
            log.info(
                "Scene %s: Pixabay search %s/%s: %s",
                scene_number,
                attempt,
                len(queries),
                query,
            )

            response = requests.get(
                PIXABAY_API_URL,
                params={
                    "key": PIXABAY_API_KEY,
                    "q": query,
                    "per_page": 30,
                    "safesearch": "true",
                    "order": "popular",
                },
                timeout=(15, PIXABAY_TIMEOUT),
            )

            if response.status_code >= 400:
                log.warning(
                    "Pixabay HTTP %s for scene %s: %s",
                    response.status_code,
                    scene_number,
                    response.text[:250],
                )
                continue

            data = response.json()
            hits = data.get("hits") or []

            # Evaluate each candidate instead of blindly choosing hits[0].
            for hit in hits:
                tags = hit.get("tags", "")

                if contains_forbidden_overlay(tags):
                    log.info(
                        "Scene %s: rejected video with overlay tags.",
                        scene_number,
                    )
                    continue

                if has_unrelated_animal(tags, visual):
                    log.info(
                        "Scene %s: rejected unrelated-animal tags: %s",
                        scene_number,
                        normalize_tags(tags)[:180],
                    )
                    continue

                matched = tag_match_count(tags, keywords)

                if matched < 2:
                    log.info(
                        "Scene %s: rejected weak tag match (%s): %s",
                        scene_number,
                        matched,
                        normalize_tags(tags)[:180],
                    )
                    continue

                variant = choose_pixabay_video_variant(hit)

                if not variant:
                    log.info(
                        "Scene %s: rejected video below 720p.",
                        scene_number,
                    )
                    continue

                destination = workdir / (
                    f"pixabay_source_{scene_number}_"
                    f"{attempt}.mp4"
                )

                try:
                    download_media(
                        variant["url"],
                        destination,
                        MAX_SOURCE_VIDEO_BYTES,
                    )

                    # Confirm FFmpeg can decode the downloaded source.
                    probe_duration(destination)

                    log.info(
                        "Scene %s: accepted Pixabay video "
                        "%sx%s; matched tags=%s",
                        scene_number,
                        variant["width"],
                        variant["height"],
                        matched,
                    )

                    return destination

                except Exception as exc:
                    log.warning(
                        "Scene %s: failed to download candidate: %s",
                        scene_number,
                        exc,
                    )

                    destination.unlink(missing_ok=True)

        except Exception as exc:
            log.warning(
                "Pixabay attempt %s failed for scene %s: %s",
                attempt,
                scene_number,
                exc,
            )

    log.info(
        "Scene %s: no acceptable Pixabay video after %s attempts.",
        scene_number,
        len(queries),
    )

    return None


# =========================================================
# AI image generation
# =========================================================

def build_scene_image_prompt(scene, package, index):
    scene_description = str(
        scene.get("visual_prompt", "")
    ).strip()

    narration = str(
        scene.get("narration", "")
    ).strip()

    style = str(
        package.get(
            "visual_style",
            "photorealistic live-action fantasy, cinematic lighting",
        )
    ).strip()

    bible = str(
        package.get(
            "character_bible",
            "Keep the same character identity in every scene.",
        )
    ).strip()

    prompt = (
        f"Vertical 9:16 cinematic still for scene {index + 1} "
        "of one continuous fantasy drama. "
        f"VISUAL STYLE: {style}. "
        f"CHARACTER CONTINUITY: {bible}. "
        f"EXACT SCENE ACTION: {scene_description}. "
        "Depict the described event literally. "
        "Do not substitute unrelated objects, animals, or scenery. "
        "Narration context, do not write these words in the image: "
        f"{narration}. "
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


def save_generated_image(url, destination):
    response = requests.get(
        url,
        stream=True,
        timeout=(20, 90),
        headers={"User-Agent": "ZIL-Video-Bot/7.7"},
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "content-type", ""
    ).lower()

    if not content_type.startswith("image/"):
        raise RuntimeError(
            "Image URL returned non-image content "
            f"({content_type or 'unknown'})."
        )

    total = 0

    with open(destination, "wb") as output:
        for chunk in response.iter_content(256 * 1024):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_SOURCE_IMAGE_BYTES:
                raise RuntimeError(
                    "Generated image exceeds configured size limit."
                )

            output.write(chunk)

    if total < 5000:
        raise RuntimeError(
            "Generated image is too small or invalid."
        )

    return destination


def fal_generate_image(prompt, destination):
    if not FAL_KEY:
        raise RuntimeError("FAL_KEY is not configured.")

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
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
            "enable_safety_checker": True,
        },
        timeout=(20, 90),
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Fal image request failed ({response.status_code}): "
            f"{response.text[:700]}"
        )

    data = response.json()
    image_url = None

    # Some endpoints may return the result immediately.
    if isinstance(data.get("images"), list) and data["images"]:
        image_url = data["images"][0].get("url")

    else:
        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")

        if not request_id or not status_url or not response_url:
            raise RuntimeError(
                "Fal returned an unexpected response: "
                + str(data)[:500]
            )

        deadline = time.time() + IMAGE_GENERATION_TIMEOUT

        while time.time() < deadline:
            status_response = requests.get(
                status_url,
                headers=headers,
                timeout=(15, 45),
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
                    timeout=(15, 60),
                )

                result_response.raise_for_status()
                result_data = result_response.json()
                images = result_data.get("images") or []

                image_url = (
                    images[0].get("url")
                    if images
                    else None
                )
                break

            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(
                    f"Fal image generation {status.lower()}: "
                    + str(status_data)[:500]
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
    from urllib.parse import quote

    url = POLLINATIONS_IMAGE_URL + quote(
        prompt,
        safe="",
    )

    headers = {
        "User-Agent": "ZIL-Video-Bot/7.7"
    }

    if POLLINATIONS_API_KEY:
        headers["Authorization"] = (
            f"Bearer {POLLINATIONS_API_KEY}"
        )

    response = requests.get(
        url,
        params={
            "width": 768,
            "height": 1365,
            "model": "flux",
            "seed": int(seed),
            "nologo": "true",
            "safe": "true",
        },
        headers=headers,
        timeout=(25, IMAGE_GENERATION_TIMEOUT),
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Pollinations image request failed: "
            f"{response.text[:400]}"
        )

    content_type = response.headers.get(
        "content-type", ""
    ).lower()

    if (
        not content_type.startswith("image/")
        or len(response.content) < 5000
    ):
        raise RuntimeError(
            "Pollinations did not return a valid image."
        )

    Path(destination).write_bytes(response.content)
    return destination


def generate_scene_image(prompt, destination, index):
    provider = IMAGE_PROVIDER
    last_error = None

    # Fal is preferred.
    if provider in ("auto", "fal") and FAL_KEY:
        for attempt in range(IMAGE_GENERATION_RETRIES):
            try:
                return fal_generate_image(
                    prompt,
                    destination,
                )

            except Exception as exc:
                last_error = exc

                log.warning(
                    "Fal image generation attempt %s failed "
                    "for scene %s: %s",
                    attempt + 1,
                    index + 1,
                    exc,
                )

                if attempt + 1 < IMAGE_GENERATION_RETRIES:
                    time.sleep(
                        min(3 * (attempt + 1), 8)
                    )

        if provider == "fal":
            raise RuntimeError(
                f"Fal image generation failed for scene "
                f"{index + 1}: {last_error}"
            )

    # Pollinations remains an optional fallback.
    if provider in ("auto", "pollinations"):
        try:
            return pollinations_generate_image(
                prompt,
                destination,
                seed=7600 + index,
            )

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "تعذر توليد صورة الذكاء الاصطناعي للمشهد "
        + str(index + 1)
        + ". تأكد من FAL_KEY أو توفر خدمة الصور. "
        + (
            f"التفاصيل: {last_error}"
            if last_error
            else ""
        )
    )


# =========================================================
# Scene rendering: same cinematic style for both sources
# =========================================================

def normalize_video_scene(source, destination, duration, index):
    """
    Normalize real video to portrait 720x1280.
    Apply a slow zoom and slight horizontal drift.
    """
    duration = max(1.0, float(duration))
    total_frames = max(1, int(duration * VIDEO_FPS))

    if index % 2:
        x_expr = (
            "iw/2-(iw/zoom/2)"
            "+(iw/zoom/45)*sin(on/55)"
        )
    else:
        x_expr = (
            "iw/2-(iw/zoom/2)"
            "-(iw/zoom/45)*sin(on/55)"
        )

    vf = (
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800,"
        f"fps={VIDEO_FPS},"
        "zoompan="
        "z='min(zoom+0.00035,1.07)':"
        f"x='{x_expr}':"
        "y='ih/2-(ih/zoom/2)':"
        "d=1:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"fps={VIDEO_FPS},"
        f"trim=end_frame={total_frames},"
        "setpts=PTS-STARTPTS,"
        "setsar=1,"
        "format=yuv420p"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-stream_loop", "-1",
        "-i", str(source),
        "-vf", vf,
        "-an",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], 300)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Video normalization failed."
        )

    return destination


def animate_still_image(source, destination, duration, index):
    duration = max(1.0, float(duration))
    total_frames = max(1, int(duration * VIDEO_FPS))

    if index % 2:
        x_expr = (
            "iw/2-(iw/zoom/2)"
            "+(iw/zoom/10)*sin(on/40)"
        )
    else:
        x_expr = (
            "iw/2-(iw/zoom/2)"
            "-(iw/zoom/10)*sin(on/40)"
        )

    vf = (
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800,"
        "zoompan="
        "z='min(zoom+0.001,1.12)':"
        f"x='{x_expr}':"
        "y='ih/2-(ih/zoom/2)':"
        "d=1:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"fps={VIDEO_FPS},"
        f"trim=end_frame={total_frames},"
        "setpts=PTS-STARTPTS,"
        "setsar=1,"
        "format=yuv420p"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-loop", "1",
        "-framerate", str(VIDEO_FPS),
        "-i", str(source),
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], 300)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Image animation failed."
        )

    return destination


def obtain_scene_visual(scene, package, workdir, index, duration):
    """
    Hybrid order:
      1. Search Pixabay with up to 3 targeted queries.
      2. Strictly filter the tags and resolution.
      3. If no acceptable video exists, generate a scene-specific AI image.
      4. Apply cinematic camera movement to either source.

    No plain colored visual fallback is used.
    """
    number = index + 1

    normalized = workdir / f"visual_{number}.mp4"

    # FIRST: try a relevant real video.
    stock_video = search_pixabay_video(
        scene,
        index,
        workdir,
    )

    if stock_video is not None:
        try:
            normalize_video_scene(
                stock_video,
                normalized,
                duration,
                index,
            )

            log.info(
                "Scene %s uses filtered Pixabay video.",
                number,
            )

            return normalized, "pixabay_video"

        except Exception as exc:
            log.warning(
                "Scene %s: video rendering failed; "
                "switching to AI image: %s",
                number,
                exc,
            )

    # SECOND: generate a scene-specific image.
    raw_image = workdir / f"ai_scene_{number}.img"
    prompt = build_scene_image_prompt(
        scene,
        package,
        index,
    )

    generate_scene_image(
        prompt,
        raw_image,
        index,
    )

    # Convert image to a predictable PNG format.
    normalized_image = (
        workdir / f"ai_scene_{number}.png"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(raw_image),
        "-frames:v", "1",
        "-vf",
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800",
        str(normalized_image),
    ], 90)

    if (
        not normalized_image.exists()
        or normalized_image.stat().st_size < 5000
    ):
        raise RuntimeError(
            f"Generated image for scene {number} "
            "could not be decoded."
        )

    animate_still_image(
        normalized_image,
        normalized,
        duration,
        index,
    )

    log.info(
        "Scene %s uses AI-generated image with camera movement.",
        number,
    )

    return normalized, "ai_image"


# =========================================================
# Optional Arabic subtitles
# =========================================================

def add_arabic_caption(
    source,
    caption,
    destination,
    workdir,
    index,
    duration,
):
    if not SUBTITLES_ENABLED:
        return source

    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display

    except Exception:
        log.warning(
            "Subtitle libraries missing; continuing without subtitles."
        )
        return source

    try:
        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]

        font_path = next(
            (
                candidate
                for candidate in font_candidates
                if Path(candidate).exists()
            ),
            None,
        )

        font = (
            ImageFont.truetype(font_path, 34)
            if font_path
            else ImageFont.load_default()
        )

        overlay = Image.new(
            "RGBA",
            (VIDEO_WIDTH, 150),
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        words = (caption or "").split()
        lines = []
        current = ""

        for word in words:
            candidate = (current + " " + word).strip()

            shaped = get_display(
                arabic_reshaper.reshape(candidate)
            )

            box = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )

            if (
                current
                and box[2] - box[0] > VIDEO_WIDTH - 80
            ):
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        lines = lines[:2]

        draw.rounded_rectangle(
            (18, 8, VIDEO_WIDTH - 18, 142),
            radius=16,
            fill=(0, 0, 0, 160),
        )

        for line_index, line in enumerate(lines):
            shaped = get_display(
                arabic_reshaper.reshape(line)
            )

            box = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )

            x = max(
                10,
                (VIDEO_WIDTH - (box[2] - box[0])) // 2,
            )

            draw.text(
                (x, 20 + line_index * 48),
                shaped,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 255),
            )

        png = workdir / f"caption_{index}.png"
        overlay.save(png)

        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(source),
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(png),
            "-filter_complex",
            "[0:v][1:v]overlay=0:H-150:format=auto[v]",
            "-map", "[v]",
            "-t", f"{duration:.3f}",
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "27",
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            str(destination),
        ], 180)

        if (
            destination.exists()
            and destination.stat().st_size > 1000
        ):
            return destination

    except Exception:
        log.exception(
            "Subtitle render failed; continuing without captions."
        )

    return source


# =========================================================
# Arabic TTS and scene-length audio padding
# =========================================================

def create_narration(text, destination):
    destination.unlink(missing_ok=True)

    try:
        import edge_tts

        async def generate():
            communicate = edge_tts.Communicate(
                text,
                voice=TTS_VOICE,
                rate=TTS_RATE,
            )
            await communicate.save(str(destination))

        asyncio.run(generate())

        if (
            destination.exists()
            and destination.stat().st_size > 1000
        ):
            return destination

    except Exception:
        log.exception("Edge TTS failed.")

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False,
        ).save(str(destination))

        if (
            destination.exists()
            and destination.stat().st_size > 1000
        ):
            return destination

    except Exception:
        log.exception("gTTS fallback failed.")

    raise RuntimeError(
        "Arabic text-to-speech failed."
    )


def prepare_synced_voice(source, destination):
    duration = probe_duration(source)

    if not duration or duration <= 0:
        raise RuntimeError(
            "Could not determine voice duration."
        )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-af",
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], 120)

    actual = probe_duration(destination)

    if not actual or actual <= 0:
        raise RuntimeError(
            "Voice conversion failed."
        )

    return actual


def fit_audio_to_duration(source, destination, duration):
    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-af",
        f"aresample=44100,"
        f"aformat=sample_rates=44100:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], 120)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Could not fit narration to scene duration."
        )

    return destination


# =========================================================
# Sound effects
# =========================================================

def make_scene_sfx(kind, destination, duration):
    duration = max(1.0, float(duration))

    if kind == "beast":
        source = (
            f"anoisesrc=color=brown:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "highpass=f=45,lowpass=f=850,"
            "tremolo=f=5:d=0.75,volume=0.22"
        )

    elif kind in ("fight", "impact"):
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "highpass=f=50,lowpass=f=1600,"
            "volume=0.22,afade=t=in:d=0.02,"
            f"afade=t=out:st={max(0.1, duration-0.8)}:d=0.7"
        )

    elif kind == "magic":
        source = (
            f"sine=frequency=95:"
            f"sample_rate=44100:duration={duration}"
        )
        af = "tremolo=f=3:d=0.75,volume=0.12"

    elif kind == "storm":
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "lowpass=f=420,volume=0.10,"
            "tremolo=f=0.25:d=0.4"
        )

    elif kind in (
        "tension",
        "mystery",
        "cliffhanger",
    ):
        source = (
            f"sine=frequency=72:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "tremolo=f=2:d=0.55,"
            "lowpass=f=500,volume=0.08"
        )

    elif kind == "romance":
        source = (
            f"sine=frequency=440:"
            f"sample_rate=44100:duration={duration}"
        )
        af = "lowpass=f=1200,volume=0.025"

    else:
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = "lowpass=f=1000,volume=0.045"

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", source,
        "-af", af,
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "64k",
        str(destination),
    ], 120)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            f"Could not create SFX: {kind}"
        )

    return destination


def prepare_background_music(destination, duration, workdir):
    if STORY_MUSIC_URL:
        try:
            source = workdir / "custom_music_source.mp3"

            download_media(
                STORY_MUSIC_URL,
                source,
                35 * 1024 * 1024,
            )

            run_command([
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-stream_loop", "-1",
                "-i", str(source),
                "-t", f"{duration:.3f}",
                "-af",
                "volume=0.18,afade=t=in:d=2,"
                f"afade=t=out:st={max(0.1, duration-4)}:d=4",
                "-ar", "44100",
                "-ac", "2",
                "-c:a", "aac",
                "-b:a", "96k",
                str(destination),
            ], 240)

            if (
                destination.exists()
                and destination.stat().st_size > 1000
            ):
                return destination

        except Exception:
            log.exception(
                "Custom music failed; using generated ambience."
            )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-af",
        "volume=0.025,tremolo=f=0.18:d=0.25,"
        "afade=t=in:d=2,"
        f"afade=t=out:st={max(0.1, duration-4)}:d=4",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "64k",
        str(destination),
    ], 180)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Background music generation failed."
        )

    return destination


# =========================================================
# Concatenation and final audio mix
# =========================================================

def write_concat_list(paths, destination):
    with open(
        destination,
        "w",
        encoding="utf-8",
    ) as output:
        for path in paths:
            resolved = Path(path).resolve().as_posix()

            if "'" in resolved:
                raise RuntimeError(
                    "A media path contains an unsupported apostrophe."
                )

            output.write(
                f"file '{resolved}'\n"
            )


def concatenate_video(paths, destination, workdir):
    listing = workdir / "video_list.txt"

    write_concat_list(paths, listing)

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(listing),
        "-an",
        "-vf", "setsar=1,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], 600)

    if (
        not destination.exists()
        or destination.stat().st_size < 10000
    ):
        raise RuntimeError(
            "Video concatenation failed."
        )

    return destination


def concatenate_audio(paths, destination, workdir, label):
    listing = workdir / f"{label}_list.txt"

    write_concat_list(paths, listing)

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(listing),
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], 600)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            f"Audio concatenation failed: {label}"
        )

    return destination


def mix_final_audio(video, voice, sfx, music, destination):
    for label, path in {
        "video": video,
        "voice": voice,
        "sfx": sfx,
        "music": music,
    }.items():
        if (
            not Path(path).exists()
            or Path(path).stat().st_size < 1000
        ):
            raise RuntimeError(
                f"Missing mix input: {label}"
            )

    for label, path in (
        ("voice", voice),
        ("sfx", sfx),
        ("music", music),
    ):
        if not has_audio_stream(path):
            raise RuntimeError(
                f"No audio stream in {label}"
            )

    duration = probe_duration(video)

    if not duration or duration <= 0:
        raise RuntimeError(
            "Could not determine video duration."
        )

    graph = (
        "[1:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=1.0,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[v];"

        "[2:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.22,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[s];"

        "[3:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.12,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[m];"

        "[v][s][m]"
        "amix=inputs=3:duration=longest:"
        "dropout_transition=2:normalize=0,"
        "alimiter=limit=0.92,"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[aout]"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-i", str(voice),
        "-i", str(sfx),
        "-i", str(music),
        "-filter_complex", graph,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(destination),
    ], 600)

    if (
        not destination.exists()
        or destination.stat().st_size < 10000
    ):
        raise RuntimeError(
            "Final video file is missing or too small."
        )

    return destination


# =========================================================
# Main production pipeline
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
            with JOBS_LOCK:
                chat_id = (
                    JOBS.get(job_id, {}).get("chat_id")
                )

            if chat_id:
                telegram_send_message(
                    chat_id,
                    f"{percent}% — {message}",
                )

        except Exception:
            log.exception(
                "Could not send progress update."
            )

    package = create_story_package(idea)
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
            int(5 + (index / SCENE_COUNT) * 65),
            f"تجهيز المشهد {number}/{SCENE_COUNT}",
        )

        raw_voice = workdir / f"voice_raw_{number}.mp3"
        voice_converted = workdir / f"voice_converted_{number}.m4a"
        voice_aligned = workdir / f"voice_{number}.m4a"

        create_narration(
            scene["narration"],
            raw_voice,
        )

        voice_duration = prepare_synced_voice(
            raw_voice,
            voice_converted,
        )

        scene_duration = max(
            BASE_SCENE_DURATION,
            voice_duration + 0.20,
        )

        durations.append(scene_duration)

        fit_audio_to_duration(
            voice_converted,
            voice_aligned,
            scene_duration,
        )

        visual, visual_type = obtain_scene_visual(
            scene,
            package,
            workdir,
            index,
            scene_duration,
        )

        update_job(
            job_id,
            current_scene=number,
            current_visual=visual_type,
        )

        captioned = workdir / f"captioned_{number}.mp4"

        final_visual = add_arabic_caption(
            visual,
            scene["narration"],
            captioned,
            workdir,
            index,
            scene_duration,
        )

        video_paths.append(final_visual)
        voice_paths.append(voice_aligned)

        sfx_file = workdir / f"sfx_{number}.m4a"

        try:
            make_scene_sfx(
                scene["effect"],
                sfx_file,
                scene_duration,
            )

        except Exception:
            log.exception(
                "SFX failed in scene %s; using mystery ambience.",
                number,
            )

            make_scene_sfx(
                "mystery",
                sfx_file,
                scene_duration,
            )

        sfx_paths.append(sfx_file)

    progress(
        "concatenating",
        75,
        "تجميع المشاهد والصوت...",
    )

    final_video_only = workdir / "video_only.mp4"
    final_voice = workdir / "voice_all.m4a"
    final_sfx = workdir / "sfx_all.m4a"
    final_music = workdir / "music.m4a"
    final_output = workdir / "ZIL_final.mp4"

    concatenate_video(
        video_paths,
        final_video_only,
        workdir,
    )

    concatenate_audio(
        voice_paths,
        final_voice,
        workdir,
        "voice",
    )

    concatenate_audio(
        sfx_paths,
        final_sfx,
        workdir,
        "sfx",
    )

    total_duration = sum(durations)

    prepare_background_music(
        final_music,
        total_duration,
        workdir,
    )

    progress(
        "audio_mix",
        90,
        "موازنة الصوت والموسيقى والمؤثرات...",
    )

    mix_final_audio(
        final_video_only,
        final_voice,
        final_sfx,
        final_music,
        final_output,
    )

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        progress(
            "compressing",
            95,
            "ضغط الفيديو ليتناسب مع إرسال تيليغرام...",
        )

        compressed = workdir / "ZIL_compressed.mp4"

        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(final_output),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "32",
            "-vf", "scale=540:960",
            "-c:a", "aac",
            "-b:a", "80k",
            "-movflags", "+faststart",
            str(compressed),
        ], 600)

        if (
            compressed.exists()
            and compressed.stat().st_size < final_output.stat().st_size
        ):
            final_output = compressed

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        raise RuntimeError(
            "Final video is still too large for the configured "
            "Telegram limit. Reduce scene count or resolution."
        )

    progress(
        "completed",
        100,
        "اكتمل الفيديو!",
    )

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
        )

        try:
            telegram_send_message(
                chat_id,
                f"🎬 بدأ إنتاج الفيديو.\n"
                f"رقم العملية: {job_id}\n"
                f"عدد المشاهد: {SCENE_COUNT}\n"
                "النظام الهجين: فيديو مناسب أولًا، "
                "ثم صورة AI متحركة عند الحاجة.",
            )

        except Exception:
            log.exception(
                "Could not send job-start message."
            )

        output, package = create_video(
            job_id,
            idea,
        )

        update_job(
            job_id,
            status="uploading",
            stage="uploading",
            progress=98,
        )

        if not output.exists():
            raise RuntimeError(
                "Final video was not created."
            )

        if output.stat().st_size > MAX_VIDEO_SIZE:
            raise RuntimeError(
                "Video exceeds the Telegram upload limit."
            )

        telegram_send_message(
            chat_id,
            f"✅ اكتمل الفيديو: {package['title']}\n"
            "جارٍ إرساله الآن...",
        )

        telegram_send_video(
            chat_id,
            output,
            f"🎬 {package['title']}\nصناعة: ظل ZIL V77",
        )

        update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            output=str(output),
            title=package["title"],
        )

        telegram_send_message(
            chat_id,
            f"🎉 انتهى الإنتاج بنجاح!\nالعملية: {job_id}",
        )

    except Exception as exc:
        log.exception(
            "Video job failed: %s",
            job_id,
        )

        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:2500],
        )

        try:
            telegram_send_message(
                chat_id,
                f"❌ فشل إنتاج الفيديو.\n"
                f"العملية: {job_id}\n"
                f"السبب: {str(exc)[:1000]}\n\n"
                f"استخدم /job {job_id} لمراجعة الحالة "
                "وافحص Runtime Logs في Render.",
            )

        except Exception:
            log.exception(
                "Could not send job-failure message."
            )

    finally:
        global_active_release()


def global_active_release():
    global ACTIVE_JOBS

    with JOBS_LOCK:
        ACTIVE_JOBS = max(
            0,
            ACTIVE_JOBS - 1,
        )

        save_state_locked()


# =========================================================
# Telegram commands
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "🎬 أهلًا بك في ظل ZIL V77 Hybrid\n\n"
        "أرسل فكرة أو قصة لتحويلها إلى فيديو رأسي.\n\n"
        "الأوامر:\n"
        "/start - تشغيل البوت\n"
        "/make فكرتك - إنشاء فيديو\n"
        "/test - تجربة قصة ثابتة\n"
        "/status - حالة النظام والعمليات\n"
        "/job رقم_العملية - تفاصيل عملية\n"
        "/health - فحص الخدمات\n\n"
        "يبحث النظام عن فيديو مناسب أولًا، "
        "ثم يولّد صورة AI متحركة إذا لم يجد نتيجة "
        "تجتاز الفلاتر. يتضمن الفيديو سردًا عربيًا "
        "ومؤثرات وموسيقى.",
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    with JOBS_LOCK:
        jobs = list(JOBS.items())
        active = ACTIVE_JOBS

    if not jobs:
        await update.effective_message.reply_text(
            f"حالة ظل ZIL V77\n"
            f"العمليات النشطة: {active}\n"
            f"Groq: {'مهيأ' if GROQ_API_KEY else 'غير مهيأ'}\n"
            f"Pixabay: "
            f"{'مهيأ' if PIXABAY_API_KEY else 'غير مهيأ'}\n"
            f"FAL: {'مهيأ' if FAL_KEY else 'غير مهيأ'}\n"
            f"FFmpeg: "
            f"{'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}"
        )
        return

    lines = [
        "📊 حالة ظل ZIL V77",
        f"العمليات النشطة: {active}",
        f"Pixabay: {'مهيأ' if PIXABAY_API_KEY else 'غير مهيأ'}",
        f"FAL: {'مهيأ' if FAL_KEY else 'غير مهيأ'}",
        "",
    ]

    for job_id, job in reversed(jobs[-5:]):
        lines.append(
            f"{job_id} | "
            f"{job.get('status', 'unknown')} | "
            f"{job.get('stage', '-')} | "
            f"{job.get('progress', 0)}%"
        )

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


async def health_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "🩺 فحص ظل ZIL V77\n"
        f"BOT_TOKEN: {'مهيأ' if BOT_TOKEN else 'مفقود'}\n"
        f"GROQ_API_KEY: {'مهيأ' if GROQ_API_KEY else 'مفقود'}\n"
        f"PIXABAY_API_KEY: "
        f"{'مهيأ' if PIXABAY_API_KEY else 'غير مهيأ'}\n"
        f"FAL_KEY: {'مهيأ' if FAL_KEY else 'غير مهيأ'}\n"
        f"FFmpeg: "
        f"{'جاهز' if shutil_which('ffmpeg') else 'غير موجود'}\n"
        f"FFprobe: "
        f"{'جاهز' if shutil_which('ffprobe') else 'غير موجود'}\n"
        f"المهام النشطة: {active_jobs()}"
    )


async def job_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    if not context.args:
        await update.effective_message.reply_text(
            "استخدم الأمر هكذا:\n/job رقم_العملية"
        )
        return

    job_id = context.args[0].strip()

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        job = dict(job) if job else None

    if not job:
        await update.effective_message.reply_text(
            "لم أجد هذه العملية."
        )
        return

    await update.effective_message.reply_text(
        f"العملية: {job_id}\n"
        f"الحالة: {job.get('status', '-')}\n"
        f"المرحلة: {job.get('stage', '-')}\n"
        f"التقدم: {job.get('progress', 0)}%\n"
        f"المشهد: {job.get('current_scene', '-')}\n"
        f"نوع الصورة: {job.get('current_visual', '-')}\n"
        f"الخطأ: {job.get('error', 'لا يوجد')}"
    )


async def launch_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    idea: str,
):
    global ACTIVE_JOBS

    if (
        not update.effective_chat
        or not update.effective_user
        or not update.effective_message
    ):
        return

    idea = (idea or "").strip()

    if len(idea) < 5:
        await update.effective_message.reply_text(
            "اكتب فكرة أو قصة أطول قليلًا."
        )
        return

    idea = idea[:MAX_STORY_LENGTH]

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.effective_message.reply_text(
                "⏳ يوجد إنتاج آخر قيد التنفيذ. "
                "انتظر حتى ينتهي قبل بدء فيديو جديد."
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
        }

        save_state_locked()

    await update.effective_message.reply_text(
        f"🚀 تم تسجيل طلبك.\n"
        f"رقم العملية: {job_id}\n"
        "سأبدأ تجهيز القصة والمشاهد."
    )

    worker = threading.Thread(
        target=job_worker,
        args=(
            job_id,
            update.effective_chat.id,
            idea,
        ),
        daemon=True,
    )

    try:
        worker.start()

    except Exception as exc:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(
                0,
                ACTIVE_JOBS - 1,
            )

        update_job(
            job_id,
            status="failed",
            stage="thread_start_failed",
            error=str(exc),
        )

        log.exception(
            "Could not start worker thread."
        )

        await update.effective_message.reply_text(
            "تعذر تشغيل المهمة. أعد المحاولة بعد قليل."
        )


async def make_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    idea = " ".join(context.args).strip()

    if not idea:
        if update.effective_message:
            await update.effective_message.reply_text(
                "استخدم الأمر هكذا:\n"
                "/make أمير غامض يواجه وحشًا لإنقاذ الأميرة"
            )
        return

    await launch_job(
        update,
        context,
        idea,
    )


async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    idea = (
        "قصة خيالية سينمائية: رجل غامض يصل إلى مملكة قديمة، "
        "وتقع الأميرة في حبه. يرفض الملك علاقتهما لأنه يخفي "
        "سرًا خطيرًا عن قوة الرجل. يقتحم وحش ضخم ساحة القصر، "
        "ويتظاهر البطل بالضعف قبل أن يكشف جزءًا من قوته "
        "الحقيقية. ينقذ الأميرة لكنه يكتشف أن الوحش كان يهرب "
        "من خطر أكبر، وتنتهي القصة بظهور علامة غامضة على ذراعه."
    )

    await launch_job(
        update,
        context,
        idea,
    )


async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    error = context.error

    if (
        isinstance(error, Conflict)
        or "conflict" in str(error).lower()
    ):
        log.error(
            "Telegram Conflict detected. Another bot process "
            "may be polling with this token. Stop duplicate "
            "deployments/processes."
        )

    else:
        log.error(
            "Unhandled Telegram application error",
            exc_info=(
                (type(error), error, error.__traceback__)
                if error
                else None
            ),
        )


# =========================================================
# Flask endpoints
# =========================================================

@app.get("/")
def index():
    return jsonify({
        "service": "ZIL V77 Hybrid",
        "status": "online",
        "active_jobs": active_jobs(),
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ZIL V77 Hybrid",
        "groq_configured": bool(GROQ_API_KEY),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "fal_configured": bool(FAL_KEY),
        "image_provider": IMAGE_PROVIDER,
        "ffmpeg_available": bool(shutil_which("ffmpeg")),
        "ffprobe_available": bool(shutil_which("ffprobe")),
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
# Startup
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing in Render environment variables."
        )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing in Render environment variables."
        )

    if not shutil_which("ffmpeg"):
        raise RuntimeError(
            "FFmpeg is not installed."
        )

    if not shutil_which("ffprobe"):
        raise RuntimeError(
            "FFprobe is not installed."
        )

    if not PIXABAY_API_KEY:
        log.warning(
            "PIXABAY_API_KEY is not configured. "
            "The hybrid pipeline will use AI images for scenes."
        )

    if not FAL_KEY and IMAGE_PROVIDER != "pollinations":
        log.warning(
            "FAL_KEY is not configured. Image generation may fail "
            "if Pollinations is unavailable."
        )

    load_state()

    threading.Thread(
        target=start_flask,
        daemon=True,
    ).start()

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )

    telegram_app.add_handler(
        CommandHandler("status", status_command)
    )

    telegram_app.add_handler(
        CommandHandler("health", health_command)
    )

    telegram_app.add_handler(
        CommandHandler("job", job_command)
    )

    telegram_app.add_handler(
        CommandHandler("make", make_command)
    )

    telegram_app.add_handler(
        CommandHandler("test", test_command)
    )

    telegram_app.add_error_handler(
        telegram_error_handler
    )

    log.info(
        "Starting ZIL V77 Hybrid; model=%s; scenes=%s; "
        "Pixabay=%s; FAL=%s",
        GROQ_MODEL,
        SCENE_COUNT,
        bool(PIXABAY_API_KEY),
        bool(FAL_KEY),
    )

    # Ensure only one running instance uses this bot token.
    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()
