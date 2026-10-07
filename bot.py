import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import traceback
import threading
import subprocess
import time
import gc
from pathlib import Path

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
)

HF_SPACE = os.getenv(
    "HF_SPACE",
    "numanajmal0/wan-video-api"
)

# Optional:
# If empty, the bot will automatically discover the endpoint.
HF_API_NAME = os.getenv(
    "HF_API_NAME",
    ""
).strip()

# ============================================================
# IMPORTANT TEST MODE
# ============================================================
# First successful test = 1 scene only.
# After everything works, change to 8.
#
# You can also set:
# SHOT_COUNT=8
# in Render Environment Variables later.

SHOT_COUNT = int(
    os.getenv(
        "SHOT_COUNT",
        "1"
    )
)

SHOT_DURATION = int(
    os.getenv(
        "SHOT_DURATION",
        "5"
    )
)

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
)

BASE_DIR = Path(
    "/tmp/abosaraj"
)

BASE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

JOB_LOCK = threading.Lock()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(
    "ABOSARAJ"
)


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

flask_app = Flask(__name__)


@flask_app.route("/")
def home():

    return (
        "ABOSARAJ BOT ONLINE",
        200
    )


@flask_app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "online",
        "video_engine": "huggingface_zero_gpu",
        "space": HF_SPACE,
        "shots": SHOT_COUNT
    }, 200


def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    flask_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


# ============================================================
# SAFE ERROR TEXT
# ============================================================

def safe_error_text(error):

    text = str(error)

    secrets = [
        HF_TOKEN,
        BOT_TOKEN,
        GROQ_API_KEY
    ]

    for secret in secrets:

        if secret:
            text = text.replace(
                secret,
                "[REDACTED]"
            )

    return text


# ============================================================
# COMMAND RUNNER
# ============================================================

def run_command(
    cmd,
    timeout=900
):

    safe_cmd = []

    for item in cmd:

        item = str(item)

        if (
            "token" in item.lower()
            or item.startswith("hf_")
        ):

            safe_cmd.append(
                "[REDACTED]"
            )

        else:

            safe_cmd.append(
                item
            )

    log.info(
        "RUN_COMMAND=%s",
        " ".join(safe_cmd)
    )

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )

    except subprocess.TimeoutExpired:

        raise RuntimeError(
            f"Command timed out after {timeout} seconds"
        )

    if result.returncode != 0:

        log.error(
            "COMMAND_EXIT=%s",
            result.returncode
        )

        log.error(
            "COMMAND_STDERR=%s",
            result.stderr[-6000:]
        )

        raise RuntimeError(
            f"Command failed: {result.returncode}\n"
            f"{result.stderr[-3000:]}"
        )

    return result


# ============================================================
# GROQ DIRECTOR
# ============================================================

def clean_json(text):

    if not text:

        raise RuntimeError(
            "Groq returned an empty response."
        )

    text = text.strip()

    if text.startswith("```"):

        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"```$",
            "",
            text
        )

    return text.strip()


def create_storyboard(story):

    client = Groq(
        api_key=GROQ_API_KEY
    )

    system_prompt = f"""
You are a professional cinematic director for TikTok,
Instagram Reels and YouTube Shorts.

Convert the Arabic story into exactly {SHOT_COUNT}
cinematic AI VIDEO shots.

IMPORTANT:

- This is NOT a slideshow.
- Every shot must contain real physical movement.
- Every shot must be visually filmable.
- Characters must remain visually consistent.
- Avoid impossible camera movement.
- Avoid text appearing inside the generated video.
- Avoid subtitles inside the generated video.
- Avoid logos.
- Avoid changing character identity between shots.
- Each shot is approximately {SHOT_DURATION} seconds.

Return ONLY valid JSON.

Format:

{{
  "title": "short title",
  "narration": "short Arabic narration covering the whole story",
  "shots": [
    {{
      "id": 1,
      "visual_prompt": "English cinematic video prompt",
      "duration": {SHOT_DURATION}
    }}
  ]
}}

The visual_prompt must be in English.

Make the prompts highly cinematic:

- realistic humans
- realistic environments
- natural movement
- cinematic lighting
- camera movement
- depth of field
- realistic physics
- dramatic composition
- vertical social media framing
- 9:16 composition

The same character must keep the same:

hair
clothes
age
face
body
visual identity

Story:
{story}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.7,
        max_tokens=7000,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": story
            }
        ]
    )

    raw = (
        response
        .choices[0]
        .message
        .content
    )

    log.info(
        "GROQ_RESPONSE_LENGTH=%s",
        len(raw or "")
    )

    try:

        data = json.loads(
            clean_json(raw)
        )

    except Exception as e:

        raise RuntimeError(
            f"Groq returned invalid JSON: {safe_error_text(e)}"
        )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "Groq returned invalid storyboard."
        )

    shots = data.get(
        "shots"
    )

    if not isinstance(
        shots,
        list
    ):

        raise RuntimeError(
            "Storyboard does not contain shots."
        )

    if len(shots) < SHOT_COUNT:

        raise RuntimeError(
            f"Groq returned only {len(shots)} shots."
        )

    data["shots"] = (
        shots[:SHOT_COUNT]
    )

    narration = str(
        data.get(
            "narration",
            ""
        )
    ).strip()

    if not narration:

        narration = story

    data["narration"] = (
        narration
    )

    return data


# ============================================================
# HUGGING FACE CLIENT
# ============================================================

def get_hf_client():

    if not HF_TOKEN:

        raise RuntimeError(
            "HF_TOKEN is missing."
        )

    log.info(
        "HF_CONNECT_SPACE=%s",
        HF_SPACE
    )

    try:

        # Gradio 6 uses token=
        # instead of hf_token=.
        client = Client(
            HF_SPACE,
            token=HF_TOKEN
        )

        log.info(
            "HF_CLIENT_CONNECTED=True"
        )

        return client

    except Exception as e:

        raise RuntimeError(
            "Could not connect to Hugging Face Space: "
            + safe_error_text(e)
        )


# ============================================================
# HUGGING FACE API SCHEMA
# ============================================================

def get_api_schema(
    client
):

    try:

        # Return dictionary form.
        info = client.view_api(
            return_format="dict"
        )

        if not isinstance(
            info,
            dict
        ):

            raise RuntimeError(
                "view_api() did not return a dictionary."
            )

        log.info(
            "HF_API_SCHEMA_RECEIVED=True"
        )

        # Log endpoint names only.
        named = info.get(
            "named_endpoints",
            {}
        )

        unnamed = info.get(
            "unnamed_endpoints",
            {}
        )

        if isinstance(
            named,
            dict
        ):

            names = list(
                named.keys()
            )

            log.info(
                "HF_NAMED_ENDPOINTS=%s",
                names
            )

        else:

            log.info(
                "HF_NAMED_ENDPOINTS=[]"
            )

        if isinstance(
            unnamed,
            dict
        ):

            log.info(
                "HF_UNNAMED_ENDPOINT_COUNT=%s",
                len(unnamed)
            )

        return info

    except Exception as e:

        raise RuntimeError(
            "Could not read Hugging Face API schema: "
            + safe_error_text(e)
        )


# ============================================================
# RESOLVE REAL ENDPOINT
# ============================================================

def resolve_endpoint(
    api_info
):

    if not isinstance(
        api_info,
        dict
    ):

        raise RuntimeError(
            "Invalid Hugging Face API schema."
        )

    named_endpoints = (
        api_info.get(
            "named_endpoints",
            {}
        )
    )

    if not isinstance(
        named_endpoints,
        dict
    ):

        named_endpoints = {}

    log.info(
        "HF_ENDPOINT_COUNT=%s",
        len(named_endpoints)
    )

    # --------------------------------------------------------
    # 1. User explicitly configured endpoint
    # --------------------------------------------------------

    if HF_API_NAME:

        if HF_API_NAME in named_endpoints:

            log.info(
                "HF_ENDPOINT_SELECTED=%s",
                HF_API_NAME
            )

            return (
                HF_API_NAME,
                named_endpoints[
                    HF_API_NAME
                ]
            )

        log.warning(
            "HF_CONFIGURED_ENDPOINT_NOT_FOUND=%s",
            HF_API_NAME
        )

    # --------------------------------------------------------
    # 2. Automatically find generate/video endpoint
    # --------------------------------------------------------

    preferred = []

    for name, endpoint in (
        named_endpoints.items()
    ):

        normalized = (
            str(name)
            .lower()
        )

        if any(
            word in normalized
            for word in (
                "generate",
                "video",
                "text_to_video",
                "text-to-video",
                "predict"
            )
        ):

            preferred.append(
                (name, endpoint)
            )

    if len(preferred) == 1:

        selected = preferred[0]

        log.info(
            "HF_ENDPOINT_AUTO_SELECTED=%s",
            selected[0]
        )

        return selected

    # --------------------------------------------------------
    # 3. Only one named endpoint
    # --------------------------------------------------------

    if len(named_endpoints) == 1:

        selected = next(
            iter(
                named_endpoints.items()
            )
        )

        log.info(
            "HF_ENDPOINT_ONLY_NAMED=%s",
            selected[0]
        )

        return selected

    # --------------------------------------------------------
    # 4. Multiple endpoints but no clear match
    # --------------------------------------------------------

    available = list(
        named_endpoints.keys()
    )

    raise RuntimeError(
        "Could not automatically choose a Hugging Face "
        f"video endpoint. Available endpoints: {available}"
    )


# ============================================================
# NORMALIZE LABEL
# ============================================================

def normalize_label(
    value
):

    if value is None:
        return ""

    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value).lower()
    ).strip("_")


# ============================================================
# PARAMETER VALUE BUILDER
# ============================================================

def choose_value_from_parameter(
    parameter,
    prompt,
    negative_prompt
):

    if not isinstance(
        parameter,
        dict
    ):

        return ""

    label = " ".join(
        [
            str(
                parameter.get(
                    "label",
                    ""
                )
            ),
            str(
                parameter.get(
                    "name",
                    ""
                )
            ),
            str(
                parameter.get(
                    "parameter_name",
                    ""
                )
            ),
            str(
                parameter.get(
                    "display_name",
                    ""
                )
            )
        ]
    )

    normalized = normalize_label(
        label
    )

    # --------------------------------------------------------
    # NEGATIVE PROMPT
    # --------------------------------------------------------

    if (
        "negative" in normalized
        or "negative_prompt" in normalized
    ):

        return negative_prompt

    # --------------------------------------------------------
    # PROMPT
    # --------------------------------------------------------

    if (
        "prompt" in normalized
        or "description" in normalized
        or normalized in (
            "text",
            "input"
        )
    ):

        return prompt

    # --------------------------------------------------------
    # MODEL
    # --------------------------------------------------------

    if "model" in normalized:

        default = parameter.get(
            "default"
        )

        if default not in (
            None,
            "",
            []
        ):

            return default

        choices = parameter.get(
            "choices"
        )

        if (
            isinstance(
                choices,
                list
            )
            and choices
        ):

            return choices[0]

        return (
            "Wan 2.1 T2V 1.3B"
        )

    # --------------------------------------------------------
    # DURATION
    # --------------------------------------------------------

    if (
        "duration" in normalized
        or "seconds" in normalized
    ):

        return SHOT_DURATION

    # --------------------------------------------------------
    # FPS
    # --------------------------------------------------------

    if (
        normalized == "fps"
        or "frame_rate" in normalized
        or "framerate" in normalized
    ):

        return 16

    # --------------------------------------------------------
    # WIDTH
    # --------------------------------------------------------

    if "width" in normalized:

        return 480

    # --------------------------------------------------------
    # HEIGHT
    # --------------------------------------------------------

    if "height" in normalized:

        return 832

    # --------------------------------------------------------
    # SEED
    # --------------------------------------------------------

    if "seed" in normalized:

        default = parameter.get(
            "default"
        )

        if default is not None:
            return default

        return -1

    # --------------------------------------------------------
    # STEPS
    # --------------------------------------------------------

    if (
        "step" in normalized
        or "inference" in normalized
    ):

        default = parameter.get(
            "default"
        )

        if default is not None:
            return default

        return 20

    # --------------------------------------------------------
    # GUIDANCE / CFG
    # --------------------------------------------------------

    if (
        "cfg" in normalized
        or "guidance" in normalized
        or "scale" in normalized
    ):

        default = parameter.get(
            "default"
        )

        if default is not None:
            return default

        return 5.0

    # --------------------------------------------------------
    # FRAMES
    # --------------------------------------------------------

    if (
        "frame" in normalized
        or "frames" in normalized
    ):

        default = parameter.get(
            "default"
        )

        if default is not None:
            return default

        return 81

    # --------------------------------------------------------
    # CHECKBOX
    # --------------------------------------------------------

    component = str(
        parameter.get(
            "component",
            ""
        )
    ).lower()

    if (
        component == "checkbox"
        or "checkbox" in normalized
    ):

        return parameter.get(
            "default",
            False
        )

    # --------------------------------------------------------
    # DEFAULT
    # --------------------------------------------------------

    if "default" in parameter:

        return parameter[
            "default"
        ]

    # --------------------------------------------------------
    # CHOICES
    # --------------------------------------------------------

    choices = parameter.get(
        "choices"
    )

    if (
        isinstance(
            choices,
            list
        )
        and choices
    ):

        return choices[0]

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    return ""


# ============================================================
# BUILD API ARGUMENTS
# ============================================================

def build_generate_arguments(
    endpoint_info,
    prompt,
    negative_prompt
):

    if not isinstance(
        endpoint_info,
        dict
    ):

        raise RuntimeError(
            "Invalid Hugging Face endpoint information."
        )

    parameters = (
        endpoint_info.get(
            "parameters",
            []
        )
    )

    if not isinstance(
        parameters,
        list
    ):

        parameters = []

    log.info(
        "HF_PARAMETER_COUNT=%s",
        len(parameters)
    )

    if not parameters:

        raise RuntimeError(
            "Hugging Face endpoint has no parameters."
        )

    args = []

    for index, parameter in enumerate(
        parameters,
        start=1
    ):

        value = choose_value_from_parameter(
            parameter,
            prompt,
            negative_prompt
        )

        label = parameter.get(
            "label",
            parameter.get(
                "parameter_name",
                f"parameter_{index}"
            )
        )

        log.info(
            "HF_PARAMETER_%s=%s",
            index,
            label
        )

        args.append(
            value
        )

    return args


# ============================================================
# FIND VIDEO RESULT
# ============================================================

def find_video_value(
    value
):

    if value is None:
        return None

    if isinstance(
        value,
        str
    ):

        lower = value.lower()

        if lower.startswith(
            (
                "http://",
                "https://"
            )
        ):

            return value

        if lower.endswith(
            (
                ".mp4",
                ".webm",
                ".mov",
                ".avi",
                ".mkv"
            )
        ):

            return value

        return None

    if isinstance(
        value,
        dict
    ):

        for key in (
            "video",
            "path",
            "url",
            "value",
            "file",
            "name"
        ):

            if key in value:

                result = (
                    find_video_value(
                        value[key]
                    )
                )

                if result:
                    return result

        for item in value.values():

            result = (
                find_video_value(
                    item
                )
            )

            if result:
                return result

        return None

    if isinstance(
        value,
        (list, tuple)
    ):

        for item in value:

            result = (
                find_video_value(
                    item
                )
            )

            if result:
                return result

    # Handle Gradio FileData-like objects
    for attribute in (
        "path",
        "url",
        "name"
    ):

        try:

            candidate = getattr(
                value,
                attribute,
                None
            )

            if candidate:

                result = (
                    find_video_value(
                        candidate
                    )
                )

                if result:
                    return result

        except Exception:
            pass

    return None


# ============================================================
# DOWNLOAD VIDEO RESULT
# ============================================================

def download_video_result(
    result,
    output_path
):

    video_value = (
        find_video_value(
            result
        )
    )

    if not video_value:

        raise RuntimeError(
            "Hugging Face returned a result, "
            "but no video file was found."
        )

    log.info(
        "HF_VIDEO_RESULT_RECEIVED=True"
    )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    if (
        isinstance(
            video_value,
            str
        )
        and video_value.startswith(
            (
                "http://",
                "https://"
            )
        )
    ):

        response = requests.get(
            video_value,
            timeout=600,
            stream=True
        )

        response.raise_for_status()

        with open(
            output_path,
            "wb"
        ) as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    f.write(chunk)

        return output_path

    # --------------------------------------------------------
    # LOCAL FILE
    # --------------------------------------------------------

    source = Path(
        str(video_value)
    )

    if not source.exists():

        raise RuntimeError(
            "Hugging Face returned a video path "
            f"that does not exist: {source}"
        )

    shutil.copyfile(
        source,
        output_path
    )

    return output_path


# ============================================================
# AI VIDEO GENERATOR
# ============================================================

def generate_ai_video(
    prompt,
    output_path,
    duration=SHOT_DURATION
):

    final_prompt = f"""
{prompt}

Professional cinematic live-action video.

Vertical social media composition.

9:16 framing.

Realistic human motion.

Natural body movement.

Realistic physics.

Cinematic lighting.

Shallow depth of field.

Film-quality camera movement.

High detail.

No text.

No subtitles.

No watermark.

No logo.

No distorted hands.

No duplicated people.

No morphing faces.

Maintain consistent character appearance.
"""

    negative_prompt = """
text, subtitles, captions, watermark, logo,
distorted face, deformed face, duplicate person,
extra limbs, extra fingers, bad hands,
flickering, morphing, unnatural motion,
cartoon, anime, illustration, low quality,
blurry, static image
"""

    log.info(
        "HF_VIDEO_GENERATION_START"
    )

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    client = get_hf_client()

    # --------------------------------------------------------
    # READ API
    # --------------------------------------------------------

    api_info = get_api_schema(
        client
    )

    # --------------------------------------------------------
    # RESOLVE ENDPOINT
    # --------------------------------------------------------

    endpoint_name, endpoint_info = (
        resolve_endpoint(
            api_info
        )
    )

    log.info(
        "HF_GENERATE_ENDPOINT=%s",
        endpoint_name
    )

    # --------------------------------------------------------
    # BUILD ARGUMENTS
    # --------------------------------------------------------

    args = build_generate_arguments(
        endpoint_info,
        final_prompt.strip(),
        negative_prompt.strip()
    )

    log.info(
        "HF_GENERATE_ARGUMENT_COUNT=%s",
        len(args)
    )

    # --------------------------------------------------------
    # GENERATION
    # --------------------------------------------------------

    last_error = None

    for attempt in range(
        1,
        3
    ):

        try:

            log.info(
                "HF_VIDEO_ATTEMPT=%s",
                attempt
            )

            result = client.predict(
                *args,
                api_name=endpoint_name
            )

            log.info(
                "HF_GENERATE_RESULT_RECEIVED=True"
            )

            download_video_result(
                result,
                output_path
            )

            if not output_path.exists():

                raise RuntimeError(
                    "Hugging Face video was not created."
                )

            size = (
                output_path.stat().st_size
            )

            log.info(
                "HF_VIDEO_FILE_SIZE=%s",
                size
            )

            if size < 50_000:

                raise RuntimeError(
                    "Generated video is suspiciously small."
                )

            log.info(
                "HF_VIDEO_SUCCESS=True"
            )

            return output_path

        except Exception as e:

            last_error = e

            log.error(
                "HF_VIDEO_ATTEMPT_FAILED=%s",
                safe_error_text(e)
            )

            if attempt < 2:

                time.sleep(
                    5
                )

                try:

                    client = (
                        get_hf_client()
                    )

                except Exception:
                    pass

    raise RuntimeError(
        "Hugging Face video generation failed: "
        + safe_error_text(
            last_error
        )
    )


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
            "TTS file was not created."
        )

    if output_path.stat().st_size < 1000:

        raise RuntimeError(
            "TTS file is too small."
        )


# ============================================================
# NORMALIZE VIDEO
# ============================================================

def normalize_video(
    input_path,
    output_path
):

    vf = (
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,"
        f"fps={FPS},"
        "format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path)
    ]

    run_command(
        cmd,
        timeout=300
    )

    if not output_path.exists():

        raise RuntimeError(
            "Normalized video was not created."
        )


# ============================================================
# CONCAT VIDEOS
# ============================================================

def concat_videos(
    video_files,
    output_path,
    work_dir
):

    concat_file = (
        work_dir /
        "videos.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for video in video_files:

            safe_path = (
                str(video)
                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    cmd = [
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
    ]

    run_command(
        cmd,
        timeout=600
    )

    if not output_path.exists():

        raise RuntimeError(
            "Concatenated video was not created."
        )


# ============================================================
# ADD AUDIO
# ============================================================

def add_audio(
    video_path,
    audio_path,
    output_path
):

    cmd = [
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
        "-movflags",
        "+faststart",
        str(output_path)
    ]

    run_command(
        cmd,
        timeout=600
    )

    if not output_path.exists():

        raise RuntimeError(
            "Final video was not created."
        )


# ============================================================
# BUILD REEL
# ============================================================

def build_reel(
    story,
    job_dir
):

    log.info(
        "========================================"
    )

    log.info(
        "BUILD_REEL_START"
    )

    log.info(
        "========================================"
    )

    # --------------------------------------------------------
    # 1. STORYBOARD
    # --------------------------------------------------------

    storyboard = (
        create_storyboard(
            story
        )
    )

    narration = (
        storyboard["narration"]
    )

    shots = (
        storyboard["shots"]
    )

    log.info(
        "STORYBOARD_READY shots=%s",
        len(shots)
    )

    with open(
        job_dir / "storyboard.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            storyboard,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 2. TTS
    # --------------------------------------------------------

    log.info(
        "TTS_START"
    )

    audio_path = (
        job_dir /
        "narration.mp3"
    )

    create_tts(
        narration,
        audio_path
    )

    log.info(
        "TTS_READY"
    )

    # --------------------------------------------------------
    # 3. VIDEO SCENES
    # --------------------------------------------------------

    normalized_videos = []

    for index, shot in enumerate(
        shots,
        start=1
    ):

        log.info(
            "========================================"
        )

        log.info(
            "SCENE %s/%s",
            index,
            len(shots)
        )

        prompt = str(
            shot.get(
                "visual_prompt",
                ""
            )
        ).strip()

        if not prompt:

            raise RuntimeError(
                f"Scene {index} has empty visual prompt."
            )

        raw_video = (
            job_dir /
            f"scene_{index}_raw.mp4"
        )

        clean_video = (
            job_dir /
            f"scene_{index}.mp4"
        )

        generate_ai_video(
            prompt,
            raw_video,
            duration=SHOT_DURATION
        )

        normalize_video(
            raw_video,
            clean_video
        )

        normalized_videos.append(
            clean_video
        )

        try:

            raw_video.unlink(
                missing_ok=True
            )

        except Exception:
            pass

        gc.collect()

    # --------------------------------------------------------
    # 4. CONCAT
    # --------------------------------------------------------

    log.info(
        "CONCAT_START"
    )

    joined_video = (
        job_dir /
        "joined.mp4"
    )

    concat_videos(
        normalized_videos,
        joined_video,
        job_dir
    )

    # --------------------------------------------------------
    # 5. AUDIO
    # --------------------------------------------------------

    final_video = (
        job_dir /
        "final_reel.mp4"
    )

    log.info(
        "AUDIO_MUX_START"
    )

    add_audio(
        joined_video,
        audio_path,
        final_video
    )

    # --------------------------------------------------------
    # 6. FINAL CHECK
    # --------------------------------------------------------

    if not final_video.exists():

        raise RuntimeError(
            "Final Reel does not exist."
        )

    final_size = (
        final_video.stat().st_size
    )

    log.info(
        "FINAL_VIDEO_SIZE=%s",
        final_size
    )

    if final_size < 100_000:

        raise RuntimeError(
            "Final Reel is suspiciously small."
        )

    log.info(
        "========================================"
    )

    log.info(
        "BUILD_REEL_SUCCESS"
    )

    log.info(
        "========================================"
    )

    return final_video


# ============================================================
# TELEGRAM /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "🎬 أهلاً!\n\n"
        "ابعتلي قصة وأنا أحولها إلى Reel."
    )


# ============================================================
# TELEGRAM STORY HANDLER
# ============================================================

async def handle_story(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    story = (
        update.message.text or ""
    ).strip()

    if not story:
        return

    if len(story) < 30:

        await update.message.reply_text(
            "✍️ ابعت قصة أطول شوي حتى أقدر "
            "أبني عليها فيلم."
        )

        return

    # --------------------------------------------------------
    # ONE JOB AT A TIME
    # --------------------------------------------------------

    if not JOB_LOCK.acquire(
        blocking=False
    ):

        await update.message.reply_text(
            "⏳ في Reel ثاني قيد الإنشاء الآن.\n"
            "استنى يخلص وبعدها ابعت القصة."
        )

        return

    job_id = uuid.uuid4().hex[:12]

    job_dir = (
        BASE_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        log.info(
            "JOB_START=%s",
            job_id
        )

        await update.message.reply_text(
            "📝 استلمت القصة.\n\n"
            f"🎬 جاري إنشاء {SHOT_COUNT} "
            "مشهد تجريبي..."
        )

        final_video = (
            await asyncio.to_thread(
                build_reel,
                story,
                job_dir
            )
        )

        await update.message.reply_text(
            "🚀 خلص الفيديو! جاري إرساله..."
        )

        with open(
            final_video,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                supports_streaming=True,
                caption=(
                    "🎬 تم إنشاء الـ Reel بنجاح"
                )
            )

        log.info(
            "JOB_SUCCESS=%s",
            job_id
        )

    except Exception as e:

        log.error(
            "JOB_FAILED=%s",
            job_id
        )

        log.error(
            "ERROR_TYPE=%s",
            type(e).__name__
        )

        error_message = (
            safe_error_text(e)
        )

        log.error(
            "ERROR_MESSAGE=%s",
            error_message
        )

        log.error(
            "TRACEBACK_START"
        )

        log.error(
            traceback.format_exc()
        )

        log.error(
            "TRACEBACK_END"
        )

        message = (
            error_message
        )

        if len(message) > 1500:

            message = (
                message[:1500]
            )

        try:

            await update.message.reply_text(
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                f"{message}"
            )

        except Exception:
            pass

    finally:

        try:

            shutil.rmtree(
                job_dir,
                ignore_errors=True
            )

        except Exception:
            pass

        gc.collect()

        JOB_LOCK.release()

        log.info(
            "JOB_CLEANUP=%s",
            job_id
        )


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def telegram_error_handler(
    update,
    context
):

    error = context.error

    log.error(
        "TELEGRAM_HANDLER_ERROR=%s",
        safe_error_text(error)
    )

    log.error(
        traceback.format_exc()
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log.info(
        "========================================"
    )

    log.info(
        "ABOSARAJ STARTING"
    )

    log.info(
        "========================================"
    )

    # --------------------------------------------------------
    # ENV CHECK
    # --------------------------------------------------------

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing."
        )

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY is missing."
        )

    if not HF_TOKEN:

        raise RuntimeError(
            "HF_TOKEN is missing."
        )

    log.info(
        "BOT_TOKEN_PRESENT=True"
    )

    log.info(
        "GROQ_API_KEY_PRESENT=True"
    )

    log.info(
        "HF_TOKEN_PRESENT=True"
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    if HF_API_NAME:

        log.info(
            "HF_API_NAME_CONFIGURED=%s",
            HF_API_NAME
        )

    else:

        log.info(
            "HF_API_NAME_MODE=AUTO_DISCOVERY"
        )

    log.info(
        "SHOT_COUNT=%s",
        SHOT_COUNT
    )

    log.info(
        "SHOT_DURATION=%s",
        SHOT_DURATION
    )

    # --------------------------------------------------------
    # HEALTH SERVER
    # --------------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    log.info(
        "HEALTH_SERVER_STARTED"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    app = (
        Application
        .builder()
        .token(
            BOT_TOKEN
        )
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_story
        )
    )

    app.add_error_handler(
        telegram_error_handler
    )

    log.info(
        "TELEGRAM_HANDLERS_READY"
    )

    log.info(
        "BOT_START_POLLING"
    )

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        log.error(
            "FATAL_ERROR=%s",
            safe_error_text(e)
        )

        log.error(
            traceback.format_exc()
        )

        raise
