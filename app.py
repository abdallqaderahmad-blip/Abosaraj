import os
import re
import json
import time
import uuid
import asyncio
import shutil
import logging
import tempfile
import threading
import subprocess
import traceback

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
import edge_tts

from flask import Flask, jsonify
from telegram import Update, Bot
from telegram.ext import (
Application,
CommandHandler,
MessageHandler,
ContextTypes,
filters,
)

=========================================================

CONFIGURATION

=========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

PIXABAY_API_KEY = (
os.getenv("PIXABAY_API_KEY", "").strip()
or os.getenv("PIXABAY_KEY", "").strip()
)

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 6
SCENE_SECONDS = 5.25
TRANSITION_SECONDS = 0.30
TOTAL_SECONDS = 30

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 25

MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MAX_ACTIVE_JOBS = 1

OUTPUT_DIR = Path("/tmp/zil_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

logging.basicConfig(
level=os.getenv("LOG_LEVEL", "INFO").upper(),
format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("zil")

app = Flask(name)

executor = ThreadPoolExecutor(max_workers=2)

JOBS = {}
JOBS_LOCK = threading.Lock()

ACTIVE_JOBS = 0
ACTIVE_LOCK = threading.Lock()

REPORTED_ERRORS = set()
REPORTED_LOCK = threading.Lock()

=========================================================

SECURITY: HIDE API KEYS FROM LOGS AND REPORTS

=========================================================

def redact_secrets(text):
if not text:
return ""

secrets = [
    BOT_TOKEN,
    PIXABAY_API_KEY,
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("WAVESPEED_API_KEY", ""),
]

for secret in secrets:
    if secret:
        text = text.replace(secret, "[REDACTED]")

text = re.sub(
    r"(?i)(token|api[_-]?key|authorization)\s*[:=]\s*\S+",
    r"\1=[REDACTED]",
    text,
)

return text

=========================================================

JOB REGISTRY

=========================================================

def save_job(job_id, **fields):
with JOBS_LOCK:
job = JOBS.setdefault(job_id, {})
job.update(fields)
job["updated_at"] = time.strftime(
"%Y-%m-%d %H:%M:%S"
)

def get_job(job_id):
with JOBS_LOCK:
return dict(JOBS.get(job_id, {}))

def recent_jobs():
with JOBS_LOCK:
return list(JOBS.items())[-10:]

=========================================================

PROCESS EXECUTION

=========================================================

def run_command(command, timeout=120):
logger.info(
"RUN COMMAND: %s",
" ".join(str(part) for part in command[:4]),
)

try:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
except subprocess.TimeoutExpired as exc:
    raise RuntimeError(
        f"Command timed out after {timeout} seconds: "
        f"{command[0]}"
    ) from exc

if result.returncode != 0:
    details = redact_secrets(
        (
            result.stderr
            or result.stdout
            or "Unknown subprocess error"
        )[-5000:]
    )

    raise RuntimeError(
        f"Command failed: {command[0]}\n"
        f"Exit code: {result.returncode}\n"
        f"Details:\n{details}"
    )

return result

=========================================================

AUTOMATIC ADMIN DIAGNOSTICS

=========================================================

async def send_admin_message(message):
if not BOT_TOKEN:
logger.error("Cannot send report: BOT_TOKEN missing")
return

if not ADMIN_CHAT_ID:
    logger.error(
        "ADMIN_CHAT_ID missing; report cannot be delivered"
    )
    return

try:
    async with Bot(token=BOT_TOKEN) as bot:
        await bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=redact_secrets(message)[:3900],
        )
except Exception:
    logger.exception("Admin report delivery failed")

def notify_admin(message):
try:
asyncio.run(send_admin_message(message))
except Exception:
logger.exception("Admin notification failed")

def report_error(job_id, stage, error, trace=None):
with REPORTED_LOCK:
if job_id in REPORTED_ERRORS:
return

    REPORTED_ERRORS.add(job_id)

error_text = redact_secrets(str(error))

if not trace:
    trace = traceback.format_exc()

trace = redact_secrets(trace)

save_job(
    job_id,
    status="failed",
    stage=stage,
    error=error_text,
    traceback=trace[-6000:],
)

logger.error(
    "JOB FAILED | ID=%s | STAGE=%s | ERROR=%s\n%s",
    job_id,
    stage,
    error_text,
    trace[-4000:],
)

message = (
    "🚨 ZIL — تقرير تشخيص تلقائي\n\n"
    f"رقم العملية: {job_id}\n"
    f"المرحلة: {stage}\n"
    f"الخطأ: {error_text[:1000]}\n\n"
    f"وقت الخطأ: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    "تفاصيل التتبع:\n"
    f"{trace[-1800:]}\n\n"
    "تم تسجيل الخطأ لتحديد الإصلاح المطلوب."
)

notify_admin(message)

=========================================================

SYSTEM CHECKS

=========================================================

def check_environment():
return {
"BOT_TOKEN": bool(BOT_TOKEN),
"PIXABAY_API_KEY": bool(PIXABAY_API_KEY),
"ADMIN_CHAT_ID": bool(ADMIN_CHAT_ID),
"FFmpeg": shutil.which("ffmpeg") is not None,
"FFprobe": shutil.which("ffprobe") is not None,
"Output directory": OUTPUT_DIR.exists(),
}

def require_tools():
missing = []

if not BOT_TOKEN:
    missing.append("BOT_TOKEN")

if not PIXABAY_API_KEY:
    missing.append("PIXABAY_API_KEY or PIXABAY_KEY")

if not shutil.which("ffmpeg"):
    missing.append("ffmpeg")

if not shutil.which("ffprobe"):
    missing.append("ffprobe")

if missing:
    raise RuntimeError(
        "Missing required configuration/tools: "
        + ", ".join(missing)
    )

=========================================================

PIXABAY VIDEO SEARCH — FIXED

=========================================================

def search_pixabay_video(query):
"""
Correct endpoint: /api/videos/

Searches video results rather than the Pixabay image API.
Returns a downloadable video URL or a detailed diagnostic.
"""

if not PIXABAY_API_KEY:
    raise RuntimeError("PIXABAY_API_KEY is missing")

try:
    response = requests.get(
        PIXABAY_VIDEO_API,
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "per_page": 20,
            "safesearch": "true",
        },
        timeout=35,
    )
except requests.RequestException as exc:
    raise RuntimeError(
        f"Pixabay connection failed: {exc}"
    ) from exc

if response.status_code != 200:
    # Never include the full request URL because it can
    # contain the API key in its query string.
    raise RuntimeError(
        f"Pixabay HTTP error: {response.status_code}. "
        f"Response: {response.text[:700]}"
    )

try:
    data = response.json()
except ValueError as exc:
    raise RuntimeError(
        "Pixabay returned invalid JSON: "
        + response.text[:400]
    ) from exc

if not isinstance(data, dict):
    raise RuntimeError(
        "Unexpected Pixabay response format"
    )

hits = data.get("hits", [])

if not hits:
    raise RuntimeError(
        f"No Pixabay video results for query: {query!r}"
    )

for hit in hits:
    video_sizes = hit.get("videos") or {}

    for size in ("large", "medium", "small", "tiny"):
        video_info = video_sizes.get(size) or {}
        url = video_info.get("url")

        if (
            isinstance(url, str)
            and url.startswith("https://")
        ):
            return url

first_hit = hits[0]
available_sizes = list(
    (first_hit.get("videos") or {}).keys()
)

raise RuntimeError(
    "Pixabay returned results but no usable video URL. "
    f"Query={query!r}; "
    f"results={len(hits)}; "
    f"available_sizes={available_sizes}"
)

def download_video(url, destination):
try:
with requests.get(
url,
stream=True,
timeout=(20, 60),
) as response:

        response.raise_for_status()

        length = response.headers.get("Content-Length")

        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise RuntimeError(
                "Video exceeds the download size limit"
            )

        total = 0

        with open(destination, "wb") as file:
            for chunk in response.iter_content(
                chunk_size=256 * 1024
            ):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        "Video exceeded the download size limit"
                    )

                file.write(chunk)

except requests.RequestException as exc:
    raise RuntimeError(
        f"Pixabay video download failed: {exc}"
    ) from exc

if (
    not Path(destination).exists()
    or Path(destination).stat().st_size < 10000
):
    raise RuntimeError(
        "Downloaded video is missing or too small"
    )

probe_media(destination)

return destination

=========================================================

MEDIA VALIDATION

=========================================================

def probe_media(path):
result = run_command(
[
"ffprobe",
"-v", "error",
"-show_entries", "format=duration",
"-of", "default=noprint_wrappers=1:nokey=1",
str(path),
],
timeout=30,
)

try:
    duration = float(result.stdout.strip())
except (ValueError, TypeError) as exc:
    raise RuntimeError(
        "FFprobe could not read the media duration"
    ) from exc

if duration <= 0:
    raise RuntimeError("Media duration is invalid")

return duration

def validate_video(path):
path = Path(path)

if not path.exists():
    raise RuntimeError("Final video does not exist")

if path.stat().st_size < 100000:
    raise RuntimeError("Final video file is too small")

duration = probe_media(path)

if duration < 25 or duration > 35:
    raise RuntimeError(
        f"Unexpected video duration: {duration:.2f}s"
    )

result = run_command(
    [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(path),
    ],
    timeout=30,
)

data = json.loads(result.stdout)
streams = data.get("streams", [])

if not streams:
    raise RuntimeError("No video stream in final file")

width = streams[0].get("width")
height = streams[0].get("height")

if not width or not height or height <= width:
    raise RuntimeError(
        f"Video is not vertical: {width}x{height}"
    )

return {
    "duration": round(duration, 2),
    "width": width,
    "height": height,
    "size_mb": round(
        path.stat().st_size / (1024 * 1024),
        2,
    ),
}

=========================================================

STORY SCENES

=========================================================

def make_search_queries(story):
"""
These are stock-video searches, not AI-generated scenes.
"""

return [
    "cinematic fantasy castle",
    "mysterious man dramatic cinematic",
    "princess royal palace",
    "dark storm dramatic landscape",
    "tiger running wildlife",
    "epic fantasy battle landscape",
]

def make_scene_captions(story):
story = re.sub(r"\s+", " ", story).strip()

opening = story[:160] if story else (
    "بدأت حكاية غامضة لم يتوقع أحد نهايتها."
)

return [
    opening,
    "لكن الحقيقة لم تكن كما تبدو...",
    "بدأ الخطر يقترب.",
    "قوة غامضة ظهرت فجأة.",
    "لم يتوقع أحد ما سيحدث.",
    "والسر الحقيقي لم يُكشف بعد...",
]

=========================================================

ARABIC CAPTION IMAGE

=========================================================

def make_caption_image(text, destination):
try:
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

except ImportError as exc:
    raise RuntimeError(
        "Caption dependencies missing: Pillow, "
        "arabic-reshaper and python-bidi"
    ) from exc

image = Image.new(
    "RGBA",
    (VIDEO_WIDTH, 230),
    (0, 0, 0, 0),
)

draw = ImageDraw.Draw(image)

font_paths = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

font = None

for font_path in font_paths:
    if os.path.exists(font_path):
        font = ImageFont.truetype(font_path, 34)
        break

if font is None:
    font = ImageFont.load_default()

shaped = arabic_reshaper.reshape(text)
display_text = get_display(shaped)

words = display_text.split()
lines = []
current = ""

for word in words:
    candidate = f"{current} {word}".strip()

    box = draw.textbbox(
        (0, 0),
        candidate,
        font=font,
    )

    if box[2] > VIDEO_WIDTH - 60 and current:
        lines.append(current)
        current = word
    else:
        current = candidate

if current:
    lines.append(current)

lines = lines[:3]

line_height = 48
start_y = max(
    5,
    (230 - len(lines) * line_height) // 2,
)

for index, line in enumerate(lines):
    box = draw.textbbox(
        (0, 0),
        line,
        font=font,
        stroke_width=2,
    )

    width = box[2] - box[0]
    x = max(10, (VIDEO_WIDTH - width) // 2)
    y = start_y + index * line_height

    draw.text(
        (x, y),
        line,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 255),
    )

image.save(destination)

=========================================================

RENDER ONE SCENE

=========================================================

def render_scene(source, caption_path, destination):
filter_graph = (
"[0:v]"
f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
"force_original_aspect_ratio=increase,"
f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
"setsar=1,"
f"fps={VIDEO_FPS},"
f"trim=duration={SCENE_SECONDS},"
"setpts=PTS-STARTPTS"
"[base];"
"[1:v]format=rgba[cap];"
"[base][cap]"
f"overlay=x=0:y={VIDEO_HEIGHT - 260}:shortest=1,"
"format=yuv420p[out]"
)

run_command(
    [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-loop", "1",
        "-framerate", str(VIDEO_FPS),
        "-i", str(caption_path),
        "-filter_complex", filter_graph,
        "-map", "[out]",
        "-an",
        "-t", str(SCENE_SECONDS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ],
    timeout=150,
)

probe_media(destination)

return destination

=========================================================

CONCATENATE SIX SCENES

=========================================================

def concatenate_scenes(scene_paths, destination):
if len(scene_paths) != SCENE_COUNT:
raise RuntimeError(
f"Expected {SCENE_COUNT} scenes, "
f"received {len(scene_paths)}"
)

inputs = []

for path in scene_paths:
    inputs.extend(["-i", str(path)])

parts = []

for index in range(len(scene_paths)):
    parts.append(
        f"[{index}:v]"
        "settb=AVTB,"
        "setpts=PTS-STARTPTS"
        f"[v{index}]"
    )

current_label = "v0"
current_duration = SCENE_SECONDS

for index in range(1, len(scene_paths)):
    next_label = f"x{index}"

    offset = (
        current_duration - TRANSITION_SECONDS
    )

    parts.append(
        f"[{current_label}][v{index}]"
        "xfade=transition=fade:"
        f"duration={TRANSITION_SECONDS}:"
        f"offset={offset:.3f}"
        f"[{next_label}]"
    )

    current_label = next_label

    current_duration += (
        SCENE_SECONDS - TRANSITION_SECONDS
    )

graph = ";".join(parts)

run_command(
    [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *inputs,
        "-filter_complex", graph,
        "-map", f"[{current_label}]",
        "-an",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        str(destination),
    ],
    timeout=240,
)

return destination

=========================================================

ARABIC NARRATION

=========================================================

async def create_edge_voice(text, destination):
voice = edge_tts.Communicate(
text=text,
voice="ar-SA-HamedNeural",
rate="-8%",
pitch="-2Hz",
)

await voice.save(str(destination))

def create_narration(text, destination):
try:
asyncio.run(
create_edge_voice(text, destination)
)

except Exception:
    logger.exception(
        "Edge TTS failed; attempting gTTS fallback"
    )

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
        ).save(str(destination))

    except Exception as exc:
        raise RuntimeError(
            f"Arabic narration failed: {exc}"
        ) from exc

if (
    not Path(destination).exists()
    or Path(destination).stat().st_size < 1000
):
    raise RuntimeError(
        "Narration file is missing or invalid"
    )

=========================================================

AMBIENT AUDIO

=========================================================

def create_ambient_audio(destination):
run_command(
[
"ffmpeg",
"-hide_banner",
"-loglevel", "error",
"-y",
"-f", "lavfi",
"-i",
"anoisesrc=color=pink:amplitude=0.03:"
"sample_rate=44100",
"-t", str(TOTAL_SECONDS),
"-af", "lowpass=f=350,volume=0.12",
"-ac", "2",
"-c:a", "aac",
str(destination),
],
timeout=90,
)

=========================================================

MIX NARRATION AND AMBIENCE

=========================================================

def mux_audio(
video_path,
voice_path,
ambient_path,
destination,
):
run_command(
[
"ffmpeg",
"-hide_banner",
"-loglevel", "error",
"-y",
"-i", str(video_path),
"-i", str(voice_path),
"-i", str(ambient_path),
"-filter_complex",
(
"[1:a]aresample=44100,volume=1.0[voice];"
"[2:a]aresample=44100,volume=0.14[bed];"
"[bed][voice]"
"sidechaincompress="
"threshold=0.03:ratio=8:"
"attack=20:release=500[duck];"
"[voice][duck]"
"amix=inputs=2:duration=first:"
"dropout_transition=2,"
"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
),
"-map", "0:v:0",
"-map", "[aout]",
"-t", str(TOTAL_SECONDS),
"-c:v", "copy",
"-c:a", "aac",
"-b:a", "192k",
"-movflags", "+faststart",
str(destination),
],
timeout=180,
)

return destination

=========================================================

MAIN VIDEO PRODUCTION PIPELINE

=========================================================

def build_video(job_id, story, output_path):
global ACTIVE_JOBS

work_dir = Path(
    tempfile.mkdtemp(
        prefix=f"zil_{job_id}_"
    )
)

with ACTIVE_LOCK:
    ACTIVE_JOBS += 1

try:
    require_tools()

    save_job(
        job_id,
        status="running",
        stage="initializing",
        story=story[:500],
        started_at=time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    )

    queries = make_search_queries(story)
    captions = make_scene_captions(story)

    scene_paths = []

    for index in range(SCENE_COUNT):
        scene_number = index + 1

        # ---------------------------------------------
        # SEARCH AND DOWNLOAD
        # ---------------------------------------------

        save_job(
            job_id,
            stage=f"scene_{scene_number}_download",
            progress=f"{scene_number}/{SCENE_COUNT}",
        )

        query = queries[index]

        try:
            video_url = search_pixabay_video(query)

        except Exception as first_error:
            logger.warning(
                "Primary Pixabay search failed for scene %s: %s",
                scene_number,
                redact_secrets(str(first_error)),
            )

            # Retry with a different query once.
            fallback_queries = [
                "cinematic nature landscape",
                "dramatic landscape",
                "cinematic wildlife",
            ]

            fallback_query = fallback_queries[
                (index + scene_number) % len(fallback_queries)
            ]

            try:
                video_url = search_pixabay_video(
                    fallback_query
                )
            except Exception as second_error:
                raise RuntimeError(
                    f"Pixabay search failed for scene "
                    f"{scene_number}. "
                    f"Primary query: {query!r}. "
                    f"Primary error: {first_error}. "
                    f"Fallback query: {fallback_query!r}. "
                    f"Fallback error: {second_error}"
                ) from second_error

        source_path = (
            work_dir / f"source_{scene_number}.mp4"
        )

        download_video(
            video_url,
            source_path,
        )

        # ---------------------------------------------
        # CREATE CAPTIONS
        # ---------------------------------------------

        caption_path = (
            work_dir / f"caption_{scene_number}.png"
        )

        make_caption_image(
            captions[index],
            caption_path,
        )

        # ---------------------------------------------
        # RENDER SCENE
        # ---------------------------------------------

        save_job(
            job_id,
            stage=f"scene_{scene_number}_render",
        )

        scene_path = (
            work_dir / f"scene_{scene_number}.mp4"
        )

        render_scene(
            source_path,
            caption_path,
            scene_path,
        )

        scene_paths.append(scene_path)

    # ---------------------------------------------
    # TRANSITIONS
    # ---------------------------------------------

    save_job(
        job_id,
        stage="concatenating_scenes",
    )

    joined_video = work_dir / "joined.mp4"

    concatenate_scenes(
        scene_paths,
        joined_video,
    )

    # ---------------------------------------------
    # NARRATION
    # ---------------------------------------------

    save_job(
        job_id,
        stage="creating_narration",
    )

    narration_text = (
        story.strip()
        or "في عالم مليء بالأسرار، بدأت حكاية غامضة."
    )

    voice_path = work_dir / "narration.mp3"

    create_narration(
        narration_text[:2500],
        voice_path,
    )

    # ---------------------------------------------
    # AMBIENCE
    # ---------------------------------------------

    save_job(
        job_id,
        stage="creating_ambient_audio",
    )

    ambient_path = work_dir / "ambient.m4a"

    create_ambient_audio(
        ambient_path,
    )

    # ---------------------------------------------
    # AUDIO MIX
    # ---------------------------------------------

    save_job(
        job_id,
        stage="mixing_audio",
    )

    mux_audio(
        joined_video,
        voice_path,
        ambient_path,
        output_path,
    )

    # ---------------------------------------------
    # FINAL VALIDATION
    # ---------------------------------------------

    save_job(
        job_id,
        stage="validating_output",
    )

    validation = validate_video(
        output_path,
    )

    save_job(
        job_id,
        status="completed",
        stage="completed",
        output=str(output_path),
        validation=validation,
        completed_at=time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    )

    logger.info(
        "JOB COMPLETED | ID=%s | RESULT=%s",
        job_id,
        validation,
    )

    return str(output_path)

except Exception as exc:
    job = get_job(job_id)
    stage = job.get("stage", "unknown")

    report_error(
        job_id,
        stage,
        exc,
        traceback.format_exc(),
    )

    raise

finally:
    with ACTIVE_LOCK:
        ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

    shutil.rmtree(
        work_dir,
        ignore_errors=True,
    )

=========================================================

TELEGRAM COMMANDS

=========================================================

async def start_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
await update.message.reply_text(
"🎬 أهلاً بك في ظل ZIL.\n\n"
"أرسل فكرة القصة لصناعة فيديو.\n\n"
"/test — اختبار الإنتاج\n"
"/status — حالة العمليات\n"
"/diagnose — فحص النظام\n"
"/last_error — آخر خطأ\n"
)

async def diagnose_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
checks = check_environment()

lines = [
    "🔍 فحص نظام ظل ZIL",
    "",
]

for name, value in checks.items():
    lines.append(
        f"{'✅' if value else '❌'} {name}: "
        f"{'جاهز' if value else 'غير جاهز'}"
    )

with ACTIVE_LOCK:
    active = ACTIVE_JOBS

lines.extend([
    "",
    f"العمليات النشطة: {active}",
    f"عدد المشاهد: {SCENE_COUNT}",
    f"المدة المستهدفة: {TOTAL_SECONDS} ثانية",
    f"الدقة: {VIDEO_WIDTH}x{VIDEO_HEIGHT}",
])

await update.message.reply_text(
    "\n".join(lines)
)

if not ADMIN_CHAT_ID:
    await update.message.reply_text(
        "⚠️ ADMIN_CHAT_ID غير مضبوط؛ "
        "التقارير التلقائية لن تصل إلى محادثة الإدارة."
    )

async def status_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
with ACTIVE_LOCK:
active = ACTIVE_JOBS

jobs = recent_jobs()

lines = [
    "📊 حالة ظل ZIL",
    "",
    f"العمليات النشطة: {active}",
    f"Pixabay: {'جاهز' if PIXABAY_API_KEY else 'المفتاح مفقود'}",
    f"FFmpeg: {'جاهز' if shutil.which('ffmpeg') else 'غير موجود'}",
    "",
    "آخر العمليات:",
]

if not jobs:
    lines.append("لا توجد عمليات مسجلة.")
else:
    for job_id, info in reversed(jobs):
        lines.extend([
            "",
            job_id,
            f"الحالة: {info.get('status', 'unknown')}",
            f"المرحلة: {info.get('stage', 'unknown')}",
        ])

        if info.get("error"):
            lines.append(
                f"الخطأ: {info['error'][:250]}"
            )

        if info.get("validation"):
            lines.append(
                "الفيديو: "
                + json.dumps(
                    info["validation"],
                    ensure_ascii=False,
                )
            )

await update.message.reply_text(
    "\n".join(lines)[:3900]
)

async def last_error_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
jobs = recent_jobs()

failed = [
    (job_id, info)
    for job_id, info in jobs
    if info.get("status") == "failed"
]

if not failed:
    await update.message.reply_text(
        "لا يوجد خطأ مسجل في آخر العمليات الموجودة بالذاكرة."
    )
    return

job_id, info = failed[-1]

await update.message.reply_text(
    "🚨 آخر خطأ مسجل\n\n"
    f"رقم العملية: {job_id}\n"
    f"المرحلة: {info.get('stage', 'unknown')}\n"
    f"الخطأ:\n{info.get('error', 'unknown')[:2500]}"
)

async def test_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
test_story = (
"في قلعة قديمة يظهر رجل غامض بقوة غير معروفة. "
"تراقبه الأميرة بينما يحاول الملك إخفاء سر خطير. "
"وفجأة يظهر نمر ضخم يهدد الجميع، "
"لكن البطل يخفي قوته الحقيقية. "
"وعندما يقترب الخطر ينكشف جزء من سره، "
"وتنتهي الحكاية بمفاجأة غامضة."
)

await launch_job(
    update,
    test_story,
)

async def story_command(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):
if not update.message or not update.message.text:
return

story = update.message.text.strip()

if len(story) < 10:
    await update.message.reply_text(
        "أرسل قصة أطول حتى أستطيع تجهيز الفيديو."
    )
    return

await launch_job(
    update,
    story,
)

=========================================================

START VIDEO JOB

=========================================================

async def launch_job(update, story):
global ACTIVE_JOBS

with ACTIVE_LOCK:
    if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
        await update.message.reply_text(
            "⏳ توجد عملية إنتاج جارية. "
            "انتظر حتى تنتهي قبل بدء عملية أخرى."
        )
        return

job_id = uuid.uuid4().hex[:10]

output_path = (
    OUTPUT_DIR / f"zil_{job_id}.mp4"
)

save_job(
    job_id,
    status="queued",
    stage="queued",
    story=story[:500],
    chat_id=update.effective_chat.id,
    user_id=(
        update.effective_user.id
        if update.effective_user
        else None
    ),
)

await update.message.reply_text(
    "🎬 بدأت صناعة فيديو ظل.\n"
    f"رقم العملية: {job_id}\n\n"
    "6 مشاهد، فيديو عمودي، "
    "راوي عربي وخلفية صوتية.\n\n"
    "استخدم /status لمتابعة العملية."
)

executor.submit(
    background_job,
    job_id,
    story,
    str(output_path),
    update.effective_chat.id,
)

def background_job(
job_id,
story,
output_path,
chat_id,
):
try:
result = build_video(
job_id,
story,
output_path,
)

    asyncio.run(
        send_video_to_user(
            chat_id,
            job_id,
            result,
        )
    )

except Exception:
    logger.exception(
        "BACKGROUND JOB ERROR | ID=%s",
        job_id,
    )

    job = get_job(job_id)

    if job.get("status") != "failed":
        report_error(
            job_id,
            "background_job",
            "فشلت العملية الخلفية أو إرسال الفيديو.",
            traceback.format_exc(),
        )

async def send_video_to_user(
chat_id,
job_id,
video_path,
):
try:
async with Bot(token=BOT_TOKEN) as bot:
with open(video_path, "rb") as video:
await bot.send_video(
chat_id=chat_id,
video=video,
caption=(
"✅ اكتمل فيديو ظل\n"
f"رقم العملية: {job_id}"
),
supports_streaming=True,
read_timeout=180,
write_timeout=180,
connect_timeout=30,
pool_timeout=30,
)

    save_job(
        job_id,
        delivered=True,
        stage="delivered",
    )

except Exception as exc:
    report_error(
        job_id,
        "telegram_video_upload",
        exc,
        traceback.format_exc(),
    )

    raise

finally:
    try:
        Path(video_path).unlink(
            missing_ok=True
        )
    except Exception:
        logger.exception(
            "Could not remove final video file"
        )

=========================================================

TELEGRAM GLOBAL ERROR HANDLER

=========================================================

async def telegram_error_handler(
update: object,
context: ContextTypes.DEFAULT_TYPE,
):
error = context.error

if error:
    trace = "".join(
        traceback.format_exception(
            type(error),
            error,
            error.__traceback__,
        )
    )

    logger.error(
        "TELEGRAM HANDLER ERROR\n%s",
        redact_secrets(trace),
    )

    notify_admin(
        "🚨 ZIL — خطأ في تيليجرام\n\n"
        f"النوع: {type(error).__name__}\n"
        f"الرسالة: {redact_secrets(str(error))[:1000]}\n\n"
        "التفاصيل:\n"
        f"{redact_secrets(trace)[-1800:]}"
    )

=========================================================

FLASK HEALTH ENDPOINTS

=========================================================

@app.get("/")
def home():
return "ZIL is running", 200

@app.get("/health")
def health():
checks = check_environment()

return jsonify({
    "service": "ZIL",
    "status": (
        "ok"
        if checks["FFmpeg"] and checks["FFprobe"]
        else "degraded"
    ),
    "checks": checks,
    "active_jobs": ACTIVE_JOBS,
    "time": time.strftime(
        "%Y-%m-%d %H:%M:%S"
    ),
}), 200

@app.get("/jobs")
def jobs_endpoint():
with JOBS_LOCK:
jobs_copy = {
key: dict(value)
for key, value in JOBS.items()
}

return jsonify({
    "jobs": jobs_copy,
    "active_jobs": ACTIVE_JOBS,
})

=========================================================

STARTUP

=========================================================

def start_flask():
app.run(
host="0.0.0.0",
port=PORT,
debug=False,
use_reloader=False,
)

def main():
require_tools()

if not ADMIN_CHAT_ID:
    logger.warning(
        "ADMIN_CHAT_ID missing; automatic reports "
        "cannot be delivered to admin."
    )

threading.Thread(
    target=start_flask,
    daemon=True,
).start()

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .build()
)

application.add_handler(
    CommandHandler("start", start_command)
)

application.add_handler(
    CommandHandler("status", status_command)
)

application.add_handler(
    CommandHandler("diagnose", diagnose_command)
)

application.add_handler(
    CommandHandler("last_error", last_error_command)
)

application.add_handler(
    CommandHandler("test", test_command)
)

application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        story_command,
    )
)

application.add_error_handler(
    telegram_error_handler
)

logger.info("ZIL service starting")
logger.info("Health endpoint: /health")

application.run_polling(
    drop_pending_updates=False,
    allowed_updates=Update.ALL_TYPES,
)

if name == "main":
main()
