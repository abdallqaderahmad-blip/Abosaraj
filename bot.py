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

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import edge_tts
import fal_client

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

FAL_KEY = os.getenv("FAL_KEY", "").strip()

VOICE = "ar-SA-HamedNeural"

# ------------------------------------------------------------
# WAN 2.1
# ------------------------------------------------------------

WAN_MODEL = "fal-ai/wan-t2v"

WAN_FRAMES = 81
WAN_FPS = 16
WAN_RESOLUTION = "480p"

# Number of AI video shots
SHOT_COUNT = 14

# Maximum parallel video generations
MAX_PARALLEL_WAN = 3

# ------------------------------------------------------------
# Story limits
# ------------------------------------------------------------

MIN_WORDS = 145
MAX_WORDS = 185

# ------------------------------------------------------------
# Directories
# ------------------------------------------------------------

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

# ------------------------------------------------------------
# Flask
# ------------------------------------------------------------

flask_app = Flask(__name__)

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

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
            result.stdout[-5000:],
        )

        logger.error(
            "CMD_STDERR=%s",
            result.stderr[-5000:],
        )

        raise RuntimeError(
            f"Command failed with exit code {result.returncode}"
        )

    return result


def get_duration(
    path: Path,
) -> float:

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


# ============================================================
# GROQ — AI DIRECTOR
# ============================================================

def generate_story_plan(
    story: str,
):

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
أنت مخرج أفلام قصيرة محترف متخصص في TikTok وInstagram Reels وYouTube Shorts.

مهمتك تحويل القصة العربية إلى فيديو رعب/غموض سينمائي احترافي.

نحن نريد بالضبط {SHOT_COUNT} لقطات فيديو حقيقية.

ممنوع عمل Slideshow.
ممنوع صور ثابتة.
ممنوع وصف Zoom على صورة.

كل Shot يجب أن يكون فيه ACTION حقيقي.

أمثلة:
- الرجل يمشي باتجاه الباب.
- مقبض الباب يهتز.
- الهاتف يهتز ويرن.
- الرجل يلتفت فجأة.
- الكاميرا تتحرك داخل الممر.
- ظل يمر خلف الباب.
- الشرطة تدخل.
- الرجل ينظر للهاتف.
- دمعة تنزل.
- الباب يفتح ببطء.

الشخصية الرئيسية يجب أن تبقى متقاربة بصرياً في كل اللقطات:

رجل عربي في أوائل الثلاثينات،
شعر أسود قصير،
لحية سوداء خفيفة،
ملابس منزلية داكنة،
مظهر واقعي.

الزوجة:
امرأة عربية في الثلاثينات،
شعر أسود طويل،
مظهر واقعي،
هادئة ومخيفة عند ظهورها.

المكان:
شقة عربية قديمة،
ليل،
إضاءة منخفضة،
أجواء رعب واقعية.

STYLE:

photorealistic live action,
cinematic thriller,
realistic human movement,
realistic physics,
dark atmospheric lighting,
shallow depth of field,
professional cinematography,
vertical 9:16,
high detail,
real camera movement.

لا تضف:
subtitles
captions
text
logos
watermarks

الصوت سيتم توليده منفصلاً بواسطة Edge TTS.

ابدأ بأقوى Hook ممكن.

كل لقطة يجب أن تحرك القصة.

آخر لقطة يجب أن تحتوي على الـTWIST.

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
      "action": "...",
      "mood": "..."
    }}
  ]
}}

قواعد مهمة:

- عدد shots = {SHOT_COUNT}
- مجموع narration بين {MIN_WORDS} و {MAX_WORDS} كلمة.
- narration عربية.
- prompt باللغة الإنجليزية.
- camera باللغة الإنجليزية.
- action باللغة الإنجليزية.
- mood باللغة الإنجليزية.
- كل لقطة مناسبة تقريباً لـ4-6 ثواني.
- لا تجعل كل لقطة مجرد شخص واقف.
- اجعل الحركة واضحة ومباشرة.
"""

    user_prompt = f"""
حوّل القصة التالية إلى Reel سينمائي احترافي:

{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.85,
        max_tokens=7000,
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
            f"Groq returned {len(shots)} shots, expected {SHOT_COUNT}."
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

    if words < MIN_WORDS:
        logger.warning(
            "Narration is shorter than target: %s",
            words,
        )

    if words > MAX_WORDS:
        logger.warning(
            "Narration is longer than target: %s",
            words,
        )

    required_fields = [
        "id",
        "narration",
        "prompt",
        "camera",
        "action",
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

    size = output_path.stat().st_size

    if size < 1000:

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
# WAN 2.1
# ============================================================

def build_wan_prompt(
    shot,
):

    return f"""
Vertical 9:16 cinematic live-action horror thriller.

CHARACTER CONTINUITY:

A realistic Arab man in his early 30s,
short black hair,
short dark beard,
dark home clothes.

ENVIRONMENT:

Old Arabic apartment at night.
Realistic interior.
Low cinematic lighting.
Deep shadows.
Photorealistic environment.

SCENE:

{shot["prompt"]}

ACTION:

{shot["action"]}

CAMERA:

{shot["camera"]}

MOOD:

{shot["mood"]}

The action must visibly happen during the video.

Natural human body movement.
Natural physics.
Realistic facial expressions.
Realistic object movement.
Cinematic camera movement.
Photorealistic live action.
Professional horror movie cinematography.

No talking to camera.
No subtitles.
No captions.
No text.
No logo.
No watermark.
"""


def generate_wan_video(
    shot,
    output_path: Path,
    shot_number: int,
):

    logger.info(
        "========== WAN SHOT %s/%s ==========",
        shot_number,
        SHOT_COUNT,
    )

    prompt = build_wan_prompt(
        shot
    )

    negative_prompt = """
bright colors,
overexposed,
static,
blurred details,
subtitles,
captions,
text,
logo,
watermark,
painting,
illustration,
anime,
cartoon,
still image,
still picture,
slideshow,
low quality,
JPEG artifacts,
deformed face,
bad hands,
extra fingers,
extra limbs,
fused fingers,
duplicate person,
duplicate body,
unnatural motion,
floating objects,
walking backwards,
three legs,
many people in background
"""

    logger.info(
        "WAN_PROMPT_LENGTH=%s",
        len(prompt),
    )

    try:

        result = fal_client.subscribe(
            WAN_MODEL,
            arguments={
                "prompt": prompt,
                "negative_prompt": negative_prompt,

                # Official Wan 2.1 range:
                # 81-100
                "num_frames": WAN_FRAMES,

                "frames_per_second": WAN_FPS,

                "resolution": WAN_RESOLUTION,

                "aspect_ratio": "9:16",

                "num_inference_steps": 30,

                "enable_safety_checker": True,

                "enable_prompt_expansion": False,

                "turbo_mode": True,
            },

            with_logs=True,
        )

        if not result:

            raise RuntimeError(
                "Wan returned empty result."
            )

        video = result.get(
            "video"
        )

        if not video:

            raise RuntimeError(
                f"Wan returned no video: {result}"
            )

        video_url = video.get(
            "url"
        )

        if not video_url:

            raise RuntimeError(
                f"Wan returned no video URL: {result}"
            )

        logger.info(
            "WAN_VIDEO_URL_RECEIVED shot=%s",
            shot_number,
        )

        response = requests.get(
            video_url,
            timeout=300,
        )

        response.raise_for_status()

        output_path.write_bytes(
            response.content
        )

        if not output_path.exists():

            raise RuntimeError(
                "Wan output file missing."
            )

        file_size = (
            output_path.stat().st_size
        )

        if file_size < 10000:

            raise RuntimeError(
                "Wan output file is too small."
            )

        logger.info(
            "WAN_SUCCESS shot=%s size=%s",
            shot_number,
            file_size,
        )

        return output_path

    except Exception as e:

        logger.error(
            "WAN_ERROR shot=%s",
            shot_number,
        )

        logger.error(
            "WAN_ERROR_TYPE=%s",
            type(e).__name__,
        )

        logger.error(
            "WAN_ERROR_MESSAGE=%s",
            str(e),
        )

        logger.error(
            "WAN_TRACEBACK:\n%s",
            traceback.format_exc(),
        )

        raise


# ============================================================
# NORMALIZE VIDEO
# ============================================================

def normalize_video(
    input_path: Path,
    output_path: Path,
):

    run_cmd(
        [
            "ffmpeg",
            "-y",

            "-i",
            str(input_path),

            "-vf",
            (
                "scale=720:1280:"
                "force_original_aspect_ratio=increase,"
                "crop=720:1280,"
                "setsar=1"
            ),

            "-r",
            "30",

            "-an",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "23",

            "-pix_fmt",
            "yuv420p",

            "-movflags",
            "+faststart",

            str(output_path),
        ],
        timeout=600,
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
            "192k",

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

def ass_time(
    seconds: float,
):

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

        # Split long captions into two lines
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

    # IMPORTANT:
    # Do NOT put .replace("\\", "\\:")
    # directly inside an f-string.
    # Python 3.11 throws:
    # SyntaxError: f-string expression part cannot include a backslash

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
            "veryfast",

            "-crf",
            "22",

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "192k",

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
    # 2. TTS PER SHOT
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

    total_audio = sum(
        audio_durations
    )

    logger.info(
        "TOTAL_AUDIO=%.2f",
        total_audio,
    )

    # --------------------------------------------------------
    # 3. WAN VIDEOS
    # --------------------------------------------------------

    logger.info(
        "========== WAN VIDEO =========="
    )

    raw_paths = []

    for index in range(
        1,
        len(shots) + 1,
    ):

        raw_paths.append(
            workdir /
            f"wan_{index:02d}.mp4"
        )

    logger.info(
        "WAN_PARALLEL_WORKERS=%s",
        min(
            MAX_PARALLEL_WAN,
            len(shots),
        ),
    )

    with ThreadPoolExecutor(
        max_workers=min(
            MAX_PARALLEL_WAN,
            len(shots),
        )
    ) as executor:

        futures = {}

        for index, (
            shot,
            output_path,
        ) in enumerate(
            zip(
                shots,
                raw_paths,
            ),
            start=1,
        ):

            future = executor.submit(
                generate_wan_video,
                shot,
                output_path,
                index,
            )

            futures[future] = index

        for future in as_completed(
            futures
        ):

            shot_number = futures[
                future
            ]

            try:

                future.result()

                logger.info(
                    "WAN_COMPLETED=%s/%s",
                    shot_number,
                    len(shots),
                )

            except Exception:

                logger.error(
                    "WAN_FAILED=%s",
                    shot_number,
                )

                raise

    # --------------------------------------------------------
    # 4. NORMALIZE
    # --------------------------------------------------------

    logger.info(
        "========== NORMALIZE =========="
    )

    normalized_paths = []

    for index, raw_path in enumerate(
        raw_paths,
        start=1,
    ):

        normalized_path = (
            workdir /
            f"normalized_{index:02d}.mp4"
        )

        normalize_video(
            raw_path,
            normalized_path,
        )

        normalized_paths.append(
            normalized_path
        )

    # --------------------------------------------------------
    # 5. CONCAT VIDEO
    # --------------------------------------------------------

    logger.info(
        "========== CONCAT VIDEO =========="
    )

    combined_video = (
        workdir /
        "combined_video.mp4"
    )

    concatenate_videos(
        normalized_paths,
        combined_video,
    )

    # --------------------------------------------------------
    # 6. CONCAT AUDIO
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

    # --------------------------------------------------------
    # 7. CAPTIONS
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
    # 8. FINAL VIDEO
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
        "ابعتلي قصة، وأنا أحولها إلى Reel سينمائي AI "
        "بمشاهد فيديو حقيقية."
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
            "عشان نقدر نبني منها مشاهد حقيقية."
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

    if not FAL_KEY:

        await update.message.reply_text(
            "❌ FAL_KEY غير موجود في Render."
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
        "🧠 جاري تحويلها إلى سيناريو سينمائي..."
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
            "🎬 خلص التوليد.\n"
            "📤 جاري تجهيز الفيديو للإرسال..."
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
                width=720,
                height=1280,
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
                f"{str(e)[:1000]}"
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


# ============================================================
# FLASK HEALTH
# ============================================================

@flask_app.route("/")
def home():

    return (
        "Story Reel Bot is running."
    )


@flask_app.route("/health")
def health():

    return {
        "status": "ok",
        "model": WAN_MODEL,
        "shots": SHOT_COUNT,
        "resolution": WAN_RESOLUTION,
    }


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

    if not FAL_KEY:

        raise RuntimeError(
            "FAL_KEY is missing."
        )

    logger.info(
        "GROQ_MODEL=%s",
        GROQ_MODEL,
    )

    logger.info(
        "WAN_MODEL=%s",
        WAN_MODEL,
    )

    logger.info(
        "WAN_RESOLUTION=%s",
        WAN_RESOLUTION,
    )

    logger.info(
        "WAN_FRAMES=%s",
        WAN_FRAMES,
    )

    logger.info(
        "WAN_FPS=%s",
        WAN_FPS,
    )

    logger.info(
        "SHOT_COUNT=%s",
        SHOT_COUNT,
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

    application.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":

    main()
