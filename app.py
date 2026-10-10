import os
import sys
import re
import time
import uuid
import json
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
# ZIL CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-20b"
).strip()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
PIXABAY_API = "https://pixabay.com/api/videos/"

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

TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
TTS_RATE = os.getenv("TTS_RATE", "-10%")

GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "6000"))
GROQ_RETRIES = int(os.getenv("GROQ_RETRIES", "4"))
GROQ_MAX_WAIT = int(os.getenv("GROQ_MAX_WAIT", "45"))

BASE_DIR = Path(tempfile.gettempdir()) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BASE_DIR / "zil.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("ZIL")
app = Flask(__name__)

JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-Video-Bot/1.0"})

EFFECTS = {
    "mystery", "castle", "steps", "romance", "tension",
    "storm", "beast", "fight", "impact", "magic",
    "cliffhanger"
}

FALLBACK_SCENES = [
    (
        "cinematic mysterious kingdom mountains castle",
        "في مملكة بعيدة، كان سر قديم يقترب من الظهور.",
        "mystery",
    ),
    (
        "dark medieval castle exterior cinematic",
        "خلف أسوار القصر، كان الجميع يخشى ما لا يعرفه.",
        "castle",
    ),
    (
        "mysterious man walking cloak cinematic silhouette",
        "ثم وصل رجل غامض، يخفي قوة لا يريد لأحد رؤيتها.",
        "steps",
    ),
    (
        "medieval palace princess royal hall cinematic",
        "رأت الأميرة فيه شيئًا مختلفًا عن كل من عرفتهم.",
        "romance",
    ),
    (
        "medieval king throne room serious king",
        "لكن الملك رفض اقترابه، وكأن ماضيه يحمل خطرًا.",
        "castle",
    ),
    (
        "princess looking toward mysterious man dramatic",
        "لم تتراجع الأميرة، بينما ازدادت الشكوك حول الغريب.",
        "tension",
    ),
    (
        "dark forest storm clouds ominous cinematic",
        "وفجأة، اهتزت الأرض وغطّى الهدير أطراف المملكة.",
        "storm",
    ),
    (
        "large tiger roaring close up wildlife",
        "ظهر نمر هائل، وزمجر حتى ارتجفت بوابات القصر.",
        "beast",
    ),
    (
        "tiger running charging wildlife dramatic",
        "اندفع الوحش نحو القصر، ولم يعد أمام الحراس وقت.",
        "beast",
    ),
    (
        "medieval guards running castle dramatic",
        "تراجع الحراس، ووقف الملك عاجزًا أمام الخطر.",
        "steps",
    ),
    (
        "mysterious warrior facing giant beast cinematic",
        "عندها تقدّم الرجل بهدوء، وكأنه كان ينتظر هذه اللحظة.",
        "tension",
    ),
    (
        "tiger attack action wildlife dust dramatic",
        "انقضّ النمر، فاشتعلت المواجهة وسط الغبار والصراخ.",
        "fight",
    ),
    (
        "fantasy warrior fighting giant beast cinematic",
        "تفادى الضربة الأولى، ثم ردّ بقوة لم يتوقعها أحد.",
        "fight",
    ),
    (
        "epic action impact dust ground cinematic",
        "دوّى الاصطدام، وتراجعت خطوات الوحش لأول مرة.",
        "impact",
    ),
    (
        "blue magical energy lightning fantasy warrior",
        "بدأت طاقة غريبة تتوهّج حوله، وانكشف جزء من سره.",
        "magic",
    ),
    (
        "epic fantasy battle energy burst smoke",
        "تجمّد الجميع حين أدركوا أن ضعفه كان مجرد تمويه.",
        "magic",
    ),
    (
        "giant tiger defeated lying ground cinematic",
        "سقط الوحش، لكن الرجل أخفى قوته قبل أن يراه الملك.",
        "impact",
    ),
    (
        "dark castle night mysterious silhouette cliffhanger",
        "وفي تلك اللحظة، ظهر أثر جديد… سرّ أخطر ينتظرهم.",
        "cliffhanger",
    ),
]


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


def run_command(cmd, timeout=180):
    # تجاهل الوسائط الفارغة حتى لا تتسبب بخلل في FFmpeg.
    cmd = [str(x) for x in cmd if str(x) != ""]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown")[-2500:]
        raise RuntimeError(
            f"Command failed ({result.returncode}): {detail}"
        )

    return result


def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)
            JOBS[job_id]["updated_at"] = time.time()


def get_active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


def notify_admin(message):
    if not ADMIN_CHAT_ID or not BOT_TOKEN:
        return

    try:
        HTTP.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_CHAT_ID,
                "text": message[:3500],
            },
            timeout=15,
        ).raise_for_status()
    except Exception:
        log.exception("Admin notification failed")


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
        error=str(error)[:2000],
    )

    threading.Thread(
        target=notify_admin,
        args=(
            f"ZIL VIDEO ERROR\nJob: {job_id}\nError: {str(error)[:2500]}",
        ),
        daemon=True,
    ).start()


def probe_duration(path):
    result = run_command(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=25,
    )

    try:
        return float(result.stdout.strip())
    except Exception:
        return None


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def extract_json(text):
    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        (text or "").strip(),
        flags=re.I,
    )

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])

        raise


# =========================================================
# GROQ STORY GENERATION
# =========================================================

def create_story_package(idea):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")

    system_prompt = """
أنت كاتب سيناريو فانتازيا محترف.
أخرج JSON صالحًا فقط دون Markdown.

اكتب قصة عربية مترابطة فيها بداية وتصاعد وخطر وكشف وخطاف للنهاية.
أنشئ 18 مشهدًا بالضبط.

كل مشهد يحتوي:
narration: جملة عربية قصيرة مناسبة لمشهد مدته خمس ثوانٍ.
visual_prompt: وصف بصري بالإنجليزية لما يظهر في المشهد.
search_query: كلمات إنجليزية مناسبة للبحث عن فيديو stock.
effect: واحدة من:
mystery, castle, steps, romance, tension, storm,
beast, fight, impact, magic, cliffhanger.

اجعل ترتيب المشاهد متتابعًا ومتوافقًا مع أحداث القصة.
مجموع السرد نحو 155 إلى 195 كلمة.
لا تضف كتابة داخل الصورة.
لا تضف مفاتيح أخرى.

الصيغة:
{
"title":"عنوان القصة",
"story":"ملخص القصة",
"scenes":[
{
"narration":"السرد العربي",
"visual_prompt":"English visual description",
"search_query":"English search words",
"effect":"mystery"
}
]
}
"""

    last_error = None

    for attempt in range(GROQ_RETRIES):
        try:
            response = HTTP.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": (
                                "فكرة القصة: "
                                + str(idea)[:MAX_STORY_LENGTH]
                                + "\nأخرج JSON صالحًا فيه 18 مشهدًا بالضبط."
                            ),
                        },
                    ],
                    "temperature": 0.45,
                    "max_tokens": GROQ_MAX_TOKENS,
                },
                timeout=120,
            )

            if response.status_code == 429:
                try:
                    body = response.json().get("error", {})
                except Exception:
                    body = {}

                message = str(body.get("message", ""))
                wait = None

                match = re.search(
                    r"try again in\s+([0-9.]+)s",
                    message,
                    re.I,
                )

                if match:
                    wait = float(match.group(1)) + 1

                retry_after = response.headers.get("retry-after")

                if retry_after:
                    try:
                        wait = max(wait or 0, float(retry_after))
                    except ValueError:
                        pass

                wait = min(
                    GROQ_MAX_WAIT,
                    max(wait or (6 * (2 ** attempt)), 2),
                )

                if attempt == GROQ_RETRIES - 1:
                    raise RuntimeError(
                        "Groq rate limit persisted. "
                        f"Wait {wait:.1f}s and try again. "
                        f"{message[:500]}"
                    )

                log.warning(
                    "Groq rate limit. Waiting %.1fs.",
                    wait,
                )

                time.sleep(wait + random.uniform(0, 0.8))
                continue

            if response.status_code >= 400:
                raise RuntimeError(
                    f"Groq HTTP {response.status_code}: "
                    f"{response.text[:700]}"
                )

            choices = response.json().get("choices") or []

            if not choices:
                raise RuntimeError("Groq returned no choices.")

            content = choices[0].get("message", {}).get("content", "")

            if isinstance(content, list):
                content = "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )

            data = extract_json(str(content))

            scenes = data.get("scenes") if isinstance(data, dict) else None

            if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
                raise ValueError(
                    f"Expected 18 scenes; received "
                    f"{len(scenes) if isinstance(scenes, list) else 'invalid'}."
                )

            clean_scenes = []

            for index, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(f"Scene {index + 1} is invalid.")

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
                    r"[^a-zA-Z0-9 ,'-]",
                    "",
                    str(scene.get("search_query", "")),
                ).strip()

                effect = str(
                    scene.get("effect", "mystery")
                ).lower().strip()

                if not narration:
                    raise ValueError(
                        f"Scene {index + 1} narration is empty."
                    )

                if len(query.split()) < 3:
                    query = " ".join(
                        re.findall(r"[A-Za-z0-9]+", visual)[:9]
                    )

                if len(query.split()) < 3:
                    query = "cinematic fantasy dramatic scene"

                if effect not in EFFECTS:
                    effect = "mystery"

                clean_scenes.append({
                    "narration": narration,
                    "visual_prompt": visual[:500],
                    "search_query": query[:150],
                    "effect": effect,
                })

            total_words = word_count(
                " ".join(s["narration"] for s in clean_scenes)
            )

            if not 100 <= total_words <= 230:
                raise ValueError(
                    f"Narration word count out of range: {total_words}"
                )

            return {
                "title": str(data.get("title") or "حكاية ظل")[:100],
                "story": str(data.get("story") or idea)[:5000],
                "narration": " ".join(
                    s["narration"] for s in clean_scenes
                ),
                "scenes": clean_scenes,
            }

        except Exception as error:
            last_error = error

            log.warning(
                "Story attempt %s/%s failed: %s",
                attempt + 1,
                GROQ_RETRIES,
                error,
            )

            if any(
                marker in str(error)
                for marker in ("HTTP 401", "HTTP 403", "HTTP 404")
            ):
                break

            if attempt < GROQ_RETRIES - 1 and "429" not in str(error):
                time.sleep(min(8, 2 * (attempt + 1)))

    raise RuntimeError(
        f"Could not create a valid story after retries: {last_error}"
    )


# =========================================================
# PIXABAY — BETTER ERROR DIAGNOSTICS
# =========================================================

def pixabay_request(query, per_page=10):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")

    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "safesearch": "true",
    }

    try:
        response = HTTP.get(
            PIXABAY_API,
            params=params,
            timeout=30,
        )
    except requests.RequestException as error:
        raise RuntimeError(
            f"Pixabay connection failed: {error}"
        ) from error

    if response.status_code >= 400:
        # لا نطبع عنوان الطلب، لأنه يحتوي على مفتاح API.
        try:
            error_body = json.dumps(
                response.json(),
                ensure_ascii=False,
            )
        except ValueError:
            error_body = response.text

        log.error(
            "Pixabay HTTP %s for query %r: %s",
            response.status_code,
            query,
            error_body[:800],
        )

        raise RuntimeError(
            f"Pixabay API HTTP {response.status_code}: "
            f"{error_body[:700]}"
        )

    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(
            "Pixabay returned invalid JSON."
        ) from error


def search_pixabay_video(query):
    data = pixabay_request(query, per_page=10)
    hits = data.get("hits", [])

    if not hits:
        raise RuntimeError(
            f"No Pixabay results for query: {query}"
        )

    for hit in hits:
        videos = hit.get("videos") or {}

        for quality in ("large", "medium", "small", "tiny"):
            video_url = (
                videos.get(quality) or {}
            ).get("url", "")

            if (
                video_url
                and urlparse(video_url).scheme == "https"
            ):
                return video_url

    raise RuntimeError(
        f"No downloadable video found for: {query}"
    )


def download_video(url, destination):
    total = 0

    with HTTP.get(
        url,
        stream=True,
        timeout=(20, 90),
    ) as response:
        response.raise_for_status()

        with open(destination, "wb") as file:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)

                if total > 150 * 1024 * 1024:
                    raise RuntimeError(
                        "Downloaded video exceeds 150 MB."
                    )

                file.write(chunk)

    if total < 10000:
        raise RuntimeError("Downloaded video is too small.")

    return destination


# =========================================================
# VIDEO NORMALIZATION AND ARABIC CAPTIONS
# =========================================================

def normalize_scene(source, destination):
    video_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"setsar=1,fps={VIDEO_FPS},"
        f"tpad=stop_mode=clone:stop_duration={SCENE_DURATION},"
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
        "-movflags", "+faststart",
        str(destination),
    ], timeout=180)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("FFmpeg produced an empty scene.")

    return destination


def add_arabic_caption(source, caption, destination, workdir, index):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning(
            "Arabic subtitle dependencies missing; subtitles skipped."
        )
        return source

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

        if Path(font_path).exists():
            font = ImageFont.truetype(font_path, 27)
        else:
            font = ImageFont.load_default()

        words = (caption or "").split()
        rows = []
        row = ""

        image = Image.new(
            "RGBA",
            (VIDEO_WIDTH, 160),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image)

        for word in words:
            candidate = (row + " " + word).strip()
            shaped = get_display(
                arabic_reshaper.reshape(candidate)
            )

            if (
                row
                and draw.textbbox((0, 0), shaped, font=font)[2]
                > VIDEO_WIDTH - 70
            ):
                rows.append(row)
                row = word
            else:
                row = candidate

        if row:
            rows.append(row)

        if len(rows) > 2:
            midpoint = max(1, len(words) // 2)
            rows = [
                " ".join(words[:midpoint]),
                " ".join(words[midpoint:]),
            ]

        shaped_rows = [
            get_display(arabic_reshaper.reshape(text))
            for text in rows[:2]
        ]

        draw.rounded_rectangle(
            (12, 12, VIDEO_WIDTH - 12, 148),
            radius=18,
            fill=(0, 0, 0, 170),
        )

        for line_index, line in enumerate(shaped_rows):
            box = draw.textbbox(
                (0, 0),
                line,
                font=font,
                stroke_width=1,
            )

            x = max(
                12,
                (VIDEO_WIDTH - (box[2] - box[0])) // 2,
            )

            draw.text(
                (x, 30 + line_index * 47),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(0, 0, 0, 230),
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
            "[0:v][1:v]overlay=0:H-170:format=auto[v]",
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

        return source

    except Exception:
        log.exception(
            "Subtitle rendering failed for scene %s",
            index,
        )
        return source


# =========================================================
# ARABIC NARRATION
# =========================================================

def create_narration(text, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
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
        log.exception("Edge TTS failed; trying gTTS.")

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

    raise RuntimeError("Arabic narration generation failed.")


def fit_audio_to_scene(source, destination, duration=SCENE_DURATION):
    audio_duration = probe_duration(source)

    if not audio_duration or audio_duration <= 0:
        raise RuntimeError(
            f"Could not read narration duration: {source}"
        )

    speed = min(
        1.45,
        max(1.0, audio_duration / (duration - 0.18)),
    )

    audio_filter = ""

    if speed > 1.001:
        audio_filter = f"atempo={speed:.4f},"

    audio_filter += (
        f"apad=pad_dur={duration},"
        f"atrim=duration={duration},"
        "asetpts=PTS-STARTPTS,"
        "loudnorm=I=-19:TP=-2:LRA=7"
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
# SYNTHETIC SOUND EFFECTS
# =========================================================

def make_scene_sfx(kind, destination, duration=SCENE_DURATION):
    d = float(duration)

    if kind == "beast":
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={d}"
        audio_filter = (
            "highpass=f=45,lowpass=f=850,"
            "tremolo=f=5:d=0.75,volume=0.34,"
            "afade=t=in:d=0.15,"
            "afade=t=out:st=3.7:d=1.0"
        )

    elif kind in ("fight", "impact"):
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={d}"
        audio_filter = (
            "highpass=f=50,lowpass=f=1600,"
            "volume=0.42,afade=t=in:d=0.02,"
            "afade=t=out:st=0.65:d=0.8"
        )

    elif kind == "magic":
        source = f"sine=frequency=95:sample_rate=44100:duration={d}"
        audio_filter = (
            "tremolo=f=3:d=0.75,"
            "chorus=0.5:0.7:45:0.35:0.25:2,"
            "volume=0.19,afade=t=in:d=0.7,"
            "afade=t=out:st=3.5:d=1.2"
        )

    elif kind == "storm":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={d}"
        audio_filter = (
            "lowpass=f=420,volume=0.15,"
            "tremolo=f=0.25:d=0.4,"
            "afade=t=in:d=0.6,"
            "afade=t=out:st=3.5:d=1.2"
        )

    elif kind in ("tension", "mystery", "cliffhanger"):
        source = f"sine=frequency=72:sample_rate=44100:duration={d}"
        audio_filter = (
            "tremolo=f=2:d=0.55,lowpass=f=500,"
            "volume=0.11,afade=t=in:d=0.7,"
            "afade=t=out:st=3.5:d=1.2"
        )

    elif kind == "steps":
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={d}"
        audio_filter = (
            "lowpass=f=240,volume=0.12,"
            "tremolo=f=1.7:d=0.85,"
            "afade=t=in:d=0.1,"
            "afade=t=out:st=3.6:d=1.0"
        )

    elif kind == "romance":
        source = f"sine=frequency=440:sample_rate=44100:duration={d}"
        audio_filter = (
            "vibrato=f=4:d=0.15,lowpass=f=1200,"
            "volume=0.035,afade=t=in:d=0.7,"
            "afade=t=out:st=3.4:d=1.2"
        )

    elif kind == "castle":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={d}"
        audio_filter = (
            "highpass=f=100,lowpass=f=700,"
            "volume=0.055,afade=t=in:d=0.4,"
            "afade=t=out:st=3.6:d=1.0"
        )

    else:
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={d}"
        audio_filter = (
            "lowpass=f=1000,volume=0.06,"
            "afade=t=in:d=0.3,"
            "afade=t=out:st=3.6:d=1.0"
        )

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
        raise RuntimeError(f"Could not create SFX: {kind}")

    return destination


def make_background_music(destination, duration):
    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-af",
        (
            "volume=0.035,tremolo=f=0.18:d=0.25,"
            "afade=t=in:d=2,afade=t=out:st=86:d=4"
        ),
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], timeout=90)

    return destination


# =========================================================
# CONCATENATION AND FINAL AUDIO MIX
# =========================================================

def write_concat_list(paths, list_path):
    with open(list_path, "w", encoding="utf-8") as file:
        for path in paths:
            safe_path = Path(path).resolve().as_posix()

            if "'" in safe_path:
                raise RuntimeError(
                    "A media path contains an unsupported apostrophe."
                )

            file.write(f"file '{safe_path}'\n")


def concatenate_video(paths, destination, workdir):
    list_path = workdir / "video_concat.txt"
    write_concat_list(paths, list_path)

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-t", str(VIDEO_DURATION),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ], timeout=240)

    return destination


def concatenate_audio(paths, destination, workdir, name):
    list_path = workdir / f"{name}_concat.txt"
    write_concat_list(paths, list_path)

    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-t", str(VIDEO_DURATION),
        "-vn",
        "-c:a", "aac",
        "-b:a", "128k",
        str(destination),
    ], timeout=180)

    return destination


def mix_final_audio(video, voice, sfx, music, destination):
    run_command([
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-i", str(voice),
        "-i", str(sfx),
        "-i", str(music),
        "-filter_complex",
        (
            "[1:a]volume=1.0,apad,atrim=duration=90[voice];"
            "[2:a]volume=0.34,apad,atrim=duration=90[sfx];"
            "[3:a]volume=0.22,apad,atrim=duration=90[music];"
            "[voice][sfx][music]"
            "amix=inputs=3:duration=longest:dropout_transition=0,"
            "alimiter=limit=0.92,atrim=duration=90[aout]"
        ),
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
        raise RuntimeError("Final video missing or too small.")

    return destination


# =========================================================
# TELEGRAM ASYNC BRIDGE
# =========================================================

def send_coroutine(application, coroutine, timeout=360):
    loop = application.bot_data.get("event_loop")

    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError("Telegram event loop unavailable.")

    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return future.result(timeout=timeout)


async def send_text(application, chat_id, message):
    await application.bot.send_message(
        chat_id=chat_id,
        text=message[:3500],
    )


async def send_video(application, chat_id, path, job_id):
    with open(path, "rb") as file:
        await application.bot.send_video(
            chat_id=chat_id,
            video=file,
            caption=(
                "تم إنشاء فيديو ظل ZIL.\n"
                "المدة المستهدفة: 90 ثانية\n"
                f"رقم العملية: {job_id}"
            ),
            supports_streaming=True,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
            pool_timeout=30,
        )


# =========================================================
# VIDEO BUILD JOB
# =========================================================

def build_video(job_id, chat_id, story, telegram_app):
    global ACTIVE_JOBS

    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        update_job(
            job_id,
            status="running",
            stage="preflight",
            progress=1,
        )

        if not command_exists("ffmpeg") or not command_exists("ffprobe"):
            raise RuntimeError("FFmpeg/FFprobe missing.")

        if not PIXABAY_API_KEY:
            raise RuntimeError("PIXABAY_API_KEY is missing.")

        story = (story or "").strip()[:MAX_STORY_LENGTH]

        if not story:
            raise RuntimeError("Story is empty.")

        update_job(
            job_id,
            stage="writing_story",
            progress=2,
        )

        package = create_story_package(story)
        scenes = package["scenes"]

        update_job(
            job_id,
            stage="story_ready",
            progress=5,
            title=package["title"],
            story=package["story"],
            narration=package["narration"],
            narration_words=word_count(package["narration"]),
        )

        try:
            send_coroutine(
                telegram_app,
                send_text(
                    telegram_app,
                    chat_id,
                    (
                        f"اكتملت كتابة القصة: {package['title']}\n"
                        "سيتم تجهيز 18 مشهدًا مع راوي عربي "
                        "وترجمة ومؤثرات. قد يستغرق العمل وقتًا."
                    ),
                ),
                timeout=45,
            )
        except Exception:
            log.exception("Could not send story-ready update.")

        videos = []
        voices = []
        effects = []

        for index, scene in enumerate(scenes):
            number = index + 1

            update_job(
                job_id,
                stage=f"scene_{number}_download",
                progress=round(5 + (index / SCENE_COUNT) * 78),
            )

            query = scene["search_query"]

            try:
                url = search_pixabay_video(query)

            except Exception as first_error:
                log.warning(
                    "Pixabay first query failed for scene %s: %s",
                    number,
                    first_error,
                )

                visual_words = re.findall(
                    r"[A-Za-z0-9]+",
                    scene.get("visual_prompt", ""),
                )[:7]

                fallback_query = (
                    " ".join(visual_words)
                    or "cinematic fantasy dramatic scene"
                )

                try:
                    url = search_pixabay_video(fallback_query)

                except Exception as second_error:
                    log.warning(
                        "Pixabay fallback failed for scene %s: %s",
                        number,
                        second_error,
                    )

                    # آخر محاولة محدودة؛ إذا فشلت نسجل الخطأ الحقيقي.
                    url = search_pixabay_video(
                        "cinematic fantasy castle warrior dramatic"
                    )

            raw_video = workdir / f"raw_{number}.mp4"
            base_video = workdir / f"base_{number}.mp4"
            caption_video = workdir / f"scene_{number}.mp4"

            download_video(url, raw_video)
            normalize_scene(raw_video, base_video)

            rendered = add_arabic_caption(
                base_video,
                scene["narration"],
                caption_video,
                workdir,
                number,
            )

            videos.append(
                rendered if Path(rendered).exists() else base_video
            )

            raw_video.unlink(missing_ok=True)

            raw_voice = workdir / f"voice_raw_{number}.mp3"
            fit_voice = workdir / f"voice_{number}.m4a"

            create_narration(scene["narration"], raw_voice)
            fit_audio_to_scene(raw_voice, fit_voice)
            voices.append(fit_voice)

            sound_effect = workdir / f"sfx_{number}.m4a"
            make_scene_sfx(scene["effect"], sound_effect)
            effects.append(sound_effect)

            update_job(
                job_id,
                stage=f"scene_{number}_complete",
                progress=round(
                    5 + ((index + 1) / SCENE_COUNT) * 78
                ),
            )

        update_job(
            job_id,
            stage="concatenate_video",
            progress=85,
        )

        silent_video = workdir / "silent_video.mp4"
        concatenate_video(videos, silent_video, workdir)

        voice_track = workdir / "voice_90s.m4a"
        concatenate_audio(voices, voice_track, workdir, "voice")

        sfx_track = workdir / "sfx_90s.m4a"
        concatenate_audio(effects, sfx_track, workdir, "sfx")

        music_track = workdir / "ambient_90s.m4a"
        make_background_music(music_track, VIDEO_DURATION)

        update_job(
            job_id,
            stage="mix_audio",
            progress=94,
        )

        final_video = workdir / "ZIL_video.mp4"

        mix_final_audio(
            silent_video,
            voice_track,
            sfx_track,
            music_track,
            final_video,
        )

        duration = probe_duration(final_video)

        if (
            duration is None
            or abs(duration - VIDEO_DURATION) > 0.35
        ):
            raise RuntimeError(
                f"Final duration check failed: {duration}; "
                f"expected {VIDEO_DURATION} seconds."
            )

        update_job(
            job_id,
            status="sending",
            stage="sending_video",
            progress=98,
            output=str(final_video),
            size=final_video.stat().st_size,
            duration=duration,
        )

        if final_video.stat().st_size > MAX_VIDEO_SIZE:
            send_coroutine(
                telegram_app,
                send_text(
                    telegram_app,
                    chat_id,
                    (
                        f"اكتمل الفيديو ومدته {duration:.1f} ثانية، "
                        "لكن حجمه يتجاوز حد الإرسال في تيليجرام.\n"
                        f"رقم العملية: {job_id}"
                    ),
                ),
                timeout=45,
            )

            update_job(
                job_id,
                status="completed",
                stage="completed_file_too_large",
                progress=100,
            )
            return

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
            duration=duration,
        )

        log.info(
            "Job %s completed successfully: %.2fs",
            job_id,
            duration,
        )

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
                        "أرسل /last_error لمعرفة السبب، "
                        "أو /status لمراجعة حالة العملية."
                    ),
                ),
                timeout=45,
            )
        except Exception:
            log.exception("Could not notify user about failure.")

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
            "/test - فيديو تجريبي 90 ثانية\n"
            "/make فكرة القصة - إنشاء فيديو\n"
            "/status - حالة العمليات\n"
            "/diagnose - فحص النظام\n"
            "/last_error - آخر خطأ\n"
            "/health - حالة الخدمة"
        )


async def start_job(update, context, story):
    global ACTIVE_JOBS

    if not update.message or not update.effective_chat:
        return

    story = (story or "").strip()[:MAX_STORY_LENGTH]

    if not story:
        await update.message.reply_text("أرسل فكرة القصة أولًا.")
        return

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            busy = True
            job_id = None
        else:
            busy = False
            job_id = uuid.uuid4().hex[:10]

            JOBS[job_id] = {
                "id": job_id,
                "chat_id": update.effective_chat.id,
                "status": "queued",
                "stage": "queued",
                "story": story[:500],
                "created_at": time.time(),
                "updated_at": time.time(),
            }

            ACTIVE_JOBS += 1

    if busy:
        await update.message.reply_text(
            "يوجد فيديو قيد المعالجة. حاول مرة أخرى لاحقًا."
        )
        return

    await update.message.reply_text(
        "بدأت صناعة فيديو ظل ZIL.\n"
        f"رقم العملية: {job_id}\n"
        "ستُكتب القصة أولًا، ثم تُجهّز 18 لقطة "
        "مع راوي عربي وترجمة ومؤثرات."
    )

    try:
        threading.Thread(
            target=build_video,
            args=(
                job_id,
                update.effective_chat.id,
                story,
                context.application,
            ),
            daemon=True,
        ).start()

    except Exception as error:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

        report_error(job_id, error)

        await update.message.reply_text(
            "تعذر بدء إنشاء الفيديو."
        )


async def test_command(update, context):
    await start_job(
        update,
        context,
        (
            "في مملكة غامضة، يصل رجل يخفي قوة خارقة. "
            "تقع الأميرة في حبه، لكن الملك يرفض العلاقة. "
            "يظهر نمر عملاق أمام القصر، فيواجهه الرجل "
            "ويكشف جزءًا من قوته المخفية، ثم يظهر سر جديد "
            "يهدد المملكة."
        ),
    )


async def make_command(update, context):
    story = " ".join(context.args).strip()

    if not story:
        await update.message.reply_text(
            "اكتب فكرة القصة بعد الأمر، مثال:\n"
            "/make رجل غامض يصل إلى مملكة تحكمها أميرة "
            "ويخفي قوة أسطورية"
        )
        return

    await start_job(update, context, story)


async def status_command(update, context):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        recent = list(JOBS.values())[-5:]

    lines = [
        "حالة ظل ZIL",
        f"العمليات النشطة: {active}",
        f"Pixabay: {'مفتاح موجود' if PIXABAY_API_KEY else 'مفتاح مفقود'}",
        f"Groq AI: {'مفتاح موجود' if GROQ_API_KEY else 'مفتاح مفقود'}",
        f"FFmpeg: {'جاهز' if command_exists('ffmpeg') else 'غير موجود'}",
        f"FFprobe: {'جاهز' if command_exists('ffprobe') else 'غير موجود'}",
    ]

    for job in reversed(recent):
        lines.append(
            f"\n{job['id']} | "
            f"{job.get('status')} | "
            f"{job.get('stage')} | "
            f"{job.get('progress', 0)}%"
        )

        if job.get("error"):
            lines.append("الخطأ: " + job["error"][:500])

    await update.message.reply_text(
        "\n".join(lines)[:3900]
    )


async def last_error_command(update, context):
    with JOBS_LOCK:
        failed = [
            job
            for job in JOBS.values()
            if job.get("status") == "failed"
        ]

    if not failed:
        await update.message.reply_text(
            "لا توجد أخطاء مسجلة في ذاكرة العمليات الحالية.\n"
            "إذا أُعيد تشغيل Render فقد لا تبقى الأخطاء السابقة "
            "في الذاكرة. افحص Logs في Render عند الحاجة."
        )
        return

    job = failed[-1]

    await update.message.reply_text(
        "آخر خطأ في ظل ZIL\n"
        f"العملية: {job['id']}\n"
        f"المرحلة: {job.get('stage')}\n"
        f"الخطأ:\n{job.get('error', 'غير معروف')[:2500]}"
    )


async def diagnose_command(update, context):
    output = [
        f"BOT_TOKEN: {'OK' if BOT_TOKEN else 'MISSING'}",
        f"PIXABAY_API_KEY: {'OK' if PIXABAY_API_KEY else 'MISSING'}",
        f"GROQ_API_KEY: {'OK' if GROQ_API_KEY else 'MISSING'}",
        f"GROQ_MODEL: {GROQ_MODEL}",
        f"Groq max output tokens: {GROQ_MAX_TOKENS}",
        f"Python: {sys.version.split()[0]}",
        f"FFmpeg: {command_exists('ffmpeg')}",
        f"FFprobe: {command_exists('ffprobe')}",
        f"Duration target: {VIDEO_DURATION}s",
    ]

    if PIXABAY_API_KEY:
        try:
            # نستخدم نفس دالة الطلب ونلتقط تفاصيل الخطأ.
            data = pixabay_request("nature", per_page=2)
            hits = data.get("hits", [])
            output.append(f"Pixabay API: OK")
            output.append(f"Pixabay results: {len(hits)}")

        except Exception as error:
            output.append(f"Pixabay API: FAILED")
            output.append(f"Pixabay details: {str(error)[:1200]}")

    await update.message.reply_text(
        "تشخيص ظل ZIL\n\n" + "\n".join(output)[:3900]
    )


async def health_command(update, context):
    await update.message.reply_text(
        "ظل ZIL يعمل.\n"
        f"Python: {sys.version.split()[0]}\n"
        f"FFmpeg: {command_exists('ffmpeg')}\n"
        f"FFprobe: {command_exists('ffprobe')}\n"
        f"Pixabay configured: {bool(PIXABAY_API_KEY)}\n"
        f"Groq configured: {bool(GROQ_API_KEY)}\n"
        f"المدة المستهدفة: {VIDEO_DURATION} ثانية"
    )


# =========================================================
# FLASK HEALTH ENDPOINTS
# =========================================================

@app.get("/")
def home():
    return jsonify({
        "service": "ZIL",
        "status": "running",
        "ai_story_configured": bool(GROQ_API_KEY),
        "target_duration_seconds": VIDEO_DURATION,
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "ffmpeg": command_exists("ffmpeg"),
        "ffprobe": command_exists("ffprobe"),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "groq_configured": bool(GROQ_API_KEY),
        "active_jobs": get_active_jobs(),
        "target_duration_seconds": VIDEO_DURATION,
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
    log.info(
        "Starting ZIL; target duration=%s",
        VIDEO_DURATION,
    )

    if not BOT_TOKEN:
        log.critical("BOT_TOKEN missing.")
        sys.exit(1)

    if not command_exists("ffmpeg") or not command_exists("ffprobe"):
        log.critical("FFmpeg/FFprobe missing; check Dockerfile.")
        sys.exit(1)

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    async def save_loop(application):
        application.bot_data["event_loop"] = (
            asyncio.get_running_loop()
        )

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(save_loop)
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

    log.info("Telegram polling starting.")

    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
