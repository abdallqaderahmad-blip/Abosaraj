import os
import json
import uuid
import asyncio
import logging
import subprocess
import threading
import shutil
from pathlib import Path

import requests
import edge_tts

from flask import Flask
from groq import Groq
from gradio_client import Client

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

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
HF_TOKEN = os.environ.get("HF_TOKEN")

GROQ_MODEL = os.environ.get(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

HF_SPACE = "Lightricks/ltx-video-distilled"

VOICE = "ar-SA-HamedNeural"

VIDEO_WIDTH = 704
VIDEO_HEIGHT = 512

AI_SCENES = 4
CLIP_SECONDS = 5.0
FINAL_SECONDS = 60

WORK_DIR = Path("/tmp/abosaraj")
WORK_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("abosaraj")


# =========================================================
# FLASK SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running!"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# =========================================================
# GROQ
# =========================================================

groq_client = None


def get_groq_client():
    global groq_client

    if groq_client is None:

        if not GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY is missing."
            )

        logger.info("Connecting to Groq...")

        groq_client = Groq(
            api_key=GROQ_API_KEY
        )

        logger.info("Groq connected.")

    return groq_client


# =========================================================
# HUGGING FACE / GRADIO
# =========================================================

_hf_client = None


def get_hf_client():
    global _hf_client

    if _hf_client is None:

        logger.info(
            "Connecting to Hugging Face Space: %s",
            HF_SPACE
        )

        if not HF_TOKEN:
            raise RuntimeError(
                "HF_TOKEN is missing from Render environment variables."
            )

        _hf_client = Client(
            HF_SPACE,
            hf_token=HF_TOKEN
        )

        logger.info(
            "Connected to Hugging Face."
        )

    return _hf_client


# =========================================================
# STORY -> 4 SCENES
# =========================================================

def create_scenes(story):

    client = get_groq_client()

    system_prompt = """
You are a professional Arabic short-video script writer.

The user will give you an Arabic story.

Convert the story into exactly 4 cinematic scenes.

The final video is intended for:
Instagram Reels
YouTube Shorts
TikTok
Facebook Reels

The video should feel cinematic, mysterious, emotional and realistic.

IMPORTANT:

Return ONLY valid JSON.

No markdown.
No explanation.
No ```.

Use exactly this structure:

{
  "scenes": [
    {
      "narration": "...",
      "screen_text": "...",
      "video_prompt": "..."
    },
    {
      "narration": "...",
      "screen_text": "...",
      "video_prompt": "..."
    },
    {
      "narration": "...",
      "screen_text": "...",
      "video_prompt": "..."
    },
    {
      "narration": "...",
      "screen_text": "...",
      "video_prompt": "..."
    }
  ]
}

Rules:

- narration must be Arabic.
- screen_text must be short Arabic text.
- video_prompt must be English.
- video_prompt must describe REALISTIC MOVING VIDEO.
- Do NOT describe a still image.
- Include camera movement.
- Include character movement.
- Include environmental movement.
- Keep the same characters and visual identity across scenes.
- Cinematic lighting.
- Realistic human appearance.
- No subtitles inside the generated video.
- No text inside the generated video.
- No logos.
- No watermark.
"""

    user_prompt = f"""
Create exactly 4 cinematic scenes from this Arabic story:

{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        temperature=0.8,
        max_tokens=4000
    )

    content = response.choices[0].message.content.strip()

    if content.startswith("```"):
        content = content.replace(
            "```json",
            ""
        ).replace(
            "```",
            ""
        ).strip()

    data = json.loads(content)

    scenes = data.get("scenes")

    if not scenes:
        raise RuntimeError(
            "Groq returned no scenes."
        )

    if len(scenes) != 4:
        raise RuntimeError(
            f"Groq returned {len(scenes)} scenes instead of 4."
        )

    return scenes


# =========================================================
# HUGGING FACE VIDEO
# =========================================================

def generate_ai_clip(prompt, output_path):

    logger.info(
        "Generating AI video clip..."
    )

    client = get_hf_client()

    negative_prompt = (
        "worst quality, low quality, blurry, "
        "static image, photograph, slideshow, "
        "jittery motion, distorted face, "
        "deformed body, extra fingers, "
        "duplicate person, text, subtitles, "
        "logo, watermark"
    )

    try:

        result = client.predict(
            prompt,
            negative_prompt,
            None,
            None,
            VIDEO_HEIGHT,
            VIDEO_WIDTH,
            "text-to-video",
            CLIP_SECONDS,
            9,
            42,
            True,
            3.0,
            False,
            api_name="/text_to_video"
        )

        logger.info(
            "LTX raw result: %s",
            result
        )

    except Exception as e:

        logger.exception(
            "Hugging Face generation failed."
        )

        raise RuntimeError(
            f"Hugging Face video generation failed: {e}"
        )

    # =====================================================
    # FIND VIDEO PATH
    # =====================================================

    video_source = None

    if isinstance(result, str):

        video_source = result

    elif isinstance(result, (list, tuple)):

        for item in result:

            if isinstance(item, str):

                if (
                    item.endswith(".mp4")
                    or item.endswith(".webm")
                    or item.endswith(".mov")
                ):
                    video_source = item
                    break

            elif isinstance(item, dict):

                for key in [
                    "video",
                    "path",
                    "file",
                    "url"
                ]:

                    value = item.get(key)

                    if isinstance(value, str):
                        video_source = value
                        break

                if video_source:
                    break

    elif isinstance(result, dict):

        for key in [
            "video",
            "path",
            "file",
            "url"
        ]:

            value = result.get(key)

            if isinstance(value, str):
                video_source = value
                break

    if not video_source:

        raise RuntimeError(
            f"Could not find video file in Hugging Face result: {result}"
        )

    logger.info(
        "Video source: %s",
        video_source
    )

    # =====================================================
    # LOCAL VIDEO
    # =====================================================

    if (
        video_source.startswith("/tmp/")
        or video_source.startswith("/home/")
    ):

        if not os.path.exists(video_source):

            raise RuntimeError(
                f"Generated video does not exist: {video_source}"
            )

        shutil.copyfile(
            video_source,
            output_path
        )

        return output_path

    # =====================================================
    # DOWNLOAD VIDEO
    # =====================================================

    if (
        video_source.startswith("http://")
        or video_source.startswith("https://")
    ):

        response = requests.get(
            video_source,
            timeout=300
        )

        response.raise_for_status()

        with open(
            output_path,
            "wb"
        ) as f:

            f.write(
                response.content
            )

        return output_path

    raise RuntimeError(
        f"Unknown video source: {video_source}"
    )


# =========================================================
# TEXT TO SPEECH
# =========================================================

async def create_voice(
    text,
    output_path
):

    logger.info(
        "Generating Arabic voice..."
    )

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="+0%",
        volume="+0%"
    )

    await communicate.save(
        str(output_path)
    )


# =========================================================
# FFMPEG
# =========================================================

def run_ffmpeg(command):

    logger.info(
        "Running FFmpeg..."
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr
        )

        raise RuntimeError(
            "FFmpeg failed."
        )

    return result


# =========================================================
# PROCESS ONE SCENE
# =========================================================

def process_scene(
    scene,
    scene_index,
    job_dir
):

    raw_video = (
        job_dir /
        f"scene_{scene_index}_raw.mp4"
    )

    voice_file = (
        job_dir /
        f"scene_{scene_index}_voice.mp3"
    )

    final_scene = (
        job_dir /
        f"scene_{scene_index}_final.mp4"
    )

    # =====================================================
    # AI VIDEO
    # =====================================================

    generate_ai_clip(
        scene["video_prompt"],
        raw_video
    )

    # =====================================================
    # VOICE
    # =====================================================

    asyncio.run(
        create_voice(
            scene["narration"],
            voice_file
        )
    )

    # =====================================================
    # TEXT
    # =====================================================

    screen_text = (
        scene.get(
            "screen_text",
            ""
        )
        .replace(
            "\\",
            ""
        )
        .replace(
            "[",
            "\\["
        )
        .replace(
            "]",
            "\\]"
        )
        .replace(
            ":",
            "\\:"
        )
        .replace(
            "'",
            "\\'"
        )
    )

    # =====================================================
    # VIDEO
    # =====================================================

    video_filter = (
        "scale=720:1280:"
        "force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        "fps=30,"
        "format=yuv420p"
    )

    if screen_text.strip():

        drawtext = (
            "drawtext="
            "fontfile=/usr/share/fonts/truetype/noto/"
            "NotoSansArabic-Regular.ttf:"
            f"text='{screen_text}':"
            "fontcolor=white:"
            "fontsize=42:"
            "borderw=3:"
            "bordercolor=black:"
            "x=(w-text_w)/2:"
            "y=h-180"
        )

        video_filter += "," + drawtext

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(raw_video),

        "-i",
        str(voice_file),

        "-vf",
        video_filter,

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-shortest",

        str(final_scene)
    ]

    run_ffmpeg(
        command
    )

    return final_scene


# =========================================================
# CONCATENATE
# =========================================================

def concatenate_scenes(
    scene_files,
    output_file
):

    concat_file = (
        output_file.parent /
        "concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in scene_files:

            safe_path = str(
                video
            ).replace(
                "'",
                "'\\''"
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
        str(concat_file),

        "-c",
        "copy",

        str(output_file)
    ]

    run_ffmpeg(
        command
    )


# =========================================================
# FINAL 60 SECONDS
# =========================================================

def make_final_60_seconds(
    source,
    output
):

    logger.info(
        "Extending final video to 60 seconds..."
    )

    command = [
        "ffmpeg",
        "-y",

        "-stream_loop",
        "-1",

        "-i",
        str(source),

        "-t",
        str(FINAL_SECONDS),

        "-vf",
        (
            "scale=720:1280:"
            "force_original_aspect_ratio=increase,"
            "crop=720:1280,"
            "fps=30,"
            "format=yuv420p"
        ),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-t",
        str(FINAL_SECONDS),

        str(output)
    ]

    run_ffmpeg(
        command
    )

    return output


# =========================================================
# GENERATE COMPLETE VIDEO
# =========================================================

def generate_video(story):

    job_id = uuid.uuid4().hex

    job_dir = (
        WORK_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        logger.info(
            "Starting job %s",
            job_id
        )

        # -------------------------------------------------
        # GROQ
        # -------------------------------------------------

        logger.info(
            "Creating 4 scenes..."
        )

        scenes = create_scenes(
            story
        )

        logger.info(
            "4 scenes created."
        )

        # -------------------------------------------------
        # AI CLIPS
        # -------------------------------------------------

        scene_files = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            logger.info(
                "Processing scene %s/4",
                index
            )

            scene_file = process_scene(
                scene,
                index,
                job_dir
            )

            scene_files.append(
                scene_file
            )

        # -------------------------------------------------
        # CONCAT
        # -------------------------------------------------

        combined = (
            job_dir /
            "combined.mp4"
        )

        concatenate_scenes(
            scene_files,
            combined
        )

        # -------------------------------------------------
        # 60 SEC
        # -------------------------------------------------

        final_video = (
            job_dir /
            "abosaraj_final.mp4"
        )

        make_final_60_seconds(
            combined,
            final_video
        )

        logger.info(
            "Video completed: %s",
            final_video
        )

        return final_video

    except Exception:

        logger.exception(
            "VIDEO GENERATION FAILED"
        )

        raise


# =========================================================
# TELEGRAM START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً بك في Abosaraj\n\n"
        "ابعث لي قصة بالعربي، "
        "وأنا أحولها إلى فيديو قصير سينمائي "
        "مع صوت عربي وحركة وترجمة.\n\n"
        "⏳ الفيديو حوالي دقيقة."
    )


# =========================================================
# TELEGRAM MESSAGE
# =========================================================

async def handle_message(
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
            "✍️ ابعتلي قصة أطول شوي، "
            "حتى أقدر أحولها لفيديو."
        )

        return

    await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 عم ببني المشاهد...\n"
        "🎥 بعدها رح أعمل الفيديو بالذكاء الاصطناعي.\n\n"
        "⏳ استنى شوي..."
    )

    try:

        loop = asyncio.get_running_loop()

        video_path = await loop.run_in_executor(
            None,
            generate_video,
            story
        )

        await update.message.reply_text(
            "✅ الفيديو جاهز 🎬\n\n👇"
        )

        with open(
            video_path,
            "rb"
        ) as video:

            await update.message.reply_video(
                video=video,
                caption=(
                    "🎬 Abosaraj\n"
                    "مصنوع بالذكاء الاصطناعي"
                ),
                supports_streaming=True
            )

    except Exception as e:

        logger.exception(
            "Telegram video generation error"
        )

        await update.message.reply_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            f"التفاصيل:\n{str(e)}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    logger.info(
        "Starting Flask server..."
    )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    logger.info(
        "Building Telegram application..."
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
            handle_message
        )
    )

    logger.info(
        "Telegram bot started!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except Exception:

        logger.exception(
            "FATAL APPLICATION ERROR"
        )

        raise
