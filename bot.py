import os
import json
import asyncio
import logging
import threading
import subprocess
import tempfile
import shutil
import textwrap

from flask import Flask
from groq import Groq
import edge_tts
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
# ENVIRONMENT
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
    "stabilityai/stable-diffusion-3-medium-diffusers"
)


# =========================================================
# VOICE
# =========================================================

VOICE = "ar-SA-HamedNeural"


# =========================================================
# FONT
# =========================================================

ARABIC_FONT = (
    "/usr/share/fonts/truetype/"
    "noto/NotoNaskhArabic-Regular.ttf"
)


# =========================================================
# CHECK ENV
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
    provider="hf-inference",
    api_key=HF_TOKEN
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Bot is running!"


@app.route("/health")
def health():
    return "OK"


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
# STORY GENERATION
# =========================================================

def create_scenes(user_story):

    logger.info(
        "Creating story scenes..."
    )

    system_prompt = """
You are a professional Arabic short-form video director.

Transform the user's story into exactly 8 cinematic scenes.

The content is for:
Instagram Reels
YouTube Shorts
TikTok
Facebook Reels

Genre:
Horror
Mystery
Suspense
Psychological thriller

Return ONLY valid JSON.

Format:

{
  "title": "Arabic title",
  "scenes": [
    {
      "scene": 1,
      "narration": "Arabic narration",
      "screen_text": "Short Arabic text",
      "image_prompt": "Detailed English cinematic image prompt"
    }
  ]
}

Rules:

1. Exactly 8 scenes.

2. Narration must be Arabic.

3. Narration must sound natural when spoken.

4. Use short sentences.

5. Use punctuation to create pauses.

6. Each narration should be approximately 15-30 Arabic words.

7. Screen text must be short and powerful.

8. Image prompts must be English.

9. Images must be photorealistic and cinematic.

10. Keep characters visually consistent between scenes.

11. Describe:
- age
- gender
- clothing
- environment
- lighting
- emotion
- camera angle
- cinematic atmosphere

12. No text inside generated images.

13. No logos.

14. No watermark.

15. Start with a very strong hook.

16. Increase suspense every scene.

17. Scene 8 must contain a strong twist.

18. Do not explain anything outside JSON.

19. Never use markdown.

20. Return JSON only.
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
                "content": user_story
            }
        ],
        temperature=0.85,
        max_tokens=7000,
        response_format={
            "type": "json_object"
        }
    )

    content = (
        response
        .choices[0]
        .message
        .content
        .strip()
    )

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

    data = json.loads(
        content
    )

    scenes = data.get(
        "scenes",
        []
    )

    if len(scenes) != 8:

        raise RuntimeError(
            f"Groq returned {len(scenes)} scenes instead of 8"
        )

    return data


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_image(
    prompt,
    output_path
):

    logger.info(
        f"Generating image using {HF_IMAGE_MODEL}"
    )

    enhanced_prompt = f"""
Vertical cinematic horror movie scene.

{prompt}

Ultra realistic photography.

Dark cinematic atmosphere.

Professional horror film cinematography.

Dramatic realistic lighting.

Deep shadows.

Detailed environment.

Realistic human skin.

Realistic facial expressions.

Photorealistic.

High detail.

Strong composition.

Vertical 9:16 composition.

Movie still.

No text.

No letters.

No subtitles.

No logo.

No watermark.
"""

    image = hf_client.text_to_image(
        prompt=enhanced_prompt,
        model=HF_IMAGE_MODEL,
    )

    image.save(
        output_path
    )

    logger.info(
        f"Image saved: {output_path}"
    )

    return output_path


# =========================================================
# ARABIC NEURAL VOICE
# =========================================================

def create_voice(
    text,
    output_path
):

    logger.info(
        "Creating Arabic Neural voice..."
    )

    async def generate():

        communicator = edge_tts.Communicate(
            text=text,
            voice=VOICE,
            rate="-7%",
            pitch="-2Hz",
            volume="+0%",
        )

        await communicator.save(
            output_path
        )

    asyncio.run(
        generate()
    )

    if not os.path.exists(
        output_path
    ):

        raise RuntimeError(
            "Voice file was not created"
        )

    logger.info(
        "Arabic Neural voice created"
    )

    return output_path


# =========================================================
# AUDIO DURATION
# =========================================================

def get_duration(
    file_path
):

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

        return float(
            result.stdout.strip()
        )

    except Exception:

        return 8.0


# =========================================================
# CREATE SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    audio_path,
    screen_text,
    output_path,
    temp_dir,
    scene_number
):

    duration = get_duration(
        audio_path
    )

    duration = max(
        duration,
        7.0
    )

    frames = int(
        duration * 30
    )

    # -----------------------------------------
    # TEXT FILE
    # -----------------------------------------

    text_path = os.path.join(
        temp_dir,
        f"text_{scene_number}.txt"
    )

    wrapped_text = "\n".join(
        textwrap.wrap(
            screen_text,
            width=24,
            break_long_words=False,
            replace_whitespace=False
        )
    )

    with open(
        text_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            wrapped_text
        )

    # -----------------------------------------
    # CAMERA MOTION
    # -----------------------------------------

    if scene_number % 2 == 0:

        zoom = (
            "min(zoom+0.0008,1.12)"
        )

        x_position = (
            "iw/2-(iw/zoom/2)"
        )

    else:

        zoom = (
            "min(zoom+0.0006,1.10)"
        )

        x_position = (
            "(iw-iw/zoom)*on/"
            f"{frames}"
        )

    # -----------------------------------------
    # FILTER
    # -----------------------------------------

    filter_complex = (
        f"[0:v]"
        f"scale=900:1600:"
        f"force_original_aspect_ratio=increase,"
        f"crop=900:1600,"
        f"zoompan="
        f"z='{zoom}':"
        f"x='{x_position}':"
        f"y='(ih-ih/zoom)/2':"
        f"d={frames}:"
        f"s=720x1280:"
        f"fps=30,"
        f"setsar=1,"
        f"format=yuv420p"
        f"[base];"

        f"[base]"
        f"drawtext="
        f"fontfile='{ARABIC_FONT}':"
        f"textfile='{text_path}':"
        f"fontcolor=white:"
        f"fontsize=42:"
        f"borderw=4:"
        f"bordercolor=black:"
        f"shadowx=2:"
        f"shadowy=2:"
        f"x=(w-text_w)/2:"
        f"y=h-text_h-100"
        f"[video]"
    )

    # -----------------------------------------
    # FFMPEG
    # -----------------------------------------

    command = [
        "ffmpeg",
        "-y",

        "-loop",
        "1",

        "-i",
        image_path,

        "-i",
        audio_path,

        "-filter_complex",
        filter_complex,

        "-map",
        "[video]",

        "-map",
        "1:a:0",

        "-t",
        str(duration),

        "-r",
        "30",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

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

        "-movflags",
        "+faststart",

        output_path
    ]

    logger.info(
        f"Creating scene video {scene_number}/8..."
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
            "FFmpeg scene creation failed"
        )

    try:

        os.remove(
            text_path
        )

    except Exception:

        pass

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_files,
    output_path
):

    logger.info(
        "Combining videos..."
    )

    list_file = (
        output_path +
        "_list.txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in video_files:

            path = (
                os.path.abspath(
                    video
                )
                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{path}'\n"
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

        "-movflags",
        "+faststart",

        output_path
    ]

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
            "FFmpeg concat failed"
        )

    try:

        os.remove(
            list_file
        )

    except Exception:

        pass

    return output_path


# =========================================================
# VIDEO GENERATION
# =========================================================

async def generate_video(
    update,
    story
):

    temp_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    try:

        await update.message.reply_text(
            "🧠 عم أحلل القصة وأقسمها إلى 8 مشاهد..."
        )

        story_data = await asyncio.to_thread(
            create_scenes,
            story
        )

        title = story_data.get(
            "title",
            "بعد منتصف الليل"
        )

        scenes = story_data[
            "scenes"
        ]

        await update.message.reply_text(
            f"🌙 {title}\n\n"
            "🎙️ صوت عربي Neural\n"
            "🎨 صور سينمائية\n"
            "🎥 حركة كاميرا\n"
            "📝 كتابة على الشاشة\n\n"
            "بدأنا..."
        )

        scene_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            await update.message.reply_text(
                f"🎞️ المشهد {index}/8\n"
                "🎨 جاري إنشاء الصورة..."
            )

            image_path = os.path.join(
                temp_dir,
                f"scene_{index}.png"
            )

            await asyncio.to_thread(
                generate_image,
                scene["image_prompt"],
                image_path
            )

            await update.message.reply_text(
                f"🎙️ المشهد {index}/8\n"
                "جاري إنشاء الصوت..."
            )

            audio_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp3"
            )

            await asyncio.to_thread(
                create_voice,
                scene["narration"],
                audio_path
            )

            await update.message.reply_text(
                f"🎬 المشهد {index}/8\n"
                "جاري تحريك الصورة وإضافة الكتابة..."
            )

            video_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp4"
            )

            await asyncio.to_thread(
                create_scene_video,
                image_path,
                audio_path,
                scene["screen_text"],
                video_path,
                temp_dir,
                index
            )

            scene_videos.append(
                video_path
            )

        await update.message.reply_text(
            "🔥 كل المشاهد جاهزة.\n"
            "جاري تجميع الفيديو النهائي..."
        )

        final_video = os.path.join(
            temp_dir,
            "abosaraj_final.mp4"
        )

        await asyncio.to_thread(
            concat_videos,
            scene_videos,
            final_video
        )

        await update.message.reply_text(
            "📤 الفيديو جاهز!\n"
            "جاري إرساله إليك..."
        )

        with open(
            final_video,
            "rb"
        ) as video:

            await update.message.reply_video(
                video=video,
                caption=(
                    f"🌙 {title}\n\n"
                    "🔥 Abosaraj"
                ),
                supports_streaming=True
            )

    except Exception as e:

        logger.exception(
            "Video generation failed"
        )

        await update.message.reply_text(
            "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
            f"التفاصيل:\n{str(e)[:1000]}"
        )

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )


# =========================================================
# TELEGRAM
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🌙 أهلاً بك في Abosaraj!\n\n"
        "ابعتلي قصة أو حتى فكرة بسيطة.\n\n"
        "وأنا أحولها إلى:\n\n"
        "📖 قصة سينمائية\n"
        "🎨 صور AI\n"
        "🎙️ صوت عربي Neural\n"
        "🎥 حركة كاميرا\n"
        "📝 كتابة على الشاشة\n"
        "📱 فيديو عمودي"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📖 أرسل القصة أو الفكرة فقط."
    )


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

    await update.message.reply_text(
        "📖 وصلت القصة.\n\n"
        "🎬 بدأت صناعة الفيديو...\n\n"
        "اصبر علي شوي 🔥"
    )

    asyncio.create_task(
        generate_video(
            update,
            story
        )
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application
):

    await application.bot.set_my_commands(
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

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

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

    logger.info(
        "Starting Abosaraj Bot..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
