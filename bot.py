import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests
import edge_tts

from flask import Flask
from groq import Groq

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b"
)

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY"
)

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux"
)


# =========================================================
# VIDEO SETTINGS
# =========================================================

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# اختبار أولي
SCENE_COUNT = 2

MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 120

VOICE = "ar-SA-HamedNeural"


# =========================================================
# WORK DIRECTORY
# =========================================================

WORK_DIR = Path("/tmp/abosaraj")

WORK_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# THREADING
# =========================================================

executor = ThreadPoolExecutor(
    max_workers=1
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Story Video Engine is alive"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "version": "story-pollinations-v3",
        "video_engine": "Pollinations Image + FFmpeg",
        "scenes": SCENE_COUNT,
        "width": FINAL_WIDTH,
        "height": FINAL_HEIGHT
    }


def run_flask():
    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# =========================================================
# VALIDATION
# =========================================================

def validate_environment():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    if not POLLINATIONS_API_KEY:
        missing.append("POLLINATIONS_API_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    logger.info("Environment variables OK")


# =========================================================
# GROQ CLIENT
# =========================================================

groq_client = None


def get_groq_client():

    global groq_client

    if groq_client is None:
        groq_client = Groq(
            api_key=GROQ_API_KEY
        )

    return groq_client


# =========================================================
# JSON CLEANING
# =========================================================

def clean_json_text(text):

    if not text:
        return ""

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

    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        text = text[start:end + 1]

    return text


# =========================================================
# STORY PLAN VALIDATION
# =========================================================

def validate_story_plan(data):

    if not isinstance(data, dict):
        return False

    title = data.get("title")
    narration = data.get("narration")
    scenes = data.get("scenes")

    if not isinstance(title, str):
        return False

    if not title.strip():
        return False

    if not isinstance(narration, str):
        return False

    if len(narration.strip()) < 50:
        return False

    if not isinstance(scenes, list):
        return False

    if len(scenes) < SCENE_COUNT:
        return False

    for scene in scenes:

        if not isinstance(scene, dict):
            return False

        if not isinstance(
            scene.get("description"),
            str
        ):
            return False

        if not scene["description"].strip():
            return False

    return True


# =========================================================
# GROQ STORY GENERATION
# =========================================================

def generate_story_plan(user_story):

    client = get_groq_client()

    system_prompt = f"""
You are a professional Arabic short-video storyteller.

Turn the user's story into a cinematic vertical-video plan.

IMPORTANT:

Return ONLY valid JSON.

The JSON must have exactly this structure:

{{
  "title": "short Arabic title",
  "narration": "complete Arabic narration",
  "scenes": [
    {{
      "description": "cinematic visual description"
    }}
  ]
}}

Rules:

- Arabic narration.
- Male narrator style.
- Strong hook at the beginning.
- Natural storytelling.
- Do not invent unnecessary facts.
- Keep the story understandable.
- Narration should normally fit a 60-120 second video.
- Create exactly {SCENE_COUNT} scenes.
- Each scene must visually represent an important moment.
- Scene descriptions should be suitable for an AI image generator.
- Do not put dialogue formatting in the scene descriptions.
- Do not use Markdown.
"""

    user_prompt = f"""
User story:

{user_story}
"""

    logger.info("Generating story plan with Groq...")

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        max_tokens=4000,
        response_format={
            "type": "json_object"
        },
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    )

    raw = response.choices[0].message.content

    cleaned = clean_json_text(raw)

    try:
        data = json.loads(cleaned)
    except Exception:

        logger.warning(
            "First JSON parse failed. Trying repair..."
        )

        repair_prompt = f"""
Fix the following response and return ONLY valid JSON.

Required structure:

{{
  "title": "Arabic title",
  "narration": "Arabic narration",
  "scenes": [
    {{
      "description": "visual description"
    }}
  ]
}}

The number of scenes MUST be exactly {SCENE_COUNT}.

Broken response:

{raw}
"""

        repair = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0,
            max_tokens=4000,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "user",
                    "content": repair_prompt
                }
            ]
        )

        repaired = repair.choices[0].message.content

        data = json.loads(
            clean_json_text(repaired)
        )

    if not validate_story_plan(data):

        raise RuntimeError(
            "Groq returned an invalid story plan"
        )

    data["scenes"] = data["scenes"][:SCENE_COUNT]

    logger.info(
        "Story plan ready: %s scenes",
        len(data["scenes"])
    )

    return data


# =========================================================
# POLLINATIONS IMAGE GENERATION
# =========================================================

def generate_image(
    prompt,
    output_path,
    seed=None
):

    if seed is None:
        seed = int(
            uuid.uuid4().int % 2147483647
        )

    encoded_prompt = requests.utils.quote(
        prompt,
        safe=""
    )

    url = (
        "https://gen.pollinations.ai/image/"
        + encoded_prompt
    )

    params = {
        "model": POLLINATIONS_MODEL,
        "width": FINAL_WIDTH,
        "height": FINAL_HEIGHT,
        "seed": seed,
        "nologo": "true"
    }

    headers = {
        "Authorization":
            f"Bearer {POLLINATIONS_API_KEY}"
    }

    logger.info(
        "Generating Pollinations image..."
    )

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=180
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Pollinations error "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    if not response.content:
        raise RuntimeError(
            "Pollinations returned empty image"
        )

    output_path.write_bytes(
        response.content
    )

    logger.info(
        "Image saved: %s",
        output_path
    )

    return output_path


# =========================================================
# IMAGE PROMPT
# =========================================================

def build_image_prompt(
    scene_description
):

    return f"""
Create a cinematic vertical 9:16 image for a
professional Arabic storytelling video.

Scene:

{scene_description}

Visual requirements:

- photorealistic cinematic style
- dramatic storytelling composition
- realistic people and environment
- natural lighting
- strong depth
- detailed background
- emotionally expressive
- high quality
- no text
- no subtitles
- no watermark
- no logo
- no UI
- vertical composition
- important subject clearly visible
"""


# =========================================================
# TTS
# =========================================================

async def generate_voice_async(
    narration,
    output_path
):

    logger.info(
        "Generating Arabic male narration..."
    )

    communicate = edge_tts.Communicate(
        narration,
        VOICE
    )

    await communicate.save(
        str(output_path)
    )

    logger.info(
        "Voice saved: %s",
        output_path
    )


def generate_voice(
    narration,
    output_path
):

    asyncio.run(
        generate_voice_async(
            narration,
            output_path
        )
    )

    return output_path


# =========================================================
# AUDIO DURATION
# =========================================================

def get_media_duration(
    file_path
):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "ffprobe failed: "
            + result.stderr
        )

    return float(
        result.stdout.strip()
    )


# =========================================================
# CREATE SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    output_path,
    duration,
    index
):

    frames = max(
        int(duration * FPS),
        FPS
    )

    zoom_start = 1.0
    zoom_end = 1.10

    if index % 2 == 1:
        zoom_start = 1.10
        zoom_end = 1.0

    zoom_expression = (
        f"zoom='"
        f"{zoom_start}+"
        f"({zoom_end}-{zoom_start})*"
        f"on/{frames}'"
    )

    filter_complex = (
        f"scale="
        f"{FINAL_WIDTH}:"
        f"{FINAL_HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop="
        f"{FINAL_WIDTH}:"
        f"{FINAL_HEIGHT},"
        f"zoompan="
        f"{zoom_expression}:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d=1:"
        f"s={FINAL_WIDTH}x{FINAL_HEIGHT}:"
        f"fps={FPS}"
    )

    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        filter_complex,
        "-t",
        str(duration),
        "-r",
        str(FPS),
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path)
    ]

    logger.info(
        "Creating scene video %s...",
        index + 1
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-3000:]
        )

        raise RuntimeError(
            "FFmpeg scene creation failed"
        )

    return output_path


# =========================================================
# CONCATENATE SCENES
# =========================================================

def concat_scene_videos(
    scene_files,
    output_path
):

    list_file = output_path.parent / (
        f"concat_{uuid.uuid4().hex}.txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for scene_file in scene_files:

            safe_path = (
                str(scene_file)
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path)
    ]

    logger.info(
        "Joining scene videos..."
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    try:
        list_file.unlink()
    except Exception:
        pass

    if result.returncode != 0:

        logger.error(
            result.stderr[-3000:]
        )

        raise RuntimeError(
            "FFmpeg concat failed"
        )

    return output_path


# =========================================================
# ADD AUDIO
# =========================================================

def add_audio(
    video_path,
    audio_path,
    output_path
):

    command = [
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
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path)
    ]

    logger.info(
        "Adding narration audio..."
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-3000:]
        )

        raise RuntimeError(
            "FFmpeg audio merge failed"
        )

    return output_path


# =========================================================
# CAPTIONS
# =========================================================

def escape_ass_text(text):

    text = text.replace(
        "\\",
        "\\\\"
    )

    text = text.replace(
        "\n",
        " "
    )

    text = text.replace(
        "{",
        "\\{"
    )

    text = text.replace(
        "}",
        "\\}"
    )

    return text


def create_subtitle_file(
    narration,
    output_path,
    duration
):

    words = narration.split()

    if not words:
        return None

    chunks = []

    chunk_size = 7

    for i in range(
        0,
        len(words),
        chunk_size
    ):
        chunks.append(
            " ".join(
                words[i:i + chunk_size]
            )
        )

    total = len(chunks)

    if total == 0:
        return None

    per_chunk = duration / total

    def ass_time(seconds):

        hours = int(seconds // 3600)

        minutes = int(
            (seconds % 3600) // 60
        )

        secs = (
            seconds
            - hours * 3600
            - minutes * 60
        )

        centiseconds = int(
            round(
                (secs - int(secs)) * 100
            )
        )

        whole_seconds = int(secs)

        if centiseconds >= 100:
            whole_seconds += 1
            centiseconds = 0

        return (
            f"{hours}:"
            f"{minutes:02d}:"
            f"{whole_seconds:02d}."
            f"{centiseconds:02d}"
        )

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 720\n"
            "PlayResY: 1280\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, "
            "PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,42,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,"
            "&H80000000,1,0,0,0,100,100,0,0,"
            "1,3,1,2,40,40,120,1\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, "
            "Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        for i, chunk in enumerate(chunks):

            start = i * per_chunk
            end = min(
                (i + 1) * per_chunk,
                duration
            )

            safe_chunk = escape_ass_text(
                chunk
            )

            f.write(
                "Dialogue: 0,"
                f"{ass_time(start)},"
                f"{ass_time(end)},"
                f"Default,,0,0,0,,"
                f"{safe_chunk}\n"
            )

    return output_path


# =========================================================
# ADD CAPTIONS
# =========================================================

def add_captions(
    video_path,
    subtitle_path,
    output_path
):

    subtitle_filter = (
        "subtitles="
        + str(subtitle_path)
        .replace("\\", "/")
        .replace(":", "\\:")
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path)
    ]

    logger.info(
        "Adding Arabic captions..."
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-3000:]
        )

        raise RuntimeError(
            "FFmpeg captions failed"
        )

    return output_path


# =========================================================
# FULL VIDEO PIPELINE
# =========================================================

def build_video(
    story,
    job_dir
):

    logger.info(
        "Starting video pipeline..."
    )

    # -----------------------------------------------------
    # 1. STORY PLAN
    # -----------------------------------------------------

    plan = generate_story_plan(
        story
    )

    title = plan["title"]
    narration = plan["narration"]
    scenes = plan["scenes"]

    logger.info(
        "Title: %s",
        title
    )

    logger.info(
        "Narration length: %s chars",
        len(narration)
    )

    # -----------------------------------------------------
    # 2. VOICE
    # -----------------------------------------------------

    audio_path = (
        job_dir / "narration.mp3"
    )

    generate_voice(
        narration,
        audio_path
    )

    audio_duration = get_media_duration(
        audio_path
    )

    audio_duration = max(
        MIN_VIDEO_SECONDS,
        min(
            audio_duration,
            MAX_VIDEO_SECONDS
        )
    )

    logger.info(
        "Target video duration: %.2f sec",
        audio_duration
    )

    # -----------------------------------------------------
    # 3. IMAGES
    # -----------------------------------------------------

    image_files = []

    for i, scene in enumerate(scenes):

        image_path = (
            job_dir /
            f"scene_{i + 1}.jpg"
        )

        prompt = build_image_prompt(
            scene["description"]
        )

        generate_image(
            prompt,
            image_path,
            seed=i + 1000
        )

        image_files.append(
            image_path
        )

    # -----------------------------------------------------
    # 4. SCENE DURATION
    # -----------------------------------------------------

    scene_duration = (
        audio_duration /
        len(image_files)
    )

    # -----------------------------------------------------
    # 5. CREATE SCENE VIDEOS
    # -----------------------------------------------------

    scene_videos = []

    for i, image_file in enumerate(
        image_files
    ):

        scene_video = (
            job_dir /
            f"scene_{i + 1}.mp4"
        )

        create_scene_video(
            image_file,
            scene_video,
            scene_duration,
            i
        )

        scene_videos.append(
            scene_video
        )

    # -----------------------------------------------------
    # 6. CONCAT
    # -----------------------------------------------------

    silent_video = (
        job_dir /
        "silent_video.mp4"
    )

    concat_scene_videos(
        scene_videos,
        silent_video
    )

    # -----------------------------------------------------
    # 7. AUDIO
    # -----------------------------------------------------

    narrated_video = (
        job_dir /
        "narrated_video.mp4"
    )

    add_audio(
        silent_video,
        audio_path,
        narrated_video
    )

    # -----------------------------------------------------
    # 8. SUBTITLES
    # -----------------------------------------------------

    subtitle_path = (
        job_dir /
        "captions.ass"
    )

    create_subtitle_file(
        narration,
        subtitle_path,
        audio_duration
    )

    # -----------------------------------------------------
    # 9. FINAL VIDEO
    # -----------------------------------------------------

    final_video = (
        job_dir /
        "final.mp4"
    )

    add_captions(
        narrated_video,
        subtitle_path,
        final_video
    )

    logger.info(
        "FINAL VIDEO READY: %s",
        final_video
    )

    return final_video, title


# =========================================================
# TELEGRAM HELPERS
# =========================================================

async def send_progress(
    update,
    text
):

    try:
        return await update.message.reply_text(
            text
        )
    except Exception as e:

        logger.warning(
            "Could not send progress: %s",
            e
        )

        return None


# =========================================================
# START COMMAND
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً بك في Abosaraj\n\n"
        "أرسل لي أي قصة، وأنا أحولها إلى "
        "فيديو عمودي مع:\n\n"
        "📝 كتابة سينمائية\n"
        "🖼️ مشاهد مرئية\n"
        "🎙️ صوت رجل عربي\n"
        "💬 ترجمة على الفيديو\n"
        "🎞️ إخراج MP4 جاهز\n\n"
        "أرسل القصة الآن."
    )


# =========================================================
# PROCESS MESSAGE
# =========================================================

async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    story = update.message.text

    if not story:
        return

    story = story.strip()

    if len(story) < 20:

        await update.message.reply_text(
            "✍️ أرسل قصة أطول قليلًا حتى أقدر "
            "أحولها إلى فيديو."
        )

        return

    job_id = (
        datetime.now(timezone.utc)
        .strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    job_dir = (
        WORK_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    progress_message = await send_progress(
        update,
        "🎬 استلمت القصة.\n"
        "⏳ جاري تجهيز السيناريو..."
    )

    loop = asyncio.get_running_loop()

    try:

        def pipeline():

            return build_video(
                story,
                job_dir
            )

        final_video, title = await loop.run_in_executor(
            executor,
            pipeline
        )

        if progress_message:

            try:
                await progress_message.edit_text(
                    "✅ الفيديو جاهز.\n"
                    "📤 جاري إرساله..."
                )
            except Exception:
                pass

        caption = (
            f"🎬 {title}\n\n"
            "🤖 Abosaraj"
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=caption,
                supports_streaming=True
            )

        logger.info(
            "Video sent successfully: %s",
            job_id
        )

    except Exception as e:

        logger.exception(
            "Video pipeline failed"
        )

        error_text = str(e)

        if len(error_text) > 1500:
            error_text = error_text[-1500:]

        await update.message.reply_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            f"```text\n{error_text}\n```",
            parse_mode="Markdown"
        )

    finally:

        # تنظيف ملفات المهمة
        try:

            for file in job_dir.iterdir():

                try:
                    file.unlink()
                except Exception:
                    pass

            try:
                job_dir.rmdir()
            except Exception:
                pass

        except Exception as cleanup_error:

            logger.warning(
                "Cleanup failed: %s",
                cleanup_error
            )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram error:",
        exc_info=context.error
    )


# =========================================================
# TELEGRAM MAIN
# =========================================================

async def main():

    validate_environment()

    logger.info(
        "Starting Telegram bot..."
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot polling started"
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    try:

        await asyncio.Event().wait()

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    asyncio.run(
        main()
    )
