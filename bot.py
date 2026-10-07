import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
import hashlib
import time
from pathlib import Path

import requests
import edge_tts
from flask import Flask, request

from groq import Groq
from gradio_client import Client

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

PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()

WEBHOOK_PATH = "/telegram/webhook"

BOT_WEBHOOK_SECRET = os.getenv(
    "BOT_WEBHOOK_SECRET",
    ""
).strip()

if not BOT_WEBHOOK_SECRET and BOT_TOKEN:
    BOT_WEBHOOK_SECRET = hashlib.sha256(
        BOT_TOKEN.encode("utf-8")
    ).hexdigest()[:32]


# =========================================================
# VIDEO SETTINGS
# =========================================================

SHOT_COUNT = int(
    os.getenv("SHOT_COUNT", "1")
)

SHOT_DURATION = int(
    os.getenv("SHOT_DURATION", "5")
)

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280

GEN_WIDTH = 576
GEN_HEIGHT = 832

GEN_FRAMES = 81
GEN_FPS = 16

TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path("/tmp/abosaraj")
BASE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return "Abosaraj is alive", 200


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "service": "abosaraj",
        "telegram_webhook": True,
        "hf_space": HF_SPACE
    }, 200


# =========================================================
# TELEGRAM GLOBALS
# =========================================================

telegram_application = None
telegram_loop = None
telegram_ready = threading.Event()


# =========================================================
# ERROR SANITIZER
# =========================================================

def safe_error_text(error):
    text = str(error)

    secrets = [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
        BOT_WEBHOOK_SECRET,
    ]

    for secret in secrets:
        if secret:
            text = text.replace(
                secret,
                "[REDACTED]"
            )

    # Hide possible HF tokens
    text = re.sub(
        r"hf_[A-Za-z0-9]+",
        "[HF_TOKEN_REDACTED]",
        text
    )

    # Hide Telegram bot token-looking strings
    text = re.sub(
        r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b",
        "[BOT_TOKEN_REDACTED]",
        text
    )

    return text


# =========================================================
# COMMAND RUNNER
# =========================================================

def run_command(command):
    log.info(
        "RUN_COMMAND=%s",
        " ".join(map(str, command))
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "COMMAND_FAILED:\n"
            + result.stderr[-5000:]
        )

    return result.stdout


# =========================================================
# TELEGRAM CONNECTION CHECK
# =========================================================

def check_telegram_connection():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN_MISSING")
        return

    try:
        response = requests.get(
            "https://api.telegram.org/bot"
            + BOT_TOKEN
            + "/getWebhookInfo",
            timeout=20
        )

        data = response.json()

        webhook = data.get(
            "result",
            {}
        )

        url = webhook.get(
            "url",
            ""
        )

        pending = webhook.get(
            "pending_update_count",
            0
        )

        log.info(
            "TELEGRAM_WEBHOOK url=%s pending=%s ip=%s",
            url if url else "<EMPTY>",
            pending,
            webhook.get("ip_address")
        )

        if not url:
            log.warning(
                "TELEGRAM_WEBHOOK_IS_EMPTY"
            )

    except Exception as e:
        log.error(
            "TELEGRAM_CONNECTION_CHECK_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )


# =========================================================
# GROQ
# =========================================================

def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY_MISSING"
        )

    return Groq(
        api_key=GROQ_API_KEY
    )


# =========================================================
# STORYBOARD
# =========================================================

def create_storyboard(user_text):
    client = get_groq_client()

    total_seconds = (
        SHOT_COUNT * SHOT_DURATION
    )

    system_prompt = f"""
You are a professional cinematic AI video director.

Create a short realistic cinematic sci-fi story
for TikTok / Instagram Reels.

The story must be fictional unless the user explicitly
asks for a real event.

Target duration: approximately {total_seconds} seconds.

Create exactly {SHOT_COUNT} scenes.

Each scene must describe:
- what happens
- environment
- characters
- camera movement
- lighting
- cinematic visual details
- continuity with previous scene

The video must feel like real moving footage,
not a slideshow.

Use realistic physical motion.
Avoid impossible visual effects unless they are
clearly part of the story.

Return ONLY valid JSON in this exact structure:

{{
  "title": "string",
  "hook": "string",
  "scenes": [
    {{
      "scene": 1,
      "duration": {SHOT_DURATION},
      "prompt": "detailed cinematic video prompt"
    }}
  ]
}}
"""

    user_prompt = f"""
Create the storyboard based on this idea:

{user_text}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()

    # Remove accidental markdown fences
    content = re.sub(
        r"^```(?:json)?",
        "",
        content,
        flags=re.IGNORECASE
    )

    content = re.sub(
        r"```$",
        "",
        content
    )

    content = content.strip()

    data = json.loads(content)

    scenes = data.get(
        "scenes",
        []
    )

    if not scenes:
        raise RuntimeError(
            "GROQ_RETURNED_NO_SCENES"
        )

    return data


# =========================================================
# HUGGING FACE CLIENT
# =========================================================

def get_hf_client():
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN_MISSING"
        )

    log.info(
        "HF_CONNECTING_SPACE=%s",
        HF_SPACE
    )

    client = Client(
        HF_SPACE,
        token=HF_TOKEN
    )

    return client


# =========================================================
# HF API SCHEMA
# =========================================================

def get_api_schema(client):
    return client.view_api(
        return_format="dict"
    )


# =========================================================
# RESOLVE GENERATE ENDPOINT
# =========================================================

def resolve_endpoint(client, api_dict):
    if HF_API_NAME:
        return HF_API_NAME

    named = api_dict.get(
        "named_endpoints",
        {}
    )

    if "/generate" in named:
        return "/generate"

    endpoints = api_dict.get(
        "unnamed_endpoints",
        []
    )

    for endpoint in endpoints:
        text = str(endpoint).lower()

        if "generate" in text:
            return "/generate"

    # Fallback
    try:
        client_endpoints = getattr(
            client,
            "endpoints",
            {}
        )

        if isinstance(
            client_endpoints,
            dict
        ):
            for endpoint in client_endpoints.values():
                name = getattr(
                    endpoint,
                    "api_name",
                    None
                )

                if name == "/generate":
                    return "/generate"

    except Exception:
        pass

    raise RuntimeError(
        "HF_GENERATE_ENDPOINT_NOT_FOUND"
    )


# =========================================================
# BUILD HF ARGUMENTS
# =========================================================

def build_generate_arguments(
    api_dict,
    endpoint_name,
    prompt,
    width=GEN_WIDTH,
    height=GEN_HEIGHT,
    num_frames=GEN_FRAMES,
    steps=20,
    guidance_scale=5.0,
    seed=0,
    lora_scale=None,
    custom_ckpt=None
):
    named = api_dict.get(
        "named_endpoints",
        {}
    )

    endpoint_info = named.get(
        endpoint_name
    )

    if not endpoint_info:
        raise RuntimeError(
            "HF_ENDPOINT_SCHEMA_NOT_FOUND="
            + str(endpoint_name)
        )

    parameters = endpoint_info.get(
        "parameters",
        []
    )

    args = []

    for parameter in parameters:
        name = parameter.get(
            "parameter_name"
        )

        name_lower = str(
            name or ""
        ).lower()

        type_info = parameter.get(
            "type",
            {}
        )

        choices = []

        if isinstance(
            type_info,
            dict
        ):
            choices = type_info.get(
                "enum",
                []
            )

        value = None

        # -------------------------
        # MODEL
        # -------------------------

        if name_lower in {
            "model_key",
            "model",
            "model_name",
            "checkpoint",
            "checkpoint_name"
        }:
            if "wan-base" in choices:
                value = "wan-base"

            elif choices:
                non_nsfw = [
                    c for c in choices
                    if "nsfw" not in str(c).lower()
                ]

                if non_nsfw:
                    value = non_nsfw[0]
                else:
                    value = choices[0]

            else:
                value = "wan-base"

        # -------------------------
        # PROMPT
        # -------------------------

        elif name_lower == "prompt":
            value = prompt

        # -------------------------
        # NEGATIVE
        # -------------------------

        elif name_lower == "negative_prompt":
            value = (
                "static, blurry, low quality, "
                "distorted, deformed, bad anatomy, "
                "text, subtitles, watermark, "
                "jpeg artifacts, frozen frame, "
                "unnatural movement"
            )

        # -------------------------
        # WIDTH
        # -------------------------

        elif name_lower == "width":
            value = width

        # -------------------------
        # HEIGHT
        # -------------------------

        elif name_lower == "height":
            value = height

        # -------------------------
        # FRAMES
        # -------------------------

        elif name_lower in {
            "num_frames",
            "frames"
        }:
            value = num_frames

        # -------------------------
        # STEPS
        # -------------------------

        elif name_lower == "steps":
            value = steps

        # -------------------------
        # GUIDANCE
        # -------------------------

        elif name_lower in {
            "guidance_scale",
            "guidance"
        }:
            value = guidance_scale

        # -------------------------
        # SEED
        # -------------------------

        elif name_lower == "seed":
            value = seed

        # -------------------------
        # LORA
        # -------------------------

        elif name_lower in {
            "lora_scale",
            "lora_strength"
        }:
            value = lora_scale

        # -------------------------
        # CUSTOM CHECKPOINT
        # -------------------------

        elif name_lower in {
            "custom_ckpt",
            "custom_checkpoint",
            "custom_checkpoint_path"
        }:
            value = custom_ckpt

        # -------------------------
        # OTHER CHOICE
        # -------------------------

        elif choices:
            value = choices[0]

        # -------------------------
        # BOOLEAN
        # -------------------------

        elif isinstance(
            type_info,
            dict
        ) and type_info.get("type") == "boolean":
            value = False

        # -------------------------
        # DEFAULT
        # -------------------------

        else:
            if parameter.get(
                "parameter_has_default",
                False
            ):
                value = parameter.get(
                    "parameter_default"
                )

            else:
                value = None

        args.append(value)

        log.warning(
            "HF_PARAMETER name=%s value=%r",
            name,
            value
        )

    return args


# =========================================================
# FIND VIDEO RESULT
# =========================================================

def find_video_value(value):
    if value is None:
        return None

    # FileData-like object
    path = getattr(
        value,
        "path",
        None
    )

    if path:
        return path

    url = getattr(
        value,
        "url",
        None
    )

    if url:
        return url

    # String
    if isinstance(
        value,
        str
    ):
        if (
            value.startswith("http://")
            or value.startswith("https://")
        ):
            return value

        if (
            value.endswith(".mp4")
            or value.endswith(".webm")
            or value.endswith(".mov")
            or value.endswith(".avi")
        ):
            return value

        if Path(value).exists():
            return value

    # Dict
    if isinstance(
        value,
        dict
    ):
        for key in [
            "path",
            "url",
            "video",
            "file",
            "value"
        ]:
            if key in value:
                result = find_video_value(
                    value[key]
                )

                if result:
                    return result

        for item in value.values():
            result = find_video_value(
                item
            )

            if result:
                return result

    # List / tuple
    if isinstance(
        value,
        (list, tuple)
    ):
        for item in value:
            result = find_video_value(
                item
            )

            if result:
                return result

    return None


# =========================================================
# DOWNLOAD FILE
# =========================================================

def download_file(
    value,
    output_dir
):
    output_dir = Path(
        output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    if value is None:
        raise RuntimeError(
            "DOWNLOAD_VALUE_EMPTY"
        )

    # Object with path
    object_path = getattr(
        value,
        "path",
        None
    )

    if object_path:
        value = object_path

    # Local file
    if isinstance(
        value,
        str
    ) and not value.startswith(
        ("http://", "https://")
    ):
        local = Path(value)

        if local.exists():
            target = output_dir / (
                "video_"
                + uuid.uuid4().hex
                + local.suffix
            )

            target.write_bytes(
                local.read_bytes()
            )

            return target

    # URL
    if isinstance(
        value,
        str
    ) and value.startswith(
        ("http://", "https://")
    ):
        response = requests.get(
            value,
            timeout=180,
            stream=True
        )

        response.raise_for_status()

        target = output_dir / (
            "video_"
            + uuid.uuid4().hex
            + ".mp4"
        )

        with open(
            target,
            "wb"
        ) as f:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    f.write(chunk)

        return target

    raise RuntimeError(
        "UNSUPPORTED_VIDEO_RESULT="
        + safe_error_text(value)
    )


# =========================================================
# REAL HF VIDEO GENERATION
# =========================================================

def generate_ai_video(
    prompt,
    output_dir
):
    client = get_hf_client()

    api_dict = get_api_schema(
        client
    )

    endpoint_name = resolve_endpoint(
        client,
        api_dict
    )

    log.warning(
        "HF_GENERATE_ENDPOINT=%s",
        endpoint_name
    )

    args = build_generate_arguments(
        api_dict=api_dict,
        endpoint_name=endpoint_name,
        prompt=prompt
    )

    log.warning(
        "HF_PREDICT_START"
    )

    start = time.time()

    try:
        result = client.predict(
            *args,
            api_name=endpoint_name
        )

    except Exception as e:
        log.error(
            "HF_PREDICT_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        raise RuntimeError(
            "HF_GENERATION_FAILED: "
            + safe_error_text(e)
        )

    elapsed = time.time() - start

    log.warning(
        "HF_PREDICT_DONE_SECONDS=%.2f",
        elapsed
    )

    log.warning(
        "HF_RESULT_TYPE=%s",
        type(result).__name__
    )

    log.warning(
        "HF_RESULT_REPR=%r",
        result
    )

    video_value = find_video_value(
        result
    )

    if not video_value:
        raise RuntimeError(
            "HF_GENERATION_RETURNED_NO_VIDEO"
        )

    return download_file(
        video_value,
        output_dir
    )


# =========================================================
# TEST 4 — REAL WAN GENERATION
# =========================================================

async def hf_test4_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    log.warning(
        "HFT_TEST4_START"
    )

    await update.message.reply_text(
        "🧪 TEST4 بدأ...\n"
        "رح أجرب Wan Base فعليًا بفيديو صغير جدًا.\n"
        "استنى شوي."
    )

    start = time.time()

    try:
        client = get_hf_client()

        api_name = "/generate"

        test_prompt = (
            "A cinematic realistic scene of a humanoid "
            "robot standing alone in a dark futuristic "
            "laboratory, subtle camera movement, realistic "
            "lighting, high detail, cinematic atmosphere, "
            "realistic physical motion"
        )

        negative_prompt = (
            "static, blurry, low quality, distorted, "
            "deformed, text, subtitles, watermark"
        )

        args = [
            "wan-base",
            test_prompt,
            negative_prompt,
            320,
            320,
            21,
            1,
            5.0,
            0,
            1.0,
            None
        ]

        log.warning(
            "HFT_TEST4_API=%s",
            api_name
        )

        log.warning(
            "HFT_TEST4_MODEL=wan-base"
        )

        log.warning(
            "HFT_TEST4_WIDTH=320"
        )

        log.warning(
            "HFT_TEST4_HEIGHT=320"
        )

        log.warning(
            "HFT_TEST4_FRAMES=21"
        )

        log.warning(
            "HFT_TEST4_STEPS=1"
        )

        log.warning(
            "HFT_TEST4_GUIDANCE=5.0"
        )

        log.warning(
            "HFT_TEST4_SEED=0"
        )

        log.warning(
            "HFT_TEST4_LORA=1.0"
        )

        log.warning(
            "HFT_TEST4_CUSTOM_CKPT=None"
        )

        log.warning(
            "HFT_TEST4_BEFORE_PREDICT"
        )

        predict_start = time.time()

        result = await asyncio.to_thread(
            client.predict,
            *args,
            api_name=api_name
        )

        elapsed = time.time() - predict_start

        log.warning(
            "HFT_TEST4_PREDICT_DONE_SECONDS=%.2f",
            elapsed
        )

        log.warning(
            "HFT_TEST4_RESULT_TYPE=%s",
            type(result).__name__
        )

        log.warning(
            "HFT_TEST4_RESULT_REPR=%r",
            result
        )

        video_value = find_video_value(
            result
        )

        if not video_value:
            log.error(
                "HFT_TEST4_NO_VIDEO_FOUND"
            )

            await update.message.reply_text(
                "❌ Wan رجّع نتيجة لكن ما لقيت ملف فيديو.\n\n"
                f"⏱ زمن التوليد: {elapsed:.2f} ثانية\n\n"
                "ابعتلي Logs الخاصة بـ HFT_TEST4."
            )

            log.warning(
                "HFT_TEST4_FINISHED_NO_VIDEO"
            )

            return

        log.warning(
            "HFT_TEST4_VIDEO_VALUE=%r",
            video_value
        )

        try:
            video_path = download_file(
                video_value,
                BASE_DIR / "hf_test4"
            )

            log.warning(
                "HFT_TEST4_DOWNLOADED=%s",
                video_path
            )

            video_path = Path(
                video_path
            )

            file_size = (
                video_path.stat().st_size
            )

            log.warning(
                "HFT_TEST4_FILE_SIZE=%s",
                file_size
            )

            with open(
                video_path,
                "rb"
            ) as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=(
                        "✅ TEST4 نجح 🎬\n\n"
                        f"⏱ زمن التوليد: "
                        f"{elapsed:.2f} ثانية\n"
                        f"📦 الحجم: "
                        f"{file_size / 1024 / 1024:.2f} MB"
                    )
                )

            total = time.time() - start

            log.warning(
                "HFT_TEST4_TOTAL_SECONDS=%.2f",
                total
            )

            log.warning(
                "HFT_TEST4_FINISHED_SUCCESS"
            )

            return

        except Exception as e:
            log.error(
                "HFT_TEST4_DOWNLOAD_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

            await update.message.reply_text(
                "⚠️ Wan ولّد النتيجة، "
                "لكن صار خطأ أثناء تنزيل/إرسال الفيديو.\n\n"
                f"الخطأ:\n{safe_error_text(e)[:1200]}"
            )

            log.warning(
                "HFT_TEST4_FINISHED_DOWNLOAD_ERROR"
            )

    except Exception as e:
        total = time.time() - start

        error = safe_error_text(e)

        log.error(
            "HFT_TEST4_PREDICT_ERROR=%s",
            error,
            exc_info=True
        )

        log.warning(
            "HFT_TEST4_TOTAL_SECONDS=%.2f",
            total
        )

        log.warning(
            "HFT_TEST4_FINISHED_WITH_ERROR"
        )

        await update.message.reply_text(
            "❌ TEST4 فشل.\n\n"
            + error[:1800]
            + f"\n\n⏱ الزمن: {total:.2f} ثانية"
        )


# =========================================================
# NORMALIZE VIDEO
# =========================================================

def normalize_video(
    input_path,
    output_path
):
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        (
            "scale="
            f"{FINAL_WIDTH}:{FINAL_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2"
        ),
        "-r",
        str(GEN_FPS),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_path)
    ]

    run_command(
        command
    )

    return Path(
        output_path
    )


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_paths,
    output_path
):
    list_file = (
        BASE_DIR
        / (
            "concat_"
            + uuid.uuid4().hex
            + ".txt"
        )
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:
        for path in video_paths:
            safe_path = (
                str(Path(path).resolve())
                .replace("'", "'\\''")
            )

            f.write(
                "file '"
                + safe_path
                + "'\n"
            )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path)
    ]

    try:
        run_command(
            command
        )

    except Exception:
        # Re-encode fallback
        command = [
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
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            str(output_path)
        ]

        run_command(
            command
        )

    return Path(
        output_path
    )


# =========================================================
# EDGE TTS
# =========================================================

async def create_tts(
    text,
    output_path
):
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicate.save(
        str(output_path)
    )

    return Path(
        output_path
    )


# =========================================================
# MUX AUDIO
# =========================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
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
        str(output_path)
    ]

    run_command(
        command
    )

    return Path(
        output_path
    )


# =========================================================
# CREATE REEL
# =========================================================

async def create_reel(
    user_text
):
    job_id = uuid.uuid4().hex

    job_dir = (
        BASE_DIR
        / job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    log.info(
        "JOB_START=%s",
        job_id
    )

    storyboard = create_storyboard(
        user_text
    )

    log.info(
        "STORYBOARD_CREATED=%s",
        json.dumps(
            storyboard,
            ensure_ascii=False,
            default=str
        )
    )

    scenes = storyboard.get(
        "scenes",
        []
    )

    generated_videos = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):
        prompt = scene.get(
            "prompt",
            ""
        )

        log.info(
            "SCENE_START=%s",
            index
        )

        raw_video = generate_ai_video(
            prompt,
            job_dir / f"scene_{index}"
        )

        normalized_path = (
            job_dir
            / f"scene_{index}_normalized.mp4"
        )

        normalize_video(
            raw_video,
            normalized_path
        )

        generated_videos.append(
            normalized_path
        )

        log.info(
            "SCENE_FINISHED=%s",
            index
        )

    if not generated_videos:
        raise RuntimeError(
            "NO_GENERATED_VIDEOS"
        )

    combined_video = (
        job_dir
        / "combined.mp4"
    )

    if len(generated_videos) == 1:
        combined_video.write_bytes(
            generated_videos[0].read_bytes()
        )

    else:
        concat_videos(
            generated_videos,
            combined_video
        )

    story_text = (
        storyboard.get(
            "hook",
            ""
        )
        + " "
        + " ".join(
            scene.get(
                "prompt",
                ""
            )
            for scene in scenes
        )
    )

    audio_path = (
        job_dir
        / "voice.mp3"
    )

    await create_tts(
        story_text,
        audio_path
    )

    final_video = (
        job_dir
        / "final.mp4"
    )

    mux_audio(
        combined_video,
        audio_path,
        final_video
    )

    log.info(
        "JOB_FINISHED=%s",
        job_id
    )

    return final_video, storyboard


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    await update.message.reply_text(
        "🤖 أهلاً بك في Abosaraj.\n\n"
        "أرسل لي فكرة القصة، وأنا أحولها إلى فيديو.\n\n"
        "مثال:\n"
        "روبوت اكتشف أن صاحبه اختفى من ذاكرته."
    )


# =========================================================
# NORMAL MESSAGE
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    text = (
        update.message.text or ""
    ).strip()

    if not text:
        return

    log.info(
        "USER_MESSAGE=%s",
        text[:1000]
    )

    processing_message = (
        await update.message.reply_text(
            "🎬 وصلت الفكرة.\n"
            "هسا ببدأ تجهيز القصة والفيديو..."
        )
    )

    try:
        final_video, storyboard = (
            await asyncio.to_thread(
                lambda: asyncio.run(
                    create_reel(text)
                )
            )
        )

        title = storyboard.get(
            "title",
            "Abosaraj Reel"
        )

        with open(
            final_video,
            "rb"
        ) as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=(
                    "🎬 "
                    + title
                    + "\n\n"
                    "تابع الحساب لقصص مشوقة."
                )
            )

        try:
            await processing_message.delete()
        except Exception:
            pass

    except Exception as e:
        error = safe_error_text(e)

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
            + error[:1800]
        )


# =========================================================
# HF TEST 3
# =========================================================

async def hf_test3_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    log.warning(
        "HFT_TEST3_START"
    )

    await update.message.reply_text(
        "🧪 فحص Hugging Face TEST3 بدأ..."
    )

    try:
        log.warning(
            "HFT_TEST3_BEFORE_CLIENT"
        )

        client = get_hf_client()

        log.warning(
            "HFT_TEST3_CLIENT_CREATED"
        )

        log.warning(
            "HFT_TEST3_CLIENT_TYPE=%s",
            type(client).__name__
        )

        log.warning(
            "HFT_TEST3_CLIENT_DICT_START"
        )

        try:
            client_dict = getattr(
                client,
                "__dict__",
                {}
            )

            if isinstance(
                client_dict,
                dict
            ):
                for key, value in client_dict.items():
                    key_text = str(
                        key
                    ).lower()

                    if any(
                        secret_word in key_text
                        for secret_word in [
                            "token",
                            "auth",
                            "password",
                            "secret"
                        ]
                    ):
                        value = "[REDACTED]"

                    log.warning(
                        "CLIENT_ATTR %s=%r",
                        key,
                        value
                    )

        except Exception as e:
            log.error(
                "HFT_TEST3_CLIENT_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.warning(
            "HFT_TEST3_CLIENT_DICT_END"
        )

        log.warning(
            "HFT_TEST3_VIEW_API_START"
        )

        try:
            api_result = client.view_api()

            log.warning(
                "HFT_TEST3_VIEW_API_TYPE=%s",
                type(api_result).__name__
            )

            log.warning(
                "HFT_TEST3_VIEW_API_REPR_START"
            )

            log.warning(
                "%r",
                api_result
            )

            log.warning(
                "HFT_TEST3_VIEW_API_REPR_END"
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_VIEW_API_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.warning(
            "HFT_TEST3_DICT_START"
        )

        try:
            api_dict = client.view_api(
                return_format="dict"
            )

            log.warning(
                "HFT_TEST3_DICT_TYPE=%s",
                type(api_dict).__name__
            )

            if isinstance(
                api_dict,
                dict
            ):
                log.warning(
                    "HFT_TEST3_DICT_KEYS=%r",
                    list(api_dict.keys())
                )

                named = api_dict.get(
                    "named_endpoints",
                    {}
                )

                endpoints = api_dict.get(
                    "endpoints",
                    []
                )

                log.warning(
                    "HFT_TEST3_NAMED_ENDPOINTS=%r",
                    named
                )

                log.warning(
                    "HFT_TEST3_ENDPOINTS=%r",
                    endpoints
                )

            log.warning(
                "HFT_TEST3_API_DICT_FULL=%s",
                json.dumps(
                    api_dict,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                )
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.warning(
            "HFT_TEST3_DICT_END"
        )

        log.warning(
            "HFT_TEST3_ENDPOINTS_START"
        )

        try:
            endpoints = getattr(
                client,
                "endpoints",
                None
            )

            log.warning(
                "HFT_TEST3_ENDPOINTS_TYPE=%s",
                type(endpoints).__name__
            )

            log.warning(
                "HFT_TEST3_ENDPOINTS_REPR=%r",
                endpoints
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_ENDPOINTS_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.warning(
            "HFT_TEST3_ENDPOINTS_END"
        )

        log.warning(
            "HFT_TEST3_SPACE=%s",
            HF_SPACE
        )

        log.warning(
            "HFT_TEST3_API_NAME=%s",
            HF_API_NAME or "<AUTO>"
        )

        log.warning(
            "HFT_TEST3_SHOT_COUNT=%s",
            SHOT_COUNT
        )

        log.warning(
            "HFT_TEST3_SHOT_DURATION=%s",
            SHOT_DURATION
        )

        log.warning(
            "HFT_TEST3_FINISHED"
        )

        await update.message.reply_text(
            "✅ HFT_TEST3 خلص.\n\n"
            "افتح Render Logs وابحث عن:\n"
            "HFT_TEST3_START"
        )

    except Exception as e:
        error = safe_error_text(e)

        log.error(
            "HFT_TEST3_FATAL_ERROR=%s",
            error,
            exc_info=True
        )

        log.warning(
            "HFT_TEST3_FINISHED_WITH_ERROR"
        )

        await update.message.reply_text(
            "❌ HFT_TEST3 ERROR:\n\n"
            + error[:1500]
        )


# =========================================================
# TELEGRAM SETUP
# =========================================================

async def initialize_telegram():
    global telegram_application

    telegram_application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    telegram_application.add_handler(
        CommandHandler(
            "hftest3",
            hf_test3_command
        )
    )

    telegram_application.add_handler(
        CommandHandler(
            "hftest4",
            hf_test4_command
        )
    )

    telegram_application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            message_handler
        )
    )

    await telegram_application.initialize()

    await telegram_application.start()

    if not RENDER_EXTERNAL_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL_MISSING"
        )

    webhook_url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + WEBHOOK_PATH
    )

    log.info(
        "TELEGRAM_SETTING_WEBHOOK=%s",
        webhook_url
    )

    await telegram_application.bot.set_webhook(
        url=webhook_url,
        secret_token=BOT_WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

    log.info(
        "TELEGRAM_WEBHOOK_SET"
    )

    telegram_ready.set()


# =========================================================
# TELEGRAM THREAD
# =========================================================

def telegram_worker():
    global telegram_loop

    telegram_loop = (
        asyncio.new_event_loop()
    )

    asyncio.set_event_loop(
        telegram_loop
    )

    try:
        telegram_loop.run_until_complete(
            initialize_telegram()
        )

        check_telegram_connection()

        log.info(
            "TELEGRAM_WEBHOOK_WORKER_READY"
        )

        telegram_loop.run_forever()

    except Exception as e:
        log.error(
            "TELEGRAM_WORKER_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

    finally:
        try:
            telegram_loop.close()
        except Exception:
            pass


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    WEBHOOK_PATH,
    methods=["POST"]
)
def telegram_webhook():
    global telegram_application
    global telegram_loop

    if (
        telegram_application is None
        or telegram_loop is None
    ):
        log.error(
            "TELEGRAM_WEBHOOK_NOT_READY"
        )

        return (
            "Service not ready",
            503
        )

    incoming_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        ""
    )

    if (
        BOT_WEBHOOK_SECRET
        and incoming_secret != BOT_WEBHOOK_SECRET
    ):
        log.warning(
            "TELEGRAM_WEBHOOK_BAD_SECRET"
        )

        return (
            "Forbidden",
            403
        )

    try:
        data = request.get_json(
            force=True,
            silent=False
        )

        update = Update.de_json(
            data,
            telegram_application.bot
        )

        future = (
            asyncio.run_coroutine_threadsafe(
                telegram_application.process_update(
                    update
                ),
                telegram_loop
            )
        )

        # We intentionally don't block waiting
        # for the complete Telegram handler.
        _ = future

        return (
            "OK",
            200
        )

    except Exception as e:
        log.error(
            "TELEGRAM_WEBHOOK_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        return (
            "Bad Request",
            400
        )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN_MISSING"
        )

    if not GROQ_API_KEY:
        log.warning(
            "GROQ_API_KEY_MISSING"
        )

    if not HF_TOKEN:
        log.warning(
            "HF_TOKEN_MISSING"
        )

    if not RENDER_EXTERNAL_URL:
        raise RuntimeError(
            "RENDER_EXTERNAL_URL_MISSING"
        )

    log.info(
        "ABOSARAJ_STARTING"
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    log.info(
        "HF_API_NAME=%s",
        HF_API_NAME or "<AUTO>"
    )

    log.info(
        "SHOT_COUNT=%s",
        SHOT_COUNT
    )

    log.info(
        "SHOT_DURATION=%s",
        SHOT_DURATION
    )

    worker = threading.Thread(
        target=telegram_worker,
        name="telegram-worker",
        daemon=True
    )

    worker.start()

    if not telegram_ready.wait(
        timeout=60
    ):
        raise RuntimeError(
            "TELEGRAM_WORKER_NOT_READY"
        )

    log.info(
        "FLASK_STARTING_PORT=%s",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )


# =========================================================
# ENTRYPOINT
# =========================================================

if __name__ == "__main__":
    main()
