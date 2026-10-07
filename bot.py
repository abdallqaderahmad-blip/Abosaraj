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
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

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

# اختبار أولي
SCENE_COUNT = 2

MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 120

VOICE = "ar-SA-HamedNeural"

BASE_DIR = Path("/tmp/story_bot")
BASE_DIR.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = 2


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
    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# VALIDATION
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
    logger.info("Groq model: %s", GROQ_MODEL)
    logger.info("Pollinations model: %s", POLLINATIONS_MODEL)


# =========================================================
# GROQ
# =========================================================

groq_client = None


def extract_json_from_text(text: str):
    """
    يحاول استخراج JSON حتى لو Groq رجعه:
    - داخل ```json ... ```
    - مع كلام قبل/بعد JSON
    - أو كـ JSON object عادي
    """

    if not text:
        raise ValueError("Groq returned an empty response.")

    text = text.strip()

    # إزالة code fences
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

    # المحاولة الأولى: النص كاملًا
    try:
        return json.loads(text)
    except Exception:
        pass

    # محاولة استخراج أول object JSON
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    # محاولة استخراج array
    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end > start:
        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise ValueError(
        "Could not extract valid JSON from Groq response."
    )


def normalize_story_plan(data):
    """
    يحوّل أشكال JSON المحتملة إلى الشكل الذي نستخدمه.
    """

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

    raise ValueError("Groq JSON has unsupported structure.")


def validate_story_plan(data):
    data = normalize_story_plan(data)

    scenes = data.get("scenes")

    if not isinstance(scenes, list):
        raise ValueError(
            "Groq response does not contain a valid 'scenes' list."
        )

    if len(scenes) == 0:
        raise ValueError("Groq returned zero scenes.")

    cleaned_scenes = []

    for index, scene in enumerate(scenes):

        if isinstance(scene, str):
            cleaned_scenes.append({
                "narration": scene,
                "visual_prompt": scene,
            })
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

        narration = str(narration).strip()
        visual_prompt = str(visual_prompt).strip()

        if not narration:
            continue

        if not visual_prompt:
            visual_prompt = narration

        cleaned_scenes.append({
            "narration": narration,
            "visual_prompt": visual_prompt,
        })

    if not cleaned_scenes:
        raise ValueError(
            "Groq returned scenes but none contained narration."
        )

    # نلتزم بعدد المشاهد المطلوب للاختبار
    cleaned_scenes = cleaned_scenes[:SCENE_COUNT]

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
        groq_client = Groq(api_key=GROQ_API_KEY)

    logger.info("Generating story plan with Groq...")

    system_prompt = f"""
أنت كاتب ومخرج محتوى قصص قصير للفيديوهات العمودية.

حوّل القصة التي يرسلها المستخدم إلى سيناريو فيديو قصصي قوي.

المطلوب:
- اللغة العربية.
- أسلوب جذاب وسهل السماع.
- لا تختلق أحداثًا رئيسية غير موجودة في القصة.
- اجعل السرد مناسبًا لصوت رجل.
- قسم القصة إلى {SCENE_COUNT} مشاهد.
- كل مشهد يحتوي على نص سردي وصورة مناسبة.

مهم جدًا:
يجب أن يكون ردك JSON فقط.
ممنوع كتابة أي شرح خارج JSON.
ممنوع Markdown.
ممنوع ```json.

الصيغة المطلوبة حرفيًا:

{{
  "title": "عنوان القصة",
  "scenes": [
    {{
      "narration": "النص الذي سيقرأه الراوي",
      "visual_prompt": "وصف سينمائي باللغة الإنجليزية للصورة"
    }}
  ]
}}
"""

    user_prompt = f"""
القصة:

{story}

حوّل هذه القصة الآن إلى JSON فقط.
"""

    last_error = None

    for attempt in range(1, 4):

        try:
            logger.info(
                "Calling Groq attempt %s/3...",
                attempt,
            )

            response = groq_client.chat.completions.create(
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
                max_tokens=2500,
            )

            logger.info("Groq HTTP request completed.")

            if not response or not response.choices:
                raise RuntimeError(
                    "Groq returned no choices."
                )

            message = response.choices[0].message

            raw_content = ""

            if message is not None:
                raw_content = message.content or ""

            logger.info(
                "Groq response length: %s characters",
                len(raw_content),
            )

            # مهم جدًا:
            # نعرض بداية الرد فقط في الـ logs عند وجود مشكلة،
            # وليس API key أو أي سر.
            if not raw_content.strip():
                logger.error(
                    "Groq returned EMPTY content. "
                    "Full response object: %r",
                    response,
                )
                raise RuntimeError(
                    "Groq returned empty content."
                )

            try:
                data = extract_json_from_text(
                    raw_content
                )

                plan = validate_story_plan(data)

                logger.info(
                    "Groq story plan OK: %s scenes",
                    len(plan["scenes"]),
                )

                return plan

            except Exception as parse_error:

                logger.error(
                    "Invalid Groq JSON: %s",
                    parse_error,
                )

                logger.error(
                    "Groq raw response preview: %r",
                    raw_content[:4000],
                )

                last_error = parse_error

                # إعادة المحاولة بطلب أوضح
                user_prompt = f"""
أعد إخراج القصة التالية كـ JSON صحيح فقط.

لا تكتب أي شيء خارج JSON.
لا تستخدم ```.
يجب أن يحتوي JSON على:
title
scenes

وكل scene يجب أن يحتوي:
narration
visual_prompt

القصة:

{story}
"""

        except Exception as exc:

            last_error = exc

            logger.exception(
                "Groq request failed on attempt %s",
                attempt,
            )

            awaitable_sleep = 1.5 * attempt
            import time
            time.sleep(awaitable_sleep)

    raise RuntimeError(
        f"Groq failed after 3 attempts: {last_error}"
    )


# =========================================================
# POLLINATIONS IMAGE
# =========================================================

def generate_image(prompt: str, output_path: Path, seed: int):
    if not POLLINATIONS_API_KEY:
        raise RuntimeError(
            "POLLINATIONS_API_KEY is missing."
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

    logger.info(
        "Generating image with Pollinations..."
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

    output_path.write_bytes(response.content)

    logger.info(
        "Image saved: %s",
        output_path,
    )

    return output_path


# =========================================================
# CINEMATIC PROMPT
# =========================================================

def build_image_prompt(
    visual_prompt: str,
    scene_index: int,
):
    return f"""
Cinematic realistic storytelling scene.

{visual_prompt}

Scene number: {scene_index}

Visual style:
- realistic cinematic photography
- dramatic lighting
- emotional atmosphere
- detailed faces and environment
- natural human proportions
- realistic clothing
- realistic skin
- strong composition
- movie still
- vertical composition
- 9:16
- no text
- no subtitles
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
    logger.info("Generating Arabic male voice...")

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
# FFMPEG HELPERS
# =========================================================

def run_command(
    command,
    timeout=300,
):
    logger.info(
        "Running command: %s",
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
# IMAGE → VIDEO
# =========================================================

def create_scene_video(
    image_path: Path,
    output_path: Path,
    duration: float,
):
    logger.info(
        "Creating scene video: %.2f seconds",
        duration,
    )

    frames = max(
        int(duration * FPS),
        FPS,
    )

    zoom_expr = (
        "min(zoom+0.0005,1.12)"
    )

    vf = (
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
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
# CONCATENATE VIDEOS
# =========================================================

def concatenate_videos(
    video_paths,
    output_path: Path,
):
    concat_file = output_path.parent / (
        "concat_" + uuid.uuid4().hex + ".txt"
    )

    with concat_file.open(
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
# ADD AUDIO
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
# ASS SUBTITLES
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

    return text


def create_ass_file(
    scenes,
    output_path: Path,
):
    """
    نضع كل نص مشهد كـ subtitle.
    """

    total_duration = 0.0

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            "Style: Default,Arial,42,"
            "&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,3,1,2,40,40,120,1"
        ),
        "",
        "[Events]",
        (
            "Format: Layer, Start, End, Style, Name, "
            "MarginL, MarginR, MarginV, Effect, Text"
        ),
    ]

    def ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        cs = int(round((s - int(s)) * 100))
        s = int(s)

        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    for scene in scenes:

        narration = scene.get(
            "narration",
            "",
        ).strip()

        if not narration:
            continue

        # تقسيم تقريبي للمدة
        words = max(
            len(narration.split()),
            1,
        )

        duration = max(
            8.0,
            min(
                35.0,
                words / 2.1,
            ),
        )

        start = total_duration
        end = start + duration

        text = escape_ass_text(
            narration.replace(
                "\n",
                " ",
            )
        )

        lines.append(
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            f"Default,,0,0,0,,"
            f"{text}"
        )

        total_duration = end

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return output_path


# =========================================================
# ADD CAPTIONS
# =========================================================

def add_captions(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
):
    # تحويل المسار إلى صيغة مناسبة لـ FFmpeg
    subtitle_path = str(
        ass_path
    ).replace(
        "\\",
        "/",
    )

    subtitle_path = subtitle_path.replace(
        ":",
        "\\:",
    )

    vf = (
        f"subtitles='{subtitle_path}'"
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
    logger.info("Starting complete video pipeline.")

    # -----------------------------------------------------
    # 1. GROQ
    # -----------------------------------------------------

    plan = generate_story_plan(
        story
    )

    scenes = plan["scenes"]

    logger.info(
        "Story plan contains %s scenes.",
        len(scenes),
    )

    # -----------------------------------------------------
    # 2. IMAGES
    # -----------------------------------------------------

    image_paths = []

    for index, scene in enumerate(
        scenes,
        start=1,
    ):

        logger.info(
            "Generating image %s/%s...",
            index,
            len(scenes),
        )

        prompt = build_image_prompt(
            scene["visual_prompt"],
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

    # -----------------------------------------------------
    # 3. VOICE
    # -----------------------------------------------------

    all_narration = "\n\n".join(
        scene["narration"]
        for scene in scenes
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
        "Narration duration: %.2f seconds",
        audio_duration,
    )

    # نحصر الفيديو بين دقيقة ودقيقتين
    target_duration = max(
        MIN_VIDEO_SECONDS,
        min(
            MAX_VIDEO_SECONDS,
            audio_duration,
        ),
    )

    scene_duration = (
        target_duration
        / max(len(image_paths), 1)
    )

    # -----------------------------------------------------
    # 4. SCENE VIDEOS
    # -----------------------------------------------------

    scene_video_paths = []

    for index, image_path in enumerate(
        image_paths,
        start=1,
    ):

        video_path = (
            job_dir
            / f"scene_{index}.mp4"
        )

        create_scene_video(
            image_path,
            video_path,
            scene_duration,
        )

        scene_video_paths.append(
            video_path
        )

    # -----------------------------------------------------
    # 5. CONCAT
    # -----------------------------------------------------

    combined_video = (
        job_dir
        / "combined.mp4"
    )

    concatenate_videos(
        scene_video_paths,
        combined_video,
    )

    # -----------------------------------------------------
    # 6. AUDIO
    # -----------------------------------------------------

    voiced_video = (
        job_dir
        / "voiced.mp4"
    )

    add_audio(
        combined_video,
        audio_path,
        voiced_video,
    )

    # -----------------------------------------------------
    # 7. CAPTIONS
    # -----------------------------------------------------

    ass_path = (
        job_dir
        / "captions.ass"
    )

    create_ass_file(
        scenes,
        ass_path,
    )

    final_video = (
        job_dir
        / "final.mp4"
    )

    add_captions(
        voiced_video,
        ass_path,
        final_video,
    )

    logger.info(
        "FINAL VIDEO READY: %s",
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

        # -------------------------------------------------
        # Run heavy synchronous pipeline
        # outside Telegram event loop.
        # -------------------------------------------------

        loop = asyncio.get_running_loop()

        final_video = await loop.run_in_executor(
            None,
            build_video,
            story,
            job_dir,
        )

        if not final_video.exists():
            raise RuntimeError(
                "Final video was not created."
            )

        logger.info(
            "Sending final video to Telegram..."
        )

        await update.message.reply_text(
            "✅ الفيديو جاهز.\n"
            "📤 جاري إرساله..."
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

        error_message = str(exc)

        # لا نرسل stack trace للمستخدم
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

        # حذف الملفات بعد انتهاء المهمة
        try:
            import shutil

            shutil.rmtree(
                job_dir,
                ignore_errors=True,
            )

        except Exception:
            pass


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

    application.add_error_handler(
        error_handler
    )

    await application.initialize()

    await application.start()

    logger.info(
        "Starting Telegram polling..."
    )

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info(
        "BOT IS RUNNING."
    )

    # إبقاء التطبيق شغال
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
