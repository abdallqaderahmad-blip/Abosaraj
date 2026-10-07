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

SHOT_COUNT = 10

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

IMAGE_WIDTH = 720
IMAGE_HEIGHT = 1280

MIN_WORDS = 145
MAX_WORDS = 185


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path("/tmp/abosaraj")

BASE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# GLOBAL LOCK
# ============================================================

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

logger = logging.getLogger("abosaraj")


# ============================================================
# BASIC HELPERS
# ============================================================

def count_words(text):
    return len(
        re.findall(
            r"\S+",
            text or "",
        )
    )


def cleanup_memory():
    gc.collect()


def safe_delete(path):
    try:
        path = Path(path)

        if path.exists():
            path.unlink()

    except Exception as e:
        logger.warning(
            "DELETE_FAILED=%s",
            str(e),
        )


def safe_rmtree(path):
    try:
        shutil.rmtree(
            path,
            ignore_errors=True,
        )
    except Exception:
        pass


def run_cmd(command, timeout=900):

    logger.info(
        "RUN_CMD=%s",
        " ".join(
            str(x)
            for x in command
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
            "FFMPEG_STDOUT=%s",
            result.stdout[-3000:],
        )

        logger.error(
            "FFMPEG_STDERR=%s",
            result.stderr[-5000:],
        )

        raise RuntimeError(
            f"Command failed: {result.returncode}"
        )

    return result


def get_duration(path):

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
# GROQ DIRECTOR
# ============================================================

def generate_story_plan(story):

    logger.info(
        "========== GROQ =========="
    )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    system_prompt = f"""
أنت مخرج أفلام قصيرة محترف متخصص في Reels الرعب والغموض.

حوّل القصة إلى فيديو سينمائي عمودي.

يجب إنتاج بالضبط {SHOT_COUNT} مشاهد.

كل مشهد يجب أن يمثل حدثاً جديداً.

الشخصية الرئيسية:
رجل عربي في أوائل الثلاثينات،
شعر أسود قصير،
لحية سوداء خفيفة،
ملابس منزلية داكنة،
وجه واقعي.

الزوجة:
امرأة عربية في الثلاثينات،
شعر أسود طويل،
ملامح واقعية.

المكان:
شقة عربية قديمة في الليل،
إضاءة منخفضة،
ظلال قوية،
جو رعب واقعي.

STYLE:
photorealistic live action,
cinematic horror thriller,
realistic human anatomy,
realistic skin,
professional cinematography,
35mm lens,
shallow depth of field,
dramatic lighting,
vertical 9:16,
high detail.

ممنوع:
text,
subtitles,
logo,
watermark,
cartoon,
anime,
painting,
illustration.

يجب أن يبدأ أول مشهد بـ HOOK قوي.

يجب أن يحتوي كل مشهد على حركة أو حدث واضح.

آخر مشهد يجب أن يحتوي على Twist.

الناتج JSON فقط.

الصيغة:

{{
  "title": "...",
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
- {SHOT_COUNT} مشاهد بالضبط.
- narration عربي.
- prompt إنجليزي.
- camera إنجليزي.
- motion إنجليزي.
- mood إنجليزي.
- مجموع narration بين {MIN_WORDS} و {MAX_WORDS} كلمة.
- لا تجعل مشهدين متتاليين بنفس زاوية الكاميرا.
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
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
                "content": story,
            },
        ],
    )

    raw = (
        response.choices[0]
        .message
        .content
    )

    data = json.loads(
        raw
    )

    shots = data.get(
        "shots",
        [],
    )

    if len(shots) != SHOT_COUNT:

        raise RuntimeError(
            f"Groq returned {len(shots)} shots "
            f"instead of {SHOT_COUNT}."
        )

    narration = " ".join(
        str(
            x.get(
                "narration",
                "",
            )
        )
        for x in shots
    )

    logger.info(
        "GROQ_SHOTS=%s",
        len(shots),
    )

    logger.info(
        "GROQ_WORDS=%s",
        count_words(narration),
    )

    return data


# ============================================================
# TTS
# ============================================================

async def tts_async(text, output):

    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
    )

    await communicate.save(
        str(output)
    )


def generate_tts(text, output):

    error = []

    def worker():

        try:
            asyncio.run(
                tts_async(
                    text,
                    output,
                )
            )

        except Exception as e:
            error.append(e)

    thread = threading.Thread(
        target=worker,
        daemon=True,
    )

    thread.start()
    thread.join()

    if error:
        raise error[0]

    if not output.exists():
        raise RuntimeError(
            "TTS file was not created."
        )

    duration = get_duration(
        output
    )

    logger.info(
        "TTS_SUCCESS duration=%.2f",
        duration,
    )

    return duration


# ============================================================
# POLLINATIONS PROMPT
# ============================================================

def build_image_prompt(shot):

    return f"""
Photorealistic live-action cinematic horror movie frame.

Vertical 9:16 composition.

MAIN CHARACTER:
realistic Arab man, early 30s,
short black hair,
short dark beard,
dark home clothes,
realistic face,
realistic skin.

ENVIRONMENT:
old Arabic apartment at night,
realistic interior,
low light,
deep shadows,
cinematic atmosphere.

EVENT:
{shot["prompt"]}

ACTION:
{shot["motion"]}

CAMERA:
{shot["camera"]}

MOOD:
{shot["mood"]}

Professional cinema photography,
35mm lens,
shallow depth of field,
dramatic realistic lighting,
natural anatomy,
realistic hands,
realistic face,
high detail.

NO TEXT.
NO SUBTITLES.
NO LOGO.
NO WATERMARK.
NO CARTOON.
NO ANIME.
NO PAINTING.
NO ILLUSTRATION.
NO DISTORTED FACE.
NO EXTRA LIMBS.
NO EXTRA FINGERS.
"""


# ============================================================
# POLLINATIONS IMAGE GENERATOR
# ============================================================

def generate_pollinations_image(
    shot,
    output_path,
    shot_number,
):

    logger.info(
        "========== IMAGE %s/%s ==========",
        shot_number,
        SHOT_COUNT,
    )

    if not POLLINATIONS_API_KEY:

        raise RuntimeError(
            "POLLINATIONS_API_KEY is missing. "
            "Add it to Render Environment Variables."
        )

    prompt = build_image_prompt(
        shot
    )

    seed = (
        int(time.time() * 1000)
        + shot_number * 7919
    ) % 2147483647

    encoded = quote(
        prompt,
        safe="",
    )

    url = (
        "https://gen.pollinations.ai/image/"
        + encoded
    )

    params = {
        "model": POLLINATIONS_MODEL,
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "seed": seed,
        "nologo": "true",
    }

    headers = {
        "Authorization":
            f"Bearer {POLLINATIONS_API_KEY}",
        "Accept": "image/*",
        "User-Agent":
            "Abosaraj/1.0",
    }

    logger.info(
        "IMAGE_SEED=%s",
        seed,
    )

    for attempt in range(1, 4):

        logger.info(
            "POLLINATIONS_ATTEMPT=%s/3",
            attempt,
        )

        try:

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=240,
            )

            status = response.status_code

            logger.info(
                "IMAGE_HTTP_STATUS=%s",
                status,
            )

            content_type = (
                response.headers
                .get(
                    "content-type",
                    "",
                )
                .lower()
            )

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            if status == 200:

                content = (
                    response.content
                )

                logger.info(
                    "IMAGE_BYTES=%s",
                    len(content),
                )

                is_image = (
                    "image/" in content_type
                    or content[:2] == b"\xff\xd8"
                    or content[:8]
                    == b"\x89PNG\r\n\x1a\n"
                    or content[:4]
                    == b"RIFF"
                )

                if (
                    len(content) > 5000
                    and is_image
                ):

                    output_path.write_bytes(
                        content
                    )

                    logger.info(
                        "IMAGE_SUCCESS=%s",
                        output_path,
                    )

                    cleanup_memory()

                    return output_path

                raise RuntimeError(
                    "Pollinations returned HTTP 200 "
                    "but the response was not a valid image."
                )

            # ------------------------------------------------
            # AUTH
            # ------------------------------------------------

            if status == 401:

                raise RuntimeError(
                    "Pollinations rejected the API key "
                    "(HTTP 401). Check POLLINATIONS_API_KEY."
                )

            # ------------------------------------------------
            # BALANCE / BUDGET
            # ------------------------------------------------

            if status == 402:

                raise RuntimeError(
                    "Pollinations accepted the API key "
                    "but the account/key has insufficient "
                    "Pollen or budget (HTTP 402)."
                )

            # ------------------------------------------------
            # FORBIDDEN
            # ------------------------------------------------

            if status == 403:

                raise RuntimeError(
                    "Pollinations denied this request "
                    "(HTTP 403)."
                )

            # ------------------------------------------------
            # BAD REQUEST
            # ------------------------------------------------

            if status == 400:

                body = (
                    response.text[:1000]
                )

                raise RuntimeError(
                    "Pollinations rejected the request "
                    f"(HTTP 400): {body}"
                )

            # ------------------------------------------------
            # RATE LIMIT / SERVER
            # ------------------------------------------------

            if status in (
                408,
                429,
                500,
                502,
                503,
                504,
            ):

                logger.warning(
                    "TEMPORARY_POLLINATIONS_ERROR "
                    "status=%s",
                    status,
                )

                if attempt < 3:

                    wait = (
                        attempt * 7
                    )

                    logger.info(
                        "RETRY_IN=%s",
                        wait,
                    )

                    time.sleep(
                        wait
                    )

                    continue

            body = (
                response.text[:1000]
            )

            raise RuntimeError(
                "Pollinations HTTP "
                f"{status}: {body}"
            )

        except requests.exceptions.Timeout:

            logger.warning(
                "POLLINATIONS_TIMEOUT attempt=%s",
                attempt,
            )

            if attempt < 3:

                time.sleep(
                    attempt * 7
                )

        except requests.exceptions.RequestException as e:

            logger.warning(
                "POLLINATIONS_NETWORK_ERROR=%s",
                str(e),
            )

            if attempt < 3:

                time.sleep(
                    attempt * 7
                )

    raise RuntimeError(
        "Pollinations image generation failed "
        "after 3 attempts."
    )


# ============================================================
# IMAGE -> VIDEO
# ============================================================

def create_scene_video(
    image_path,
    output_path,
    duration,
    scene_number,
):

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
        "push_right",
    ]

    movement = movements[
        (scene_number - 1)
        % len(movements)
    ]

    frames = max(
        1,
        int(duration * FPS),
    )

    if movement == "zoom_in":

        z = "min(zoom+0.0015,1.15)"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"

    elif movement == "zoom_out":

        z = (
            "if(eq(on,1),1.15,"
            "max(zoom-0.0015,1.0))"
        )
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"

    elif movement == "pan_left":

        z = "1.10"
        x = (
            "(iw-iw/zoom)"
            "*(1-on/total)"
        )
        y = "(ih-ih/zoom)/2"

    elif movement == "pan_right":

        z = "1.10"
        x = (
            "(iw-iw/zoom)"
            "*(on/total)"
        )
        y = "(ih-ih/zoom)/2"

    elif movement == "push_left":

        z = "min(zoom+0.001,1.10)"
        x = (
            "(iw-iw/zoom)"
            "*(1-on/total)"
        )
        y = "(ih-ih/zoom)/2"

    else:

        z = "min(zoom+0.001,1.10)"
        x = (
            "(iw-iw/zoom)"
            "*(on/total)"
        )
        y = "(ih-ih/zoom)/2"

    vf = (
        "scale=800:1422:"
        "force_original_aspect_ratio=increase,"
        "crop=800:1422,"
        f"zoompan=z='{z}':"
        f"x='{x}':"
        f"y='{y}':"
        f"d={frames}:"
        f"s={FINAL_WIDTH}x{FINAL_HEIGHT}:"
        f"fps={FPS},"
        "setsar=1"
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
            vf,
            "-t",
            str(duration),
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
# CONCAT
# ============================================================

def concat_videos(
    paths,
    output,
):

    txt = (
        output.parent /
        "videos.txt"
    )

    with open(
        txt,
        "w",
        encoding="utf-8",
    ) as f:

        for path in paths:

            p = (
                str(path)
                .replace("\\", "/")
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{p}'\n"
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
            str(txt),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        timeout=600,
    )

    return output


def concat_audio(
    paths,
    output,
):

    txt = (
        output.parent /
        "audio.txt"
    )

    with open(
        txt,
        "w",
        encoding="utf-8",
    ) as f:

        for path in paths:

            p = (
                str(path)
                .replace("\\", "/")
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{p}'\n"
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
            str(txt),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ],
        timeout=600,
    )

    return output


# ============================================================
# CAPTIONS
# ============================================================

def find_font():

    fonts = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for font in fonts:

        if Path(font).exists():
            return font

    return None


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
        round(
            (seconds - int(seconds))
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


def create_ass(
    shots,
    durations,
    output,
):

    font = find_font()

    font_name = (
        Path(font).stem
        if font
        else "DejaVu Sans"
    )

    content = f"""
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

    current = 0.0

    for shot, duration in zip(
        shots,
        durations,
    ):

        start = current
        end = (
            current +
            duration
        )

        text = re.sub(
            r"\s+",
            " ",
            str(
                shot["narration"]
            ).strip(),
        )

        words = text.split()

        if len(words) > 10:

            middle = (
                len(words) // 2
            )

            text = (
                " ".join(
                    words[:middle]
                )
                + r"\N"
                + " ".join(
                    words[middle:]
                )
            )

        content += (
            "Dialogue: 0,"
            f"{ass_time(start)},"
            f"{ass_time(end)},"
            "Default,,0,0,0,,"
            f"{text}\n"
        )

        current = end

    output.write_text(
        content,
        encoding="utf-8",
    )

    return output


# ============================================================
# FINAL VIDEO
# ============================================================

def create_final_video(
    video,
    audio,
    ass,
    output,
):

    ass_path = (
        str(ass)
        .replace("\\", "/")
        .replace(":", "\\:")
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-vf",
            f"ass={ass_path}",
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
            str(output),
        ],
        timeout=900,
    )

    return output


# ============================================================
# STORY PROCESSOR
# ============================================================

def process_story(
    story,
    workdir,
):

    # -------------------------------
    # PLAN
    # -------------------------------

    plan = generate_story_plan(
        story
    )

    shots = plan["shots"]

    # -------------------------------
    # AUDIO
    # -------------------------------

    audio_paths = []
    durations = []

    for i, shot in enumerate(
        shots,
        start=1,
    ):

        audio = (
            workdir /
            f"audio_{i:02d}.mp3"
        )

        duration = generate_tts(
            shot["narration"],
            audio,
        )

        audio_paths.append(
            audio
        )

        durations.append(
            duration
        )

        cleanup_memory()

    # -------------------------------
    # SCENES
    # -------------------------------

    scene_paths = []

    for i, (
        shot,
        duration,
    ) in enumerate(
        zip(
            shots,
            durations,
        ),
        start=1,
    ):

        image = (
            workdir /
            f"image_{i:02d}.jpg"
        )

        scene = (
            workdir /
            f"scene_{i:02d}.mp4"
        )

        logger.info(
            "========== SCENE %s/%s ==========",
            i,
            SHOT_COUNT,
        )

        # Image
        generate_pollinations_image(
            shot,
            image,
            i,
        )

        cleanup_memory()

        # Video
        create_scene_video(
            image,
            scene,
            duration,
            i,
        )

        scene_paths.append(
            scene
        )

        # Delete image immediately
        safe_delete(
            image
        )

        cleanup_memory()

    # -------------------------------
    # COMBINE VIDEO
    # -------------------------------

    combined_video = (
        workdir /
        "combined.mp4"
    )

    concat_videos(
        scene_paths,
        combined_video,
    )

    for path in scene_paths:
        safe_delete(path)

    scene_paths.clear()

    cleanup_memory()

    # -------------------------------
    # COMBINE AUDIO
    # -------------------------------

    combined_audio = (
        workdir /
        "audio.m4a"
    )

    concat_audio(
        audio_paths,
        combined_audio,
    )

    for path in audio_paths:
        safe_delete(path)

    audio_paths.clear()

    cleanup_memory()

    # -------------------------------
    # CAPTIONS
    # -------------------------------

    ass = (
        workdir /
        "captions.ass"
    )

    create_ass(
        shots,
        durations,
        ass,
    )

    # -------------------------------
    # FINAL
    # -------------------------------

    final = (
        workdir /
        "FINAL_REEL.mp4"
    )

    create_final_video(
        combined_video,
        combined_audio,
        ass,
        final,
    )

    logger.info(
        "FINAL_VIDEO=%s",
        final,
    )

    logger.info(
        "FINAL_SIZE=%s",
        final.stat().st_size,
    )

    return final, plan


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update,
    context,
):

    await update.message.reply_text(
        "🎬 أهلاً!\n\n"
        "ابعتلي قصة وأنا أحولها إلى Reel."
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
            "📝 ابعت قصة أطول شوي."
        )

        return

    if not JOB_LOCK.acquire(
        blocking=False
    ):

        await update.message.reply_text(
            "⏳ في فيديو ثاني قيد المعالجة."
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

    status = None

    try:

        status = await update.message.reply_text(
            "📝 استلمت القصة.\n"
            "🧠 جاري بناء السيناريو..."
        )

        loop = (
            asyncio.get_running_loop()
        )

        final, plan = await loop.run_in_executor(
            None,
            process_story,
            story,
            workdir,
        )

        await status.edit_text(
            "✅ خلص الفيديو.\n"
            "📤 جاري الإرسال..."
        )

        with open(
            final,
            "rb",
        ) as video:

            await update.message.reply_video(
                video=video,
                caption=(
                    f"🎬 {plan.get('title', 'AI Reel')}"
                ),
                supports_streaming=True,
                width=FINAL_WIDTH,
                height=FINAL_HEIGHT,
            )

        try:
            await status.delete()
        except Exception:
            pass

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

        message = str(e)

        # Do not expose secrets.
        message = re.sub(
            r"sk_[A-Za-z0-9_-]+",
            "[HIDDEN_KEY]",
            message,
        )

        message = re.sub(
            r"pk_[A-Za-z0-9_-]+",
            "[HIDDEN_KEY]",
            message,
        )

        try:

            await status.edit_text(
                "❌ صار خطأ.\n\n"
                f"{message[:1200]}"
            )

        except Exception:
            pass

    finally:

        safe_rmtree(
            workdir
        )

        cleanup_memory()

        JOB_LOCK.release()


# ============================================================
# HEALTH
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
        "engine":
            "Pollinations Image + FFmpeg",
        "shots":
            SHOT_COUNT,
        "resolution":
            f"{FINAL_WIDTH}x{FINAL_HEIGHT}",
        "fps":
            FPS,
        "pollinations":
            bool(POLLINATIONS_API_KEY),
    }


# ============================================================
# FLASK
# ============================================================

def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
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
        "================================"
    )

    logger.info(
        "ABOSARAJ BOT STARTING"
    )

    logger.info(
        "================================"
    )

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing."
        )

    if not POLLINATIONS_API_KEY:
        logger.warning(
            "POLLINATIONS_API_KEY IS MISSING"
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
        "SHOTS=%s",
        SHOT_COUNT,
    )

    logger.info(
        "POLLINATIONS_KEY_PRESENT=%s",
        bool(POLLINATIONS_API_KEY),
    )

    # Flask
    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    # Telegram
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story,
        )
    )

    app.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
