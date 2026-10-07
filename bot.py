import os
import re
import json
import time
import uuid
import asyncio
import logging
import subprocess
from pathlib import Path

import requests
import edge_tts

from flask import Flask
from groq import Groq
from gradio_client import Client, handle_file

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

HF_SPACE = os.getenv(
    "HF_SPACE",
    "numanajmal0/wan-video-api"
).strip()

HF_API_NAME = os.getenv("HF_API_NAME", "").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

SHOT_COUNT = int(os.getenv("SHOT_COUNT", "1"))
SHOT_DURATION = int(os.getenv("SHOT_DURATION", "5"))

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 16

TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()

BASE_DIR = Path("/tmp/abosaraj")
BASE_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")


# =========================================================
# FLASK HEALTH SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "ABOSARAJ OK", 200


@app.route("/health")
def health():
    return "OK", 200


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    import threading

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

    log.info(
        "HEALTH_SERVER_STARTED port=%s",
        port
    )


# =========================================================
# SECURITY / ERROR CLEANING
# =========================================================

def safe_error_text(error):
    text = str(error)

    secrets = [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
    ]

    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")

    # Hide Telegram bot token if it appears inside URLs
    text = re.sub(
        r"/bot\d+:[A-Za-z0-9_-]+/",
        "/bot[REDACTED]/",
        text
    )

    return text


def run_command(command):
    safe_command = " ".join(command)

    for secret in [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
    ]:
        if secret:
            safe_command = safe_command.replace(
                secret,
                "[REDACTED]"
            )

    log.info(
        "RUN_COMMAND=%s",
        safe_command
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {safe_error_text(result.stderr)}"
        )

    return result


# =========================================================
# TELEGRAM CONNECTION CHECK
# =========================================================

def check_telegram_connection():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN_MISSING")
        return

    try:
        log.info("TELEGRAM_CONNECTION_CHECK_START")

        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo",
            timeout=20
        )

        log.info(
            "TELEGRAM_API_HTTP_STATUS=%s",
            response.status_code
        )

        if response.status_code != 200:
            log.warning(
                "TELEGRAM_API_RESPONSE=%s",
                safe_error_text(response.text)
            )
            return

        data = response.json()

        result = data.get("result", {})

        webhook_url = result.get("url") or "<EMPTY>"
        pending = result.get("pending_update_count", 0)
        ip = result.get("ip_address") or "<NONE>"

        log.info(
            "TELEGRAM_WEBHOOK url=%s pending=%s ip=%s",
            webhook_url,
            pending,
            ip
        )

        if not webhook_url or webhook_url == "<EMPTY>":
            log.info("TELEGRAM_WEBHOOK_IS_EMPTY")

    except Exception as e:
        log.error(
            "TELEGRAM_CONNECTION_CHECK_ERROR=%s",
            safe_error_text(e)
        )


# =========================================================
# GROQ
# =========================================================

def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing"
        )

    return Groq(
        api_key=GROQ_API_KEY
    )


def generate_storyboard(story):
    client = get_groq_client()

    prompt = f"""
You are the cinematic director of a professional short-form video factory.

Convert the following Arabic story into exactly {SHOT_COUNT} cinematic video scene(s).

The video is for TikTok / Instagram Reels.

IMPORTANT:
- This is fictional unless the story explicitly states otherwise.
- Do not invent claims that make fiction look like a real news event.
- Focus on visual storytelling.
- Every scene must be visually understandable.
- Use realistic human behavior.
- Use realistic robotics and technology.
- Avoid text appearing inside the generated video.
- Avoid logos.
- Avoid subtitles.
- Avoid UI screenshots.
- Avoid static slideshow composition.
- Camera movement should feel cinematic.
- Characters should look realistic.
- Lighting should be cinematic.
- The scene should feel like live-action science fiction.
- Maintain character and environment continuity.
- Build suspense.
- Make the final scene visually strong.

Return ONLY valid JSON.

Required format:

[
  {{
    "scene": 1,
    "duration": {SHOT_DURATION},
    "prompt": "English cinematic video prompt"
  }}
]

STORY:

{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.7,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert cinematic AI video director. "
                    "Return valid JSON only."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()

    # Remove markdown fences if model adds them
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

    scenes = json.loads(content)

    if not isinstance(scenes, list):
        raise RuntimeError(
            "Groq storyboard is not a list"
        )

    if not scenes:
        raise RuntimeError(
            "Groq returned no scenes"
        )

    return scenes


# =========================================================
# HUGGING FACE
# =========================================================

def get_hf_client():
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN is missing"
        )

    log.info(
        "HF_CLIENT_CONNECTING space=%s",
        HF_SPACE
    )

    client = Client(
        HF_SPACE,
        token=HF_TOKEN
    )

    log.info(
        "HF_CLIENT_CONNECTED"
    )

    return client


def get_api_schema(client):
    log.info(
        "HF_VIEW_API_START"
    )

    info = client.view_api(
        return_format="dict"
    )

    log.info(
        "HF_VIEW_API_DONE"
    )

    return info


# =========================================================
# HF ENDPOINT DISCOVERY
# =========================================================

def resolve_endpoint(client, info):
    """
    Automatically find a usable Gradio named endpoint.
    """

    candidates = []

    named_endpoints = info.get(
        "named_endpoints",
        {}
    )

    if isinstance(named_endpoints, dict):
        for name, endpoint in named_endpoints.items():

            score = 0

            text = str(name).lower()

            if "generate" in text:
                score += 100

            if "video" in text:
                score += 50

            if "text" in text:
                score += 20

            candidates.append(
                (
                    score,
                    name,
                    endpoint
                )
            )

    # Some Gradio versions expose named endpoints differently
    if not candidates:

        endpoints = info.get(
            "endpoints",
            []
        )

        if isinstance(endpoints, list):

            for endpoint in endpoints:

                name = endpoint.get(
                    "api_name",
                    ""
                )

                if not name:
                    continue

                score = 0

                text = str(name).lower()

                if "generate" in text:
                    score += 100

                if "video" in text:
                    score += 50

                candidates.append(
                    (
                        score,
                        name,
                        endpoint
                    )
                )

    if not candidates:
        raise RuntimeError(
            "Could not find a named Hugging Face endpoint."
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    score, name, endpoint = candidates[0]

    log.info(
        "HF_ENDPOINT_AUTO_SELECTED=%s",
        name
    )

    return (
        name
        if str(name).startswith("/")
        else "/" + str(name),
        endpoint
    )


# =========================================================
# PARAMETER VALUE SELECTION
# =========================================================

def choose_value_from_parameter(parameter):
    """
    Choose safe values based on the parameter name.

    Wan 2.1 ZeroGPU currently rejects:
    - frames > 81
    - height > 832
    """

    name = str(
        parameter.get(
            "label",
            ""
        )
        or parameter.get(
            "parameter_name",
            ""
        )
        or parameter.get(
            "name",
            ""
        )
    ).lower()

    minimum = parameter.get(
        "minimum"
    )

    maximum = parameter.get(
        "maximum"
    )

    combined = name

    # -----------------------------------------------------
    # NUMBER OF FRAMES
    # -----------------------------------------------------

    if (
        "num_frames" in combined
        or "number of frames" in combined
        or "frames" in combined
    ):
        value = 81

    # -----------------------------------------------------
    # WIDTH
    # -----------------------------------------------------

    elif "width" in combined:
        value = 576

    # -----------------------------------------------------
    # HEIGHT
    # -----------------------------------------------------

    elif "height" in combined:
        value = 832

    # -----------------------------------------------------
    # INFERENCE STEPS
    # -----------------------------------------------------

    elif (
        "steps" in combined
        or "num_inference_steps" in combined
        or "inference steps" in combined
    ):
        value = 20

    # -----------------------------------------------------
    # GUIDANCE
    # -----------------------------------------------------

    elif (
        "guidance" in combined
        or "guidance scale" in combined
        or combined == "cfg"
        or "cfg scale" in combined
    ):
        value = 5.0

    # -----------------------------------------------------
    # SEED
    # -----------------------------------------------------

    elif "seed" in combined:
        value = 0

    # -----------------------------------------------------
    # FPS
    # -----------------------------------------------------

    elif (
        "fps" in combined
        or "frame rate" in combined
    ):
        value = 16

    # -----------------------------------------------------
    # UNKNOWN NUMERIC PARAMETER
    # -----------------------------------------------------

    else:

        if minimum is not None:
            value = minimum

        elif maximum is not None:
            value = maximum

        else:
            value = 0

    # -----------------------------------------------------
    # RESPECT MAXIMUM
    # -----------------------------------------------------

    if maximum is not None:

        try:
            value = min(
                value,
                maximum
            )
        except Exception:
            pass

    # -----------------------------------------------------
    # RESPECT MINIMUM
    # -----------------------------------------------------

    if minimum is not None:

        try:
            value = max(
                value,
                minimum
            )
        except Exception:
            pass

    return value


# =========================================================
# BUILD GENERATE ARGUMENTS
# =========================================================

def build_generate_arguments(endpoint_info, prompt):
    """
    Build arguments according to the discovered endpoint.
    """

    parameters = endpoint_info.get(
        "parameters",
        []
    )

    if not isinstance(parameters, list):
        parameters = []

    args = []

    for parameter in parameters:

        if not isinstance(parameter, dict):
            continue

        name = (
            parameter.get("parameter_name")
            or parameter.get("name")
            or parameter.get("label")
            or ""
        )

        name_lower = str(name).lower()

        # -------------------------------------------------
        # PROMPT
        # -------------------------------------------------

        if (
            "prompt" in name_lower
            and "negative" not in name_lower
        ):
            value = prompt

        # -------------------------------------------------
        # NEGATIVE PROMPT
        # -------------------------------------------------

        elif "negative" in name_lower:

            value = (
                "blurry, low quality, distorted, "
                "deformed, duplicate person, "
                "extra limbs, text, subtitles, logo, "
                "watermark, cartoon, anime"
            )

        # -------------------------------------------------
        # MODEL SELECTOR
        # -------------------------------------------------

        elif (
            "model" in name_lower
            and isinstance(
                parameter.get("enum"),
                list
            )
        ):

            choices = parameter.get(
                "enum",
                []
            )

            value = choices[0] if choices else None

        # -------------------------------------------------
        # BOOLEAN
        # -------------------------------------------------

        elif parameter.get("type") == "boolean":

            value = False

        # -------------------------------------------------
        # FILE
        # -------------------------------------------------

        elif (
            "image" in name_lower
            or "video" in name_lower
        ):

            # Do not invent a file for optional file inputs.
            if parameter.get("optional", False):
                value = None
            else:
                value = None

        # -------------------------------------------------
        # NUMERIC
        # -------------------------------------------------

        else:
            value = choose_value_from_parameter(
                parameter
            )

        args.append(value)

        log.info(
            "HF_PARAMETER name=%s value=%s",
            name,
            value
        )

    return args


# =========================================================
# FIND VIDEO VALUE
# =========================================================

def find_video_value(value):
    """
    Search recursively for a generated video/file.
    """

    if value is None:
        return None

    if isinstance(value, str):

        # Direct path / URL
        if (
            value.startswith("http://")
            or value.startswith("https://")
            or os.path.exists(value)
        ):
            return value

        return None

    if isinstance(value, dict):

        for key in [
            "video",
            "video_path",
            "path",
            "url",
            "file",
            "value"
        ]:

            if key in value:

                found = find_video_value(
                    value[key]
                )

                if found:
                    return found

        for item in value.values():

            found = find_video_value(
                item
            )

            if found:
                return found

        return None

    if isinstance(value, (list, tuple)):

        for item in value:

            found = find_video_value(
                item
            )

            if found:
                return found

    return None


# =========================================================
# DOWNLOAD FILE
# =========================================================

def download_file(source, destination):
    if not source:
        raise RuntimeError(
            "HF returned an empty video result"
        )

    if isinstance(source, str):

        if source.startswith(
            "http://"
        ) or source.startswith(
            "https://"
        ):

            response = requests.get(
                source,
                timeout=300
            )

            response.raise_for_status()

            Path(destination).write_bytes(
                response.content
            )

            return destination

        if os.path.exists(source):

            Path(destination).write_bytes(
                Path(source).read_bytes()
            )

            return destination

    # Gradio FileData-like object
    if hasattr(source, "path"):

        path = source.path

        if path and os.path.exists(path):

            Path(destination).write_bytes(
                Path(path).read_bytes()
            )

            return destination

    raise RuntimeError(
        "Could not download Hugging Face video result"
    )


# =========================================================
# GENERATE AI VIDEO
# =========================================================

def generate_ai_video(prompt, output_path):
    client = get_hf_client()

    info = get_api_schema(
        client
    )

    endpoint_name, endpoint_info = resolve_endpoint(
        client,
        info
    )

    log.info(
        "HF_GENERATE_ENDPOINT=%s",
        endpoint_name
    )

    args = build_generate_arguments(
        endpoint_info,
        prompt
    )

    log.info(
        "HF_GENERATE_START"
    )

    result = client.predict(
        *args,
        api_name=endpoint_name
    )

    log.info(
        "HF_GENERATE_RESULT_RECEIVED"
    )

    video_value = find_video_value(
        result
    )

    if video_value is None:

        # Sometimes Gradio returns FileData
        if isinstance(result, tuple):
            for item in result:
                video_value = find_video_value(
                    item
                )

                if video_value:
                    break

    if video_value is None:
        raise RuntimeError(
            "Hugging Face returned no video file."
        )

    download_file(
        video_value,
        output_path
    )

    if not os.path.exists(output_path):
        raise RuntimeError(
            "Generated video file does not exist."
        )

    if os.path.getsize(output_path) < 1000:
        raise RuntimeError(
            "Generated video file is too small."
        )

    log.info(
        "HF_VIDEO_READY path=%s size=%s",
        output_path,
        os.path.getsize(output_path)
    )

    return output_path


# =========================================================
# FFMPEG NORMALIZE VIDEO
# =========================================================

def normalize_video(
    input_path,
    output_path
):

    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vf",
            (
                f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:"
                "(ow-iw)/2:(oh-ih)/2"
            ),
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            output_path
        ]
    )

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_paths,
    output_path
):

    concat_file = BASE_DIR / (
        f"concat_{uuid.uuid4().hex}.txt"
    )

    lines = []

    for path in video_paths:

        safe_path = str(
            Path(path).resolve()
        ).replace(
            "'",
            "'\\''"
        )

        lines.append(
            f"file '{safe_path}'"
        )

    concat_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    run_command(
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
            output_path
        ]
    )

    return output_path


# =========================================================
# TEXT TO SPEECH
# =========================================================

async def generate_tts(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text,
        TTS_VOICE
    )

    await communicate.save(
        output_path
    )

    return output_path


# =========================================================
# MUX AUDIO
# =========================================================

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
            video_path,
            "-i",
            audio_path,
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
            output_path
        ]
    )

    return output_path


# =========================================================
# FULL VIDEO PIPELINE
# =========================================================

async def create_reel(story):

    job_id = uuid.uuid4().hex

    work_dir = (
        BASE_DIR / job_id
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        log.info(
            "VIDEO_JOB_START id=%s",
            job_id
        )

        # -------------------------------------------------
        # 1. GROQ STORYBOARD
        # -------------------------------------------------

        scenes = generate_storyboard(
            story
        )

        log.info(
            "STORYBOARD_SCENES=%s",
            len(scenes)
        )

        generated_videos = []

        # -------------------------------------------------
        # 2. GENERATE VIDEO SCENES
        # -------------------------------------------------

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            prompt = scene.get(
                "prompt",
                ""
            ).strip()

            if not prompt:
                continue

            raw_video = work_dir / (
                f"scene_{index}_raw.mp4"
            )

            normalized_video = work_dir / (
                f"scene_{index}.mp4"
            )

            log.info(
                "SCENE_START=%s",
                index
            )

            generate_ai_video(
                prompt,
                str(raw_video)
            )

            normalize_video(
                str(raw_video),
                str(normalized_video)
            )

            generated_videos.append(
                str(normalized_video)
            )

            log.info(
                "SCENE_DONE=%s",
                index
            )

        if not generated_videos:
            raise RuntimeError(
                "No video scenes were generated."
            )

        # -------------------------------------------------
        # 3. CONCAT
        # -------------------------------------------------

        combined_video = work_dir / (
            "combined.mp4"
        )

        if len(generated_videos) == 1:

            combined_video.write_bytes(
                Path(
                    generated_videos[0]
                ).read_bytes()
            )

        else:

            concat_videos(
                generated_videos,
                str(combined_video)
            )

        # -------------------------------------------------
        # 4. TTS
        # -------------------------------------------------

        audio_file = work_dir / (
            "narration.mp3"
        )

        await generate_tts(
            story,
            str(audio_file)
        )

        # -------------------------------------------------
        # 5. FINAL MUX
        # -------------------------------------------------

        final_video = work_dir / (
            "final_reel.mp4"
        )

        mux_audio(
            str(combined_video),
            str(audio_file),
            str(final_video)
        )

        log.info(
            "VIDEO_JOB_DONE id=%s",
            job_id
        )

        return str(final_video)

    except Exception as e:

        log.error(
            "VIDEO_JOB_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        raise


# =========================================================
# TELEGRAM HANDLERS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 أهلاً! ابعتلي القصة، وأنا أحولها لفيديو Reel."
    )


async def handle_message(
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

    # Limit extremely large inputs
    if len(story) > 12000:
        story = story[:12000]

    await update.message.reply_text(
        "🎬 وصلت القصة.\n"
        "🧠 بجهز المشاهد...\n"
        "⏳ وبعدها بصنع الفيديو."
    )

    try:

        final_video = await create_reel(
            story
        )

        await update.message.reply_video(
            video=final_video,
            caption=(
                "🎬 تم تجهيز الفيديو بنجاح."
            ),
            supports_streaming=True
        )

    except Exception as e:

        error = safe_error_text(e)

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            error
        )

        await update.message.reply_text(
            "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
            f"{error[:1500]}"
        )


# =========================================================
# TELEGRAM ERROR HANDLER
# =========================================================

async def telegram_error_handler(
    update,
    context
):

    error = context.error

    log.error(
        "TELEGRAM_HANDLER_ERROR=%s",
        safe_error_text(error),
        exc_info=True
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing"
        )

    log.info(
        "ABOSARAJ STARTING"
    )

    start_health_server()

    check_telegram_connection()

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
            handle_message
        )
    )

    application.add_error_handler(
        telegram_error_handler
    )

    log.info(
        "TELEGRAM_HANDLERS_READY"
    )

    log.info(
        "BOT_START_POLLING"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
