import os
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
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

PORT = int(os.getenv("PORT", "10000"))

PIXABAY_API = "https://pixabay.com/api/videos/"

SCENE_COUNT = 6
SCENE_DURATION = 5
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

BASE_DIR = Path(tempfile.gettempdir()) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("ZIL")

app = Flask(__name__)

JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0

# =====================================================
# UTILITIES
# =====================================================

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
        error = (result.stderr or result.stdout or "")[-2500:]
        raise RuntimeError(
            f"Command failed ({result.returncode}): {error}"
        )

    return result


def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)
            JOBS[job_id]["updated_at"] = time.time()


async def notify_admin(context, message):
    if not ADMIN_CHAT_ID:
        return

    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=message[:3900],
        )
    except Exception:
        log.exception("Could not send admin notification")


def report_error(job_id, error):
    log.exception("Job %s failed: %s", job_id, error)

    update_job(
        job_id,
        status="failed",
        error=str(error)[:2000],
    )

    if not ADMIN_CHAT_ID or not BOT_TOKEN:
        return

    message = (
        "⚠️ ZIL VIDEO ERROR\n\n"
        f"Job: {job_id}\n"
        f"Error: {str(error)[:2500]}\n"
        "راجع Render Logs لمعرفة التفاصيل."
    )

    def send():
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": int(ADMIN_CHAT_ID),
                    "text": message[:3900],
                },
                timeout=15,
            )
        except Exception:
            log.exception("Admin report failed")

    threading.Thread(target=send, daemon=True).start()


# =====================================================
# PIXABAY VIDEO SEARCH
# =====================================================

def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError(
            "PIXABAY_API_KEY is missing in Render Environment."
        )

    response = requests.get(
        PIXABAY_API,
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "per_page": 10,
            "safesearch": "true",
            "video_type": "film",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pixabay HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError("Pixabay returned invalid JSON.")

    if "hits" not in data:
        raise RuntimeError(
            "Unexpected Pixabay response: "
            + json.dumps(data, ensure_ascii=False)[:700]
        )

    for hit in data.get("hits", []):
        videos = hit.get("videos") or {}

        for quality in ("medium", "large", "small", "tiny"):
            item = videos.get(quality) or {}
            url = item.get("url")

            if url and urlparse(url).scheme == "https":
                return url

    raise RuntimeError(
        f"No downloadable video URL found for: {query}"
    )


def download_video(url, destination):
    with requests.get(
        url,
        stream=True,
        timeout=(20, 90),
    ) as response:
        response.raise_for_status()

        total = 0
        max_bytes = 150 * 1024 * 1024

        with open(destination, "wb") as output:
            for chunk in response.iter_content(1024 * 256):
                if not chunk:
                    continue

                total += len(chunk)

                if total > max_bytes:
                    raise RuntimeError(
                        "Downloaded video exceeds 150 MB limit."
                    )

                output.write(chunk)

    if total < 10_000:
        raise RuntimeError("Downloaded video is too small.")

    return destination


# =====================================================
# VIDEO PROCESSING
# =====================================================

def normalize_scene(source, destination):
    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-t", str(SCENE_DURATION),
        "-vf",
        (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
            "setsar=1,fps=24,format=yuv420p"
        ),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "25",
        "-movflags", "+faststart",
        str(destination),
    ], timeout=180)

    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("FFmpeg produced an empty scene.")

    return destination


def create_narration(text, destination):
    """
    Arabic narration using Edge TTS.
    Falls back to gTTS if Edge TTS fails.
    """

    try:
        import edge_tts

        async def generate():
            communicate = edge_tts.Communicate(
                text,
                voice="ar-SA-HamedNeural",
                rate="-8%",
            )
            await communicate.save(str(destination))

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

    raise RuntimeError("Both Arabic narration engines failed.")


def make_background_music(destination, duration):
    """
    Generate a quiet ambient audio bed without an external music API.
    """

    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        (
            "sine=frequency=110:sample_rate=44100:"
            f"duration={duration}"
        ),
        "-af", "volume=0.035,afade=t=in:d=2",
        "-c:a", "aac",
        "-b:a", "96k",
        str(destination),
    ], timeout=60)

    return destination


def mux_audio(video, narration, music, output):
    run_command([
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-i", str(narration),
        "-i", str(music),
        "-filter_complex",
        (
            "[1:a]volume=1.0[voice];"
            "[2:a]volume=0.20[bed];"
            "[bed][voice]sidechaincompress="
            "threshold=0.03:ratio=6:attack=20:release=300[duck];"
            "[voice][duck]amix=inputs=2:duration=first:"
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

    if not output.exists() or output.stat().st_size < 10_000:
        raise RuntimeError("Final video file is missing or too small.")

    return output


# =====================================================
# VIDEO JOB
# =====================================================

def build_video(job_id, story):
    global ACTIVE_JOBS

    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    with JOBS_LOCK:
        ACTIVE_JOBS += 1

    try:
        update_job(
            job_id,
            status="running",
            stage="preflight",
        )

        if not command_exists("ffmpeg"):
            raise RuntimeError(
                "FFmpeg is missing. Check the Dockerfile."
            )

        if not PIXABAY_API_KEY:
            raise RuntimeError(
                "PIXABAY_API_KEY is not configured."
            )

        update_job(job_id, stage="story")

        if not story or not story.strip():
            story = (
                "رجل غامض يصل إلى مملكة قديمة، "
                "فتكتشف الأميرة أن لديه قوة مخفية."
            )

        # Queries can be adjusted to match the submitted story.
        queries = [
            "cinematic dramatic landscape",
            "dark medieval castle",
            "mysterious forest cinematic",
            "storm dramatic sky",
            "epic mountains cinematic",
            "castle sunset cinematic",
        ]

        scenes = []

        for index in range(SCENE_COUNT):
            stage = f"scene_{index + 1}_download"
            update_job(job_id, stage=stage)

            query = queries[index]

            try:
                video_url = search_pixabay_video(query)
            except Exception as first_error:
                log.warning(
                    "Primary query failed for %s: %s",
                    query,
                    first_error,
                )

                fallback_query = "cinematic nature"
                video_url = search_pixabay_video(fallback_query)

            raw_path = workdir / f"raw_{index + 1}.mp4"
            normalized_path = workdir / f"scene_{index + 1}.mp4"

            download_video(video_url, raw_path)

            update_job(
                job_id,
                stage=f"scene_{index + 1}_processing",
            )

            normalize_scene(raw_path, normalized_path)
            scenes.append(normalized_path)

        update_job(job_id, stage="concatenate")

        concat_file = workdir / "concat.txt"

        with open(concat_file, "w", encoding="utf-8") as file:
            for scene in scenes:
                safe_path = str(scene).replace("'", "'\\''")
                file.write(f"file '{safe_path}'\n")

        silent_video = workdir / "silent_video.mp4"

        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "25",
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            "-movflags", "+faststart",
            str(silent_video),
        ], timeout=300)

        update_job(job_id, stage="narration")

        narration_text = (
            story.strip()
            + " تابعوا الجزء القادم لاكتشاف السر."
        )

        narration_path = workdir / "narration.mp3"
        create_narration(narration_text, narration_path)

        update_job(job_id, stage="audio")

        music_path = workdir / "ambient.m4a"
        make_background_music(
            music_path,
            SCENE_COUNT * SCENE_DURATION,
        )

        final_path = workdir / "ZIL_video.mp4"

        update_job(job_id, stage="mux_audio")

        mux_audio(
            silent_video,
            narration_path,
            music_path,
            final_path,
        )

        update_job(
            job_id,
            status="completed",
            stage="completed",
            output=str(final_path),
            size=final_path.stat().st_size,
        )

        log.info(
            "Job %s completed successfully: %s",
            job_id,
            final_path,
        )

    except Exception as error:
        log.exception("Job %s failed", job_id)

        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(error)[:2000],
        )

    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)


# =====================================================
# TELEGRAM COMMANDS
# =====================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🎬 أهلًا بك في ظل ZIL.\n\n"
        "الأوامر:\n"
        "/test - إنشاء فيديو تجريبي\n"
        "/make نص القصة - إنشاء فيديو من قصتك\n"
        "/status - حالة العمليات\n"
        "/diagnose - فحص النظام\n"
        "/last_error - آخر خطأ\n"
        "/health - حالة الخدمة"
    )


async def start_job(update, context, story):
    job_id = uuid.uuid4().hex[:10]

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "story": story[:500],
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    thread = threading.Thread(
        target=build_video,
        args=(job_id, story),
        daemon=True,
    )
    thread.start()

    await update.message.reply_text(
        "🎬 بدأت صناعة فيديو ظل ZIL.\n"
        f"رقم العملية: {job_id}\n\n"
        "6 مشاهد، فيديو عمودي، راوي عربي.\n"
        "استخدم /status لمتابعة العملية."
    )


async def test_command(update, context):
    story = (
        "في مملكة غامضة، يصل رجل يخفي قوة خارقة. "
        "ترى الأميرة قوته، لكن الملك يحذّر من كشف السر."
    )
    await start_job(update, context, story)


async def make_command(update, context):
    story = " ".join(context.args).strip()

    if not story:
        await update.message.reply_text(
            "اكتب القصة بعد الأمر:\n"
            "/make وصل رجل غامض إلى القصر..."
        )
        return

    await start_job(update, context, story)


async def status_command(update, context):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        recent = list(JOBS.values())[-5:]

    lines = [
        "📊 حالة ظل ZIL",
        f"العمليات النشطة: {active}",
        f"Pixabay: {'جاهز' if PIXABAY_API_KEY else 'مفتاح مفقود'}",
        f"FFmpeg: {'جاهز' if command_exists('ffmpeg') else 'غير موجود'}",
        "",
        "آخر العمليات:",
    ]

    for job in reversed(recent):
        lines.append(
            f"\n{job['id']}\n"
            f"الحالة: {job.get('status')}\n"
            f"المرحلة: {job.get('stage')}"
        )

        if job.get("error"):
            lines.append(f"الخطأ: {job['error'][:500]}")

    await update.message.reply_text("\n".join(lines)[:3900])


async def last_error_command(update, context):
    with JOBS_LOCK:
        failed = [
            job for job in JOBS.values()
            if job.get("status") == "failed"
        ]

    if not failed:
        await update.message.reply_text(
            "✅ لا توجد أخطاء مسجلة في الذاكرة الحالية."
        )
        return

    job = failed[-1]

    await update.message.reply_text(
        f"آخر خطأ في ظل ZIL\n\n"
        f"العملية: {job['id']}\n"
        f"المرحلة: {job.get('stage')}\n"
        f"الخطأ:\n{job.get('error', 'غير معروف')[:2500]}"
    )


async def diagnose_command(update, context):
    results = []

    results.append(
        f"BOT_TOKEN: {'OK' if BOT_TOKEN else 'MISSING'}"
    )
    results.append(
        f"PIXABAY_API_KEY: {'OK' if PIXABAY_API_KEY else 'MISSING'}"
    )
    results.append(
        f"ADMIN_CHAT_ID: {'OK' if ADMIN_CHAT_ID else 'OPTIONAL/MISSING'}"
    )
    results.append(
        f"Python: {sys.version.split()[0]}"
    )
    results.append(
        f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}"
    )
    results.append(
        f"Work directory: {'OK' if BASE_DIR.exists() else 'MISSING'}"
    )

    if PIXABAY_API_KEY:
        try:
            response = requests.get(
                PIXABAY_API,
                params={
                    "key": PIXABAY_API_KEY,
                    "q": "nature",
                    "per_page": 3,
                    "safesearch": "true",
                },
                timeout=15,
            )

            if response.status_code == 200:
                data = response.json()
                results.append(
                    "Pixabay API: OK "
                    f"(hits={len(data.get('hits', []))})"
                )
            else:
                results.append(
                    f"Pixabay API: HTTP {response.status_code}"
                )

        except Exception as error:
            results.append(
                f"Pixabay API: ERROR {str(error)[:300]}"
            )

    await update.message.reply_text(
        "🔍 تشخيص ظل ZIL\n\n"
        + "\n".join(results)
    )


async def health_command(update, context):
    await update.message.reply_text(
        "✅ ظل ZIL يعمل.\n"
        f"Python: {sys.version.split()[0]}\n"
        f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}\n"
        f"Pixabay key: {'OK' if PIXABAY_API_KEY else 'MISSING'}"
    )


# =====================================================
# FLASK HEALTH ENDPOINTS
# =====================================================

@app.get("/")
def home():
    return jsonify({
        "service": "ZIL",
        "status": "running",
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "ffmpeg": command_exists("ffmpeg"),
        "pixabay_configured": bool(PIXABAY_API_KEY),
        "active_jobs": ACTIVE_JOBS,
    })


def run_web():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =====================================================
# STARTUP
# =====================================================

def main():
    log.info("Starting ZIL service...")
    log.info("Python: %s", sys.version)
    log.info("Port: %s", PORT)
    log.info("FFmpeg available: %s", command_exists("ffmpeg"))
    log.info("Pixabay key configured: %s", bool(PIXABAY_API_KEY))

    if not BOT_TOKEN:
        log.critical(
            "BOT_TOKEN is missing. Add it in Render Environment."
        )
        sys.exit(1)

    if not command_exists("ffmpeg"):
        log.critical(
            "FFmpeg is missing. Check the Dockerfile installation."
        )
        sys.exit(1)

    threading.Thread(
        target=run_web,
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

    log.info("Telegram polling starting...")

    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
