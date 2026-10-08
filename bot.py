import os
import json
import time
import asyncio
import shutil
import tempfile
import threading
import subprocess
import mimetypes
from pathlib import Path

import requests
import edge_tts
from flask import Flask, request
from groq import Groq


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "")

PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL", ""
).rstrip("/")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)


def envbool(key, default):
    return os.getenv(
        key,
        default
    ).lower() in (
        "1",
        "true",
        "yes",
        "on"
    )


# =========================================================
# PRODUCTION SETTINGS
# =========================================================

# IMPORTANT:
# Real generation is ON by default.
#
# If you ever want a free local pipeline test:
# TEST_MODE=true
#
TEST_MODE = envbool(
    "TEST_MODE",
    "false"
)

LIPSYNC_ENABLED = envbool(
    "LIPSYNC_ENABLED",
    "false"
)

LIPSYNC_MODE = os.getenv(
    "LIPSYNC_MODE",
    "face"
).lower()

DEFAULT_LIPSYNC_EMOTION = os.getenv(
    "LIPSYNC_EMOTION",
    "neutral"
)

MAX_LIPSYNC_SCENES = int(
    os.getenv(
        "MAX_LIPSYNC_SCENES",
        "1"
    )
)

PRODUCTION_SCENE_LIMIT = int(
    os.getenv(
        "PRODUCTION_SCENE_LIMIT",
        "0"
    )
)


# =========================================================
# SOUND
# =========================================================

SOUND_DESIGN_ENABLED = envbool(
    "SOUND_DESIGN_ENABLED",
    "true"
)

MMAUDIO_STEPS = int(
    os.getenv(
        "MMAUDIO_STEPS",
        "25"
    )
)

MMAUDIO_GUIDANCE = float(
    os.getenv(
        "MMAUDIO_GUIDANCE",
        "4.5"
    )
)

MUSIC_ENABLED = envbool(
    "MUSIC_ENABLED",
    "true"
)

MUSIC_VOLUME = float(
    os.getenv(
        "MUSIC_VOLUME",
        "0.12"
    )
)

SFX_VOLUME = float(
    os.getenv(
        "SFX_VOLUME",
        "0.62"
    )
)

AMBIENCE_VOLUME = float(
    os.getenv(
        "AMBIENCE_VOLUME",
        "0.14"
    )
)

VOICE_VOLUME = float(
    os.getenv(
        "VOICE_VOLUME",
        "1.0"
    )
)


# =========================================================
# VIDEO
# =========================================================

SHOT_COUNT = 4
SHOT_DURATION = 5
TOTAL_DURATION = SHOT_COUNT * SHOT_DURATION

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24


# =========================================================
# VOICES
# =========================================================

VOICE_CONFIG = {

    "male_lead": {
        "voice": "ar-SY-LaithNeural",
        "rate": "-10%",
        "pitch": "-3Hz"
    },

    "princess": {
        "voice": "ar-SA-ZariyahNeural",
        "rate": "-6%"
    },

    "king": {
        "voice": "ar-EG-ShakirNeural",
        "rate": "-8%",
        "pitch": "-4Hz"
    },

    "guard": {
        "voice": "ar-IQ-BasselNeural",
        "rate": "-2%",
        "pitch": "-1Hz"
    },

    "narrator": {
        "voice": "ar-SA-HamedNeural",
        "rate": "-8%",
        "pitch": "-2Hz"
    }
}


# =========================================================
# WAVESPEED MODELS
# =========================================================

WAVESPEED_BASE = (
    "https://api.wavespeed.ai/api/v3"
)

IMAGE_MODEL = (
    "wavespeed-ai/z-image/turbo"
)

VIDEO_MODEL = (
    "wavespeed-ai/wan-2.2/"
    "i2v-480p-ultra-fast"
)

LIPSYNC_MODEL = (
    "sync/react-1"
)

SFX_MODEL = (
    "wavespeed-ai/mmaudio-v2"
)

MUSIC_MODEL = (
    "wavespeed-ai/ace-step/"
    "prompt-to-audio"
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)

logging_lock = threading.Lock()

processing_chats = set()


# =========================================================
# LOG
# =========================================================

def log(message):

    with logging_lock:

        print(
            "[ABOSARAJ] "
            + time.strftime("%H:%M:%S")
            + " "
            + str(message),
            flush=True
        )


# =========================================================
# HTTP HELPERS
# =========================================================

def auth_headers():

    return {
        "Authorization":
            f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type":
            "application/json"
    }


def get_headers():

    return {
        "Authorization":
            f"Bearer {WAVESPEED_API_KEY}"
    }


# =========================================================
# WAVESPEED SUBMIT
# =========================================================

def wavespeed_submit(
    model,
    payload
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed blocked because "
            "TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:

        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    url = (
        f"{WAVESPEED_BASE}/"
        f"{model}"
    )

    log(
        f"WaveSpeed submit: {model}"
    )

    try:

        response = requests.post(
            url,
            headers=auth_headers(),
            json=payload,
            timeout=(10, 60)
        )

    except Exception as e:

        log(
            "WaveSpeed submit network error: "
            + repr(e)
        )

        raise

    if not response.ok:

        raise RuntimeError(
            "WaveSpeed HTTP error "
            f"{response.status_code}: "
            f"{response.text[:2000]}"
        )

    try:

        body = response.json()

    except Exception:

        raise RuntimeError(
            "WaveSpeed returned invalid JSON: "
            + response.text[:2000]
        )

    if body.get("code") != 200:

        raise RuntimeError(
            "WaveSpeed task submission failed: "
            + str(body)
        )

    data = body.get(
        "data",
        {}
    )

    task_id = data.get(
        "id"
    )

    if not task_id:

        raise RuntimeError(
            "WaveSpeed returned no task id: "
            + str(body)
        )

    log(
        f"WaveSpeed task created: {task_id}"
    )

    return task_id


# =========================================================
# WAVESPEED RESULT
# =========================================================

def wavespeed_wait(
    task_id,
    timeout=1200
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed polling blocked because "
            "TEST_MODE=true"
        )

    started = time.monotonic()

    poll_interval = 2.0

    url = (
        f"{WAVESPEED_BASE}/"
        f"predictions/"
        f"{task_id}/result"
    )

    terminal_failures = {
        "failed",
        "cancelled",
        "timeout",
        "deleted"
    }

    while True:

        if (
            time.monotonic()
            - started
            > timeout
        ):

            raise TimeoutError(
                f"WaveSpeed timeout: {task_id}"
            )

        try:

            response = requests.get(
                url,
                headers=get_headers(),
                timeout=(10, 30)
            )

        except requests.RequestException as e:

            log(
                "Result polling network error: "
                + repr(e)
            )

            time.sleep(
                min(
                    10,
                    poll_interval
                )
            )

            poll_interval = min(
                10,
                poll_interval + 1
            )

            continue

        if not response.ok:

            log(
                "Result polling HTTP "
                f"{response.status_code}: "
                f"{response.text[:1000]}"
            )

            time.sleep(
                min(
                    10,
                    poll_interval
                )
            )

            poll_interval = min(
                10,
                poll_interval + 1
            )

            continue

        try:

            body = response.json()

        except Exception:

            time.sleep(
                poll_interval
            )

            continue

        if body.get("code") != 200:

            raise RuntimeError(
                "WaveSpeed result error: "
                + str(body)
            )

        data = body.get(
            "data",
            body
        )

        status = str(
            data.get(
                "status",
                ""
            )
        ).lower()

        log(
            f"Task {task_id}: {status}"
        )

        if status == "completed":

            outputs = data.get(
                "outputs",
                []
            )

            if not outputs:

                raise RuntimeError(
                    "WaveSpeed completed "
                    "without outputs: "
                    + str(body)
                )

            return outputs[0]

        if status in terminal_failures:

            error = data.get(
                "error",
                ""
            )

            raise RuntimeError(
                f"WaveSpeed task {task_id} "
                f"ended with {status}: "
                f"{error or body}"
            )

        time.sleep(
            poll_interval
        )

        poll_interval = min(
            10,
            poll_interval + 1
        )


# =========================================================
# OUTPUT URL EXTRACTION
# =========================================================

def output_to_url(output):

    if isinstance(
        output,
        str
    ):

        return output

    if isinstance(
        output,
        dict
    ):

        for key in (
            "url",
            "audio_url",
            "video_url",
            "download_url"
        ):

            value = output.get(
                key
            )

            if value:

                return value

    raise RuntimeError(
        "Unsupported WaveSpeed output: "
        + repr(output)
    )


# =========================================================
# UPLOAD
# =========================================================

def upload_to_wavespeed(
    path
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed upload blocked because "
            "TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:

        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    path = Path(path)

    if not path.exists():

        raise FileNotFoundError(
            str(path)
        )

    size = path.stat().st_size

    if size <= 0:

        raise RuntimeError(
            f"Cannot upload empty file: {path}"
        )

    content_type = (
        mimetypes.guess_type(
            path.name
        )[0]
        or "application/octet-stream"
    )

    payload = {
        "filename": path.name,
        "size": size,
        "content_type": content_type
    }

    log(
        f"WaveSpeed upload ticket: "
        f"{path.name} "
        f"({size} bytes)"
    )

    response = requests.post(
        f"{WAVESPEED_BASE}/media/uploads",
        headers=auth_headers(),
        json=payload,
        timeout=(10, 60)
    )

    if not response.ok:

        raise RuntimeError(
            "Upload ticket failed "
            f"{response.status_code}: "
            f"{response.text[:2000]}"
        )

    body = response.json()

    if body.get("code") != 200:

        raise RuntimeError(
            "Upload ticket error: "
            + str(body)
        )

    data = body.get(
        "data",
        {}
    )

    upload = data.get(
        "upload",
        {}
    )

    upload_url = upload.get(
        "url"
    )

    upload_headers = upload.get(
        "headers",
        {}
    )

    download_url = data.get(
        "download_url"
    )

    if not upload_url or not download_url:

        raise RuntimeError(
            "Invalid upload ticket: "
            + str(body)
        )

    with path.open(
        "rb"
    ) as file:

        upload_response = requests.put(
            upload_url,
            headers=upload_headers,
            data=file,
            timeout=300
        )

    if not upload_response.ok:

        raise RuntimeError(
            "WaveSpeed media upload failed "
            f"{upload_response.status_code}: "
            f"{upload_response.text[:1000]}"
        )

    log(
        f"WaveSpeed upload complete: "
        f"{path.name}"
    )

    return download_url


# =========================================================
# DOWNLOAD
# =========================================================

def download_file(
    url,
    path
):

    if not url:

        raise RuntimeError(
            "Empty download URL."
        )

    path = Path(path)

    response = requests.get(
        url,
        timeout=300
    )

    if not response.ok:

        raise RuntimeError(
            "Download failed "
            f"{response.status_code}: "
            f"{url}"
        )

    path.write_bytes(
        response.content
    )

    if not path.exists():

        raise RuntimeError(
            f"Download did not create file: {path}"
        )

    if path.stat().st_size <= 0:

        raise RuntimeError(
            f"Downloaded file is empty: {path}"
        )

    return path


# =========================================================
# GROQ STORY
# =========================================================

def create_story(
    user_idea
):

    system = f"""
أنت كاتب سيناريو عربي سينمائي محترف
ومخرج ومشرف استمرارية ومصمم صوت.

الم
