import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
import hashlib
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


# =========================================================
# RENDER WEBHOOK CONFIG
# =========================================================

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
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
# TEST SETTINGS
# =========================================================

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


# =========================================================
# VIDEO SETTINGS
# =========================================================

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
# WORK DIRECTORY
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
# FLASK SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "ABOSARAJ OK", 200


@app.route("/health")
def health():
    return "OK", 200


# =========================================================
# TELEGRAM APPLICATION GLOBALS
# =========================================================

telegram_application = None
telegram_loop = None
telegram_ready = threading.Event()


# =========================================================
# SAFE ERROR
# =========================================================

def safe_error_text(error):

    text = str(error)

    secrets = [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
        BOT_WEBHOOK_SECRET
    ]

    for secret in secrets:

        if secret:
            text = text.replace(
                secret,
                "[REDACTED]"
            )

    text = re.sub(
        r"bot\d+:[A-Za-z0-9_-]+",
        "bot[REDACTED]",
        text,
        flags=re.IGNORECASE
    )

    return text


# =========================================================
# RUN COMMAND
# =========================================================

def run_command(command):

    visible_command = " ".join(
        str(x)
        for x in command
    )

    for secret in [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
        BOT_WEBHOOK_SECRET
    ]:

        if secret:
            visible_command = visible_command.replace(
                secret,
                "[REDACTED]"
            )

    log.info(
        "RUN_COMMAND=%s",
        visible_command
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            safe_error_text(
                result.stderr
            )
        )

    return result


# =========================================================
# TELEGRAM CHECK
# =========================================================

def check_telegram_connection():

    if not BOT_TOKEN:

        log.error(
            "BOT_TOKEN_MISSING"
        )

        return

    try:

        response = requests.get(
            "https://api.telegram.org/"
            f"bot{BOT_TOKEN}/getWebhookInfo",
            timeout=20
        )

        log.info(
            "TELEGRAM_API_HTTP_STATUS=%s",
            response.status_code
        )

        if response.status_code != 200:

            log.warning(
                "TELEGRAM_API_RESPONSE=%s",
                safe_error_text(
                    response.text
                )
            )

            return

        data = response.json()

        result = data.get(
            "result",
            {}
        )

        webhook_url = (
            result.get("url")
            or "<EMPTY>"
        )

        pending = result.get(
            "pending_update_count",
            0
        )

        ip = (
            result.get(
                "ip_address"
            )
            or "<NONE>"
        )

        last_error = (
            result.get(
                "last_error_message"
            )
            or "<NONE>"
        )

        log.info(
            "TELEGRAM_WEBHOOK url=%s pending=%s ip=%s last_error=%s",
            webhook_url,
            pending,
            ip,
            last_error
        )

    except Exception as e:

        log.error(
            "TELEGRAM_CONNECTION_CHECK_ERROR=%s",
            safe_error_text(e)
        )


# =========================================================
# GROQ CLIENT
# =========================================================

def get_groq_client():

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY is missing"
        )

    return Groq(
        api_key=GROQ_API_KEY
    )


# =========================================================
# STORYBOARD
# =========================================================

def generate_storyboard(story):

    client = get_groq_client()

    prompt = f"""
You are the cinematic director of a professional
TikTok and Instagram Reels content factory.

Convert the Arabic story below into exactly
{SHOT_COUNT} cinematic video scene(s).

Each scene should be approximately
{SHOT_DURATION} seconds.

The content should feel like realistic live-action
cinematic science fiction.

Rules:

- Strong visual storytelling.
- No slideshow feeling.
- Realistic humans.
- Realistic robots.
- Cinematic camera movement.
- Cinematic lighting.
- Realistic environments.
- Maintain character continuity.
- No subtitles inside the generated video.
- No logos.
- No watermark.
- No text inside the generated video.
- No UI screenshots.
- No cartoon/anime style.
- Build suspense.
- Make the final visual memorable.

The story is fictional unless explicitly stated otherwise.
Do not present fictional events as real news.

Return ONLY valid JSON.

Format:

[
  {{
    "scene": 1,
    "duration": {SHOT_DURATION},
    "prompt": "English cinematic video generation prompt"
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
                    "You are an expert cinematic "
                    "AI video director. "
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
        response
        .choices[0]
        .message
        .content
        .strip()
    )

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

    scenes = json.loads(
        content
    )

    if not isinstance(
        scenes,
        list
    ):

        raise RuntimeError(
            "Groq storyboard is not a list"
        )

    if not scenes:

        raise RuntimeError(
            "Groq returned no scenes"
        )

    return scenes


# =========================================================
# HUGGING FACE CLIENT
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


# =========================================================
# HF API SCHEMA
# =========================================================

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
# RESOLVE ENDPOINT
# =========================================================

def resolve_endpoint(
    client,
    info
):

    candidates = []

    named_endpoints = info.get(
        "named_endpoints",
        {}
    )

    if isinstance(
        named_endpoints,
        dict
    ):

        for name, endpoint in (
            named_endpoints.items()
        ):

            score = 0

            text = str(
                name
            ).lower()

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

    if not candidates:

        endpoints = info.get(
            "endpoints",
            []
        )

        if isinstance(
            endpoints,
            list
        ):

            for endpoint in endpoints:

                if not isinstance(
                    endpoint,
                    dict
                ):
                    continue

                name = endpoint.get(
                    "api_name",
                    ""
                )

                if not name:
                    continue

                score = 0

                text = str(
                    name
                ).lower()

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
            "Could not find Hugging Face endpoint."
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    score, name, endpoint = candidates[0]

    endpoint_name = (
        name
        if str(name).startswith("/")
        else "/" + str(name)
    )

    log.info(
        "HF_ENDPOINT_AUTO_SELECTED=%s score=%s",
        endpoint_name,
        score
    )

    return (
        endpoint_name,
        endpoint
    )


# =========================================================
# PARAMETER NAME
# =========================================================

def parameter_name(parameter):

    return str(
        parameter.get(
            "parameter_name",
            ""
        )
        or parameter.get(
            "name",
            ""
        )
        or parameter.get(
            "label",
            ""
        )
    ).strip()


# =========================================================
# PARAMETER CHOICES
# =========================================================

def get_choices(parameter):

    for key in [
        "enum",
        "choices",
        "values"
    ]:

        value = parameter.get(
            key
        )

        if isinstance(
            value,
            list
        ):

            return value

    return []


# =========================================================
# BUILD GENERATE ARGUMENTS
# =========================================================

def build_generate_arguments(
    endpoint_info,
    prompt
):

    parameters = endpoint_info.get(
        "parameters",
        []
    )

    if not isinstance(
        parameters,
        list
    ):

        parameters = []

    args = []

    for parameter in parameters:

        if not isinstance(
            parameter,
            dict
        ):

            continue

        name = parameter_name(
            parameter
        )

        name_lower = name.lower()

        choices = get_choices(
            parameter
        )

        # =================================================
        # MODEL KEY
        # =================================================

        if name_lower in (
            "model_key",
            "model",
            "model_name",
            "checkpoint",
            "checkpoint_name"
        ):

            if choices:

                if "wan-base" in choices:

                    value = "wan-base"

                else:

                    normal_choices = [
                        x
                        for x in choices
                        if "nsfw"
                        not in str(x).lower()
                    ]

                    if normal_choices:

                        value = normal_choices[0]

                    else:

                        value = choices[0]

            else:

                value = "wan-base"

        # =================================================
        # PROMPT
        # =================================================

        elif (
            "prompt" in name_lower
            and "negative"
            not in name_lower
        ):

            value = prompt

        # =================================================
        # NEGATIVE PROMPT
        # =================================================

        elif "negative" in name_lower:

            value = (
                "blurry, low quality, distorted, "
                "deformed, duplicate person, "
                "extra limbs, text, subtitles, "
                "logo, watermark, cartoon, anime"
            )

        # =================================================
        # WIDTH
        # =================================================

        elif "width" in name_lower:

            value = GEN_WIDTH

        # =================================================
        # HEIGHT
        # =================================================

        elif "height" in name_lower:

            value = GEN_HEIGHT

        # =================================================
        # FRAMES
        # =================================================

        elif (
            "num_frames" in name_lower
            or "number of frames" in name_lower
            or name_lower == "frames"
        ):

            value = GEN_FRAMES

        # =================================================
        # STEPS
        # =================================================

        elif (
            "steps" in name_lower
            or "inference_steps" in name_lower
            or "inference steps" in name_lower
        ):

            value = 20

        # =================================================
        # GUIDANCE
        # =================================================

        elif (
            "guidance" in name_lower
            or "guidance_scale" in name_lower
            or "cfg" in name_lower
        ):

            value = 5.0

        # =================================================
        # SEED
        # =================================================

        elif "seed" in name_lower:

            value = 0

        # =================================================
        # LORA SCALE
        # =================================================

        elif "lora_scale" in name_lower:

            value = None

        # =================================================
        # CUSTOM CHECKPOINT
        # =================================================

        elif (
            "custom_ckpt" in name_lower
            or "custom checkpoint" in name_lower
        ):

            value = None

        # =================================================
        # OTHER CHOICES
        # =================================================

        elif choices:

            value = choices[0]

        # =================================================
        # BOOLEAN
        # =================================================

        elif parameter.get(
            "type"
        ) == "boolean":

            value = False

        # =================================================
        # DEFAULT
        # =================================================

        else:

            minimum = parameter.get(
                "minimum"
            )

            maximum = parameter.get(
                "maximum"
            )

            if minimum is not None:

                value = minimum

            elif maximum is not None:

                value = maximum

            else:

                value = None

        log.info(
            "HF_PARAMETER name=%s value=%s",
            name,
            value
        )

        args.append(
            value
        )

    return args


# =========================================================
# FIND VIDEO VALUE
# =========================================================

def find_video_value(value):

    if value is None:
        return None

    if isinstance(
        value,
        str
    ):

        if (
            value.startswith("http://")
            or value.startswith("https://")
            or os.path.exists(value)
        ):

            return value

        return None

    if isinstance(
        value,
        dict
    ):

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

    if isinstance(
        value,
        (list, tuple)
    ):

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

def download_file(
    source,
    destination
):

    if source is None:

        raise RuntimeError(
            "Hugging Face returned empty video."
        )

    if isinstance(
        source,
        str
    ):

        if (
            source.startswith("http://")
            or source.startswith("https://")
        ):

            response = requests.get(
                source,
                timeout=600
            )

            response.raise_for_status()

            Path(
                destination
            ).write_bytes(
                response.content
            )

            return destination

        if os.path.exists(source):

            Path(
                destination
            ).write_bytes(
                Path(source).read_bytes()
            )

            return destination

    if hasattr(
        source,
        "path"
    ):

        path = source.path

        if (
            path
            and os.path.exists(path)
        ):

            Path(
                destination
            ).write_bytes(
                Path(path).read_bytes()
            )

            return destination

    raise RuntimeError(
        "Could not download HF video."
    )


# =========================================================
# GENERATE AI VIDEO
# =========================================================

def generate_ai_video(
    prompt,
    output_path
):

    client = get_hf_client()

    info = get_api_schema(
        client
    )

    endpoint_name, endpoint_info = (
        resolve_endpoint(
            client,
            info
        )
    )

    args = build_generate_arguments(
        endpoint_info,
        prompt
    )

    log.info(
        "HF_GENERATE_ENDPOINT=%s",
        endpoint_name
    )

    log.info(
        "HF_ARGUMENT_COUNT=%s",
        len(args)
    )

    log.info(
        "HF_GENERATE_START"
    )

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

    log.info(
        "HF_GENERATE_RESULT_RECEIVED"
    )

    video_value = find_video_value(
        result
    )

    if video_value is None:

        raise RuntimeError(
            "Hugging Face returned no video."
        )

    download_file(
        video_value,
        output_path
    )

    if not os.path.exists(
        output_path
    ):

        raise RuntimeError(
            "Generated video does not exist."
        )

    size = os.path.getsize(
        output_path
    )

    if size < 1000:

        raise RuntimeError(
            "Generated video file is too small."
        )

    log.info(
        "HF_VIDEO_READY path=%s size=%s",
        output_path,
        size
    )

    return output_path


# =========================================================
# NORMALIZE VIDEO
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

    concat_file = (
        BASE_DIR
        / f"concat_{uuid.uuid4().hex}.txt"
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
# EDGE TTS
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
# CREATE REEL
# =========================================================

async def create_reel(
    story
):

    job_id = uuid.uuid4().hex

    work_dir = (
        BASE_DIR
        / job_id
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

        scenes = generate_storyboard(
            story
        )

        log.info(
            "STORYBOARD_SCENES=%s",
            len(scenes)
        )

        generated_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            prompt = str(
                scene.get(
                    "prompt",
                    ""
                )
            ).strip()

            if not prompt:
                continue

            raw_video = (
                work_dir
                / f"scene_{index}_raw.mp4"
            )

            normalized_video = (
                work_dir
                / f"scene_{index}.mp4"
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
                str(
                    normalized_video
                )
            )

            log.info(
                "SCENE_DONE=%s",
                index
            )

        if not generated_videos:

            raise RuntimeError(
                "No video scenes generated."
            )

        combined_video = (
            work_dir
            / "combined.mp4"
        )

        if len(
            generated_videos
        ) == 1:

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

        audio_file = (
            work_dir
            / "narration.mp3"
        )

        await generate_tts(
            story,
            str(audio_file)
        )

        final_video = (
            work_dir
            / "final_reel.mp4"
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

        return str(
            final_video
        )

    except Exception as e:

        log.error(
            "VIDEO_JOB_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        raise


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
        "🤖 أهلاً! ابعتلي القصة، "
        "وأحولها إلى Reel."
    )


# =========================================================
# HF API TEST 3
# =========================================================

async def hf_test3_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    # =====================================================
    # ASCII ONLY VERSION MARKERS
    # =====================================================

    log.warning(
        "HFT_TEST3_START"
    )

    await update.message.reply_text(
        "🧪 فحص Hugging Face TEST3 بدأ..."
    )

    try:

        # =================================================
        # CREATE CLIENT
        # =================================================

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

        # =================================================
        # CLIENT DICT
        # =================================================

        try:

            client_dict = getattr(
                client,
                "__dict__",
                {}
            )

            log.warning(
                "HFT_TEST3_CLIENT_DICT_START"
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

            log.warning(
                "HFT_TEST3_CLIENT_DICT_END"
            )

        except Exception as e:

            log.error(
                "HFT_TEST3_CLIENT_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # =================================================
        # VIEW API RAW
        # =================================================

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

        # =================================================
        # VIEW API DICT
        # =================================================

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
                    list(
                        api_dict.keys()
                    )
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

        # =================================================
        # CLIENT ENDPOINTS
        # =================================================

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

        # =================================================
        # SPACE SETTINGS
        # =================================================

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

        # =================================================
        # FINISHED
        # =================================================

        log.warning(
            "HFT_TEST3_FINISHED"
        )

        await update.message.reply_text(
            "✅ HFT_TEST3 خلص.\n\n"
            "هسا افتح Render Logs وابحث عن:\n"
            "HFT_TEST3_START\n\n"
            "وابعتلي كل السطور من "
            "HFT_TEST3_START "
            "إلى "
            "HFT_TEST3_FINISHED"
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
# HANDLE MESSAGE
# =========================================================

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

    if len(story) > 12000:
        story = story[:12000]

    await update.message.reply_text(
        "🎬 وصلت القصة.\n"
        "🧠 بجهز المشهد...\n"
        "🤖 أرسلها لمحرك الفيديو..."
    )

    try:

        final_video = await create_reel(
            story
        )

        await update.message.reply_video(
            video=final_video,
            caption="🎬 تم تجهيز الفيديو.",
            supports_streaming=True
        )

    except Exception as e:

        error = safe_error_text(
            e
        )

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
# TELEGRAM ASYNC LOOP
# =========================================================

def telegram_loop_worker():

    global telegram_loop
    global telegram_application

    telegram_loop = asyncio.new_event_loop()

    asyncio.set_event_loop(
        telegram_loop
    )

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

    # =====================================================
    # HFT TEST 3
    # =====================================================

    telegram_application.add_handler(
        CommandHandler(
            "hftest3",
            hf_test3_command
        )
    )

    telegram_application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    telegram_application.add_error_handler(
        telegram_error_handler
    )

    async def initialize():

        log.info(
            "TELEGRAM_APPLICATION_INITIALIZING"
        )

        await telegram_application.initialize()

        await telegram_application.start()

        if not RENDER_EXTERNAL_URL:

            raise RuntimeError(
                "RENDER_EXTERNAL_URL is missing. "
                "This service must run as a Render Web Service."
            )

        webhook_url = (
            RENDER_EXTERNAL_URL.rstrip("/")
            + WEBHOOK_PATH
        )

        log.info(
            "TELEGRAM_SETTING_WEBHOOK url=%s",
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

    try:

        telegram_loop.run_until_complete(
            initialize()
        )

        log.info(
            "TELEGRAM_ASYNC_LOOP_READY"
        )

        telegram_loop.run_forever()

    except Exception as e:

        log.error(
            "TELEGRAM_LOOP_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

    finally:

        try:

            if telegram_application:

                telegram_loop.run_until_complete(
                    telegram_application.stop()
                )

                telegram_loop.run_until_complete(
                    telegram_application.shutdown()
                )

        except Exception as e:

            log.error(
                "TELEGRAM_SHUTDOWN_ERROR=%s",
                safe_error_text(e)
            )

        try:

            telegram_loop.close()

        except Exception:
            pass


# =========================================================
# TELEGRAM WEBHOOK ROUTE
# =========================================================

@app.route(
    WEBHOOK_PATH,
    methods=["POST"]
)
def telegram_webhook():

    global telegram_application
    global telegram_loop

    if not telegram_ready.is_set():

        log.warning(
            "TELEGRAM_WEBHOOK_RECEIVED_BEFORE_READY"
        )

        return "Service not ready", 503

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

        return "Forbidden", 403

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

        log.info(
            "TELEGRAM_WEBHOOK_UPDATE_ACCEPTED"
        )

        return "OK", 200

    except Exception as e:

        log.error(
            "TELEGRAM_WEBHOOK_ERROR=%s",
            safe_error_text(e),
            exc_info=True
        )

        return "Webhook error", 500


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

    log.info(
        "RENDER_EXTERNAL_URL=%s",
        RENDER_EXTERNAL_URL or "<MISSING>"
    )

    log.info(
        "WEBHOOK_PATH=%s",
        WEBHOOK_PATH
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    check_telegram_connection()

    telegram_thread = threading.Thread(
        target=telegram_loop_worker,
        daemon=True,
        name="telegram-loop"
    )

    telegram_thread.start()

    if not telegram_ready.wait(
        timeout=60
    ):

        raise RuntimeError(
            "Telegram webhook initialization timed out."
        )

    log.info(
        "TELEGRAM_WEBHOOK_READY"
    )

    log.info(
        "HEALTH_SERVER_STARTED port=%s",
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
# START
# =========================================================

if __name__ == "__main__":
    main()
