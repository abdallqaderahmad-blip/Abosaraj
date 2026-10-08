import os
import re
import json
import uuid
import shutil
import logging
import tempfile
import subprocess
import asyncio

import requests
import edge_tts

from flask import Flask, request, jsonify
from groq import Groq
from gradio_client import Client


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

HF_SPACE = os.getenv(
    "HF_SPACE",
    "numanajmal0/wan-video-api"
)

SHOT_COUNT = 2
SHOT_DURATION = 5

# Wan API limits from /hftest4
GEN_WIDTH = 576
GEN_HEIGHT = 832
GEN_FRAMES = 81
GEN_STEPS = 20
GEN_GUIDANCE = 5.0
GEN_SEED = 0

# Parameter 10 has range 0-2
GEN_PARAM_10 = 1.0

# Last parameter is string
GEN_PARAM_11 = ""

GEN_FPS = 16

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FINAL_FPS = 16

TTS_VOICE = "ar-SA-HamedNeural"

DEFAULT_CTA = "إذا عجبك الفيديو تابعنا للمزيد"


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


# =========================================================
# HELPERS
# =========================================================

def safe_error_text(error):
    text = str(error)

    for secret in [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN
    ]:
        if secret:
            text = text.replace(secret, "***")

    return text


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def run_cmd(cmd, cwd=None, timeout=None):

    log.info(
        "RUN_CMD=%s",
        " ".join(map(str, cmd))
    )

    try:

        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )

    except Exception as error:

        log.error(
            "COMMAND_EXCEPTION=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    if result.stdout:
        log.info(
            "COMMAND_STDOUT=%s",
            result.stdout[-4000:]
        )

    if result.stderr:
        log.info(
            "COMMAND_STDERR=%s",
            result.stderr[-4000:]
        )

    if result.returncode != 0:

        log.error(
            "COMMAND_ERROR=returncode=%s stderr=%s",
            result.returncode,
            result.stderr[-4000:]
        )

        raise RuntimeError(
            f"Command failed with code {result.returncode}"
        )

    return result


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    payload=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=payload,
            files=files,
            timeout=120
        )

        if not response.ok:

            log.error(
                "TELEGRAM_API_ERROR method=%s "
                "status=%s body=%s",
                method,
                response.status_code,
                response.text[:4000]
            )

            return None

        try:
            return response.json()
        except Exception:
            return None

    except Exception as error:

        log.error(
            "TELEGRAM_REQUEST_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return None


def send_message(
    chat_id,
    text
):

    return telegram_api(
        "sendMessage",
        payload={
            "chat_id": chat_id,
            "text": text
        }
    )


def send_video(
    chat_id,
    video_path,
    caption=None
):

    log.info(
        "SEND_VIDEO path=%s",
        video_path
    )

    payload = {
        "chat_id": chat_id
    }

    if caption:
        payload["caption"] = caption

    try:

        with open(
            video_path,
            "rb"
        ) as video_file:

            return telegram_api(
                "sendVideo",
                payload=payload,
                files={
                    "video": (
                        os.path.basename(video_path),
                        video_file,
                        "video/mp4"
                    )
                }
            )

    except Exception as error:

        log.error(
            "SEND_VIDEO_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return None


# =========================================================
# GROQ
# =========================================================

def create_storyboard(
    user_idea
):

    log.info(
        "PHASE=STORYBOARD_START"
    )

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing"
        )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    prompt = f"""
أنت كاتب سيناريوهات فيديوهات قصيرة سينمائية.

حوّل فكرة المستخدم التالية إلى فيديو قصير عمودي.

فكرة المستخدم:
{user_idea}

أخرج JSON فقط بدون Markdown.

الشكل المطلوب:

{{
  "title": "عنوان قصير",
  "hook": "جملة افتتاحية قوية",
  "narration": [
    "تعليق صوتي للمشهد الأول",
    "تعليق صوتي للمشهد الثاني"
  ],
  "cta": "دعوة قصيرة للمتابعة",
  "scenes": [
    {{
      "scene": 1,
      "prompt": "وصف بصري سينمائي باللغة الإنجليزية",
      "duration": 5
    }},
    {{
      "scene": 2,
      "prompt": "وصف بصري سينمائي باللغة الإنجليزية",
      "duration": 5
    }}
  ]
}}

الشروط:

- عدد المشاهد: {SHOT_COUNT}
- مدة كل مشهد حوالي {SHOT_DURATION} ثوانٍ.
- prompts باللغة الإنجليزية.
- أسلوب cinematic realistic.
- المشاهد مترابطة.
- لا تضع نصوصًا داخل الفيديو.
- لا شعارات.
"""

    try:

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional "
                        "cinematic short-video "
                        "storyboard writer."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=2500
        )

        raw = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        log.info(
            "GROQ_RAW_RESPONSE_START"
        )

        log.info(
            raw
        )

        log.info(
            "GROQ_RAW_RESPONSE_END"
        )

        raw = re.sub(
            r"^```(?:json)?",
            "",
            raw,
            flags=re.IGNORECASE
        )

        raw = re.sub(
            r"```$",
            "",
            raw
        ).strip()

        data = json.loads(raw)

        if not isinstance(
            data,
            dict
        ):
            raise ValueError(
                "Storyboard is not an object"
            )

        scenes = data.get(
            "scenes"
        )

        if not isinstance(
            scenes,
            list
        ):
            raise ValueError(
                "scenes is not a list"
            )

        if not scenes:
            raise ValueError(
                "No scenes"
            )

        data["scenes"] = scenes[
            :SHOT_COUNT
        ]

        narration = data.get(
            "narration",
            []
        )

        if not isinstance(
            narration,
            list
        ):
            narration = []

        while len(narration) < len(
            data["scenes"]
        ):
            narration.append("")

        data["narration"] = narration

        if not data.get(
            "cta"
        ):
            data["cta"] = DEFAULT_CTA

        log.info(
            "STORYBOARD_OK scenes=%s",
            len(data["scenes"])
        )

        return data

    except Exception as error:

        log.error(
            "STORYBOARD_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise


# =========================================================
# HUGGING FACE
# =========================================================

def create_hf_client():

    log.info(
        "HF_CLIENT_CREATE space=%s token=%s",
        HF_SPACE,
        bool(HF_TOKEN)
    )

    if HF_TOKEN:

        return Client(
            HF_SPACE,
            token=HF_TOKEN
        )

    return Client(
        HF_SPACE
    )


def get_hf_api_dict(
    client
):

    log.info(
        "HF_VIEW_API_START"
    )

    api = client.view_api(
        return_format="dict"
    )

    log.info(
        "HF_VIEW_API_DONE"
    )

    return api


def get_generate_endpoint(
    api
):

    named = api.get(
        "named_endpoints",
        {}
    )

    if "/generate" in named:
        return named["/generate"]

    for name, endpoint in named.items():

        if "generate" in name.lower():

            return endpoint

    raise RuntimeError(
        "Could not find /generate endpoint"
    )


# =========================================================
# IMPORTANT:
# WAN API PARAMETERS ARE UNNAMED
# =========================================================

def build_wan_arguments(
    prompt
):

    """
    /hftest4 showed the actual order:

    1  model
    2  prompt
    3  negative prompt
    4  width       320-832
    5  height      320-832
    6  frames      21-81
    7  steps       1-50
    8  guidance    0-20
    9  seed        integer
    10 parameter   0-2
    11 parameter   string
    """

    args = [

        # 1
        "wan-base",

        # 2
        prompt,

        # 3
        (
            "blurry, low quality, distorted, "
            "deformed, watermark, text, logo"
        ),

        # 4
        GEN_WIDTH,

        # 5
        GEN_HEIGHT,

        # 6
        GEN_FRAMES,

        # 7
        GEN_STEPS,

        # 8
        GEN_GUIDANCE,

        # 9
        GEN_SEED,

        # 10
        GEN_PARAM_10,

        # 11
        GEN_PARAM_11
    ]

    log.info(
        "WAN_ARGUMENTS:"
    )

    for index, value in enumerate(
        args,
        start=1
    ):

        if index == 2:
            log.info(
                "WAN_ARG_%s=%s",
                index,
                str(value)[:1000]
            )
        else:
            log.info(
                "WAN_ARG_%s=%r",
                index,
                value
            )

    return args


def extract_video_source(
    result
):

    log.info(
        "HF_RESULT_TYPE=%s",
        type(result).__name__
    )

    log.info(
        "HF_RESULT_REPR=%s",
        repr(result)[:6000]
    )

    if isinstance(
        result,
        str
    ):

        return result

    if isinstance(
        result,
        dict
    ):

        for key in [
            "video",
            "path",
            "url",
            "value",
            "data",
            "file"
        ]:

            if key in result:

                value = result[key]

                if isinstance(
                    value,
                    str
                ):
                    return value

                if isinstance(
                    value,
                    dict
                ):

                    nested = extract_video_source(
                        value
                    )

                    if nested:
                        return nested

        for value in result.values():

            if isinstance(
                value,
                str
            ):

                if (
                    value.startswith(
                        "http://"
                    )
                    or
                    value.startswith(
                        "https://"
                    )
                    or
                    os.path.exists(
                        value
                    )
                ):

                    return value

            if isinstance(
                value,
                dict
            ):

                nested = extract_video_source(
                    value
                )

                if nested:
                    return nested

    if isinstance(
        result,
        (list, tuple)
    ):

        for item in result:

            source = extract_video_source(
                item
            )

            if source:
                return source

    return None


def download_url(
    url,
    destination
):

    log.info(
        "HF_DOWNLOAD_START"
    )

    with requests.get(
        url,
        stream=True,
        timeout=300
    ) as response:

        response.raise_for_status()

        with open(
            destination,
            "wb"
        ) as output:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    output.write(
                        chunk
                    )

    log.info(
        "HF_DOWNLOAD_DONE size=%s",
        os.path.getsize(
            destination
        )
    )

    return destination


def generate_ai_video(
    prompt,
    output_dir
):

    log.info(
        "=" * 80
    )

    log.info(
        "PHASE=HF_VIDEO_GENERATION_START"
    )

    log.info(
        "HF_PROMPT=%s",
        prompt
    )

    log.info(
        "=" * 80
    )

    ensure_dir(
        output_dir
    )

    try:

        client = create_hf_client()

        api = get_hf_api_dict(
            client
        )

        endpoint = get_generate_endpoint(
            api
        )

        log.info(
            "HF_GENERATE_ENDPOINT_FOUND"
        )

        # -------------------------------------
        # DO NOT USE PARAMETER NAMES
        # They are None in this API.
        # Use exact positional order.
        # -------------------------------------

        args = build_wan_arguments(
            prompt
        )

        log.info(
            "HF_PREDICT_START"
        )

        result = client.predict(
            *args,
            api_name="/generate"
        )

        log.info(
            "HF_PREDICT_SUCCESS"
        )

        source = extract_video_source(
            result
        )

        if not source:

            raise RuntimeError(
                "HF returned no video source"
            )

        log.info(
            "HF_VIDEO_SOURCE=%s",
            str(source)[:2000]
        )

        destination = os.path.join(
            output_dir,
            f"scene_{uuid.uuid4().hex}.mp4"
        )

        if isinstance(
            source,
            str
        ) and (
            source.startswith(
                "http://"
            )
            or
            source.startswith(
                "https://"
            )
        ):

            download_url(
                source,
                destination
            )

        elif (
            isinstance(
                source,
                str
            )
            and
            os.path.exists(
                source
            )
        ):

            shutil.copy2(
                source,
                destination
            )

        else:

            raise RuntimeError(
                "Invalid HF video source: "
                + str(source)
            )

        if not os.path.exists(
            destination
        ):

            raise RuntimeError(
                "Video file was not created"
            )

        size = os.path.getsize(
            destination
        )

        if size < 1000:

            raise RuntimeError(
                f"Video file too small: {size}"
            )

        log.info(
            "PHASE=HF_VIDEO_GENERATION_DONE "
            "size=%s",
            size
        )

        return destination

    except Exception as error:

        log.error(
            "HF_PREDICT_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise RuntimeError(
            "HF_GENERATION_FAILED: "
            + safe_error_text(error)
        )


# =========================================================
# VIDEO NORMALIZE
# =========================================================

def normalize_video(
    input_path,
    output_path
):

    log.info(
        "PHASE=VIDEO_NORMALIZE_START"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,

        "-vf",
        (
            f"scale={FINAL_WIDTH}:"
            f"{FINAL_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={FINAL_WIDTH}:"
            f"{FINAL_HEIGHT}:"
            "(ow-iw)/2:"
            "(oh-ih)/2"
        ),

        "-r",
        str(FINAL_FPS),

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-pix_fmt",
        "yuv420p",

        output_path
    ]

    run_cmd(
        cmd,
        timeout=600
    )

    log.info(
        "PHASE=VIDEO_NORMALIZE_DONE"
    )

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_paths,
    output_path
):

    log.info(
        "PHASE=VIDEO_CONCAT_START count=%s",
        len(video_paths)
    )

    list_file = (
        output_path
        + ".txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in video_paths:

            absolute = os.path.abspath(
                path
            )

            f.write(
                "file '"
                + absolute.replace(
                    "'",
                    "'\\''"
                )
                + "'\n"
            )

    try:

        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                output_path
            ],
            timeout=900
        )

    finally:

        if os.path.exists(
            list_file
        ):
            os.remove(
                list_file
            )

    log.info(
        "PHASE=VIDEO_CONCAT_DONE"
    )

    return output_path


# =========================================================
# TTS
# =========================================================

async def _tts(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicate.save(
        output_path
    )


def generate_tts(
    text,
    output_path
):

    log.info(
        "PHASE=TTS_START text=%s",
        text
    )

    try:

        asyncio.run(
            _tts(
                text,
                output_path
            )
        )

    except Exception as error:

        log.error(
            "TTS_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    if not os.path.exists(
        output_path
    ):

        raise RuntimeError(
            "TTS file was not created"
        )

    log.info(
        "PHASE=TTS_DONE size=%s",
        os.path.getsize(
            output_path
        )
    )

    return output_path


# =========================================================
# DURATION
# =========================================================

def get_duration(
    path
):

    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path
        ]
    )

    return float(
        result.stdout.strip()
    )


# =========================================================
# AUDIO CONCAT
# =========================================================

def concat_audio(
    audio_paths,
    output_path
):

    list_file = (
        output_path
        + ".txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in audio_paths:

            absolute = os.path.abspath(
                path
            )

            f.write(
                "file '"
                + absolute.replace(
                    "'",
                    "'\\''"
                )
                + "'\n"
            )

    try:

        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                output_path
            ],
            timeout=600
        )

    finally:

        if os.path.exists(
            list_file
        ):
            os.remove(
                list_file
            )

    return output_path


# =========================================================
# MUX
# =========================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
):

    log.info(
        "PHASE=MUX_START"
    )

    run_cmd(
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

            "-movflags",
            "+faststart",

            output_path
        ],
        timeout=900
    )

    log.info(
        "PHASE=MUX_DONE"
    )

    return output_path


# =========================================================
# SRT
# =========================================================

def format_srt_time(
    seconds
):

    milliseconds = int(
        round(
            (
                seconds
                - int(seconds)
            ) * 1000
        )
    )

    total = int(
        seconds
    )

    hours = total // 3600

    minutes = (
        total % 3600
    ) // 60

    secs = total % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def create_srt(
    items,
    output_path
):

    current = 0.0

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        for index, item in enumerate(
            items,
            start=1
        ):

            text = str(
                item.get(
                    "text",
                    ""
                )
            ).strip()

            duration = float(
                item.get(
                    "duration",
                    0
                )
            )

            if not text:

                current += duration
                continue

            start = current
            end = (
                current
                + duration
            )

            f.write(
                f"{index}\n"
            )

            f.write(
                f"{format_srt_time(start)} --> "
                f"{format_srt_time(end)}\n"
            )

            f.write(
                text
                + "\n\n"
            )

            current = end

    return output_path


# =========================================================
# CAPTIONS
# =========================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):

    log.info(
        "PHASE=CAPTIONS_START"
    )

    subtitle_file = (
        srt_path
        .replace(
            "\\",
            "/"
        )
        .replace(
            ":",
            "\\:"
        )
        .replace(
            "'",
            "\\'"
        )
    )

    subtitle_filter = (
        "subtitles='"
        + subtitle_file
        + "'"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path,

            "-vf",
            subtitle_filter,

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "20",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            output_path
        ],
        timeout=900
    )

    log.info(
        "PHASE=CAPTIONS_DONE"
    )

    return output_path


# =========================================================
# CREATE REEL
# =========================================================

def create_reel(
    user_idea
):

    work_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    log.info(
        "=" * 80
    )

    log.info(
        "PHASE=CREATE_REEL_START"
    )

    log.info(
        "WORK_DIR=%s",
        work_dir
    )

    try:

        # -----------------------------------------
        # STORYBOARD
        # -----------------------------------------

        storyboard = create_storyboard(
            user_idea
        )

        scenes = storyboard[
            "scenes"
        ]

        narration = storyboard.get(
            "narration",
            []
        )

        cta = storyboard.get(
            "cta",
            DEFAULT_CTA
        )

        # -----------------------------------------
        # VIDEO
        # -----------------------------------------

        raw_dir = ensure_dir(
            os.path.join(
                work_dir,
                "raw"
            )
        )

        normalized_dir = ensure_dir(
            os.path.join(
                work_dir,
                "normalized"
            )
        )

        normalized_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            log.info(
                "SCENE_%s_START",
                index
            )

            prompt = scene.get(
                "prompt",
                ""
            )

            if not prompt:
                raise RuntimeError(
                    f"Scene {index} prompt is empty"
                )

            raw_video = generate_ai_video(
                prompt,
                raw_dir
            )

            normalized = os.path.join(
                normalized_dir,
                f"scene_{index}.mp4"
            )

            normalize_video(
                raw_video,
                normalized
            )

            normalized_videos.append(
                normalized
            )

        # -----------------------------------------
        # VIDEO CONCAT
        # -----------------------------------------

        concat_video = os.path.join(
            work_dir,
            "video_concat.mp4"
        )

        concat_videos(
            normalized_videos,
            concat_video
        )

        # -----------------------------------------
        # TTS
        # -----------------------------------------

        audio_dir = ensure_dir(
            os.path.join(
                work_dir,
                "audio"
            )
        )

        audio_files = []
        subtitle_items = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            if (
                index - 1
                < len(narration)
            ):

                narration_text = str(
                    narration[
                        index - 1
                    ]
                ).strip()

            else:

                narration_text = ""

            if not narration_text:
                narration_text = " "

            audio_path = os.path.join(
                audio_dir,
                f"voice_{index}.mp3"
            )

            generate_tts(
                narration_text,
                audio_path
            )

            video_duration = get_duration(
                normalized_videos[
                    index - 1
                ]
            )

            audio_files.append(
                audio_path
            )

            subtitle_items.append(
                {
                    "text": narration_text,
                    "duration": video_duration
                }
            )

        # -----------------------------------------
        # CTA
        # -----------------------------------------

        if cta:

            cta = str(
                cta
            ).strip()

            if cta:

                cta_audio = os.path.join(
                    audio_dir,
                    "cta.mp3"
                )

                generate_tts(
                    cta,
                    cta_audio
                )

                cta_duration = get_duration(
                    cta_audio
                )

                audio_files.append(
                    cta_audio
                )

                subtitle_items.append(
                    {
                        "text": cta,
                        "duration": cta_duration
                    }
                )

        # -----------------------------------------
        # AUDIO CONCAT
        # -----------------------------------------

        audio_concat = os.path.join(
            work_dir,
            "audio.m4a"
        )

        concat_audio(
            audio_files,
            audio_concat
        )

        # -----------------------------------------
        # MUX
        # -----------------------------------------

        muxed = os.path.join(
            work_dir,
            "muxed.mp4"
        )

        mux_audio(
            concat_video,
            audio_concat,
            muxed
        )

        # -----------------------------------------
        # SRT
        # -----------------------------------------

        srt = os.path.join(
            work_dir,
            "captions.srt"
        )

        create_srt(
            subtitle_items,
            srt
        )

        # -----------------------------------------
        # FINAL
        # -----------------------------------------

        final = os.path.join(
            work_dir,
            "final.mp4"
        )

        burn_captions(
            muxed,
            srt,
            final
        )

        if not os.path.exists(
            final
        ):

            raise RuntimeError(
                "Final MP4 missing"
            )

        size = os.path.getsize(
            final
        )

        log.info(
            "CREATE_REEL_FINAL_SIZE=%s",
            size
        )

        persistent = os.path.join(
            tempfile.gettempdir(),
            "abosaraj_"
            + uuid.uuid4().hex
            + ".mp4"
        )

        shutil.copy2(
            final,
            persistent
        )

        log.info(
            "PHASE=CREATE_REEL_DONE"
        )

        return persistent

    except Exception as error:

        log.error(
            "CREATE_REEL_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# HF TEST 5
# =========================================================

def run_hf_test():

    test_dir = tempfile.mkdtemp(
        prefix="hf_test_"
    )

    prompt = (
        "A cinematic realistic robot standing alone "
        "in a dark futuristic laboratory, slowly looking "
        "toward the camera, subtle natural body movement, "
        "dramatic cinematic lighting, photorealistic "
        "moving video"
    )

    try:

        video = generate_ai_video(
            prompt,
            test_dir
        )

        duration = get_duration(
            video
        )

        size = os.path.getsize(
            video
        )

        persistent = os.path.join(
            tempfile.gettempdir(),
            "hf_test_"
            + uuid.uuid4().hex
            + ".mp4"
        )

        shutil.copy2(
            video,
            persistent
        )

        return (
            persistent,
            duration,
            size
        )

    finally:

        shutil.rmtree(
            test_dir,
            ignore_errors=True
        )


# =========================================================
# TELEGRAM PROCESSOR
# =========================================================

def process_message(
    update
):

    if not update:
        return

    message = update.get(
        "message"
    )

    if not message:
        return

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    ).strip()

    if not chat_id or not text:
        return

    log.info(
        "TELEGRAM_MESSAGE chat_id=%s text=%s",
        chat_id,
        text
    )

    # -----------------------------------------
    # START
    # -----------------------------------------

    if text == "/start":

        send_message(
            chat_id,
            "👋 أهلاً بك.\n\n"
            "أرسل فكرة فيديو وسأحولها "
            "إلى فيديو قصير."
        )

        return

    # -----------------------------------------
    # TEST 3
    # -----------------------------------------

    if text == "/hftest3":

        send_message(
            chat_id,
            "🔎 أفحص Hugging Face..."
        )

        try:

            client = create_hf_client()

            api = get_hf_api_dict(
                client
            )

            endpoints = list(
                api.get(
                    "named_endpoints",
                    {}
                ).keys()
            )

            send_message(
                chat_id,
                "✅ HF API يعمل.\n\n"
                + "\n".join(
                    endpoints[:30]
                )
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HF TEST 3 فشل:\n\n"
                + safe_error_text(error)
            )

        return

    # -----------------------------------------
    # TEST 4
    # -----------------------------------------

    if text == "/hftest4":

        send_message(
            chat_id,
            "🔎 أقرأ باراميترات Wan..."
        )

        try:

            client = create_hf_client()

            api = get_hf_api_dict(
                client
            )

            endpoint = get_generate_endpoint(
                api
            )

            params = endpoint.get(
                "parameters",
                []
            )

            lines = []

            for index, p in enumerate(
                params,
                start=1
            ):

                lines.append(
                    f"{index}. "
                    f"name={p.get('name')} "
                    f"default={p.get('default')} "
                    f"type={p.get('type')} "
                    f"choices={p.get('choices')}"
                )

            send_message(
                chat_id,
                "✅ Generate parameters:\n\n"
                + "\n".join(lines)
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HF TEST 4 فشل:\n\n"
                + safe_error_text(error)
            )

        return

    # -----------------------------------------
    # TEST 5
    # -----------------------------------------

    if text == "/hftest5":

        send_message(
            chat_id,
            "🎥 بدأت اختبار Wan مباشر...\n\n"
            "هذه المرة أستخدم ترتيب "
            "الـ API الحقيقي الذي ظهر عندنا.\n"
            "اصبر شوي."
        )

        video = None

        try:

            video, duration, size = run_hf_test()

            send_message(
                chat_id,
                "✅✅ Wan اشتغل!\n\n"
                f"⏱️ المدة: {duration:.2f} ثانية\n"
                f"📦 الحجم: "
                f"{size / 1024 / 1024:.2f} MB\n\n"
                "🎬 سأرسل لك فيديو الاختبار الآن."
            )

            send_video(
                chat_id,
                video,
                caption="🎥 Wan Test — SUCCESS"
            )

        except Exception as error:

            log.error(
                "HF_TEST_ERROR=%s",
                safe_error_text(error),
                exc_info=True
            )

            send_message(
                chat_id,
                "❌ اختبار Wan فشل.\n\n"
                + safe_error_text(error)
            )

        finally:

            if video and os.path.exists(
                video
            ):

                try:
                    os.remove(
                        video
                    )
                except Exception:
                    pass

        return

    # -----------------------------------------
    # NORMAL VIDEO
    # -----------------------------------------

    send_message(
        chat_id,
        "🎬 وصلت الفكرة.\n"
        "🧠 بناء القصة والمشاهد..."
    )

    try:

        send_message(
            chat_id,
            "🎥 توليد الفيديو...\n"
            "⏳ اصبر شوي."
        )

        final_video = create_reel(
            text
        )

        send_message(
            chat_id,
            "🎙️ تجهيز الصوت...\n"
            "📝 تجهيز الكابشن..."
        )

        result = send_video(
            chat_id,
            final_video,
            caption="🎬 تم إنشاء الفيديو"
        )

        if not result:

            raise RuntimeError(
                "Telegram failed to send video"
            )

        log.info(
            "FINAL_VIDEO_SENT"
        )

        try:
            os.remove(
                final_video
            )
        except Exception:
            pass

    except Exception as error:

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        send_message(
            chat_id,
            "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
            "تم تسجيل الخطأ في Render Logs."
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    try:

        update = request.get_json(
            silent=True
        )

        log.info(
            "TELEGRAM_WEBHOOK_UPDATE_RECEIVED"
        )

        process_message(
            update
        )

        return jsonify(
            {
                "ok": True
            }
        )

    except Exception as error:

        log.error(
            "WEBHOOK_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return jsonify(
            {
                "ok": False
            }
        ), 200


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify(
        {
            "status": "ok",
            "service": "abosaraj",
            "hf_space": HF_SPACE,
            "model": GROQ_MODEL
        }
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify(
        {
            "status": "healthy"
        }
    )


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    public_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if not public_url:

        log.warning(
            "RENDER_EXTERNAL_URL_NOT_FOUND"
        )

        return

    webhook_url = (
        public_url.rstrip("/")
        + "/telegram/webhook"
    )

    log.info(
        "SETTING_WEBHOOK=%s",
        webhook_url
    )

    result = telegram_api(
        "setWebhook",
        payload={
            "url": webhook_url
        }
    )

    log.info(
        "SET_WEBHOOK_RESULT=%s",
        result
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log.info(
        "=" * 80
    )

    log.info(
        "ABOSARAJ BOT STARTING"
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    log.info(
        "GROQ_MODEL=%s",
        GROQ_MODEL
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
        "GEN_GUIDANCE=%s",
        GEN_GUIDANCE
    )

    log.info(
        "=" * 80
    )

    try:

        setup_webhook()

    except Exception as error:

        log.error(
            "WEBHOOK_SETUP_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

    app.run(
        host="0.0.0.0",
        port=PORT
    )
