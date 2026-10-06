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
GROQ_MODEL = os.environ.get(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")

# Current Replicate video model
REPLICATE_MODEL = "wan-video/wan-2.1-1.3b"

VOICE = "ar-SA-HamedNeural"

WIDTH = 720
HEIGHT = 1280

SCENES_COUNT = 12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Abosaraj")

# =========================================================
# CHECK ENV
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

if not REPLICATE_API_TOKEN:
    raise RuntimeError("REPLICATE_API_TOKEN is missing")

groq_client = Groq(api_key=GROQ_API_KEY)

# =========================================================
# FLASK HEALTH SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj Video Bot is running!"


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
# GROQ - CREATE STORY
# =========================================================

def create_story(user_story):

    logger.info("Creating story with Groq...")

    prompt = f"""
أنت كاتب سيناريو محترف لمحتوى Reels وYouTube Shorts.

حوّل القصة التالية إلى فيديو رعب/غموض سينمائي قصير.

القصة:
{user_story}

المطلوب:

أنشئ بالضبط {SCENES_COUNT} مشهد.

كل مشهد يجب أن يكون مناسباً لفيديو مدته حوالي 5 ثوانٍ.

أريد:
- مشاهد بصرية قوية.
- حركة حقيقية داخل المشهد.
- أجواء سينمائية.
- رعب وغموض.
- لا تستخدم لقطات ثابتة.
- لا تجعل الشخصيات تتحدث أمام الكاميرا.
- لا تضع أي كتابة داخل الفيديو الذي سيولده الذكاء الاصطناعي.
- النص سيضاف لاحقاً بواسطة البرنامج.

لكل مشهد أعطني:

narration:
جملة عربية قصيرة جداً تناسب حوالي 5 ثوانٍ.

screen_text:
كلمات قليلة تظهر على الشاشة.

video_prompt:
وصف إنجليزي تفصيلي للفيديو المطلوب توليده بالذكاء الاصطناعي.
يجب أن يحتوي على حركة واضحة مثل:
walking, slowly turning, camera movement, wind, flickering lights,
rain, shadows moving, cinematic motion.

مهم:
اجعل كل مشهد مختلفاً بصرياً عن السابق مع المحافظة على نفس الشخصيات والجو العام.

أرجع JSON فقط بهذا الشكل:

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

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        messages=[
            {
                "role": "system",
                "content": "أنت كاتب سيناريو محترف. أرجع JSON صحيح فقط."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()

    # إزالة markdown إن وُجد
    if content.startswith("```"):
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

    # استخراج JSON لو Groq أضاف كلام
    start = content.find("{")
    end = content.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Groq did not return valid JSON")

    content = content[start:end + 1]

    data = json.loads(content)

    scenes = data.get("scenes", [])

    if len(scenes) < SCENES_COUNT:
        raise ValueError(
            f"Expected {SCENES_COUNT} scenes, got {len(scenes)}"
        )

    return data


# =========================================================
# REPLICATE VIDEO
# =========================================================

def create_replicate_video(prompt, output_path):

    logger.info("Generating AI video...")
    logger.info("Prompt: %s", prompt[:200])

    url = (
        "https://api.replicate.com/v1/models/"
        f"{REPLICATE_MODEL}/predictions"
    )

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",
        "Cancel-After": "5m",
    }

    payload = {
        "input": {
            "prompt": prompt,
            "frame_num": 81,
            "resolution": "480p",
            "aspect_ratio": "9:16",
            "sample_shift": 8,
            "sample_steps": 30,
            "sample_guide_scale": 6
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Replicate error {response.status_code}: "
            f"{response.text[:1000]}"
        )

    prediction = response.json()

    prediction_url = prediction.get("urls", {}).get("get")

    if not prediction_url:
        prediction_id = prediction.get("id")

        if not prediction_id:
            raise RuntimeError(
                f"Replicate did not return prediction ID: {prediction}"
            )

        prediction_url = (
            f"https://api.replicate.com/v1/predictions/"
            f"{prediction_id}"
        )

    # Poll
    while True:

        status_response = requests.get(
            prediction_url,
            headers={
                "Authorization": f"Bearer {REPLICATE_API_TOKEN}"
            },
            timeout=30
        )

        if status_response.status_code != 200:
            raise RuntimeError(
                f"Replicate status error: "
                f"{status_response.text[:1000]}"
            )

        prediction = status_response.json()

        status = prediction.get("status")

        logger.info("Replicate status: %s", status)

        if status == "succeeded":
            break

        if status in ("failed", "canceled"):
            error = prediction.get("error", "Unknown error")
            raise RuntimeError(
                f"Video generation failed: {error}"
            )

        time.sleep(3)

    output = prediction.get("output")

    if not output:
        raise RuntimeError("Replicate returned no video output")

    if isinstance(output, list):
        video_url = output[0]
    else:
        video_url = output

    logger.info("Downloading generated video...")

    video_response = requests.get(
        video_url,
        timeout=120
    )

    if video_response.status_code != 200:
        raise RuntimeError(
            f"Could not download generated video: "
            f"{video_response.status_code}"
        )

    with open(output_path, "wb") as f:
        f.write(video_response.content)

    return output_path


# =========================================================
# VOICE
# =========================================================

def create_voice(text, output_path):

    logger.info("Creating Arabic neural voice...")

    async def generate():

        communicator = edge_tts.Communicate(
            text=text,
            voice=VOICE,
            rate="-7%",
            pitch="-2Hz",
            volume="+0%"
        )

        await communicator.save(output_path)

    asyncio.run(generate())

    return output_path


# =========================================================
# CAPTION
# =========================================================

def create_caption_file(text, path):

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    return path


# =========================================================
# PROCESS ONE SCENE
# =========================================================

def process_scene(
    scene,
    index,
    workdir
):

    raw_video = workdir / f"raw_{index}.mp4"
    voice_file = workdir / f"voice_{index}.mp3"
    caption_file = workdir / f"caption_{index}.txt"
    final_scene = workdir / f"scene_{index}.mp4"

    # -----------------------------------------
    # VIDEO
    # -----------------------------------------

    create_replicate_video(
        scene["video_prompt"],
        raw_video
    )

    # -----------------------------------------
    # VOICE
    # -----------------------------------------

    create_voice(
        scene["narration"],
        voice_file
    )

    # -----------------------------------------
    # CAPTION
    # -----------------------------------------

    create_caption_file(
        scene["screen_text"],
        caption_file
    )

    # -----------------------------------------
    # VIDEO + AUDIO + TEXT
    # -----------------------------------------

    font_path = (
        "/usr/share/fonts/truetype/noto/"
        "NotoSansArabic-Regular.ttf"
    )

    filter_complex = (
        f"[0:v]"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"fps=30,"
        f"format=yuv420p,"
        f"drawtext="
        f"fontfile='{font_path}':"
        f"textfile='{caption_file}':"
        f"fontcolor=white:"
        f"fontsize=42:"
        f"borderw=4:"
        f"bordercolor=black:"
        f"x=(w-text_w)/2:"
        f"y=h-220:"
        f"box=1:"
        f"boxcolor=black@0.45:"
        f"boxborderw=18"
        f"[v]"
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(raw_video),

        "-i",
        str(voice_file),

        "-filter_complex",
        filter_complex,

        "-map",
        "[v]",

        "-map",
        "1:a",

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

        "-movflags",
        "+faststart",

        str(final_scene)
    ]

    logger.info("Combining scene %s...", index)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(result.stderr[-4000:])

        raise RuntimeError(
            f"FFmpeg scene error {index}"
        )

    return final_scene


# =========================================================
# CONCATENATE ALL SCENES
# =========================================================

def combine_scenes(scene_files, output_path, workdir):

    concat_file = workdir / "concat.txt"

    with open(concat_file, "w", encoding="utf-8") as f:

        for scene_file in scene_files:

            safe_path = str(scene_file).replace("'", "'\\''")

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

        "-movflags",
        "+faststart",

        str(output_path)
    ]

    logger.info("Combining all scenes...")

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(result.stderr[-5000:])

        raise RuntimeError(
            "Final video combination failed"
        )

    return output_path


# =========================================================
# CREATE COMPLETE VIDEO
# =========================================================

def create_complete_video(story):

    job_id = uuid.uuid4().hex[:10]

    workdir = Path("/tmp") / f"abosaraj_{job_id}"

    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        logger.info(
            "Starting job %s",
            job_id
        )

        # -----------------------------------------
        # STORY
        # -----------------------------------------

        data = create_story(story)

        title = data.get(
            "title",
            "قصة غامضة"
        )

        scenes = data["scenes"][:SCENES_COUNT]

        logger.info(
            "Story title: %s",
            title
        )

        # -----------------------------------------
        # PROCESS SCENES
        # -----------------------------------------

        scene_files = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            logger.info(
                "========== SCENE %s/%s ==========",
                index,
                SCENES_COUNT
            )

            scene_file = process_scene(
                scene,
                index,
                workdir
            )

            scene_files.append(scene_file)

        # -----------------------------------------
        # FINAL VIDEO
        # -----------------------------------------

        final_video = workdir / "Abosaraj_Final.mp4"

        combine_scenes(
            scene_files,
            final_video,
            workdir
        )

        logger.info(
            "FINAL VIDEO READY: %s",
            final_video
        )

        return final_video, title

    except Exception:

        logger.exception(
            "Video creation failed"
        )

        raise


# =========================================================
# TELEGRAM
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔥 Abosaraj AI Video\n\n"
        "ابعتلي أي قصة أو فكرة.\n\n"
        "وأنا أحولها إلى:\n"
        "🎬 فيديوهات AI متحركة\n"
        "🎙️ صوت عربي عصبي\n"
        "📝 كتابة على الشاشة\n"
        "📱 مقاس 9:16\n"
        "🔥 مناسب لـ Reels وShorts\n\n"
        "مثال:\n"
        "كان رجل يمشي وحده في شارع مهجور، "
        "وفجأة سمع صوت طفل يناديه من خلفه..."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    story = update.message.text.strip()

    if not story:
        return

    if len(story) < 20:

        await update.message.reply_text(
            "✍️ ابعتلي قصة أطول شوي، "
            "عشان أقدر أبني عليها فيديو كامل."
        )

        return

    processing_message = await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 بكتب السيناريو...\n"
        "🎥 بعدها ببدأ توليد الفيديوهات الحقيقية...\n"
        "🎙️ وبعدها الصوت والمونتاج.\n\n"
        "⏳ العملية ممكن تاخذ عدة دقائق."
    )

    try:

        loop = asyncio.get_running_loop()

        video_path, title = await loop.run_in_executor(
            None,
            create_complete_video,
            story
        )

        await processing_message.edit_text(
            "🔥 الفيديو خلص!\n\n"
            f"🎬 {title}\n"
            "📤 جاري إرساله..."
        )

        with open(video_path, "rb") as video:

            await update.message.reply_video(
                video=video,
                caption=(
                    "🔥 Abosaraj\n\n"
                    f"🎬 {title}\n"
                    "#رعب #غموض #قصص #Shorts #Reels"
                ),
                supports_streaming=True
            )

        await processing_message.delete()

    except Exception as e:

        logger.exception(
            "Telegram job failed"
        )

        error_text = str(e)

        if len(error_text) > 1500:
            error_text = error_text[-1500:]

        await processing_message.edit_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            f"التفاصيل:\n{error_text}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "Starting Abosaraj Video Bot..."
    )

    # Flask for Render
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info(
        "Telegram bot started!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
