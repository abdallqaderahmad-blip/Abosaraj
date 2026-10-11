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

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# ZIL V73 - video + image fallback + motion + synced narration
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-20b"
).strip()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_IMAGE_URL = "https://pixabay.com/api/"

PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 18
BASE_SCENE_DURATION = 5.0
TARGET_DURATION = SCENE_COUNT * BASE_SCENE_DURATION

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024

GROQ_MAX_TOKENS = int(
    os.getenv("GROQ_MAX_TOKENS", "5000")
)
GROQ_RETRIES = int(os.getenv("GROQ_RETRIES", "4"))
GROQ_MAX_WAIT = int(os.getenv("GROQ_MAX_WAIT", "60"))

TTS_VOICE = os.getenv(
    "TTS_VOICE", "ar-SA-HamedNeural"
)
TTS_RATE = os.getenv("TTS_RATE", "-5%")

# Optional direct music URL.
# Set STORY_MUSIC_URL in Render to use your own licensed audio.
STORY_MUSIC_URL = os.getenv(
    "STORY_MUSIC_URL", ""
).strip()

# Maximum source download sizes.
MAX_SOURCE_VIDEO_BYTES = 100 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 25 * 1024 * 1024

BASE_DIR = (
    Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir()))
    / "zil_video_jobs"
)
BASE_DIR.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = BASE_DIR / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BASE_DIR / "zil.log"
STATE_FILE = BASE_DIR / "jobs_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("ZIL")

app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "ZIL-Video-Bot/4.0"
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

# These are optional direct audio URLs. Some links may expire or fail.
# The bot validates the download and falls back to generated ambience.
MUSIC_DIRECT_LINKS = {
    "horror": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a65012.mp3",
    "action": "https://cdn.pixabay.com/download/audio/2021/08/04/audio_0625c1539c.mp3",
    "romance": "https://cdn.pixabay.com/download/audio/2022/10/30/audio_8ef91b2e0e.mp3",
    "sad": "https://cdn.pixabay.com/download/audio/2021/10/25/audio_7c8d1b8e5a.mp3",
    "adventure": "https://cdn.pixabay.com/download/audio/2022/06/07/audio_b9bd4170e8.mp3",
    "mystery": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_7f0c2a0a5e.mp3",
    "comedy": "https://cdn.pixabay.com/download/audio/2021/11/10/audio_3a3c3e6f1e.mp3",
    "epic": "https://cdn.pixabay.com/download/audio/2022/01/18/audio_7beec2a0e8.mp3",
}


# =========================================================
# Persistence
# =========================================================

def save_state_locked():
    try:
        safe = {}

        for key, value in JOBS.items():
            safe[key] = {
                k: v
                for k, v in value.items()
                if k not in {"narration", "idea"}
            }

        tmp = STATE_FILE.with_suffix(".tmp")

        tmp.write_text(
            json.dumps(safe, ensure_ascii=False),
            encoding="utf-8",
        )

        tmp.replace(STATE_FILE)

    except Exception:
        log.exception("Could not persist job state")


def load_state():
    try:
        if not STATE_FILE.exists():
            return

        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return

        for key, job in data.items():
            if not isinstance(job, dict):
                continue

            if job.get("status") in {"running", "queued"}:
                job["status"] = "interrupted"
                job["stage"] = "interrupted_after_restart"
                job["error"] = (
                    "Render restarted while this job was running."
                )

            JOBS[key] = job

    except Exception:
        log.exception("Could not load persisted job state")


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id not in JOBS:
            return

        JOBS[job_id].update(kwargs)
        JOBS[job_id]["updated_at"] = time.time()

        save_state_locked()


def get_active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


# =========================================================
# Utilities
# =========================================================

def command_exists(name):
    try:
        result = subprocess.run(
            ["which", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def run_command(command, timeout=180):
    result = subprocess.run(
        [str(x) for x in command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        details = (
            result.stderr or result.stdout or "Unknown error"
        )[-3000:]

        raise RuntimeError(
            f"Command failed ({result.returncode}): {details}"
        )

    return result


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def probe_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], timeout=25)

    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return None


def has_audio_stream(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], timeout=25)

    return "audio" in result.stdout.lower()


def extract_json(text):
    text = (text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.I,
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError(
            "AI response did not contain valid JSON."
        )


def report_error(job_id, error):
    log.error(
        "Job %s failed: %s",
        job_id,
        error,
        exc_info=True,
    )

    update_job(
        job_id,
        status="failed",
        stage="failed",
        error=str(error)[:2500],
    )


def safe_query(text, maximum=100):
    text = re.sub(
        r"[^A-Za-z0-9 ,'-]",
        "",
        str(text or ""),
    )

    return re.sub(r"\s+", " ", text).strip()[:maximum]


# =========================================================
# Groq
# =========================================================

def groq_wait_seconds(response, attempt):
    message = ""

    try:
        message = str(
            response.json().get("error", {}).get("message", "")
        )
    except Exception:
        message = response.text or ""

    wait = None

    patterns = (
        r"try again in\s+([0-9.]+)\s*s",
        r"retry after\s+([0-9.]+)",
        r"wait\s+([0-9.]+)\s*s",
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
        wait = min(8 * (2 ** attempt), GROQ_MAX_WAIT)

    return max(1, min(wait + 1.5, GROQ_MAX_WAIT))


def groq_chat(messages, max_tokens=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")

    last_error = "unknown error"

    for attempt in range(max(1, GROQ_RETRIES)):
        try:
            response = HTTP.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.25,
                    "max_tokens": (
                        max_tokens or GROQ_MAX_TOKENS
                    ),
                },
                timeout=(20, 120),
            )

            if response.status_code == 429:
                last_error = (
                    f"HTTP 429: {response.text[:900]}"
                )

                wait = groq_wait_seconds(response, attempt)

                log.warning(
                    "Groq rate limited. Attempt %s/%s; "
                    "waiting %.1fs",
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
                    f"HTTP {response.status_code}: "
                    f"{response.text[:1000]}"
                )

                if response.status_code in (400, 401, 403, 404):
                    raise RuntimeError(
                        f"Groq API {last_error}"
                    )

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(min(4 * (attempt + 1), 12))
                    continue

                break

            payload = response.json()
            choices = payload.get("choices") or []

            if not choices:
                raise RuntimeError(
                    "Groq returned no choices."
                )

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
                raise RuntimeError(
                    "Groq returned empty content."
                )

            return str(content)

        except requests.RequestException as exc:
            last_error = f"Groq connection error: {exc}"

            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(4 * (attempt + 1), 12))

    raise RuntimeError(
        "Groq request failed after retries. "
        f"Last error: {last_error}"
    )


# =========================================================
# Automatic story generation
# =========================================================

def create_story_package(idea):
    system_prompt = f"""
أنت كاتب سيناريو محترف لقصص عربية مشوقة.

أنشئ قصة مترابطة من {SCENE_COUNT} مشهدًا بالضبط.
المشهد يستهدف خمس ثوانٍ، لكن يمكن أن تزيد مدته إذا احتاج
الصوت وقتًا إضافيًا حتى لا ينقطع الكلام.

لكل مشهد أخرج:
narration: جملة عربية قصيرة جدًا، واضحة وسهلة النطق.
visual_prompt: وصف إنجليزي لمشهد واقعي سينمائي.
search_query: كلمات إنجليزية مناسبة للبحث عن فيديو أو صورة.
effect: نوع واحد من القائمة التالية:
mystery, castle, steps, romance, tension, storm,
beast, fight, impact, magic, cliffhanger

قواعد مهمة:
- حافظ على نفس أبطال القصة وتسلسل الأحداث.
- اجعل لكل مشهد حدثًا بصريًا واضحًا.
- اجعل السرد قصيرًا، ويفضل 5 إلى 10 كلمات لكل مشهد.
- لا تجعل المشهد الرابع عشر يعيد أحداث المشهد الثالث.
- ابدأ بخطاف درامي.
- صعّد الخطر تدريجيًا.
- اختم بتشويق يفتح الباب للجزء التالي.
- لا تكتب أكثر من جملتين قصيرتين في المشهد.
- لا تضع تعليمات تصوير داخل narration.
- أخرج JSON صالحًا فقط، دون Markdown.

المفاتيح:
title, story, scenes

مثال المشهد:
{{
  "narration": "وقف الغريب أمام الوحش دون أن يتراجع.",
  "visual_prompt": "A mysterious hero facing a giant beast, realistic live action, cinematic lighting",
  "search_query": "man facing giant beast cinematic",
  "effect": "beast"
}}
"""

    user_prompt = (
        f"فكرة القصة:\n{str(idea)[:MAX_STORY_LENGTH]}\n"
        f"أنشئ {SCENE_COUNT} مشهدًا مترابطًا."
    )

    last_error = None

    for validation_attempt in range(2):
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]

        if validation_attempt:
            messages.append({
                "role": "user",
                "content": (
                    f"أعد JSON صحيحًا يحتوي على {SCENE_COUNT} مشهدًا. "
                    "اختصر narration في كل مشهد."
                ),
            })

        raw = groq_chat(messages)

        try:
            data = extract_json(raw)

            scenes = (
                data.get("scenes")
                if isinstance(data, dict)
                else None
            )

            if (
                not isinstance(scenes, list)
                or len(scenes) != SCENE_COUNT
            ):
                raise ValueError(
                    f"Expected {SCENE_COUNT} scenes."
                )

            clean = []

            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(
                        f"Scene {index + 1} is invalid."
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

                query = safe_query(
                    scene.get("search_query", "")
                )

                effect = str(
                    scene.get("effect", "mystery")
                ).lower().strip()

                if not narration:
                    raise ValueError(
                        f"Scene {index + 1} has no narration."
                    )

                if not visual:
                    visual = (
                        "cinematic realistic dramatic scene"
                    )

                if len(query.split()) < 3:
                    query = safe_query(
                        " ".join(
                            re.findall(
                                r"[A-Za-z0-9]+",
                                visual,
                            )[:8]
                        )
                    )

                if len(query.split()) < 3:
                    query = "cinematic dramatic scene"

                if effect not in EFFECTS:
                    effect = "mystery"

                clean.append({
                    "narration": narration[:180],
                    "visual_prompt": visual[:350],
                    "search_query": query[:120],
                    "effect": effect,
                })

            total = word_count(
                " ".join(
                    scene["narration"] for scene in clean
                )
            )

            if not 45 <= total <= 230:
                raise ValueError(
                    f"Narration word count unsuitable: {total}"
                )

            return {
                "title": str(
                    data.get("title", "حكاية ظل")
                )[:100],
                "story": str(
                    data.get("story", idea)
                )[:2000],
                "scenes": clean,
            }

        except Exception as exc:
            last_error = exc

            log.warning(
                "Story validation %s failed: %s",
                validation_attempt + 1,
                exc,
            )

    raise RuntimeError(
        f"Could not validate story JSON: {last_error}"
    )


# =========================================================
# Pixabay video search
# =========================================================

def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")

    response = HTTP.get(
        PIXABAY_VIDEO_URL,
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "per_page": 10,
            "safesearch": "true",
        },
        timeout=(15, 35),
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Pixabay video HTTP {response.status_code}"
        )

    data = response.json()

    for hit in data.get("hits", []):
        videos = hit.get("videos", {})

        for quality in ("medium", "large", "small", "tiny"):
            url = (
                videos.get(quality, {}).get("url", "")
            )

            if (
                url
                and urlparse(url).scheme == "https"
            ):
                return url

    raise RuntimeError(
        f"No Pixabay video found for: {query}"
    )


# =========================================================
# Pixabay image fallback
# =========================================================

def search_pixabay_image(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")

    response = HTTP.get(
        PIXABAY_IMAGE_URL,
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

    if response.status_code >= 400:
        raise RuntimeError(
            f"Pixabay image HTTP {response.status_code}"
        )

    data = response.json()

    for hit in data.get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")

        if (
            url
            and urlparse(url).scheme == "https"
        ):
            return url

    raise RuntimeError(
        f"No Pixabay image found for: {query}"
    )


def download_media(url, destination, maximum_bytes):
    total = 0

    with HTTP.get(
        url,
        stream=True,
        timeout=(20, 90),
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get(
            "content-type", ""
        ).lower()

        if (
            "text/html" in content_type
            or "application/json" in content_type
        ):
            raise RuntimeError(
                "Media URL returned HTML or JSON, not media."
            )

        with open(destination, "wb") as output:
            for chunk in response.iter_content(
                256 * 1024
            ):
                if not chunk:
                    continue

                total += len(chunk)

                if total > maximum_bytes:
                    raise RuntimeError(
                        "Downloaded media exceeds size limit."
                    )

                output.write(chunk)

    if total < 5000:
        raise RuntimeError(
            "Downloaded media is too small."
        )

    return destination


# =========================================================
# Video movement / image Ken Burns motion
# =========================================================

def normalize_video_scene(source, destination, duration):
    """
    Crop video to vertical format and apply subtle zoom movement.
    """
    duration = max(1.0, float(duration))

    vf = (
        "scale=900:1600:force_original_aspect_ratio=increase,"
        "crop=900:1600,"
        "zoompan="
        "z='min(zoom+0.00035,1.07)':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
        f"trim=duration={duration:.3f},"
        "setpts=PTS-STARTPTS,"
        "setsar=1,format=yuv420p"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
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
    ], 240)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Video normalization failed."
        )

    return destination


def animate_still_image(source, destination, duration, index):
    """
    Turn a still image into a moving vertical video:
    slow zoom and alternating pan direction.
    """
    duration = max(1.0, float(duration))
    total_frames = max(1, int(duration * VIDEO_FPS))

    if index % 2:
        x_expression = (
            "iw/2-(iw/zoom/2)"
            "+(iw/zoom/8)*sin(on/45)"
        )
    else:
        x_expression = (
            "iw/2-(iw/zoom/2)"
            "-(iw/zoom/8)*sin(on/45)"
        )

    vf = (
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800,"
        "zoompan="
        "z='min(zoom+0.0010,1.15)':"
        f"x='{x_expression}':"
        "y='ih/2-(ih/zoom/2)':"
        f"d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
        f"trim=end_frame={total_frames},"
        "setpts=PTS-STARTPTS,"
        "setsar=1,format=yuv420p"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
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
    ], 240)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Could not animate fallback image."
        )

    return destination


def create_fallback_visual(destination, duration, index):
    """
    Last-resort background if both video and image search fail.
    This is an abstract background, not an AI-generated scene.
    """
    duration = max(1.0, float(duration))

    # Different dark colors per scene, avoiding a black empty screen.
    colors = [
        "0x172238",
        "0x28203D",
        "0x26343A",
        "0x38232B",
        "0x1D2A42",
    ]

    color = colors[index % len(colors)]

    vf = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        "zoompan="
        "z='min(zoom+0.0008,1.12)':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
        "format=yuv420p"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", (
            f"color=c={color}:"
            f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
            f"r={VIDEO_FPS}:d={duration:.3f}"
        ),
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        str(destination),
    ], 120)

    return destination


def obtain_scene_visual(scene, workdir, index, duration):
    """
    Try video first, image second, abstract fallback last.
    """
    n = index + 1

    query = scene["search_query"]

    visual_query = safe_query(
        " ".join(
            re.findall(
                r"[A-Za-z0-9]+",
                scene["visual_prompt"],
            )[:8]
        )
    )

    raw_video = workdir / f"raw_video_{n}.mp4"
    raw_image = workdir / f"raw_image_{n}.jpg"
    normalized = workdir / f"visual_{n}.mp4"

    queries = [query]

    if (
        visual_query
        and visual_query.lower() != query.lower()
    ):
        queries.append(visual_query)

    for candidate_query in queries:
        try:
            url = search_pixabay_video(candidate_query)

            download_media(
                url,
                raw_video,
                MAX_SOURCE_VIDEO_BYTES,
            )

            normalize_video_scene(
                raw_video,
                normalized,
                duration,
            )

            raw_video.unlink(missing_ok=True)
            raw_image.unlink(missing_ok=True)

            log.info(
                "Scene %s uses video source.",
                n,
            )

            return normalized, "video"

        except Exception as exc:
            log.warning(
                "Scene %s video search failed: %s",
                n,
                exc,
            )

    for candidate_query in queries:
        try:
            url = search_pixabay_image(candidate_query)

            download_media(
                url,
                raw_image,
                MAX_SOURCE_IMAGE_BYTES,
            )

            animate_still_image(
                raw_image,
                normalized,
                duration,
                index,
            )

            raw_video.unlink(missing_ok=True)
            raw_image.unlink(missing_ok=True)

            log.info(
                "Scene %s uses animated still image.",
                n,
            )

            return normalized, "image"

        except Exception as exc:
            log.warning(
                "Scene %s image search failed: %s",
                n,
                exc,
            )

    log.warning(
        "Scene %s uses abstract fallback.",
        n,
    )

    create_fallback_visual(
        normalized,
        duration,
        index,
    )

    raw_video.unlink(missing_ok=True)
    raw_image.unlink(missing_ok=True)

    return normalized, "fallback"


# =========================================================
# Arabic subtitles
# =========================================================

def add_arabic_caption(
    source,
    caption,
    destination,
    workdir,
    index,
    duration,
):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display

    except Exception:
        log.warning(
            "Subtitle dependencies missing."
        )
        return source

    try:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

        font = None

        for fp in font_candidates:
            if Path(fp).exists():
                font = ImageFont.truetype(fp, 32)
                break

        if font is None:
            font = ImageFont.load_default()

        overlay = Image.new(
            "RGBA",
            (VIDEO_WIDTH, 170),
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        words = (caption or "").split()
        lines = []
        current = ""

        for word in words:
            candidate = (
                current + " " + word
            ).strip()

            shaped = get_display(
                arabic_reshaper.reshape(candidate)
            )

            width = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )[2]

            if current and width > VIDEO_WIDTH - 80:
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        # Keep subtitles readable. Narration is deliberately short.
        lines = lines[:2]

        draw.rounded_rectangle(
            (16, 8, VIDEO_WIDTH - 16, 158),
            radius=18,
            fill=(0, 0, 0, 165),
        )

        for i, line in enumerate(lines):
            shaped = get_display(
                arabic_reshaper.reshape(line)
            )

            box = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )

            text_width = box[2] - box[0]

            x = max(
                10,
                (VIDEO_WIDTH - text_width) // 2,
            )

            draw.text(
                (x, 25 + i * 48),
                shaped,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 255),
            )

        png = workdir / f"caption_{index}.png"
        overlay.save(png)

        run_command([
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(png),
            "-filter_complex",
            "[0:v][1:v]overlay=0:H-170:format=auto[v]",
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
            "Subtitle render failed for scene %s",
            index,
        )

    return source


# =========================================================
# Arabic voice
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


def prepare_synced_voice(
    source,
    destination,
    desired_duration,
):
    """
    Keep the complete narration.
    Speed up gently if needed; never intentionally trim the sentence.
    The video duration follows the resulting audio duration.
    """
    raw_duration = probe_duration(source)

    if not raw_duration or raw_duration <= 0:
        raise RuntimeError(
            "Could not determine narration duration."
        )

    desired_duration = max(
        BASE_SCENE_DURATION,
        float(desired_duration),
    )

    # Do not force long speech into a five-second container.
    if raw_duration > desired_duration * 1.25:
        desired_duration = raw_duration / 1.25

    speed = min(
        1.25,
        max(1.0, raw_duration / desired_duration),
    )

    filters = []

    if speed > 1.01:
        filters.append(
            f"atempo={speed:.4f}"
        )

    filters += [
        "aresample=44100",
        "aformat=sample_rates=44100:channel_layouts=stereo",
        "asetpts=PTS-STARTPTS",
    ]

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af", ",".join(filters),
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], 120)

    actual = probe_duration(destination)

    if not actual or actual <= 0:
        raise RuntimeError(
            "Could not prepare narration audio."
        )

    # A small audio padding buffer protects against rounding differences.
    final_duration = max(
        BASE_SCENE_DURATION,
        actual + 0.08,
    )

    padded = destination.with_name(
        destination.stem + "_padded.m4a"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(destination),
        "-af",
        f"apad=pad_dur={final_duration:.3f},"
        f"atrim=duration={final_duration:.3f},"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(padded),
    ], 120)

    padded.replace(destination)

    return final_duration


# =========================================================
# Sound effects
# =========================================================

def make_scene_sfx(kind, destination, duration):
    """
    Low-volume synthetic effects. These are not real recordings.
    """
    duration = max(1.0, float(duration))

    if kind == "beast":
        source = (
            "anoisesrc=color=brown:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "highpass=f=45,lowpass=f=850,"
            "tremolo=f=5:d=0.75,volume=0.22"
        )

    elif kind in ("fight", "impact"):
        source = (
            "anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "highpass=f=50,lowpass=f=1600,"
            "volume=0.25,"
            "afade=t=in:d=0.02,"
            "afade=t=out:st=0.65:d=0.8"
        )

    elif kind == "magic":
        source = (
            "sine=frequency=95:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "tremolo=f=3:d=0.75,"
            "volume=0.12,"
            "afade=t=in:d=0.7,"
            f"afade=t=out:st={max(0.1, duration-1.2)}:d=1.2"
        )

    elif kind == "storm":
        source = (
            "anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "lowpass=f=420,"
            "volume=0.10,"
            "tremolo=f=0.25:d=0.4"
        )

    elif kind in (
        "tension",
        "mystery",
        "cliffhanger",
    ):
        source = (
            "sine=frequency=72:"
            f"sample_rate=44100:duration={duration}"
        )
        af = (
            "tremolo=f=2:d=0.55,"
            "lowpass=f=500,"
            "volume=0.08"
        )

    elif kind == "romance":
        source = (
            "sine=frequency=440:"
            f"sample_rate=44100:duration={duration}"
        )
        af = "lowpass=f=1200,volume=0.025"

    else:
        source = (
            "anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        af = "lowpass=f=1000,volume=0.045"

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", source,
        "-af", af,
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "64k",
        str(destination),
    ], 90)

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            f"Could not create sound effect: {kind}"
        )

    return destination


# =========================================================
# Music
# =========================================================

def detect_mood(package):
    text = (
        str(package.get("title", ""))
        + " "
        + str(package.get("story", ""))
    ).lower()

    if any(x in text for x in (
        "حب", "أميرة", "عشق", "love", "romance"
    )):
        return "romance"

    if any(x in text for x in (
        "رعب", "وحش", "ظلام", "horror"
    )):
        return "horror"

    if any(x in text for x in (
        "حزين", "فقد", "وداع", "sad"
    )):
        return "sad"

    if any(x in text for x in (
        "معركة", "قتال", "حرب", "fight", "battle"
    )):
        return "action"

    if any(x in text for x in (
        "مملكة", "سحر", "أسطورة", "epic"
    )):
        return "epic"

    return "mystery"


def download_music(url, destination):
    if not url:
        raise RuntimeError("No music URL supplied.")

    if urlparse(url).scheme != "https":
        raise RuntimeError(
            "Music URL must use HTTPS."
        )

    return download_media(
        url,
        destination,
        35 * 1024 * 1024,
    )


def make_generated_music(destination, duration):
    """
    Fallback ambience when a direct music URL is unavailable.
    This is synthesized ambience, not a real licensed soundtrack.
    """
    duration = max(1.0, float(duration))

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        (
            "sine=frequency=55:"
            f"sample_rate=44100:duration={duration}"
        ),
        "-af",
        (
            "volume=0.025,"
            "tremolo=f=0.18:d=0.25,"
            "afade=t=in:d=2,"
            f"afade=t=out:st={max(0.1, duration-4)}:d=4"
        ),
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "64k",
        str(destination),
    ], 120)

    return destination


def prepare_background_music(
    package,
    destination,
    duration,
    workdir,
):
    mood = detect_mood(package)

    candidates = []

    if STORY_MUSIC_URL:
        candidates.append(STORY_MUSIC_URL)

    if mood in MUSIC_DIRECT_LINKS:
        candidates.append(
            MUSIC_DIRECT_LINKS[mood]
        )

    candidates += [
        url for key, url in MUSIC_DIRECT_LINKS.items()
        if key != mood
    ]

    for index, url in enumerate(candidates):
        try:
            downloaded = workdir / f"music_source_{index}.mp3"

            download_music(
                url,
                downloaded,
            )

            run_command([
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "error",
                "-stream_loop", "-1",
                "-i", str(downloaded),
                "-t", f"{duration:.3f}",
                "-af",
                (
                    "volume=0.20,"
                    "afade=t=in:d=2,"
                    f"afade=t=out:st={max(0.1, duration-4)}:d=4"
                ),
                "-ar", "44100",
                "-ac", "2",
                "-c:a", "aac",
                "-b:a", "96k",
                str(destination),
            ], 180)

            if (
                destination.exists()
                and destination.stat().st_size > 1000
            ):
                log.info(
                    "Using downloaded music: %s",
                    url,
                )
                return destination

        except Exception as exc:
            log.warning(
                "Music source failed: %s",
                exc,
            )

    log.warning(
        "All music URLs failed; using generated ambience."
    )

    return make_generated_music(
        destination,
        duration,
    )


# =========================================================
# Concatenation
# =========================================================

def write_concat_list(paths, destination):
    with open(
        destination,
        "w",
        encoding="utf-8",
    ) as file:
        for path in paths:
            resolved = Path(path).resolve().as_posix()

            if "'" in resolved:
                raise RuntimeError(
                    "Media path contains unsupported apostrophe."
                )

            file.write(
                f"file '{resolved}'\n"
            )


def concatenate_video(paths, destination, workdir):
    listing = workdir / "video_list.txt"

    write_concat_list(
        paths,
        listing,
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(listing),
        "-an",
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

    write_concat_list(
        paths,
        listing,
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
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


# =========================================================
# Final audio mix
# =========================================================

def mix_final_audio(
    video,
    voice,
    sfx,
    music,
    destination,
):
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
                f"Missing or empty mix input: {label}"
            )

    for label, path in (
        ("voice", voice),
        ("sfx", sfx),
        ("music", music),
    ):
        if not has_audio_stream(path):
            raise RuntimeError(
                f"No audio stream detected in {label}."
            )

    duration = probe_duration(video)

    if not duration or duration <= 0:
        raise RuntimeError(
            "Could not determine final video duration."
        )

    graph = (
        "[1:a]"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=1.0,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[v];"

        "[2:a]"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.22,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[s];"

        "[3:a]"
        "aresample=44100,"
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
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
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

   
