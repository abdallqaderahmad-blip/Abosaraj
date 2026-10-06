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


# ============================================================
# CONFIG
# ============================================================

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


# ============================================================
# WORK DIRECTORY
# ============================================================

WORK_DIR = Path("/tmp/abosaraj")
WORK_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True
)

logger = logging.getLogger("Abosaraj")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj is running", 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    try:
        port = int(os.environ.get("PORT", "10000"))

        logger.info(
            "Starting health server on port %s",
            port
        )

        app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False
        )

    except Exception:
        logger.exception(
            "FLASK SERVER ERROR"
        )


# ============================================================
# ENVIRONMENT CHECK
# ============================================================

def check_environment():

    logger.info("Checking environment variables...")

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

    logger.info("BOT_TOKEN: OK")
    logger.info("GROQ_API_KEY: OK")
    logger.info("HF_TOKEN: OK")
    logger.info("GROQ_MODEL: %s", GROQ_MODEL)
    logger.info("HF_SPACE: %s", HF_SPACE)


# ============================================================
# GROQ STORY GENERATION
# ============================================================

def create_story(user_story: str):

    logger.info(
        "Sending story to Groq..."
    )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    prompt = f"""
أنت كاتب ومخرج فيديوهات رعب وغموض قصيرة.

حوّل القصة التالية إلى فيديو عربي سينمائي:

{user_story}

نريد فيديو نهائي حوالي دقيقة.

قسّم القصة إلى بالضبط {AI_SCENES} مشاهد رئيسية.

لكل مشهد:

narration:
النص العربي الذي سيُقرأ بالصوت.

screen_text:
جملة عربية قصيرة تظهر على الشاشة.

video_prompt:
وصف باللغة الإنجليزية لمشهد فيديو AI حقيقي متحرك.

شروط video_prompt:

- cinematic realistic horror
- real moving video
- visible character movement
- camera movement
- environmental movement
- realistic lighting
- atmospheric motion
- vertical social-media composition
- no text
- no subtitles
- no logos
- no watermark
- no static image
- no slideshow
- no photograph

اجعل المشاهد مترابطة بصرياً.

مهم جداً:

لا تجعل الفيديو مجرد شخص واقف.

يجب أن يحدث شيء في كل مشهد مثل:

walking
turning
opening door
looking around
wind
moving curtains
moving shadows
camera movement
breathing
approaching
running
slow head movement

أخرج JSON فقط بدون أي شرح.

الصيغة:

{{
  "title": "عنوان",
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
                    "You are an expert Arabic horror "
                    "short-video director. "
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

    content = (
        response.choices[0]
        .message.content
        .strip()
    )

    logger.info(
        "Groq response received."
    )

    # Remove markdown fences
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

    except json.JSONDecodeError as e:

        logger.error(
            "Invalid JSON from Groq:"
        )

        logger.error(
            "%s",
            content
        )

        raise RuntimeError(
            "Groq returned invalid JSON: "
            + str(e)
        )

    scenes = data.get("scenes")

    if not scenes:

        raise RuntimeError(
            "Groq returned no scenes."
        )

    if len(scenes) < AI_SCENES:

        raise RuntimeError(
            f"Groq returned only "
            f"{len(scenes)} scenes."
        )

    logger.info(
        "Groq created %d scenes.",
        len(scenes)
    )

    return data


# ============================================================
# HUGGING FACE
# ============================================================

_hf_client = None


def get_hf_client():

    global _hf_client

    if _hf_client is None:

        logger.info(
            "Connecting to Hugging Face Space..."
        )

        _hf_client = Client(
            HF_SPACE,
            token=HF_TOKEN
        )

        logger.info(
            "Connected to Hugging Face."
        )

    return _hf_client


# ============================================================
# EXTRACT VIDEO PATH
# ============================================================

def extract_video_path(result):

    found = []

    def walk(value):

        if value is None:
            return

        if isinstance(value, (list, tuple)):

            for item in value:
                walk(item)

            return

        if isinstance(value, dict):

            for key in [
                "path",
                "video",
                "file",
                "name",
                "url"
            ]:

                if key in value and value[key]:
                    walk(value[key])

            for item in value.values():
                walk(item)

            return

        for attr in [
            "path",
            "video",
            "file",
            "name",
            "url"
        ]:

            try:

                attr_value = getattr(
                    value,
                    attr,
                    None
                )

                if attr_value:
                    walk(attr_value)

            except Exception:
                pass

        if isinstance(value, str):

            found.append(value)

    walk(result)

    unique = []

    for item in found:

        if item not in unique:
            unique.append(item)

    logger.info(
        "Possible HF outputs: %r",
        unique
    )

    # Local files first
    for item in unique:

        try:

            if os.path.isfile(item):
                return item

        except Exception:
            pass

    # URLs second
    for item in unique:

        if (
            item.startswith("http://")
            or item.startswith("https://")
        ):
            return item

    return None


# ============================================================
# SAVE VIDEO
# ============================================================

def save_video_result(
    source,
    destination: Path
):

    if not source:

        raise RuntimeError(
            "Hugging Face returned no video source."
        )

    logger.info(
        "Selected video source: %r",
        source
    )

    # Local file
    if os.path.isfile(str(source)):

        shutil.copyfile(
            str(source),
            str(destination)
        )

    # Remote URL
    elif (
        str(source).startswith("http://")
        or str(source).startswith("https://")
    ):

        logger.info(
            "Downloading generated video..."
        )

        response = requests.get(
            str(source),
            timeout=300
        )

        response.raise_for_status()

        with open(
            destination,
            "wb"
        ) as f:

            f.write(
                response.content
            )

    else:

        raise RuntimeError(
            "Unknown video source: "
            + str(source)
        )

    if not destination.exists():

        raise RuntimeError(
            "Video was not saved."
        )

    size = destination.stat().st_size

    logger.info(
        "Saved AI video: %s bytes",
        size
    )

    if size < 10000:

        raise RuntimeError(
            "Generated video is too small."
        )

    return destination


# ============================================================
# GENERATE AI VIDEO
# ============================================================

def generate_ai_clip(
    prompt: str,
    output_path: Path
):

    logger.info(
        "======================================"
    )

    logger.info(
        "GENERATING REAL AI VIDEO"
    )

    logger.info(
        "Prompt: %s",
        prompt[:500]
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
            api_name="text_to_video"
        )

        logger.info(
            "RAW HF RESULT TYPE: %s",
            type(result)
        )

        logger.info(
            "RAW HF RESULT: %r",
            result
        )

    except Exception as e:

        logger.exception(
            "HUGGING FACE GENERATION FAILED"
        )

        raise RuntimeError(
            "Hugging Face generation failed: "
            + str(e)
        )

    source = extract_video_path(
        result
    )

    if not source:

        raise RuntimeError(
            "Hugging Face returned a response "
            "but no video file was found.\n\n"
            f"RAW RESULT:\n{result!r}"
        )

    save_video_result(
        source,
        output_path
    )

    return output_path


# ============================================================
# EDGE TTS
# ============================================================

async def generate_voice(
    text: str,
    output_path: Path
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="-7%",
        pitch="-2Hz",
        volume="+0%"
    )

    await communicate.save(
        str(output_path)
    )


def generate_voice_sync(
    text: str,
    output_path: Path
):

    asyncio.run(
        generate_voice(
            text,
            output_path
        )
    )


# ============================================================
# PROCESS SCENE
# ============================================================

def process_scene(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    screen_text: str
):

    font = (
        "/usr/share/fonts/truetype/noto/"
        "NotoSansArabic-Regular.ttf"
    )

    if not os.path.exists(font):

        font = (
            "/usr/share/fonts/truetype/noto/"
            "NotoSansArabic-Bold.ttf"
        )

    # FFmpeg drawtext escaping
    safe_text = (
        screen_text
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\")
        .replace("]", "\")
    )

    vf = (
        "scale=720:1280:"
        "force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        "fps=30,"
        f"drawtext=fontfile='{font}':"
        f"text='{safe_text}':"
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

    logger.info(
        "Running FFmpeg..."
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        logger.error(
            "FFmpeg failed:\n%s",
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "FFmpeg failed."
        )

    logger.info(
        "Scene processed successfully."
    )


# ============================================================
# CONCAT VIDEOS
# ============================================================

def concat_videos(
    videos,
    output_path: Path
):

    concat_file = (
        WORK_DIR /
        f"concat_{uuid.uuid4().hex}.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in videos:

            path = str(
                video.resolve()
            )

            f.write(
                "file '"
                + path.replace(
                    "'",
                    "'\\''"
                )
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
            "Concat failed:\n%s",
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "Failed to combine scenes."
        )

    logger.info(
        "Scenes combined successfully."
    )

    return output_path


# ============================================================
# MAKE 60 SECOND VIDEO
# ============================================================

def make_one_minute_video(
    source_video: Path,
    output_video: Path
):

    logger.info(
        "Extending final video to 60 seconds..."
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-stream_loop",
        "-1",

        "-i",
        str(source_video),

        "-t",
        str(FINAL_SECONDS),

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
            "Final video error:\n%s",
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "Could not create final video."
        )

    logger.info(
        "60 second video created."
    )

    return output_video


# ============================================================
# COMPLETE VIDEO JOB
# ============================================================

def create_complete_video(
    story_text: str
):

    job_id = uuid.uuid4().hex

    job_dir = (
        WORK_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STARTING VIDEO JOB: %s",
        job_id
    )

    # ----------------------------------------
    # 1. Create story scenes
    # ----------------------------------------

    story = create_story(
        story_text
    )

    title = story.get(
        "title",
        "Abosaraj AI"
    )

    scenes = story["scenes"][
        :AI_SCENES
    ]

    logger.info(
        "Story contains %d scenes.",
        len(scenes)
    )

    processed_videos = []

    # ----------------------------------------
    # 2. Generate scenes
    # ----------------------------------------

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        logger.info(
            "========== SCENE %d/%d ==========",
            index,
            len(scenes)
        )

        raw_video = (
            job_dir /
            f"scene_{index}_ai.mp4"
        )

        voice_file = (
            job_dir /
            f"scene_{index}_voice.mp3"
        )

        processed_video = (
            job_dir /
            f"scene_{index}_final.mp4"
        )

        # AI moving video
        generate_ai_clip(
            scene["video_prompt"],
            raw_video
        )

        # Arabic voice
        generate_voice_sync(
            scene["narration"],
            voice_file
        )

        # Captions + 9:16
        process_scene(
            raw_video,
            voice_file,
            processed_video,
            scene["screen_text"]
        )

        processed_videos.append(
            processed_video
        )

    # ----------------------------------------
    # 3. Combine scenes
    # ----------------------------------------

    combined = (
        job_dir /
        "combined.mp4"
    )

    concat_videos(
        processed_videos,
        combined
    )

    # ----------------------------------------
    # 4. Extend to 60 seconds
    # ----------------------------------------

    final_video = (
        job_dir /
        "Abosaraj_Final.mp4"
    )

    make_one_minute_video(
        combined,
        final_video
    )

    logger.info(
        "======================================"
    )

    logger.info(
        "FINAL VIDEO READY: %s",
        final_video
    )

    logger.info(
        "======================================"
    )

    return final_video, title


# ============================================================
# TELEGRAM /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "🎬 أهلاً في Abosaraj AI\n\n"
        "ابعتلي قصة، وأنا أحولها إلى:\n\n"
        "🎥 فيديو AI متحرك\n"
        "🎙️ صوت عربي رجالي\n"
        "📝 ترجمة عربية\n"
        "📱 مقاس 9:16\n\n"
        "⏳ التوليد ممكن يأخذ عدة دقائق."
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

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
            "اكتب قصة أطول شوي حتى أقدر أعمل فيديو."
        )

        return

    status = await update.message.reply_text(
        "🎬 استلمت القصة.\n\n"
        "🧠 أجهز المشاهد...\n"
        "🎥 بعدها أبدأ توليد الفيديو الحقيقي بالـ AI.\n\n"
        "⏳ لا تغلق المحادثة."
    )

    try:

        loop = asyncio.get_running_loop()

        final_video, title = (
            await loop.run_in_executor(
                None,
                create_complete_video,
                text
            )
        )

        await status.edit_text(
            "✅ الفيديو خلص!\n\n"
            f"🎬 {title}\n\n"
            "⬆️ جاري إرسال الفيديو..."
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

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

        if len(error_text) > 3500:
            error_text = error_text[-3500:]

        await status.edit_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            "التفاصيل:\n"
            + error_text
        )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "======================================"
    )

    logger.info(
        "ABOSARAJ STARTING"
    )

    logger.info(
        "======================================"
    )

    # Environment
    check_environment()

    logger.info(
        "Environment check passed."
    )

    # Flask health server
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    logger.info(
        "Flask health server started."
    )

    # Telegram application
    logger.info(
        "Creating Telegram application..."
    )

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
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info(
        "Telegram handlers registered."
    )

    logger.info(
        "Telegram bot starting polling..."
    )

    # IMPORTANT:
    # This keeps the Render process alive.
    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception:

        logger.exception(
            "FATAL APPLICATION ERROR"
        )

        raise
