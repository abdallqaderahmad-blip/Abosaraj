import os
import json
import asyncio
import logging
import threading
import subprocess
import tempfile
import shutil

import requests
from flask import Flask
from gtts import gTTS
from groq import Groq
from huggingface_hub import InferenceClient

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

HF_IMAGE_MODEL = os.getenv(
    "HF_IMAGE_MODEL",
    "black-forest-labs/FLUX.1-schnell"
)


# =========================================================
# CHECK REQUIRED VARIABLES
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN is missing")


# =========================================================
# CLIENTS
# =========================================================

groq_client = Groq(
    api_key=GROQ_API_KEY
)

hf_client = InferenceClient(
    api_key=HF_TOKEN,
    provider="auto"
)


# =========================================================
# FLASK HEALTH SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running!"


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# =========================================================
# TELEGRAM GLOBALS
# =========================================================

telegram_loop = None
application = None


# =========================================================
# SEND MESSAGE FROM BACKGROUND THREAD
# =========================================================

def send_async_message(chat_id, text):
    global telegram_loop

    if telegram_loop is None:
        logger.error("Telegram loop is not available")
        return

    future = asyncio.run_coroutine_threadsafe(
        application.bot.send_message(
            chat_id=chat_id,
            text=text
        ),
        telegram_loop
    )

    try:
        future.result(timeout=60)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


# =========================================================
# START COMMAND
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🔥 أهلاً بك في Abosaraj Bot!\n\n"
        "🎬 أنا جاهز لتحويل قصتك إلى فيديو.\n\n"
        "📖 أرسل لي قصة أو فكرة، وسأقوم بـ:\n\n"
        "🧠 تقسيم القصة إلى مشاهد\n"
        "🎨 إنشاء صور للمشاهد\n"
        "🎙️ إنشاء تعليق صوتي عربي\n"
        "🎬 تركيب الفيديو\n\n"
        "⏱️ مدة الفيديو حوالي دقيقة أو أكثر."
    )

    await update.message.reply_text(text)


# =========================================================
# HELP COMMAND
# =========================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "📖 أرسل لي أي قصة أو فكرة وسأحولها إلى فيديو قصصي."
    )


# =========================================================
# CREATE 8 SCENES WITH GROQ
# =========================================================

def create_scenes(story):

    logger.info("Starting Groq story processing...")

    system_prompt = """
You are a professional short-video story director.

Your job is to transform the user's Arabic story into exactly 8 cinematic scenes.

Return ONLY valid JSON.

The JSON format must be exactly:

{
  "scenes": [
    {
      "scene": 1,
      "narration": "Arabic narration",
      "image_prompt": "Detailed English cinematic image prompt"
    }
  ]
}

Rules:

1. Exactly 8 scenes.
2. The story must progress logically from scene 1 to scene 8.
3. Narration must be in Arabic.
4. Image prompts must be in English.
5. Each image prompt should describe:
   - characters
   - location
   - emotions
   - lighting
   - camera composition
   - cinematic atmosphere
6. Keep the same character appearance throughout the story.
7. Make the scenes visually interesting.
8. Do not include markdown.
9. Do not include ```json.
10. Return JSON only.
"""

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": story
            }
        ],
        temperature=0.7,
        max_tokens=5000,
        response_format={
            "type": "json_object"
        }
    )

    logger.info("Groq response received")

    content = response.choices[0].message.content

    data = json.loads(content)

    scenes = data.get("scenes", [])

    if not scenes:
        raise RuntimeError("Groq returned no scenes")

    # If fewer than 8 scenes, repeat the last one
    while len(scenes) < 8:
        last_scene = dict(scenes[-1])
        last_scene["scene"] = len(scenes) + 1
        scenes.append(last_scene)

    scenes = scenes[:8]

    logger.info(f"Created {len(scenes)} scenes")

    return scenes


# =========================================================
# GENERATE IMAGE USING HUGGING FACE
# =========================================================

def generate_image(prompt, output_path):

    logger.info("Generating image with Hugging Face...")
    logger.info(f"Image model: {HF_IMAGE_MODEL}")

    enhanced_prompt = f"""
Cinematic vertical story scene.

{prompt}

High quality cinematic photography,
dramatic lighting,
realistic characters,
detailed environment,
strong composition,
emotional storytelling,
movie still,
professional film production,
no text,
no watermark.
"""

    image = hf_client.text_to_image(
        prompt=enhanced_prompt,
        model=HF_IMAGE_MODEL,
        width=768,
        height=1024,
        num_inference_steps=4
    )

    image.save(output_path)

    logger.info(f"Image saved: {output_path}")

    return output_path


# =========================================================
# DOWNLOAD FILE
# =========================================================

def download_file(url, output_path):

    logger.info(f"Downloading file: {url}")

    response = requests.get(
        url,
        timeout=120
    )

    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


# =========================================================
# CREATE ARABIC VOICE
# =========================================================

def create_voice(text, output_path):

    logger.info("Creating Arabic voice...")

    tts = gTTS(
        text=text,
        lang="ar",
        slow=False
    )

    tts.save(output_path)

    logger.info(f"Voice saved: {output_path}")

    return output_path


# =========================================================
# CHECK FFMPEG
# =========================================================

def check_ffmpeg():

    result = subprocess.run(
        ["ffmpeg", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError("FFmpeg is not available")


# =========================================================
# GET AUDIO DURATION
# =========================================================

def get_duration(file_path):

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        return 8.0

    try:
        return float(result.stdout.strip())
    except Exception:
        return 8.0


# =========================================================
# CREATE SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    audio_path,
    output_path
):

    logger.info("Creating scene video...")

    duration = get_duration(audio_path)

    # Minimum 8 seconds per scene
    duration = max(duration, 8.0)

    command = [
        "ffmpeg",
        "-y",

        "-loop",
        "1",

        "-i",
        image_path,

        "-i",
        audio_path,

        "-vf",
        (
            "scale=720:1280:"
            "force_original_aspect_ratio=increase,"
            "crop=720:1280"
        ),

        "-t",
        str(duration),

        "-r",
        "30",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "25",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-ar",
        "44100",

        "-ac",
        "2",

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        output_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        logger.error(result.stderr)

        raise RuntimeError(
            "FFmpeg scene creation failed"
        )

    logger.info(
        f"Scene video created: {output_path}"
    )

    return output_path


# =========================================================
# CONCAT SCENE VIDEOS
# =========================================================

def concat_videos(video_files, output_path):

    logger.info("Combining scene videos...")

    list_file = output_path + "_list.txt"

    with open(list_file, "w", encoding="utf-8") as f:

        for video in video_files:

            safe_path = os.path.abspath(video).replace(
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
        list_file,

        "-c",
        "copy",

        output_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        os.remove(list_file)
    except Exception:
        pass

    if result.returncode != 0:

        logger.error(result.stderr)

        raise RuntimeError(
            "FFmpeg concat failed"
        )

    logger.info(
        f"Final video created: {output_path}"
    )

    return output_path


# =========================================================
# SEND FINAL VIDEO
# =========================================================

async def send_final_video(
    chat_id,
    video_path
):

    logger.info("Sending final video to Telegram...")

    with open(video_path, "rb") as video:

        await application.bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=(
                "🎬 تم إنشاء الفيديو بنجاح!\n\n"
                "🔥 Abosaraj"
            ),
            supports_streaming=True
        )

    logger.info("Final video sent")


# =========================================================
# PROGRESS MESSAGE
# =========================================================

def progress(chat_id, text):

    send_async_message(
        chat_id,
        text
    )


# =========================================================
# GENERATE VIDEO
# =========================================================

def generate_video_for_user(
    chat_id,
    story
):

    temp_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    try:

        check_ffmpeg()

        # ---------------------------------------------
        # STORY
        # ---------------------------------------------

        progress(
            chat_id,
            "🧠 أقرأ القصة وأقسمها إلى 8 مشاهد..."
        )

        scenes = create_scenes(story)

        scene_videos = []

        # ---------------------------------------------
        # SCENES
        # ---------------------------------------------

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            progress(
                chat_id,
                f"🎨 المشهد {index}/8\n\n"
                "جاري إنشاء الصورة..."
            )

            image_path = os.path.join(
                temp_dir,
                f"scene_{index}.png"
            )

            generate_image(
                scene["image_prompt"],
                image_path
            )

            # -----------------------------------------
            # VOICE
            # -----------------------------------------

            progress(
                chat_id,
                f"🎙️ المشهد {index}/8\n"
                "جاري إنشاء التعليق الصوتي..."
            )

            audio_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp3"
            )

            create_voice(
                scene["narration"],
                audio_path
            )

            # -----------------------------------------
            # VIDEO
            # -----------------------------------------

            progress(
                chat_id,
                f"🎬 المشهد {index}/8\n"
                "جاري تركيب الفيديو..."
            )

            video_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp4"
            )

            create_scene_video(
                image_path,
                audio_path,
                video_path
            )

            scene_videos.append(
                video_path
            )

        # ---------------------------------------------
        # CONCAT
        # ---------------------------------------------

        progress(
            chat_id,
            "🎬 جمّعت المشاهد كلها...\n"
            "جاري تجهيز الفيديو النهائي."
        )

        final_video = os.path.join(
            temp_dir,
            "abosaraj_final.mp4"
        )

        concat_videos(
            scene_videos,
            final_video
        )

        # ---------------------------------------------
        # SEND
        # ---------------------------------------------

        progress(
            chat_id,
            "📤 الفيديو جاهز!\n"
            "جاري إرساله إليك..."
        )

        future = asyncio.run_coroutine_threadsafe(
            send_final_video(
                chat_id,
                final_video
            ),
            telegram_loop
        )

        future.result(
            timeout=300
        )

        logger.info(
            "Video generation completed successfully"
        )

    except Exception as e:

        logger.exception(
            "Video generation failed"
        )

        progress(
            chat_id,
            "❌ حصل خطأ أثناء إنشاء الفيديو.\n\n"
            f"الخطأ:\n{str(e)}"
        )

    finally:

        try:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )
        except Exception:
            pass


# =========================================================
# STORY HANDLER
# =========================================================

async def story_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    story = update.message.text.strip()

    if not story:
        return

    chat_id = update.effective_chat.id

    logger.info(
        f"Received story from chat {chat_id}"
    )

    await update.message.reply_text(
        "📖 وصلت القصة!\n\n"
        "🧠 بدأت الآن تحويلها إلى فيديو...\n\n"
        "⏳ العملية قد تستغرق عدة دقائق.\n"
        "لا ترسل قصة ثانية حتى ينتهي الفيديو."
    )

    thread = threading.Thread(
        target=generate_video_for_user,
        args=(chat_id, story),
        daemon=True
    )

    thread.start()


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.exception(
        "Telegram error:",
        exc_info=context.error
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    app_instance
):

    global telegram_loop

    telegram_loop = asyncio.get_running_loop()

    logger.info(
        "Telegram event loop initialized"
    )

    await app_instance.bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "Webhook deleted"
    )

    await app_instance.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "بدء البوت"
            ),
            BotCommand(
                "help",
                "المساعدة"
            )
        ]
    )


# =========================================================
# MAIN
# =========================================================

def main():

    global application

    # Start Flask
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram Application
    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Handlers
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            story_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Starting Abosaraj Telegram bot..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
