import os
import json
import asyncio
import logging
import threading
import subprocess
import tempfile
import shutil
import textwrap
import time

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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

HF_TOKEN = os.getenv("HF_TOKEN")

HF_IMAGE_MODEL = os.getenv(
    "HF_IMAGE_MODEL",
    "black-forest-labs/FLUX.1-schnell"
)


# =========================================================
# SETTINGS
# =========================================================

VOICE = "ar-SA-HamedNeural"

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
    return "Abosaraj Bot is running."


@app.route("/health")
def health():
    return "OK"


def run_flask():
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# =========================================================
# STORY GENERATION
# =========================================================

def generate_story():

    system_prompt = """
أنت كاتب محترف لقصص الفيديو القصيرة على Instagram Reels وYouTube Shorts.

نحن نبني قناة عربية اسمها "بعد منتصف الليل".

اكتب قصة أصلية بالكامل من نوع:
رعب / غموض / تشويق / نهاية صادمة.

القصة يجب أن تكون مناسبة لفيديو عمودي قصير.

ممنوع:
- نسخ قصص مشهورة.
- استخدام أسماء مشاهير.
- مقدمات طويلة.
- حشو.
- شرح خارج القصة.

نريد:
- Hook قوي جدًا في أول مشهد.
- تصاعد مستمر.
- غموض.
- جمل مناسبة للسرد الصوتي.
- نهايتها مفاجئة.
- إمكانية عمل Part 2 إذا كان ذلك مناسبًا.

قسّم القصة إلى 8 مشاهد.

كل مشهد يجب أن يحتوي:
1. narration:
النص العربي الذي سيقرأه الراوي.
استخدم علامات الترقيم والوقفات بشكل طبيعي.

2. image_prompt:
وصف بصري باللغة الإنجليزية فقط.
الوصف يجب أن يكون سينمائيًا ومفصلًا.

3. screen_text:
جملة عربية قصيرة تظهر على الشاشة.

مهم جدًا:
النص الصوتي يجب أن يكون طبيعيًا عند القراءة.

استخدم:
...
لخلق وقفة قصيرة.

واستخدم:
—
لخلق وقفة درامية.

لا تجعل narration طويلًا جدًا.

كل مشهد تقريبًا 15 إلى 30 كلمة عربية.

أعد JSON فقط بالشكل التالي:

{
  "title": "عنوان القصة",
  "genre": "horror",
  "hook": "الجملة الأقوى في القصة",
  "scenes": [
    {
      "narration": "...",
      "image_prompt": "...",
      "screen_text": "..."
    }
  ]
}
"""

    user_prompt = """
اكتب قصة عربية أصلية مرعبة ومشوقة جدًا.

ابدأ بجملة تجعل المشاهد يريد معرفة ماذا حدث.

أريد نهاية صادمة وغير متوقعة.

عدد المشاهد بالضبط: 8.
"""

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.9,
        max_tokens=7000,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    content = response.choices[0].message.content.strip()

    # تنظيف JSON إذا أضاف النموذج markdown
    if content.startswith("```"):
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

    story = json.loads(content)

    if "scenes" not in story:
        raise RuntimeError(
            "Story JSON does not contain scenes"
        )

    if len(story["scenes"]) != 8:
        raise RuntimeError(
            f"Expected 8 scenes, got {len(story['scenes'])}"
        )

    return story


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_image(
    prompt,
    output_path
):

    full_prompt = f"""
Cinematic dark Arabic horror short film scene.

{prompt}

Style:
photorealistic,
cinematic lighting,
dramatic shadows,
moody atmosphere,
high detail,
film still,
realistic characters,
realistic environment,
vertical composition,
9:16.

NO TEXT.
NO LETTERS.
NO WORDS.
NO LOGOS.
NO WATERMARK.
"""

    logger.info(
        "Generating image..."
    )

    image = hf_client.text_to_image(
        prompt=full_prompt,
        model=HF_IMAGE_MODEL,
    )

    image.save(
        output_path
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
        "Creating Arabic neural voice..."
    )

    # تنظيف بسيط للنص
    text = text.strip()

    # زيادة طبيعية بسيطة للوقفات
    text = text.replace(
        "...",
        "..."
    )

    async def generate():

        communicate = edge_tts.Communicate(
            text=text,
            voice=VOICE,
            rate="-7%",
            pitch="-2Hz",
            volume="+0%",
        )

        await communicate.save(
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
        "Voice created successfully."
    )

    return output_path


# =========================================================
# AUDIO DURATION
# =========================================================

def get_audio_duration(
    audio_path
):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    return float(
        result.stdout.strip()
    )


# =========================================================
# CREATE SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    audio_path,
    screen_text,
    output_path,
    scene_number,
):

    audio_duration = get_audio_duration(
        audio_path
    )

    # الحد الأدنى لكل مشهد
    duration = max(
        audio_duration + 0.3,
        7.0
    )

    text_file = output_path + ".txt"

    wrapped_text = "\n".join(
        textwrap.wrap(
            screen_text,
            width=24,
            break_long_words=False,
            replace_whitespace=False,
        )
    )

    with open(
        text_file,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(
            wrapped_text
        )

    # حركة كاميرا مختلفة لكل مشهد
    if scene_number % 2 == 0:

        zoom_expression = (
            "min(zoom+0.0008,1.12)"
        )

    else:

        zoom_expression = (
            "min(zoom+0.0006,1.10)"
        )

    frames = int(
        duration * 30
    )

    filter_complex = (
        f"[0:v]"
        f"scale=800:1422:force_original_aspect_ratio=increase,"
        f"crop=800:1422,"
        f"zoompan="
        f"z='{zoom_expression}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={frames}:"
        f"s=720x1280:"
        f"fps=30,"
        f"setsar=1,"
        f"format=yuv420p"
        f"[v];"

        f"[v]"
        f"drawtext="
        f"fontfile='{ARABIC_FONT}':"
        f"textfile='{text_file}':"
        f"fontcolor=white:"
        f"fontsize=42:"
        f"borderw=3:"
        f"bordercolor=black:"
        f"shadowx=2:"
        f"shadowy=2:"
        f"x=(w-text_w)/2:"
        f"y=h-text_h-110"
        f"[vout]"
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

        "-filter_complex",
        filter_complex,

        "-map",
        "[vout]",

        "-map",
        "1:a",

        "-t",
        str(duration),

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

        "-pix_fmt",
        "yuv420p",

        "-movflags",
        "+faststart",

        output_path,
    ]

    logger.info(
        f"Creating scene video {scene_number}..."
    )

    subprocess.run(
        command,
        check=True,
    )

    try:
        os.remove(
            text_file
        )
    except Exception:
        pass

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_paths,
    output_path
):

    list_file = output_path + ".txt"

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in video_paths:

            safe_path = (
                os.path.abspath(
                    video
                )
                .replace(
                    "'",
                    "'\\''"
                )
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

        "-movflags",
        "+faststart",

        output_path,
    ]

    subprocess.run(
        command,
        check=True,
    )

    try:
        os.remove(
            list_file
        )
    except Exception:
        pass

    return output_path


# =========================================================
# GENERATE COMPLETE VIDEO
# =========================================================

async def generate_video_for_user(
    update: Update
):

    temp_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    try:

        await update.message.reply_text(
            "🎬 تمام... عم بكتب القصة وبجهز المشاهد..."
        )

        story = await asyncio.to_thread(
            generate_story
        )

        title = story.get(
            "title",
            "قصة جديدة"
        )

        scenes = story[
            "scenes"
        ]

        await update.message.reply_text(
            f"📖 {title}\n\n"
            "بدأت أصنع الفيديو...\n"
            "🎙️ صوت عربي Neural\n"
            "🎥 حركة سينمائية\n"
            "📝 كتابة على الشاشة"
        )

        scene_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            await update.message.reply_text(
                f"🎞️ المشهد {index}/8..."
            )

            image_path = os.path.join(
                temp_dir,
                f"scene_{index}.png"
            )

            audio_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp3"
            )

            video_path = os.path.join(
                temp_dir,
                f"scene_{index}.mp4"
            )

            # -------------------------
            # IMAGE
            # -------------------------

            await asyncio.to_thread(
                generate_image,
                scene["image_prompt"],
                image_path,
            )

            # -------------------------
            # VOICE
            # -------------------------

            await asyncio.to_thread(
                create_voice,
                scene["narration"],
                audio_path,
            )

            # -------------------------
            # VIDEO
            # -------------------------

            await asyncio.to_thread(
                create_scene_video,
                image_path,
                audio_path,
                scene["screen_text"],
                video_path,
                index,
            )

            scene_videos.append(
                video_path
            )

        final_video = os.path.join(
            temp_dir,
            "final_video.mp4"
        )

        await update.message.reply_text(
            "🔥 عم أجمع الفيديو النهائي..."
        )

        await asyncio.to_thread(
            concat_videos,
            scene_videos,
            final_video,
        )

        await update.message.reply_text(
            "✅ خلص الفيديو! عم أرسله..."
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    f"🌙 {title}\n\n"
                    "بعد منتصف الليل..."
                ),
                supports_streaming=True,
            )

    except Exception as e:

        logger.exception(
            "Video generation failed"
        )

        await update.message.reply_text(
            "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
            f"التفاصيل: {str(e)[:500]}"
        )

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )


# =========================================================
# BACKGROUND WORKER
# =========================================================

def start_video_generation(
    update
):

    asyncio.run(
        generate_video_for_user(
            update
        )
    )


# =========================================================
# TELEGRAM HANDLERS
# =========================================================

async def start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🌙 أهلاً في بوت بعد منتصف الليل.\n\n"
        "ابعتلي أي رسالة، وأنا بحولها لقصة فيديو "
        "بصوت عربي + صور + حركة + كتابة."
    )


async def help_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 طريقة الاستخدام:\n\n"
        "ابعت أي فكرة أو قصة.\n\n"
        "البوت يحولها إلى:\n"
        "📖 قصة\n"
        "🎙️ صوت عربي Neural\n"
        "🎨 صور سينمائية\n"
        "🎥 حركة كاميرا\n"
        "📝 نص على الشاشة\n"
        "📱 فيديو عمودي 9:16"
    )


async def story_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    thread = threading.Thread(
        target=start_video_generation,
        args=(update,),
        daemon=True,
    )

    thread.start()


# =========================================================
# MAIN
# =========================================================

async def post_init(
    application
):

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "تشغيل البوت"
            ),
            BotCommand(
                "help",
                "المساعدة"
            ),
        ]
    )


def main():

    # Flask
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()

    # Telegram
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_handler
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            story_handler
        )
    )

    logger.info(
        "Starting Telegram bot..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
