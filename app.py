import os
import sys
import time
import uuid
import asyncio
import logging
import threading
import subprocess
import tempfile
from pathlib import Path

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PIXABAY_API_KEY = (
os.getenv("PIXABAY_API_KEY", "").strip()
or os.getenv("PIXABAY_KEY", "").strip()
)
PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 6
SCENE_DURATION = 5
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
MAX_STORY_LENGTH = 2500

BASE_DIR = Path(tempfile.gettempdir()) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s %(levelname)s %(message)s",
stream=sys.stdout,
)
log = logging.getLogger("ZIL")

app = Flask(name)
JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0

def command_exists(name):
try:
result = subprocess.run(
["which", name],
capture_output=True,
text=True,
timeout=5,
)
return result.returncode == 0
except Exception:
return False

def run_command(command, timeout=180):
result = subprocess.run(
command,
capture_output=True,
text=True,
timeout=timeout,
)
if result.returncode != 0:
raise RuntimeError(
(result.stderr or result.stdout or "Command failed")[-2500:]
)
return result

def update_job(job_id, **values):
with JOBS_LOCK:
if job_id in JOBS:
JOBS[job_id].update(values)
JOBS[job_id]["updated_at"] = time.time()

def search_pixabay_video(query):
if not PIXABAY_API_KEY:
raise RuntimeError("PIXABAY_API_KEY is missing.")

response = requests.get(
    "https://pixabay.com/api/videos/",
    params={
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": 10,
        "safesearch": "true",
        "video_type": "film",
    },
    timeout=30,
)
response.raise_for_status()
data = response.json()

for hit in data.get("hits", []):
    videos = hit.get("videos") or {}
    for quality in ("medium", "large", "small", "tiny"):
        item = videos.get(quality) or {}
        url = item.get("url", "")
        if url.startswith("https://"):
            return url

raise RuntimeError(f"No video found for query: {query}")

def download_video(url, destination):
total = 0
limit = 150 * 1024 * 1024

with requests.get(
    url,
    stream=True,
    timeout=(20, 90),
) as response:
    response.raise_for_status()
    with open(destination, "wb") as output:
        for chunk in response.iter_content(262144):
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise RuntimeError("Video exceeds 150 MB.")
            output.write(chunk)

if total < 10000:
    raise RuntimeError("Downloaded video is too small.")

return destination

def normalize_scene(source, destination):
run_command([
"ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
"-i", str(source),
"-t", str(SCENE_DURATION),
"-vf",
(
f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
"force_original_aspect_ratio=increase,"
f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
f"fps={VIDEO_FPS},setsar=1,format=yuv420p"
),
"-an",
"-c:v", "libx264",
"-preset", "ultrafast",
"-crf", "25",
"-movflags", "+faststart",
str(destination),
], timeout=180)

if not destination.exists() or destination.stat().st_size < 1000:
    raise RuntimeError("Scene processing failed.")

return destination

def create_narration(text, destination):
try:
import edge_tts

    async def generate():
        voice = edge_tts.Communicate(
            text,
            voice="ar-SA-HamedNeural",
            rate="-8%",
        )
        await voice.save(str(destination))

    asyncio.run(generate())

    if destination.exists() and destination.stat().st_size > 1000:
        return destination
except Exception:
    log.exception("Edge TTS failed.")

try:
    from gtts import gTTS
    gTTS(text=text, lang="ar").save(str(destination))
    if destination.exists() and destination.stat().st_size > 1000:
        return destination
except Exception:
    log.exception("gTTS failed.")

raise RuntimeError("Arabic narration generation failed.")

def create_music(destination, duration):
run_command([
"ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
"-f", "lavfi",
"-i", f"sine=frequency=110:sample_rate=44100:duration={duration}",
"-af", "volume=0.035,afade=t=in:d=2",
"-c:a", "aac", "-b:a", "96k",
str(destination),
], timeout=60)
return destination

def combine_audio(video, narration, music, output):
run_command([
"ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
"-i", str(video),
"-i", str(narration),
"-i", str(music),
"-filter_complex",
(
"[1:a]volume=1.0[voice];"
"[2:a]volume=0.15[bed];"
"[voice][bed]amix=inputs=2:duration=first:"
"dropout_transition=2[aout]"
),
"-map", "0:v:0",
"-map", "[aout]",
"-c:v", "copy",
"-c:a", "aac",
"-b:a", "128k",
"-shortest",
"-movflags", "+faststart",
str(output),
], timeout=180)

if not output.exists() or output.stat().st_size < 10000:
    raise RuntimeError("Final video generation failed.")

return output

def build_video(job_id, story):
global ACTIVE_JOBS

workdir = BASE_DIR / job_id
workdir.mkdir(parents=True, exist_ok=True)

with JOBS_LOCK:
    ACTIVE_JOBS += 1

try:
    update_job(job_id, status="running", stage="preflight")

    if not command_exists("ffmpeg"):
        raise RuntimeError("FFmpeg is missing.")

    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")

    queries = [
        "cinematic dramatic landscape",
        "dark medieval castle",
        "mysterious forest cinematic",
        "storm dramatic sky",
        "epic mountains cinematic",
        "castle sunset cinematic",
    ]

    scenes = []

    for index, query in enumerate(queries, start=1):
        update_job(job_id, stage=f"scene_{index}_download")

        try:
            url = search_pixabay_video(query)
        except Exception:
            url = search_pixabay_video("cinematic nature landscape")

        raw = workdir / f"raw_{index}.mp4"
        scene = workdir / f"scene_{index}.mp4"

        download_video(url, raw)
        update_job(job_id, stage=f"scene_{index}_processing")
        normalize_scene(raw, scene)
        scenes.append(scene)
        raw.unlink(missing_ok=True)

    update_job(job_id, stage="concatenate")

    concat_file = workdir / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as file:
        for scene in scenes:
            file.write(f"file '{scene.as_posix()}'\n")

    silent = workdir / "silent.mp4"

    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        "-movflags", "+faststart",
        str(silent),
    ], timeout=300)

    update_job(job_id, stage="narration")
    narration = workdir / "narration.mp3"
    create_narration(
        story[:MAX_STORY_LENGTH] + "\nتابعوا الجزء القادم لاكتشاف السر.",
        narration,
    )

    update_job(job_id, stage="audio")
    music = workdir / "music.m4a"
    create_music(music, SCENE_COUNT * SCENE_DURATION)

    output = workdir / "ZIL_video.mp4"
    update_job(job_id, stage="finalizing")
    combine_audio(silent, narration, music, output)

    update_job(
        job_id,
        status="completed",
        stage="completed",
        output=str(output),
        size=output.stat().st_size,
    )
    log.info("Job %s completed.", job_id)

except Exception as error:
    log.exception("Job %s failed.", job_id)
    update_job(
        job_id,
        status="failed",
        stage="failed",
        error=str(error)[:2000],
    )
finally:
    with JOBS_LOCK:
        ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
"أهلًا بك في ظل ZIL.\n\n"
"/test - فيديو تجريبي\n"
"/make نص القصة - إنشاء فيديو\n"
"/status - حالة العمليات\n"
"/diagnose - فحص الإعدادات\n"
"/last_error - آخر خطأ\n"
"/health - حالة الخدمة"
)

async def start_job(update, story):
if not update.message:
return

story = (story or "").strip()[:MAX_STORY_LENGTH]
if not story:
    await update.message.reply_text("أرسل نص القصة.")
    return

with JOBS_LOCK:
    if ACTIVE_JOBS > 0:
        await update.message.reply_text(
            "هناك فيديو قيد المعالجة. حاول لاحقًا."
        )
        return

    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {
        "id": job_id,
        "status": "queued",
        "stage": "queued",
        "story": story[:500],
        "created_at": time.time(),
        "updated_at": time.time(),
    }

threading.Thread(
    target=build_video,
    args=(job_id, story),
    daemon=True,
).start()

await update.message.reply_text(
    f"بدأ إنشاء الفيديو.\nرقم العملية: {job_id}\n"
    "استخدم /status لمتابعة العملية."
)

async def test_command(update, context):
story = (
"في مملكة غامضة، يصل رجل يخفي قوة خارقة. "
"تحبه الأميرة ويرفض الملك علاقتهما. "
"يظهر نمر عملاق أمام القصر، فيواجهه الرجل "
"ويكشف جزءًا من قوته السرية."
)
await start_job(update, story)

async def make_command(update, context):
await start_job(update, " ".join(context.args))

async def status_command(update, context):
with JOBS_LOCK:
active = ACTIVE_JOBS
jobs = list(JOBS.values())[-5:]

lines = [
    f"العمليات النشطة: {active}",
    f"Pixabay: {'جاهز' if PIXABAY_API_KEY else 'مفتاح مفقود'}",
]

for job in reversed(jobs):
    lines.append(
        f"\n{job['id']} | {job['status']} | {job['stage']}"
    )
    if job.get("error"):
        lines.append(job["error"][:300])

await update.message.reply_text("\n".join(lines)[:3900])

async def diagnose_command(update, context):
results = [
f"Python: {sys.version.split()[0]}",
f"BOT_TOKEN: {'OK' if BOT_TOKEN else 'MISSING'}",
f"PIXABAY_API_KEY: {'OK' if PIXABAY_API_KEY else 'MISSING'}",
f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}",
]

if PIXABAY_API_KEY:
    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_API_KEY,
                "q": "nature",
                "per_page": 1,
            },
            timeout=15,
        )
        results.append(f"Pixabay API: HTTP {response.status_code}")
    except Exception as error:
        results.append(f"Pixabay error: {str(error)[:200]}")

await update.message.reply_text("\n".join(results))

async def last_error_command(update, context):
with JOBS_LOCK:
failed = [
job for job in JOBS.values()
if job.get("status") == "failed"
]

if not failed:
    await update.message.reply_text("لا توجد أخطاء مسجلة.")
    return

job = failed[-1]
await update.message.reply_text(
    f"العملية: {job['id']}\n"
    f"المرحلة: {job.get('stage')}\n"
    f"الخطأ: {job.get('error', 'غير معروف')[:2500]}"
)

async def health_command(update, context):
await update.message.reply_text(
f"ZIL يعمل.\nFFmpeg: {command_exists('ffmpeg')}\n"
f"Pixabay: {bool(PIXABAY_API_KEY)}"
)

@app.get("/")
def home():
return jsonify({"service": "ZIL", "status": "running"})

@app.get("/health")
def health():
with JOBS_LOCK:
active = ACTIVE_JOBS

return jsonify({
    "status": "ok",
    "ffmpeg": command_exists("ffmpeg"),
    "pixabay_configured": bool(PIXABAY_API_KEY),
    "active_jobs": active,
})

def run_web():
app.run(
host="0.0.0.0",
port=PORT,
debug=False,
use_reloader=False,
)

def main():
log.info("Starting ZIL.")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not command_exists("ffmpeg"):
    raise RuntimeError("FFmpeg is missing from Docker image.")

threading.Thread(target=run_web, daemon=True).start()

telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("test", test_command))
telegram_app.add_handler(CommandHandler("make", make_command))
telegram_app.add_handler(CommandHandler("status", status_command))
telegram_app.add_handler(CommandHandler("diagnose", diagnose_command))
telegram_app.add_handler(CommandHandler("last_error", last_error_command))
telegram_app.add_handler(CommandHandler("health", health_command))

log.info("Telegram polling starting.")
telegram_app.run_polling(
    drop_pending_updates=False,
    allowed_updates=Update.ALL_TYPES,
)

if name == "main":
main()
