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
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY",
    ""
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
).strip()

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux"
).strip()


# =========================================================
# SETTINGS
# =========================================================

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# الآن نختبر 4 مشاهد
SCENE_COUNT = 4

# المدة المستهدفة
MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 120

# الصوت الذي كان جيدًا عندك
VOICE = "ar-SA-HamedNeural"

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Story Video Bot is running.", 200


@app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(
        os.environ.get(
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
# ENVIRONMENT VALIDATION
# =========================================================

def validate_environment():

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

    logger.info("Environment variables OK")
    logger.info(
        "Groq model: %s",
        GROQ_MODEL,
    )

    logger.info(
        "Pollinations model: %s",
        POLLINATIONS_MODEL,
    )


# =========================================================
# ARABIC FONT DETECTION
# =========================================================

def find_arabic_font():
    """
    يبحث عن خط يدعم العربية على Render.

    نعطي الأولوية لـ:
    - Noto Sans Arabic
    - Noto Naskh Arabic
    - DejaVu Sans

    وإذا كان fontconfig موجودًا،
    نحاول اكتشاف أفضل خط للغة العربية تلقائيًا.
    """

    preferred_fonts = [
        (
            "Noto Sans Arabic",
            [
                "/usr/share/fonts/truetype/noto/"
                "NotoSansArabic-Regular.ttf",

                "/usr/share/fonts/opentype/noto/"
                "NotoSansArabic-Regular.ttf",
            ],
        ),
        (
            "Noto Naskh Arabic",
            [
                "/usr/share/fonts/truetype/noto/"
                "NotoNaskhArabic-Regular.ttf",

                "/usr/share/fonts/opentype/noto/"
                "NotoNaskhArabic-Regular.ttf",
            ],
        ),
        (
            "DejaVu Sans",
            [
                "/usr/share/fonts/truetype/dejavu/"
                "DejaVuSans.ttf",

                "/usr/share/fonts/dejavu/"
                "DejaVuSans.ttf",
            ],
        ),
    ]

    # -----------------------------------------------------
    # أولاً: الخطوط المعروفة
    # -----------------------------------------------------

    for family, paths in preferred_fonts:

        for path in paths:

            font_path = Path(path)

            if font_path.exists():

                logger.info(
                    "Arabic font found: %s",
                    font_path,
                )

                return {
                    "family": family,
                    "path": font_path,
                }

    # -----------------------------------------------------
    # ثانيًا: fontconfig
    # -----------------------------------------------------

    try:

        result = subprocess.run(
            [
                "fc-match",
                "-f",
                "%{family}\n%{file}\n",
                ":lang=ar",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )

        output = result.stdout.strip()

        if output:

            lines = [
                line.strip()
                for line in output.splitlines()
                if line.strip()
            ]

            if len(lines) >= 2:

                family = lines[0]
                path = Path(lines[1])

                if path.exists():

                    logger.info(
                        "Arabic font detected by fontconfig: "
                        "%s -> %s",
                        family,
                        path,
                    )

                    return {
                        "family": family,
                        "path": path,
                    }

    except Exception as exc:

        logger.warning(
            "fc-match font detection failed: %s",
            exc,
        )

    # -----------------------------------------------------
    # آخر حل
    # -----------------------------------------------------

    logger.warning(
        "Could not detect Arabic font. "
        "Falling back to DejaVu Sans."
    )

    return {
        "family": "DejaVu Sans",
        "path": None,
    }


# =========================================================
# GROQ
# =========================================================

groq_client = None


def extract_json_from_text(text: str):

    if not text:
        raise ValueError(
            "Groq returned an empty response."
        )

    text = text.strip()

    # إزالة Markdown fences
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = text.strip()

    # JSON كامل
    try:
        return json.loads(text)
    except Exception:
        pass

    # استخراج object
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    # استخراج array
    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end > start:

        candidate = text[
            start:end + 1
        ]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise ValueError(
        "Could not extract valid JSON from Groq response."
    )


def normalize_story_plan(data):

    if isinstance(data, dict):

        scenes = data.get("scenes")

        if scenes is None:
            scenes = data.get("story")

        if scenes is None:
            scenes = data.get("parts")

        if scenes is None:
            scenes = data.get("segments")

        if isinstance(scenes, list):
            data["scenes"] = scenes

        return data

    if isinstance(data, list):

        return {
            "title": "قصة",
            "scenes": data,
        }

    raise ValueError(
        "Groq JSON has unsupported structure."
    )


def validate_story_plan(data):

    data = normalize_story_plan(data)

    scenes = data.get("scenes")

    if not isinstance(scenes, list):

        raise ValueError(
            "Groq response does not contain "
            "a valid 'scenes' list."
        )

    if len(scenes) == 0:

        raise ValueError(
            "Groq returned zero scenes."
        )

    cleaned_scenes = []

    for scene in scenes:

        if isinstance(scene, str):

            cleaned_scenes.append(
                {
                    "narration": scene.strip(),
                    "visual_prompt": scene.strip(),
                }
            )

            continue

        if not isinstance(scene, dict):
            continue

        narration = (
            scene.get("narration")
            or scene.get("voice")
            or scene.get("text")
            or scene.get("script")
            or ""
        )

        visual_prompt = (
            scene.get("visual_prompt")
            or scene.get("image_prompt")
            or scene.get("prompt")
            or scene.get("visual")
            or narration
        )

        narration = str(
            narration
        ).strip()

        visual_prompt = str(
            visual_prompt
        ).strip()

        if not narration:
            continue

        if not visual_prompt:
            visual_prompt = narration

        cleaned_scenes.append(
            {
                "narration": narration,
                "visual_prompt": visual_prompt,
            }
        )

    if not cleaned_scenes:

        raise ValueError(
            "Groq returned scenes but none "
            "contained narration."
        )

    # -----------------------------------------------------
    # مهم:
    # لا نسمح بأقل من SCENE_COUNT
    # -----------------------------------------------------

    if len(cleaned_scenes) < SCENE_COUNT:

        raise ValueError(
            f"Groq returned only "
            f"{len(cleaned_scenes)} scenes. "
            f"Required: {SCENE_COUNT}."
        )

    # نأخذ العدد المطلوب فقط
    cleaned_scenes = cleaned_scenes[
        :SCENE_COUNT
    ]

    return {
        "title": str(
            data.get("title")
            or data.get("name")
            or "قصة"
        ).strip(),

        "scenes": cleaned_scenes,
    }


def generate_story_plan(story: str):

    global groq_client

    if groq_client is None:

        groq_client = Groq(
            api_key=GROQ_API_KEY
        )

    logger.info(
        "Generating story plan with Groq..."
    )

    system_prompt = f"""
أنت كاتب ومخرج محتوى قصصي احترافي للفيديوهات العمودية القصيرة.

مهمتك تحويل القصة التي يرسلها المستخدم إلى سيناريو فيديو قوي وممتع.

المطلوب:

- اللغة العربية.
- السرد طبيعي جدًا وكأنه رجل يحكي القصة.
- لا تختلق أحداثًا رئيسية غير موجودة في القصة.
- اجعل البداية جذابة.
- اجعل السرد مناسبًا لفيديو مدته من دقيقة إلى دقيقتين.
- قسم القصة إلى {SCENE_COUNT} مشاهد بالضبط.
- كل مشهد يجب أن يكون جزءًا مختلفًا من القصة.
- لا تكرر نفس الصورة أو نفس الحدث في المشاهد.
- كل مشهد يجب أن يحتوي على narration و visual_prompt.
- visual_prompt يجب أن يكون باللغة الإنجليزية.
- visual_prompt يجب أن يصف لقطة سينمائية واضحة ومختلفة عن باقي المشاهد.

مهم جدًا:

يجب أن يكون الرد JSON فقط.

ممنوع:
- Markdown
- ```json
- شرح خارج JSON
- أي نص قبل أو بعد JSON

الصيغة:

{{
  "title": "عنوان القصة",
  "scenes": [
    {{
      "narration": "نص المشهد الأول",
      "visual_prompt": "Cinematic English visual description"
    }},
    {{
      "narration": "نص المشهد الثاني",
      "visual_prompt": "Cinematic English visual description"
    }},
    {{
      "narration": "نص المشهد الثالث",
      "visual_prompt": "Cinematic English visual description"
    }},
    {{
      "narration": "نص المشهد الرابع",
      "visual_prompt": "Cinematic English visual description"
    }}
  ]
}}
"""

    user_prompt = f"""
القصة:

{story}

حوّلها الآن إلى {SCENE_COUNT} مشاهد بالضبط.
أريد JSON فقط.
"""

    last_error = None

    for attempt in range(1, 4):

        try:

            logger.info(
                "Calling Groq attempt %s/3...",
                attempt,
            )

            response = (
                groq_client
                .chat
                .completions
                .create(
                    model=GROQ_MODEL,

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

                    temperature=0.4,

                    max_tokens=3000,

                    response_format={
                        "type": "json_object"
                    },
                )
            )

            logger.info(
                "Groq HTTP request completed."
            )

            if (
                not response
                or not response.choices
            ):

                raise RuntimeError(
                    "Groq returned no choices."
                )

            message = (
                response.choices[0].message
            )

            raw_content = ""

            if message is not None:

                raw_content = (
                    message.content
                    or ""
                )

            logger.info(
                "Groq response length: %s characters",
                len(raw_content),
            )

            if not raw_content.strip():

                logger.error(
                    "Groq returned EMPTY content."
                )

                raise RuntimeError(
                    "Groq returned empty content."
                )

            try:

                data = (
                    extract_json_from_text(
                        raw_content
                    )
                )

                plan = (
                    validate_story_plan(
                        data
                    )
                )

                logger.info(
                    "Groq story plan OK: %s scenes",
                    len(plan["scenes"]),
                )

                return plan

            except Exception as parse_error:

                logger.error(
                    "Invalid Groq response: %s",
                    parse_error,
                )

                logger.error(
                    "Groq raw response preview: %r",
                    raw_content[:3000],
                )

                last_error = parse_error

                user_prompt = f"""
أعد كتابة القصة التالية كـ JSON صحيح فقط.

يجب أن يكون عدد المشاهد {SCENE_COUNT} بالضبط.

كل مشهد يجب أن يحتوي:

narration
visual_prompt

لا تستخدم Markdown.
لا تستخدم ```.

القصة:

{story}
"""

        except Exception as exc:

            last_error = exc

            logger.exception(
                "Groq request failed on attempt %s",
                attempt,
            )

            time.sleep(
                1.5 * attempt
            )

    raise RuntimeError(
        "Groq failed after 3 attempts: "
        f"{last_error}"
    )


# =========================================================
# POLLINATIONS
# =========================================================

def generate_image(
    prompt: str,
    output_path: Path,
    seed: int,
):

    if not POLLINATIONS_API_KEY:

        raise RuntimeError(
            "POLLINATIONS_API_KEY is missing."
        )

    encoded_prompt = (
        requests.utils.quote(
            prompt,
            safe="",
        )
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
        "Generating Pollinations image..."
    )

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=180,
    )

    response.raise_for_status()

    if not response.content:

        raise RuntimeError(
            "Pollinations returned empty image."
        )

    output_path.write_bytes(
        response.content
    )

    logger.info(
        "Image saved: %s",
        output_path,
    )

    return output_path


# =========================================================
# IMAGE PROMPT
# =========================================================

def build_image_prompt(
    visual_prompt: str,
    scene_index: int,
):

    return f"""
Cinematic realistic movie still for a short story.

{visual_prompt}

This is scene {scene_index} of a continuous story.

Important visual requirements:
- realistic cinematic photography
- dramatic natural lighting
- emotional atmosphere
- realistic human faces
- realistic skin
- natural body proportions
- realistic clothing
- detailed environment
- strong cinematic composition
- professional movie still
- vertical 9:16 composition
- subject clearly visible
- no text
- no subtitles
- no letters
- no watermark
""".strip()


# =========================================================
# EDGE TTS
# =========================================================

async def _generate_tts_async(
    text: str,
    output_path: Path,
):

    communicate = edge_tts.Communicate(
        text,
        VOICE,
    )

    await communicate.save(
        str(output_path)
    )


def generate_tts(
    text: str,
    output_path: Path,
):

    logger.info(
        "Generating Arabic male voice..."
    )

    asyncio.run(
        _generate_tts_async(
            text,
            output_path,
        )
    )

    if not output_path.exists():

        raise RuntimeError(
            "TTS output file was not created."
        )

    if output_path.stat().st_size == 0:

        raise RuntimeError(
            "TTS output file is empty."
        )

    logger.info(
        "Voice saved: %s",
        output_path,
    )

    return output_path


# =========================================================
# FFMPEG
# =========================================================

def run_command(
    command,
    timeout=300,
):

    logger.info(
        "Running command: %s",
        " ".join(
            map(
                str,
                command,
            )
        ),
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:

        logger.error(
            "Command failed:\n%s",
            result.stderr[-5000:],
        )

        raise RuntimeError(
            "FFmpeg/command failed."
        )

    return result


def get_duration(path: Path):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    result = run_command(
        command,
        timeout=60,
    )

    try:

        return float(
            result.stdout.strip()
        )

    except Exception:

        raise RuntimeError(
            f"Could not read duration for {path}"
        )


# =========================================================
# SCENE DURATION
# =========================================================

def calculate_scene_durations(
    scenes,
    total_duration,
):

    weights = []

    for scene in scenes:

        narration = (
            scene.get(
                "narration",
                "",
            )
            .strip()
        )

        # الوزن يعتمد على طول الكلام
        words = max(
            len(
                narration.split()
            ),
            1,
        )

        weights.append(words)

    total_words = sum(
        weights
    )

    if total_words <= 0:

        equal_duration = (
            total_duration
            / len(scenes)
        )

        return [
            equal_duration
            for _ in scenes
        ]

    durations = []

    for words in weights:

        duration = (
            total_duration
            * words
            / total_words
        )

        durations.append(
            duration
        )

    return durations


# =========================================================
# IMAGE → VIDEO
# =========================================================

def create_scene_video(
    image_path: Path,
    output_path: Path,
    duration: float,
    motion_type: int,
):

    logger.info(
        "Creating scene video: %.2f seconds | motion=%s",
        duration,
        motion_type,
    )

    frames = max(
        int(
            duration * FPS
        ),
        FPS,
    )

    # -----------------------------------------------------
    # حركة مختلفة حسب المشهد
    # -----------------------------------------------------

    if motion_type == 1:

        # Zoom in
        zoom = (
            "min(zoom+0.0008,1.15)"
        )

        x = (
            "iw/2-(iw/zoom/2)"
        )

        y = (
            "ih/2-(ih/zoom/2)"
        )

    elif motion_type == 2:

        # Zoom out
        zoom = (
            "if(eq(on,1),1.15,"
            "max(zoom-0.0008,1.0))"
        )

        x = (
            "iw/2-(iw/zoom/2)"
        )

        y = (
            "ih/2-(ih/zoom/2)"
        )

    elif motion_type == 3:

        # Pan left
        zoom = (
            "min(zoom+0.0007,1.12)"
        )

        x = (
            "iw/zoom/2"
        )

        y = (
            "ih/2-(ih/zoom/2)"
        )

    else:

        # Pan right
        zoom = (
            "min(zoom+0.0007,1.12)"
        )

        x = (
            "iw-iw/zoom/2"
        )

        y = (
            "ih/2-(ih/zoom/2)"
        )

    vf = (
        f"scale="
        f"{FINAL_WIDTH}:{FINAL_HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        f"zoompan="
        f"z='{zoom}':"
        f"x='{x}':"
        f"y='{y}':"
        f"d={frames}:"
        f"s={FINAL_WIDTH}x{FINAL_HEIGHT}:"
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
        "23",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]

    run_command(
        command,
        timeout=300,
    )

    return output_path


# =========================================================
# CONCAT
# =========================================================

def concatenate_videos(
    video_paths,
    output_path: Path,
):

    concat_file = (
        output_path.parent
        / (
            "concat_"
            + uuid.uuid4().hex
            + ".txt"
        )
    )

    with concat_file.open(
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

    run_command(
        command,
        timeout=300,
    )

    try:
        concat_file.unlink()
    except Exception:
        pass

    return output_path


# =========================================================
# AUDIO
# =========================================================

def add_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
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
        timeout=300,
    )

    return output_path


# =========================================================
# ASS HELPERS
# =========================================================

def escape_ass_text(text: str):

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

    # ASS يستخدم \N كسطر جديد
    text = text.replace(
        "\n",
        " ",
    )

    return text


def ass_time(seconds):

    if seconds < 0:
        seconds = 0

    h = int(
        seconds // 3600
    )

    m = int(
        (seconds % 3600) // 60
    )

    s_float = (
        seconds % 60
    )

    s = int(
        s_float
    )

    cs = int(
        round(
            (s_float - s)
            * 100
        )
    )

    if cs >= 100:
        cs = 0
        s += 1

    return (
        f"{h}:"
        f"{m:02d}:"
        f"{s:02d}."
        f"{cs:02d}"
    )


# =========================================================
# CREATE ASS
# =========================================================

def create_ass_file(
    scenes,
    durations,
    output_path: Path,
    font_info,
):

    font_family = (
        font_info["family"]
    )

    logger.info(
        "Creating Arabic subtitles "
        "using font: %s",
        font_family,
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, "
            "PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, "
            "MarginV, Encoding"
        ),

        (
            f"Style: Default,"
            f"{font_family},"
            f"42,"
            f"&H00FFFFFF,"
            f"&H00FFFFFF,"
            f"&H00000000,"
            f"&H90000000,"
            f"-1,0,0,0,"
            f"100,100,0,0,"
            f"1,3,1,"
            f"2,45,45,130,1"
        ),

        "",
        "[Events]",

        (
            "Format: Layer, Start, End, "
            "Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text"
        ),
    ]

    current_time = 0.0

    for index, scene in enumerate(
        scenes
    ):

        narration = (
            scene.get(
                "narration",
                "",
            )
            .strip()
        )

        if not narration:
            continue

        duration = durations[
            index
        ]

        start = current_time
        end = (
            current_time
            + duration
        )

        text = escape_ass_text(
            narration
        )

        # -------------------------------------------------
        # تقسيم النص الطويل إلى سطرين تقريبًا
        # -------------------------------------------------

        words = text.split()

        if len(words) > 12:

            middle = len(words) // 2

            text = (
                " ".join(
                    words[:middle]
                )
                + r"\N"
                + " ".join(
                    words[middle:]
                )
            )

        lines.append(
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,"
            f"{text}"
        )

        current_time = end

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    logger.info(
        "ASS subtitles created: %s",
        output_path,
    )

    return output_path


# =========================================================
# ADD CAPTIONS
# =========================================================

def add_captions(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    font_info,
):

    subtitle_path = str(
        ass_path
    ).replace(
        "\\",
        "/",
    )

    subtitle_path = (
        subtitle_path
        .replace(
            ":",
            "\\:",
        )
        .replace(
            "'",
            "\\'",
        )
    )

    # -----------------------------------------------------
    # fontsdir
    # -----------------------------------------------------

    filters = []

    if font_info.get("path"):

        font_dir = str(
            Path(
                font_info["path"]
            ).parent
        )

        font_dir = (
            font_dir
            .replace(
                "\\",
                "/",
            )
            .replace(
                ":",
                "\\:",
            )
            .replace(
                "'",
                "\\'",
            )
        )

        subtitle_filter = (
            f"subtitles="
            f"'{subtitle_path}'"
            f":fontsdir='{font_dir}'"
        )

    else:

        subtitle_filter = (
            f"subtitles="
            f"'{subtitle_path}'"
        )

    filters.append(
        subtitle_filter
    )

    vf = ",".join(
        filters
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
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
        str(output_path),
    ]

    run_command(
        command,
        timeout=300,
    )

    return output_path


# =========================================================
# BUILD VIDEO
# =========================================================

def build_video(
    story: str,
    job_dir: Path,
):

    logger.info(
        "========================================"
    )

    logger.info(
        "STARTING VIDEO PIPELINE"
    )

    logger.info(
        "========================================"
    )

    # =====================================================
    # 1. GROQ
    # =====================================================

    plan = generate_story_plan(
        story
    )

    scenes = plan[
        "scenes"
    ]

    logger.info(
        "FINAL STORY PLAN: %s scenes",
        len(scenes),
    )

    if len(scenes) != SCENE_COUNT:

        raise RuntimeError(
            f"Expected {SCENE_COUNT} scenes "
            f"but got {len(scenes)}."
        )

    # =====================================================
    # 2. IMAGES
    # =====================================================

    image_paths = []

    for index, scene in enumerate(
        scenes,
        start=1,
    ):

        logger.info(
            "================================"
        )

        logger.info(
            "GENERATING IMAGE %s/%s",
            index,
            len(scenes),
        )

        logger.info(
            "================================"
        )

        prompt = build_image_prompt(
            scene[
                "visual_prompt"
            ],
            index,
        )

        image_path = (
            job_dir
            / f"scene_{index}.jpg"
        )

        generate_image(
            prompt,
            image_path,
            seed=1000 + index,
        )

        image_paths.append(
            image_path
        )

    logger.info(
        "TOTAL IMAGES CREATED: %s",
        len(image_paths),
    )

    # =====================================================
    # 3. VOICE
    # =====================================================

    all_narration = (
        "\n\n".join(
            scene[
                "narration"
            ]
            for scene in scenes
        )
    )

    audio_path = (
        job_dir
        / "narration.mp3"
    )

    generate_tts(
        all_narration,
        audio_path,
    )

    audio_duration = get_duration(
        audio_path
    )

    logger.info(
        "ACTUAL VOICE DURATION: %.2f seconds",
        audio_duration,
    )

    # =====================================================
    # 4. VIDEO DURATION
    # =====================================================

    target_duration = audio_duration

    if target_duration < MIN_VIDEO_SECONDS:

        logger.info(
            "Voice is shorter than %s sec. "
            "Visual duration will follow voice.",
            MIN_VIDEO_SECONDS,
        )

    if target_duration > MAX_VIDEO_SECONDS:

        logger.warning(
            "Voice is longer than %s sec.",
            MAX_VIDEO_SECONDS,
        )

        target_duration = (
            MAX_VIDEO_SECONDS
        )

    # =====================================================
    # 5. SCENE DURATIONS
    # =====================================================

    scene_durations = (
        calculate_scene_durations(
            scenes,
            target_duration,
        )
    )

    logger.info(
        "Scene durations: %s",
        [
            round(x, 2)
            for x in scene_durations
        ],
    )

    # =====================================================
    # 6. SCENE VIDEOS
    # =====================================================

    scene_video_paths = []

    for index, image_path in enumerate(
        image_paths,
        start=1,
    ):

        video_path = (
            job_dir
            / f"scene_{index}.mp4"
        )

        motion_type = (
            ((index - 1) % 4)
            + 1
        )

        create_scene_video(
            image_path,
            video_path,
            scene_durations[
                index - 1
            ],
            motion_type,
        )

        scene_video_paths.append(
            video_path
        )

    # =====================================================
    # 7. CONCAT
    # =====================================================

    combined_video = (
        job_dir
        / "combined.mp4"
    )

    concatenate_videos(
        scene_video_paths,
        combined_video,
    )

    # =====================================================
    # 8. AUDIO
    # =====================================================

    voiced_video = (
        job_dir
        / "voiced.mp4"
    )

    add_audio(
        combined_video,
        audio_path,
        voiced_video,
    )

    # =====================================================
    # 9. ARABIC FONT
    # =====================================================

    font_info = (
        find_arabic_font()
    )

    logger.info(
        "Selected subtitle font: %s",
        font_info["family"],
    )

    # =====================================================
    # 10. ASS
    # =====================================================

    ass_path = (
        job_dir
        / "captions.ass"
    )

    create_ass_file(
        scenes,
        scene_durations,
        ass_path,
        font_info,
    )

    # =====================================================
    # 11. CAPTIONS
    # =====================================================

    final_video = (
        job_dir
        / "final.mp4"
    )

    add_captions(
        voiced_video,
        ass_path,
        final_video,
        font_info,
    )

    # =====================================================
    # 12. FINAL
    # =====================================================

    final_duration = get_duration(
        final_video
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "FINAL VIDEO READY"
    )

    logger.info(
        "Duration: %.2f seconds",
        final_duration,
    )

    logger.info(
        "Scenes: %s",
        len(scenes),
    )

    logger.info(
        "Images: %s",
        len(image_paths),
    )

    logger.info(
        "Voice: %s",
        VOICE,
    )

    logger.info(
        "Subtitle font: %s",
        font_info["family"],
    )

    logger.info(
        "========================================"
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

    logger.info(
        "Received story from Telegram user."
    )

    await update.message.reply_text(
        "🎬 استلمت القصة.\n"
        "⏳ جاري تجهيز السيناريو..."
    )

    job_id = uuid.uuid4().hex

    job_dir = (
        BASE_DIR
        / job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        loop = (
            asyncio.get_running_loop()
        )

        final_video = (
            await loop.run_in_executor(
                None,
                build_video,
                story,
                job_dir,
            )
        )

        if not final_video.exists():

            raise RuntimeError(
                "Final video was not created."
            )

        await update.message.reply_text(
            "✅ الفيديو جاهز.\n"
            "📤 جاري إرساله..."
        )

        logger.info(
            "Sending final video to Telegram..."
        )

        with final_video.open(
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🎬 تم إنشاء الفيديو بنجاح"
                ),
                supports_streaming=True,
            )

        logger.info(
            "Video sent successfully."
        )

    except Exception as exc:

        logger.exception(
            "Video generation failed."
        )

        error_message = str(
            exc
        )

        if len(error_message) > 1000:

            error_message = (
                error_message[:1000]
                + "..."
            )

        await update.message.reply_text(
            "❌ صار خطأ أثناء تجهيز الفيديو.\n\n"
            f"الخطأ: {error_message}\n\n"
            "📋 راقب Render Logs لمعرفة المرحلة التي توقفت."
        )

    finally:

        try:

            shutil.rmtree(
                job_dir,
                ignore_errors=True,
            )

        except Exception:

            pass


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Telegram handler error:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    validate_environment()

    logger.info(
        "Starting Telegram application..."
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

    application.add_error_handler(
        error_handler
    )

    await application.initialize()

    await application.start()

    logger.info(
        "Starting Telegram polling..."
    )

    await (
        application
        .updater
        .start_polling(
            drop_pending_updates=True
        )
    )

    logger.info(
        "BOT IS RUNNING."
    )

    await asyncio.Event().wait()


# =========================================================
# STARTUP
# =========================================================

if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()

    asyncio.run(
        main()
    )
