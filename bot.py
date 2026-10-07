import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading

from pathlib import Path
from datetime import datetime, timezone
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
    "llama-3.3-70b-versatile"
)

FAL_KEY = os.getenv("FAL_KEY")

FAL_MODEL = os.getenv(
    "FAL_MODEL",
    "fal-ai/hunyuan-image/v3/text-to-image"
)


# =========================================================
# VIDEO SETTINGS
# =========================================================

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# =========================================================
# مهم:
#
# أول اختبار = 2 مشاهد فقط
#
# بعد نجاح أول فيديو:
#
# SCENE_COUNT = 8
#
# =========================================================

SCENE_COUNT = 2

MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 120

VOICE = "ar-SA-HamedNeural"


# =========================================================
# WORK DIRECTORY
# =========================================================

WORK_DIR = Path("/tmp/abosaraj")

WORK_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# THREADING
# =========================================================

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

    return "Abosaraj Story Video Engine is alive"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "version": "story-images-v1",
        "video_engine": "Hunyuan Image + FFmpeg",
        "scenes": SCENE_COUNT,
        "width": FINAL_WIDTH,
        "height": FINAL_HEIGHT
    }


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

    os.environ["FAL_KEY"] = FAL_KEY

    logger.info(
        "FAL model: %s",
        FAL_MODEL
    )

    logger.info(
        "Groq model: %s",
        GROQ_MODEL
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


# =========================================================
# STORY DIRECTOR
# =========================================================

def create_story_plan(story):

    prompt = f"""
أنت مخرج محترف لفيديوهات القصص القصيرة
المخصصة لـ TikTok وReels وYouTube Shorts.

حوّل القصة التي سأعطيك إياها إلى فيديو
سينمائي مدته بين دقيقة ودقيقتين.

أريد بالضبط {SCENE_COUNT} مشاهد.

كل مشهد سيصبح صورة سينمائية واحدة،
ثم سيتم تحريك الصورة بواسطة FFmpeg
بحركة Zoom وPan سينمائية.

أريد JSON فقط.

الشكل:

{{
  "title": "عنوان عربي قصير ومثير",
  "hook": "Hook عربي قوي",
  "narration": "نص الراوي العربي الكامل",
  "scenes": [
    {{
      "scene": 1,
      "duration_hint": 8,
      "image_prompt": "English cinematic image prompt"
    }}
  ]
}}

القواعد:

- narration بين 150 و260 كلمة تقريبًا.
- يبدأ النص مباشرة بـ Hook قوي.
- لا تضع مقدمة فارغة.
- القصة يجب أن تتطور من البداية للنهاية.
- المشهد الأول يجب أن يجذب المشاهد فورًا.
- المشهد الأخير يجب أن يحتوي على نتيجة أو Twist
  أو سؤال قوي عندما يناسب القصة.
- كل مشهد يجب أن يضيف معلومة أو تطورًا جديدًا.
- لا تكرر نفس الصورة.
- لا تكرر نفس زاوية الكاميرا في كل المشاهد.

إذا ظهر شخص رئيسي:
حافظ على نفس العمر والجنس والملابس
والملامح والمظهر قدر الإمكان بين المشاهد.

استخدم:

realistic cinematic photography,
photorealistic,
cinematic lighting,
realistic human anatomy,
realistic facial expressions,
realistic environment,
dramatic atmosphere,
vertical composition.

كل image_prompt يجب أن يحتوي على:

- نوع اللقطة
- زاوية الكاميرا
- البيئة
- الإضاءة
- وضعية الشخصيات
- العناصر المهمة
- الإحساس الدرامي

ممنوع:

- text
- subtitles
- logos
- watermark
- Arabic writing inside image
- posters
- UI
- gore
- excessive blood

image_prompt باللغة الإنجليزية فقط.

اترك مساحة مناسبة أسفل الصورة للـcaptions.

القصة:

{story}
"""

    client = get_groq()

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional cinematic "
                    "short-form story director. "
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

    except Exception:

        logger.error(
            "Invalid Groq JSON: %s",
            content
        )

        raise RuntimeError(
            "Groq returned invalid JSON."
        )

    scenes = data.get(
        "scenes",
        []
    )

    if len(scenes) != SCENE_COUNT:

        raise RuntimeError(
            f"Groq returned {len(scenes)} scenes "
            f"instead of {SCENE_COUNT}."
        )

    narration = (
        data.get(
            "narration",
            ""
        )
        .strip()
    )

    if len(narration) < 100:

        raise RuntimeError(
            "Generated narration is too short."
        )

    title = (
        data.get(
            "title",
            "قصة جديدة"
        )
        .strip()
    )

    if not title:

        title = "قصة جديدة"

    data["title"] = title

    return data


# =========================================================
# FAL HUNYUAN IMAGE
# =========================================================

def generate_image(
    prompt,
    output_path,
    seed
):

    logger.info(
        "Generating image using %s",
        FAL_MODEL
    )

    negative_prompt = (
        "text, subtitles, logo, watermark, "
        "signature, UI, poster, distorted face, "
        "bad anatomy, extra fingers, deformed hands, "
        "blurry, low quality, duplicate person, "
        "gore, excessive blood"
    )

    enhanced_prompt = (
        prompt
        + ", "
        "vertical 9:16 cinematic composition, "
        "photorealistic, highly detailed, "
        "realistic skin texture, "
        "realistic lighting, "
        "cinematic photography, "
        "no text, no subtitles, "
        "no logo, no watermark"
    )

    try:

        result = fal_client.subscribe(
            FAL_MODEL,

            arguments={
                "prompt": enhanced_prompt,

                "negative_prompt": negative_prompt,

                "image_size": {
                    "width": FINAL_WIDTH,
                    "height": FINAL_HEIGHT
                },

                "num_images": 1,

                "enable_prompt_expansion": True,

                "enable_safety_checker": True,

                "output_format": "jpeg",

                "seed": int(seed)
            }
        )

    except Exception as e:

        error = str(e)

        logger.exception(
            "Hunyuan image generation failed"
        )

        if (
            "402" in error
            or "credit" in error.lower()
            or "insufficient" in error.lower()
            or "balance" in error.lower()
        ):

            raise RuntimeError(
                "❌ رصيد fal.ai غير كافي لتوليد الصورة."
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
            "❌ خطأ Hunyuan:\n"
            + error
        )

    image_url = None

    if isinstance(
        result,
        dict
    ):

        images = result.get(
            "images",
            []
        )

        if images:

            first_image = images[0]

            if isinstance(
                first_image,
                dict
            ):

                image_url = first_image.get(
                    "url"
                )

    if not image_url:

        raise RuntimeError(
            "❌ Hunyuan لم يرجع رابط الصورة."
        )

    logger.info(
        "Downloading image..."
    )

    response = requests.get(
        image_url,
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
            "Image file was not created."
        )

    if output_path.stat().st_size < 5000:

        raise RuntimeError(
            "Downloaded image is invalid."
        )

    return str(output_path)


# =========================================================
# VOICE
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

    if output_path.stat().st_size < 1000:

        raise RuntimeError(
            "Arabic voice file is invalid."
        )

    return str(output_path)


# =========================================================
# DURATION
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
# IMAGE → CINEMATIC MOTION
# =========================================================

def create_motion_clip(
    image_path,
    output_path,
    duration,
    motion_type
):

    frames = max(
        int(duration * FPS),
        FPS
    )

    motion = motion_type % 4

    if motion == 0:

        zoom_expression = (
            "min(zoom+0.0008,1.18)"
        )

        x_expression = (
            "(iw-iw/zoom)/2"
        )

        y_expression = (
            "(ih-ih/zoom)/2"
        )

    elif motion == 1:

        zoom_expression = (
            "min(zoom+0.0006,1.14)"
        )

        x_expression = (
            f"(iw-iw/zoom)*"
            f"(on/{frames})"
        )

        y_expression = (
            "(ih-ih/zoom)/2"
        )

    elif motion == 2:

        zoom_expression = (
            "max(zoom-0.0005,1.0)"
        )

        x_expression = (
            "(iw-iw/zoom)*0.65"
        )

        y_expression = (
            "(ih-ih/zoom)*0.35"
        )

    else:

        zoom_expression = (
            "min(zoom+0.0007,1.16)"
        )

        x_expression = (
            "(iw-iw/zoom)*0.25"
        )

        y_expression = (
            "(ih-ih/zoom)*0.55"
        )

    zoom_filter = (
        "zoompan="
        f"z='{zoom_expression}':"
        f"x='{x_expression}':"
        f"y='{y_expression}':"
        f"d={frames}:"
        f"s={FINAL_WIDTH}x{FINAL_HEIGHT}:"
        f"fps={FPS}"
    )

    filter_chain = (
        "scale="
        f"{FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        "setsar=1,"
        + zoom_filter
    )

    subprocess.run(
        [
            "ffmpeg",
            "-y",

            "-loop",
            "1",

            "-i",
            str(image_path),

            "-vf",
            filter_chain,

            "-t",
            str(duration),

            "-an",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "25",

            "-pix_fmt",
            "yuv420p",

            "-r",
            str(FPS),

            str(output_path)
        ],
        check=True,

        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if not output_path.exists():

        raise RuntimeError(
            "Motion clip was not created."
        )

    return str(output_path)


# =========================================================
# CONCAT
# =========================================================

def concat_clips(
    clips,
    output_path
):

    concat_file = (
        output_path.parent /
        "concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for clip in clips:

            path = (
                str(clip)
                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{path}'\n"
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

    if not output_path.exists():

        raise RuntimeError(
            "Combined video was not created."
        )

    return str(output_path)


# =========================================================
# CAPTIONS
# =========================================================

def create_captions(
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
            len(current) >= 5
            or word.endswith(
                (
                    "،",
                    ".",
                    "!",
                    "؟",
                    ":",
                    "؛"
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

    part = (
        duration /
        len(chunks)
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

            start = i * part

            end = min(
                duration,
                (i + 1) * part
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
# DRAW TEXT ESCAPE
# =========================================================

def escape_drawtext(text):

    return (
        str(text)
        .replace(
            "\\",
            "\\\\"
        )
        .replace(
            ":",
            "\\:"
        )
        .replace(
            "'",
            "\\'"
        )
        .replace(
            ",",
            "\\,"
        )
        .replace(
            "[",
            "\\["
        )
        .replace(
            "]",
            "\\]"
        )
    )


# =========================================================
# FINAL VIDEO
# =========================================================

def make_final_video(
    clips,
    voice_file,
    narration,
    title,
    output_path
):

    # -----------------------------------------------------
    # Combined clips
    # -----------------------------------------------------

    combined = (
        output_path.parent /
        "combined.mp4"
    )

    concat_clips(
        clips,
        combined
    )

    # -----------------------------------------------------
    # Voice duration
    # -----------------------------------------------------

    voice_duration = get_duration(
        voice_file
    )

    if voice_duration <= 0:

        raise RuntimeError(
            "Could not read voice duration."
        )

    target_duration = max(
        MIN_VIDEO_SECONDS,
        voice_duration
    )

    target_duration = min(
        MAX_VIDEO_SECONDS,
        target_duration
    )

    logger.info(
        "Voice duration: %.2f",
        voice_duration
    )

    logger.info(
        "Target duration: %.2f",
        target_duration
    )

    # -----------------------------------------------------
    # Captions
    # -----------------------------------------------------

    caption_duration = min(
        voice_duration,
        target_duration
    )

    captions = (
        output_path.parent /
        "captions.ass"
    )

    create_captions(
        narration,
        caption_duration,
        captions
    )

    # -----------------------------------------------------
    # Title
    # -----------------------------------------------------

    safe_title = escape_drawtext(
        title
    )

    title_filter = (
        "drawtext="
        "fontfile=/usr/share/fonts/"
        "truetype/noto/"
        "NotoSansArabic-Regular.ttf:"
        f"text='{safe_title}':"
        "fontcolor=white:"
        "fontsize=38:"
        "borderw=3:"
        "bordercolor=black:"
        "x=(w-text_w)/2:"
        "y=90:"
        "enable='between(t,0,4)'"
    )

    subtitle_filter = (
        f"ass={captions}"
    )

    final_filter = (
        f"{subtitle_filter},"
        f"{title_filter}"
    )

    # -----------------------------------------------------
    # Loop visual clips
    # -----------------------------------------------------

    looped = (
        output_path.parent /
        "looped.mp4"
    )

    filter_video = (
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
            filter_video,

            "-an",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "24",

            "-pix_fmt",
            "yuv420p",

            str(looped)
        ],
        check=True,

        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # -----------------------------------------------------
    # Final render
    # -----------------------------------------------------

    subprocess.run(
        [
            "ffmpeg",
            "-y",

            "-i",
            str(looped),

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

    if output_path.stat().st_size < 10000:

        raise RuntimeError(
            "Final video file is invalid."
        )

    return str(output_path)


# =========================================================
# CREATE ONE VIDEO
# =========================================================

def create_one_video(
    story,
    generation_number
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
        "Starting job: %s",
        job_id
    )

    # =====================================================
    # 1. STORY PLAN
    # =====================================================

    logger.info(
        "Step 1/5 - Creating story plan..."
    )

    plan = create_story_plan(
        story
    )

    title = plan.get(
        "title",
        "قصة جديدة"
    )

    narration = plan.get(
        "narration",
        ""
    )

    scenes = plan.get(
        "scenes",
        []
    )

    # =====================================================
    # 2. VOICE
    # =====================================================

    logger.info(
        "Step 2/5 - Generating Arabic voice..."
    )

    voice_file = (
        job_dir /
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
            "Voice duration could not be determined."
        )

    target_duration = max(
        MIN_VIDEO_SECONDS,
        voice_duration
    )

    target_duration = min(
        MAX_VIDEO_SECONDS,
        target_duration
    )

    # =====================================================
    # 3. SCENE TIMING
    # =====================================================

    scene_duration = (
        target_duration /
        len(scenes)
    )

    logger.info(
        "Each scene duration: %.2f seconds",
        scene_duration
    )

    # =====================================================
    # 4. IMAGES + MOTION
    # =====================================================

    logger.info(
        "Step 3/5 - Generating images..."
    )

    clips = []

    for index, scene in enumerate(
        scenes
    ):

        image_prompt = (
            scene.get(
                "image_prompt",
                ""
            )
            .strip()
        )

        if not image_prompt:

            image_prompt = (
                "A realistic cinematic "
                "mysterious scene, "
                "photorealistic, dramatic "
                "lighting, realistic environment, "
                "vertical composition."
            )

        image_path = (
            job_dir /
            f"scene_{index + 1}.jpg"
        )

        motion_path = (
            job_dir /
            f"scene_{index + 1}.mp4"
        )

        logger.info(
            "Scene %s/%s",
            index + 1,
            len(scenes)
        )

        generate_image(
            image_prompt,
            image_path,
            seed=(
                10000
                + generation_number * 100
                + index
            )
        )

        logger.info(
            "Animating scene %s/%s",
            index + 1,
            len(scenes)
        )

        create_motion_clip(
            image_path,
            motion_path,
            scene_duration,
            index
        )

        clips.append(
            motion_path
        )

    # =====================================================
    # 5. FINAL VIDEO
    # =====================================================

    logger.info(
        "Step 4/5 - Building final video..."
    )

    final_video = (
        job_dir /
        "final.mp4"
    )

    make_final_video(
        clips=clips,
        voice_file=voice_file,
        narration=narration,
        title=title,
        output_path=final_video
    )

    logger.info(
        "Step 5/5 - Video completed: %s",
        final_video
    )

    return {
        "video": str(final_video),
        "title": title,
        "job_id": job_id,
        "duration": target_duration
    }


# =========================================================
# TELEGRAM START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً في Abosaraj\n\n"
        "ابعتلي قصة وأنا أحولها لفيديو قصصي جاهز.\n\n"
        "🧠 كتابة سينمائية\n"
        "🖼️ صور AI\n"
        "🎞️ حركة سينمائية\n"
        "🎙️ صوت رجل عربي\n"
        "📝 Captions\n"
        "📱 9:16\n"
        "🔥 جاهز للنشر"
    )


# =========================================================
# STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📊 حالة Abosaraj\n\n"
        "🧠 Story Engine: ON\n"
        f"🖼️ Scenes: {SCENE_COUNT}\n"
        "🎙️ Arabic Voice: ON\n"
        "📝 Captions: ON\n"
        "🎞️ Motion: ON\n"
        "📱 Format: 720x1280\n"
        f"⏱️ Target: "
        f"{MIN_VIDEO_SECONDS}-{MAX_VIDEO_SECONDS} sec\n\n"
        f"🖼️ Image model:\n{FAL_MODEL}"
    )


# =========================================================
# TELEGRAM STORY
# =========================================================

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

    if len(story) < 30:

        await update.message.reply_text(
            "اكتب قصة أطول شوي حتى أقدر "
            "أعمل منها فيديو قوي 🎬"
        )

        return

    await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 1/5 عم أبني السيناريو...\n"
        "🎙️ 2/5 عم أجهز صوت الراوي...\n"
        "🖼️ 3/5 عم أعمل المشاهد...\n"
        "🎞️ 4/5 عم أحرك المشاهد...\n"
        "🎥 5/5 عم أركب الفيديو...\n\n"
        "⏳ أول تجربة ممكن تاخذ وقت شوي."
    )

    loop = asyncio.get_running_loop()

    generation_number = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    try:

        result = await loop.run_in_executor(
            executor,
            create_one_video,
            story,
            generation_number
        )

        video_path = result["video"]

        title = result["title"]

        duration = result["duration"]

        await update.message.reply_text(
            "✅ خلص الفيديو!\n\n"
            f"🎬 {title}\n"
            f"⏱️ {duration:.0f} ثانية\n"
            "🖼️ AI Scenes\n"
            "🎞️ Cinematic Motion\n"
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
                    "#shorts #reels #ai"
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
        "Starting Abosaraj Story Engine..."
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
        CommandHandler(
            "status",
            status_command
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
