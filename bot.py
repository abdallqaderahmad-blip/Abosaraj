import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import traceback
import subprocess
import threading
import gc
import time
from pathlib import Path
from urllib.parse import quote

import requests
import edge_tts

from groq import Groq
from flask import Flask

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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

# Optional Pollinations key.
# The bot first tries the legacy endpoint that previously worked
# in this project, then uses the new authenticated endpoint if key exists.
POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
).strip()

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux"
).strip()

VOICE = os.getenv(
    "VOICE",
    "ar-SA-HamedNeural"
).strip()

# ============================================================
# VIDEO SETTINGS
# ============================================================

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# Better storytelling without killing Render RAM
SHOT_COUNT = 10

MIN_WORDS = 145
MAX_WORDS = 185

# Image generation resolution.
# Keep moderate because Render has only 512 MB RAM.
IMAGE_WIDTH = 768
IMAGE_HEIGHT = 1365

# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

# ============================================================
# GLOBAL JOB LOCK
# ============================================================

# Render should process ONE story at a time.
JOB_LOCK = threading.Lock()

# ============================================================
# FLASK
# ============================================================

flask_app = Flask(__name__)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("story_bot")


# ============================================================
# HELPERS
# ============================================================

def count_words(text: str) -> int:
    return len(
        re.findall(
            r"\S+",
            text or "",
        )
    )


def clean_json(text: str) -> str:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"```$",
            "",
            text,
        )

    return text.strip()


def run_cmd(
    command,
    timeout=900,
):
    logger.info(
        "RUN_CMD=%s",
        " ".join(map(str, command)),
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
            "CMD_STDOUT=%s",
            result.stdout[-4000:],
        )

        logger.error(
            "CMD_STDERR=%s",
            result.stderr[-6000:],
        )

        raise RuntimeError(
            f"Command failed with exit code {result.returncode}"
        )

    return result


def get_duration(path: Path) -> float:
    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=120,
    )

    return float(
        result.stdout.strip()
    )


def safe_delete(path: Path):
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.warning(
            "DELETE_FAILED %s: %s",
            path,
            e,
        )


def cleanup_memory():
    gc.collect()


# ============================================================
# GROQ — AI DIRECTOR
# ============================================================

def generate_story_plan(story: str):

    logger.info(
        "========== GROQ DIRECTOR =========="
    )

    logger.info(
        "INPUT_WORDS=%s",
        count_words(story),
    )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    system_prompt = f"""
أنت مخرج Reels محترف متخصص في قصص الرعب والغموض.

حوّل القصة العربية إلى فيديو قصير سينمائي مناسب لـ
TikTok / Instagram Reels / YouTube Shorts.

نريد بالضبط {SHOT_COUNT} مشاهد.

مهم جداً:
هذه النسخة تستخدم صور AI متحركة بواسطة FFmpeg،
لذلك يجب أن تكون كل صورة قوية بصرياً وقابلة للتحريك بالكاميرا.

ممنوع:
- slideshow عادي
- صور عامة لا علاقة لها بالقصة
- تكرار نفس الكادر
- شخص واقف بدون حدث
- نص داخل الصورة
- subtitles
- logos
- watermarks

الشخصية الرئيسية يجب أن تكون ثابتة بصرياً:

رجل عربي في أوائل الثلاثينات،
شعر أسود قصير،
لحية سوداء خفيفة،
ملابس منزلية داكنة،
ملامح واقعية.

الزوجة عند ظهورها:

امرأة عربية في الثلاثينات،
شعر أسود طويل،
ملامح واقعية.

المكان:
شقة عربية قديمة،
ليل،
إضاءة منخفضة،
ظلال قوية،
جو رعب وغموض واقعي.

STYLE:

photorealistic live action,
cinematic horror thriller,
professional cinematography,
realistic human anatomy,
realistic skin,
natural lighting,
dramatic shadows,
shallow depth of field,
film still,
35mm cinematic photography,
vertical composition,
9:16,
high detail.

كل مشهد يجب أن يحتوي على حدث بصري واضح.

أمثلة:

رجل يمسك مقبض الباب.
هاتف يهتز على الطاولة.
الرجل يلتفت نحو الممر.
ظل يظهر خلف الباب.
امرأة تقف في نهاية الممر.
الرجل يركض.
الباب يفتح.
الهاتف يسقط.
الرجل يرى شيئاً مرعباً.
الكاميرا تقترب من وجهه.

ابدأ بأقوى Hook ممكن.

آخر مشهد يجب أن يكون Twist قوي.

الناتج JSON فقط.

الصيغة:

{{
  "title": "...",
  "hook": "...",
  "shots": [
    {{
      "id": 1,
      "narration": "...",
      "prompt": "...",
      "camera": "...",
      "motion": "...",
      "mood": "..."
    }}
  ]
}}

القواعد:

- عدد المشاهد = {SHOT_COUNT}
- مجموع narration بين {MIN_WORDS} و {MAX_WORDS} كلمة.
- narration بالعربية.
- prompt بالإنجليزية.
- camera بالإنجليزية.
- motion بالإنجليزية.
- mood بالإنجليزية.
- كل مشهد مناسب تقريباً لـ 5-8 ثواني.
- كل مشهد يجب أن يغير حالة القصة.
- لا تكرر نفس زاوية الكاميرا في مشهدين متتاليين.
"""

    user_prompt = f"""
حوّل القصة التالية إلى Reel رعب سينمائي:

{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.85,
        max_tokens=6000,
        response_format={
            "type": "json_object"
        },
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

    raw = response.choices[0].message.content

    logger.info(
        "GROQ_RESPONSE_LENGTH=%s",
        len(raw or ""),
    )

    data = json.loads(
        clean_json(raw)
    )

    shots = data.get(
        "shots",
        [],
    )

    if len(shots) != SHOT_COUNT:
        raise RuntimeError(
            f"Groq returned {len(shots)} shots, expected {SHOT_COUNT}"
        )

    narration = " ".join(
        str(
            shot.get(
                "narration",
                "",
            )
        ).strip()
        for shot in shots
    )

    words = count_words(
        narration
    )

    logger.info(
        "GROQ_SHOTS=%s",
        len(shots),
    )

    logger.info(
        "GROQ_WORDS=%s",
        words,
    )

    required_fields = [
        "id",
        "narration",
        "prompt",
        "camera",
        "motion",
        "mood",
    ]

    for shot in shots:
        for field in required_fields:
            if not shot.get(field):
                raise RuntimeError(
                    f"Shot {shot.get('id')} missing field: {field}"
                )

    return data


# ============================================================
# EDGE TTS
# ============================================================

async def generate_tts_async(
    text: str,
    output_path: Path,
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
    text: str,
    output_path: Path,
):

    logger.info(
        "TTS_START words=%s",
        count_words(text),
    )

    result = {
        "error": None
    }

    def worker():

        try:
            asyncio.run(
                generate_tts_async(
                    text,
                    output_path,
                )
            )

        except Exception as e:
            result["error"] = e

    thread = threading.Thread(
        target=worker,
        daemon=True,
    )

    thread.start()
    thread.join()

    if result["error"]:
        raise result["error"]

    if not output_path.exists():
        raise RuntimeError(
            "TTS file was not created."
        )

    if output_path.stat().st_size < 1000:
        raise RuntimeError(
            "TTS file is suspiciously small."
        )

    duration = get_duration(
        output_path
    )

    logger.info(
        "TTS_SUCCESS duration=%.2f",
        duration,
    )

    return output_path, duration


# ============================================================
# POLLINATIONS IMAGE
# ============================================================

def build_image_prompt(
    shot,
):

    return f"""
Photorealistic cinematic live-action horror thriller film still.

VERTICAL 9:16.

CHARACTER CONTINUITY:
A realistic Arab man in his early 30s,
short black hair,
short dark beard,
dark home clothes,
natural realistic face,
realistic skin texture.

ENVIRONMENT:
Old Arabic apartment at night,
realistic interior,
low cinematic lighting,
deep shadows,
moody atmosphere,
realistic architecture.

SCENE:
{shot["prompt"]}

VISIBLE ACTION:
{shot["motion"]}

CAMERA:
{shot["camera"]}

MOOD:
{shot["mood"]}

Professional movie cinematography,
35mm lens,
shallow depth of field,
dramatic composition,
realistic lighting,
natural human anatomy,
photorealistic,
high detail.

No text,
no subtitles,
no captions,
no logo,
no watermark,
no illustration,
no cartoon,
no anime,
no painting,
no distorted face,
no extra fingers,
no extra limbs,
no duplicate person.
""".strip()


def generate_pollinations_image(
    shot,
    output_path: Path,
    shot_number: int,
):

    logger.info(
        "========== IMAGE GENERATION %s/%s ==========",
        shot_number,
        SHOT_COUNT,
    )

    prompt = build_image_prompt(
        shot
    )

    seed = int(
        time.time() * 1000
    ) % 2147483647

    # Add shot number so scenes remain deterministic within a job.
    seed = (
        seed + shot_number * 7919
    ) % 2147483647

    logger.info(
        "IMAGE_SEED=%s",
        seed,
    )

    encoded_prompt = quote(
        prompt,
        safe=""
    )

    # --------------------------------------------------------
    # FIRST:
    # Legacy endpoint that was previously working in this bot.
    # --------------------------------------------------------

    legacy_url = (
        f"https://image.pollinations.ai/prompt/"
        f"{encoded_prompt}"
    )

    params = {
        "model": POLLINATIONS_MODEL,
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "seed": seed,
        "nologo": "true",
        "enhance": "true",
    }

    headers = {
        "User-Agent": "Abosaraj-Story-Reel-Bot/1.0"
    }

    if POLLINATIONS_API_KEY:
        headers[
            "Authorization"
        ] = f"Bearer {POLLINATIONS_API_KEY}"

    logger.info(
        "IMAGE_URL=%s",
        legacy_url[:180],
    )

    try:

        response = requests.get(
            legacy_url,
            params=params,
            headers=headers,
            timeout=180,
        )

        logger.info(
            "IMAGE_HTTP_STATUS=%s",
            response.status_code,
        )

        if response.status_code == 200:

            output_path.write_bytes(
                response.content
            )

            size = output_path.stat().st_size

            logger.info(
                "IMAGE_FILE_SIZE=%s",
                size,
            )

            if size > 5000:

                logger.info(
                    "IMAGE_SUCCESS=%s",
                    output_path,
                )

                cleanup_memory()

                return output_path

        logger.warning(
            "LEGACY_IMAGE_FAILED status=%s body=%s",
            response.status_code,
            response.text[:300],
        )

    except Exception as e:

        logger.warning(
            "LEGACY_IMAGE_ERROR=%s",
            str(e),
        )

    # --------------------------------------------------------
    # SECOND:
    # New official endpoint when a Pollinations key exists.
    # --------------------------------------------------------

    if POLLINATIONS_API_KEY:

        new_url = (
            f"https://gen.pollinations.ai/image/"
            f"{encoded_prompt}"
        )

        new_params = {
            "model": POLLINATIONS_MODEL,
            "width": IMAGE_WIDTH,
            "height": IMAGE_HEIGHT,
            "seed": seed,
        }

        new_headers = {
            "Authorization":
                f"Bearer {POLLINATIONS_API_KEY}",
            "User-Agent":
                "Abosaraj-Story-Reel-Bot/1.0",
        }

        logger.info(
            "TRYING_NEW_POLLINATIONS_ENDPOINT"
        )

        try:

            response = requests.get(
                new_url,
                params=new_params,
                headers=new_headers,
                timeout=180,
            )

            logger.info(
                "NEW_IMAGE_HTTP_STATUS=%s",
                response.status_code,
            )

            if response.status_code == 200:

                output_path.write_bytes(
                    response.content
                )

                size = output_path.stat().st_size

                logger.info(
                    "NEW_IMAGE_FILE_SIZE=%s",
                    size,
                )

                if size > 5000:

                    logger.info(
                        "IMAGE_SUCCESS=%s",
                        output_path,
                    )

                    cleanup_memory()

                    return output_path

            else:

                logger.error(
                    "NEW_IMAGE_FAILED status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )

        except Exception as e:

            logger.error(
                "NEW_IMAGE_ERROR=%s",
                str(e),
            )

    raise RuntimeError(
        "Pollinations image generation failed."
    )


# ============================================================
# CREATE ANIMATED SCENE
# ============================================================

def create_scene_video(
    image_path: Path,
    output_path: Path,
    duration: float,
    shot_number: int,
):

    # Different camera movement for different scenes.
    movements = [
        "zoom_in",
        "zoom_out",
        "pan_left",
        "pan_right",
        "push_left",
        "push_right",
        "zoom_in",
        "pan_right",
        "zoom_out",
        "push_in",
    ]

    movement = movements[
        (shot_number - 1) % len(movements)
    ]

    frames = max(
        1,
        int(duration * FPS)
    )

    if movement == "zoom_in":

        zoom_expr = (
            "min(zoom+0.0015,1.16)"
        )

        x_expr = (
            "(iw-iw/zoom)/2"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    elif movement == "zoom_out":

        zoom_expr = (
            "if(eq(on,1),1.16,max(zoom-0.0015,1.0))"
        )

        x_expr = (
            "(iw-iw/zoom)/2"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    elif movement == "pan_left":

        zoom_expr = "1.12"

        x_expr = (
            "(iw-iw/zoom)*"
            "(1-on/total)"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    elif movement == "pan_right":

        zoom_expr = "1.12"

        x_expr = (
            "(iw-iw/zoom)*"
            "(on/total)"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    elif movement == "push_left":

        zoom_expr = (
            "min(zoom+0.001,1.10)"
        )

        x_expr = (
            "(iw-iw/zoom)*"
            "(1-on/total)"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    elif movement == "push_right":

        zoom_expr = (
            "min(zoom+0.001,1.10)"
        )

        x_expr = (
            "(iw-iw/zoom)*"
            "(on/total)"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    else:

        zoom_expr = (
            "min(zoom+0.001,1.10)"
        )

        x_expr = (
            "(iw-iw/zoom)/2"
        )

        y_expr = (
            "(ih-ih/zoom)/2"
        )

    filter_complex = (
        f"scale=800:1422:force_original_aspect_ratio=increase,"
        f"crop=800:1422,"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d={frames}:"
        f"s=720x1280:"
        f"fps={FPS},"
        f"setsar=1"
    )

    logger.info(
        "SCENE_MOVEMENT=%s duration=%.2f",
        movement,
        duration,
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            filter_complex,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "27",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=300,
    )

    if not output_path.exists():
        raise RuntimeError(
            "Scene video was not created."
        )

    logger.info(
        "SCENE_VIDEO_SUCCESS=%s",
        output_path,
    )

    return output_path


# ============================================================
# CONCAT VIDEO
# ============================================================

def concatenate_videos(
    video_paths,
    output_path: Path,
):

    concat_file = (
        output_path.parent /
        "videos.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8",
    ) as f:

        for path in video_paths:

            path_string = (
                str(path)
                .replace("\\", "/")
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{path_string}'\n"
            )

    run_cmd(
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
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=600,
    )

    return output_path


# ============================================================
# CONCAT AUDIO
# ============================================================

def concatenate_audio(
    audio_paths,
    output_path: Path,
):

    concat_file = (
        output_path.parent /
        "audio.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8",
    ) as f:

        for path in audio_paths:

            path_string = (
                str(path)
                .replace("\\", "/")
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{path_string}'\n"
            )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ],
        timeout=600,
    )

    return output_path


# ============================================================
# ARABIC FONT
# ============================================================

def find_arabic_font():

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for font in candidates:

        if Path(font).exists():
            return font

    return None


# ============================================================
# ASS TIME
# ============================================================

def ass_time(seconds: float):

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    whole_seconds = int(
        seconds % 60
    )

    centiseconds = int(
        round(
            (seconds - int(seconds))
            * 100
        )
    )

    if centiseconds >= 100:

        centiseconds = 0
        whole_seconds += 1

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{whole_seconds:02d}."
        f"{centiseconds:02d}"
    )


# ============================================================
# CREATE CAPTIONS
# ============================================================

def create_ass(
    shots,
    audio_durations,
    output_path: Path,
):

    font_path = find_arabic_font()

    if font_path:

        font_name = Path(
            font_path
        ).stem

    else:

        font_name = "DejaVu Sans"

    header = f"""
[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,3,1,2,45,45,130,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [
        header
    ]

    current_time = 0.0

    for shot, duration in zip(
        shots,
        audio_durations,
    ):

        start = current_time

        end = (
            current_time +
            duration
        )

        caption = str(
            shot["narration"]
        ).strip()

        caption = re.sub(
            r"\s+",
            " ",
            caption,
        )

        words = caption.split()

        if len(words) > 9:

            middle = (
                len(words) // 2
            )

            caption = (
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
            "Default,,0,0,0,,"
            f"{caption}\n"
        )

        current_time = end

    output_path.write_text(
        "".join(lines),
        encoding="utf-8",
    )

    return output_path


# ============================================================
# FINAL VIDEO
# ============================================================

def create_final_video(
    video_path: Path,
    audio_path: Path,
    ass_path: Path,
    output_path: Path,
):

    ass_filter_path = str(
        ass_path
    ).replace(
        "\\",
        "/",
    )

    ass_filter_path = ass_filter_path.replace(
        ":",
        "\\:",
    )

    video_filter = (
        f"ass={ass_filter_path}"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",

            "-i",
            str(video_path),

            "-i",
            str(audio_path),

            "-vf",
            video_filter,

            "-map",
            "0:v:0",

            "-map",
            "1:a:0",

            "-c:v",
            "libx264",

            "-preset",
            "ultrafast",

            "-crf",
            "27",

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-shortest",

            "-movflags",
            "+faststart",

            str(output_path),
        ],
        timeout=900,
    )

    return output_path


# ============================================================
# PROCESS STORY
# ============================================================

def process_story(
    story: str,
    workdir: Path,
):

    logger.info(
        "========================================"
    )

    logger.info(
        "START STORY"
    )

    logger.info(
        "========================================"
    )

    # --------------------------------------------------------
    # 1. GROQ
    # --------------------------------------------------------

    plan = generate_story_plan(
        story
    )

    shots = plan["shots"]

    logger.info(
        "PLAN_READY shots=%s",
        len(shots),
    )

    # --------------------------------------------------------
    # 2. TTS
    # --------------------------------------------------------

    logger.info(
        "========== TTS =========="
    )

    audio_paths = []
    audio_durations = []

    for index, shot in enumerate(
        shots,
        start=1,
    ):

        audio_path = (
            workdir /
            f"audio_{index:02d}.mp3"
        )

        _, duration = generate_tts(
            shot["narration"],
            audio_path,
        )

        audio_paths.append(
            audio_path
        )

        audio_durations.append(
            duration
        )

        cleanup_memory()

    total_audio = sum(
        audio_durations
    )

    logger.info(
        "TOTAL_AUDIO=%.2f",
        total_audio,
    )

    # --------------------------------------------------------
    # 3. GENERATE IMAGE + VIDEO ONE BY ONE
    # --------------------------------------------------------

    logger.info(
        "========== SCENES =========="
    )

    scene_paths = []

    for index, (
        shot,
        duration,
    ) in enumerate(
        zip(
            shots,
            audio_durations,
        ),
        start=1,
    ):

        image_path = (
            workdir /
            f"scene_{index:02d}.jpg"
        )

        scene_path = (
            workdir /
            f"video_{index:02d}.mp4"
        )

        logger.info(
            "========================================"
        )

        logger.info(
            "SCENE %s/%s",
            index,
            len(shots),
        )

        # IMAGE
        generate_pollinations_image(
            shot,
            image_path,
            index,
        )

        cleanup_memory()

        # VIDEO
        create_scene_video(
            image_path,
            scene_path,
            duration,
            index,
        )

        scene_paths.append(
            scene_path
        )

        # CRITICAL:
        # Delete source image immediately.
        safe_delete(
            image_path
        )

        cleanup_memory()

        logger.info(
            "SCENE_COMPLETE=%s/%s",
            index,
            len(shots),
        )

    # --------------------------------------------------------
    # 4. CONCAT VIDEO
    # --------------------------------------------------------

    logger.info(
        "========== CONCAT VIDEO =========="
    )

    combined_video = (
        workdir /
        "combined_video.mp4"
    )

    concatenate_videos(
        scene_paths,
        combined_video,
    )

    # We can now delete individual scenes
    # to reduce disk usage.
    for scene in scene_paths:
        safe_delete(scene)

    scene_paths.clear()

    cleanup_memory()

    # --------------------------------------------------------
    # 5. CONCAT AUDIO
    # --------------------------------------------------------

    logger.info(
        "========== CONCAT AUDIO =========="
    )

    combined_audio = (
        workdir /
        "combined_audio.m4a"
    )

    concatenate_audio(
        audio_paths,
        combined_audio,
    )

    # Individual audio files no longer needed.
    for audio in audio_paths:
        safe_delete(audio)

    audio_paths.clear()

    cleanup_memory()

    # --------------------------------------------------------
    # 6. CAPTIONS
    # --------------------------------------------------------

    logger.info(
        "========== CAPTIONS =========="
    )

    ass_path = (
        workdir /
        "captions.ass"
    )

    create_ass(
        shots,
        audio_durations,
        ass_path,
    )

    # --------------------------------------------------------
    # 7. FINAL VIDEO
    # --------------------------------------------------------

    logger.info(
        "========== FINAL VIDEO =========="
    )

    final_video = (
        workdir /
        "FINAL_REEL.mp4"
    )

    create_final_video(
        combined_video,
        combined_audio,
        ass_path,
        final_video,
    )

    final_duration = get_duration(
        final_video
    )

    final_size = (
        final_video.stat().st_size
    )

    logger.info(
        "FINAL_DURATION=%.2f",
        final_duration,
    )

    logger.info(
        "FINAL_SIZE=%s",
        final_size,
    )

    logger.info(
        "========== STORY COMPLETE =========="
    )

    return (
        final_video,
        plan,
    )


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    await update.message.reply_text(
        "🎬 أهلاً!\n\n"
        "ابعتلي قصة وأنا أحولها إلى Reel سينمائي."
    )


async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    story = (
        update.message.text or ""
    ).strip()

    if len(story) < 80:

        await update.message.reply_text(
            "📝 ابعت قصة أطول شوي، "
            "عشان نقدر نبني منها فيديو."
        )

        return

    if not BOT_TOKEN:

        await update.message.reply_text(
            "❌ BOT_TOKEN غير موجود."
        )

        return

    if not GROQ_API_KEY:

        await update.message.reply_text(
            "❌ GROQ_API_KEY غير موجود."
        )

        return

    # --------------------------------------------------------
    # ONE JOB ONLY
    # --------------------------------------------------------

    if not JOB_LOCK.acquire(
        blocking=False
    ):

        await update.message.reply_text(
            "⏳ في فيديو ثاني قيد المعالجة حالياً.\n"
            "استنى يخلص وبعدين ابعت القصة."
        )

        return

    job_id = uuid.uuid4().hex[:12]

    workdir = (
        BASE_DIR /
        job_id
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger.info(
        "NEW_JOB=%s",
        job_id,
    )

    status = await update.message.reply_text(
        "🎬 استلمت القصة.\n\n"
        "🧠 جاري بناء السيناريو..."
    )

    try:

        loop = asyncio.get_running_loop()

        final_video, plan = (
            await loop.run_in_executor(
                None,
                process_story,
                story,
                workdir,
            )
        )

        await status.edit_text(
            "🎬 الفيديو خلص.\n"
            "📤 جاري إرساله..."
        )

        title = plan.get(
            "title",
            "AI Reel",
        )

        caption = (
            f"🎬 {title}\n\n"
            "🤖 AI Generated Reel"
        )

        with open(
            final_video,
            "rb",
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=caption,
                supports_streaming=True,
                width=FINAL_WIDTH,
                height=FINAL_HEIGHT,
            )

        try:
            await status.delete()
        except Exception:
            pass

        logger.info(
            "JOB_SUCCESS=%s",
            job_id,
        )

    except Exception as e:

        logger.error(
            "JOB_FAILED=%s",
            job_id,
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
            "TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        try:

            await status.edit_text(
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                f"{type(e).__name__}: "
                f"{str(e)[:800]}"
            )

        except Exception:
            pass

    finally:

        try:

            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )

        except Exception:
            pass

        cleanup_memory()

        JOB_LOCK.release()


# ============================================================
# FLASK HEALTH
# ============================================================

@flask_app.route("/")
def home():

    return (
        "Abosaraj Story Reel Bot is alive."
    )


@flask_app.route("/health")
def health():

    return {
        "status": "ok",
        "engine": "Pollinations Image + FFmpeg",
        "shots": SHOT_COUNT,
        "resolution": f"{FINAL_WIDTH}x{FINAL_HEIGHT}",
        "fps": FPS,
    }


# ============================================================
# FLASK THREAD
# ============================================================

def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    logger.info(
        "FLASK_START port=%s",
        port,
    )

    flask_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "STORY REEL BOT STARTING"
    )

    logger.info(
        "========================================"
    )

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing."
        )

    logger.info(
        "GROQ_MODEL=%s",
        GROQ_MODEL,
    )

    logger.info(
        "POLLINATIONS_MODEL=%s",
        POLLINATIONS_MODEL,
    )

    logger.info(
        "SHOT_COUNT=%s",
        SHOT_COUNT,
    )

    logger.info(
        "IMAGE_SIZE=%sx%s",
        IMAGE_WIDTH,
        IMAGE_HEIGHT,
    )

    logger.info(
        "VIDEO_SIZE=%sx%s",
        FINAL_WIDTH,
        FINAL_HEIGHT,
    )

    logger.info(
        "POLLINATIONS_KEY=%s",
        "YES" if POLLINATIONS_API_KEY else "NO",
    )

    # --------------------------------------------------------
    # Start Flask in background
    # --------------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
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

    application.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
