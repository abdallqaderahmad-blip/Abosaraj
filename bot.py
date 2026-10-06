import os
import re
import json
import uuid
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
import edge_tts
from flask import Flask
from groq import Groq
from gradio_client import Client


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
HF_TOKEN = os.getenv("HF_TOKEN")

HF_SPACE = "Lightricks/ltx-video-distilled"

WORK_DIR = Path("/tmp/abosaraj")
WORK_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_WIDTH = 704
VIDEO_HEIGHT = 512

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280

FPS = 30

# كل لقطة AI
CLIP_SECONDS = 5.0

# عدد اللقطات AI
AI_SCENES = 4

# الناتج النهائي
FINAL_SECONDS = 60

VOICE = "ar-SA-HamedNeural"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("abosaraj")

executor = ThreadPoolExecutor(max_workers=2)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj V2 is alive"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Abosaraj V2"
    }


def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# =========================================================
# CHECK ENV
# =========================================================

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


# =========================================================
# GROQ
# =========================================================

groq_client = None


def get_groq():

    global groq_client

    if groq_client is None:
        groq_client = Groq(
            api_key=GROQ_API_KEY
        )

    return groq_client


def create_scenes(story):

    prompt = f"""
You are a professional Arabic short-video director.

Turn the user's story into exactly 4 cinematic scenes.

The final video will be around 60 seconds.

Return ONLY valid JSON.

Format:

{{
  "title": "short Arabic title",
  "hook": "very short Arabic hook",
  "scenes": [
    {{
      "narration": "Arabic narration",
      "screen_text": "short Arabic caption",
      "video_prompt": "English cinematic video prompt"
    }}
  ]
}}

Rules:

- Exactly 4 scenes.
- Arabic narration.
- Arabic screen_text.
- video_prompt MUST be English.
- Make the video prompts describe REAL MOTION.
- Do NOT ask for text inside the generated video.
- Cinematic realistic style.
- Vertical-video composition.
- Dark dramatic lighting when suitable.
- Each scene should be visually different.
- Avoid static images.
- Describe camera movement and subject movement.
- The story must remain coherent.
- Make narration suitable for approximately 50-60 seconds total.

USER STORY:

{story}
"""

    client = get_groq()

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        messages=[
            {
                "role": "system",
                "content": "Return valid JSON only."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()

    # تنظيف Markdown لو رجع ```json
    content = re.sub(
        r"^```json\s*",
        "",
        content,
        flags=re.IGNORECASE
    )

    content = re.sub(
        r"\s*```$",
        "",
        content
    )

    data = json.loads(content)

    scenes = data.get("scenes", [])

    if len(scenes) != 4:
        raise RuntimeError(
            f"Groq returned {len(scenes)} scenes instead of 4."
        )

    return data


# =========================================================
# HF CLIENT
# =========================================================

hf_client = None


def get_hf_client():

    global hf_client

    if hf_client is None:

        logger.info(
            "Connecting to Hugging Face Space: %s",
            HF_SPACE
        )

        hf_client = Client(
            HF_SPACE,
            hf_token=HF_TOKEN
        )

    return hf_client


# =========================================================
# VIDEO GENERATION
# =========================================================

def generate_ai_clip(
    prompt,
    output_path,
    seed
):

    client = get_hf_client()

    negative_prompt = (
        "worst quality, blurry, distorted, "
        "jittery motion, inconsistent anatomy, "
        "duplicate objects, text, subtitles, watermark"
    )

    logger.info(
        "Generating AI video clip..."
    )

    # Current LTX Space uses:
    #
    # prompt
    # negative_prompt
    # input_image
    # input_video
    # height
    # width
    # mode
    # duration
    # frames_to_use
    # seed
    # randomize_seed
    # guidance
    # improve_texture
    #
    # We try the current API name first.
    # A fallback is kept because HF/Gradio can expose
    # the endpoint with or without the leading slash.

    args = [
        prompt,
        negative_prompt,
        None,
        None,
        VIDEO_HEIGHT,
        VIDEO_WIDTH,
        "text-to-video",
        CLIP_SECONDS,
        9,
        int(seed),
        False,
        3.0,
        False
    ]

    result = None
    last_error = None

    for api_name in [
        "text_to_video",
        "/text_to_video"
    ]:

        try:

            logger.info(
                "Trying Hugging Face API: %s",
                api_name
            )

            result = client.predict(
                *args,
                api_name=api_name
            )

            if result:
                break

        except Exception as e:

            last_error = e

            logger.warning(
                "API %s failed: %s",
                api_name,
                e
            )

    if not result:
        raise RuntimeError(
            "Hugging Face video generation failed: "
            + str(last_error)
        )

    # Gradio may return a filepath or dict
    video_file = None

    if isinstance(result, str):
        video_file = result

    elif isinstance(result, dict):

        video_file = (
            result.get("video")
            or result.get("path")
            or result.get("value")
        )

    elif isinstance(result, (list, tuple)):

        for item in result:

            if isinstance(item, str):
                video_file = item
                break

            if isinstance(item, dict):

                video_file = (
                    item.get("video")
                    or item.get("path")
                    or item.get("value")
                )

                if video_file:
                    break

    if not video_file:
        raise RuntimeError(
            "Hugging Face returned an unknown video result."
        )

    video_file = str(video_file)

    logger.info(
        "HF video result: %s",
        video_file
    )

    # If local path
    if os.path.exists(video_file):

        subprocess.run(
            [
                "cp",
                video_file,
                str(output_path)
            ],
            check=True
        )

    else:

        # Sometimes Gradio returns a URL
        response = requests.get(
            video_file,
            timeout=180
        )

        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

    if not output_path.exists():
        raise RuntimeError(
            "AI video file was not created."
        )

    return str(output_path)


# =========================================================
# TEXT TO SPEECH
# =========================================================

async def generate_voice_async(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="+0%",
        volume="+0%"
    )

    await communicate.save(
        str(output_path)
    )


def generate_voice(
    text,
    output_path
):

    asyncio.run(
        generate_voice_async(
            text,
            output_path
        )
    )

    if not output_path.exists():
        raise RuntimeError(
            "Voice file was not created."
        )

    return str(output_path)


# =========================================================
# FFPROBE
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
            str(path)
        ],
        capture_output=True,
        text=True
    )

    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# =========================================================
# MAKE VERTICAL CLIP
# =========================================================

def prepare_clip(
    input_video,
    output_video
):

    filter_complex = (
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        "setsar=1,"
        "fps=30"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-vf",
            filter_complex,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            str(output_video)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return str(output_video)


# =========================================================
# CONCAT CLIPS
# =========================================================

def concat_clips(
    clips,
    output_path
):

    concat_file = WORK_DIR / (
        f"concat_{uuid.uuid4().hex}.txt"
    )

    with open(concat_file, "w", encoding="utf-8") as f:

        for clip in clips:

            safe_path = str(clip).replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    subprocess.run(
        [
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
            str(output_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return str(output_path)


# =========================================================
# CREATE CAPTIONS ASSUMPTION
# =========================================================

def create_caption_file(
    narration,
    duration,
    output_path
):

    words = narration.split()

    if not words:
        return None

    # تقسيم النص إلى جمل قصيرة
    chunks = []

    current = []

    for word in words:

        current.append(word)

        if (
            len(current) >= 6
            or word.endswith(("،", ".", "!", "؟", ":"))
        ):

            chunks.append(
                " ".join(current)
            )

            current = []

    if current:
        chunks.append(
            " ".join(current)
        )

    if not chunks:
        return None

    chunk_duration = duration / len(chunks)

    def ass_time(seconds):

        h = int(seconds // 3600)

        m = int(
            (seconds % 3600) // 60
        )

        s = int(seconds % 60)

        cs = int(
            (seconds - int(seconds)) * 100
        )

        return (
            f"{h}:{m:02d}:{s:02d}.{cs:02d}"
        )

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "[Script Info]\n"
        )

        f.write(
            "ScriptType: v4.00+\n\n"
        )

        f.write(
            "[V4+ Styles]\n"
        )

        f.write(
            "Format: Name, Fontname, Fontsize, "
            "PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )

        f.write(
            "Style: Default,Noto Sans Arabic,28,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,"
            "&H80000000,1,0,0,0,100,100,0,0,"
            "1,2,1,2,40,40,100,1\n\n"
        )

        f.write(
            "[Events]\n"
        )

        f.write(
            "Format: Layer, Start, End, Style, "
            "Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
        )

        for i, chunk in enumerate(chunks):

            start = i * chunk_duration

            end = min(
                duration,
                (i + 1) * chunk_duration
            )

            # ASS يحتاج escape
            text = chunk.replace(
                "{",
                "\\{"
            ).replace(
                "}",
                "\\}"
            )

            f.write(
                f"Dialogue: 0,"
                f"{ass_time(start)},"
                f"{ass_time(end)},"
                f"Default,,0,0,0,,"
                f"{text}\n"
            )

    return str(output_path)


# =========================================================
# FINAL VIDEO
# =========================================================

def make_final_video(
    clips,
    narration,
    output_path
):

    combined = (
        WORK_DIR /
        f"combined_{uuid.uuid4().hex}.mp4"
    )

    concat_clips(
        clips,
        combined
    )

    combined_duration = get_duration(
        combined
    )

    if combined_duration <= 0:
        raise RuntimeError(
            "Could not read combined video duration."
        )

    voice_file = (
        WORK_DIR /
        f"voice_{uuid.uuid4().hex}.mp3"
    )

    generate_voice(
        narration,
        voice_file
    )

    voice_duration = get_duration(
        voice_file
    )

    target_duration = max(
        FINAL_SECONDS,
        voice_duration + 2
    )

    captions = (
        WORK_DIR /
        f"captions_{uuid.uuid4().hex}.ass"
    )

    create_caption_file(
        narration,
        voice_duration,
        captions
    )

    # نكرر الفيديوهات المتحركة حتى طول الصوت
    video_looped = (
        WORK_DIR /
        f"looped_{uuid.uuid4().hex}.mp4"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(combined),
            "-t",
            str(target_duration),
            "-vf",
            (
                f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
                "force_original_aspect_ratio=increase,"
                f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
                "setsar=1,"
                "fps=30"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            str(video_looped)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # النصوص + الصوت
    subtitles_path = str(captions)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_looped),
            "-i",
            str(voice_file),
            "-vf",
            f"ass={subtitles_path}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(target_duration),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "25",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return str(output_path)


# =========================================================
# PROCESS STORY
# =========================================================

def process_story(story):

    job_id = uuid.uuid4().hex

    job_dir = (
        WORK_DIR / job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "Starting job %s",
        job_id
    )

    # -------------------------
    # 1. AI STORY DIRECTOR
    # -------------------------

    data = create_scenes(
        story
    )

    title = data.get(
        "title",
        "قصة جديدة"
    )

    scenes = data["scenes"]

    # -------------------------
    # 2. NARRATION
    # -------------------------

    narration_parts = []

    for scene in scenes:

        narration = scene.get(
            "narration",
            ""
        ).strip()

        if narration:
            narration_parts.append(
                narration
            )

    narration = " ".join(
        narration_parts
    )

    # -------------------------
    # 3. GENERATE AI VIDEOS
    # -------------------------

    clips = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        prompt = scene.get(
            "video_prompt",
            ""
        )

        if not prompt:
            prompt = (
                "cinematic realistic scene, "
                "dramatic lighting, "
                "natural human movement, "
                "slow camera movement"
            )

        raw_clip = (
            job_dir /
            f"scene_{index}_raw.mp4"
        )

        final_clip = (
            job_dir /
            f"scene_{index}.mp4"
        )

        logger.info(
            "Generating scene %s/4",
            index
        )

        generate_ai_clip(
            prompt,
            raw_clip,
            seed=1000 + index
        )

        prepare_clip(
            raw_clip,
            final_clip
        )

        clips.append(
            final_clip
        )

    # -------------------------
    # 4. FINAL EDIT
    # -------------------------

    final_video = (
        job_dir /
        "final.mp4"
    )

    make_final_video(
        clips,
        narration,
        final_video
    )

    logger.info(
        "Job %s completed",
        job_id
    )

    return {
        "video": str(final_video),
        "title": title,
        "job_id": job_id
    }


# =========================================================
# TELEGRAM
# =========================================================

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً في Abosaraj V2\n\n"
        "ابعتلي قصة، وأنا أحولها إلى:\n\n"
        "🎥 فيديو متحرك\n"
        "🎙️ صوت عربي\n"
        "📝 كتابة عربية\n"
        "🎞️ مونتاج 9:16\n"
        "📱 جاهز للـReels وShorts\n\n"
        "⏳ التوليد يحتاج بعض الوقت."
    )


async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    story = (
        update.message.text
        or ""
    ).strip()

    if not story:
        return

    if len(story) < 20:

        await update.message.reply_text(
            "اكتبلي قصة أطول شوي 😄"
        )

        return

    await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 عم ببني المشاهد...\n"
        "🎥 عم أعمل لقطات فيديو حقيقية...\n"
        "🎙️ بعدها الصوت...\n"
        "📝 وبعدها الكتابة والمونتاج.\n\n"
        "⏳ استنى شوي..."
    )

    loop = asyncio.get_running_loop()

    try:

        result = await loop.run_in_executor(
            executor,
            process_story,
            story
        )

        video_path = result["video"]

        title = result["title"]

        await update.message.reply_text(
            f"✅ خلص الفيديو!\n\n"
            f"🎬 {title}\n"
            f"📱 9:16\n"
            f"🎙️ صوت عربي\n"
            f"📝 Captions\n"
            f"🎞️ مونتاج"
        )

        with open(
            video_path,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🔥 جاهز للنشر\n"
                    "#قصص #رعب #غموض #shorts #reels"
                ),
                supports_streaming=True
            )

    except Exception as e:

        logger.exception(
            "Story processing failed"
        )

        error_text = str(e)

        # اختصار أخطاء Hugging Face الطويلة
        if len(error_text) > 1500:
            error_text = (
                error_text[:1500]
                + "\n..."
            )

        await update.message.reply_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            "التفاصيل:\n"
            + error_text
        )


# =========================================================
# MAIN
# =========================================================

def main():

    check_environment()

    logger.info(
        "Starting Abosaraj V2..."
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

    logger.info(
        "Telegram bot started!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    import threading

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    try:
        main()

    except Exception:

        logger.exception(
            "FATAL APPLICATION ERROR"
        )

        raise
