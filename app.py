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
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# CONFIGURATION
# =========================================================

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

MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024

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


# =========================================================
# UTILITIES
# =========================================================

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
        error = (
            result.stderr or result.stdout or "Unknown FFmpeg error"
        )[-2500:]

        raise RuntimeError(
            f"Command failed ({result.returncode}): {error}"
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
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_CHAT_ID,
                "text": message[:3500],
            },
            timeout=15,
        )
        response.raise_for_status()

    except Exception:
        log.exception("Admin notification failed")


def report_error(job_id, error):
    log.error(
        "Job %s failed: %s",
        job_id,
        error,
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
            "ZIL VIDEO ERROR\n\n"
            f"Job: {job_id}\n"
            f"Error: {str(error)[:2500]}",
        ),
        daemon=True,
    ).start()


# =========================================================
# PIXABAY VIDEO SEARCH
# =========================================================

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

    response.raise_for_status()
    data = response.json()

    if not isinstance(data, dict) or "hits" not in data:
        raise RuntimeError("Unexpected Pixabay API response.")

    for hit in data.get("hits", []):
        videos = hit.get("videos") or {}

        for quality in ("medium", "large", "small", "tiny"):
            item = videos.get(quality) or {}
            url = item.get("url", "")

            if url and urlparse(url).scheme == "https":
                return url

    raise RuntimeError(
        f"No downloadable video found for: {query}"
    )


def download_video(url, destination):
    total = 0
    max_bytes = 150 * 1024 * 1024

    with requests.get(
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

                if total > max_bytes:
                    raise RuntimeError(
                        "Downloaded video exceeds 150 MB."
                    )

                output.write(chunk)

    if total < 10000:
        raise RuntimeError(
            "Downloaded video is too small."
        )

    return destination


# =========================================================
# VIDEO PROCESSING
# =========================================================

def normalize_scene(source, destination):
    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-t",
            str(SCENE_DURATION),
            "-vf",
            (
                f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
                "force_original_aspect_ratio=increase,"
                f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                "setsar=1,"
                f"fps={VIDEO_FPS},"
                "format=yuv420p"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        timeout=180,
    )

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "FFmpeg produced an empty scene."
        )

    return destination


# =========================================================
# ARABIC NARRATION
# =========================================================

def create_narration(text, destination):
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

        if (
            destination.exists()
            and destination.stat().st_size > 1000
        ):
            return destination

    except Exception:
        log.exception(
            "Edge TTS failed; trying gTTS."
        )

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
        log.exception(
            "gTTS failed."
        )

    raise RuntimeError(
        "Arabic narration generation failed."
    )


# =========================================================
# BACKGROUND AUDIO
# =========================================================

def make_background_music(destination, duration):
    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                "sine=frequency=110:sample_rate=44100:"
                f"duration={duration}"
            ),
            "-af",
            "volume=0.035,afade=t=in:d=2",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(destination),
        ],
        timeout=60,
    )

    if (
        not destination.exists()
        or destination.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Background audio generation failed."
        )

    return destination


# =========================================================
# FIXED AUDIO MIXING
# =========================================================

def mux_audio(video, narration, music, output):
    # The voice stream is used only once in the final mix.
    # This avoids the previous FFmpeg filtergraph error.

    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(narration),
            "-i",
            str(music),
            "-filter_complex",
            (
                "[1:a]volume=1.0[voice];"
                "[2:a]volume=0.12[bed];"
                "[voice][bed]"
                "amix=inputs=2:duration=first:"
                "dropout_transition=2[aout]"
            ),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ],
        timeout=180,
    )

    if (
        not output.exists()
        or output.stat().st_size < 10000
    ):
        raise RuntimeError(
            "Final video file is missing or too small."
        )

    return output


# =========================================================
# TELEGRAM ASYNC BRIDGE
# =========================================================

def send_coroutine(application, coroutine, timeout=360):
    loop = application.bot_data.get("event_loop")

    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError(
            "Telegram event loop is not available."
        )

    future = asyncio.run_coroutine_threadsafe(
        coroutine,
        loop,
    )

    return future.result(timeout=timeout)


async def send_video_to_user(
    application,
    chat_id,
    video_path,
    job_id,
):
    with open(video_path, "rb") as video_file:
        await application.bot.send_video(
            chat_id=chat_id,
            video=video_file,
            caption=(
                "تم إنشاء فيديو ظل ZIL.\n"
                f"رقم العملية: {job_id}"
            ),
            supports_streaming=True,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
            pool_timeout=30,
        )


async def send_text_to_user(
    application,
    chat_id,
    message,
):
    await application.bot.send_message(
        chat_id=chat_id,
        text=message[:3500],
    )


# =========================================================
# VIDEO GENERATION JOB
# =========================================================

def build_video(job_id, chat_id, story, telegram_app):
    global ACTIVE_JOBS

    workdir = BASE_DIR / job_id
    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        update_job(
            job_id,
            status="running",
            stage="preflight",
        )

        if not command_exists("ffmpeg"):
            raise RuntimeError(
                "FFmpeg is missing."
            )

        if not PIXABAY_API_KEY:
            raise RuntimeError(
                "PIXABAY_API_KEY is missing."
            )

        story = (
            story or ""
        ).strip()[:MAX_STORY_LENGTH]

        if not story:
            raise RuntimeError(
                "Story is empty."
            )

        queries = [
            "cinematic dramatic landscape",
            "dark medieval castle",
            "mysterious forest cinematic",
            "storm dramatic sky",
            "epic mountains cinematic",
            "castle sunset cinematic",
        ]

        scenes = []

        for index, query in enumerate(
            queries,
            start=1,
        ):
            update_job(
                job_id,
                stage=f"scene_{index}_download",
            )

            try:
                video_url = search_pixabay_video(
                    query
                )

            except Exception as error:
                log.warning(
                    "Search failed for %s: %s",
                    query,
                    error,
                )

                video_url = search_pixabay_video(
                    "cinematic nature landscape"
                )

            raw_path = (
                workdir / f"raw_{index}.mp4"
            )

            normalized_path = (
                workdir / f"scene_{index}.mp4"
            )

            download_video(
                video_url,
                raw_path,
            )

            update_job(
                job_id,
                stage=f"scene_{index}_processing",
            )

            normalize_scene(
                raw_path,
                normalized_path,
            )

            scenes.append(normalized_path)

            raw_path.unlink(
                missing_ok=True
            )

        # Concatenate scenes.
        update_job(
            job_id,
            stage="concatenate",
        )

        concat_file = (
            workdir / "concat.txt"
        )

        with open(
            concat_file,
            "w",
            encoding="utf-8",
        ) as file:
            for scene in scenes:
                safe_path = str(scene).replace(
                    "'",
                    "'\\''",
                )

                file.write(
                    f"file '{safe_path}'\n"
                )

        silent_video = (
            workdir / "silent_video.mp4"
        )

        run_command(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "25",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(VIDEO_FPS),
                "-movflags",
                "+faststart",
                str(silent_video),
            ],
            timeout=300,
        )

        # Generate Arabic narration.
        update_job(
            job_id,
            stage="narration",
        )

        narration_text = (
            story
            + "\n\nتابعوا الجزء القادم لاكتشاف السر."
        )

        narration_path = (
            workdir / "narration.mp3"
        )

        create_narration(
            narration_text,
            narration_path,
        )

        # Generate background audio.
        update_job(
            job_id,
            stage="background_audio",
        )

        music_path = (
            workdir / "ambient.m4a"
        )

        make_background_music(
            music_path,
            SCENE_COUNT * SCENE_DURATION,
        )

        # Mix audio with video.
        update_job(
            job_id,
            stage="mux_audio",
        )

        final_path = (
            workdir / "ZIL_video.mp4"
        )

        mux_audio(
            silent_video,
            narration_path,
            music_path,
            final_path,
        )

        update_job(
            job_id,
            status="sending",
            stage="sending_video",
            output=str(final_path),
            size=final_path.stat().st_size,
        )

        # Send final video through Telegram.
        if final_path.stat().st_size > MAX_VIDEO_SIZE:
            send_coroutine(
                telegram_app,
                send_text_to_user(
                    telegram_app,
                    chat_id,
                    (
                        "اكتمل إنشاء الفيديو، لكن حجمه أكبر "
                        "من حد الإرسال الذي ضبطه البوت.\n"
                        f"رقم العملية: {job_id}"
                    ),
                ),
                timeout=45,
            )

            update_job(
                job_id,
                status="completed",
                stage="completed_file_too_large",
            )

            return

        send_coroutine(
            telegram_app,
            send_video_to_user(
                telegram_app,
                chat_id,
                final_path,
                job_id,
            ),
            timeout=360,
        )

        update_job(
            job_id,
            status="completed",
            stage="completed",
        )

        log.info(
            "Job %s completed and sent to Telegram.",
            job_id,
        )

    except Exception as error:
        report_error(
            job_id,
            error,
        )

        try:
            send_coroutine(
                telegram_app,
                send_text_to_user(
                    telegram_app,
                    chat_id,
                    (
                        "تعذر إكمال الفيديو أو إرساله.\n"
                        f"رقم العملية: {job_id}\n"
                        "استخدم /last_error لمعرفة الخطأ."
                    ),
                ),
                timeout=45,
            )

        except Exception:
            log.exception(
                "Could not notify user about failure."
            )

    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(
                0,
                ACTIVE_JOBS - 1,
            )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    await update.message.reply_text(
        "أهلًا بك في ظل ZIL.\n\n"
        "/test - إنشاء فيديو تجريبي\n"
        "/make نص القصة - إنشاء فيديو من قصتك\n"
        "/status - حالة العمليات\n"
        "/diagnose - فحص النظام\n"
        "/last_error - آخر خطأ\n"
        "/health - حالة الخدمة"
    )


async def start_job(
    update,
    context,
    story,
):
    global ACTIVE_JOBS

    if not update.message:
        return

    if not update.effective_chat:
        return

    story = (
        story or ""
    ).strip()[:MAX_STORY_LENGTH]

    if not story:
        await update.message.reply_text(
            "أرسل نص القصة أولًا."
        )
        return

    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            busy = True
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

            # Reserve the slot before starting the worker.
            ACTIVE_JOBS += 1

    if busy:
        await update.message.reply_text(
            "يوجد فيديو قيد المعالجة. "
            "حاول مرة أخرى لاحقًا."
        )
        return

    await update.message.reply_text(
        "بدأت صناعة فيديو ظل ZIL.\n"
        f"رقم العملية: {job_id}\n"
        "6 مشاهد، فيديو عمودي، راوي عربي.\n"
        "سأرسل الفيديو هنا عند اكتماله."
    )

    try:
        worker = threading.Thread(
            target=build_video,
            args=(
                job_id,
                update.effective_chat.id,
                story,
                context.application,
            ),
            daemon=True,
        )

        worker.start()

    except Exception as error:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(
                0,
                ACTIVE_JOBS - 1,
            )

        report_error(
            job_id,
            error,
        )

        await update.message.reply_text(
            "تعذر بدء عملية إنشاء الفيديو."
        )


async def test_command(
    update,
    context,
):
    story = (
        "في مملكة غامضة، يصل رجل يخفي قوة خارقة. "
        "تقع الأميرة في حبه، لكن الملك يرفض العلاقة. "
        "يظهر نمر عملاق أمام القصر، فيواجهه الرجل "
        "ويكشف جزءًا من قوته المخفية."
    )

    await start_job(
        update,
        context,
        story,
    )


async def make_command(
    update,
    context,
):
    story = " ".join(
        context.args
    ).strip()

    if not story:
        await update.message.reply_text(
            "اكتب القصة بعد الأمر:\n"
            "/make وصل رجل غامض إلى القصر..."
        )
        return

    await start_job(
        update,
        context,
        story,
    )


async def status_command(
    update,
    context,
):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        recent = list(JOBS.values())[-5:]

    lines = [
        "حالة ظل ZIL",
        f"العمليات النشطة: {active}",
        (
            "Pixabay: جاهز"
            if PIXABAY_API_KEY
            else "Pixabay: مفتاح مفقود"
        ),
        (
            "FFmpeg: جاهز"
            if command_exists("ffmpeg")
            else "FFmpeg: غير موجود"
        ),
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
            lines.append(
                f"الخطأ: {job['error'][:400]}"
            )

    await update.message.reply_text(
        "\n".join(lines)[:3900]
    )


async def last_error_command(
    update,
    context,
):
    with JOBS_LOCK:
        failed = [
            job
            for job in JOBS.values()
            if job.get("status") == "failed"
        ]

    if not failed:
        await update.message.reply_text(
            "لا توجد أخطاء مسجلة حاليًا."
        )
        return

    job = failed[-1]

    await update.message.reply_text(
        "آخر خطأ في ظل ZIL\n\n"
        f"العملية: {job['id']}\n"
        f"المرحلة: {job.get('stage')}\n"
        "الخطأ:\n"
        f"{job.get('error', 'غير معروف')[:2500]}"
    )


async def diagnose_command(
    update,
    context,
):
    results = [
        f"BOT_TOKEN: {'OK' if BOT_TOKEN else 'MISSING'}",
        (
            "PIXABAY_API_KEY: OK"
            if PIXABAY_API_KEY
            else "PIXABAY_API_KEY: MISSING"
        ),
        (
            "ADMIN_CHAT_ID: OK"
            if ADMIN_CHAT_ID
            else "ADMIN_CHAT_ID: OPTIONAL/MISSING"
        ),
        f"Python: {sys.version.split()[0]}",
        (
            "FFmpeg: OK"
            if command_exists("ffmpeg")
            else "FFmpeg: MISSING"
        ),
        (
            "Work directory: OK"
            if BASE_DIR.exists()
            else "Work directory: MISSING"
        ),
    ]

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
        "تشخيص ظل ZIL\n\n"
        + "\n".join(results)
    )


async def health_command(
    update,
    context,
):
    await update.message.reply_text(
        "ظل ZIL يعمل.\n"
        f"Python: {sys.version.split()[0]}\n"
        f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}\n"
        f"Pixabay: {'OK' if PIXABAY_API_KEY else 'MISSING'}"
    )


# =========================================================
# FLASK ENDPOINTS
# =========================================================

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
    log.info("Starting ZIL service")
    log.info("Python: %s", sys.version)
    log.info("Port: %s", PORT)

    if not BOT_TOKEN:
        log.critical(
            "BOT_TOKEN is missing in Render Environment."
        )
        sys.exit(1)

    if not command_exists("ffmpeg"):
        log.critical(
            "FFmpeg is missing. Check Dockerfile."
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

    async def save_event_loop(application):
        application.bot_data["event_loop"] = (
            asyncio.get_running_loop()
        )

    telegram_app.post_init = save_event_loop

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

    log.info("Telegram polling starting")

    telegram_app.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
