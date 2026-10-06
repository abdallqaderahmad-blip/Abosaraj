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
import fal_client

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

FAL_MODEL = os.getenv(
    "FAL_MODEL",
    "fal-ai/hunyuan-image/v3/text-to-image"
)


# =========================================================
# CHECK ENVIRONMENT VARIABLES
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

if not FAL_KEY:
    raise RuntimeError("FAL_KEY is missing")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running."


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# =========================================================
# TELEGRAM LOOP
# =========================================================

telegram_loop = None
application = None


# =========================================================
# SEND MESSAGE FROM BACKGROUND THREAD
# =========================================================

def send_async_message(chat_id, text):

    global telegram_loop

    if telegram_loop is None:
        logger.error("Telegram event loop is not available")
        return

    try:

        future = asyncio.run_coroutine_threadsafe(
            application.bot.send_message(
                chat_id=chat_id,
                text=text
            ),
            telegram_loop
        )

        future.result(timeout=60)

    except Exception as e:

        logger.exception(
            "Could not send Telegram message: %s",
            e
        )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔥 أهلاً بك في Abosaraj Bot!\n\n"
        "🎬 أنا جاهز لتحويل قصتك إلى فيديو.\n\n"
        "📖 أرسل لي قصة أو فكرة، وسأقوم بـ:\n\n"
        "🧠 تقسيم القصة إلى مشاهد\n"
        "🎨 إنشاء صور للمشاهد\n"
        "🎙️ إنشاء تعليق صوتي عربي\n"
        "🎬 تركيب الفيديو\n\n"
        "⏱️ مدة الفيديو حوالي دقيقة أو أكثر."
    )


# =========================================================
# /HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📚 طريقة الاستخدام:\n\n"
        "فقط أرسل القصة التي تريد تحويلها إلى فيديو.\n\n"
        "مثال:\n\n"
        "رجل فقير وجد حقيبة مليئة بالمال، "
        "وعندما فتحها وجد رسالة غامضة غيرت حياته بالكامل."
    )


# =========================================================
# GROQ - CREATE STORY SCENES
# =========================================================

def create_scenes(story):

    logger.info("Starting Groq story processing...")

    client = Groq(
        api_key=GROQ_API_KEY
    )

    system_prompt = """
أنت كاتب ومخرج محترف لفيديوهات القصص القصيرة.

مهمتك تحويل القصة إلى 8 مشاهد سينمائية مترابطة.

يجب أن يكون الناتج JSON فقط بدون أي كلام إضافي.

يجب أن يحتوي JSON على:

{
  "scenes": [
    {
      "scene": 1,
      "narration": "...",
      "image_prompt": "..."
    }
  ]
}

الشروط:

1. يجب إنشاء 8 مشاهد بالضبط.

2. narration يجب أن يكون باللغة العربية.

3. narration يجب أن يكون مناسباً للتعليق الصوتي.

4. image_prompt يجب أن يكون باللغة الإنجليزية.

5. image_prompt يجب أن يكون وصفاً سينمائياً مفصلاً.

6. وصف الصورة يجب أن يذكر:
- الشخصيات
- العمر التقريبي
- الملابس
- المكان
- الإضاءة
- الوقت
- المشاعر
- البيئة
- زاوية التصوير
- الأسلوب السينمائي

7. حافظ على شكل الشخصيات ثابتاً بين المشاهد.

8. لا تضع أي كتابة أو نص داخل الصور.

9. اجعل القصة تبدأ بمقدمة قوية.

10. اجعل الأحداث تتطور تدريجياً.

11. اجعل المشهد الثامن نهاية واضحة ومؤثرة.

12. لا تكتب أي شيء خارج JSON.

كل مشهد يجب أن يكون مناسباً لفيديو قصير مدته حوالي 7 إلى 8 ثوانٍ.
"""

    user_prompt = f"""
حوّل القصة التالية إلى 8 مشاهد:

{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
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

    content = response.choices[0].message.content.strip()

    logger.info(
        "Groq response received"
    )

    # إزالة Markdown إذا رجعه Groq
    if content.startswith("```"):

        content = content.replace(
            "```json",
            ""
        )

        content = content.replace(
            "```",
            ""
        )

        content = content.strip()

    try:

        data = json.loads(content)

    except Exception as e:

        logger.error(
            "Could not parse Groq JSON:"
        )

        logger.error(content)

        raise RuntimeError(
            f"Groq returned invalid JSON: {e}"
        )

    scenes = data.get(
        "scenes",
        []
    )

    if not scenes:

        raise RuntimeError(
            "Groq returned no scenes"
        )

    # إذا رجع أقل من 8 مشاهد
    while len(scenes) < 8:

        last_scene = dict(
            scenes[-1]
        )

        last_scene["scene"] = (
            len(scenes) + 1
        )

        scenes.append(
            last_scene
        )

    # نأخذ أول 8 فقط
    scenes = scenes[:8]

    logger.info(
        "Created %s scenes",
        len(scenes)
    )

    return scenes


# =========================================================
# FAL - GENERATE IMAGE
# =========================================================

def generate_image(prompt):

    logger.info(
        "Generating image with FAL..."
    )

    result = fal_client.subscribe(
        FAL_MODEL,
        arguments={
            "prompt": prompt,
            "image_size": "portrait_16_9",
            "num_images": 1
        },
        with_logs=False
    )

    logger.info(
        "FAL result received"
    )

    data = result

    if hasattr(result, "model_dump"):

        data = result.model_dump()

    elif hasattr(result, "dict"):

        data = result.dict()

    elif hasattr(result, "data"):

        data = result.data

    if isinstance(data, str):

        data = json.loads(data)

    images = data.get(
        "images",
        []
    )

    if not images:

        raise RuntimeError(
            f"FAL did not return an image. Result: {data}"
        )

    image_url = images[0].get(
        "url"
    )

    if not image_url:

        raise RuntimeError(
            "FAL image URL is missing"
        )

    return image_url


# =========================================================
# DOWNLOAD IMAGE
# =========================================================

def download_file(
    url,
    path
):

    logger.info(
        "Downloading image..."
    )

    response = requests.get(
        url,
        timeout=120
    )

    response.raise_for_status()

    with open(
        path,
        "wb"
    ) as f:

        f.write(
            response.content
        )


# =========================================================
# TEXT TO SPEECH
# =========================================================

def create_voice(
    text,
    output_path
):

    logger.info(
        "Creating Arabic voice..."
    )

    tts = gTTS(
        text=text,
        lang="ar",
        slow=False
    )

    tts.save(
        output_path
    )


# =========================================================
# FFMPEG CHECK
# =========================================================

def check_ffmpeg():

    logger.info(
        "Checking FFmpeg..."
    )

    result = subprocess.run(
        [
            "ffmpeg",
            "-version"
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            "FFmpeg is not available"
        )

    logger.info(
        "FFmpeg is ready"
    )


# =========================================================
# GET VIDEO DURATION
# =========================================================

def get_duration(path):

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        return 0

    try:

        return float(
            result.stdout.strip()
        )

    except Exception:

        return 0


# =========================================================
# CREATE VIDEO SCENE
# =========================================================

def create_scene_video(
    image_path,
    audio_path,
    output_path
):

    logger.info(
        "Creating video scene..."
    )

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
            "crop=720:1280,"
            "setsar=1"
        ),

        "-t",
        "8",

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
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "FFmpeg scene error:"
        )

        logger.error(
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "FFmpeg failed while creating scene"
        )


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_files,
    output_path
):

    concat_file = (
        output_path +
        ".txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in video_files:

            absolute = os.path.abspath(
                video
            )

            f.write(
                f"file '{absolute}'\n"
            )

    command = [

        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        concat_file,

        "-c",
        "copy",

        output_path
    ]

    logger.info(
        "Combining all scenes..."
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "Concat error:"
        )

        logger.error(
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "FFmpeg concat failed"
        )

    try:

        os.remove(
            concat_file
        )

    except Exception:
        pass


# =========================================================
# SEND FINAL VIDEO
# =========================================================

def send_final_video(
    chat_id,
    video_path
):

    global telegram_loop

    if telegram_loop is None:

        raise RuntimeError(
            "Telegram loop unavailable"
        )

    async def send():

        with open(
            video_path,
            "rb"
        ) as video:

            await application.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=(
                    "🎬 تم إنشاء الفيديو بنجاح!\n\n"
                    "🔥 Abosaraj"
                ),
                supports_streaming=True
            )

    future = asyncio.run_coroutine_threadsafe(
        send(),
        telegram_loop
    )

    future.result(
        timeout=600
    )


# =========================================================
# SEND PROGRESS
# =========================================================

def progress(
    chat_id,
    message
):

    logger.info(
        message
    )

    send_async_message(
        chat_id,
        message
    )


# =========================================================
# COMPLETE VIDEO GENERATION
# =========================================================

def generate_video_for_user(
    chat_id,
    story
):

    work_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    try:

        # -----------------------------------------
        # STEP 1
        # -----------------------------------------

        progress(
            chat_id,
            "🧠 أقرأ القصة وأقسمها إلى 8 مشاهد..."
        )

        scenes = create_scenes(
            story
        )

        # -----------------------------------------
        # STEP 2
        # -----------------------------------------

        video_files = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            progress(
                chat_id,
                f"🎨 المشهد {index}/8\n"
                f"جاري إنشاء الصورة..."
            )

            image_prompt = scene.get(
                "image_prompt",
                ""
            )

            narration = scene.get(
                "narration",
                ""
            )

            if not image_prompt:

                raise RuntimeError(
                    f"Scene {index} has no image prompt"
                )

            if not narration:

                raise RuntimeError(
                    f"Scene {index} has no narration"
                )

            # -----------------------------------------
            # IMAGE
            # -----------------------------------------

            image_url = generate_image(
                image_prompt
            )

            image_path = os.path.join(
                work_dir,
                f"image_{index}.jpg"
            )

            download_file(
                image_url,
                image_path
            )

            # -----------------------------------------
            # VOICE
            # -----------------------------------------

            progress(
                chat_id,
                f"🎙️ المشهد {index}/8\n"
                f"جاري إنشاء الصوت..."
            )

            audio_path = os.path.join(
                work_dir,
                f"audio_{index}.mp3"
            )

            create_voice(
                narration,
                audio_path
            )

            # -----------------------------------------
            # VIDEO
            # -----------------------------------------

            progress(
                chat_id,
                f"🎬 المشهد {index}/8\n"
                f"جاري تجهيز الفيديو..."
            )

            scene_video = os.path.join(
                work_dir,
                f"scene_{index}.mp4"
            )

            create_scene_video(
                image_path,
                audio_path,
                scene_video
            )

            video_files.append(
                scene_video
            )

        # -----------------------------------------
        # STEP 3
        # -----------------------------------------

        progress(
            chat_id,
            "🔥 كل المشاهد جاهزة!\n\n"
            "🎬 جاري تجميع الفيديو النهائي..."
        )

        final_video = os.path.join(
            work_dir,
            "abosaraj_final.mp4"
        )

        concat_videos(
            video_files,
            final_video
        )

        # -----------------------------------------
        # STEP 4
        # -----------------------------------------

        duration = get_duration(
            final_video
        )

        logger.info(
            "Final video duration: %.2f seconds",
            duration
        )

        progress(
            chat_id,
            f"✅ الفيديو جاهز!\n"
            f"⏱️ المدة: {duration:.1f} ثانية\n\n"
            f"📤 جاري إرسال الفيديو..."
        )

        # -----------------------------------------
        # STEP 5
        # -----------------------------------------

        send_final_video(
            chat_id,
            final_video
        )

        logger.info(
            "Video sent successfully"
        )

    except Exception as e:

        logger.exception(
            "VIDEO GENERATION FAILED"
        )

        send_async_message(
            chat_id,
            "❌ حصل خطأ أثناء إنشاء الفيديو.\n\n"
            f"الخطأ:\n{str(e)[:3000]}"
        )

    finally:

        try:

            shutil.rmtree(
                work_dir,
                ignore_errors=True
            )

        except Exception:
            pass


# =========================================================
# STORY MESSAGE
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

    if story.startswith("/"):
        return

    chat_id = update.effective_chat.id

    logger.info(
        "===================================="
    )

    logger.info(
        "Received story from chat %s",
        chat_id
    )

    logger.info(
        "Story length: %s",
        len(story)
    )

    logger.info(
        "===================================="
    )

    await update.message.reply_text(
        "📖 وصلت القصة!\n\n"
        "🧠 بدأت الآن تحويلها إلى فيديو...\n\n"
        "⏳ العملية قد تستغرق عدة دقائق.\n"
        "لا ترسل قصة ثانية حتى ينتهي الفيديو."
    )

    thread = threading.Thread(
        target=generate_video_for_user,
        args=(
            chat_id,
            story
        ),
        daemon=True
    )

    thread.start()


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Telegram error: %s",
        context.error
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

    try:

        await app_instance.bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Webhook deleted"
        )

    except Exception as e:

        logger.warning(
            "Could not delete webhook: %s",
            e
        )

    try:

        await app_instance.bot.set_my_commands(
            [
                (
                    "start",
                    "بدء البوت"
                ),
                (
                    "help",
                    "طريقة الاستخدام"
                )
            ]
        )

    except Exception as e:

        logger.warning(
            "Could not set bot commands: %s",
            e
        )


# =========================================================
# CREATE TELEGRAM APPLICATION
# =========================================================

application = (
    Application.builder()
    .token(BOT_TOKEN)
    .post_init(post_init)
    .build()
)


# =========================================================
# HANDLERS
# =========================================================

application.add_handler(
    CommandHandler(
        "start",
        start
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


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    logger.info(
        "===================================="
    )

    logger.info(
        "🔥 ABOSARAJ BOT STARTING..."
    )

    logger.info(
        "===================================="
    )

    # FFmpeg
    check_ffmpeg()

    # Flask
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    logger.info(
        "Flask server started"
    )

    # Telegram
    logger.info(
        "Starting Telegram polling..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
