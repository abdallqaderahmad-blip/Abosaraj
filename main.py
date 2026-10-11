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
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL V80 FREE CINEMA
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
POLLINATIONS_IMAGE_URL = "https://image.pollinations.ai/prompt/"
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

FREE_MODE = True
ALLOW_PAID_APIS = False

PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 18
MAX_EPISODES = 20

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

TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()

TTS_RATE = os.getenv("TTS_RATE", "-5%").strip()

SUBTITLES_ENABLED = True
SCENE_TRANSITION_SECONDS = 0.25

VOICE_VOLUME = 1.0
SFX_VOLUME = 0.09
MUSIC_VOLUME = 0.16

DATA_ROOT = Path(
    os.getenv("ZIL_DATA_DIR", tempfile.gettempdir())
)

BASE_DIR = DATA_ROOT / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = BASE_DIR / "jobs_state.json"
STORY_PROGRESS_FILE = BASE_DIR / "story_progress.json"
CHARACTER_MEMORY_FILE = BASE_DIR / "characters_memory.json"

THUMBNAIL_DIRNAME = "thumbnails"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            BASE_DIR / "zil.log",
            encoding="utf-8"
        )
    ]
)

log = logging.getLogger("ZIL")

app = Flask(__name__)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "ZIL-V80-Free-Cinema/8.0"
})

JOBS = {}
JOBS_LOCK = threading.RLock()
STATE_LOCK = threading.RLock()
STORY_LOCK = threading.RLock()

ACTIVE_JOBS = 0

EFFECTS = {
    "mystery", "castle", "steps", "romance",
    "tension", "storm", "beast", "fight",
    "impact", "magic", "cliffhanger"
}


# =========================================================
# JSON AND PERSISTENCE
# =========================================================

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return default if default is not None else {}


def load_state():
    global JOBS

    with STATE_LOCK:
        loaded = read_json(STATE_FILE, {})

        if not isinstance(loaded, dict):
            loaded = {}

        JOBS = loaded

        # Jobs that were running before a restart cannot be
        # assumed to still have an active worker.
        for job in JOBS.values():
            if job.get("status") in (
                "queued", "running", "uploading"
            ):
                job["status"] = "interrupted"
                job["stage"] = "interrupted"
                job["message"] = (
                    "انقطع التنفيذ بسبب إعادة تشغيل الخدمة"
                )

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


def save_story_progress(data):
    with STORY_LOCK:
        atomic_json(STORY_PROGRESS_FILE, data)


def load_character_memory():
    data = read_json(CHARACTER_MEMORY_FILE, {})
    return data if isinstance(data, dict) else {}


def save_character_memory(data):
    with STORY_LOCK:
        atomic_json(CHARACTER_MEMORY_FILE, data)


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
            combined = (
                old_story + "\n\n" + episode_story
            ).strip()
        else:
            combined = old_story

        combined = combined[-MAX_STORY_LENGTH:]

        progress[key] = {
            "episode": episode,
            "series_title": package.get(
                "series_title", "ظل"
            ),
            "full_story_so_far": combined,
            "last_episode_text": episode_story[-1200:],
            "last_cliffhanger": str(
                package.get("cliffhanger", "")
            )[:1000],
            "updated_at": time.time()
        }

        old_memory = memory.get(key, {})

        character_memory = package.get(
            "character_memory",
            old_memory.get("characters", {})
        )

        memory[key] = {
            "characters": character_memory,
            "updated_at": time.time()
        }

        atomic_json(STORY_PROGRESS_FILE, progress)
        atomic_json(CHARACTER_MEMORY_FILE, memory)


# =========================================================
# FFMPEG UTILITIES
# =========================================================

def run_command(command, timeout=300):
    log.info("Running command: %s", command[0])

    result = subprocess.run(
        [str(x) for x in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "")[-4000:]
        raise RuntimeError(
            f"Command failed ({result.returncode}): {error}"
        )

    return result


def probe_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], 60)

    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def safe_duration(path, fallback=BASE_SCENE_DURATION):
    duration = probe_duration(path)
    return duration if duration > 0 else fallback


def verify_dependencies():
    missing = [
        name for name in ("ffmpeg", "ffprobe")
        if not __import__("shutil").which(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing system dependencies: " + ", ".join(missing)
        )


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def telegram_send_message(chat_id, text):
    if not BOT_TOKEN:
        return

    response = HTTP.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": str(text)[:4000]
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(str(result))


def telegram_send_video(chat_id, path, caption):
    with open(path, "rb") as media:
        response = HTTP.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
            data={
                "chat_id": chat_id,
                "caption": caption[:1000]
            },
            files={
                "video": (
                    Path(path).name,
                    media,
                    "video/mp4"
                )
            },
            timeout=(30, 600)
        )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError(response.text[:1000])


def telegram_send_photo(chat_id, path, caption=""):
    with open(path, "rb") as media:
        response = HTTP.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption[:1000]
            },
            files={
                "photo": (
                    Path(path).name,
                    media,
                    "image/jpeg"
                )
            },
            timeout=(20, 120)
        )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError(response.text[:1000])


# =========================================================
# GROQ STORY GENERATION
# =========================================================

def extract_json(text):
    text = (text or "").strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I
    )

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

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": GROQ_MAX_TOKENS,
        "response_format": {"type": "json_object"}
    }

    last_error = "Unknown Groq error"

    for attempt in range(GROQ_RETRIES):
        try:
            response = HTTP.post(
                GROQ_API_URL,
                headers=headers,
                json=payload,
                timeout=120
            )

            if response.status_code == 429:
                wait = min(
                    GROQ_MAX_WAIT,
                    5 * (attempt + 1)
                )

                last_error = (
                    "Groq rate limit; retry later"
                )

                if attempt < GROQ_RETRIES - 1:
                    time.sleep(wait)
                    continue

            response.raise_for_status()

            data = response.json()

            content = (
                data["choices"][0]["message"]["content"]
            )

            return extract_json(content)

        except Exception as exc:
            last_error = str(exc)

            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(10, 2 ** attempt))

    raise RuntimeError(
        "Groq story generation failed: " + last_error
    )


def normalize_package(package, episode, idea):
    if not isinstance(package, dict):
        raise RuntimeError("Story package is not an object")

    scenes = package.get("scenes")

    if not isinstance(scenes, list):
        raise RuntimeError("Story scenes are missing")

    if len(scenes) != SCENE_COUNT:
        raise RuntimeError(
            f"Expected {SCENE_COUNT} scenes, got {len(scenes)}"
        )

    normalized = []

    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise RuntimeError(
                f"Scene {index + 1} is invalid"
            )

        narration = str(scene.get("narration", "")).strip()
        action = str(scene.get("action", "")).strip()
        effect = str(scene.get("effect", "mystery")).lower()

        if not narration:
            raise RuntimeError(
                f"Scene {index + 1} has no narration"
            )

        if not action:
            action = narration

        if effect not in EFFECTS:
            effect = "mystery"

        normalized.append({
            "narration": narration[:1200],
            "action": action[:1500],
            "effect": effect
        })

    package["scenes"] = normalized
    package["episode"] = episode
    package["title"] = str(
        package.get("title", f"الحلقة {episode}")
    )[:200]
    package["series_title"] = str(
        package.get("series_title", "ظل")
    )[:100]
    package["story"] = str(
        package.get("story", idea)
    )[:6000]
    package["cliffhanger"] = str(
        package.get("cliffhanger", "")
    )[:1000]

    if not package["cliffhanger"]:
        package["cliffhanger"] = normalized[-1]["narration"]

    return package


def create_story_package(
    idea, user_id, episode, previous_story=None
):
    memory_all = load_character_memory()
    memory = memory_all.get(str(user_id), {}).get(
        "characters", {}
    )

    previous_story = previous_story or {}

    system_prompt = f"""
You are the lead screenwriter for ZIL, an Arabic fantasy drama.

Return one valid JSON object and nothing else.

Create episode {episode} of a connected series with a maximum
of {MAX_EPISODES} episodes.

The episode must contain EXACTLY {SCENE_COUNT} scenes.

Every scene must have:
- narration: a natural Arabic narration sentence or short paragraph
- action: a detailed cinematic visual description
- effect: one of {sorted(EFFECTS)}

Required story principles:
- Mysterious powerful male hero.
- Princess develops feelings for him.
- The king opposes or distrusts him.
- A small white wolf is important and never speaks.
- Maintain character identity, clothing, appearance and abilities.
- Use suspense, danger, emotional escalation and revelations.
- The last scene ends on a compelling cliffhanger.
- Do not resolve the entire series before episode 20.
- Episode 1 establishes the world and the main mystery.
- Later episodes continue directly from the saved cliffhanger.
- No modern objects unless the story requires them.
- The action descriptions should be suitable for vertical
  photorealistic fantasy illustrations.
- Narration must be Arabic.

JSON structure:
{{
  "series_title": "Arabic series title",
  "title": "Episode title",
  "episode": {episode},
  "story": "Episode story summary in Arabic",
  "cliffhanger": "Exact unresolved ending in Arabic",
  "character_memory": {{
    "hero": "consistent physical appearance and clothing",
    "princess": "consistent physical appearance and clothing",
    "king": "consistent physical appearance and clothing",
    "wolf": "small white wolf appearance"
  }},
  "scenes": [
    {{
      "narration": "Arabic narration",
      "action": "Visual description",
      "effect": "mystery"
    }}
  ]
}}
"""

    user_prompt = f"""
NEW STORY IDEA:
{idea}

PREVIOUS EPISODE:
{previous_story.get("last_episode_text", "")}

PREVIOUS CLIFFHANGER:
{previous_story.get("last_cliffhanger", "")}

PREVIOUS STORY SUMMARY:
{previous_story.get("full_story_so_far", "")[-2500:]}

CHARACTER MEMORY:
{json.dumps(memory, ensure_ascii=False)}

For episode 1, start the series from the beginning.
For later episodes, continue from the previous cliffhanger.
"""

    package = groq_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ])

    return normalize_package(package, episode, idea)


# =========================================================
# IMAGE GENERATION
# =========================================================

def build_scene_image_prompt(scene, package, index):
    memory = package.get("character_memory", {})

    characters = json.dumps(
        memory,
        ensure_ascii=False
    )

    return (
        "Photorealistic live-action cinematic fantasy film still. "
        "Vertical 9:16 composition. Real human anatomy, realistic "
        "skin texture, practical-looking medieval costumes, "
        "cinematic lighting, atmospheric depth, realistic materials. "
        "No cartoon, no anime, no watermark, no text, no subtitles, "
        "no borders, no black bars. "
        f"Series: {package.get('series_title', 'ZIL')}. "
        f"Episode: {package.get('episode', 1)}. "
        f"Scene: {index + 1}/{SCENE_COUNT}. "
        f"Character continuity: {characters}. "
        f"Scene action: {scene['action']}. "
        "Preserve the established appearances of all recurring "
        "characters. Compose the most important subject in the "
        "central vertical safe area. High-detail fantasy cinema."
    )


def save_generated_image(url, destination):
    with requests.get(
        url,
        stream=True,
        timeout=(20, IMAGE_GENERATION_TIMEOUT),
        headers={"User-Agent": "ZIL-V80-Free-Cinema/8.0"}
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get(
            "content-type", ""
        ).lower()

        if not content_type.startswith("image/"):
            raise RuntimeError(
                f"Non-image response: {content_type}"
            )

        total = 0

        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_SOURCE_IMAGE_BYTES:
                    raise RuntimeError(
                        "Image exceeds size limit"
                    )

                output.write(chunk)

    if total < 5000:
        Path(destination).unlink(missing_ok=True)
        raise RuntimeError("Image is too small")

    return destination


def pollinations_generate_image(
    prompt, destination, seed
):
    url = POLLINATIONS_IMAGE_URL + quote(
        prompt, safe=""
    )

    params = {
        "width": 768,
        "height": 1365,
        "seed": int(seed),
        "nologo": "true",
        "safe": "true",
        "model": "flux"
    }

    response = HTTP.get(
        url,
        params=params,
        headers={"Accept": "image/*"},
        timeout=(20, IMAGE_GENERATION_TIMEOUT)
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "content-type", ""
    ).lower()

    if not content_type.startswith("image/"):
        raise RuntimeError(
            "Image provider returned a non-image response"
        )

    if len(response.content) < 5000:
        raise RuntimeError("Generated image is too small")

    if len(response.content) > MAX_SOURCE_IMAGE_BYTES:
        raise RuntimeError("Generated image is too large")

    Path(destination).write_bytes(response.content)
    return destination


def pixabay_generate_image(prompt, destination, index):
    key = os.getenv("PIXABAY_API_KEY", "").strip()

    if not key:
        raise RuntimeError("Pixabay key not configured")

    words = re.findall(
        r"[A-Za-z]{3,}",
        prompt.lower()
    )

    stop = {
        "photorealistic", "cinematic", "realistic",
        "vertical", "composition", "scene", "series",
        "character", "characters", "preserve"
    }

    query_words = []

    for word in words:
        if word not in stop and word not in query_words:
            query_words.append(word)

        if len(query_words) >= 7:
            break

    query = " ".join(query_words) or "medieval castle fantasy"

    response = HTTP.get(
        "https://pixabay.com/api/",
        params={
            "key": key,
            "q": query,
            "image_type": "photo",
            "orientation": "vertical",
            "safesearch": "true",
            "per_page": 15
        },
        timeout=(15, 35)
    )

    response.raise_for_status()

    hits = response.json().get("hits", [])

    if not hits:
        raise RuntimeError("No Pixabay images found")

    hit = hits[(index * 3) % len(hits)]

    image_url = (
        hit.get("largeImageURL")
        or hit.get("webformatURL")
    )

    if not image_url:
        raise RuntimeError("Pixabay image URL missing")

    return save_generated_image(image_url, destination)


def generate_scene_image(prompt, destination, index):
    errors = []

    for attempt in range(2):
        try:
            return pollinations_generate_image(
                prompt,
                destination,
                seed=7600 + index * 101 + attempt
            )

        except Exception as exc:
            errors.append(str(exc))
            log.warning(
                "Image generation attempt failed: %s",
                exc
            )

            if attempt == 0:
                time.sleep(2)

    try:
        return pixabay_generate_image(
            prompt, destination, index
        )
    except Exception as exc:
        errors.append(str(exc))

    raise RuntimeError(
        "No image provider succeeded: "
        + "; ".join(errors[-3:])
    )


# =========================================================
# SCENE VISUALS
# =========================================================

def animate_still_image(
    source, destination, duration, index
):
    duration = max(1.0, float(duration))
    frames = max(1, int(duration * VIDEO_FPS))

    x_expr = (
        "iw/2-(iw/zoom/2)"
        "+(iw/zoom/10)*sin(on/45)"
    )

    y_expr = "ih/2-(ih/zoom/2)"

    if index % 4 == 1:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = (
            "ih/2-(ih/zoom/2)"
            "+(ih/zoom/10)*sin(on/45)"
        )

    elif index % 4 == 2:
        x_expr = (
            "iw/2-(iw/zoom/2)"
            "-(iw/zoom/10)*sin(on/45)"
        )

    elif index % 4 == 3:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = (
            "ih/2-(ih/zoom/2)"
            "-(ih/zoom/10)*sin(on/45)"
        )

    vf = (
        "scale=1000:1800:force_original_aspect_ratio=increase,"
        "crop=1000:1800,"
        f"zoompan=z='min(zoom+0.001,1.12)':"
        f"x='{x_expr}':y='{y_expr}':"
        f"d=1:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
        f"trim=end_frame={frames},"
        "setpts=PTS-STARTPTS,"
        "eq=contrast=1.06:saturation=1.08,"
        "vignette=PI/5,"
        "noise=alls=8:allf=t,"
        f"fade=t=in:st=0:d={SCENE_TRANSITION_SECONDS},"
        f"fade=t=out:st={max(0.1, duration - SCENE_TRANSITION_SECONDS):.3f}:"
        f"d={SCENE_TRANSITION_SECONDS},"
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
        str(destination)
    ], 300)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Image animation failed")

    return destination


def obtain_scene_visual(
    scene, package, workdir, index, duration
):
    number = index + 1

    raw_image = workdir / f"ai_scene_{number}.jpg"
    normalized_image = workdir / f"ai_scene_{number}.png"
    normalized_video = workdir / f"visual_{number}.mp4"

    if not normalized_image.exists():
        if not raw_image.exists():
            prompt = build_scene_image_prompt(
                scene, package, index
            )

            generate_scene_image(
                prompt, raw_image, index
            )

        run_command([
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "error",
            "-i", str(raw_image),
            "-frames:v", "1",
            "-vf",
            "scale=1000:1800:force_original_aspect_ratio=increase,"
            "crop=1000:1800",
            str(normalized_image)
        ], 120)

        if (
            not normalized_image.exists()
            or normalized_image.stat().st_size < 5000
        ):
            raise RuntimeError(
                f"Could not normalize scene {number}"
            )

    if (
        not normalized_video.exists()
        or normalized_video.stat().st_size < 1000
    ):
        animate_still_image(
            normalized_image,
            normalized_video,
            duration,
            index
        )

    return normalized_video, normalized_image


# =========================================================
# ARABIC SUBTITLES
# =========================================================

def add_arabic_caption(
    source, caption, destination, workdir,
    index, duration
):
    if not SUBTITLES_ENABLED:
        return source

    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display

    except ImportError:
        log.exception("Arabic subtitle dependencies unavailable")
        return source

    try:
        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        ]

        font_path = next(
            (
                path for path in font_candidates
                if Path(path).exists()
            ),
            None
        )

        if font_path:
            font = ImageFont.truetype(font_path, 36)
        else:
            font = ImageFont.load_default()

        overlay_height = 200

        overlay = Image.new(
            "RGBA",
            (VIDEO_WIDTH, overlay_height),
            (0, 0, 0, 200)
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
                (0, 0), shaped, font=font
            )

            if current and box[2] - box[0] > VIDEO_WIDTH - 50:
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        if len(lines) > 2:
            lines = lines[:2]

        for line_index, line in enumerate(lines):
            shaped = get_display(
                arabic_reshaper.reshape(line)
            )

            box = draw.textbbox(
                (0, 0), shaped, font=font
            )

            text_width = box[2] - box[0]
            x = max(10, (VIDEO_WIDTH - text_width) // 2)
            y = 35 + line_index * 60

            draw.text(
                (x, y),
                shaped,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0, 255)
            )

        png = workdir / f"caption_{index + 1}.png"
        overlay.save(png)

        run_command([
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(png),
            "-filter_complex",
            "[0:v][1:v]overlay=0:H-200:format=auto[v]",
            "-map", "[v]",
            "-t", f"{duration:.3f}",
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "27",
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            str(destination)
        ], 240)

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception("Subtitle rendering failed")

    return source


# =========================================================
# ARABIC NARRATION
# =========================================================

def create_narration(text, destination):
    if destination.exists() and destination.stat().st_size > 1000:
        return destination

    destination.unlink(missing_ok=True)

    try:
        import edge_tts

        async def generate():
            communicate = edge_tts.Communicate(
                text,
                voice=TTS_VOICE,
                rate=TTS_RATE
            )

            await communicate.save(str(destination))

        asyncio.run(generate())

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception("Edge TTS failed")

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False
        ).save(str(destination))

        if destination.exists() and destination.stat().st_size > 1000:
            return destination

    except Exception:
        log.exception("gTTS fallback failed")

    raise RuntimeError("Arabic narration generation failed")


def prepare_synced_voice(source, destination):
    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af",
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination)
    ], 150)

    duration = probe_duration(destination)

    if duration <= 0:
        raise RuntimeError("Voice conversion failed")

    return duration


def fit_audio_to_duration(source, destination, duration):
    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af",
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination)
    ], 150)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Voice alignment failed")

    return destination


# =========================================================
# SOUND EFFECTS AND MUSIC
# =========================================================

def make_scene_sfx(kind, destination, duration):
    duration = max(1.0, float(duration))
    kind = str(kind or "mystery").lower()

    if kind in ("storm", "beast", "fight", "impact"):
        source = (
            f"anoisesrc=color=pink:sample_rate=44100:"
            f"duration={duration:.3f}"
        )
        af = (
            "lowpass=f=260,highpass=f=35,"
            "tremolo=f=0.22:d=0.7,volume=0.22"
        )

    elif kind in ("magic", "cliffhanger"):
        source = (
            f"sine=frequency=420:sample_rate=44100:"
            f"duration={duration:.3f}"
        )
        af = (
            "tremolo=f=4:d=0.65,"
            "lowpass=f=2200,volume=0.10"
        )

    elif kind == "steps":
        source = (
            f"anoisesrc=color=white:sample_rate=44100:"
            f"duration={duration:.3f}"
        )
        af = (
            "highpass=f=650,lowpass=f=2800,"
            "tremolo=f=2.2:d=0.7,volume=0.09"
        )

    elif kind == "romance":
        source = (
            f"sine=frequency=196:sample_rate=44100:"
            f"duration={duration:.3f}"
        )
        af = (
            "tremolo=f=0.35:d=0.3,"
            "lowpass=f=800,volume=0.06"
        )

    else:
        source = (
            f"anoisesrc=color=pink:sample_rate=44100:"
            f"duration={duration:.3f}"
        )
        af = (
            "lowpass=f=700,highpass=f=70,"
            "tremolo=f=0.15:d=0.4,volume=0.10"
        )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", source,
        "-af",
        f"{af},"
        "afade=t=in:d=0.25,"
        f"afade=t=out:st={max(0.1, duration - 0.5):.3f}:d=0.45,"
        "aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo",
        "-t", f"{duration:.3f}",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "48k",
        str(destination)
    ], 150)

    if not destination.exists() or destination.stat().st_size < 500:
        raise RuntimeError("Sound effect generation failed")

    return destination


def prepare_background_music(destination, duration, workdir, package=None):
    music_filter = (
        "[0:a]volume=0.45,lowpass=f=180[a0];"
        "[1:a]volume=0.30,lowpass=f=650,"
        "tremolo=f=0.12:d=0.25[a1];"
        "[2:a]volume=0.15,highpass=f=180,"
        "lowpass=f=6500[a2];"
        "[a0][a1][a2]"
        "amix=inputs=3:duration=longest:normalize=0,"
        "alimiter=limit=0.85,"
        "afade=t=in:d=2,"
        f"afade=t=out:st={max(0.1, duration - 4):.3f}:d=4,"
        f"atrim=duration={duration:.3f}[out]"
    )

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency=82.41:sample_rate=44100:duration={duration}",
        "-f", "lavfi",
        "-i", f"anoisesrc=color=pink:sample_rate=44100:duration={duration}",
        "-filter_complex", music_filter,
        "-map", "[out]",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "80k",
        str(destination)
    ], 300)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Background music generation failed")

    return destination


# =========================================================
# CONCATENATION AND AUDIO MIX
# =========================================================

def write_concat_list(paths, destination):
    with open(destination, "w", encoding="utf-8") as output:
        for path in paths:
            resolved = Path(path).resolve().as_posix()

            if "'" in resolved:
                raise RuntimeError(
                    "Unsupported apostrophe in media path"
                )

            output.write(f"file '{resolved}'\n")


def concatenate_video(paths, destination, workdir):
    if not paths:
        raise RuntimeError("No scene videos")

    listing = workdir / "video_list.txt"
    write_concat_list(paths, listing)

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
        "-movflags", "+faststart",
        str(destination)
    ], 1200)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation failed")

    return destination


def concatenate_audio(paths, destination, workdir, label):
    listing = workdir / f"{label}_list.txt"
    write_concat_list(paths, listing)

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
        str(destination)
    ], 900)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(
            f"Audio concatenation failed: {label}"
        )

    return destination


def mix_final_audio(video, voice, sfx, music, destination):
    for label, path in {
        "video": video,
        "voice": voice,
        "sfx": sfx,
        "music": music
    }.items():
        if not Path(path).exists() or Path(path).stat().st_size < 500:
            raise RuntimeError(f"Missing mix input: {label}")

    duration = probe_duration(video)

    if duration <= 0:
        raise RuntimeError("Cannot determine video duration")

    graph = (
        "[1:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume={VOICE_VOLUME},"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS,"
        "asplit=2[voice_sc][voice_mix];"

        "[2:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume={SFX_VOLUME},"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[sfx_mix];"

        "[3:a]aresample=44100,"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume={MUSIC_VOLUME},"
        f"apad=whole_dur={duration:.3f},"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[music_raw];"

        "[music_raw][voice_sc]"
        "sidechaincompress="
        "threshold=0.025:ratio=5:attack=25:release=300"
        "[music_ducked];"

        "[voice_mix][sfx_mix][music_ducked]"
        "amix=inputs=3:duration=longest:"
        "dropout_transition=2:normalize=0,"
        "alimiter=limit=0.92,"
        f"atrim=duration={duration:.3f},"
        "asetpts=PTS-STARTPTS[aout]"
    )

    destination.unlink(missing_ok=True)

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
        str(destination)
    ], 900)

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Final audio mix failed")

    return destination


# =========================================================
# THUMBNAILS
# =========================================================

def create_thumbnails(workdir):
    try:
        from PIL import Image, ImageEnhance
    except ImportError:
        log.exception("Pillow unavailable")
        return []

    thumb_dir = workdir / THUMBNAIL_DIRNAME
    thumb_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        (1, "hero.jpg"),
        (9, "transformation.jpg"),
        (18, "cliffhanger.jpg")
    ]

    created = []

    for scene_number, filename in targets:
        source = workdir / f"ai_scene_{scene_number}.png"

        if not source.exists():
            source = workdir / f"ai_scene_{scene_number}.jpg"

        if not source.exists():
            continue

        try:
            with Image.open(source) as original:
                image = original.convert("RGB")

            width, height = image.size

            left = int(width * 0.25)
            top = int(height * 0.11)
            right = int(width * 0.75)
            bottom = int(height * 0.50)

            cropped = image.crop(
                (left, top, right, bottom)
            )

            cropped.thumbnail((1280, 1280))

            if cropped.width < 800:
                cropped = cropped.resize(
                    (
                        800,
                        int(800 * cropped.height / cropped.width)
                    ),
                    Image.Resampling.LANCZOS
                )

            cropped = ImageEnhance.Contrast(
                cropped
            ).enhance(1.06)

            cropped = ImageEnhance.Color(
                cropped
            ).enhance(1.08)

            destination = thumb_dir / filename

            cropped.save(
                destination,
                "JPEG",
                quality=93,
                optimize=True
            )

            if destination.exists() and destination.stat().st_size > 1000:
                created.append(destination)

        except Exception:
            log.exception("Thumbnail failed: %s", filename)

    return created


# =========================================================
# VIDEO JOB
# =========================================================

def create_video(job_id, idea, user_id, episode):
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    def progress(stage, percent, message):
        update_job(
            job_id,
            stage=stage,
            progress=percent,
            message=message
        )

        try:
            chat_id = JOBS.get(job_id, {}).get("chat_id")

            if chat_id:
                telegram_send_message(
                    chat_id,
                    f"{percent}% — {message}"
                )

        except Exception:
            log.exception("Progress notification failed")

    package_file = workdir / "story_package.json"
    package = None

    if package_file.exists():
        cached = read_json(package_file, None)

        if (
            isinstance(cached, dict)
            and cached.get("episode") == episode
            and isinstance(cached.get("scenes"), list)
            and len(cached["scenes"]) == SCENE_COUNT
        ):
            package = cached

    previous_story = get_user_story(user_id)

    if package is None:
        progress(
            "story_generation",
            2,
            f"كتابة الحلقة {episode} وحفظ تسلسل القصة..."
        )

        package = create_story_package(
            idea=idea,
            user_id=user_id,
            episode=episode,
            previous_story=previous_story
        )

        atomic_json(package_file, package)

    scenes = package["scenes"]

    update_job(
        job_id,
        title=package["title"],
        series_title=package.get("series_title", "ظل"),
        episode=episode,
        story=package["story"],
        scene_count=len(scenes)
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
            f"الحلقة {episode} — المشهد {number}/{SCENE_COUNT}"
        )

        raw_voice = workdir / f"voice_raw_{number}.mp3"
        converted_voice = workdir / f"voice_converted_{number}.m4a"
        aligned_voice = workdir / f"voice_{number}.m4a"

        if not aligned_voice.exists():
            if not raw_voice.exists():
                create_narration(
                    scene["narration"], raw_voice
                )

            prepare_synced_voice(
                raw_voice, converted_voice
            )

            voice_duration = safe_duration(
                converted_voice, BASE_SCENE_DURATION
            )

            scene_duration = max(
                BASE_SCENE_DURATION,
                voice_duration + 0.20
            )

            fit_audio_to_duration(
                converted_voice,
                aligned_voice,
                scene_duration
            )

        else:
            scene_duration = safe_duration(
                aligned_voice,
                BASE_SCENE_DURATION
            )

        durations.append(scene_duration)

        visual = workdir / f"visual_{number}.mp4"
        captioned = workdir / f"captioned_{number}.mp4"

        if captioned.exists() and captioned.stat().st_size > 1000:
            final_visual = captioned

        else:
            if not visual.exists() or visual.stat().st_size < 1000:
                visual, _ = obtain_scene_visual(
                    scene,
                    package,
                    workdir,
                    index,
                    scene_duration
                )

            if SUBTITLES_ENABLED:
                final_visual = add_arabic_caption(
                    visual,
                    scene["narration"],
                    captioned,
                    workdir,
                    index,
                    scene_duration
                )
            else:
                final_visual = visual

        update_job(job_id, current_scene=number)

        video_paths.append(final_visual)
        voice_paths.append(aligned_voice)

        sfx_file = workdir / f"sfx_{number}.m4a"

        if not sfx_file.exists() or sfx_file.stat().st_size < 1000:
            try:
                make_scene_sfx(
                    scene["effect"],
                    sfx_file,
                    scene_duration
                )
            except Exception:
                log.exception("SFX generation failed")
                make_scene_sfx(
                    "mystery",
                    sfx_file,
                    scene_duration
                )

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
        prepare_background_music(
            music, total_duration, workdir, package
        )

    progress(
        "audio_mix",
        90,
        "موازنة الراوي والموسيقى والمؤثرات..."
    )

    mix_final_audio(
        video_only,
        voice_all,
        sfx_all,
        music,
        final_output
    )

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        progress(
            "compressing",
            95,
            "ضغط الفيديو لإرساله عبر تيليغرام..."
        )

        compressed = workdir / "ZIL_compressed.mp4"

        run_command([
            "ffmpeg", "-y",
            "-hide_banner", "-loglevel", "error",
            "-i", str(final_output),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "32",
            "-vf", "scale=540:960",
            "-c:a", "aac",
            "-b:a", "80k",
            "-movflags", "+faststart",
            str(compressed)
        ], 900)

        if compressed.exists() and compressed.stat().st_size < final_output.stat().st_size:
            final_output = compressed

    if final_output.stat().st_size > MAX_VIDEO_SIZE:
        raise RuntimeError(
            "Video exceeds Telegram upload size limit"
        )

    progress("thumbnails", 97, "إنشاء الصور التسويقية...")

    thumbnails = create_thumbnails(workdir)

    update_story_after_episode(
        user_id, episode, package
    )

    update_job(
        job_id,
        thumbnails=[str(path) for path in thumbnails],
        output=str(final_output)
    )

    progress(
        "completed",
        100,
        f"اكتملت الحلقة {episode}!"
    )

    return final_output, package, thumbnails


# =========================================================
# JOB WORKER
# =========================================================

def job_worker(job_id, chat_id, user_id, idea, episode):
    global ACTIVE_JOBS

    try:
        update_job(
            job_id,
            status="running",
            stage="starting",
            progress=1,
            error=""
        )

        telegram_send_message(
            chat_id,
            f"🎬 بدأ إنتاج الحلقة {episode}.\n"
            f"العملية: {job_id}\n"
            f"عدد المشاهد: {SCENE_COUNT}"
        )

        output, package, thumbnails = create_video(
            job_id, idea, user_id, episode
        )

        update_job(
            job_id,
            status="uploading",
            stage="uploading",
            progress=98
        )

        telegram_send_message(
            chat_id,
            f"✅ اكتملت الحلقة {episode}: "
            f"{package['title']}\nجارٍ إرسال الفيديو..."
        )

        telegram_send_video(
            chat_id,
            output,
            f"🎬 {package['series_title']}\n"
            f"الحلقة {episode}: {package['title']}\n"
            "ZIL FREE CINEMA"
        )

        for thumbnail in thumbnails:
            labels = {
                "hero.jpg": "🖼️ صورة البطل",
                "transformation.jpg": "✨ صورة التحول",
                "cliffhanger.jpg": "⚡ صورة النهاية التشويقية"
            }

            try:
                telegram_send_photo(
                    chat_id,
                    thumbnail,
                    labels.get(thumbnail.name, "صورة تسويقية")
                )
            except Exception:
                log.exception("Thumbnail upload failed")

        update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            output=str(output),
            title=package["title"],
            error=""
        )

        telegram_send_message(
            chat_id,
            f"🎉 انتهت الحلقة {episode} بنجاح!\n"
            f"العملية: {job_id}\n"
            f"النهاية التشويقية:\n"
            f"{package.get('cliffhanger', '')[:700]}\n\n"
            "للمتابعة استخدم /next"
        )

    except Exception as exc:
        log.exception("Job failed: %s", job_id)

        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:3000]
        )

        try:
            telegram_send_message(
                chat_id,
                f"❌ تعذر إكمال الفيديو.\n"
                f"العملية: {job_id}\n"
                f"الحلقة: {episode}\n"
                f"السبب: {str(exc)[:1000]}\n\n"
                "راجع إعدادات الخدمة ثم جرّب /resume "
                f"{job_id}"
            )
        except Exception:
            log.exception("Failure notification failed")

    finally:
        global ACTIVE_JOBS

        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

            with STATE_LOCK:
                save_state_locked()


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def launch_job(update, context, idea, episode):
    global ACTIVE_JOBS

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if not FREE_MODE or ALLOW_PAID_APIS:
        await message.reply_text(
            "الإعدادات غير متوافقة مع وضع المجاني."
        )
        return

    idea = (idea or "").strip()

    if len(idea) < 5:
        await message.reply_text("اكتب فكرة أطول قليلًا.")
        return

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await message.reply_text(
                "⏳ يوجد عمل قيد التنفيذ. انتظر قليلًا."
            )
            return

        ACTIVE_JOBS += 1

        job_id = f"zil_{uuid.uuid4().hex[:8]}"

        workdir = BASE_DIR / job_id
        workdir.mkdir(parents=True, exist_ok=True)

        JOBS[job_id] = {
            "id": job_id,
            "user_id": user.id,
            "chat_id": chat.id,
            "idea": idea,
            "episode": episode,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created_at": time.time(),
            "updated_at": time.time()
        }

        with STATE_LOCK:
            save_state_locked()

    await message.reply_text(
        f"🎬 تم بدء الحلقة {episode}\n"
        f"العملية: {job_id}\n"
        f"عدد المشاهد: {SCENE_COUNT}\n"
        "ترجمة عربية + صوت + مؤثرات + صور تسويقية"
    )

    threading.Thread(
        target=job_worker,
        args=(job_id, chat.id, user.id, idea, episode),
        daemon=True
    ).start()


async def cmd_test(update, context):
    idea = " ".join(context.args).strip()

    if not idea:
        idea = (
            "رجل غامض يصل إلى مملكة قديمة. "
            "تظهر علامة متوهجة على ذراعه وتبدأ قوة "
            "سحرية مخفية بالاستيقاظ. الأميرة تنجذب إليه، "
            "والملك يخشى سرًا يعرفه عن ماضيه. "
            "ينتهي المشهد بكشف خطر جديد."
        )

    progress = load_story_progress()
    key = str(update.effective_user.id)

    if key in progress and progress[key].get("episode", 0) >= 1:
        await update.effective_message.reply_text(
            "عندك مسلسل محفوظ. استخدم /next للمتابعة "
            "أو /reset لبدء قصة جديدة."
        )
        return

    await launch_job(update, context, idea, 1)


async def cmd_next(update, context):
    user_id = update.effective_user.id
    story = get_user_story(user_id)

    if not story or not story.get("full_story_so_far"):
        await update.effective_message.reply_text(
            "اعمل /test أولًا لبدء المسلسل."
        )
        return

    episode = int(story.get("episode", 0)) + 1

    if episode > MAX_EPISODES:
        await update.effective_message.reply_text(
            "اكتملت الحلقات العشرون."
        )
        return

    idea = (
        f"تابع مباشرة الحلقة {episode - 1}. "
        f"ملخص القصة: "
        f"{story.get('full_story_so_far', '')[-2000:]}. "
        f"النهاية السابقة: "
        f"{story.get('last_cliffhanger', '')}. "
        "لا تبدأ قصة جديدة ولا تتجاهل الأحداث السابقة."
    )

    await launch_job(
        update, context, idea, episode
    )


async def cmd_reset(update, context):
    user_id = str(update.effective_user.id)

    with STORY_LOCK:
        progress = load_story_progress()
        memory = load_character_memory()

        progress.pop(user_id, None)
        memory.pop(user_id, None)

        atomic_json(STORY_PROGRESS_FILE, progress)
        atomic_json(CHARACTER_MEMORY_FILE, memory)

    await update.effective_message.reply_text(
        "تم مسح ذاكرة المسلسل. استخدم /test للبدء من الحلقة 1."
    )


async def cmd_status(update, context):
    user_id = update.effective_user.id
    story = get_user_story(user_id)

    if not story:
        await update.effective_message.reply_text(
            "ما عندك مسلسل محفوظ. استخدم /test."
        )
        return

    await update.effective_message.reply_text(
        f"📊 حالة المسلسل:\n"
        f"الحلقة: {story.get('episode')}\n"
        f"السلسلة: {story.get('series_title')}\n"
        f"طول القصة المحفوظة: "
        f"{len(story.get('full_story_so_far', ''))} حرف\n"
        f"آخر تشويق:\n"
        f"{story.get('last_cliffhanger', '')[:300]}"
    )


async def cmd_jobs(update, context):
    user_id = update.effective_user.id

    with JOBS_LOCK:
        user_jobs = [
            job.copy()
            for job in JOBS.values()
            if job.get("user_id") == user_id
        ]

    if not user_jobs:
        await update.effective_message.reply_text(
            "لا توجد عمليات محفوظة."
        )
        return

    user_jobs.sort(
        key=lambda job: job.get("created_at", 0),
        reverse=True
    )

    lines = ["📋 آخر عمليات ZIL:"]

    for job in user_jobs[:8]:
        lines.append(
            f"{job.get('id')} | "
            f"{job.get('status')} | "
            f"{job.get('progress', 0)}%"
        )

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


async def cmd_resume(update, context):
    if not context.args:
        await update.effective_message.reply_text(
            "استخدم /resume ثم معرّف العملية."
        )
        return

    job_id = context.args[0].strip()

    with JOBS_LOCK:
        job = JOBS.get(job_id)

        if not job or job.get("user_id") != update.effective_user.id:
            await update.effective_message.reply_text(
                "العملية غير موجودة أو لا تخص حسابك."
            )
            return

        if job.get("status") not in (
            "failed", "interrupted"
        ):
            await update.effective_message.reply_text(
                "يمكن استئناف العمليات الفاشلة أو المتوقفة فقط."
            )
            return

        episode = int(job.get("episode", 1))
        idea = job.get("idea", "")

    await launch_job(
        update, context, idea, episode
    )


async def cmd_start(update, context):
    await update.effective_message.reply_text(
        "🎬 ZIL V80 FREE CINEMA\n\n"
        "/test - بدء الحلقة 1\n"
        "/next - متابعة الحلقة التالية\n"
        "/reset - مسح ذاكرة القصة\n"
        "/status - حالة المسلسل\n"
        "/jobs - عرض العمليات\n"
        "/resume ID - إعادة محاولة عملية فاشلة\n\n"
        "18 مشهدًا لكل حلقة، مع الراوي العربي "
        "والترجمة والمؤثرات والصور."
    )


# =========================================================
# HEALTH ENDPOINTS
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "ZIL V80 FREE CINEMA running",
        "episodes": MAX_EPISODES,
        "scenes": SCENE_COUNT,
        "free_mode": FREE_MODE,
        "paid_apis_allowed": ALLOW_PAID_APIS
    })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "groq_configured": bool(GROQ_API_KEY),
        "bot_configured": bool(BOT_TOKEN),
        "active_jobs": ACTIVE_JOBS
    })


def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False
    )


# =========================================================
# MAIN
# =========================================================

def main():
    global ACTIVE_JOBS

    load_state()
    verify_dependencies()

    ACTIVE_JOBS = 0

    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", cmd_start)
    )
    application.add_handler(
        CommandHandler("test", cmd_test)
    )
    application.add_handler(
        CommandHandler("next", cmd_next)
    )
    application.add_handler(
        CommandHandler("reset", cmd_reset)
    )
    application.add_handler(
        CommandHandler("status", cmd_status)
    )
    application.add_handler(
        CommandHandler("jobs", cmd_jobs)
    )
    application.add_handler(
        CommandHandler("resume", cmd_resume)
    )

    log.info(
        "ZIL V80 started on port %s",
        PORT
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
