import os
import re
import json
import time
import uuid
import shutil
import asyncio
import logging
import threading
import subprocess
import traceback
from pathlib import Path

import requests
import edge_tts

from flask import Flask, jsonify
from groq import Groq
from gradio_client import Client


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

HF_SPACE = os.getenv(
    "HF_SPACE",
    "numanajmal0/wan-video-api"
).strip()

# Leave empty = automatic endpoint discovery
HF_API_NAME = os.getenv("HF_API_NAME", "").strip()

# INITIAL TEST
# Keep this at 1 while we test Hugging Face.
SHOT_COUNT = int(os.getenv("SHOT_COUNT", "1"))

SHOT_DURATION = int(os.getenv("SHOT_DURATION", "5"))

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()

BASE_DIR = Path("/app")
WORK_DIR = BASE_DIR / "work"

WORK_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ABOSARAJ")


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Abosaraj",
        "bot": "running"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False
        ),
        daemon=True
    )

    thread.start()

    log.info("HEALTH_SERVER_STARTED port=%s", port)


# ============================================================
# SECRET SAFE LOGGING
# ============================================================

def safe_error_text(text):
    if text is None:
        return ""

    text = str(text)

    secrets = [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN
    ]

    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")

    return text


# ============================================================
# COMMAND RUNNER
# ============================================================

def run_command(command, timeout=None):
    safe_command = []

    for arg in command:
        arg = str(arg)

        if BOT_TOKEN and BOT_TOKEN in arg:
            arg = "[REDACTED]"

        if HF_TOKEN and HF_TOKEN in arg:
            arg = "[REDACTED]"

        if GROQ_API_KEY and GROQ_API_KEY in arg:
            arg = "[REDACTED]"

        safe_command.append(arg)

    log.info("RUN_COMMAND=%s", " ".join(safe_command))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            f"{safe_error_text(result.stdout[-5000:])}"
        )

    return result.stdout


# ============================================================
# TELEGRAM CONNECTION DIAGNOSTIC
# ============================================================

def check_telegram_connection():
    """
    Checks whether Telegram has a webhook configured.

    This does NOT start polling.
    It only asks Telegram for webhook status.
    """

    log.info("TELEGRAM_CONNECTION_CHECK_START")

    if not BOT_TOKEN:
        log.error("TELEGRAM_CONNECTION_CHECK_FAILED=BOT_TOKEN missing")
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"

        response = requests.get(
            url,
            timeout=15
        )

        log.info(
            "TELEGRAM_API_HTTP_STATUS=%s",
            response.status_code
        )

        if response.status_code != 200:
            log.error(
                "TELEGRAM_API_RESPONSE=%s",
                safe_error_text(response.text[:2000])
            )
            return

        data = response.json()

        if not data.get("ok"):
            log.error(
                "TELEGRAM_API_ERROR=%s",
                safe_error_text(json.dumps(data, ensure_ascii=False))
            )
            return

        info = data.get("result", {})

        webhook_url = info.get("url", "")
        pending = info.get("pending_update_count", 0)
        ip_address = info.get("ip_address", "")
        last_error_date = info.get("last_error_date")
        last_error_message = info.get("last_error_message")

        log.info(
            "TELEGRAM_WEBHOOK url=%s pending=%s ip=%s",
            webhook_url if webhook_url else "<EMPTY>",
            pending,
            ip_address if ip_address else "<NONE>"
        )

        if last_error_date:
            log.info(
                "TELEGRAM_WEBHOOK_LAST_ERROR_DATE=%s",
                last_error_date
            )

        if last_error_message:
            log.info(
                "TELEGRAM_WEBHOOK_LAST_ERROR=%s",
                safe_error_text(last_error_message)
            )

        if webhook_url:
            log.warning(
                "TELEGRAM_WEBHOOK_IS_SET"
            )
        else:
            log.info(
                "TELEGRAM_WEBHOOK_IS_EMPTY"
            )

    except Exception as e:
        log.error(
            "TELEGRAM_CONNECTION_CHECK_FAILED=%s",
            safe_error_text(str(e))
        )

        log.error(
            traceback.format_exc()
        )


# ============================================================
# TELEGRAM BOT IMPORT
# ============================================================

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)


# ============================================================
# GROQ
# ============================================================

def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing")

    return Groq(
        api_key=GROQ_API_KEY
    )


# ============================================================
# STORYBOARD
# ============================================================

def create_storyboard(story_text):
    """
    Converts Arabic story into cinematic scene prompts.
    """

    client = get_groq_client()

    prompt = f"""
You are a professional cinematic AI video director.

Convert the following Arabic story into exactly {SHOT_COUNT}
cinematic video scene(s).

The final video is for TikTok / Instagram Reels.

IMPORTANT:
- The story is fictional unless explicitly stated otherwise.
- Do not invent factual claims.
- Preserve the story meaning.
- Make each scene visually cinematic.
- Use realistic humans and realistic robotics.
- No text inside the generated video.
- No subtitles inside the generated video.
- No logos.
- No watermarks.
- Vertical-video composition.
- 9:16 framing.
- Strong visual storytelling.
- Camera movement should feel cinematic.
- Realistic lighting.
- Realistic physics.
- Characters should remain visually consistent.

Return ONLY valid JSON in this exact structure:

{{
  "scenes": [
    {{
      "scene": 1,
      "prompt": "detailed cinematic English video prompt"
    }}
  ]
}}

STORY:

{story_text}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a cinematic AI video director. "
                    "Return valid JSON only."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.8,
        max_tokens=3000
    )

    content = response.choices[0].message.content.strip()

    # Remove accidental markdown fences
    content = re.sub(
        r"^```(?:json)?\s*",
        "",
        content,
        flags=re.IGNORECASE
    )

    content = re.sub(
        r"\s*```$",
        "",
        content
    )

    data = json.loads(content)

    scenes = data.get("scenes")

    if not isinstance(scenes, list) or not scenes:
        raise RuntimeError(
            "Groq returned an empty storyboard"
        )

    return scenes[:SHOT_COUNT]


# ============================================================
# HUGGING FACE
# ============================================================

_hf_client = None
_hf_client_lock = threading.Lock()


def get_hf_client():
    global _hf_client

    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is missing")

    with _hf_client_lock:

        if _hf_client is None:

            log.info(
                "HF_CONNECTING space=%s",
                HF_SPACE
            )

            _hf_client = Client(
                HF_SPACE,
                token=HF_TOKEN
            )

            log.info(
                "HF_CONNECTED space=%s",
                HF_SPACE
            )

    return _hf_client


def get_api_schema(client):

    log.info("HF_VIEW_API_START")

    try:
        info = client.view_api(
            return_format="dict"
        )
    except TypeError:
        # Compatibility fallback
        info = client.view_api()

    log.info("HF_VIEW_API_SUCCESS")

    try:
        named = info.get("named_endpoints", {})

        if isinstance(named, dict):
            log.info(
                "HF_NAMED_ENDPOINTS=%s",
                list(named.keys())
            )
        else:
            log.info(
                "HF_NAMED_ENDPOINTS=%s",
                "<NOT_DICT>"
            )

    except Exception:
        pass

    return info


def resolve_endpoint(api_info):

    named = api_info.get(
        "named_endpoints",
        {}
    )

    if not isinstance(named, dict):
        named = {}

    # Explicit endpoint
    if HF_API_NAME:

        endpoint_name = HF_API_NAME

        if endpoint_name.startswith("/"):
            endpoint_name = endpoint_name[1:]

        if endpoint_name in named:
            return (
                "/" + endpoint_name,
                named[endpoint_name]
            )

        slash_name = "/" + endpoint_name

        if slash_name in named:
            return (
                slash_name,
                named[slash_name]
            )

        raise RuntimeError(
            "Configured HF_API_NAME was not found. "
            f"Available endpoints: {list(named.keys())}"
        )

    # Automatic discovery
    candidates = []

    for name, endpoint in named.items():

        lower = str(name).lower()

        score = 0

        if "generate" in lower:
            score += 100

        if "video" in lower:
            score += 80

        if "text_to_video" in lower:
            score += 120

        if "text-to-video" in lower:
            score += 120

        if "predict" in lower:
            score += 60

        candidates.append(
            (
                score,
                name,
                endpoint
            )
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    if candidates and candidates[0][0] > 0:
        _, name, endpoint = candidates[0]

        log.info(
            "HF_ENDPOINT_AUTO_SELECTED=%s",
            name
        )

        return (
            name if str(name).startswith("/")
            else "/" + str(name),
            endpoint
        )

    if len(named) == 1:

        name, endpoint = next(
            iter(named.items())
        )

        log.info(
            "HF_ENDPOINT_ONLY_AVAILABLE=%s",
            name
        )

        return (
            name if str(name).startswith("/")
            else "/" + str(name),
            endpoint
        )

    raise RuntimeError(
        "Could not automatically determine Hugging Face "
        f"video endpoint. Available endpoints: {list(named.keys())}"
    )


# ============================================================
# HF PARAMETER HELPERS
# ============================================================

def choose_value_from_parameter(
    parameter,
    prompt,
    negative_prompt=""
):

    name = str(
        parameter.get("parameter_name", "")
    ).lower()

    label = str(
        parameter.get("label", "")
    ).lower()

    combined = f"{name} {label}"

    component = parameter.get(
        "component",
        ""
    )

    # Prompt
    if (
        "negative" not in combined
        and (
            "prompt" in combined
            or component == "Textbox"
        )
    ):
        return prompt

    # Negative prompt
    if "negative" in combined:
        return negative_prompt

    # Model
    if "model" in combined:
        choices = parameter.get("choices")

        if choices:
            return choices[0]

        return parameter.get(
            "default",
            None
        )

    # Duration
    if (
        "duration" in combined
        or "seconds" in combined
    ):
        return SHOT_DURATION

    # FPS
    if (
        "fps" in combined
        or "frame rate" in combined
    ):
        return FPS

    # Width
    if "width" in combined:
        return FINAL_WIDTH

    # Height
    if "height" in combined:
        return FINAL_HEIGHT

    # Frames
    if "frames" in combined:
        return max(
            FPS * SHOT_DURATION,
            1
        )

    # Steps
    if "steps" in combined:
        return 20

    # Guidance
    if (
        "guidance" in combined
        or "cfg" in combined
    ):
        return 6.0

    # Seed
    if "seed" in combined:
        return -1

    # Checkbox
    if component == "Checkbox":
        return bool(
            parameter.get(
                "default",
                False
            )
        )

    # Dropdown choices
    choices = parameter.get("choices")

    if choices:
        return choices[0]

    # Default
    if "default" in parameter:
        return parameter["default"]

    # Unknown
    return None


def build_generate_arguments(
    endpoint_info,
    prompt
):

    parameters = endpoint_info.get(
        "parameters",
        []
    )

    if not isinstance(parameters, list):
        raise RuntimeError(
            "HF endpoint parameters are unavailable"
        )

    args = []

    for parameter in parameters:

        value = choose_value_from_parameter(
            parameter,
            prompt
        )

        name = parameter.get(
            "parameter_name",
            ""
        )

        log.info(
            "HF_PARAMETER name=%s value=%s",
            name,
            safe_error_text(str(value))[:300]
        )

        args.append(value)

    return args


# ============================================================
# FIND VIDEO RESULT
# ============================================================

def find_video_value(value):

    if value is None:
        return None

    # String
    if isinstance(value, str):

        lower = value.lower()

        if (
            lower.startswith("http://")
            or lower.startswith("https://")
            or lower.startswith("/")
            or ".mp4" in lower
            or ".webm" in lower
            or ".mov" in lower
        ):
            return value

        return None

    # List / tuple
    if isinstance(value, (list, tuple)):

        for item in value:

            found = find_video_value(item)

            if found:
                return found

        return None

    # Dict
    if isinstance(value, dict):

        preferred_keys = [
            "video",
            "video_url",
            "url",
            "path",
            "file",
            "name"
        ]

        for key in preferred_keys:

            if key in value:

                found = find_video_value(
                    value[key]
                )

                if found:
                    return found

        for item in value.values():

            found = find_video_value(item)

            if found:
                return found

        return None

    # Gradio FileData-like object
    for attr in [
        "path",
        "url",
        "name"
    ]:

        try:

            attr_value = getattr(
                value,
                attr,
                None
            )

            if attr_value:

                found = find_video_value(
                    attr_value
                )

                if found:
                    return found

        except Exception:
            pass

    return None


# ============================================================
# DOWNLOAD VIDEO
# ============================================================

def download_file(
    source,
    destination
):

    destination = Path(destination)

    # Local file
    if (
        isinstance(source, str)
        and os.path.exists(source)
    ):

        shutil.copy2(
            source,
            destination
        )

        return destination

    # URL
    if isinstance(source, str) and (
        source.startswith("http://")
        or source.startswith("https://")
    ):

        log.info(
            "DOWNLOADING_VIDEO=%s",
            source[:200]
        )

        response = requests.get(
            source,
            stream=True,
            timeout=180
        )

        response.raise_for_status()

        with open(
            destination,
            "wb"
        ) as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    f.write(chunk)

        return destination

    raise RuntimeError(
        "Hugging Face returned an unsupported video result"
    )


# ============================================================
# GENERATE AI VIDEO
# ============================================================

def generate_ai_video(
    prompt,
    output_path
):

    client = get_hf_client()

    api_info = get_api_schema(
        client
    )

    endpoint_name, endpoint_info = resolve_endpoint(
        api_info
    )

    log.info(
        "HF_GENERATION_ENDPOINT=%s",
        endpoint_name
    )

    args = build_generate_arguments(
        endpoint_info,
        prompt
    )

    log.info(
        "HF_GENERATION_START"
    )

    try:

        result = client.predict(
            *args,
            api_name=endpoint_name
        )

    except Exception as e:

        log.error(
            "HF_GENERATION_FIRST_ATTEMPT_FAILED=%s",
            safe_error_text(str(e))
        )

        log.error(
            traceback.format_exc()
        )

        log.info(
            "HF_GENERATION_RETRY"
        )

        time.sleep(2)

        result = client.predict(
            *args,
            api_name=endpoint_name
        )

    log.info(
        "HF_GENERATION_RESPONSE_RECEIVED"
    )

    video_value = find_video_value(
        result
    )

    if not video_value:

        raise RuntimeError(
            "Hugging Face generation completed but "
            "no video file/URL was found in the response. "
            f"Response type: {type(result).__name__}"
        )

    download_file(
        video_value,
        output_path
    )

    if not output_path.exists():
        raise RuntimeError(
            "Video output file was not created"
        )

    log.info(
        "HF_VIDEO_READY=%s size=%s",
        output_path,
        output_path.stat().st_size
    )

    return output_path


# ============================================================
# TTS
# ============================================================

async def create_tts_async(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text,
        TTS_VOICE
    )

    await communicate.save(
        str(output_path)
    )


def create_tts(
    text,
    output_path
):

    asyncio.run(
        create_tts_async(
            text,
            output_path
        )
    )

    if not output_path.exists():
        raise RuntimeError(
            "TTS file was not created"
        )

    return output_path


# ============================================================
# VIDEO NORMALIZATION
# ============================================================

def normalize_video(
    input_path,
    output_path
):

    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),

            "-vf",
            (
                "scale=720:1280:"
                "force_original_aspect_ratio=decrease,"
                "pad=720:1280:"
                "(ow-iw)/2:"
                "(oh-ih)/2"
            ),

            "-r",
            str(FPS),

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-pix_fmt",
            "yuv420p",

            "-an",

            str(output_path)
        ],
        timeout=300
    )

    return output_path


# ============================================================
# CONCAT VIDEOS
# ============================================================

def concat_videos(
    video_paths,
    output_path
):

    if not video_paths:
        raise RuntimeError(
            "No videos to concatenate"
        )

    list_file = output_path.parent / (
        f"concat_{uuid.uuid4().hex}.txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in video_paths:

            safe_path = str(
                Path(path).resolve()
            ).replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    try:

        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),

                "-c:v",
                "libx264",

                "-preset",
                "veryfast",

                "-pix_fmt",
                "yuv420p",

                "-an",

                str(output_path)
            ],
            timeout=300
        )

    finally:

        try:
            list_file.unlink()
        except Exception:
            pass

    return output_path


# ============================================================
# ADD AUDIO
# ============================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
):

    run_command(
        [
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

            str(output_path)
        ],
        timeout=300
    )

    return output_path


# ============================================================
# FULL VIDEO PIPELINE
# ============================================================

def create_video(
    story_text,
    job_dir
):

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    log.info(
        "STORYBOARD_START"
    )

    scenes = create_storyboard(
        story_text
    )

    log.info(
        "STORYBOARD_READY scenes=%s",
        len(scenes)
    )

    scene_videos = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        prompt = scene.get(
            "prompt",
            ""
        ).strip()

        if not prompt:
            raise RuntimeError(
                f"Scene {index} has no prompt"
            )

        log.info(
            "SCENE_START=%s",
            index
        )

        raw_video = job_dir / (
            f"scene_{index}_raw.mp4"
        )

        normalized_video = job_dir / (
            f"scene_{index}.mp4"
        )

        generate_ai_video(
            prompt,
            raw_video
        )

        normalize_video(
            raw_video,
            normalized_video
        )

        scene_videos.append(
            normalized_video
        )

        log.info(
            "SCENE_DONE=%s",
            index
        )

    combined_video = job_dir / (
        "combined.mp4"
    )

    concat_videos(
        scene_videos,
        combined_video
    )

    audio_file = job_dir / (
        "narration.mp3"
    )

    create_tts(
        story_text,
        audio_file
    )

    final_video = job_dir / (
        "final.mp4"
    )

    mux_audio(
        combined_video,
        audio_file,
        final_video
    )

    if not final_video.exists():
        raise RuntimeError(
            "Final video was not created"
        )

    return final_video


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🎬 أهلاً! ابعتلي القصة وأنا أحولها لفيديو."
    )


async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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
        else "unknown"
    )

    job_id = uuid.uuid4().hex[:8]

    job_dir = WORK_DIR / job_id

    log.info(
        "JOB_START=%s user=%s",
        job_id,
        user_id
    )

    try:

        await update.message.reply_text(
            "🎬 وصلت القصة.\n"
            "⏳ بدأت تجهيز الفيديو..."
        )

        # Run heavy synchronous work outside async handler
        final_video = await asyncio.to_thread(
            create_video,
            story,
            job_dir
        )

        log.info(
            "JOB_SUCCESS=%s",
            job_id
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🎬 الفيديو جاهز\n"
                    f"EP — {job_id}"
                ),
                supports_streaming=True
            )

    except Exception as e:

        error_message = safe_error_text(
            str(e)
        )

        log.error(
            "JOB_FAILED=%s",
            job_id
        )

        log.error(
            "ERROR_TYPE=%s",
            type(e).__name__
        )

        log.error(
            "ERROR_MESSAGE=%s",
            error_message
        )

        log.error(
            traceback.format_exc()
        )

        try:

            await update.message.reply_text(
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                f"{error_message[:1500]}"
            )

        except Exception:

            log.error(
                "FAILED_TO_SEND_ERROR_TO_USER"
            )

    finally:

        log.info(
            "JOB_CLEANUP=%s",
            job_id
        )

        try:

            if job_dir.exists():

                shutil.rmtree(
                    job_dir,
                    ignore_errors=True
                )

        except Exception:

            pass


async def telegram_error_handler(
    update,
    context
):

    error = context.error

    error_text = safe_error_text(
        str(error)
    )

    log.error(
        "TELEGRAM_HANDLER_ERROR=%s",
        error_text
    )

    log.error(
        traceback.format_exc()
    )


# ============================================================
# BUILD TELEGRAM APPLICATION
# ============================================================

def build_application():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing"
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
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story
        )
    )

    application.add_error_handler(
        telegram_error_handler
    )

    return application


# ============================================================
# MAIN
# ============================================================

def main():

    log.info(
        "ABOSARAJ STARTING"
    )

    start_health_server()

    # --------------------------------------------------------
    # TELEGRAM DIAGNOSTIC
    # --------------------------------------------------------

    check_telegram_connection()

    # --------------------------------------------------------
    # BUILD BOT
    # --------------------------------------------------------

    application = build_application()

    log.info(
        "TELEGRAM_HANDLERS_READY"
    )

    log.info(
        "BOT_START_POLLING"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log.error(
            "FATAL_ERROR=%s",
            safe_error_text(str(e))
        )

        log.error(
            traceback.format_exc()
        )

        raise
