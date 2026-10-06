import os
import json
import time
import uuid
import asyncio
import logging
import subprocess
import threading
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


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

HF_TOKEN = os.environ.get("HF_TOKEN")

VOICE = "ar-SA-HamedNeural"

# Hugging Face official LTX Video Space
HF_SPACE = "Lightricks/ltx-video-distilled"

# Video settings
WIDTH = 512
HEIGHT = 896

# We generate real moving clips.
# Final video is assembled from multiple clips.
SCENES_COUNT = 8

# Each generated clip is short to reduce ZeroGPU usage.
CLIP_DURATION = 4.0

# Final target
FINAL_MIN_SECONDS = 60

WORK_DIR = Path("/tmp/abosaraj")
WORK_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)

logger = logging.getLogger("Abosaraj")


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj is running", 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# ============================================================
# CHECK ENV
# ============================================================

def check_environment():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    if not HF_TOKEN:
        missing.append("HF_TOKEN")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# ============================================================
# GROQ STORY ENGINE
# ============================================================

def create_story(user_story: str):

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""
أنت كاتب ومخرج فيديوهات قصيرة احترافية.

حوّل القصة التالية إلى فيديو رعب/غموض قصير مناسب لـ
Instagram Reels وYouTube Shorts وTikTok.

القصة:
{user_story}

أريد بالضبط {SCENES_COUNT} مشاهد.

كل مشهد يجب أن يحتوي:

1. narration:
النص الذي سيُقرأ بالصوت العربي.

2. screen_text:
جملة قصيرة تظهر على الشاشة.

3. video_prompt:
وصف بصري باللغة الإنجليزية لتوليد فيديو AI حقيقي متحرك.

مهم جداً:

- لا تضع أي كتابة داخل الفيديو.
- لا تستخدم صور ثابتة.
- المشهد يجب أن يحتوي حركة حقيقية.
- استخدم حركة كاميرا واضحة.
- cinematic realistic horror.
- vertical video.
- 9:16 composition.
- realistic human movement.
- realistic lighting.
- atmospheric motion.
- لا تذكر subtitles أو text في video_prompt.
- حافظ على نفس الشخصيات والأماكن قدر الإمكان.

كل video_prompt يجب أن يكون مشهداً قابلاً للتحويل إلى فيديو،
وليس مجرد وصف لصورة.

أخرج JSON فقط بهذا الشكل:

{{
  "title": "عنوان القصة",
  "scenes": [
    {{
      "narration": "...",
      "screen_text": "...",
      "video_prompt": "..."
    }}
  ]
}}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional Arabic short-video "
                    "writer and cinematic director. "
                    "Return valid JSON only."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.8,
        max_tokens=5000
    )

    content = response.choices[0].message.content.strip()

    # Remove accidental markdown fences
    if content.startswith("```"):
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

    data = json.loads(content)

    if "scenes" not in data:
        raise RuntimeError("Groq did not return scenes")

    if len(data["scenes"]) < SCENES_COUNT:
        raise RuntimeError(
            f"Groq returned only {len(data['scenes'])} scenes"
        )

    return data


# ============================================================
# HUGGING FACE LTX VIDEO
# ============================================================

_hf_client = None


def get_hf_client():

    global _hf_client

    if _hf_client is None:

        logger.info(
            "Connecting to Hugging Face Space: %s",
            HF_SPACE
        )

        _hf_client = Client(
            HF_SPACE,
            token=HF_TOKEN
        )

    return _hf_client


def generate_ai_clip(prompt: str, output_path: Path):

    logger.info("Generating AI video clip...")
    logger.info("Prompt: %s", prompt[:250])

    client = get_hf_client()

    negative_prompt = (
        "worst quality, blurry, low quality, "
        "inconsistent motion, jittery motion, "
        "deformed body, distorted face, "
        "extra fingers, duplicate people, "
        "text, subtitles, watermark, logo"
    )

    # Current Lightricks Space exposes:
    #
    # text_to_video(
    #   prompt,
    #   negative_prompt,
    #   image,
    #   video,
    #   height,
    #   width,
    #   mode,
    #   duration,
    #   frames,
    #   seed,
    #   randomize_seed,
    #   guidance_scale,
    #   improve_texture
    # )
    #
    # The endpoint is officially named "text_to_video".

    result = client.predict(
        prompt,
        negative_prompt,
        None,
        None,
        HEIGHT,
        WIDTH,
        "text-to-video",
        CLIP_DURATION,
        9,
        0,
        True,
        3.0,
        False,
        api_name="/text_to_video"
    )

    logger.info("HF result: %s", result)

    # Gradio normally returns a filepath for gr.Video.
    video_source = result[0] if isinstance(result, tuple) else result

    if isinstance(video_source, dict):
        video_source = (
            video_source.get("path")
            or video_source.get("url")
        )

    if not video_source:
        raise RuntimeError(
            "Hugging Face returned no video file"
        )

    # Local file
    if os.path.exists(str(video_source)):

        subprocess.run(
            [
                "cp",
                str(video_source),
                str(output_path)
            ],
            check=True
        )

    # URL
    elif str(video_source).startswith("http"):

        r = requests.get(
            str(video_source),
            timeout=180
        )

        r.raise_for_status()

        output_path.write_bytes(r.content)

    else:
        raise RuntimeError(
            f"Unknown Hugging Face video result: {video_source}"
        )

    if not output_path.exists():
        raise RuntimeError(
            "Video file was not created"
        )

    if output_path.stat().st_size < 10000:
        raise RuntimeError(
            "Generated video file is suspiciously small"
        )

    logger.info(
        "AI clip created: %s (%d bytes)",
        output_path,
        output_path.stat().st_size
    )

    return output_path


# ============================================================
# ARABIC VOICE
# ============================================================

async def create_voice(text: str, output_path: Path):

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="-7%",
        pitch="-2Hz",
        volume="+0%"
    )

    await communicate.save(str(output_path))


def create_voice_sync(text: str, output_path: Path):

    asyncio.run(
        create_voice(text, output_path)
    )


# ============================================================
# FFMPEG - CAPTIONS + AUDIO
# ============================================================

def process_scene(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    screen_text: str
):

    font = "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"

    if not os.path.exists(font):
        font = "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"

    escaped_text = (
        screen_text
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\")
        .replace("]", "\")
    )

    vf = (
        "scale=720:1280:force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        "fps=30,"
        f"drawtext=fontfile='{font}':"
        f"text='{escaped_text}':"
        "fontcolor=white:"
        "fontsize=42:"
        "borderw=4:"
        "bordercolor=black:"
        "box=1:"
        "boxcolor=black@0.55:"
        "boxborderw=18:"
        "x=(w-text_w)/2:"
        "y=h-text_h-100"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-vf",
        vf,
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

    logger.info("Processing scene with FFmpeg...")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "FFmpeg error:\n%s",
            result.stderr[-4000:]
        )

        raise RuntimeError(
            "FFmpeg failed while processing scene"
        )


# ============================================================
# CONCATENATE VIDEOS
# ============================================================

def concat_videos(video_files, output_path: Path):

    concat_file = WORK_DIR / f"concat_{uuid.uuid4().hex}.txt"

    with open(concat_file, "w", encoding="utf-8") as f:

        for video in video_files:

            absolute = str(video.resolve())

            f.write(
                "file '"
                + absolute.replace("'", "'\\''")
                + "'\n"
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
        "-movflags",
        "+faststart",
        str(output_path)
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "Concat error:\n%s",
            result.stderr[-4000:]
        )

        raise RuntimeError(
            "Failed to combine video scenes"
        )

    return output_path


# ============================================================
# MAKE FINAL VIDEO 60+ SECONDS
# ============================================================

def extend_to_one_minute(
    input_video: Path,
    output_video: Path
):

    # The AI clips are real moving video.
    #
    # We loop the generated sequence until it reaches
    # approximately one minute.
    #
    # This avoids generating 60 seconds of expensive GPU
    # video for every story.

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(input_video),
        "-t",
        "60",
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
        "-movflags",
        "+faststart",
        str(output_video)
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "Final extension error:\n%s",
            result.stderr[-4000:]
        )

        raise RuntimeError(
            "Failed to create final 60 second video"
        )

    return output_video


# ============================================================
# COMPLETE VIDEO
# ============================================================

def create_complete_video(story_text: str):

    job_id = uuid.uuid4().hex

    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting video job: %s",
        job_id
    )

    story = create_story(story_text)

    scenes = story["scenes"]

    processed_scenes = []

    for index, scene in enumerate(
        scenes[:SCENES_COUNT],
        start=1
    ):

        logger.info(
            "========== SCENE %d/%d ==========",
            index,
            SCENES_COUNT
        )

        raw_video = (
            job_dir /
            f"scene_{index:02d}_raw.mp4"
        )

        voice_file = (
            job_dir /
            f"scene_{index:02d}.mp3"
        )

        processed_file = (
            job_dir /
            f"scene_{index:02d}_final.mp4"
        )

        # Generate actual moving AI video
        generate_ai_clip(
            scene["video_prompt"],
            raw_video
        )

        # Generate Arabic male voice
        create_voice_sync(
            scene["narration"],
            voice_file
        )

        # Captions + voice + formatting
        process_scene(
            raw_video,
            voice_file,
            processed_file,
            scene["screen_text"]
        )

        processed_scenes.append(
            processed_file
        )

    combined = (
        job_dir /
        "combined.mp4"
    )

    concat_videos(
        processed_scenes,
        combined
    )

    final_video = (
        job_dir /
        "Abosaraj_Final.mp4"
    )

    extend_to_one_minute(
        combined,
        final_video
    )

    logger.info(
        "FINAL VIDEO CREATED: %s",
        final_video
    )

    return final_video, story.get(
        "title",
        "Abosaraj Video"
    )


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً بك في Abosaraj\n\n"
        "ابعتلي أي قصة، وأنا أحولها إلى فيديو AI حقيقي "
        "بمشاهد متحركة + صوت عربي رجالي + ترجمة.\n\n"
        "⏳ العملية ممكن تأخذ عدة دقائق لأن الفيديو يتم "
        "توليده بالذكاء الاصطناعي."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    if len(text.strip()) < 30:

        await update.message.reply_text(
            "اكتبلي قصة أطول شوي، عشان أقدر أحولها لفيديو كامل."
        )

        return

    status = await update.message.reply_text(
        "🎬 استلمت القصة.\n\n"
        "🧠 جاري تقسيمها إلى مشاهد...\n"
        "🎥 بعدها رح أبدأ توليد فيديوهات AI حقيقية.\n\n"
        "لا تغلق المحادثة."
    )

    try:

        loop = asyncio.get_running_loop()

        final_video, title = await loop.run_in_executor(
            None,
            create_complete_video,
            text
        )

        await status.edit_text(
            "✅ الفيديو خلص!\n"
            f"🎬 {title}\n\n"
            "⬆️ جاري إرساله..."
        )

        with open(final_video, "rb") as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    f"🎬 {title}\n\n"
                    "Made by Abosaraj AI"
                ),
                supports_streaming=True
            )

    except Exception as e:

        logger.exception(
            "VIDEO GENERATION ERROR"
        )

        error_text = str(e)

        if len(error_text) > 2500:
            error_text = error_text[-2500:]

        await status.edit_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            "التفاصيل:\n"
            + error_text
        )


# ============================================================
# MAIN
# ============================================================

def main():

    check_environment()

    logger.info("Starting Abosaraj...")

    # Render health server
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
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
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info("Telegram bot started!")

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
