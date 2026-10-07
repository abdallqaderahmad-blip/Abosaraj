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

HF_API_NAME = os.getenv(
    "HF_API_NAME",
    ""
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

PORT = int(
    os.getenv("PORT", "10000")
)

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

# أول Pipeline حقيقي:
# 3 مشاهد × 5 ثواني = حوالي 15 ثانية
#
# لاحقاً نرفعها تدريجياً:
# 6 scenes = 30s
# 12 scenes = 60s
# 18 scenes = 90s
#
# يمكن تغييرها من Render Environment Variables.

SHOT_COUNT = int(
    os.getenv("SHOT_COUNT", "3")
)

SHOT_DURATION = int(
    os.getenv("SHOT_DURATION", "5")
)

# Final Reel
FINAL_WIDTH = 720
FINAL_HEIGHT = 1280

# Wan generation
GEN_WIDTH = 576
GEN_HEIGHT = 832

# Wan 2.1 1.3B
GEN_FRAMES = 81
GEN_FPS = 16

GEN_STEPS = 20
GEN_GUIDANCE = 5.0
GEN_SEED = 0

# Arabic male voice
TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()

# CTA
CTA_TEXT = os.getenv(
    "CTA_TEXT",
    "تابعنا، لأن القصة الجاية أخطر."
).strip()


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(
    "/tmp/abosaraj"
)

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

log = logging.getLogger(
    "abosaraj"
)


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

    text = re.sub(
        r"hf_[A-Za-z0-9]+",
        "[HF_TOKEN_REDACTED]",
        text
    )

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
        " ".join(
            map(str, command)
        )
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
        log.error(
            "BOT_TOKEN_MISSING"
        )
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
You are the lead writer and cinematic AI video director
for a professional TikTok / Instagram Reels channel.

Create a highly engaging fictional cinematic sci-fi story.

IMPORTANT:
The story is FICTIONAL unless the user explicitly asks
for a real event.

TARGET:
Approximately {total_seconds} seconds.

Create EXACTLY {SHOT_COUNT} scenes.

The story must have:

1. A very strong hook in the first seconds.
2. Clear escalation.
3. Something strange or unexpected.
4. A strong ending or mini-twist.
5. Smooth continuity between scenes.
6. Natural Arabic narration.
7. Visual prompts written in English for an AI video model.

The final video should feel like a real cinematic movie scene,
NOT a slideshow.

Characters and environment must remain visually consistent.

For every scene include:

- scene number
- duration
- English cinematic video prompt
- Arabic narration for that scene

The Arabic narration must describe what the viewer needs
to understand from the story.

DO NOT put visual prompt instructions inside narration.

The narration must sound natural when spoken by an Arabic
male narrator.

Keep the narration concise enough to fit the target duration.

At the end include a very short CTA.

Return ONLY valid JSON.

EXACT JSON STRUCTURE:

{{
  "title": "short Arabic title",
  "hook": "short Arabic hook",
  "narration": "complete Arabic narration",
  "cta": "short Arabic CTA",
  "scenes": [
    {{
      "scene": 1,
      "duration": {SHOT_DURATION},
      "prompt": "detailed English cinematic video prompt",
      "narration": "Arabic narration for this scene"
    }}
  ]
}}
"""

    user_prompt = f"""
Create the cinematic story based on this idea:

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

    content = (
        response
        .choices[0]
        .message
        .content
        .strip()
    )

    # Remove markdown fences if Groq adds them
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

    data = json.loads(
        content
    )

    scenes = data.get(
        "scenes",
        []
    )

    if not scenes:
        raise RuntimeError(
            "GROQ_RETURNED_NO_SCENES"
        )

    if len(scenes) != SHOT_COUNT:
        raise RuntimeError(
            "GROQ_SCENE_COUNT_MISMATCH: "
            f"expected={SHOT_COUNT} "
            f"received={len(scenes)}"
        )

    # Make sure every scene has narration
    for index, scene in enumerate(
        scenes,
        start=1
    ):
        if not scene.get(
            "prompt"
        ):
            raise RuntimeError(
                f"GROQ_SCENE_{index}_PROMPT_EMPTY"
            )

        if not scene.get(
            "narration"
        ):
            raise RuntimeError(
                f"GROQ_SCENE_{index}_NARRATION_EMPTY"
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

def resolve_endpoint(
    client,
    api_dict
):
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
        text = str(
            endpoint
        ).lower()

        if "generate" in text:
            return "/generate"

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
            for endpoint in (
                client_endpoints.values()
            ):
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
    steps=GEN_STEPS,
    guidance_scale=GEN_GUIDANCE,
    seed=GEN_SEED,
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

        # MODEL
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
                    c
                    for c in choices
                    if "nsfw"
                    not in str(c).lower()
                ]

                if non_nsfw:
                    value = non_nsfw[0]
                else:
                    value = choices[0]

            else:
                value = "wan-base"

        # PROMPT
        elif name_lower == "prompt":
            value = prompt

        # NEGATIVE PROMPT
        elif name_lower == "negative_prompt":
            value = (
                "static, slideshow, frozen frame, "
                "blurry, low quality, distorted, "
                "deformed, bad anatomy, extra limbs, "
                "extra fingers, duplicate objects, "
                "duplicate people, melting face, "
                "warped body, text, subtitles, "
                "watermark, logo, jpeg artifacts, "
                "unnatural movement, flickering, "
                "camera shake"
            )

        # WIDTH
        elif name_lower == "width":
            value = width

        # HEIGHT
        elif name_lower == "height":
            value = height

        # FRAMES
        elif name_lower in {
            "num_frames",
            "frames"
        }:
            value = num_frames

        # STEPS
        elif name_lower == "steps":
            value = steps

        # GUIDANCE
        elif name_lower in {
            "guidance_scale",
            "guidance"
        }:
            value = guidance_scale

        # SEED
        elif name_lower == "seed":
            value = seed

        # LORA
        elif name_lower in {
            "lora_scale",
            "lora_strength"
        }:
            value = lora_scale

        # CUSTOM CHECKPOINT
        elif name_lower in {
            "custom_ckpt",
            "custom_checkpoint",
            "custom_checkpoint_path"
        }:
            value = custom_ckpt

        # OTHER ENUM
        elif choices:
            value = choices[0]

        # BOOLEAN
        elif (
            isinstance(type_info, dict)
            and type_info.get("type")
            == "boolean"
        ):
            value = False

        # DEFAULT
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

        args.append(
            value
        )

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

    if isinstance(
        value,
        str
    ):
        if value.startswith(
            "http://"
        ) or value.startswith(
            "https://"
        ):
            return value

        if value.endswith(
            (
                ".mp4",
                ".webm",
                ".mov",
                ".avi"
            )
        ):
            return value

        if Path(value).exists():
            return value

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
# EXTRACT GPU TIME
# =========================================================

def extract_gpu_seconds(result):
    text = str(
        result
    )

    patterns = [
        r"([\d.]+)\s*s\s*GPU",
        r"([\d.]+)\s*sec(?:onds?)?\s*GPU",
        r"GPU\s*[:=]\s*([\d.]+)"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            try:
                return float(
                    match.group(1)
                )
            except Exception:
                pass

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

    object_path = getattr(
        value,
        "path",
        None
    )

    if object_path:
        value = object_path

    # Local file
    if (
        isinstance(value, str)
        and not value.startswith(
            ("http://", "https://")
        )
    ):
        local = Path(
            value
        )

        if local.exists():
            target = (
                output_dir
                / (
                    "video_"
                    + uuid.uuid4().hex
                    + local.suffix
                )
            )

            target.write_bytes(
                local.read_bytes()
            )

            return target

    # URL
    if (
        isinstance(value, str)
        and value.startswith(
            ("http://", "https://")
        )
    ):
        response = requests.get(
            value,
            timeout=180,
            stream=True
        )

        response.raise_for_status()

        target = (
            output_dir
            / (
                "video_"
                + uuid.uuid4().hex
                + ".mp4"
            )
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

    elapsed = (
        time.time()
        - start
    )

    gpu_seconds = extract_gpu_seconds(
        result
    )

    log.warning(
        "HF_PREDICT_DONE_SECONDS=%.2f",
        elapsed
    )

    if gpu_seconds is not None:
        log.warning(
            "HF_GPU_SECONDS=%.2f",
            gpu_seconds
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
# VIDEO DURATION
# =========================================================

def get_video_duration(
    video_path
):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]

    output = run_command(
        command
    ).strip()

    try:
        return float(
            output
        )
    except Exception:
        return 0.0


# =========================================================
# NORMALIZE VIDEO
# =========================================================

def normalize_video(
    input_path,
    output_path
):
    # Crop to exact 9:16 instead of adding black bars.
    #
    # Wan output is close to vertical.
    # We scale enough to cover 720x1280,
    # then crop the sides.

    video_filter = (
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        "setsar=1"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        video_filter,
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
                str(
                    Path(path).resolve()
                )
                .replace(
                    "'",
                    "'\\''"
                )
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
    text = (
        text or ""
    ).strip()

    if not text:
        raise RuntimeError(
            "TTS_TEXT_EMPTY"
        )

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
# CONCAT AUDIO
# =========================================================

def concat_audio(
    audio_paths,
    output_path
):
    list_file = (
        BASE_DIR
        / (
            "audio_concat_"
            + uuid.uuid4().hex
            + ".txt"
        )
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:
        for path in audio_paths:
            safe_path = (
                str(
                    Path(path).resolve()
                )
                .replace(
                    "'",
                    "'\\''"
                )
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
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path)
    ]

    run_command(
        command
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
# FONT DETECTION
# =========================================================

def find_arabic_font():
    candidates = [
        "Noto Sans Arabic",
        "Noto Naskh Arabic",
        "Noto Sans",
        "DejaVu Sans"
    ]

    for font_name in candidates:
        try:
            result = subprocess.run(
                [
                    "fc-match",
                    "-f",
                    "%{file}",
                    font_name
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            path = (
                result.stdout.strip()
            )

            if path and Path(path).exists():
                log.info(
                    "ARABIC_FONT=%s",
                    path
                )

                return path

        except Exception as e:
            log.warning(
                "FONT_CHECK_ERROR=%s",
                safe_error_text(e)
            )

    return None


# =========================================================
# SRT HELPERS
# =========================================================

def format_srt_time(
    seconds
):
    seconds = max(
        0.0,
        float(seconds)
    )

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600)
        // 60
    )

    secs = int(
        seconds % 60
    )

    millis = int(
        round(
            (seconds - int(seconds))
            * 1000
        )
    )

    if millis >= 1000:
        secs += 1
        millis -= 1000

    if secs >= 60:
        minutes += 1
        secs -= 60

    if minutes >= 60:
        hours += 1
        minutes -= 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{millis:03d}"
    )


# =========================================================
# CREATE SRT
# =========================================================

def create_srt(
    subtitle_items,
    output_path,
    cta_text=None,
    cta_duration=2.5
):
    lines = []

    counter = 1

    for item in subtitle_items:
        start = float(
            item["start"]
        )

        end = float(
            item["end"]
        )

        text = (
            item["text"]
            .strip()
        )

        if not text:
            continue

        lines.append(
            str(counter)
        )

        lines.append(
            f"{format_srt_time(start)} --> "
            f"{format_srt_time(end)}"
        )

        lines.append(
            text
        )

        lines.append("")

        counter += 1

    if cta_text:
        if subtitle_items:
            last_end = max(
                float(
                    x["end"]
                )
                for x in subtitle_items
            )
        else:
            last_end = 0.0

        cta_start = max(
            0.0,
            last_end - cta_duration
        )

        lines.append(
            str(counter)
        )

        lines.append(
            f"{format_srt_time(cta_start)} --> "
            f"{format_srt_time(last_end)}"
        )

        lines.append(
            cta_text
        )

        lines.append("")

    Path(
        output_path
    ).write_text(
        "\n".join(lines),
        encoding="utf-8-sig"
    )

    return Path(
        output_path
    )


# =========================================================
# ADD ARABIC CAPTIONS
# =========================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):
    font_path = find_arabic_font()

    # First try libass subtitles.
    #
    # This is much better for Arabic than drawtext
    # because libass handles shaping and RTL.

    if font_path:
        font_dir = str(
            Path(font_path).parent
        )

        subtitle_filter = (
            "subtitles="
            + str(srt_path)
            + ":"
            + "fontsdir="
            + font_dir
        )

    else:
        subtitle_filter = (
            "subtitles="
            + str(srt_path)
        )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(output_path)
    ]

    try:
        run_command(
            command
        )

    except Exception as e:
        log.error(
            "CAPTION_BURN_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        raise RuntimeError(
            "ARABIC_CAPTION_RENDER_FAILED: "
            + safe_error_text(e)
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

    # =====================================================
    # 1. GROQ STORY
    # =====================================================

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

    # =====================================================
    # 2. GENERATE VIDEO SCENES
    # =====================================================

    generated_videos = []

    gpu_total = 0.0

    for index, scene in enumerate(
        scenes,
        start=1
    ):
        prompt = (
            scene.get(
                "prompt",
                ""
            )
            .strip()
        )

        log.info(
            "SCENE_START=%s",
            index
        )

        scene_start = time.time()

        raw_video = generate_ai_video(
            prompt,
            job_dir / f"scene_{index}"
        )

        scene_elapsed = (
            time.time()
            - scene_start
        )

        log.info(
            "SCENE_GENERATION_DONE=%s elapsed=%.2f",
            index,
            scene_elapsed
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

    # =====================================================
    # 3. CONCAT VIDEO
    # =====================================================

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

    # =====================================================
    # 4. TTS PER SCENE
    #
    # This is important:
    # Each scene gets its own narration audio.
    # This lets us create accurate subtitle timing.
    # =====================================================

    scene_audio_paths = []

    subtitle_items = []

    current_time = 0.0

    for index, scene in enumerate(
        scenes,
        start=1
    ):
        narration = (
            scene.get(
                "narration",
                ""
            )
            .strip()
        )

        if not narration:
            continue

        audio_path = (
            job_dir
            / f"voice_{index}.mp3"
        )

        log.info(
            "TTS_START_SCENE=%s",
            index
        )

        await create_tts(
            narration,
            audio_path
        )

        audio_duration = get_video_duration(
            audio_path
        )

        if audio_duration <= 0:
            raise RuntimeError(
                f"TTS_DURATION_FAILED_SCENE_{index}"
            )

        scene_audio_paths.append(
            audio_path
        )

        subtitle_items.append(
            {
                "start": current_time,
                "end": (
                    current_time
                    + audio_duration
                ),
                "text": narration
            }
        )

        current_time += audio_duration

        log.info(
            "TTS_FINISHED_SCENE=%s duration=%.2f",
            index,
            audio_duration
        )

    if not scene_audio_paths:
        raise RuntimeError(
            "NO_TTS_AUDIO_GENERATED"
        )

    # =====================================================
    # 5. CONCAT VOICE
    # =====================================================

    voice_audio = (
        job_dir
        / "voice_full.m4a"
    )

    if len(scene_audio_paths) == 1:
        voice_audio.write_bytes(
            scene_audio_paths[0].read_bytes()
        )
    else:
        concat_audio(
            scene_audio_paths,
            voice_audio
        )

    # =====================================================
    # 6. CREATE CAPTIONS
    # =====================================================

    srt_path = (
        job_dir
        / "captions.srt"
    )

    cta = (
        storyboard.get(
            "cta",
            ""
        )
        .strip()
        or CTA_TEXT
    )

    create_srt(
        subtitle_items,
        srt_path,
        cta_text=cta,
        cta_duration=2.5
    )

    log.info(
        "SRT_CREATED=%s",
        srt_path
    )

    # =====================================================
    # 7. MUX VOICE INTO VIDEO
    # =====================================================

    voiced_video = (
        job_dir
        / "voiced.mp4"
    )

    mux_audio(
        combined_video,
        voice_audio,
        voiced_video
    )

    # =====================================================
    # 8. BURN ARABIC CAPTIONS
    # =====================================================

    captioned_video = (
        job_dir
        / "captioned.mp4"
    )

    burn_captions(
        voiced_video,
        srt_path,
        captioned_video
    )

    # =====================================================
    # 9. FINAL
    # =====================================================

    final_video = (
        job_dir
        / "final.mp4"
    )

    final_video.write_bytes(
        captioned_video.read_bytes()
    )

    # =====================================================
    # 10. FINAL LOGS
    # =====================================================

    final_duration = get_video_duration(
        final_video
    )

    log.info(
        "FINAL_VIDEO_DURATION=%.2f",
        final_duration
    )

    log.info(
        "JOB_FINISHED=%s",
        job_id
    )

    return (
        final_video,
        storyboard
    )


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
        "أرسل لي فكرة القصة، وأنا أحولها إلى Reel.\n\n"
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
            "🎬 وصلت الفكرة.\n\n"
            "🧠 بكتب القصة...\n"
            "🎥 بجهز المشاهد...\n"
            "🗣️ بجهز الصوت...\n"
            "📝 بجهز الكتابة...\n\n"
            "استنى شوي 🔥"
        )
    )

    try:
        final_video, storyboard = (
            await create_reel(
                text
            )
        )

        title = (
            storyboard.get(
                "title",
                "Abosaraj Reel"
            )
        )

        final_duration = get_video_duration(
            final_video
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
                    f"⏱ المدة: "
                    f"{final_duration:.1f} ثانية\n"
                    "🎙️ صوت عربي\n"
                    "📝 كابشن عربي\n"
                    "📱 9:16\n\n"
                    "🔥 تابعنا، لأن القصة الجاية أخطر."
                )
            )

        try:
            await processing_message.delete()
        except Exception:
            pass

    except Exception as e:
        error = safe_error_text(
            e
        )

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
        error = safe_error_text(
            e
        )

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
# TEST 4
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
        "🧪 TEST4 بدأ..."
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

        predict_start = time.time()

        result = await asyncio.to_thread(
            client.predict,
            *args,
            api_name=api_name
        )

        elapsed = (
            time.time()
            - predict_start
        )

        gpu_seconds = extract_gpu_seconds(
            result
        )

        video_value = find_video_value(
            result
        )

        if not video_value:
            await update.message.reply_text(
                "❌ TEST4 ما لقى فيديو.\n\n"
                f"⏱ {elapsed:.2f}s"
            )
            return

        video_path = download_file(
            video_value,
            BASE_DIR / "hf_test4"
        )

        file_size = (
            video_path.stat().st_size
        )

        gpu_text = (
            f"{gpu_seconds:.2f}s"
            if gpu_seconds is not None
            else "unknown"
        )

        with open(
            video_path,
            "rb"
        ) as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=(
                    "✅ TEST4 نجح 🎬\n\n"
                    f"⏱ الطلب: {elapsed:.2f}s\n"
                    f"🎮 GPU: {gpu_text}\n"
                    f"📦 الحجم: "
                    f"{file_size / 1024 / 1024:.2f} MB"
                )
            )

    except Exception as e:
        total = (
            time.time()
            - start
        )

        error = safe_error_text(
            e
        )

        log.error(
            "HFT_TEST4_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ TEST4 فشل.\n\n"
            + error[:1800]
            + f"\n\n⏱ {total:.2f}s"
        )


# =========================================================
# TEST 5.1 — PRODUCTION WAN TEST
# =========================================================

async def hf_test5_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    log.warning(
        "HFT_TEST5_START"
    )

    await update.message.reply_text(
        "🚀 TEST5.1 بدأ...\n\n"
        "📐 480×832\n"
        "🎞️ 81 frames\n"
        "⚙️ 20 steps\n"
        "🤖 wan-base\n\n"
        "استنى شوي 🎬"
    )

    total_start = time.time()

    try:
        client = get_hf_client()

        api_name = "/generate"

        test_prompt = (
            "A highly realistic cinematic science fiction "
            "scene inside a modern futuristic research "
            "laboratory at night. A humanoid robot slowly "
            "turns its head and notices a human scientist "
            "standing behind glass. The scientist looks "
            "surprised. Subtle natural body movement, "
            "realistic facial expressions, realistic hands, "
            "realistic physics, cinematic camera slowly "
            "moves forward, shallow depth of field, "
            "dramatic but realistic laboratory lighting, "
            "photorealistic live action movie look, "
            "high detail, coherent motion, no text."
        )

        negative_prompt = (
            "static image, slideshow, frozen frame, "
            "blurry, low quality, distorted, deformed, "
            "bad anatomy, extra limbs, extra fingers, "
            "duplicate person, duplicate robot, "
            "melting face, warped body, text, subtitles, "
            "watermark, logo, jpeg artifacts, "
            "unnatural motion, flickering, camera shake"
        )

        args = [
            "wan-base",
            test_prompt,
            negative_prompt,
            480,
            832,
            81,
            20,
            5.0,
            0,
            1.0,
            None
        ]

        predict_start = time.time()

        result = await asyncio.to_thread(
            client.predict,
            *args,
            api_name=api_name
        )

        predict_elapsed = (
            time.time()
            - predict_start
        )

        gpu_seconds = extract_gpu_seconds(
            result
        )

        video_value = find_video_value(
            result
        )

        if not video_value:
            raise RuntimeError(
                "HFT_TEST5_NO_VIDEO_FOUND"
            )

        video_path = download_file(
            video_value,
            BASE_DIR / "hf_test5"
        )

        video_path = Path(
            video_path
        )

        file_size = (
            video_path.stat().st_size
        )

        video_duration = (
            81 / 16
        )

        if gpu_seconds is not None:
            gpu_per_second = (
                gpu_seconds
                / video_duration
            )

            estimated_90 = (
                gpu_per_second
                * 90
            )

            estimated_90_text = (
                f"{estimated_90:.1f} ثانية GPU"
            )
        else:
            estimated_90_text = (
                "غير محسوب"
            )

        total = (
            time.time()
            - total_start
        )

        caption = (
            "🚀 TEST5.1 نجح 🎬🔥\n\n"
            "📐 الدقة: 480×832\n"
            "🎞️ الفريمات: 81\n"
            f"🎬 مدة الفيديو: {video_duration:.2f}s\n"
            "⚙️ Steps: 20\n"
            "🤖 Model: wan-base\n"
            "🧩 LoRA: 1.0\n\n"
            f"🎮 GPU: "
            + (
                f"{gpu_seconds:.2f}s"
                if gpu_seconds is not None
                else "غير معروف"
            )
            + "\n"
            f"⏱ زمن الطلب: {predict_elapsed:.2f}s\n"
            f"⏱ الإجمالي: {total:.2f}s\n"
            f"📦 الحجم: "
            f"{file_size / 1024 / 1024:.2f} MB\n\n"
            "📊 تقدير 90 ثانية بنفس المعدل:\n"
            f"{estimated_90_text}"
        )

        with open(
            video_path,
            "rb"
        ) as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=caption
            )

        log.warning(
            "HFT_TEST5_FINISHED_SUCCESS"
        )

    except Exception as e:
        total = (
            time.time()
            - total_start
        )

        error = safe_error_text(
            e
        )

        log.error(
            "HFT_TEST5_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ TEST5.1 فشل.\n\n"
            + error[:1800]
            + f"\n\n⏱ {total:.2f}s"
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
        CommandHandler(
            "hftest5",
            hf_test5_command
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
        and incoming_secret
        != BOT_WEBHOOK_SECRET
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

        asyncio.run_coroutine_threadsafe(
            telegram_application.process_update(
                update
            ),
            telegram_loop
        )

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

    log.info(
        "GEN_WIDTH=%s",
        GEN_WIDTH
    )

    log.info(
        "GEN_HEIGHT=%s",
        GEN_HEIGHT
    )

    log.info(
        "GEN_FRAMES=%s",
        GEN_FRAMES
    )

    log.info(
        "GEN_STEPS=%s",
        GEN_STEPS
    )

    log.info(
        "TTS_VOICE=%s",
        TTS_VOICE
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
