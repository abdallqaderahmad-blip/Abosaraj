import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
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

FAL_MODEL = "alibaba/wan-3.0/text-to-video"

# =========================================================
# DAILY LIMIT
# =========================================================

MAX_DAILY_GENERATIONS = 5

# نستخدم 15 ثانية لكل توليد
AI_CLIP_SECONDS = 15

# 5 توليدات:
# فيديو 1 = 2 clips
# فيديو 2 = 2 clips
# فيديو 3 = 1 clip

# =========================================================
# FINAL VIDEO
# =========================================================

FINAL_SECONDS = 60

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

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
    return "Abosaraj Daily 5 is alive"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "version": "daily-5",
        "video_engine": "Wan 3",
        "daily_limit": MAX_DAILY_GENERATIONS
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


# =========================================================
# DAILY COUNTER
# =========================================================

counter_lock = threading.Lock()

daily_counter = {
    "date": None,
    "used": 0
}


def malaysia_date():

    tz = timezone(
        timedelta(hours=8)
    )

    return datetime.now(tz).date().isoformat()


def get_daily_usage():

    today = malaysia_date()

    with counter_lock:

        if daily_counter["date"] != today:

            daily_counter["date"] = today
            daily_counter["used"] = 0

        return daily_counter["used"]


def reserve_generation():

    today = malaysia_date()

    with counter_lock:

        if daily_counter["date"] != today:

            daily_counter["date"] = today
            daily_counter["used"] = 0

        if (
            daily_counter["used"]
            >= MAX_DAILY_GENERATIONS
        ):

            return False

        daily_counter["used"] += 1

        logger.info(
            "Daily Wan usage: %s/%s",
            daily_counter["used"],
            MAX_DAILY_GENERATIONS
        )

        return True


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
أنت مخرج محترف لفيديوهات Reels وShorts.

حوّل القصة إلى فيديو قصير جدًا لكنه قوي.

أريد 2 مشاهد رئيسية فقط.

كل مشهد يجب أن يكون مناسبًا لتوليد فيديو AI مدته 15 ثانية.

الفيديو النهائي سيتم بناؤه من هذه المشاهد مع المونتاج والصوت.

أرجع JSON فقط بهذا الشكل:

{{
  "title": "عنوان عربي قصير",
  "hook": "Hook عربي قوي",
  "narration": "نص الراوي العربي الكامل",
  "scenes": [
    {{
      "video_prompt": "English cinematic video prompt"
    }},
    {{
      "video_prompt": "English cinematic video prompt"
    }}
  ]
}}

القواعد:

- narration بين 110 و160 كلمة تقريبًا.
- القصة يجب أن تبدأ بـ Hook قوي.
- المشهد الأول يجب أن يجذب الانتباه فورًا.
- المشهد الثاني يجب أن يكون أقوى وأكثر غموضًا.
- الفيديو واقعي وسينمائي.
- الحركة حقيقية وليست صورة ثابتة.
- اذكر حركة الشخصيات.
- اذكر حركة البيئة.
- اذكر حركة الكاميرا.
- استخدم realistic cinematic lighting.
- استخدم vertical 9:16 composition.
- لا تضع أي كتابة داخل الفيديو.
- لا subtitles.
- لا logos.
- لا watermark.
- لا دماء أو gore.
- لا تستخدم كلمات عربية داخل video_prompt.
- video_prompt باللغة الإنجليزية فقط.
- حافظ على نفس الشخصية والمكان قدر الإمكان بين المشهدين.
- اجعل كل مشهد مختلفًا بصريًا.
- اجعل النهاية فيها مفاجأة أو سؤال أو twist عندما تناسب القصة.

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
                    "You are a professional "
                    "short-form video director. "
                    "Return JSON only."
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

    except Exception as e:

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

    if len(scenes) != 2:

        raise RuntimeError(
            "Groq did not create exactly 2 scenes."
        )

    narration = (
        data.get(
            "narration",
            ""
        )
        .strip()
    )

    if len(narration) < 50:

        raise RuntimeError(
            "Generated narration is too short."
        )

    return data


# =========================================================
# WAN 3
# =========================================================

def generate_wan_clip(
    prompt,
    output_path,
    seed
):

    if not reserve_generation():

        raise RuntimeError(
            "🛑 وصلنا إلى حد 5 توليدات Wan 3 "
            "المسموح بها لهذا اليوم."
        )

    logger.info(
        "Generating Wan 3 clip..."
    )

    try:

        result = fal_client.subscribe(
            FAL_MODEL,

            arguments={
                "prompt": prompt,

                "resolution": "720p",

                "aspect_ratio": "9:16",

                "duration": AI_CLIP_SECONDS,

                "audio": True,

                "enable_prompt_expansion": True,

                "seed": int(seed),

                "enable_safety_checker": True
            }
        )

    except Exception as e:

        error = str(e)

        logger.exception(
            "Wan 3 generation failed"
        )

        # نرجع العداد لأن التوليد لم يكتمل
        with counter_lock:

            if daily_counter["used"] > 0:

                daily_counter["used"] -= 1

        if (
            "402" in error
            or "credit" in error.lower()
            or "insufficient" in error.lower()
            or "balance" in error.lower()
        ):

            raise RuntimeError(
                "❌ fal.ai لا يملك رصيد API كافيًا "
                "لهذا التوليد."
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
            "❌ Wan 3 error: "
            + error
        )

    video_url = None

    if isinstance(
        result,
        dict
    ):

        video = result.get(
            "video"
        )

        if isinstance(
            video,
            dict
        ):

            video_url = (
                video.get("url")
                or video.get("video_url")
            )

        elif isinstance(
            video,
            str
        ):

            video_url = video

        if not video_url:

            video_url = (
                result.get("video_url")
                or result.get("url")
            )

    if not video_url:

        raise RuntimeError(
            "Wan 3 returned no video URL."
        )

    logger.info(
        "Downloading Wan 3 clip..."
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
            "Video file was not created."
        )

    if output_path.stat().st_size < 1000:

        raise RuntimeError(
            "Downloaded video is invalid."
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
# PREPARE VIDEO
# =========================================================

def prepare_clip(
    input_video,
    output_video
):

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

            "-i",
            str(input_video),

            "-vf",
            filter_video,

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

            start = (
                i * part
            )

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
    narration,
    title,
    output_path
):

    combined = (
        output_path.parent /
        "combined.mp4"
    )

    concat_clips(
        clips,
        combined
    )

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

    target_duration = max(
        FINAL_SECONDS,
        voice_duration + 1
    )

    captions = (
        output_path.parent /
        "captions.ass"
    )

    create_captions(
        narration,
        voice_duration,
        captions
    )

    # -----------------------------------------------------
    # نكرر المشاهد حتى نهاية الصوت
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
    # العنوان
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
        "enable='between(t,0,3)'"
    )

    subtitle_filter = (
        f"ass={captions}"
    )

    final_filter = (
        f"{subtitle_filter},"
        f"{title_filter}"
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

    return str(output_path)


# =========================================================
# CREATE ONE VIDEO
# =========================================================

def create_one_video(
    story,
    generation_number,
    clips_allowed
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
        "Starting video job %s",
        job_id
    )

    # -----------------------------------------------------
    # Groq
    # -----------------------------------------------------

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

    clips = []

    # -----------------------------------------------------
    # توليد اللقطات المطلوبة
    # -----------------------------------------------------

    for index in range(
        clips_allowed
    ):

        if index >= len(scenes):

            scene_prompt = (
                "A cinematic mysterious "
                "night scene with realistic "
                "human movement, wind, "
                "moving environment, "
                "slow camera push-in, "
                "dramatic realistic lighting, "
                "photorealistic, vertical 9:16, "
                "no text, no subtitles."
            )

        else:

            scene_prompt = (
                scenes[index]
                .get(
                    "video_prompt",
                    ""
                )
                .strip()
            )

        if not scene_prompt:

            scene_prompt = (
                "A realistic cinematic "
                "mystery scene, "
                "natural human movement, "
                "moving environment, "
                "slow tracking camera, "
                "dramatic lighting, "
                "vertical 9:16, "
                "no text."
            )

        raw_clip = (
            job_dir /
            f"clip_{index + 1}_raw.mp4"
        )

        final_clip = (
            job_dir /
            f"clip_{index + 1}.mp4"
        )

        logger.info(
            "Video %s: generating clip %s/%s",
            generation_number,
            index + 1,
            clips_allowed
        )

        generate_wan_clip(
            scene_prompt,
            raw_clip,
            seed=(
                5000
                + generation_number * 100
                + index
            )
        )

        prepare_clip(
            raw_clip,
            final_clip
        )

        clips.append(
            final_clip
        )

    # -----------------------------------------------------
    # Final montage
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

    return {
        "video": str(final_video),
        "title": title,
        "job_id": job_id
    }


# =========================================================
# TELEGRAM START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    used = get_daily_usage()

    remaining = (
        MAX_DAILY_GENERATIONS
        - used
    )

    await update.message.reply_text(
        "🎬 أهلاً في Abosaraj\n\n"
        "ابعتلي القصة وأنا أحولها إلى "
        "فيديو Reels/Shorts جاهز.\n\n"
        "🎥 AI Video\n"
        "🎙️ صوت عربي\n"
        "📝 Captions\n"
        "📱 9:16\n"
        "🎞️ مونتاج تلقائي\n\n"
        f"🔥 التوليدات المستخدمة اليوم: "
        f"{used}/{MAX_DAILY_GENERATIONS}\n"
        f"🟢 المتبقي: {remaining}"
    )


# =========================================================
# TELEGRAM STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    used = get_daily_usage()

    remaining = (
        MAX_DAILY_GENERATIONS
        - used
    )

    await update.message.reply_text(
        "📊 حالة Abosaraj\n\n"
        f"🎥 Wan generations: "
        f"{used}/{MAX_DAILY_GENERATIONS}\n"
        f"🟢 المتبقي: {remaining}\n\n"
        "النظام يعمل بنظام حد يومي."
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

    used = get_daily_usage()

    remaining = (
        MAX_DAILY_GENERATIONS
        - used
    )

    if remaining <= 0:

        await update.message.reply_text(
            "🛑 خلصت توليدات Wan لهذا اليوم.\n\n"
            "استخدم /status لمعرفة الحالة."
        )

        return

    # -----------------------------------------------------
    # توزيع التوليدات
    #
    # إذا المتبقي 5:
    # أول فيديو = 2
    #
    # إذا المتبقي 3:
    # ثاني فيديو = 2
    #
    # إذا المتبقي 1:
    # ثالث فيديو = 1
    # -----------------------------------------------------

    if remaining >= 5:

        clips_for_video = 2

    elif remaining >= 3:

        clips_for_video = 2

    else:

        clips_for_video = 1

    await update.message.reply_text(
        "🎬 وصلت القصة.\n\n"
        "🧠 عم أبني السيناريو...\n"
        f"🎥 عم أستخدم {clips_for_video} "
        "توليد AI لهذا الفيديو...\n"
        "🎙️ بعدها الصوت العربي...\n"
        "📝 بعدها الـCaptions...\n"
        "🎞️ وبالأخير المونتاج.\n\n"
        "⏳ استنى شوي..."
    )

    loop = asyncio.get_running_loop()

    try:

        result = await loop.run_in_executor(
            executor,
            create_one_video,
            story,
            used + 1,
            clips_for_video
        )

        video_path = result["video"]

        title = result["title"]

        new_used = get_daily_usage()

        await update.message.reply_text(
            "✅ خلص الفيديو!\n\n"
            f"🎬 {title}\n"
            "🎥 AI Motion\n"
            "🎙️ صوت عربي\n"
            "📝 Captions\n"
            "📱 9:16\n\n"
            f"📊 الاستخدام اليوم: "
            f"{new_used}/{MAX_DAILY_GENERATIONS}"
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
        "Starting Abosaraj Daily 5..."
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
