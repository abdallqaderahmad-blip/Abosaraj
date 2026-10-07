import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import traceback
import threading
import subprocess
import time
import gc
from pathlib import Path
from urllib.parse import quote

import requests
import edge_tts
from groq import Groq
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

POLLINATIONS_API_KEY = (
    os.getenv("POLLINATIONS_API_KEY", "").strip()
    or os.getenv("POLLINATIONS_KEY", "").strip()
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

# Direct AI video model
POLLINATIONS_VIDEO_MODEL = os.getenv(
    "POLLINATIONS_VIDEO_MODEL",
    "alibaba/wan-2.2-fast"
)

# Number of cinematic shots
SHOT_COUNT = 8

# AI video duration per shot
SHOT_DURATION = 5

# Final Reel
FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# Arabic voice
TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
)

BASE_DIR = Path("/tmp/abosaraj")
BASE_DIR.mkdir(parents=True, exist_ok=True)

JOB_LOCK = threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ABOSARAJ")


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "ABOSARAJ BOT ONLINE", 200


@flask_app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "online",
        "video_engine": "pollinations"
    }, 200


def run_flask():
    port = int(os.getenv("PORT", "10000"))

    flask_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# ============================================================
# COMMAND RUNNER
# ============================================================

def run_command(cmd, timeout=900):
    log.info("RUN_COMMAND=%s", " ".join(map(str, cmd)))

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Command timed out after {timeout} seconds"
        )

    if result.returncode != 0:
        log.error(
            "COMMAND_EXIT=%s",
            result.returncode
        )

        log.error(
            "COMMAND_STDERR=%s",
            result.stderr[-6000:]
        )

        raise RuntimeError(
            f"Command failed: {result.returncode}\n"
            f"{result.stderr[-3000:]}"
        )

    return result


# ============================================================
# GROQ DIRECTOR
# ============================================================

def clean_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"```$",
            "",
            text
        )

    return text.strip()


def create_storyboard(story):
    client = Groq(api_key=GROQ_API_KEY)

    system_prompt = f"""
You are a professional cinematic director for TikTok, Instagram Reels and YouTube Shorts.

Convert the Arabic story into exactly {SHOT_COUNT} cinematic AI VIDEO shots.

IMPORTANT:

- This is NOT a slideshow.
- Every shot must describe real physical movement.
- Every shot must be visually filmable.
- Characters must remain visually consistent.
- Avoid impossible camera movement.
- Avoid text appearing inside the generated video.
- Avoid subtitles inside the generated video.
- Avoid logos.
- Avoid changing character identity between shots.
- Each shot is approximately {SHOT_DURATION} seconds.

Return ONLY valid JSON.

Format:

{{
  "title": "short title",
  "narration": "short Arabic narration covering the whole story",
  "shots": [
    {{
      "id": 1,
      "visual_prompt": "English cinematic video prompt",
      "duration": {SHOT_DURATION}
    }}
  ]
}}

The visual_prompt must be in English because the video model performs better with English prompts.

Make the prompts highly cinematic:

- realistic humans
- realistic environments
- natural movement
- cinematic lighting
- camera movement
- depth of field
- realistic physics
- dramatic composition
- vertical social media framing
- 9:16 composition

The same character must keep the same:
hair
clothes
age
face
body
visual identity

Story:
{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.7,
        max_tokens=7000,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": story
            }
        ]
    )

    raw = response.choices[0].message.content

    log.info(
        "GROQ_RESPONSE_LENGTH=%s",
        len(raw or "")
    )

    data = json.loads(clean_json(raw))

    if not isinstance(data, dict):
        raise RuntimeError(
            "Groq returned invalid storyboard."
        )

    shots = data.get("shots")

    if not isinstance(shots, list):
        raise RuntimeError(
            "Storyboard does not contain shots."
        )

    if len(shots) < SHOT_COUNT:
        raise RuntimeError(
            f"Groq returned only {len(shots)} shots."
        )

    data["shots"] = shots[:SHOT_COUNT]

    narration = data.get("narration", "").strip()

    if not narration:
        narration = story

    data["narration"] = narration

    return data


# ============================================================
# POLLINATIONS VIDEO GENERATOR
# ============================================================

def generate_ai_video(prompt, output_path, duration=SHOT_DURATION):
    if not POLLINATIONS_API_KEY:
        raise RuntimeError(
            "POLLINATIONS_API_KEY is missing. "
            "Add it to Render Environment Variables."
        )

    # Extra quality instructions
    final_prompt = f"""
{prompt}

Professional cinematic live-action video.

Vertical 9:16 composition.

Realistic human motion.

Natural body movement.

Realistic physics.

Cinematic lighting.

Shallow depth of field.

Film-quality camera movement.

High detail.

No text.

No subtitles.

No watermark.

No logo.

No distorted hands.

No duplicated people.

No morphing faces.

Maintain consistent character appearance.
"""

    encoded_prompt = quote(
        final_prompt.strip(),
        safe=""
    )

    url = (
        "https://gen.pollinations.ai/video/"
        + encoded_prompt
    )

    params = {
        "model": POLLINATIONS_VIDEO_MODEL,
        "duration": str(duration)
    }

    headers = {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
        "Accept": "video/mp4"
    }

    last_error = None

    for attempt in range(1, 4):

        log.info(
            "VIDEO_ATTEMPT=%s MODEL=%s",
            attempt,
            POLLINATIONS_VIDEO_MODEL
        )

        try:

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=600,
                stream=True
            )

            log.info(
                "VIDEO_HTTP_STATUS=%s",
                response.status_code
            )

            content_type = response.headers.get(
                "content-type",
                ""
            )

            log.info(
                "VIDEO_CONTENT_TYPE=%s",
                content_type
            )

            if response.status_code == 401:
                raise RuntimeError(
                    "Pollinations authentication failed (401). "
                    "Check POLLINATIONS_API_KEY."
                )

            if response.status_code == 402:
                raise RuntimeError(
                    "Pollinations returned 402. "
                    "The account does not have enough Pollen/budget "
                    "for this video generation."
                )

            if response.status_code == 403:
                raise RuntimeError(
                    "Pollinations rejected the request (403)."
                )

            if response.status_code == 429:
                raise RuntimeError(
                    "Pollinations rate limit reached (429)."
                )

            if response.status_code >= 500:
                raise RuntimeError(
                    f"Pollinations server error: "
                    f"{response.status_code}"
                )

            if response.status_code != 200:
                body = response.text[:1000]

                raise RuntimeError(
                    f"Pollinations video failed: "
                    f"HTTP {response.status_code} "
                    f"{body}"
                )

            with open(output_path, "wb") as f:

                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):

                    if chunk:
                        f.write(chunk)

            size = output_path.stat().st_size

            log.info(
                "VIDEO_FILE_SIZE=%s",
                size
            )

            if size < 50_000:
                raise RuntimeError(
                    "Generated video file is suspiciously small."
                )

            log.info(
                "VIDEO_SUCCESS=%s",
                output_path
            )

            return output_path

        except Exception as e:

            last_error = e

            log.error(
                "VIDEO_ATTEMPT_FAILED=%s",
                str(e)
            )

            if attempt < 3:
                time.sleep(4 * attempt)

    raise RuntimeError(
        f"Pollinations video generation failed: "
        f"{last_error}"
    )


# ============================================================
# TTS
# ============================================================

async def create_tts_async(text, output_path):
    communicate = edge_tts.Communicate(
        text,
        TTS_VOICE
    )

    await communicate.save(str(output_path))


def create_tts(text, output_path):
    asyncio.run(
        create_tts_async(
            text,
            output_path
        )
    )

    if not output_path.exists():
        raise RuntimeError(
            "TTS file was not created."
        )

    if output_path.stat().st_size < 1000:
        raise RuntimeError(
            "TTS file is too small."
        )


# ============================================================
# NORMALIZE VIDEO
# ============================================================

def normalize_video(input_path, output_path):
    """
    Normalize every generated AI clip into exactly:
    720x1280
    30fps
    H264
    yuv420p

    This avoids the previous zoompan/libx264 failure.
    """

    vf = (
        "scale=720:1280:"
        "force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,"
        "fps=30,"
        "format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_path),

        "-vf",
        vf,

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "24",

        "-pix_fmt",
        "yuv420p",

        "-movflags",
        "+faststart",

        str(output_path)
    ]

    run_command(cmd, timeout=300)

    if not output_path.exists():
        raise RuntimeError(
            "Normalized video was not created."
        )


# ============================================================
# CONCAT VIDEOS
# ============================================================

def concat_videos(video_files, output_path, work_dir):

    concat_file = work_dir / "videos.txt"

    with open(concat_file, "w", encoding="utf-8") as f:

        for video in video_files:

            safe_path = str(video).replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    cmd = [
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(concat_file),

        "-c",
        "copy",

        str(output_path)
    ]

    run_command(cmd, timeout=600)

    if not output_path.exists():
        raise RuntimeError(
            "Concatenated video was not created."
        )


# ============================================================
# ADD AUDIO
# ============================================================

def add_audio(video_path, audio_path, output_path):

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        str(video_path),

        "-i",
        str(audio_path),

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-shortest",

        "-movflags",
        "+faststart",

        str(output_path)
    ]

    run_command(cmd, timeout=600)

    if not output_path.exists():
        raise RuntimeError(
            "Final video was not created."
        )


# ============================================================
# MAIN VIDEO PIPELINE
# ============================================================

def build_reel(story, job_dir):

    log.info("========================================")
    log.info("BUILD_REEL_START")
    log.info("========================================")

    # ----------------------------------------
    # 1. STORYBOARD
    # ----------------------------------------

    storyboard = create_storyboard(story)

    narration = storyboard["narration"]

    shots = storyboard["shots"]

    log.info(
        "STORYBOARD_READY shots=%s",
        len(shots)
    )

    with open(
        job_dir / "storyboard.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            storyboard,
            f,
            ensure_ascii=False,
            indent=2
        )

    # ----------------------------------------
    # 2. TTS
    # ----------------------------------------

    log.info("TTS_START")

    audio_path = job_dir / "narration.mp3"

    create_tts(
        narration,
        audio_path
    )

    log.info("TTS_READY")

    # ----------------------------------------
    # 3. AI VIDEOS
    # ----------------------------------------

    normalized_videos = []

    for index, shot in enumerate(
        shots,
        start=1
    ):

        log.info(
            "========================================"
        )

        log.info(
            "SCENE %s/%s",
            index,
            len(shots)
        )

        prompt = shot.get(
            "visual_prompt",
            ""
        ).strip()

        if not prompt:
            raise RuntimeError(
                f"Scene {index} has empty visual prompt."
            )

        raw_video = (
            job_dir /
            f"scene_{index}_raw.mp4"
        )

        clean_video = (
            job_dir /
            f"scene_{index}.mp4"
        )

        # Generate actual AI motion video
        generate_ai_video(
            prompt,
            raw_video,
            duration=SHOT_DURATION
        )

        # Normalize safely
        normalize_video(
            raw_video,
            clean_video
        )

        normalized_videos.append(
            clean_video
        )

        # Delete raw huge video immediately
        try:
            raw_video.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        gc.collect()

    # ----------------------------------------
    # 4. CONCAT
    # ----------------------------------------

    log.info("CONCAT_START")

    joined_video = (
        job_dir /
        "joined.mp4"
    )

    concat_videos(
        normalized_videos,
        joined_video,
        job_dir
    )

    # ----------------------------------------
    # 5. AUDIO
    # ----------------------------------------

    final_video = (
        job_dir /
        "final_reel.mp4"
    )

    log.info("AUDIO_MUX_START")

    add_audio(
        joined_video,
        audio_path,
        final_video
    )

    # ----------------------------------------
    # 6. CHECK
    # ----------------------------------------

    if not final_video.exists():
        raise RuntimeError(
            "Final Reel does not exist."
        )

    final_size = final_video.stat().st_size

    log.info(
        "FINAL_VIDEO_SIZE=%s",
        final_size
    )

    if final_size < 100_000:
        raise RuntimeError(
            "Final Reel is suspiciously small."
        )

    log.info("========================================")
    log.info("BUILD_REEL_SUCCESS")
    log.info("========================================")

    return final_video


# ============================================================
# TELEGRAM HANDLER
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً!\n\n"
        "ابعتلي قصة وأنا أحولها إلى Reel."
    )


async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    story = (
        update.message.text or ""
    ).strip()

    if not story:
        return

    if len(story) < 30:

        await update.message.reply_text(
            "✍️ ابعت قصة أطول شوي حتى أقدر "
            "أبني عليها فيلم."
        )

        return

    # ----------------------------------------
    # ONE JOB AT A TIME
    # ----------------------------------------

    if not JOB_LOCK.acquire(
        blocking=False
    ):

        await update.message.reply_text(
            "⏳ في Reel ثاني قيد الإنشاء الآن.\n"
            "استنى يخلص وبعدها ابعت القصة."
        )

        return

    job_id = uuid.uuid4().hex[:12]

    job_dir = (
        BASE_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        log.info(
            "JOB_START=%s",
            job_id
        )

        await update.message.reply_text(
            "📝 استلمت القصة.\n\n"
            "🎬 جاري تحويلها إلى فيلم قصير..."
        )

        # Build Reel in background thread
        final_video = await asyncio.to_thread(
            build_reel,
            story,
            job_dir
        )

        await update.message.reply_text(
            "🚀 خلص الفيلم! جاري إرساله..."
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                supports_streaming=True,
                caption=(
                    "🎬 تم إنشاء الـ Reel بنجاح"
                )
            )

        log.info(
            "JOB_SUCCESS=%s",
            job_id
        )

    except Exception as e:

        log.error(
            "JOB_FAILED=%s",
            job_id
        )

        log.error(
            "ERROR_TYPE=%s",
            type(e).__name__
        )

        log.error(
            "ERROR_MESSAGE=%s",
            str(e)
        )

        log.error(
            traceback.format_exc()
        )

        # Don't expose giant traceback to Telegram
        message = str(e)

        if len(message) > 1500:
            message = message[:1500]

        try:

            await update.message.reply_text(
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                f"{message}"
            )

        except Exception:
            pass

    finally:

        # ----------------------------------------
        # CLEAN TEMP FILES
        # ----------------------------------------

        try:

            shutil.rmtree(
                job_dir,
                ignore_errors=True
            )

        except Exception:
            pass

        gc.collect()

        JOB_LOCK.release()

        log.info(
            "JOB_CLEANUP=%s",
            job_id
        )


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def telegram_error_handler(
    update,
    context
):

    log.error(
        "TELEGRAM_HANDLER_ERROR=%s",
        context.error
    )

    log.error(
        traceback.format_exc()
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log.info("========================================")
    log.info("ABOSARAJ STARTING")
    log.info("========================================")

    # ----------------------------------------
    # Validate environment
    # ----------------------------------------

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing."
        )

    if not POLLINATIONS_API_KEY:
        raise RuntimeError(
            "POLLINATIONS_API_KEY is missing."
        )

    log.info(
        "POLLINATIONS_KEY_PRESENT=True"
    )

    log.info(
        "VIDEO_MODEL=%s",
        POLLINATIONS_VIDEO_MODEL
    )

    log.info(
        "SHOT_COUNT=%s",
        SHOT_COUNT
    )

    # ----------------------------------------
    # Health server
    # ----------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    log.info(
        "HEALTH_SERVER_STARTED"
    )

    # ----------------------------------------
    # Telegram
    # ----------------------------------------

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story
        )
    )

    app.add_error_handler(
        telegram_error_handler
    )

    log.info(
        "TELEGRAM_HANDLERS_READY"
    )

    log.info(
        "BOT_START_POLLING"
    )

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log.error(
            "FATAL_ERROR=%s",
            str(e)
        )

        log.error(
            traceback.format_exc()
        )

        raise
