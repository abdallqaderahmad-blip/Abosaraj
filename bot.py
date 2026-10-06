import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
import edge_tts
import fal_client

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

FAL_KEY = os.getenv("FAL_KEY")

# Wan 3 Text-to-Video
FAL_MODEL = "alibaba/wan-3.0/text-to-video"

# كل لقطة AI
CLIP_SECONDS = 5

# عدد اللقطات
AI_SCENES = 4

# مدة الفيديو النهائي
FINAL_SECONDS = 60

# الفيديو النهائي
FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# صوت عربي
VOICE = "ar-SA-HamedNeural"

# ملفات العمل
WORK_DIR = Path("/tmp/abosaraj")
WORK_DIR.mkdir(
    parents=True,
    exist_ok=True
)

# لا نشغل أكثر من فيديو بنفس الوقت
executor = ThreadPoolExecutor(
    max_workers=1
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Abosaraj V3 is alive"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "version": "3",
        "video_engine": "Wan 3"
    }


def run_flask():
    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# =========================================================
# ENVIRONMENT
# =========================================================

def check_environment():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    if not FAL_KEY:
        missing.append("FAL_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    # fal-client يستخدم FAL_KEY
    os.environ["FAL_KEY"] = FAL_KEY


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


# =========================================================
# AI STORY DIRECTOR
# =========================================================

def create_scenes(story):

    prompt = f"""
أنت مخرج محترف لفيديوهات TikTok وInstagram Reels وYouTube Shorts.

حوّل القصة التالية إلى فيديو قصير سينمائي.

أريد بالضبط 4 مشاهد.

كل مشهد مدته حوالي 5 ثواني.

الفيديو النهائي سيكون حوالي دقيقة، لذلك اجعل المشاهد الأربعة قوية ويمكن تكرار اللقطات أثناء المونتاج بدون أن تبدو كصور ثابتة.

أرجع JSON فقط.

الشكل:

{{
  "title": "عنوان عربي قصير وقوي",
  "hook": "جملة افتتاحية تشد المشاهد",
  "scenes": [
    {{
      "narration": "جملة أو جمل عربية للمشهد",
      "screen_text": "كلمات عربية قصيرة",
      "video_prompt": "English cinematic video prompt"
    }},
    {{
      "narration": "جملة أو جمل عربية للمشهد",
      "screen_text": "كلمات عربية قصيرة",
      "video_prompt": "English cinematic video prompt"
    }},
    {{
      "narration": "جملة أو جمل عربية للمشهد",
      "screen_text": "كلمات عربية قصيرة",
      "video_prompt": "English cinematic video prompt"
    }},
    {{
      "narration": "جملة أو جمل عربية للمشهد",
      "screen_text": "كلمات عربية قصيرة",
      "video_prompt": "English cinematic video prompt"
    }}
  ]
}}

القواعد المهمة:

- بالضبط 4 مشاهد.
- narration باللغة العربية.
- video_prompt باللغة الإنجليزية فقط.
- لا تضع أي كتابة أو subtitles داخل video_prompt.
- الفيديو يجب أن يكون REALISTIC وCINEMATIC.
- يجب أن يكون هناك حركة حقيقية في كل مشهد.
- اذكر حركة الشخص أو الأشياء.
- اذكر حركة الكاميرا.
- لا تجعل المشهد يبدو كصورة ثابتة.
- كل مشهد يجب أن يكون مختلفًا بصريًا.
- حافظ على نفس الشخصيات والمنطق البصري للقصة قدر الإمكان.
- استخدم إضاءة سينمائية.
- استخدم عمق ميدان واقعي.
- استخدم حركة كاميرا مثل tracking shot أو handheld أو slow push-in أو orbit عندما تناسب المشهد.
- لا تستخدم الدماء أو العنف الدموي الصريح.
- مناسب لفيديوهات الرعب والغموض والقصص المثيرة.
- اجعل القصة مشوقة من أول ثانية.
- narration الكامل للمشاهد الأربعة يجب أن يكون تقريبًا 100 إلى 140 كلمة.
- screen_text قصير جدًا.

القصة:

{story}
"""

    client = get_groq()

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.85,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert Arabic "
                    "short-form video director. "
                    "Return valid JSON only."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = (
        response.choices[0]
        .message.content
        .strip()
    )

    # إزالة ```json إذا أرسلها Groq
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

    try:
        data = json.loads(content)

    except json.JSONDecodeError as e:

        logger.error(
            "Groq returned invalid JSON: %s",
            content
        )

        raise RuntimeError(
            "Groq returned invalid JSON: "
            + str(e)
        )

    scenes = data.get(
        "scenes",
        []
    )

    if len(scenes) != AI_SCENES:

        raise RuntimeError(
            f"Groq returned {len(scenes)} "
            f"scenes instead of {AI_SCENES}."
        )

    return data


# =========================================================
# WAN 3 VIDEO GENERATION
# =========================================================

def generate_ai_clip(
    prompt,
    output_path,
    seed
):

    logger.info(
        "Starting Wan 3 generation..."
    )

    try:

        result = fal_client.subscribe(
            FAL_MODEL,
            arguments={
                "prompt": prompt,

                "resolution": "720p",

                "aspect_ratio": "9:16",

                "duration": CLIP_SECONDS,

                "audio": True,

                "enable_prompt_expansion": True,

                "seed": int(seed)
            }
        )

    except Exception as e:

        error = str(e)

        logger.exception(
            "Wan 3 generation failed"
        )

        if (
            "402" in error
            or "credit" in error.lower()
            or "insufficient" in error.lower()
            or "balance" in error.lower()
        ):

            raise RuntimeError(
                "❌ fal.ai رفض التوليد بسبب الرصيد "
                "أو عدم وجود رصيد API كافي."
            )

        if (
            "401" in error
            or "unauthorized" in error.lower()
            or "authentication" in error.lower()
        ):

            raise RuntimeError(
                "❌ FAL_KEY غير صحيح أو غير موجود."
            )

        raise RuntimeError(
            "❌ Wan 3 generation failed: "
            + error
        )

    logger.info(
        "Wan 3 result received."
    )

    # =====================================================
    # استخراج رابط الفيديو
    # =====================================================

    video_url = None

    if isinstance(result, dict):

        video = result.get(
            "video"
        )

        if isinstance(video, dict):

            video_url = (
                video.get("url")
                or video.get("video_url")
            )

        elif isinstance(video, str):

            video_url = video

        if not video_url:

            video_url = (
                result.get("video_url")
                or result.get("url")
            )

    if not video_url:

        logger.error(
            "Unexpected Wan response: %s",
            result
        )

        raise RuntimeError(
            "Wan 3 returned no video URL."
        )

    # =====================================================
    # تحميل الفيديو
    # =====================================================

    logger.info(
        "Downloading Wan 3 video..."
    )

    response = requests.get(
        video_url,
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

    if not output_path.exists():

        raise RuntimeError(
            "Wan 3 video file was not created."
        )

    file_size = (
        output_path.stat().st_size
    )

    if file_size < 1000:

        raise RuntimeError(
            "Downloaded video file is too small."
        )

    logger.info(
        "Wan 3 clip saved: %s bytes",
        file_size
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
        rate="-3%",
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
            "Arabic voice was not created."
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

        return float(
            result.stdout.strip()
        )

    except Exception:

        return 0.0


# =========================================================
# PREPARE CLIP
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
        f"fps={FPS}"
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

    concat_file = (
        WORK_DIR /
        f"concat_{uuid.uuid4().hex}.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for clip in clips:

            safe_path = (
                str(clip)
                .replace("'", "'\\''")
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
# SUBTITLES
# =========================================================

def create_caption_file(
    narration,
    duration,
    output_path
):

    words = narration.split()

    if not words:

        return None

    chunks = []

    current = []

    for word in words:

        current.append(word)

        if (
            len(current) >= 6
            or word.endswith(
                (
                    "،",
                    ".",
                    "!",
                    "؟",
                    ":"
                )
            )
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

    chunk_duration = (
        duration / len(chunks)
    )

    def ass_time(seconds):

        h = int(
            seconds // 3600
        )

        m = int(
            (seconds % 3600) // 60
        )

        s = int(
            seconds % 60
        )

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
            "ScriptType: v4.00+\n"
        )

        f.write(
            "PlayResX: 720\n"
        )

        f.write(
            "PlayResY: 1280\n\n"
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
            "Style: Default,Noto Sans Arabic,30,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,"
            "&H90000000,1,0,0,0,100,100,0,0,"
            "1,3,1,2,40,40,100,1\n\n"
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

            start = (
                i * chunk_duration
            )

            end = min(
                duration,
                (i + 1) * chunk_duration
            )

            text = (
                chunk
                .replace(
                    "{",
                    "\\{"
                )
                .replace(
                    "}",
                    "\\}"
                )
            )

            f.write(
                "Dialogue: 0,"
                f"{ass_time(start)},"
                f"{ass_time(end)},"
                "Default,,0,0,0,,"
                f"{text}\n"
            )

    return str(output_path)


# =========================================================
# ESCAPE DRAW TEXT
# =========================================================

def escape_drawtext(text):

    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\")
        .replace("]", "\")
    )


# =========================================================
# FINAL VIDEO
# =========================================================

def make_final_video(
    clips,
    narration,
    title,
    output_path
):

    # -----------------------------------------------------
    # 1. دمج اللقطات
    # -----------------------------------------------------

    combined = (
        output_path.parent /
        "combined.mp4"
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

    # -----------------------------------------------------
    # 2. الصوت
    # -----------------------------------------------------

    voice_file = (
        output_path.parent /
        "voice.mp3"
    )

    generate_voice(
        narration,
        voice_file
    )

    voice_duration = get_duration(
        voice_file
    )

    if voice_duration <= 0:

        raise RuntimeError(
            "Could not read voice duration."
        )

    # -----------------------------------------------------
    # 3. مدة الفيديو
    # -----------------------------------------------------

    target_duration = max(
        FINAL_SECONDS,
        voice_duration + 1
    )

    # -----------------------------------------------------
    # 4. Captions
    # -----------------------------------------------------

    captions = (
        output_path.parent /
        "captions.ass"
    )

    create_caption_file(
        narration,
        voice_duration,
        captions
    )

    # -----------------------------------------------------
    # 5. تمديد الفيديو
    # -----------------------------------------------------

    video_looped = (
        output_path.parent /
        "looped.mp4"
    )

    video_filter = (
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        "setsar=1,"
        f"fps={FPS}"
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
            video_filter,

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

    # -----------------------------------------------------
    # 6. العنوان
    # -----------------------------------------------------

    safe_title = escape_drawtext(
        title
    )

    title_filter = (
        "drawtext="
        "fontfile=/usr/share/fonts/truetype/"
        "noto/NotoSansArabic-Regular.ttf:"
        f"text='{safe_title}':"
        "fontcolor=white:"
        "fontsize=38:"
        "borderw=3:"
        "bordercolor=black:"
        "x=(w-text_w)/2:"
        "y=90:"
        "enable='between(t,0,3)'"
    )

    # -----------------------------------------------------
    # 7. Captions + title + الصوت
    # -----------------------------------------------------

    subtitle_filter = (
        f"ass={captions}"
    )

    final_filter = (
        f"{subtitle_filter},"
        f"{title_filter}"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",

            "-i",
            str(video_looped),

            "-i",
            str(voice_file),

            "-vf",
            final_filter,

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

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            str(output_path)
        ],
        check=True,

        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if not output_path.exists():

        raise RuntimeError(
            "Final video was not created."
        )

    return str(output_path)


# =========================================================
# PROCESS STORY
# =========================================================

def process_story(story):

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
        "Starting job %s",
        job_id
    )

    # -----------------------------------------------------
    # 1. Groq
    # -----------------------------------------------------

    data = create_scenes(
        story
    )

    title = data.get(
        "title",
        "قصة جديدة"
    )

    scenes = data["scenes"]

    # -----------------------------------------------------
    # 2. Narration
    # -----------------------------------------------------

    narration_parts = []

    for scene in scenes:

        narration = (
            scene.get(
                "narration",
                ""
            )
            .strip()
        )

        if narration:

            narration_parts.append(
                narration
            )

    narration = " ".join(
        narration_parts
    )

    if len(narration) < 30:

        raise RuntimeError(
            "Narration generated by Groq is too short."
        )

    # -----------------------------------------------------
    # 3. Generate 4 AI clips
    # -----------------------------------------------------

    clips = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        prompt = (
            scene.get(
                "video_prompt",
                ""
            )
            .strip()
        )

        if not prompt:

            prompt = (
                "A realistic cinematic "
                "mysterious night scene, "
                "a person walking slowly "
                "through a dark environment, "
                "wind moving clothing and trees, "
                "subtle handheld camera movement, "
                "slow cinematic push-in, "
                "realistic lighting, "
                "photorealistic motion, "
                "vertical 9:16 composition, "
                "no text, no subtitles, no watermark."
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
            "Generating Wan 3 scene %s/%s",
            index,
            AI_SCENES
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

    # -----------------------------------------------------
    # 4. Final montage
    # -----------------------------------------------------

    final_video = (
        job_dir /
        "final.mp4"
    )

    make_final_video(
        clips,
        narration,
        title,
        final_video
    )

    logger.info(
        "Job %s completed successfully",
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

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً في Abosaraj V3\n\n"
        "ابعتلي أي قصة وأنا أحولها إلى:\n\n"
        "🎥 فيديو AI متحرك\n"
        "🎙️ صوت عربي\n"
        "📝 Captions عربية\n"
        "🎞️ مونتاج\n"
        "📱 9:16\n"
        "🔥 جاهز للـReels وShorts\n\n"
        "ابعت القصة 👇"
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
            "اكتب قصة أطول شوي حتى أقدر "
            "أعمل منها فيديو قوي 🎬"
        )

        return

    await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 عم ببني السيناريو...\n"
        "🎥 عم أعمل 4 لقطات AI حقيقية...\n"
        "🎙️ بعدها الصوت العربي...\n"
        "📝 بعدها الـCaptions...\n"
        "🎞️ وبالأخير المونتاج 9:16.\n\n"
        "⏳ استنى شوي وما تبعت قصة ثانية."
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
            "✅ خلص الفيديو!\n\n"
            f"🎬 {title}\n"
            "🎥 AI Video\n"
            "🎙️ صوت عربي\n"
            "📝 Captions\n"
            "📱 9:16"
        )

        with open(
            video_path,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,

                caption=(
                    "🔥 جاهز للنشر\n\n"
                    "#قصص #رعب #غموض "
                    "#shorts #reels"
                ),

                supports_streaming=True
            )

    except Exception as e:

        logger.exception(
            "Story processing failed"
        )

        error_text = str(e)

        if len(error_text) > 1800:

            error_text = (
                error_text[:1800]
                + "\n..."
            )

        await update.message.reply_text(
            "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
            + error_text
        )


# =========================================================
# MAIN
# =========================================================

def main():

    check_environment()

    logger.info(
        "Starting Abosaraj V3..."
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
