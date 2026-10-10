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
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

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

SCENE_COUNT = 6
SCENE_SECONDS = 5
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 25
TOTAL_SECONDS = SCENE_COUNT * SCENE_SECONDS

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/zil_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MAX_ACTIVE_JOBS = 1
JOB_TIMEOUT_SECONDS = 600

PIXABAY_SEARCH_URL = "https://pixabay.com/api/"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("zil")

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=2)

JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0
ACTIVE_LOCK = threading.Lock()

# Prevent duplicate diagnostic messages for the same job.
REPORTED_ERRORS = set()
REPORTED_LOCK = threading.Lock()


# =========================================================
# DIAGNOSTICS AND ERROR REPORTING
# =========================================================

def redact_secrets(text):
    """Remove API keys and tokens before writing or sending reports."""
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


def run_command(command, timeout=90):
    """Run a subprocess and preserve useful error details."""
    logger.info("RUN COMMAND: %s", " ".join(map(str, command)))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )

    if result.returncode != 0:
        details = redact_secrets(
            (result.stderr or result.stdout or "Unknown command error")[-6000:]
        )
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}\n"
            f"Command: {command[0]}\n"
            f"Details:\n{details}"
        )

    return result


def save_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {})
        job.update(fields)
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id, {}))


async def send_admin_message(text):
    """Send private diagnostic reports to the configured admin chat."""
    if not ADMIN_CHAT_ID:
        logger.error(
            "ADMIN_CHAT_ID is missing; automatic Telegram reports are disabled."
        )
        return

    try:
        from telegram import Bot

        bot = Bot(token=BOT_TOKEN)

        async with bot:
            await bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=redact_secrets(text)[:3900],
            )

    except Exception:
        logger.exception("Could not send admin diagnostic message")


def notify_admin(text):
    """Run admin notification safely from synchronous worker threads."""
    try:
        asyncio.run(send_admin_message(text))
    except Exception:
        logger.exception("Admin notification failed")


def report_error(job_id, stage, exc, tb_text=None):
    """Record the complete error and send one report for the failed job."""
    with REPORTED_LOCK:
        if job_id in REPORTED_ERRORS:
            return
        REPORTED_ERRORS.add(job_id)

    if tb_text is None:
        tb_text = traceback.format_exc()

    details = redact_secrets(str(exc))
    safe_trace = redact_secrets(tb_text)

    save_job(
        job_id,
        status="failed",
        stage=stage,
        error=details,
    )

    logger.error(
        "JOB FAILED | id=%s | stage=%s | error=%s\n%s",
        job_id,
        stage,
        details,
        safe_trace,
    )

    report = (
        "🚨 ZIL — تقرير خطأ تلقائي\n\n"
        f"رقم العملية: {job_id}\n"
        f"المرحلة: {stage}\n"
        f"الخطأ: {details[:1200]}\n\n"
        f"وقت الخطأ: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "تفاصيل التتبع:\n"
        f"{safe_trace[-1700:]}\n\n"
        "تم تسجيل الخطأ. راجع هذا التقرير لتحديد التعديل المطلوب."
    )

    notify_admin(report)


# =========================================================
# SYSTEM HEALTH
# =========================================================

def check_environment():
    results = {}

    results["BOT_TOKEN"] = bool(BOT_TOKEN)
    results["PIXABAY_API_KEY"] = bool(PIXABAY_API_KEY)
    results["ADMIN_CHAT_ID"] = bool(ADMIN_CHAT_ID)
    results["ffmpeg"] = shutil.which("ffmpeg") is not None
    results["ffprobe"] = shutil.which("ffprobe") is not None

    results["output_directory"] = OUTPUT_DIR.exists()

    return results


def require_tools():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not PIXABAY_API_KEY:
        missing.append("PIXABAY_API_KEY أو PIXABAY_KEY")

    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg")

    if not shutil.which("ffprobe"):
        missing.append("ffprobe")

    if missing:
        raise RuntimeError(
            "Missing configuration or tools: " + ", ".join(missing)
        )


def probe_duration(path):
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

    return float(result.stdout.strip())


def validate_video(path):
    path = Path(path)

    if not path.exists():
        raise RuntimeError("Output video does not exist")

    if path.stat().st_size < 100_000:
        raise RuntimeError("Output video is unexpectedly small")

    duration = probe_duration(path)

    if duration < 20 or duration > 40:
        raise RuntimeError(
            f"Unexpected final video duration: {duration:.2f} seconds"
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
        raise RuntimeError("Final video has no video stream")

    width = streams[0].get("width")
    height = streams[0].get("height")

    if not width or not height or height <= width:
        raise RuntimeError(
            f"Final video is not vertical: {width}x{height}"
        )

    return {
        "duration": round(duration, 2),
        "width": width,
        "height": height,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
    }


# =========================================================
# PIXABAY STOCK VIDEO
# =========================================================

def search_pixabay_video(query):
    response = requests.get(
        PIXABAY_SEARCH_URL,
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "video_type": "film",
            "safesearch": "true",
            "per_page": 15,
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    if "hits" not in data:
        raise RuntimeError(
            "Pixabay returned an unexpected response: "
            + json.dumps(data, ensure_ascii=False)[:1000]
        )

    hits = data.get("hits", [])

    if not hits:
        raise RuntimeError(f"No Pixabay videos found for: {query}")

    # Prefer higher-resolution sources when available.
    hits.sort(
        key=lambda item: (
            item.get("videos", {}).get("large", {}).get("width", 0)
            * item.get("videos", {}).get("large", {}).get("height", 0)
        ),
        reverse=True,
    )

    for hit in hits:
        videos = hit.get("videos", {})

        for size in ("large", "medium", "small", "tiny"):
            video = videos.get(size, {})
            url = video.get("url")

            if url:
                return url

    raise RuntimeError(f"No downloadable video URL found for: {query}")


def download_video(url, destination):
    with requests.get(
        url,
        stream=True,
        timeout=(20, 60),
    ) as response:
        response.raise_for_status()

        length = response.headers.get("Content-Length")

        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("Downloaded video exceeds the size limit")

        total = 0

        with open(destination, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("Video download exceeded size limit")

                file.write(chunk)

    if not Path(destination).exists() or Path(destination).stat().st_size < 10_000:
        raise RuntimeError("Downloaded video is empty or invalid")

    # Verify the downloaded media before rendering.
    probe_duration(destination)

    return destination


# =========================================================
# STORY AND SCENES
# =========================================================

def make_search_queries(story):
    """
    Convert the submitted story into practical stock-footage searches.
    This does not claim to generate original AI footage.
    """
    text = re.sub(r"\s+", " ", story).strip()

    base_queries = [
        "cinematic fantasy castle",
        "mysterious man dramatic cinematic",
        "princess royal palace cinematic",
        "dark storm dramatic landscape",
        "wild tiger running cinematic",
        "epic fantasy battle landscape",
    ]

    if text:
        # Search using the story's main words, while retaining
        # reliable English stock-footage fallback searches.
        words = re.findall(r"[A-Za-z]{4,}", text)[:4]

        if words:
            base_queries[0] = "cinematic " + " ".join(words[:2])

    return base_queries[:SCENE_COUNT]


# =========================================================
# ARABIC CAPTIONS
# =========================================================

def make_caption_image(text, destination):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError as exc:
        raise RuntimeError(
            "Caption dependencies are missing. "
            "Install Pillow, arabic-reshaper and python-bidi."
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
            font = ImageFont.truetype(font_path, 36)
            break

    if font is None:
        font = ImageFont.load_default()

    reshaped = arabic_reshaper.reshape(text)
    display_text = get_display(reshaped)

    # Break long lines so captions remain inside the frame.
    words = display_text.split()
    lines = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()

        if draw.textbbox((0, 0), candidate, font=font)[2] > VIDEO_WIDTH - 70:
            if current:
                lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    lines = lines[:3]
    line_height = 48
    start_y = max(8, (230 - len(lines) * line_height) // 2)

    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=2)
        text_width = bbox[2] - bbox[0]
        x = max(10, (VIDEO_WIDTH - text_width) // 2)
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


# =========================================================
# NARRATION AND BACKGROUND AUDIO
# =========================================================

async def create_edge_voice(text, destination):
    communicate = edge_tts.Communicate(
        text=text,
        voice="ar-SA-HamedNeural",
        rate="-8%",
        pitch="-2Hz",
    )
    await communicate.save(str(destination))


def create_narration(text, destination):
    try:
        asyncio.run(create_edge_voice(text, destination))
    except Exception:
        logger.exception("Edge TTS failed; trying gTTS fallback")

        try:
            from gtts import gTTS

            gTTS(text=text, lang="ar").save(str(destination))

        except Exception as exc:
            raise RuntimeError(
                "Both Edge TTS and gTTS failed: " + str(exc)
            ) from exc

    if not Path(destination).exists() or Path(destination).stat().st_size < 1000:
        raise RuntimeError("Narration file is missing or too small")


def create_ambient_audio(destination):
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", "anoisesrc=color=pink:amplitude=0.03:sample_rate=44100",
            "-t", str(TOTAL_SECONDS),
            "-af", "lowpass=f=350,volume=0.12",
            "-ac", "2",
            "-c:a", "aac",
            str(destination),
        ],
        timeout=90,
    )


# =========================================================
# VIDEO RENDERING
# =========================================================

def render_scene(source, caption_path, destination):
    """
    Fit stock footage to a vertical canvas without black bars.
    Crop the source to fill the screen, then overlay the caption.
    """
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(caption_path),
            "-filter_complex",
            (
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
                "[base][cap]overlay="
                f"x=0:y={VIDEO_HEIGHT - 260}:"
                "shortest=1,"
                "format=yuv420p"
                "[out]"
            ),
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

    probe_duration(destination)

    return destination


def concatenate_scenes(scene_paths, destination):
    """
    Concatenate scenes with a short dissolve.
    Re-encode to keep the final format consistent.
    """
    if not scene_paths:
        raise RuntimeError("No rendered scenes to concatenate")

    if len(scene_paths) == 1:
        shutil.copy2(scene_paths[0], destination)
        return destination

    inputs = []

    for path in scene_paths:
        inputs.extend(["-i", str(path)])

    transition = 0.30
    clip_duration = float(SCENE_SECONDS)

    filter_parts = []

    for index in range(len(scene_paths)):
        filter_parts.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
        )

    last_label = "v0"
    current_duration = clip_duration

    for index in range(1, len(scene_paths)):
        output_label = f"x{index}"
        offset = current_duration - transition

        filter_parts.append(
            f"[{last_label}][v{index}]"
            f"xfade=transition=fade:duration={transition}:"
            f"offset={offset:.3f}[{output_label}]"
        )

        last_label = output_label
        current_duration += clip_duration - transition

    filter_graph = ";".join(filter_parts)

    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", filter_graph,
            "-map", f"[{last_label}]",
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


def mux_audio(video_path, voice_path, ambient_path, destination):
    """
    Narration stays clear; background ambience remains quieter.
    """
    run_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video_path),
            "-i", str(voice_path),
            "-i", str(ambient_path),
            "-filter_complex",
            (
                "[1:a]aresample=44100,"
                "volume=1.0[voice];"
                "[2:a]aresample=44100,"
                "volume=0.14[bed];"
                "[bed][voice]sidechaincompress="
                "threshold=0.03:ratio=8:attack=20:release=500[duck];"
                "[voice][duck]amix=inputs=2:duration=first:"
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
        timeout=150,
    )

    return destination


# =========================================================
# VIDEO JOB
# =========================================================

def build_video(job_id, story, output_path):
    global ACTIVE_JOBS

    work_dir = Path(tempfile.mkdtemp(prefix=f"zil_{job_id}_"))

    try:
        with ACTIVE_LOCK:
            ACTIVE_JOBS += 1

        require_tools()

        save_job(
            job_id,
            status="running",
            stage="initializing",
            story=story[:500],
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        queries = make_search_queries(story)

        scene_paths = []
        caption_texts = [
            story[:180],
            "لكن الحقيقة لم تكن كما تبدو.",
            "بدأ الخطر يقترب.",
            "قوة غامضة ظهرت فجأة.",
            "لم يتوقع أحد ما سيحدث.",
            "والسر الحقيقي لم يُكشف بعد...",
        ]

        for index in range(SCENE_COUNT):
            stage = f"scene_{index + 1}_download"

            save_job(
                job_id,
                stage=stage,
                progress=f"{index + 1}/{SCENE_COUNT}",
            )

            query = queries[index]

            try:
                url = search_pixabay_video(query)
            except Exception:
                logger.exception(
                    "Pixabay search failed for scene %s; using fallback query",
                    index + 1,
                )
                url = search_pixabay_video("cinematic dramatic landscape")

            source_path = work_dir / f"source_{index + 1}.mp4"
            download_video(url, source_path)

            caption_path = work_dir / f"caption_{index + 1}.png"
            make_caption_image(
                caption_texts[index],
                caption_path,
            )

            scene_output = work_dir / f"scene_{index + 1}.mp4"

            save_job(
                job_id,
                stage=f"scene_{index + 1}_render",
            )

            render_scene(
                source_path,
                caption_path,
                scene_output,
            )

            scene_paths.append(scene_output)

        save_job(job_id, stage="concatenating_scenes")

        joined_video = work_dir / "joined.mp4"
        concatenate_scenes(
            scene_paths,
            joined_video,
        )

        save_job(job_id, stage="creating_narration")

        narration_text = (
            story.strip()
            or "في عالم مليء بالأسرار، بدأت حكاية لم يتوقع أحد نهايتها."
        )

        voice_path = work_dir / "narration.mp3"
        create_narration(
            narration_text[:2500],
            voice_path,
        )

        save_job(job_id, stage="creating_ambient_audio")

        ambient_path = work_dir / "ambient.m4a"
        create_ambient_audio(ambient_path)

        save_job(job_id, stage="mixing_audio")

        mux_audio(
            joined_video,
            voice_path,
            ambient_path,
            output_path,
        )

        save_job(job_id, stage="validating_output")

        validation = validate_video(output_path)

        save_job(
            job_id,
            status="completed",
            stage="completed",
            output=str(output_path),
            validation=validation,
            completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        logger.info(
            "JOB COMPLETED | id=%s | validation=%s",
            job_id,
            validation,
        )

        return str(output_path)

    except Exception as exc:
        current = get_job(job_id)
        stage = current.get("stage", "unknown")

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

        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            logger.exception("Could not clean temporary job directory")


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 أهلاً بك في ظل ZIL.\n\n"
        "أرسل فكرة القصة لبدء إنتاج فيديو عمودي.\n\n"
        "/test — تجربة النظام\n"
        "/status — حالة النظام والعمليات\n"
        "/diagnose — فحص الأدوات والإعدادات\n"
        "/last_error — آخر خطأ مسجل"
    )


async def diagnose_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    checks = check_environment()

    lines = ["🔍 تقرير فحص ظل ZIL", ""]

    for name, value in checks.items():
        lines.append(
            f"{'✅' if value else '❌'} {name}: "
            f"{'جاهز' if value else 'غير جاهز'}"
        )

    with ACTIVE_LOCK:
        active_count = ACTIVE_JOBS

    lines.extend([
        "",
        f"العمليات النشطة: {active_count}",
        f"المشاهد: {SCENE_COUNT}",
        f"المدة المستهدفة: {TOTAL_SECONDS} ثانية",
        f"المقاس: {VIDEO_WIDTH}×{VIDEO_HEIGHT}",
    ])

    await update.message.reply_text("\n".join(lines))

    if not all(checks.get(k) for k in ("ffmpeg", "ffprobe", "BOT_TOKEN")):
        notify_admin("⚠️ ZIL — فشل فحص النظام\n\n" + "\n".join(lines))


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    with ACTIVE_LOCK:
        active_count = ACTIVE_JOBS

    with JOBS_LOCK:
        jobs = list(JOBS.items())[-5:]

    lines = [
        "📊 حالة ظل ZIL",
        "",
        f"العمليات النشطة: {active_count}",
        f"Pixabay: {'جاهز' if PIXABAY_API_KEY else 'المفتاح مفقود'}",
        f"FFmpeg: {'جاهز' if shutil.which('ffmpeg') else 'غير موجود'}",
        "",
        "آخر العمليات:",
    ]

    if not jobs:
        lines.append("لا توجد عمليات مسجلة.")
    else:
        for job_id, info in reversed(jobs):
            lines.append(
                f"\n{job_id}\n"
                f"الحالة: {info.get('status', 'unknown')}\n"
                f"المرحلة: {info.get('stage', 'unknown')}\n"
                f"الخطأ: {info.get('error', 'لا يوجد')[:180]}"
            )

    await update.message.reply_text("\n".join(lines)[:3900])


async def last_error_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    with JOBS_LOCK:
        failed = [
            (job_id, info)
            for job_id, info in JOBS.items()
            if info.get("status") == "failed"
        ]

    if not failed:
        await update.message.reply_text(
            "لا يوجد خطأ مسجل في ذاكرة التشغيل الحالية."
        )
        return

    job_id, info = failed[-1]

    await update.message.reply_text(
        "🚨 آخر خطأ\n\n"
        f"رقم العملية: {job_id}\n"
        f"المرحلة: {info.get('stage', 'unknown')}\n"
        f"الخطأ: {info.get('error', 'unknown')[:2500]}"
    )


async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await launch_job(
        update,
        "اختبار سينمائي: بطل غامض يدخل قلعة قديمة، "
        "وتظهر قوة خفية بينما يقترب خطر مجهول. "
        "تنتهي اللقطة باكتشاف سر جديد.",
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
            "أرسل قصة أطول قليلاً حتى أستطيع تجهيز الفيديو."
        )
        return

    if story.startswith("/"):
        return

    await launch_job(update, story)


async def launch_job(update, story):
    global ACTIVE_JOBS

    with ACTIVE_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            await update.message.reply_text(
                "هناك عملية إنتاج جارية حالياً. انتظر حتى تنتهي."
            )
            return

    job_id = uuid.uuid4().hex[:10]
    output_path = OUTPUT_DIR / f"zil_{job_id}.mp4"

    save_job(
        job_id,
        status="queued",
        stage="queued",
        story=story[:500],
        user_id=update.effective_user.id if update.effective_user else None,
    )

    await update.message.reply_text(
        "🎬 بدأت صناعة فيديو ظل.\n"
        f"رقم العملية: {job_id}\n\n"
        "6 مشاهد، فيديو عمودي، راوي عربي وخلفية صوتية.\n\n"
        "استخدم /status لمتابعة العملية."
    )

    executor.submit(
        background_job,
        job_id,
        story,
        str(output_path),
        update.effective_chat.id,
    )


def background_job(job_id, story, output_path, chat_id):
    try:
        result_path = build_video(
            job_id,
            story,
            output_path,
        )

        asyncio.run(
            send_video_to_user(
                chat_id,
                job_id,
                result_path,
            )
        )

    except Exception:
        logger.exception(
            "BACKGROUND JOB ERROR | id=%s",
            job_id,
        )

        # build_video already sends the diagnostic report.
        # This catches failures occurring after video generation too.
        current = get_job(job_id)

        if current.get("status") != "failed":
            report_error(
                job_id,
                "background_job",
                "فشل إرسال الفيديو أو تنفيذ المهمة الخلفية.",
                traceback.format_exc(),
            )


async def send_video_to_user(chat_id, job_id, video_path):
    from telegram import Bot

    bot = Bot(token=BOT_TOKEN)

    try:
        async with bot:
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
            Path(video_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("Could not delete output video")


# =========================================================
# FLASK HEALTH ENDPOINTS
# =========================================================

@app.get("/")
def home():
    return "ZIL is running", 200


@app.get("/health")
def health():
    checks = check_environment()

    return jsonify({
        "service": "ZIL",
        "status": "ok" if checks.get("ffmpeg") and checks.get("ffprobe") else "degraded",
        "checks": checks,
        "active_jobs": ACTIVE_JOBS,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }), 200


@app.get("/jobs")
def jobs_endpoint():
    with JOBS_LOCK:
        return jsonify({
            "jobs": JOBS,
            "active_jobs": ACTIVE_JOBS,
        })


# =========================================================
# GLOBAL TELEGRAM ERROR HANDLER
# =========================================================

async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    error = context.error

    logger.error(
        "TELEGRAM HANDLER ERROR",
        exc_info=(
            type(error),
            error,
            error.__traceback__,
        ) if error else None,
    )

    report = (
        "🚨 ZIL — خطأ في معالج تيليجرام\n\n"
        f"نوع الخطأ: {type(error).__name__ if error else 'Unknown'}\n"
        f"الرسالة: {str(error) if error else 'No details'}\n\n"
        "التفاصيل:\n"
        f"{traceback.format_exc()[-1800:]}"
    )

    notify_admin(report)


# =========================================================
# STARTUP
# =========================================================

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
            "ADMIN_CHAT_ID is not configured. "
            "Automatic admin reports will not be delivered."
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

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("diagnose", diagnose_command))
    application.add_handler(CommandHandler("last_error", last_error_command))
    application.add_handler(CommandHandler("test", test_command))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            story_command,
        )
    )

    application.add_error_handler(telegram_error_handler)

    logger.info("ZIL starting")
    logger.info("Health endpoint: /health")
    logger.info("Admin diagnostics: %s", bool(ADMIN_CHAT_ID))

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
