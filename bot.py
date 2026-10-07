import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
import shutil
import time
import traceback
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

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

SCENE_COUNT = 4

# عدد الكلمات المطلوب للنص
MIN_WORDS = 150
MAX_WORDS = 190

# المدة المستهدفة
MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 90

VOICE = "ar-SA-HamedNeural"

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
).upper()


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=getattr(
        logging,
        LOG_LEVEL,
        logging.INFO,
    ),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger("story_bot")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Story Bot is running."


@app.route("/health")
def health():
    return "OK"


def start_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN"
)

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY"
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile",
)

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY"
)

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux",
)


def check_environment():

    logger.info(
        "========== ENVIRONMENT CHECK =========="
    )

    missing = []

    if not BOT_TOKEN:
        missing.append(
            "BOT_TOKEN"
        )

    if not GROQ_API_KEY:
        missing.append(
            "GROQ_API_KEY"
        )

    if not POLLINATIONS_API_KEY:
        missing.append(
            "POLLINATIONS_API_KEY"
        )

    if missing:

        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    logger.info(
        "BOT_TOKEN: OK"
    )

    logger.info(
        "GROQ_API_KEY: OK"
    )

    logger.info(
        "GROQ_MODEL: %s",
        GROQ_MODEL,
    )

    logger.info(
        "POLLINATIONS_API_KEY: OK"
    )

    logger.info(
        "POLLINATIONS_MODEL: %s",
        POLLINATIONS_MODEL,
    )

    logger.info(
        "Environment check completed."
    )


# =========================================================
# GROQ
# =========================================================

groq_client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# FONT DETECTION
# =========================================================

def find_arabic_font():

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in candidates:

        if os.path.exists(path):

            logger.info(
                "Arabic font found: %s",
                path,
            )

            return path

    try:

        result = subprocess.run(
            [
                "fc-match",
                ":lang=ar",
                "-f",
                "%{file}\n",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        lines = (
            result.stdout
            .strip()
            .splitlines()
        )

        if lines:

            font_path = lines[0].strip()

            if os.path.exists(
                font_path
            ):

                logger.info(
                    "Arabic font found with fc-match: %s",
                    font_path,
                )

                return font_path

    except Exception as e:

        logger.warning(
            "Font detection failed: %s",
            e,
        )

    fallback = (
        "/usr/share/fonts/truetype/"
        "dejavu/DejaVuSans.ttf"
    )

    logger.warning(
        "Using fallback font: %s",
        fallback,
    )

    return fallback


ARABIC_FONT_PATH = find_arabic_font()


def get_font_family(
    font_path
):

    try:

        result = subprocess.run(
            [
                "fc-scan",
                "--format=%{family}",
                font_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        family = (
            result.stdout
            .strip()
        )

        if family:

            return family.split(
                ","
            )[0]

    except Exception as e:

        logger.warning(
            "Font family detection failed: %s",
            e,
        )

    return "DejaVu Sans"


ARABIC_FONT_FAMILY = get_font_family(
    ARABIC_FONT_PATH
)

logger.info(
    "Arabic font family: %s",
    ARABIC_FONT_FAMILY,
)


# =========================================================
# JSON
# =========================================================

def extract_json_from_text(
    text
):

    if not text:

        raise ValueError(
            "Groq returned empty response."
        )

    text = text.strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"^```\s*",
        "",
        text,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:

        return json.loads(
            text
        )

    except Exception:
        pass

    start = text.find(
        "{"
    )

    end = text.rfind(
        "}"
    )

    if (
        start != -1
        and end != -1
        and end > start
    ):

        candidate = text[
            start:end + 1
        ]

        try:

            return json.loads(
                candidate
            )

        except Exception:
            pass

    raise ValueError(
        "Could not extract JSON from Groq response."
    )


# =========================================================
# STORY NORMALIZATION
# =========================================================

def normalize_story_plan(
    data
):

    if isinstance(
        data,
        list,
    ):

        scenes = data

    elif isinstance(
        data,
        dict,
    ):

        scenes = (
            data.get("scenes")
            or data.get("story")
            or data.get("sections")
        )

    else:

        scenes = None

    if not isinstance(
        scenes,
        list,
    ):

        raise ValueError(
            "Groq JSON does not contain scenes list."
        )

    normalized = []

    for index, scene in enumerate(
        scenes
    ):

        if not isinstance(
            scene,
            dict,
        ):

            continue

        narration = (
            scene.get("narration")
            or scene.get("voice")
            or scene.get("text")
            or ""
        )

        image_prompt = (
            scene.get("image_prompt")
            or scene.get("prompt")
            or scene.get("visual")
            or ""
        )

        narration = str(
            narration
        ).strip()

        image_prompt = str(
            image_prompt
        ).strip()

        if not narration:
            continue

        if not image_prompt:

            image_prompt = (
                "cinematic realistic scene, "
                "dramatic storytelling, "
                "vertical composition, "
                "realistic lighting"
            )

        normalized.append(
            {
                "scene": index + 1,
                "narration": narration,
                "image_prompt": image_prompt,
            }
        )

    return normalized


# =========================================================
# WORD COUNT
# =========================================================

def count_words(text):

    if not text:
        return 0

    return len(
        re.findall(
            r"\S+",
            text.strip(),
            flags=re.UNICODE,
        )
    )


def total_story_words(
    scenes
):

    total = 0

    for scene in scenes:

        total += count_words(
            scene.get(
                "narration",
                "",
            )
        )

    return total


# =========================================================
# VALIDATE STORY
# =========================================================

def validate_story_plan(
    scenes
):

    if len(scenes) != SCENE_COUNT:

        raise ValueError(
            f"Expected {SCENE_COUNT} scenes, "
            f"got {len(scenes)}."
        )

    total_words = total_story_words(
        scenes
    )

    logger.info(
        "STORY_WORD_COUNT=%s",
        total_words,
    )

    if total_words < MIN_WORDS:

        raise ValueError(
            f"Story too short: "
            f"{total_words} words. "
            f"Minimum={MIN_WORDS}"
        )

    if total_words > MAX_WORDS:

        raise ValueError(
            f"Story too long: "
            f"{total_words} words. "
            f"Maximum={MAX_WORDS}"
        )

    for index, scene in enumerate(
        scenes
    ):

        narration = scene.get(
            "narration",
            "",
        ).strip()

        prompt = scene.get(
            "image_prompt",
            "",
        ).strip()

        scene_words = count_words(
            narration
        )

        logger.info(
            "SCENE_%s_WORDS=%s",
            index + 1,
            scene_words,
        )

        if not narration:

            raise ValueError(
                f"Scene {index + 1} "
                "has empty narration."
            )

        if not prompt:

            raise ValueError(
                f"Scene {index + 1} "
                "has empty image prompt."
            )

    return True


# =========================================================
# GENERATE STORY
# =========================================================

def generate_story_plan(
    user_story
):

    logger.info(
        "========== GROQ STORY GENERATION =========="
    )

    prompt = f"""
أنت كاتب محترف لفيديوهات القصص القصيرة.

حوّل القصة التالية إلى سيناريو عربي مشوق جداً.

القصة:
{user_story}

شروط صارمة:

- أريد EXACTLY {SCENE_COUNT} مشاهد.
- مجموع كلمات narration يجب أن يكون بين {MIN_WORDS} و {MAX_WORDS}.
- الهدف صوت مدته 60 إلى 90 ثانية.
- كل مشهد تقريباً 35 إلى 50 كلمة.
- لا تختصر الأحداث.
- لا تستخدم حشواً.
- لا تكرر الجمل.
- اجعل البداية Hook قوية.
- اجعل الأحداث تتصاعد.
- اجعل النهاية قوية ومفاجئة.
- narration باللغة العربية.
- image_prompt باللغة الإنجليزية.
- image_prompt يجب أن يكون وصفاً بصرياً سينمائياً.
- الصور واقعية.
- الصور عمودية 9:16.
- لا يوجد أي نص داخل الصور.

أرجع JSON فقط.

الصيغة:

{{
  "scenes": [
    {{
      "scene": 1,
      "narration": "...",
      "image_prompt": "..."
    }},
    {{
      "scene": 2,
      "narration": "...",
      "image_prompt": "..."
    }},
    {{
      "scene": 3,
      "narration": "...",
      "image_prompt": "..."
    }},
    {{
      "scene": 4,
      "narration": "...",
      "image_prompt": "..."
    }}
  ]
}}
"""

    retry_prompts = [
        "",
        """
المحاولة السابقة غير مناسبة.
أعد الكتابة واجعل النص أطول.
يجب أن يكون مجموع narration على الأقل 155 كلمة.
لا تختصر.
""",
        """
مهم جداً:
أريد نصاً قريباً من 170 كلمة.
لا ترسل نصاً قصيراً.
يجب أن يكون مناسباً لصوت مدته دقيقة تقريباً أو أكثر.
""",
    ]

    last_error = None

    for attempt in range(3):

        logger.info(
            "GROQ_ATTEMPT=%s/3",
            attempt + 1,
        )

        try:

            response = (
                groq_client
                .chat
                .completions
                .create(
                    model=GROQ_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "أنت كاتب سيناريو عربي محترف. "
                                "التزم بالتعليمات. "
                                "أرسل JSON فقط."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                prompt
                                + retry_prompts[
                                    attempt
                                ]
                            ),
                        },
                    ],
                    temperature=0.75,
                    max_tokens=3000,
                )
            )

            raw = (
                response
                .choices[0]
                .message
                .content
            )

            logger.info(
                "GROQ_RESPONSE_LENGTH=%s",
                len(raw or ""),
            )

            data = extract_json_from_text(
                raw
            )

            scenes = normalize_story_plan(
                data
            )

            validate_story_plan(
                scenes
            )

            logger.info(
                "GROQ_STORY_SUCCESS"
            )

            return scenes

        except Exception as e:

            last_error = e

            logger.error(
                "GROQ_ERROR_TYPE=%s",
                type(e).__name__,
            )

            logger.error(
                "GROQ_ERROR_MESSAGE=%s",
                str(e),
            )

            logger.error(
                "GROQ_ERROR_TRACEBACK:\n%s",
                traceback.format_exc(),
            )

            time.sleep(1)

    raise RuntimeError(
        f"Groq story generation failed: "
        f"{last_error}"
    )


# =========================================================
# IMAGE PROMPT
# =========================================================

def build_image_prompt(
    scene,
    index
):

    prompt = scene[
        "image_prompt"
    ].strip()

    extra = """
Cinematic realistic photography.
Vertical 9:16.
Realistic human faces.
Detailed environment.
Natural skin.
Dramatic cinematic lighting.
Strong depth.
Professional movie still.
No text.
No subtitles.
No watermark.
No logo.
"""

    if index == 0:

        extra += """
This is the opening shot.
Make it visually powerful and immediately interesting.
"""

    return (
        prompt
        + "\n"
        + extra
    )


# =========================================================
# POLLINATIONS
# =========================================================

def generate_image(
    prompt,
    output_path,
    seed
):

    logger.info(
        "========== IMAGE GENERATION =========="
    )

    logger.info(
        "IMAGE_SEED=%s",
        seed,
    )

    encoded_prompt = requests.utils.quote(
        prompt,
        safe="",
    )

    url = (
        "https://gen.pollinations.ai/image/"
        + encoded_prompt
    )

    params = {
        "model": POLLINATIONS_MODEL,
        "width": FINAL_WIDTH,
        "height": FINAL_HEIGHT,
        "seed": seed,
        "nologo": "true",
    }

    headers = {
        "Authorization":
            f"Bearer {POLLINATIONS_API_KEY}",
    }

    try:

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=180,
        )

        logger.info(
            "IMAGE_HTTP_STATUS=%s",
            response.status_code,
        )

        response.raise_for_status()

        if not response.content:

            raise RuntimeError(
                "Pollinations returned empty image."
            )

        with open(
            output_path,
            "wb",
        ) as f:

            f.write(
                response.content
            )

        size = output_path.stat().st_size

        logger.info(
            "IMAGE_FILE_SIZE=%s",
            size,
        )

        if size < 10000:

            raise RuntimeError(
                "Generated image is too small."
            )

        logger.info(
            "IMAGE_SUCCESS=%s",
            output_path,
        )

        return output_path

    except Exception as e:

        logger.error(
            "IMAGE_ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "IMAGE_ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "IMAGE_ERROR_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        raise


# =========================================================
# TTS
# =========================================================

async def generate_tts_async(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="+0%",
        volume="+0%",
        pitch="+0Hz",
    )

    await communicate.save(
        str(output_path)
    )


def generate_tts(
    text,
    output_path
):

    logger.info(
        "========== TTS GENERATION =========="
    )

    logger.info(
        "TTS_VOICE=%s",
        VOICE,
    )

    logger.info(
        "TTS_WORD_COUNT=%s",
        count_words(text),
    )

    try:

        asyncio.run(
            generate_tts_async(
                text,
                output_path,
            )
        )

        if not output_path.exists():

            raise RuntimeError(
                "TTS file was not created."
            )

        size = output_path.stat().st_size

        logger.info(
            "TTS_FILE_SIZE=%s",
            size,
        )

        logger.info(
            "TTS_SUCCESS"
        )

        return output_path

    except Exception as e:

        logger.error(
            "TTS_ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "TTS_ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "TTS_ERROR_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        raise


# =========================================================
# COMMAND
# =========================================================

def run_command(
    command,
    stage="UNKNOWN"
):

    logger.info(
        "========== FFMPEG: %s ==========",
        stage,
    )

    logger.info(
        "COMMAND=%s",
        " ".join(
            map(
                str,
                command,
            )
        ),
    )

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        logger.info(
            "FFMPEG_RETURN_CODE=%s",
            result.returncode,
        )

        if result.stdout:

            logger.info(
                "FFMPEG_STDOUT:\n%s",
                result.stdout[-3000:],
            )

        if result.returncode != 0:

            logger.error(
                "FFMPEG_STDERR:\n%s",
                result.stderr[-10000:],
            )

            raise RuntimeError(
                f"FFmpeg failed during {stage}"
            )

        logger.info(
            "FFMPEG_SUCCESS=%s",
            stage,
        )

        return result

    except Exception as e:

        logger.error(
            "FFMPEG_ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "FFMPEG_ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "FFMPEG_ERROR_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        raise


# =========================================================
# DURATION
# =========================================================

def get_duration(
    file_path
):

    try:

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:

            raise RuntimeError(
                result.stderr
            )

        duration = float(
            result.stdout.strip()
        )

        logger.info(
            "DURATION %s = %.2f sec",
            file_path.name,
            duration,
        )

        return duration

    except Exception as e:

        logger.error(
            "DURATION_ERROR=%s",
            str(e),
        )

        raise


# =========================================================
# SCENE DURATIONS
# =========================================================

def calculate_scene_durations(
    scenes,
    total_duration
):

    weights = []

    for scene in scenes:

        weights.append(
            max(
                count_words(
                    scene["narration"]
                ),
                1,
            )
        )

    total_weight = sum(
        weights
    )

    durations = []

    for weight in weights:

        duration = (
            total_duration
            * weight
            / total_weight
        )

        durations.append(
            duration
        )

    return durations


# =========================================================
# SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    output_path,
    duration,
    motion_type
):

    logger.info(
        "Creating scene video: %s",
        image_path.name,
    )

    frames = max(
        1,
        int(
            duration * FPS
        )
    )

    zoom_step = 0.0008

    if motion_type == 1:

        vf = (
            "scale=900:-2,"
            "crop=720:1280,"
            "zoompan="
            f"z='min(zoom+{zoom_step},1.12)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:"
            "s=720x1280:"
            f"fps={FPS}"
        )

    elif motion_type == 2:

        vf = (
            "scale=900:-2,"
            "crop=720:1280,"
            "zoompan="
            f"z='max(zoom-{zoom_step},1.0)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:"
            "s=720x1280:"
            f"fps={FPS}"
        )

    elif motion_type == 3:

        vf = (
            "scale=900:-2,"
            "crop=720:1280,"
            "zoompan="
            "z='min(zoom+0.0005,1.08)':"
            "x='(iw-iw/zoom)*0.15':"
            "y='(ih-ih/zoom)/2':"
            f"d={frames}:"
            "s=720x1280:"
            f"fps={FPS}"
        )

    else:

        vf = (
            "scale=900:-2,"
            "crop=720:1280,"
            "zoompan="
            "z='min(zoom+0.0005,1.08)':"
            "x='(iw-iw/zoom)*0.85':"
            "y='(ih-ih/zoom)/2':"
            f"d={frames}:"
            "s=720x1280:"
            f"fps={FPS}"
        )

    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        vf,
        "-t",
        str(duration),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    run_command(
        command,
        stage=f"SCENE_{motion_type}",
    )

    return output_path


# =========================================================
# CONCAT
# =========================================================

def concatenate_videos(
    video_paths,
    output_path
):

    logger.info(
        "========== CONCATENATING VIDEOS =========="
    )

    concat_file = (
        output_path.parent
        / (
            "concat_"
            + uuid.uuid4().hex
            + ".txt"
        )
    )

    try:

        with open(
            concat_file,
            "w",
            encoding="utf-8",
        ) as f:

            for path in video_paths:

                safe_path = (
                    str(path)
                    .replace(
                        "'",
                        "'\\''",
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
            str(concat_file),
            "-c",
            "copy",
            str(output_path),
        ]

        run_command(
            command,
            stage="CONCAT",
        )

        return output_path

    finally:

        try:

            concat_file.unlink(
                missing_ok=True
            )

        except Exception:
            pass


# =========================================================
# AUDIO
# =========================================================

def add_audio(
    video_path,
    audio_path,
    output_path
):

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(output_path),
    ]

    run_command(
        command,
        stage="ADD_AUDIO",
    )

    return output_path


# =========================================================
# ASS
# =========================================================

def escape_ass_text(
    text
):

    text = text.replace(
        "\\",
        "\\\\",
    )

    text = text.replace(
        "{",
        "\\{",
    )

    text = text.replace(
        "}",
        "\\}",
    )

    text = text.replace(
        "\n",
        "\\N",
    )

    return text


def split_caption_text(
    text,
    max_words=12
):

    words = text.split()

    lines = []

    current = []

    for word in words:

        current.append(
            word
        )

        if len(current) >= max_words:

            lines.append(
                " ".join(current)
            )

            current = []

    if current:

        lines.append(
            " ".join(current)
        )

    return "\\N".join(
        lines
    )


def ass_time(
    seconds
):

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600)
        // 60
    )

    secs = (
        seconds % 60
    )

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{secs:05.2f}"
    )


def create_ass_file(
    scenes,
    scene_durations,
    ass_path
):

    logger.info(
        "Creating Arabic captions."
    )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{ARABIC_FONT_FAMILY},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,45,45,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    current_time = 0.0

    with open(
        ass_path,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            header
        )

        for scene, duration in zip(
            scenes,
            scene_durations,
        ):

            start = current_time

            end = (
                current_time
                + duration
            )

            text = split_caption_text(
                scene["narration"],
                12,
            )

            text = escape_ass_text(
                text
            )

            line = (
                "Dialogue: 0,"
                f"{ass_time(start)},"
                f"{ass_time(end)},"
                "Default,,0,0,0,,"
                f"{text}\n"
            )

            f.write(
                line
            )

            current_time = end

    logger.info(
        "ASS_FILE_CREATED=%s",
        ass_path,
    )

    return ass_path


# =========================================================
# CAPTIONS
# =========================================================

def add_captions(
    video_path,
    ass_path,
    output_path
):

    logger.info(
        "========== ADDING CAPTIONS =========="
    )

    fonts_dir = str(
        Path(
            ARABIC_FONT_PATH
        ).parent
    )

    subtitle_filter = (
        f"subtitles={ass_path}"
        f":fontsdir='{fonts_dir}'"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "copy",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    run_command(
        command,
        stage="CAPTIONS",
    )

    return output_path


# =========================================================
# BUILD VIDEO
# =========================================================

def build_video(
    scenes,
    workdir
):

    logger.info(
        "========================================"
    )

    logger.info(
        "========== BUILD VIDEO START =========="
    )

    logger.info(
        "========================================"
    )

    try:

        # -------------------------------------------------
        # Validate
        # -------------------------------------------------

        validate_story_plan(
            scenes
        )

        # -------------------------------------------------
        # Narration
        # -------------------------------------------------

        full_narration = "\n".join(
            scene["narration"]
            for scene in scenes
        )

        total_words = count_words(
            full_narration
        )

        logger.info(
            "FINAL_NARRATION_WORDS=%s",
            total_words,
        )

        # -------------------------------------------------
        # TTS
        # -------------------------------------------------

        audio_path = (
            workdir
            / "narration.mp3"
        )

        generate_tts(
            full_narration,
            audio_path,
        )

        audio_duration = get_duration(
            audio_path
        )

        logger.info(
            "TTS_DURATION=%.2f",
            audio_duration,
        )

        if audio_duration < 50:

            logger.warning(
                "WARNING: TTS is very short: %.2f seconds",
                audio_duration,
            )

        # -------------------------------------------------
        # Scene durations
        # -------------------------------------------------

        scene_durations = (
            calculate_scene_durations(
                scenes,
                audio_duration,
            )
        )

        logger.info(
            "SCENE_DURATIONS=%s",
            [
                round(
                    x,
                    2,
                )
                for x in scene_durations
            ],
        )

        # -------------------------------------------------
        # Images
        # -------------------------------------------------

        scene_videos = []

        for index, (
            scene,
            duration,
        ) in enumerate(
            zip(
                scenes,
                scene_durations,
            )
        ):

            scene_number = (
                index + 1
            )

            logger.info(
                "========== SCENE %s/%s ==========",
                scene_number,
                SCENE_COUNT,
            )

            image_path = (
                workdir
                / (
                    f"scene_"
                    f"{scene_number}.jpg"
                )
            )

            video_path = (
                workdir
                / (
                    f"scene_"
                    f"{scene_number}.mp4"
                )
            )

            prompt = (
                build_image_prompt(
                    scene,
                    index,
                )
            )

            seed = (
                int(time.time())
                + index * 1000
            )

            generate_image(
                prompt,
                image_path,
                seed,
            )

            create_scene_video(
                image_path,
                video_path,
                duration,
                (
                    index % 4
                ) + 1,
            )

            scene_videos.append(
                video_path
            )

        # -------------------------------------------------
        # Concat
        # -------------------------------------------------

        silent_video = (
            workdir
            / "silent_video.mp4"
        )

        concatenate_videos(
            scene_videos,
            silent_video,
        )

        # -------------------------------------------------
        # Audio
        # -------------------------------------------------

        video_with_audio = (
            workdir
            / "video_with_audio.mp4"
        )

        add_audio(
            silent_video,
            audio_path,
            video_with_audio,
        )

        # -------------------------------------------------
        # Captions
        # -------------------------------------------------

        ass_path = (
            workdir
            / "captions.ass"
        )

        create_ass_file(
            scenes,
            scene_durations,
            ass_path,
        )

        # -------------------------------------------------
        # Final
        # -------------------------------------------------

        final_video = (
            workdir
            / "final.mp4"
        )

        add_captions(
            video_with_audio,
            ass_path,
            final_video,
        )

        final_duration = get_duration(
            final_video
        )

        logger.info(
            "========================================"
        )

        logger.info(
            "FINAL_VIDEO_DURATION=%.2f",
            final_duration,
        )

        logger.info(
            "FINAL_VIDEO=%s",
            final_video,
        )

        logger.info(
            "========== BUILD VIDEO SUCCESS =========="
        )

        logger.info(
            "========================================"
        )

        return final_video

    except Exception as e:

        logger.error(
            "========================================"
        )

        logger.error(
            "BUILD_VIDEO_FAILED"
        )

        logger.error(
            "ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "FULL_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        logger.error(
            "========================================"
        )

        raise


# =========================================================
# TELEGRAM
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "👋 ابعتلي القصة وأنا أحولها لفيديو قصصي."
    )


async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    story = (
        update.message.text
        or ""
    ).strip()

    if not story:
        return

    logger.info(
        "========================================"
    )

    logger.info(
        "NEW STORY RECEIVED"
    )

    logger.info(
        "STORY_LENGTH=%s",
        len(story),
    )

    status_message = (
        await update.message.reply_text(
            "🎬 استلمت القصة.\n"
            "⏳ جاري تجهيز السيناريو..."
        )
    )

    workdir = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    try:

        workdir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # -------------------------------------------------
        # Groq
        # -------------------------------------------------

        scenes = (
            generate_story_plan(
                story
            )
        )

        words = total_story_words(
            scenes
        )

        await status_message.edit_text(
            "📝 تم تجهيز السيناريو.\n"
            f"📖 الكلمات: {words}\n"
            "🖼️ جاري تجهيز المشاهد..."
        )

        # -------------------------------------------------
        # Build
        # -------------------------------------------------

        final_video = build_video(
            scenes,
            workdir,
        )

        duration = get_duration(
            final_video
        )

        # -------------------------------------------------
        # Telegram
        # -------------------------------------------------

        await status_message.edit_text(
            "🎞️ الفيديو جاهز.\n"
            "📤 جاري الإرسال..."
        )

        with open(
            final_video,
            "rb",
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🎬 تم إنشاء الفيديو بنجاح\n"
                    f"⏱️ المدة: "
                    f"{duration:.1f} ثانية"
                ),
                supports_streaming=True,
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
            )

        await status_message.delete()

        logger.info(
            "TELEGRAM_SEND_SUCCESS"
        )

    except Exception as e:

        # =================================================
        # IMPORTANT ERROR LOG
        # =================================================

        logger.error(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        logger.error(
            "VIDEO_GENERATION_FAILED"
        )

        logger.error(
            "ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "ERROR_REPR=%r",
            e,
        )

        logger.error(
            "FULL_TRACEBACK:"
        )

        logger.error(
            traceback.format_exc()
        )

        logger.error(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        # رسالة للمستخدم
        try:

            await status_message.edit_text(
                "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
                f"النوع: {type(e).__name__}\n"
                f"الخطأ: {str(e)[:700]}\n\n"
                "راجع Logs في Render."
            )

        except Exception as telegram_error:

            logger.error(
                "Could not send error message: %s",
                telegram_error,
            )

    finally:

        try:

            if workdir.exists():

                shutil.rmtree(
                    workdir,
                    ignore_errors=True,
                )

                logger.info(
                    "WORKDIR_CLEANED"
                )

        except Exception as e:

            logger.warning(
                "Cleanup failed: %s",
                e,
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    check_environment()

    logger.info(
        "========================================"
    )

    logger.info(
        "STARTING TELEGRAM BOT"
    )

    logger.info(
        "========================================"
    )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story,
        )
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info(
        "BOT_POLLING_STARTED"
    )

    try:

        while True:

            await asyncio.sleep(
                3600
            )

    except asyncio.CancelledError:

        logger.info(
            "BOT_CANCELLED"
        )

    finally:

        logger.info(
            "STOPPING BOT"
        )

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    logger.info(
        "APPLICATION_START"
    )

    flask_thread = threading.Thread(
        target=start_flask,
        daemon=True,
    )

    flask_thread.start()

    try:

        asyncio.run(
            main()
        )

    except Exception as e:

        logger.error(
            "APPLICATION_FATAL_ERROR"
        )

        logger.error(
            "FATAL_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "FATAL_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "FATAL_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        raise
