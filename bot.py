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

# الهدف الحقيقي للنص
MIN_WORDS = 150
MAX_WORDS = 190

# الهدف التقريبي للفيديو
MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 90

VOICE = "ar-SA-HamedNeural"

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
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
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile",
)

POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")
POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux",
)


def check_environment():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    if not POLLINATIONS_API_KEY:
        missing.append("POLLINATIONS_API_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    logger.info("Environment variables OK.")


# =========================================================
# GROQ CLIENT
# =========================================================

groq_client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# ARABIC FONT
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
            logger.info("Arabic font found: %s", path)
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

        font_path = result.stdout.strip().splitlines()[0]

        if font_path and os.path.exists(font_path):
            logger.info(
                "Arabic font found using fc-match: %s",
                font_path,
            )
            return font_path

    except Exception as e:
        logger.warning(
            "Could not detect Arabic font: %s",
            e,
        )

    logger.warning(
        "Arabic font not found. Falling back to DejaVu Sans."
    )

    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


ARABIC_FONT_PATH = find_arabic_font()


def get_font_family(font_path):
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

        family = result.stdout.strip()

        if family:
            return family.split(",")[0]

    except Exception as e:
        logger.warning(
            "Could not detect font family: %s",
            e,
        )

    return "DejaVu Sans"


ARABIC_FONT_FAMILY = get_font_family(
    ARABIC_FONT_PATH
)

logger.info(
    "Using font family: %s",
    ARABIC_FONT_FAMILY,
)


# =========================================================
# JSON HELPERS
# =========================================================

def extract_json_from_text(text):
    """
    يحاول استخراج JSON حتى لو Groq وضع كلام حوله.
    """

    if not text:
        raise ValueError("Empty Groq response.")

    text = text.strip()

    # إزالة markdown fences
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
        return json.loads(text)
    except Exception:
        pass

    # البحث عن أول object
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise ValueError(
        "Could not extract valid JSON from Groq response."
    )


# =========================================================
# STORY NORMALIZATION
# =========================================================

def normalize_story_plan(data):

    if isinstance(data, list):
        scenes = data

    elif isinstance(data, dict):
        scenes = data.get("scenes")

        if scenes is None:
            scenes = data.get("story")

        if scenes is None:
            scenes = data.get("sections")

    else:
        scenes = None

    if not isinstance(scenes, list):
        raise ValueError(
            "Groq response does not contain a scenes list."
        )

    normalized = []

    for index, scene in enumerate(scenes):

        if not isinstance(scene, dict):
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

        narration = str(narration).strip()
        image_prompt = str(image_prompt).strip()

        if not narration:
            continue

        if not image_prompt:
            image_prompt = (
                "cinematic realistic scene, "
                "dramatic storytelling, "
                "vertical composition, "
                "detailed lighting"
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

def count_arabic_words(text):
    if not text:
        return 0

    words = re.findall(
        r"\S+",
        text.strip(),
        flags=re.UNICODE,
    )

    return len(words)


def total_story_words(scenes):

    total = 0

    for scene in scenes:
        total += count_arabic_words(
            scene.get("narration", "")
        )

    return total


# =========================================================
# VALIDATE STORY
# =========================================================

def validate_story_plan(scenes):

    if not isinstance(scenes, list):
        raise ValueError(
            "Story plan is not a list."
        )

    if len(scenes) != SCENE_COUNT:
        raise ValueError(
            f"Expected exactly {SCENE_COUNT} scenes, "
            f"got {len(scenes)}."
        )

    total_words = total_story_words(scenes)

    logger.info(
        "Story contains %s words.",
        total_words,
    )

    if total_words < MIN_WORDS:
        raise ValueError(
            f"Story is too short: "
            f"{total_words} words. "
            f"Minimum is {MIN_WORDS}."
        )

    if total_words > MAX_WORDS:
        raise ValueError(
            f"Story is too long: "
            f"{total_words} words. "
            f"Maximum is {MAX_WORDS}."
        )

    for index, scene in enumerate(scenes):

        narration = scene.get(
            "narration",
            ""
        ).strip()

        image_prompt = scene.get(
            "image_prompt",
            ""
        ).strip()

        if not narration:
            raise ValueError(
                f"Scene {index + 1} has no narration."
            )

        if not image_prompt:
            raise ValueError(
                f"Scene {index + 1} has no image prompt."
            )

    return True


# =========================================================
# GROQ STORY GENERATION
# =========================================================

def generate_story_plan(user_story):

    base_prompt = f"""
أنت كاتب محترف لفيديوهات القصص القصيرة على TikTok وReels وYouTube Shorts.

حوّل القصة التالية إلى سيناريو عربي قوي ومثير بصيغة JSON فقط.

القصة:
{user_story}

المطلوب:

1. عدد المشاهد EXACTLY {SCENE_COUNT} مشاهد.
2. مجموع نصوص narration كلها يجب أن يكون بين {MIN_WORDS} و {MAX_WORDS} كلمة عربية.
3. الهدف أن يكون الصوت النهائي تقريباً من {MIN_VIDEO_SECONDS} إلى {MAX_VIDEO_SECONDS} ثانية.
4. كل مشهد يجب أن يحتوي تقريباً على 35 إلى 50 كلمة.
5. لا تختصر القصة.
6. لا تكرر نفس الجمل.
7. لا تضف حشوًا فارغًا فقط لزيادة عدد الكلمات.
8. اجعل السرد مشوقاً، سينمائياً، واضحاً ومناسباً للصوت.
9. البداية يجب أن تحتوي على Hook قوي.
10. كل مشهد يجب أن يكمل الذي قبله.
11. النهاية يجب أن تكون قوية وتترك أثراً.
12. narration باللغة العربية فقط.
13. image_prompt يكون وصفاً بصرياً مفصلاً للمشهد، باللغة الإنجليزية.
14. الصور يجب أن تكون واقعية وسينمائية وعمودية ومناسبة لفيديو 9:16.
15. لا تستخدم نصوصاً أو كتابة داخل الصور.

أرجع JSON فقط بهذا الشكل:

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

    retry_instructions = [
        "",
        """

تنبيه مهم جداً:
المحاولة السابقة كانت قصيرة.
أعد كتابة السيناريو بحيث يكون مجموع narration بين
160 و180 كلمة على الأقل.
لا تقلل عدد الكلمات.
كل مشهد يجب أن يحتوي على سرد حقيقي ومفيد.
""",
        """

هذه المحاولة يجب أن تكون طويلة بما يكفي لصوت مدته دقيقة أو أكثر.

أريد مجموع narration قريباً من 170 كلمة.
استخدم أحداثاً وتفاصيل وحواراً داخلياً ووصفاً مناسباً
بدون تكرار أو حشو.
لا ترسل أقل من 150 كلمة.
""",
    ]

    last_error = None

    for attempt in range(3):

        logger.info(
            "Generating story plan. Attempt %s/3",
            attempt + 1,
        )

        prompt = (
            base_prompt
            + retry_instructions[attempt]
        )

        try:

            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "أنت كاتب سيناريو عربي محترف. "
                            "التزم بالتعليمات حرفياً. "
                            "أرسل JSON فقط."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.75,
                max_tokens=3000,
            )

            raw = response.choices[0].message.content

            logger.info(
                "Groq response received. Length=%s",
                len(raw or ""),
            )

            data = extract_json_from_text(raw)

            scenes = normalize_story_plan(data)

            validate_story_plan(scenes)

            words = total_story_words(scenes)

            logger.info(
                "Valid story generated: %s scenes / %s words",
                len(scenes),
                words,
            )

            return scenes

        except Exception as e:

            last_error = e

            logger.warning(
                "Story generation attempt %s failed: %s",
                attempt + 1,
                e,
            )

            time.sleep(1)

    raise RuntimeError(
        f"Could not generate valid story after 3 attempts: "
        f"{last_error}"
    )


# =========================================================
# IMAGE PROMPT
# =========================================================

def build_image_prompt(scene, scene_index):

    prompt = scene.get(
        "image_prompt",
        ""
    ).strip()

    base = f"""
{prompt}

Cinematic realistic photography.
Vertical 9:16 composition.
High detail.
Natural realistic human faces.
Dramatic cinematic lighting.
Strong depth.
Professional movie still.
No text.
No subtitles.
No watermark.
No logo.
"""

    if scene_index == 0:
        base += """
This is the opening shot.
Make it immediately visually interesting.
Strong establishing composition.
"""

    return base.strip()


# =========================================================
# POLLINATIONS IMAGE
# =========================================================

def generate_image(prompt, output_path, seed):

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

    logger.info(
        "Generating image seed=%s",
        seed,
    )

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=180,
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "content-type",
        "",
    )

    logger.info(
        "Pollinations response: %s bytes, %s",
        len(response.content),
        content_type,
    )

    if not response.content:
        raise RuntimeError(
            "Pollinations returned empty image."
        )

    with open(output_path, "wb") as f:
        f.write(response.content)

    if not output_path.exists():
        raise RuntimeError(
            "Image file was not created."
        )

    if output_path.stat().st_size < 10000:
        raise RuntimeError(
            "Generated image is suspiciously small."
        )

    logger.info(
        "Image saved: %s",
        output_path,
    )

    return output_path


# =========================================================
# TTS
# =========================================================

async def generate_tts_async(text, output_path):

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


def generate_tts(text, output_path):

    logger.info(
        "Generating Arabic TTS..."
    )

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

    logger.info(
        "TTS saved: %s",
        output_path,
    )

    return output_path


# =========================================================
# FFMPEG
# =========================================================

def run_command(command):

    logger.info(
        "Running command: %s",
        " ".join(map(str, command)),
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        logger.error(
            "Command failed.\nSTDOUT:\n%s\nSTDERR:\n%s",
            result.stdout[-5000:],
            result.stderr[-5000:],
        )

        raise RuntimeError(
            "FFmpeg command failed."
        )

    return result


def get_duration(file_path):

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
            f"Could not read duration: {file_path}"
        )

    return float(result.stdout.strip())


# =========================================================
# SCENE DURATIONS
# =========================================================

def calculate_scene_durations(scenes, total_duration):

    weights = []

    for scene in scenes:
        words = count_arabic_words(
            scene["narration"]
        )
        weights.append(max(words, 1))

    total_weight = sum(weights)

    durations = []

    for weight in weights:
        duration = (
            total_duration
            * weight
            / total_weight
        )

        durations.append(duration)

    return durations


# =========================================================
# CREATE SCENE VIDEO
# =========================================================

def create_scene_video(
    image_path,
    output_path,
    duration,
    motion_type,
):

    frames = max(
        1,
        int(duration * FPS)
    )

    zoom_step = 0.0008

    if motion_type == 1:

        vf = (
            f"scale=900:-2,"
            f"crop=720:1280,"
            f"zoompan="
            f"z='min(zoom+{zoom_step},1.12)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:"
            f"s=720x1280:"
            f"fps={FPS}"
        )

    elif motion_type == 2:

        vf = (
            f"scale=900:-2,"
            f"crop=720:1280,"
            f"zoompan="
            f"z='max(zoom-{zoom_step},1.0)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:"
            f"s=720x1280:"
            f"fps={FPS}"
        )

    elif motion_type == 3:

        vf = (
            f"scale=900:-2,"
            f"crop=720:1280,"
            f"zoompan="
            f"z='min(zoom+0.0005,1.08)':"
            f"x='(iw-iw/zoom)*0.15':"
            f"y='(ih-ih/zoom)/2':"
            f"d={frames}:"
            f"s=720x1280:"
            f"fps={FPS}"
        )

    else:

        vf = (
            f"scale=900:-2,"
            f"crop=720:1280,"
            f"zoompan="
            f"z='min(zoom+0.0005,1.08)':"
            f"x='(iw-iw/zoom)*0.85':"
            f"y='(ih-ih/zoom)/2':"
            f"d={frames}:"
            f"s=720x1280:"
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

    run_command(command)

    return output_path


# =========================================================
# CONCATENATE
# =========================================================

def concatenate_videos(
    video_paths,
    output_path,
):

    concat_file = output_path.parent / (
        "concat_" + uuid.uuid4().hex + ".txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8",
    ) as f:

        for path in video_paths:
            safe_path = str(
                path
            ).replace(
                "'",
                "'\\''",
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

    try:
        run_command(command)

    finally:

        try:
            concat_file.unlink(
                missing_ok=True
            )
        except Exception:
            pass

    return output_path


# =========================================================
# ADD AUDIO
# =========================================================

def add_audio(
    video_path,
    audio_path,
    output_path,
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

    run_command(command)

    return output_path


# =========================================================
# ASS CAPTIONS
# =========================================================

def escape_ass_text(text):

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


def split_caption_text(text, max_words=12):

    words = text.split()

    lines = []

    current = []

    for word in words:

        current.append(word)

        if len(current) >= max_words:

            lines.append(
                " ".join(current)
            )

            current = []

    if current:
        lines.append(
            " ".join(current)
        )

    return "\\N".join(lines)


def create_ass_file(
    scenes,
    scene_durations,
    ass_path,
):

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

    events = []

    for scene, duration in zip(
        scenes,
        scene_durations,
    ):

        start = current_time
        end = current_time + duration

        text = split_caption_text(
            scene["narration"],
            max_words=12,
        )

        text = escape_ass_text(
            text
        )

        def ass_time(seconds):

            hours = int(seconds // 3600)

            minutes = int(
                (seconds % 3600) // 60
            )

            secs = seconds % 60

            return (
                f"{hours}:"
                f"{minutes:02d}:"
                f"{secs:05.2f}"
            )

        events.append(
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,"
            f"{text}\n"
        )

        current_time = end

    with open(
        ass_path,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(header)

        for event in events:
            f.write(event)

    return ass_path


# =========================================================
# ADD CAPTIONS
# =========================================================

def add_captions(
    video_path,
    ass_path,
    output_path,
):

    fonts_dir = None

    if ARABIC_FONT_PATH:

        fonts_dir = str(
            Path(ARABIC_FONT_PATH).parent
        )

    subtitle_filter = (
        f"subtitles={ass_path}"
    )

    if fonts_dir:
        subtitle_filter += (
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

    run_command(command)

    return output_path


# =========================================================
# BUILD VIDEO
# =========================================================

def build_video(
    scenes,
    workdir,
):

    logger.info(
        "================ BUILD VIDEO ================"
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------------------
    # Narration
    # -----------------------------------------------------

    full_narration = "\n".join(
        scene["narration"]
        for scene in scenes
    )

    total_words = count_arabic_words(
        full_narration
    )

    logger.info(
        "Final narration words: %s",
        total_words,
    )

    if total_words < MIN_WORDS:
        raise RuntimeError(
            f"Narration too short: "
            f"{total_words} words."
        )

    audio_path = workdir / "narration.mp3"

    generate_tts(
        full_narration,
        audio_path,
    )

    audio_duration = get_duration(
        audio_path
    )

    logger.info(
        "Actual TTS duration: %.2f seconds",
        audio_duration,
    )

    # لا نمدد الصوت بشكل مصطنع.
    # إذا كان أقصر قليلاً، نكمل كما هو ونظهر التحذير.
    if audio_duration < MIN_VIDEO_SECONDS:

        logger.warning(
            "TTS is shorter than target: "
            "%.2f sec < %s sec",
            audio_duration,
            MIN_VIDEO_SECONDS,
        )

    if audio_duration > MAX_VIDEO_SECONDS:

        logger.warning(
            "TTS is longer than target: "
            "%.2f sec > %s sec",
            audio_duration,
            MAX_VIDEO_SECONDS,
        )

    target_duration = audio_duration

    # -----------------------------------------------------
    # Scene durations
    # -----------------------------------------------------

    scene_durations = calculate_scene_durations(
        scenes,
        target_duration,
    )

    logger.info(
        "Scene durations: %s",
        [
            round(x, 2)
            for x in scene_durations
        ],
    )

    # -----------------------------------------------------
    # Generate images
    # -----------------------------------------------------

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

        scene_number = index + 1

        logger.info(
            "========== SCENE %s/%s ==========",
            scene_number,
            SCENE_COUNT,
        )

        image_path = (
            workdir
            / f"scene_{scene_number}.jpg"
        )

        video_path = (
            workdir
            / f"scene_{scene_number}.mp4"
        )

        prompt = build_image_prompt(
            scene,
            index,
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

        motion_type = (
            index % 4
        ) + 1

        create_scene_video(
            image_path=image_path,
            output_path=video_path,
            duration=duration,
            motion_type=motion_type,
        )

        scene_videos.append(
            video_path
        )

    # -----------------------------------------------------
    # Concatenate
    # -----------------------------------------------------

    silent_video = (
        workdir
        / "silent_video.mp4"
    )

    concatenate_videos(
        scene_videos,
        silent_video,
    )

    # -----------------------------------------------------
    # Add audio
    # -----------------------------------------------------

    video_with_audio = (
        workdir
        / "video_with_audio.mp4"
    )

    add_audio(
        silent_video,
        audio_path,
        video_with_audio,
    )

    # -----------------------------------------------------
    # Captions
    # -----------------------------------------------------

    ass_path = (
        workdir
        / "captions.ass"
    )

    create_ass_file(
        scenes,
        scene_durations,
        ass_path,
    )

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
        "FINAL VIDEO DURATION: %.2f seconds",
        final_duration,
    )

    logger.info(
        "Final video created: %s",
        final_video,
    )

    return final_video


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

    user_id = (
        update.effective_user.id
        if update.effective_user
        else 0
    )

    logger.info(
        "New story from user %s. Length=%s",
        user_id,
        len(story),
    )

    status_message = await update.message.reply_text(
        "🎬 استلمت القصة.\n"
        "⏳ جاري تجهيز السيناريو..."
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
        # Generate story
        # -------------------------------------------------

        scenes = generate_story_plan(
            story
        )

        words = total_story_words(
            scenes
        )

        await status_message.edit_text(
            "📝 تم تجهيز السيناريو.\n"
            f"📖 عدد الكلمات: {words}\n"
            "🖼️ جاري توليد المشاهد..."
        )

        # -------------------------------------------------
        # Build video
        # -------------------------------------------------

        final_video = build_video(
            scenes,
            workdir,
        )

        duration = get_duration(
            final_video
        )

        logger.info(
            "Sending final video. "
            "Duration=%.2f sec",
            duration,
        )

        await status_message.edit_text(
            "🎙️ تم تجهيز الصوت والصور.\n"
            "🎞️ جاري إرسال الفيديو..."
        )

        with open(
            final_video,
            "rb",
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🎬 تم إنشاء الفيديو بنجاح\n"
                    f"⏱️ المدة: {duration:.1f} ثانية"
                ),
                supports_streaming=True,
                read_timeout=180,
                write_timeout=180,
                connect_timeout=60,
            )

        await status_message.delete()

    except Exception as e:

        logger.exception(
            "Video generation failed."
        )

        try:

            await status_message.edit_text(
                "❌ صار خطأ أثناء إنشاء الفيديو.\n"
                "شوف Logs في Render لمعرفة السبب."
            )

        except Exception:
            pass

    finally:

        try:

            if workdir.exists():
                shutil.rmtree(
                    workdir,
                    ignore_errors=True,
                )

        except Exception as e:

            logger.warning(
                "Could not cleanup workdir: %s",
                e,
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    check_environment()

    logger.info(
        "Starting Telegram bot..."
    )

    application = (
        Application.builder()
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

    logger.info(
        "Bot polling started."
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    try:

        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info(
            "Bot stopping..."
        )

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=start_flask,
        daemon=True,
    )

    flask_thread.start()

    asyncio.run(
        main()
    )
