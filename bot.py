import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import traceback
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
FAL_KEY = os.getenv("FAL_KEY", "").strip()

VOICE = "ar-SA-HamedNeural"

# Wan 2.1
WAN_MODEL = "fal-ai/wan-t2v"

# 81 frames / 16 fps ~= 5.06 sec
WAN_FRAMES = 81
WAN_FPS = 16
WAN_RESOLUTION = "480p"

# Number of actual AI video shots
SHOT_COUNT = 14

# Arabic story target
MIN_WORDS = 145
MAX_WORDS = 185

# Final video target
MIN_SECONDS = 55
MAX_SECONDS = 85

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(parents=True, exist_ok=True)

app_flask = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("story_bot")


# ============================================================
# BASIC HELPERS
# ============================================================

def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def clean_json_text(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.I)
        text = re.sub(r"```$", "", text.strip())

    return text.strip()


def run_cmd(cmd, timeout=900):
    logger.info("RUN_CMD=%s", " ".join(map(str, cmd)))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        logger.error("CMD_STDOUT=%s", result.stdout[-4000:])
        logger.error("CMD_STDERR=%s", result.stderr[-4000:])
        raise RuntimeError(
            f"Command failed with code {result.returncode}"
        )

    return result


def ffprobe_duration(path: Path) -> float:
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

    return float(result.stdout.strip())


# ============================================================
# GROQ DIRECTOR
# ============================================================

def generate_story_plan(user_story: str):
    logger.info("========== GROQ DIRECTOR ==========")
    logger.info("INPUT_WORDS=%s", count_words(user_story))

    client = Groq(api_key=GROQ_API_KEY)

    system_prompt = """
أنت مخرج أفلام قصيرة محترف متخصص في Reels وTikTok وShorts.

مهمتك تحويل القصة العربية إلى فيديو قصير سينمائي شديد الجاذبية.

ممنوع تقسيم القصة إلى 4 مشاهد فقط.
نريد 14 لقطة VIDEO حقيقية.

كل لقطة يجب أن تحتوي على:
- narration: الجملة العربية التي سيقولها الراوي في هذه اللقطة.
- prompt: وصف سينمائي باللغة الإنجليزية لتوليد فيديو AI حقيقي.
- camera: حركة الكاميرا.
- action: الحركة الأساسية.
- mood: الجو والمشاعر.

مهم جداً:
كل لقطة يجب أن تحتوي على حركة حقيقية.
لا تكتب:
"still image"
"static image"
"photo"
"zoom on a picture"

نريد أفعالاً مثل:
walks
opens
turns
looks
runs
phone vibrates
door shakes
camera moves
shadow passes
tears fall
police enters

اجعل اللقطات مترابطة بصرياً.

الشخصية الرئيسية يجب أن تبقى ثابتة:
رجل عربي في الثلاثينات، شعر أسود قصير، لحية خفيفة، ملابس منزلية داكنة.

الزوجة المتوفاة:
امرأة عربية في الثلاثينات، شعر أسود طويل، مظهر هادئ ومرعب عند ظهورها.

المكان:
شقة عربية قديمة، أجواء ليلية واقعية، إضاءة سينمائية منخفضة.

STYLE:
cinematic realistic live-action thriller,
photorealistic,
dark atmospheric lighting,
shallow depth of field,
real human motion,
professional camera movement,
high detail,
vertical social media composition,
9:16.

لا تجعل الشخصيات تتكلم أمام الكاميرا إلا إذا كان ذلك ضرورياً.
الصوت سيأتي من الراوي لاحقاً.

ممنوع وضع أي subtitles أو text داخل الفيديو.

الناتج JSON فقط بالشكل التالي:

{
  "title": "...",
  "hook": "...",
  "shots": [
    {
      "id": 1,
      "narration": "...",
      "prompt": "...",
      "camera": "...",
      "action": "...",
      "mood": "..."
    }
  ]
}

يجب أن يكون عدد shots بالضبط 14.

يجب أن تكون narration الكاملة بين 145 و185 كلمة.

كل narration قصيرة ومناسبة تقريباً لـ4-6 ثوانٍ من الصوت.

ابدأ بأقوى Hook.
اجعل آخر لقطة هي الـ Twist الأقوى.
"""

    user_prompt = f"""
حوّل القصة التالية إلى Reel رعب/غموض احترافي:

{user_story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.85,
        max_tokens=6000,
        response_format={"type": "json_object"},
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
    logger.info("GROQ_RAW_LENGTH=%s", len(raw or ""))

    data = json.loads(clean_json_text(raw))

    shots = data.get("shots", [])

    if len(shots) != SHOT_COUNT:
        raise RuntimeError(
            f"Groq returned {len(shots)} shots instead of {SHOT_COUNT}."
        )

    narration = " ".join(
        str(s.get("narration", "")).strip()
        for s in shots
    )

    words = count_words(narration)

    logger.info("GROQ_SHOTS=%s", len(shots))
    logger.info("GROQ_WORDS=%s", words)

    if words < MIN_WORDS or words > MAX_WORDS:
        logger.warning(
            "WORD_COUNT_OUT_OF_RANGE=%s",
            words,
        )

    for shot in shots:
        required = [
            "id",
            "narration",
            "prompt",
            "camera",
            "action",
            "mood",
        ]

        for key in required:
            if not shot.get(key):
                raise RuntimeError(
                    f"Shot {shot.get('id')} missing {key}"
                )

    return data


# ============================================================
# TTS
# ============================================================

async def _tts_async(text: str, output_path: Path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate="+0%",
        volume="+0%",
        pitch="+0Hz",
    )

    await communicate.save(str(output_path))


def generate_tts(text: str, output_path: Path):
    logger.info("TTS_START words=%s", count_words(text))

    result = {"error": None}

    def worker():
        try:
            asyncio.run(
                _tts_async(
                    text,
                    output_path,
                )
            )
        except Exception as e:
            result["error"] = e

    thread = __import__("threading").Thread(
        target=worker,
        daemon=True,
    )

    thread.start()
    thread.join()

    if result["error"]:
        raise result["error"]

    if not output_path.exists():
        raise RuntimeError("TTS output missing.")

    size = output_path.stat().st_size

    if size < 1000:
        raise RuntimeError("TTS output suspiciously small.")

    duration = ffprobe_duration(output_path)

    logger.info(
        "TTS_SUCCESS file=%s duration=%.2f",
        output_path.name,
        duration,
    )

    return output_path, duration


# ============================================================
# WAN 2.1 VIDEO
# ============================================================

def generate_wan_video(shot, output_path: Path, shot_number: int):
    logger.info(
        "========== WAN SHOT %s/%s ==========",
        shot_number,
        SHOT_COUNT,
    )

    # Build a strong cinematic prompt.
    prompt = f"""
Vertical 9:16 cinematic live-action horror thriller.

Main character continuity:
A realistic Arab man in his early 30s,
short black hair,
short dark beard,
dark home clothes.

Environment continuity:
old Arabic apartment,
night,
realistic interior,
low cinematic lighting,
deep shadows,
photorealistic live-action.

SHOT ACTION:
{shot["action"]}

CAMERA:
{shot["camera"]}

MOOD:
{shot["mood"]}

SCENE:
{shot["prompt"]}

The subject performs the action naturally.
Realistic human body movement.
Natural physics.
Subtle facial emotion.
Cinematic camera movement.
Photorealistic skin and environment.
Professional thriller cinematography.
Strong depth of field.
No text.
No subtitles.
No logos.
No watermark.
"""

    negative_prompt = """
static image,
still picture,
slideshow,
painting,
illustration,
cartoon,
anime,
low quality,
blurry,
deformed face,
extra fingers,
extra limbs,
fused fingers,
bad hands,
duplicate person,
duplicate body,
unnatural motion,
walking backwards,
floating objects,
text,
subtitles,
watermark,
logo
"""

    try:
        result = fal_client.subscribe(
            WAN_MODEL,
            arguments={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
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

        video_info = result.get("video")

        if not video_info:
            raise RuntimeError(
                f"Wan returned no video: {result}"
            )

        video_url = video_info.get("url")

        if not video_url:
            raise RuntimeError(
                f"Wan returned no URL: {result}"
            )

        logger.info(
            "WAN_URL=%s",
            video_url,
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
                "Wan video was not saved."
            )

        logger.info(
            "WAN_SUCCESS shot=%s size=%s",
            shot_number,
            output_path.stat().st_size,
        )

        return output_path

    except Exception as e:
        logger.error(
            "WAN_ERROR shot=%s type=%s",
            shot_number,
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
# VIDEO NORMALIZATION
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

            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",

            "-an",

            "-movflags",
            "+faststart",

            str(output_path),
        ],
        timeout=600,
    )

    return output_path


# ============================================================
# CONCAT VIDEO CLIPS
# ============================================================

def concatenate_videos(
    video_paths,
    output_path: Path,
):
    concat_file = output_path.parent / "concat.txt"

    with open(
        concat_file,
        "w",
        encoding="utf-8",
    ) as f:
        for path in video_paths:
            safe_path = str(path).replace(
                "'",
                "'\\''",
            )

            f.write(
                f"file '{safe_path}'\n"
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

            str(output_path),
        ],
        timeout=600,
    )

    return output_path


# ============================================================
# BUILD AUDIO
# ============================================================

def concatenate_audio(
    audio_paths,
    output_path: Path,
):
    concat_file = output_path.parent / "audio_concat.txt"

    with open(
        concat_file,
        "w",
        encoding="utf-8",
    ) as f:
        for path in audio_paths:
            safe_path = str(path).replace(
                "'",
                "'\\''",
            )

            f.write(
                f"file '{safe_path}'\n"
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
# ASS ARABIC CAPTIONS
# ============================================================

def find_arabic_font():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in candidates:
        if Path(path).exists():
            return path

    return None


def ass_time(seconds: float):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))

    if cs >= 100:
        cs = 0
        s += 1

    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def create_ass(
    shots,
    audio_durations,
    output_path: Path,
):
    font_path = find_arabic_font()

    if font_path:
        font_name = Path(font_path).stem
    else:
        font_name = "DejaVu Sans"

    header = f"""[Script Info]
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

    current = 0.0

    lines = [header]

    for shot, duration in zip(
        shots,
        audio_durations,
    ):
        start = current
        end = current + duration

        text = str(
            shot["narration"]
        ).strip()

        # ASS line breaks
        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        # Keep captions readable
        words = text.split()

        if len(words) > 9:
            midpoint = len(words) // 2
            text = (
                " ".join(words[:midpoint])
                + r"\N"
                + " ".join(words[midpoint:])
            )

        lines.append(
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,"
            f"{text}\n"
        )

        current = end

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
    video_filter = (
        f"ass={str(ass_path).replace(':', '\\:')}"
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

def process_story(story: str, workdir: Path):
    logger.info("========================================")
    logger.info("START STORY")
    logger.info("========================================")

    plan = generate_story_plan(story)

    shots = plan["shots"]

    # --------------------------------------------------------
    # TTS first
    # --------------------------------------------------------

    logger.info("========== GENERATING SHOT AUDIO ==========")

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

        audio_paths.append(audio_path)
        audio_durations.append(duration)

    total_audio = sum(audio_durations)

    logger.info(
        "TOTAL_AUDIO_SECONDS=%.2f",
        total_audio,
    )

    # --------------------------------------------------------
    # Video generation
    # --------------------------------------------------------

    logger.info(
        "========== GENERATING %s WAN VIDEOS ==========",
        len(shots),
    )

    raw_video_paths = [
        workdir / f"wan_{i:02d}.mp4"
        for i in range(1, len(shots) + 1)
    ]

    # Start several jobs at the same time.
    # This massively reduces total waiting time.
    max_workers = min(3, len(shots))

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as executor:

        futures = {}

        for index, (shot, output_path) in enumerate(
            zip(shots, raw_video_paths),
            start=1,
        ):
            future = executor.submit(
                generate_wan_video,
                shot,
                output_path,
                index,
            )

            futures[future] = index

        for future in as_completed(futures):
            index = futures[future]

            try:
                future.result()

                logger.info(
                    "WAN_SHOT_COMPLETED=%s",
                    index,
                )

            except Exception:
                logger.error(
                    "WAN_SHOT_FAILED=%s",
                    index,
                )
                raise

    # --------------------------------------------------------
    # Normalize
    # --------------------------------------------------------

    normalized_paths = []

    for index, raw_path in enumerate(
        raw_video_paths,
        start=1,
    ):
        normalized = (
            workdir /
            f"normalized_{index:02d}.mp4"
        )

        normalize_video(
            raw_path,
            normalized,
        )

        normalized_paths.append(
            normalized
        )

    # --------------------------------------------------------
    # Concatenate
    # --------------------------------------------------------

    combined_video = (
        workdir /
        "combined_video.mp4"
    )

    concatenate_videos(
        normalized_paths,
        combined_video,
    )

    # --------------------------------------------------------
    # Audio
    # --------------------------------------------------------

    combined_audio = (
        workdir /
        "combined_audio.m4a"
    )

    concatenate_audio(
        audio_paths,
        combined_audio,
    )

    # --------------------------------------------------------
    # Captions
    # --------------------------------------------------------

    ass_file = (
        workdir /
        "captions.ass"
    )

    create_ass(
        shots,
        audio_durations,
        ass_file,
    )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    final_video = (
        workdir /
        "FINAL_REEL.mp4"
    )

    create_final_video(
        combined_video,
        combined_audio,
        ass_file,
        final_video,
    )

    duration = ffprobe_duration(
        final_video
    )

    logger.info(
        "FINAL_DURATION=%.2f",
        duration,
    )

    logger.info(
        "FINAL_SIZE=%s",
        final_video.stat().st_size,
    )

    return final_video, plan


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🎬 ابعتلي القصة، وأنا أحولها إلى Reel سينمائي AI."
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
            "ابعت قصة أطول شوي، عشان أقدر أبني منها مشاهد سينمائية قوية."
        )
        return

    if not BOT_TOKEN:
        await update.message.reply_text(
            "❌ BOT_TOKEN غير مضبوط."
        )
        return

    if not GROQ_API_KEY:
        await update.message.reply_text(
            "❌ GROQ_API_KEY غير مضبوط."
        )
        return

    if not FAL_KEY:
        await update.message.reply_text(
            "❌ FAL_KEY غير مضبوط في Render."
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

    status_message = await update.message.reply_text(
        "🎬 استلمت القصة.\n"
        "🧠 Groq يحللها ويقسمها إلى لقطات سينمائية..."
    )

    try:
        loop = asyncio.get_running_loop()

        final_video, plan = await loop.run_in_executor(
            None,
            process_story,
            story,
            workdir,
        )

        await status_message.edit_text(
            "🎬 الفيديو خلص.\n"
            "📤 جاري إرساله..."
        )

        caption = (
            f"🎬 {plan.get('title', 'AI Reel')}\n\n"
            "🤖 Generated automatically"
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

        await status_message.delete()

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
            await status_message.edit_text(
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                f"{type(e).__name__}: {str(e)[:700]}"
            )
        except Exception:
            pass

    finally:
        # Keep logs/files only during the job.
        try:
            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )
        except Exception:
            pass


# ============================================================
# FLASK
# ============================================================

@app_flask.route("/")
def health():
    return "Story Reel Bot is running."


@app_flask.route("/health")
def health_check():
    return {
        "status": "ok",
        "video_model": WAN_MODEL,
        "shots": SHOT_COUNT,
    }


# ============================================================
# MAIN
# ============================================================

def main():
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

    # fal-client reads FAL_KEY from environment.
    logger.info("BOT_START")
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
            filters.TEXT & ~filters.COMMAND,
            handle_story,
        )
    )

    application.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
