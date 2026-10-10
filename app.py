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
from telegram.ext import Application, CommandHandler

# =========================================================
# ZIL CONFIGURATION
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
PIXABAY_API_URL = "https://pixabay.com/api/videos/"

PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 18
SCENE_DURATION = 5
VIDEO_DURATION = SCENE_COUNT * SCENE_DURATION

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024

GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "3000"))
GROQ_RETRIES = int(os.getenv("GROQ_RETRIES", "4"))
GROQ_MAX_WAIT = int(os.getenv("GROQ_MAX_WAIT", "60"))

TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
TTS_RATE = os.getenv("TTS_RATE", "-10%")

BASE_DIR = Path(tempfile.gettempdir()) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BASE_DIR / "zil.log"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("ZIL")

# =========================================================
# GLOBAL STATE
# =========================================================

app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-Video-Bot/2.0"})

JOBS = {}
JOBS_LOCK = threading.Lock()
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
# GENERAL UTILITIES
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
        )[-2500:]

        raise RuntimeError(
            f"Command failed ({result.returncode}): {details}"
        )

    return result


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)
            JOBS[job_id]["updated_at"] = time.time()


def get_active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


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


def extract_json(text):
    text = (text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError("AI response did not contain valid JSON.")


def report_error(job_id, error):
    log.error(
        "Job %s failed: %s",
        job_id,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )

    update_job(
        job_id,
        status="failed",
        stage="failed",
        error=str(error)[:2000],
    )


# =========================================================
# GROQ RATE-LIMIT HANDLING
# =========================================================

def get_groq_wait_seconds(response, attempt):
    """
    Extract the wait duration from Groq's error message.
    Falls back to exponential backoff if the duration is unknown.
    """

    wait_seconds = None

    try:
        payload = response.json()
        message = str(
            payload.get("error", {}).get("message", "")
        )
    except Exception:
        message = response.text or ""

    patterns = [
        r"try again in\s+([0-9.]+)\s*s",
        r"retry after\s+([0-9.]+)",
        r"wait\s+([0-9.]+)\s*s",
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)

        if match:
            try:
                wait_seconds = float(match.group(1))
                break
            except ValueError:
                pass

    retry_after = response.headers.get("retry-after")

    if retry_after:
        try:
            header_wait = float(retry_after)

            if wait_seconds is None:
                wait_seconds = header_wait
            else:
                wait_seconds = max(wait_seconds, header_wait)
        except ValueError:
            pass

    if wait_seconds is None:
        wait_seconds = min(
            10 * (2 ** attempt),
            GROQ_MAX_WAIT,
        )

    # Do not retry before the requested delay.
    wait_seconds = max(1, wait_seconds + 1.5)

    return min(wait_seconds, GROQ_MAX_WAIT)


def groq_chat(messages, max_tokens=None):
    """
    Groq request helper.
    Retries 429 errors with a controlled wait.
    Avoids an immediate retry loop.
    """

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")

    token_limit = max_tokens or GROQ_MAX_TOKENS
    last_error = None

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
                    "max_tokens": token_limit,
                },
                timeout=120,
            )

            if response.status_code == 429:
                last_error = (
                    f"Groq rate limit HTTP 429: "
                    f"{response.text[:1000]}"
                )

                wait_seconds = get_groq_wait_seconds(
                    response,
                    attempt,
                )

                log.warning(
                    "Groq rate limit. Attempt %s/%s. "
                    "Waiting %.1f seconds.",
                    attempt + 1,
                    GROQ_RETRIES,
                    wait_seconds,
                )

                if attempt == GROQ_RETRIES - 1:
                    break

                time.sleep(
                    wait_seconds + random.uniform(0.2, 1.0)
                )
                continue

            if response.status_code >= 400:
                message = response.text[:1500]

                if response.status_code in (401, 403, 404):
                    raise RuntimeError(
                        f"Groq API HTTP {response.status_code}: "
                        f"{message}"
                    )

                last_error = (
                    f"Groq API HTTP {response.status_code}: "
                    f"{message}"
                )

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(
                        min(5 * (attempt + 1), 15)
                    )
                    continue

                break

            payload = response.json()
            choices = payload.get("choices") or []

            if not choices:
                raise RuntimeError(
                    "Groq returned no response choices."
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
                raise RuntimeError("Groq returned empty content.")

            return str(content)

        except requests.RequestException as error:
            last_error = f"Groq connection error: {error}"

            log.warning("%s", last_error)

            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(5 * (attempt + 1), 15))

    raise RuntimeError(
        "Groq request failed after retries. "
        f"Last error: {last_error}"
    )


# =========================================================
# STORY GENERATION
# =========================================================

def create_story_package(idea):
    """
    Generates exactly 18 scenes.
    Validates the response and retries invalid JSON.
    """

    system_prompt = """
أنت كاتب سيناريو محترف لمسلسلات الفانتازيا والتشويق.

أخرج JSON صالحًا فقط. ممنوع Markdown أو الشرح خارج JSON.

أنشئ قصة عربية مترابطة مكونة من 18 مشهدًا بالضبط.

كل مشهد يناسب فيديو مدته خمس ثوانٍ.

المطلوب لكل مشهد:
- narration: جملة عربية قصيرة قابلة للإلقاء.
- visual_prompt: وصف بصري بالإنجليزية.
- search_query: كلمات إنجليزية للبحث عن فيديو مناسب.
- effect: مؤثر واحد من القائمة المحددة.

المؤثرات المسموحة:
mystery, castle, steps, romance, tension,
storm, beast, fight, impact, magic, cliffhanger

حافظ على تسلسل الأحداث واستمرارية القصة.
اجعل القصة مثيرة، مع تصاعد الخطر ونهاية معلقة.
لا تستخدم شخصيات أو أسماء جديدة بلا داعٍ.
اجعل مجموع السرد العربي بين 100 و230 كلمة.

أخرج المفاتيح التالية فقط:
{
  "title": "عنوان عربي",
  "story": "ملخص عربي",
  "scenes": [
    {
      "narration": "جملة عربية",
      "visual_prompt": "English visual description",
      "search_query": "English search terms",
      "effect": "mystery"
    }
  ]
}
"""

    user_prompt = (
        "فكرة القصة:\n"
        + str(idea)[:MAX_STORY_LENGTH]
        + "\nأنشئ JSON صالحًا مع 18 مشهدًا بالضبط."
    )

    # Validate and retry malformed model output without
    # repeatedly sending an unnecessarily large prompt.
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

        if validation_attempt == 1:
            messages.append({
                "role": "user",
                "content": (
                    "أعد إنشاء JSON فقط. تأكد من وجود 18 مشهدًا "
                    "والمفاتيح المطلوبة في كل مشهد. "
                    "اجعل كل وصف قصيرًا لتقليل حجم الإجابة."
                ),
            })

        raw = groq_chat(messages, max_tokens=GROQ_MAX_TOKENS)

        try:
            data = extract_json(raw)

            if not isinstance(data, dict):
                raise ValueError("The AI response is not a JSON object.")

            scenes = data.get("scenes")

            if not isinstance(scenes, list):
                raise ValueError("The scenes field is not a list.")

            if len(scenes) != SCENE_COUNT:
                raise ValueError(
                    f"Expected {SCENE_COUNT} scenes, "
                    f"received {len(scenes)}."
                )

            clean_scenes = []

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

                query = re.sub(
                    r"[^A-Za-z0-9 ,'-]",
                    "",
                    str(scene.get("search_query", "")),
                ).strip()

                effect = str(
                    scene.get("effect", "mystery")
                ).lower().strip()

                if not narration:
                    raise ValueError(
                        f"Scene {index + 1} has no narration."
                    )

                if not visual:
                    visual = "Cinematic dramatic fantasy scene"

                if len(query.split()) < 3:
                    query = " ".join(
                        re.findall(r"[A-Za-z0-9]+", visual)[:8]
                    )

                if len(query.split()) < 3:
                    query = "cinematic fantasy dramatic scene"

                if effect not in EFFECTS:
                    effect = "mystery"

                clean_scenes.append({
                    "narration": narration[:350],
                    "visual_prompt": visual[:350],
                    "search_query": query[:120],
                    "effect": effect,
                })

            total_words = word_count(
                " ".join(
                    scene["narration"]
                    for scene in clean_scenes
                )
            )

            if total_words < 80 or total_words > 250:
                raise ValueError(
                    f"Narration length is unsuitable: {total_words} words."
                )

            return {
                "title": str(data.get("title", "حكاية ظل"))[:100],
                "story": str(data.get("story", idea))[:2000],
                "scenes": clean_scenes,
                "narration": " ".join(
                    scene["narration"]
                    for scene in clean_scenes
                ),
            }

        except Exception as error:
            log.warning(
                "Story validation attempt %s failed: %s",
                validation_attempt + 1,
                error,
            )

            if validation_attempt == 1:
                raise RuntimeError(
                    f"Could not validate story JSON: {error}"
                ) from error

    raise RuntimeError("Could not generate a valid story.")


# =========================================================
# PIXABAY VIDEO SEARCH
# =========================================================

def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")

    response = HTTP.get(
        PIXABAY_API_URL,
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "per_page": 10,
            "safesearch": "true",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Pixabay HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    for hit in data.get("hits", []):
        videos = hit.get("videos", {})

        for quality in ("large", "medium", "small", "tiny"):
            video_url = (
                videos.get(quality, {}).get("url", "")
            )

            if (
                video_url
                and urlparse(video_url).scheme == "https"
            ):
                return video_url

    raise RuntimeError(
        f"No Pixabay video found for: {query}"
    )


def download_video(url, destination):
    total = 0

    with HTTP.get(
        url,
        stream=True,
        timeout=(20, 90),
    ) as response:
        response.raise_for_status()

        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)

                if total > 150 * 1024 * 1024:
                    raise RuntimeError(
                        "Downloaded video exceeds 150 MB."
                    )

                output.write(chunk)

    if total < 10000:
        raise RuntimeError("Downloaded video is too small.")

    return destination


# =========================================================
# VIDEO NORMALIZATION
# =========================================================

def normalize_scene(source, destination):
    video_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        "setsar=1,"
        f"fps={VIDEO_FPS},"
        "format=yuv420p"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", video_filter,
        "-t", str(SCENE_DURATION),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], timeout=180)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Scene normalization failed.")

    return destination


# =========================================================
# ARABIC SUBTITLES
# =========================================================

def add_arabic_caption(source, caption, destination, workdir, index):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning(
            "Arabic subtitle libraries unavailable; skipping subtitles."
        )
        return source

    try:
        font_path = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )

        try:
            font = ImageFont.truetype(font_path, 28)
        except Exception:
            font = ImageFont.load_default()

        image = Image.new(
            "RGBA",
            (VIDEO_WIDTH, 150),
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(image)
        words = (caption or "").split()
        lines = []
        current = ""

        for word in words:
            candidate = (current + " " + word).strip()
            shaped = get_display(
                arabic_reshaper.reshape(candidate)
            )

            bounds = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )

            if current and bounds[2] > VIDEO_WIDTH - 70:
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        if len(lines) > 2:
            lines = [
                " ".join(words[:len(words) // 2]),
                " ".join(words[len(words) // 2:]),
            ]

        draw.rounded_rectangle(
            (10, 8, VIDEO_WIDTH - 10, 140),
            radius=15,
            fill=(0, 0, 0, 175),
        )

        for line_index, line in enumerate(lines[:2]):
            shaped = get_display(
                arabic_reshaper.reshape(line)
            )

            bounds = draw.textbbox(
                (0, 0),
                shaped,
                font=font,
            )

            text_width = bounds[2] - bounds[0]
            x = max(10, (VIDEO_WIDTH - text_width) // 2)
            y = 30 + line_index * 45

            draw.text(
                (x, y),
                shaped,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 255),
            )

        png_path = workdir / f"caption_{index}.png"
        image.save(png_path)

        run_command([
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(png_path),
            "-filter_complex",
            "[0:v][1:v]overlay=0:H-150:format=auto[v]",
            "-map", "[v]",
            "-t", str(SCENE_DURATION),
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            str(destination),
        ], timeout=120)

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception(
            "Caption rendering failed for scene %s",
            index,
        )

    return source


# =========================================================
# ARABIC TTS
# =========================================================

def create_narration(text, destination):
    destination.unlink(missing_ok=True)

    try:
        import edge_tts

        async def generate():
            await edge_tts.Communicate(
                text,
                voice=TTS_VOICE,
                rate=TTS_RATE,
            ).save(str(destination))

        asyncio.run(generate())

        if destination.exists() and destination.stat().st_size > 1000:
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

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception("gTTS failed.")

    raise RuntimeError("Both Arabic TTS providers failed.")


def fit_audio_to_scene(source, destination):
    duration = probe_duration(source)

    if not duration or duration <= 0:
        raise RuntimeError("Could not determine narration duration.")

    # Speed up long narration modestly to fit a five-second scene.
    speed = min(1.5, max(1.0, duration / 4.7))

    audio_filter = ""

    if speed > 1.001:
        audio_filter = f"atempo={speed:.4f},"

    audio_filter += (
        f"apad=pad_dur={SCENE_DURATION},"
        f"atrim=duration={SCENE_DURATION},"
        "asetpts=PTS-STARTPTS"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af", audio_filter,
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "128k",
        str(destination),
    ], timeout=90)

    return destination


# =========================================================
# SOUND EFFECTS AND MUSIC
# =========================================================

def make_scene_sfx(kind, destination):
    duration = SCENE_DURATION

    if kind == "beast":
        source = (
            f"anoisesrc=color=brown:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = (
            "highpass=f=45,lowpass=f=850,"
            "tremolo=f=5:d=0.75,volume=0.34"
        )

    elif kind in ("fight", "impact"):
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = (
            "highpass=f=50,lowpass=f=1600,"
            "volume=0.42,"
            "afade=t=in:d=0.02,"
            "afade=t=out:st=0.65:d=0.8"
        )

    elif kind == "magic":
        source = (
            f"sine=frequency=95:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = (
            "tremolo=f=3:d=0.75,"
            "volume=0.19,"
            "afade=t=in:d=0.7,"
            "afade=t=out:st=3.5:d=1.2"
        )

    elif kind == "storm":
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = (
            "lowpass=f=420,volume=0.15,"
            "tremolo=f=0.25:d=0.4"
        )

    elif kind in ("tension", "mystery", "cliffhanger"):
        source = (
            f"sine=frequency=72:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = (
            "tremolo=f=2:d=0.55,"
            "lowpass=f=500,volume=0.11"
        )

    elif kind == "romance":
        source = (
            f"sine=frequency=440:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = "lowpass=f=1200,volume=0.035"

    else:
        source = (
            f"anoisesrc=color=pink:"
            f"sample_rate=44100:duration={duration}"
        )
        audio_filter = "lowpass=f=1000,volume=0.06"

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", source,
        "-af", audio_filter,
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], timeout=60)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Failed to create SFX: {kind}")

    return destination


def make_background_music(destination):
    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        f"sine=frequency=55:sample_rate=44100:duration={VIDEO_DURATION}",
        "-af",
        (
            "volume=0.035,"
            "tremolo=f=0.18:d=0.25,"
            "afade=t=in:d=2,"
            "afade=t=out:st=86:d=4"
        ),
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], timeout=90)

    return destination


# =========================================================
# CONCATENATION
# =========================================================

def write_concat_list(paths, destination):
    with open(destination, "w", encoding="utf-8") as file:
        for path in paths:
            resolved = Path(path).resolve().as_posix()

            if "'" in resolved:
                raise RuntimeError(
                    "Unsupported apostrophe in media file path."
                )

            file.write(f"file '{resolved}'\n")


def concatenate_video(paths, destination, workdir):
    list_file = workdir / "video_list.txt"
    write_concat_list(paths, list_file)

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-t", str(VIDEO_DURATION),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], timeout=240)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation failed.")

    return destination


def concatenate_audio(paths, destination, workdir, label):
    list_file = workdir / f"{label}_list.txt"
    write_concat_list(paths, list_file)

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-t", str(VIDEO_DURATION),
        "-vn",
        "-c:a", "aac",
        "-b:a", "128k",
        str(destination),
    ], timeout=180)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Audio concatenation failed: {label}")

    return destination


# =========================================================
# FINAL AUDIO MIX
# =========================================================

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


def mix_final_audio(video, voice, sfx, music, destination):
    inputs = {
        "video": video,
        "voice": voice,
        "sfx": sfx,
        "music": music,
    }

    for name, path in inputs.items():
        path = Path(path)

        if not path.exists() or path.stat().st_size < 1000:
            raise RuntimeError(
                f"Missing or empty mix input: {name}"
            )

    for name in ("voice", "sfx", "music"):
        if not has_audio_stream(inputs[name]):
            raise RuntimeError(
                f"No audio stream detected in {name}."
            )

    filter_graph = (
        "[1:a:0]"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=1.0,"
        "apad=whole_dur=90,"
        "atrim=duration=90,"
        "asetpts=PTS-STARTPTS[voice_track];"

        "[2:a:0]"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.34,"
        "apad=whole_dur=90,"
        "atrim=duration=90,"
        "asetpts=PTS-STARTPTS[sfx_track];"

        "[3:a:0]"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.22,"
        "apad=whole_dur=90,"
        "atrim=duration=90,"
        "asetpts=PTS-STARTPTS[music_track];"

        "[voice_track][sfx_track][music_track]"
        "amix=inputs=3:duration=longest:"
        "dropout_transition=2:normalize=0,"
        "alimiter=limit=0.92,"
        "atrim=duration=90,"
        "asetpts=PTS-STARTPTS[aout]"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-i", str(voice),
        "-i", str(sfx),
        "-i", str(music),
        "-filter_complex", filter_graph,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-t", str(VIDEO_DURATION),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(destination),
    ], timeout=240)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Final video file is missing or too small.")

    duration = probe_duration(destination)

    if duration is None or abs(duration - VIDEO_DURATION) > 0.35:
        raise RuntimeError(
            f"Final duration check failed: {duration}"
        )

    return destination


# =========================================================
# TELEGRAM ASYNC BRIDGE
# =========================================================

def send_coroutine(application, coroutine, timeout=360):
    loop = application.bot_data.get("event_loop")

    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError("Telegram event loop is unavailable.")

    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return future.result(timeout=timeout)


async def send_text(application, chat_id, text):
    await application.bot.send_message(
        chat_id=chat_id,
        text=text[:3500],
    )


async def send_video(application, chat_id, path, job_id):
    with open(path, "rb") as file:
        await application.bot.send_video(
            chat_id=chat_id,
            video=file,
            caption=(
                "اكتمل فيديو ظل ZIL.\n"
                f"رقم العملية: {job_id}"
            ),
            supports_streaming=True,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
        )


# =========================================================
# VIDEO JOB
# =========================================================

def build_video(job_id, chat_id, idea, telegram_app):
    global ACTIVE_JOBS

    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        update_job(
            job_id,
            status="running",
            stage="checking",
            progress=1,
        )

        if not command_exists("ffmpeg"):
            raise RuntimeError("FFmpeg is missing.")

        if not command_exists("ffprobe"):
            raise RuntimeError("FFprobe is missing.")

        if not PIXABAY_API_KEY:
            raise RuntimeError("PIXABAY_API_KEY is missing.")

        update_job(
            job_id,
            stage="writing_story",
            progress=2,
        )

        package = create_story_package(idea)
        scenes = package["scenes"]

        update_job(
            job_id,
            stage="story_ready",
            progress=5,
            title=package["title"],
            narration=package["narration"],
        )

        try:
            send_coroutine(
                telegram_app,
                send_text(
                    telegram_app,
                    chat_id,
                    (
                        f"اكتملت كتابة القصة: {package['title']}\n"
                        "بدأ تجهيز المشاهد والراوي العربي "
                        "والترجمة والمؤثرات الصوتية."
                    ),
                ),
                timeout=45,
            )
        except Exception:
            log.exception("Could not send story-ready message.")

        video_scenes = []
        voice_scenes = []
        sfx_scenes = []

        for index, scene in enumerate(scenes):
            number = index + 1

            update_job(
                job_id,
                stage=f"scene_{number}",
                progress=round(
                    5 + index / SCENE_COUNT * 78
                ),
            )

            try:
                video_url = search_pixabay_video(
                    scene["search_query"]
                )

            except Exception as first_error:
                log.warning(
                    "Primary search failed for scene %s: %s",
                    number,
                    first_error,
                )

                fallback_query = " ".join(
                    re.findall(
                        r"[A-Za-z0-9]+",
                        scene["visual_prompt"],
                    )[:8]
                )

                if len(fallback_query.split()) < 3:
                    fallback_query = (
                        "cinematic fantasy dramatic scene"
                    )

                video_url = search_pixabay_video(fallback_query)

            raw_video = workdir / f"raw_{number}.mp4"
            normalized = workdir / f"normalized_{number}.mp4"
            captioned = workdir / f"captioned_{number}.mp4"

            download_video(video_url, raw_video)
            normalize_scene(raw_video, normalized)

            caption_result = add_arabic_caption(
                normalized,
                scene["narration"],
                captioned,
                workdir,
                number,
            )

            video_scenes.append(
                Path(caption_result)
                if Path(caption_result).exists()
                else normalized
            )

            raw_video.unlink(missing_ok=True)

            raw_voice = workdir / f"voice_raw_{number}.mp3"
            fitted_voice = workdir / f"voice_{number}.m4a"

            create_narration(
                scene["narration"],
                raw_voice,
            )

            fit_audio_to_scene(
                raw_voice,
                fitted_voice,
            )

            voice_scenes.append(fitted_voice)

            sfx_path = workdir / f"sfx_{number}.m4a"

            make_scene_sfx(
                scene["effect"],
                sfx_path,
            )

            sfx_scenes.append(sfx_path)

        update_job(
            job_id,
            stage="concatenating",
            progress=85,
        )

        silent_video = workdir / "silent_video.mp4"
        voice_track = workdir / "voice_track.m4a"
        sfx_track = workdir / "sfx_track.m4a"
        music_track = workdir / "music_track.m4a"
        final_video = workdir / "ZIL_video.mp4"

        concatenate_video(
            video_scenes,
            silent_video,
            workdir,
        )

        concatenate_audio(
            voice_scenes,
            voice_track,
            workdir,
            "voice",
        )

        concatenate_audio(
            sfx_scenes,
            sfx_track,
            workdir,
            "sfx",
        )

        make_background_music(music_track)

        update_job(
            job_id,
            stage="mixing_audio",
            progress=94,
        )

        mix_final_audio(
            silent_video,
            voice_track,
            sfx_track,
            music_track,
            final_video,
        )

        if final_video.stat().st_size > MAX_VIDEO_SIZE:
            raise RuntimeError(
                "Final video exceeds the configured Telegram upload limit."
            )

        update_job(
            job_id,
            stage="sending",
            progress=98,
        )

        send_coroutine(
            telegram_app,
            send_video(
                telegram_app,
                chat_id,
                final_video,
                job_id,
            ),
            timeout=360,
        )

        update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            duration=probe_duration(final_video),
        )

        log.info("Job %s completed.", job_id)

    except Exception as error:
        report_error(job_id, error)

        try:
            send_coroutine(
                telegram_app,
                send_text(
                    telegram_app,
                    chat_id,
                    (
                        "تعذر إكمال الفيديو.\n"
                        f"رقم العملية: {job_id}\n"
                        "أرسل /last_error لمعرفة تفاصيل الخطأ."
                    ),
                ),
                timeout=45,
            )
        except Exception:
            log.exception("Could not notify user of job failure.")

    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(update, context):
    if update.message:
        await update.message.reply_text(
            "أهلًا بك في ظل ZIL.\n\n"
            "/test - إنشاء فيديو تجريبي\n"
            "/make فكرة القصة - إنشاء فيديو من فكرتك\n"
            "/status - حالة النظام والعمليات\n"
            "/diagnose - فحص الخدمات\n"
            "/last_error - آخر خطأ\n"
            "/health - فحص الخدمة"
        )


async def start_job(update, context, idea):
    global ACTIVE_JOBS

    if not update.message or not update.effective_chat:
        return

    idea = (idea or "").strip()[:MAX_STORY_LENGTH]

    if not idea:
        await update.message.reply_text(
            "أرسل فكرة القصة أولًا."
        )
        return

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            job_id = None
        else:
            job_id = uuid.uuid4().hex[:10]

            JOBS[job_id] = {
                "id": job_id,
                "chat_id": update.effective_chat.id,
                "status": "queued",
                "stage": "queued",
                "created_at": time.time(),
                "updated_at": time.time(),
                "progress": 0,
            }

            ACTIVE_JOBS += 1

    if job_id is None:
        await update.message.reply_text(
            "يوجد فيديو قيد المعالجة. حاول مرة أخرى لاحقًا."
        )
        return

    await update.message.reply_text(
        "بدأت عملية إنشاء فيديو ظل ZIL.\n"
        f"رقم العملية: {job_id}\n"
        "قد يستغرق إنتاج الفيديو عدة دقائق."
    )

    threading.Thread(
        target=build_video,
        args=(
            job_id,
            update.effective_chat.id,
            idea,
            context.application,
        ),
        daemon=True,
    ).start()


async def test_command(update, context):
    idea = (
        "في مملكة غامضة، يصل رجل يخفي قوة خارقة. "
        "تقع الأميرة في حبه، لكن الملك يرفض العلاقة. "
        "يظهر نمر عملاق أمام القصر ويهاجم الحراس. "
        "يواجهه الرجل ويكشف جزءًا من قوته المخفية، "
        "ثم يظهر سر جديد يهدد المملكة."
    )

    await start_job(update, context, idea)


async def make_command(update, context):
    idea = " ".join(context.args).strip()

    if not idea:
        await update.message.reply_text(
            "اكتب فكرة القصة بعد الأمر.\n\n"
            "مثال:\n"
            "/make رجل غامض يصل إلى مملكة ويخفي قوة أسطورية"
        )
        return

    await start_job(update, context, idea)


async def status_command(update, context):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        jobs = list(JOBS.values())[-5:]

    lines = [
        "حالة ظل ZIL",
        f"العمليات النشطة: {active}",
        f"Groq: {'مهيأ' if GROQ_API_KEY else 'مفتاح مفقود'}",
        f"Pixabay: {'مهيأ' if PIXABAY_API_KEY else 'مفتاح مفقود'}",
        f"FFmpeg: {'جاهز' if command_exists('ffmpeg') else 'غير موجود'}",
    ]

    for job in reversed(jobs):
        lines.append(
            f"\n{job['id']} | {job.get('status')} | "
            f"{job.get('stage')} | {job.get('progress', 0)}%"
        )

        if job.get("error"):
            lines.append(job["error"][:350])

    await update.message.reply_text(
        "\n".join(lines)[:3900]
    )


async def last_error_command(update, context):
    with JOBS_LOCK:
        failed_jobs = [
            job for job in JOBS.values()
            if job.get("status") == "failed"
        ]

    if not failed_jobs:
        await update.message.reply_text(
            "لا يوجد خطأ مسجل في ذاكرة العمليات الحالية."
        )
        return

    job = failed_jobs[-1]

    await update.message.reply_text(
        f"آخر خطأ في ظل ZIL\n"
        f"العملية: {job['id']}\n"
        f"المرحلة: {job.get('stage')}\n"
        f"الخطأ:\n{job.get('error', 'غير معروف')[:2500]}"
    )


async def diagnose_command(update, context):
    results = [
        f"Python: {sys.version.split()[0]}",
        f"Groq key: {'OK' if GROQ_API_KEY else 'MISSING'}",
        f"Pixabay key: {'OK' if PIXABAY_API_KEY else 'MISSING'}",
        f"Groq model: {GROQ_MODEL}",
        f"Groq max tokens: {GROQ_MAX_TOKENS}",
        f"Groq retries: {GROQ_RETRIES}",
        f"Groq max wait: {GROQ_MAX_WAIT}s",
        f"FFmpeg: {command_exists('ffmpeg')}",
        f"FFprobe: {command_exists('ffprobe')}",
        f"Target duration: {VIDEO_DURATION}s",
    ]

    await update.message.reply_text(
        "تشخيص ظل ZIL\n\n" + "\n".join(results)
    )


async def health_command(update, context):
    await update.message.reply_text(
        "ظل ZIL يعمل.\n"
        f"FFmpeg: {command_exists('ffmpeg')}\n"
        f"FFprobe: {command_exists('ffprobe')}\n"
        f"Groq configured: {bool(GROQ_API_KEY)}\n"
        f"Pixabay configured: {bool(PIXABAY_API_KEY)}\n"
        f"Target duration: {VIDEO_DURATION} seconds"
    )


async def telegram_error_handler(update, context):
    error = context.error

    if error:
        log.error(
            "TELEGRAM HANDLER ERROR",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )
    else:
        log.error("Telegram handler reported an unknown error.")


# =========================================================
# FLASK HEALTH ENDPOINTS
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "service": "ZIL",
        "status": "running",
        "target_duration_seconds": VIDEO_DURATION,
    })


@app.get("/health")
def health_endpoint():
    return jsonify({
        "status": "ok",
        "ffmpeg": command_exists("ffmpeg"),
        "ffprobe": command_exists("ffprobe"),
        "groq_configured": bool(GROQ_API_KEY),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "active_jobs": get_active_jobs(),
    })


def run_web():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# STARTUP
# =========================================================

def main():
    log.info("Starting ZIL bot.")

    if not BOT_TOKEN:
        log.critical("BOT_TOKEN is missing.")
        sys.exit(1)

    if not command_exists("ffmpeg"):
        log.critical("FFmpeg is missing.")
        sys.exit(1)

    if not command_exists("ffprobe"):
        log.critical("FFprobe is missing.")
        sys.exit(1)

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    async def post_init(application):
        application.bot_data["event_loop"] = (
            asyncio.get_running_loop()
        )

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )
    telegram_app.add_handler(
        CommandHandler("test", test_command)
    )
    telegram_app.add_handler(
        CommandHandler("make", make_command)
    )
    telegram_app.add_handler(
        CommandHandler("status", status_command)
    )
    telegram_app.add_handler(
        CommandHandler("diagnose", diagnose_command)
    )
    telegram_app.add_handler(
        CommandHandler("last_error", last_error_command)
    )
    telegram_app.add_handler(
        CommandHandler("health", health_command)
    )

    telegram_app.add_error_handler(
        telegram_error_handler
    )

    log.info("Telegram polling starting.")

    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
