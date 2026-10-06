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

# NEW MODEL
REPLICATE_MODEL = "wan-video/wan-2.2-5b-fast"

VOICE = "ar-SA-HamedNeural"

WIDTH = 720
HEIGHT = 1280

# 12 × 5 seconds ≈ 1 minute
SCENES_COUNT = 12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Abosaraj")

# =========================================================
# ENV CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

if not REPLICATE_API_TOKEN:
    raise RuntimeError("REPLICATE_API_TOKEN is missing")

groq_client = Groq(api_key=GROQ_API_KEY)

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj AI Video Bot is running!"


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
# CREATE STORY
# =========================================================

def create_story(user_story):

    logger.info("Creating story with Groq...")

    prompt = f"""
أنت كاتب سيناريو محترف لفيديوهات الرعب والغموض
على Instagram Reels وYouTube Shorts.

حوّل القصة التالية إلى فيديو سينمائي مدته حوالي دقيقة:

القصة:
{user_story}

أنشئ بالضبط {SCENES_COUNT} مشهداً.

كل مشهد يجب أن يكون تقريباً 5 ثوانٍ.

مهم جداً:

- الفيديو يجب أن يكون مرعباً وسينمائياً.
- لا تستخدم صوراً ثابتة.
- يجب أن يكون هناك حركة واضحة في كل مشهد.
- الشخصيات تتحرك.
- الكاميرا تتحرك.
- الإضاءة والظلال تتحرك.
- الرياح أو المطر أو الدخان يتحرك عند الحاجة.
- لا يوجد أي نص مكتوب داخل الفيديو الذي سيولد بالذكاء الاصطناعي.
- الكتابة ستضاف لاحقاً بواسطة البرنامج.
- حافظ على شكل الشخصيات بين المشاهد قدر الإمكان.

لكل مشهد أعطني:

narration:
جملة عربية قصيرة جداً تناسب حوالي 5 ثوانٍ.

screen_text:
كلمات قليلة ومخيفة تظهر على الشاشة.

video_prompt:
وصف إنجليزي سينمائي مفصل جداً للفيديو.
يجب أن يصف الحركة وليس صورة ثابتة.

مثال على الحركة:
slow camera movement,
character walking,
character suddenly turns,
flickering light,
moving shadows,
wind moving clothes,
rain falling,
cinematic handheld camera,
slow push in.

أرجع JSON فقط:

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
                "content": (
                    "أنت كاتب سيناريو محترف. "
                    "أرجع JSON صحيح فقط بدون أي كلام إضافي."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()

    if content.startswith("```"):
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

    start = content.find("{")
    end = content.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Groq did not return valid JSON")

    content = content[start:end + 1]

    data = json.loads(content)

    scenes = data.get("scenes", [])

    if len(scenes) < SCENES_COUNT:
        raise ValueError(
            f"Groq returned only {len(scenes)} scenes"
        )

    return data


# =========================================================
# REPLICATE VIDEO GENERATION
# =========================================================

def create_replicate_video(prompt, output_path):

    logger.info("Generating Wan 2.2 Fast video...")
    logger.info("Prompt: %s", prompt[:300])

    api_url = (
        "https://api.replicate.com/v1/models/"
        f"{REPLICATE_MODEL}/predictions"
    )

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait=60"
    }

    payload = {
        "input": {
            "prompt": prompt,

            # 81 frames ≈ 5 seconds at 16fps
            "num_frames": 81,

            # Cheaper resolution
            "resolution": "480p",

            # Vertical
            "aspect_ratio": "9:16",

            # Fast mode
            "go_fast": True,

            # Model default / good quality
            "sample_shift": 12,

            # 16 FPS = approximately 5 seconds
            "frames_per_second": 16
        }
    }

    response = requests.post(
        api_url,
        headers=headers,
        json=payload,
        timeout=90
    )

    if response.status_code not in (200, 201):

        raise RuntimeError(
            f"Replicate error {response.status_code}: "
            f"{response.text[:1500]}"
        )

    prediction = response.json()

    prediction_url = (
        prediction
        .get("urls", {})
        .get("get")
    )

    if not prediction_url:

        prediction_id = prediction.get("id")

        if not prediction_id:
            raise RuntimeError(
                f"No prediction ID returned: {prediction}"
            )

        prediction_url = (
            "https://api.replicate.com/v1/predictions/"
            f"{prediction_id}"
        )

    # =====================================================
    # WAIT
    # =====================================================

    while True:

        status_response = requests.get(
            prediction_url,
            headers={
                "Authorization":
                f"Bearer {REPLICATE_API_TOKEN}"
            },
            timeout=30
        )

        if status_response.status_code != 200:

            raise RuntimeError(
                "Replicate status error: "
                f"{status_response.text[:1500]}"
            )

        prediction = status_response.json()

        status = prediction.get("status")

        logger.info(
            "Replicate status: %s",
            status
        )

        if status == "succeeded":
            break

        if status in (
            "failed",
            "canceled"
        ):

            error = prediction.get(
                "error",
                "Unknown Replicate error"
            )

            raise RuntimeError(
                f"Video generation failed: {error}"
            )

        time.sleep(3)

    # =====================================================
    # OUTPUT
    # =====================================================

    output = prediction.get("output")

    if not output:
        raise RuntimeError(
            "Replicate returned no video"
        )

    if isinstance(output, list):
        video_url = output[0]
    else:
        video_url = output

    logger.info(
        "Downloading generated video..."
    )

    video_response = requests.get(
        video_url,
        timeout=180
    )

    if video_response.status_code != 200:

        raise RuntimeError(
            "Failed downloading video: "
            f"{video_response.status_code}"
        )

    with open(output_path, "wb") as f:
        f.write(video_response.content)

    logger.info(
        "Video downloaded successfully."
    )

    return output_path


# =========================================================
# VOICE
# =========================================================

def create_voice(text, output_path):

    logger.info(
        "Creating Arabic neural voice..."
    )

    async def generate():

        communicator = edge_tts.Communicate(
            text=text,
            voice=VOICE,
            rate="-7%",
            pitch="-2Hz",
            volume="+0%"
        )

        await communicator.save(
            output_path
        )

    asyncio.run(generate())

    return output_path


# =========================================================
# CAPTION
# =========================================================

def create_caption_file(text, path):

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text)

    return path


# =========================================================
# PROCESS SCENE
# =========================================================

def process_scene(
    scene,
    index,
    workdir
):

    raw_video = (
        workdir /
        f"raw_{index}.mp4"
    )

    voice_file = (
        workdir /
        f"voice_{index}.mp3"
    )

    caption_file = (
        workdir /
        f"caption_{index}.txt"
    )

    final_scene = (
        workdir /
        f"scene_{index}.mp4"
    )

    # =====================================================
    # AI VIDEO
    # =====================================================

    create_replicate_video(
        scene["video_prompt"],
        raw_video
    )

    # =====================================================
    # VOICE
    # =====================================================

    create_voice(
        scene["narration"],
        voice_file
    )

    # =====================================================
    # CAPTION
    # =====================================================

    create_caption_file(
        scene["screen_text"],
        caption_file
    )

    # =====================================================
    # FFMPEG
    # =====================================================

    font_path = (
        "/usr/share/fonts/truetype/noto/"
        "NotoSansArabic-Regular.ttf"
    )

    filter_complex = (
        f"[0:v]"
        f"scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
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

    logger.info(
        "Combining scene %s...",
        index
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-4000:]
        )

        raise RuntimeError(
            f"FFmpeg error on scene {index}"
        )

    return final_scene


# =========================================================
# COMBINE SCENES
# =========================================================

def combine_scenes(
    scene_files,
    output_path,
    workdir
):

    concat_file = (
        workdir /
        "concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for scene_file in scene_files:

            path = str(
                scene_file
            ).replace(
                "'",
                "'\\''"
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
        str(concat_file),

        "-c",
        "copy",

        "-movflags",
        "+faststart",

        str(output_path)
    ]

    logger.info(
        "Combining final video..."
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "Final video combination failed"
        )

    return output_path


# =========================================================
# COMPLETE VIDEO
# =========================================================

def create_complete_video(story):

    job_id = uuid.uuid4().hex[:10]

    workdir = (
        Path("/tmp") /
        f"abosaraj_{job_id}"
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        logger.info(
            "Starting job %s",
            job_id
        )

        # Story
        data = create_story(
            story
        )

        title = data.get(
            "title",
            "قصة غامضة"
        )

        scenes = data["scenes"][
            :SCENES_COUNT
        ]

        logger.info(
            "Title: %s",
            title
        )

        # =================================================
        # PROCESS ALL SCENES
        # =================================================

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

            scene_files.append(
                scene_file
            )

        # =================================================
        # FINAL
        # =================================================

        final_video = (
            workdir /
            "Abosaraj_Final.mp4"
        )

        combine_scenes(
            scene_files,
            final_video,
            workdir
        )

        logger.info(
            "FINAL VIDEO READY!"
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

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔥 Abosaraj AI Video\n\n"
        "ابعتلي قصة أو فكرة.\n\n"
        "وأنا أحولها إلى:\n"
        "🎬 فيديو AI متحرك\n"
        "🎙️ صوت عربي\n"
        "📝 كتابة على الشاشة\n"
        "📱 9:16\n"
        "🔥 Reels / Shorts"
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
            "✍️ ابعت قصة أطول شوي."
        )

        return

    processing_message = (
        await update.message.reply_text(
            "🎬 وصلت القصة.\n\n"
            "🧠 بكتب السيناريو...\n"
            "🎥 بجهز فيديوهات AI حقيقية...\n"
            "🎙️ بعمل الصوت...\n"
            "🎞️ وبجمعهم بفيديو واحد.\n\n"
            "⏳ اصبر عليّ شوي..."
        )
    )

    try:

        loop = asyncio.get_running_loop()

        video_path, title = (
            await loop.run_in_executor(
                None,
                create_complete_video,
                story
            )
        )

        await processing_message.edit_text(
            "🔥 الفيديو خلص!\n\n"
            f"🎬 {title}\n"
            "📤 جاري الإرسال..."
        )

        with open(
            video_path,
            "rb"
        ) as video:

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
            "التفاصيل:\n"
            f"{error_text}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "Starting Abosaraj..."
    )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

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
            filters.TEXT &
            ~filters.COMMAND,
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
