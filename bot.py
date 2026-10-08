import os
import re
import json
import uuid
import time
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

GEN_WIDTH = 576
GEN_HEIGHT = 832
GEN_FRAMES = 81
GEN_FPS = 16
GEN_STEPS = 20
GEN_GUIDANCE = 5.0
GEN_SEED = 0

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
# BASIC HELPERS
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
            text = text.replace(secret, "***")

    return text


def run_cmd(cmd, cwd=None, timeout=None):
    log.info("RUN_CMD=%s", " ".join(map(str, cmd)))

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


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(method, payload=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            data=payload,
            files=files,
            timeout=120
        )

        if not response.ok:
            log.error(
                "TELEGRAM_API_ERROR method=%s status=%s body=%s",
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


def send_message(chat_id, text):
    return telegram_api(
        "sendMessage",
        payload={
            "chat_id": chat_id,
            "text": text
        }
    )


def send_video(chat_id, video_path, caption=None):
    log.info(
        "TELEGRAM_SEND_VIDEO path=%s size=%s",
        video_path,
        os.path.getsize(video_path)
        if os.path.exists(video_path)
        else "MISSING"
    )

    payload = {
        "chat_id": chat_id
    }

    if caption:
        payload["caption"] = caption

    try:
        with open(video_path, "rb") as video_file:
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
# GROQ STORYBOARD
# =========================================================

def create_storyboard(user_idea):
    log.info("PHASE=STORYBOARD_START")
    log.info("USER_IDEA=%s", user_idea)

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing")

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""
أنت كاتب سيناريوهات فيديوهات قصيرة سينمائية.

حوّل فكرة المستخدم التالية إلى فيديو قصير عمودي.

فكرة المستخدم:
{user_idea}

المطلوب إخراج JSON فقط بدون Markdown وبدون أي كلام خارجه.

الشكل المطلوب حرفيًا:

{{
  "title": "عنوان قصير",
  "hook": "جملة افتتاحية قوية",
  "narration": [
    "جملة تعليق صوتي للمشهد الأول",
    "جملة تعليق صوتي للمشهد الثاني"
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
- مدة كل مشهد تقريبًا {SHOT_DURATION} ثوانٍ.
- الـ prompt الخاص بالفيديو يجب أن يكون باللغة الإنجليزية.
- اجعل المشاهد مترابطة بصريًا.
- اجعل الشخصيات والأماكن واضحة.
- لا تستخدم نصوصًا داخل الفيديو.
- لا تستخدم شعارات.
- اجعل الأسلوب cinematic realistic.
"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional cinematic "
                        "short-video storyboard writer."
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

        raw = response.choices[0].message.content.strip()

        log.info("GROQ_RAW_RESPONSE_START")
        log.info(raw)
        log.info("GROQ_RAW_RESPONSE_END")

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

        if not isinstance(data, dict):
            raise ValueError("Storyboard is not an object")

        scenes = data.get("scenes")

        if not isinstance(scenes, list):
            raise ValueError("Storyboard scenes is not a list")

        if not scenes:
            raise ValueError("Storyboard contains no scenes")

        data["scenes"] = scenes[:SHOT_COUNT]

        narration = data.get("narration", [])

        if not isinstance(narration, list):
            narration = []

        while len(narration) < len(data["scenes"]):
            narration.append("")

        data["narration"] = narration

        if not data.get("cta"):
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
# HUGGING FACE / GRADIO
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

    return Client(HF_SPACE)


def get_hf_api_dict(client):
    log.info("HF_VIEW_API_START")

    api = client.view_api(
        return_format="dict"
    )

    log.info("HF_VIEW_API_OK")

    return api


def get_generate_endpoint(api):
    named = api.get("named_endpoints", {})

    if "/generate" in named:
        return named["/generate"]

    for name, endpoint in named.items():
        if "generate" in name.lower():
            log.info(
                "HF_GENERATE_ENDPOINT_FALLBACK=%s",
                name
            )
            return endpoint

    raise RuntimeError(
        "Could not find /generate endpoint"
    )


def get_parameter_list(endpoint):
    parameters = endpoint.get("parameters", [])

    if not isinstance(parameters, list):
        return []

    return parameters


def build_generate_arguments(endpoint, prompt):
    parameters = get_parameter_list(endpoint)

    log.info(
        "HF_GENERATE_PARAMETER_COUNT=%s",
        len(parameters)
    )

    args = []

    for index, parameter in enumerate(parameters):
        name = str(parameter.get("name", "")).lower()

        component = parameter.get("component", {})
        default = parameter.get("default", None)

        choices = parameter.get("choices")

        log.info(
            "HF_PARAM index=%s name=%s default=%s choices=%s",
            index,
            name,
            default,
            choices
        )

        # -----------------------------------------
        # MODEL
        # -----------------------------------------

        if (
            "model" in name
            or "checkpoint" in name
            or "model_key" in name
        ):
            value = default

            if value is None:
                if isinstance(choices, list) and choices:
                    value = choices[0]
                else:
                    value = "wan-base"

            args.append(value)
            continue

        # -----------------------------------------
        # PROMPT
        # -----------------------------------------

        if (
            "prompt" in name
            and "negative" not in name
        ):
            args.append(prompt)
            continue

        # -----------------------------------------
        # NEGATIVE PROMPT
        # -----------------------------------------

        if "negative" in name:
            args.append(
                "blurry, low quality, distorted, "
                "deformed, watermark, text, logo"
            )
            continue

        # -----------------------------------------
        # WIDTH
        # -----------------------------------------

        if name in ("width", "video_width", "output_width"):
            args.append(GEN_WIDTH)
            continue

        # -----------------------------------------
        # HEIGHT
        # -----------------------------------------

        if name in ("height", "video_height", "output_height"):
            args.append(GEN_HEIGHT)
            continue

        # -----------------------------------------
        # FRAMES
        # -----------------------------------------

        if (
            "frames" in name
            or "num_frames" in name
            or "frame_count" in name
        ):
            args.append(GEN_FRAMES)
            continue

        # -----------------------------------------
        # FPS
        # -----------------------------------------

        if (
            name == "fps"
            or "frame_rate" in name
        ):
            args.append(GEN_FPS)
            continue

        # -----------------------------------------
        # STEPS
        # -----------------------------------------

        if (
            "steps" in name
            or "num_inference_steps" in name
        ):
            args.append(GEN_STEPS)
            continue

        # -----------------------------------------
        # GUIDANCE
        # -----------------------------------------

        if (
            "guidance" in name
            or "cfg" in name
        ):
            args.append(GEN_GUIDANCE)
            continue

        # -----------------------------------------
        # SEED
        # -----------------------------------------

        if "seed" in name:
            args.append(GEN_SEED)
            continue

        # -----------------------------------------
        # LORA
        # -----------------------------------------

        if "lora" in name:
            args.append(default)
            continue

        # -----------------------------------------
        # BOOLEAN
        # -----------------------------------------

        if (
            "enable" in name
            or "offload" in name
            or "cpu" in name
            or "high_res" in name
        ):
            if default is not None:
                args.append(default)
            else:
                args.append(False)
            continue

        # -----------------------------------------
        # CHOICES
        # -----------------------------------------

        if isinstance(choices, list) and choices:
            args.append(
                default
                if default is not None
                else choices[0]
            )
            continue

        # -----------------------------------------
        # DEFAULT
        # -----------------------------------------

        if default is not None:
            args.append(default)
            continue

        # -----------------------------------------
        # UNKNOWN PARAMETER
        # -----------------------------------------

        param_type = str(
            parameter.get("type", "")
        ).lower()

        if (
            "int" in param_type
            or "number" in param_type
        ):
            args.append(0)

        elif "float" in param_type:
            args.append(0.0)

        elif "bool" in param_type:
            args.append(False)

        else:
            args.append(None)

    log.info(
        "HF_ARGUMENTS_BUILT count=%s",
        len(args)
    )

    return args


def extract_video_source(result):
    """
    يحاول استخراج ملف الفيديو من أغلب
    الأشكال التي يمكن أن ترجعها Gradio.
    """

    log.info(
        "HF_RESULT_TYPE=%s",
        type(result).__name__
    )

    log.info(
        "HF_RESULT_REPR=%s",
        repr(result)[:6000]
    )

    # -----------------------------------------
    # STRING
    # -----------------------------------------

    if isinstance(result, str):
        return result

    # -----------------------------------------
    # DICT
    # -----------------------------------------

    if isinstance(result, dict):

        possible_keys = [
            "video",
            "path",
            "url",
            "value",
            "data",
            "file"
        ]

        for key in possible_keys:
            if key in result:
                value = result[key]

                if isinstance(value, str):
                    return value

                if isinstance(value, dict):
                    nested = extract_video_source(value)
                    if nested:
                        return nested

        # Search recursively
        for value in result.values():

            if isinstance(value, str):
                if (
                    value.startswith("http://")
                    or value.startswith("https://")
                    or os.path.exists(value)
                ):
                    return value

            if isinstance(value, dict):
                nested = extract_video_source(value)

                if nested:
                    return nested

    # -----------------------------------------
    # LIST / TUPLE
    # -----------------------------------------

    if isinstance(result, (list, tuple)):

        for item in result:

            source = extract_video_source(item)

            if source:
                return source

    return None


def download_url(url, destination):
    log.info(
        "HF_DOWNLOAD_START url=%s",
        url[:500]
    )

    with requests.get(
        url,
        stream=True,
        timeout=300
    ) as response:

        response.raise_for_status()

        with open(destination, "wb") as output:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    output.write(chunk)

    log.info(
        "HF_DOWNLOAD_DONE size=%s",
        os.path.getsize(destination)
    )

    return destination


def generate_ai_video(prompt, output_dir):
    log.info("=" * 70)
    log.info("PHASE=HF_VIDEO_GENERATION_START")
    log.info("HF_PROMPT=%s", prompt)
    log.info("=" * 70)

    ensure_dir(output_dir)

    client = None

    try:
        client = create_hf_client()

        api = get_hf_api_dict(client)

        endpoint = get_generate_endpoint(api)

        log.info(
            "HF_ENDPOINT=%s",
            endpoint
        )

        args = build_generate_arguments(
            endpoint,
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

        source = extract_video_source(result)

        if not source:
            raise RuntimeError(
                "HF returned no recognizable video source"
            )

        log.info(
            "HF_VIDEO_SOURCE=%s",
            str(source)[:2000]
        )

        destination = os.path.join(
            output_dir,
            f"scene_{uuid.uuid4().hex}.mp4"
        )

        # URL
        if isinstance(source, str) and (
            source.startswith("http://")
            or source.startswith("https://")
        ):
            download_url(
                source,
                destination
            )

        # Local path
        elif isinstance(source, str) and os.path.exists(source):

            shutil.copy2(
                source,
                destination
            )

        else:
            raise RuntimeError(
                f"HF returned invalid video source: {source}"
            )

        if not os.path.exists(destination):
            raise RuntimeError(
                "Generated video file does not exist"
            )

        size = os.path.getsize(destination)

        if size < 1000:
            raise RuntimeError(
                f"Generated video is too small: {size}"
            )

        log.info(
            "PHASE=HF_VIDEO_GENERATION_DONE size=%s path=%s",
            size,
            destination
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
# VIDEO NORMALIZATION
# =========================================================

def normalize_video(input_path, output_path):
    log.info(
        "PHASE=VIDEO_NORMALIZE_START input=%s",
        input_path
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,

        "-vf",
        (
            f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
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
        "PHASE=VIDEO_NORMALIZE_DONE output=%s",
        output_path
    )

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(video_paths, output_path):
    log.info(
        "PHASE=VIDEO_CONCAT_START count=%s",
        len(video_paths)
    )

    if not video_paths:
        raise RuntimeError(
            "No videos to concatenate"
        )

    list_file = output_path + ".txt"

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in video_paths:
            absolute = os.path.abspath(path)

            f.write(
                "file "
                + "'"
                + absolute.replace("'", "'\\''")
                + "'\n"
            )

    cmd = [
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
    ]

    try:
        run_cmd(
            cmd,
            timeout=900
        )
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)

    log.info(
        "PHASE=VIDEO_CONCAT_DONE output=%s",
        output_path
    )

    return output_path


# =========================================================
# TTS
# =========================================================

async def _generate_tts(text, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicate.save(output_path)


def generate_tts(text, output_path):
    log.info(
        "PHASE=TTS_START text=%s",
        text
    )

    if not text.strip():
        raise RuntimeError(
            "TTS text is empty"
        )

    try:
        asyncio.run(
            _generate_tts(
                text,
                output_path
            )
        )
    except RuntimeError as error:

        # في بعض البيئات asyncio loop موجود
        log.info(
            "TTS_ASYNCIO_RETRY"
        )

        loop = asyncio.new_event_loop()

        try:
            loop.run_until_complete(
                _generate_tts(
                    text,
                    output_path
                )
            )
        finally:
            loop.close()

    if not os.path.exists(output_path):
        raise RuntimeError(
            "TTS output file was not created"
        )

    size = os.path.getsize(output_path)

    if size < 100:
        raise RuntimeError(
            f"TTS output too small: {size}"
        )

    log.info(
        "PHASE=TTS_DONE size=%s",
        size
    )

    return output_path


# =========================================================
# FFPROBE DURATION
# =========================================================

def get_duration(path):
    result = run_cmd([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path
    ])

    value = result.stdout.strip()

    duration = float(value)

    log.info(
        "VIDEO_DURATION path=%s duration=%s",
        path,
        duration
    )

    return duration


# =========================================================
# AUDIO CONCAT
# =========================================================

def concat_audio(audio_paths, output_path):
    log.info(
        "PHASE=AUDIO_CONCAT_START count=%s",
        len(audio_paths)
    )

    if not audio_paths:
        raise RuntimeError(
            "No audio files"
        )

    list_file = output_path + ".txt"

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in audio_paths:

            absolute = os.path.abspath(path)

            f.write(
                "file "
                + "'"
                + absolute.replace("'", "'\\''")
                + "'\n"
            )

    cmd = [
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
    ]

    try:
        run_cmd(
            cmd,
            timeout=600
        )
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)

    log.info(
        "PHASE=AUDIO_CONCAT_DONE output=%s",
        output_path
    )

    return output_path


# =========================================================
# MUX AUDIO + VIDEO
# =========================================================

def mux_audio(video_path, audio_path, output_path):
    log.info(
        "PHASE=MUX_START"
    )

    cmd = [
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
    ]

    run_cmd(
        cmd,
        timeout=900
    )

    log.info(
        "PHASE=MUX_DONE output=%s",
        output_path
    )

    return output_path


# =========================================================
# ARABIC FONT
# =========================================================

def find_arabic_font():
    fonts = [
        "Noto Sans Arabic",
        "Noto Naskh Arabic",
        "Noto Sans",
        "DejaVu Sans"
    ]

    for font in fonts:

        try:

            result = subprocess.run(
                [
                    "fc-match",
                    "-f",
                    "%{file}",
                    font
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20
            )

            path = result.stdout.strip()

            if path and os.path.exists(path):
                log.info(
                    "FONT_FOUND=%s path=%s",
                    font,
                    path
                )
                return path

        except Exception:
            pass

    log.warning(
        "NO_ARABIC_FONT_FOUND"
    )

    return None


# =========================================================
# SRT
# =========================================================

def format_srt_time(seconds):
    milliseconds = int(
        round(
            (seconds - int(seconds)) * 1000
        )
    )

    total = int(seconds)

    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def create_srt(items, output_path):
    log.info(
        "PHASE=SRT_START count=%s",
        len(items)
    )

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
                item.get("text", "")
            ).strip()

            duration = float(
                item.get("duration", 0)
            )

            if not text:
                current += duration
                continue

            start = current
            end = current + duration

            f.write(
                f"{index}\n"
            )

            f.write(
                f"{format_srt_time(start)} --> "
                f"{format_srt_time(end)}\n"
            )

            f.write(
                text + "\n\n"
            )

            current = end

    log.info(
        "PHASE=SRT_DONE path=%s",
        output_path
    )

    return output_path


# =========================================================
# BURN CAPTIONS
# =========================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):
    log.info(
        "PHASE=CAPTIONS_START"
    )

    font_path = find_arabic_font()

    subtitle_file = (
        srt_path
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )

    subtitle_filter = (
        "subtitles='"
        + subtitle_file
        + "'"
    )

    cmd = [
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
    ]

    run_cmd(
        cmd,
        timeout=900
    )

    log.info(
        "PHASE=CAPTIONS_DONE output=%s",
        output_path
    )

    return output_path


# =========================================================
# CREATE REEL
# =========================================================

def create_reel(user_idea):
    work_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    log.info("=" * 80)
    log.info(
        "PHASE=CREATE_REEL_START work_dir=%s",
        work_dir
    )
    log.info("=" * 80)

    try:

        # -----------------------------------------
        # STORYBOARD
        # -----------------------------------------

        storyboard = create_storyboard(
            user_idea
        )

        scenes = storyboard["scenes"]

        narration = storyboard.get(
            "narration",
            []
        )

        cta = storyboard.get(
            "cta",
            DEFAULT_CTA
        )

        log.info(
            "REEL_SCENES=%s",
            len(scenes)
        )

        # -----------------------------------------
        # VIDEO GENERATION
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
                "=" * 70
            )

            log.info(
                "PHASE=SCENE_%s_START",
                index
            )

            prompt = scene.get(
                "prompt",
                ""
            )

            if not prompt:
                raise RuntimeError(
                    f"Scene {index} has empty prompt"
                )

            raw_video = generate_ai_video(
                prompt,
                raw_dir
            )

            normalized_video = os.path.join(
                normalized_dir,
                f"scene_{index}.mp4"
            )

            normalize_video(
                raw_video,
                normalized_video
            )

            normalized_videos.append(
                normalized_video
            )

            log.info(
                "PHASE=SCENE_%s_DONE",
                index
            )

        # -----------------------------------------
        # CONCAT VIDEO
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

            if index - 1 < len(narration):
                text = str(
                    narration[index - 1]
                ).strip()
            else:
                text = ""

            if not text:
                text = " "

            audio_path = os.path.join(
                audio_dir,
                f"voice_{index}.mp3"
            )

            generate_tts(
                text,
                audio_path
            )

            duration = get_duration(
                normalized_videos[index - 1]
            )

            audio_files.append(
                audio_path
            )

            subtitle_items.append(
                {
                    "text": text,
                    "duration": duration
                }
            )

        # -----------------------------------------
        # CTA
        # -----------------------------------------

        if cta:

            cta_text = str(
                cta
            ).strip()

            if cta_text:

                cta_audio = os.path.join(
                    audio_dir,
                    "cta.mp3"
                )

                generate_tts(
                    cta_text,
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
                        "text": cta_text,
                        "duration": cta_duration
                    }
                )

        # -----------------------------------------
        # CONCAT AUDIO
        # -----------------------------------------

        audio_concat = os.path.join(
            work_dir,
            "audio_concat.m4a"
        )

        concat_audio(
            audio_files,
            audio_concat
        )

        # -----------------------------------------
        # MUX
        # -----------------------------------------

        muxed_video = os.path.join(
            work_dir,
            "muxed.mp4"
        )

        mux_audio(
            concat_video,
            audio_concat,
            muxed_video
        )

        # -----------------------------------------
        # SUBTITLES
        # -----------------------------------------

        srt_path = os.path.join(
            work_dir,
            "captions.srt"
        )

        create_srt(
            subtitle_items,
            srt_path
        )

        # -----------------------------------------
        # BURN
        # -----------------------------------------

        final_video = os.path.join(
            work_dir,
            "final.mp4"
        )

        burn_captions(
            muxed_video,
            srt_path,
            final_video
        )

        # -----------------------------------------
        # VERIFY
        # -----------------------------------------

        if not os.path.exists(
            final_video
        ):
            raise RuntimeError(
                "Final video was not created"
            )

        final_size = os.path.getsize(
            final_video
        )

        if final_size < 10000:
            raise RuntimeError(
                f"Final video is too small: {final_size}"
            )

        log.info("=" * 80)
        log.info(
            "PHASE=CREATE_REEL_DONE size=%s",
            final_size
        )
        log.info("=" * 80)

        # Important:
        # copy to persistent temp path before deleting work_dir

        persistent_final = os.path.join(
            tempfile.gettempdir(),
            f"abosaraj_final_{uuid.uuid4().hex}.mp4"
        )

        shutil.copy2(
            final_video,
            persistent_final
        )

        return persistent_final

    except Exception as error:

        log.error(
            "CREATE_REEL_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    finally:

        try:
            shutil.rmtree(
                work_dir,
                ignore_errors=True
            )

            log.info(
                "WORK_DIR_CLEANED=%s",
                work_dir
            )

        except Exception:
            pass


# =========================================================
# /HFTES5
# =========================================================

def run_hf_test():
    test_dir = tempfile.mkdtemp(
        prefix="hf_test_"
    )

    try:

        prompt = (
            "A cinematic realistic robot standing alone "
            "in a dark futuristic laboratory, slowly looking "
            "toward the camera, subtle natural body movement, "
            "dramatic cinematic lighting, photorealistic "
            "moving video"
        )

        log.info(
            "=" * 80
        )

        log.info(
            "PHASE=HF_TEST_START"
        )

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

        log.info(
            "PHASE=HF_TEST_SUCCESS duration=%s size=%s",
            duration,
            size
        )

        return video, duration, size

    except Exception as error:

        log.error(
            "HF_TEST_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    finally:

        # لا نحذف الآن إذا نجح؟ نحتاج إرسال الفيديو
        pass


# =========================================================
# TELEGRAM MESSAGE PROCESSING
# =========================================================

def process_message(update):
    log.info(
        "TELEGRAM_UPDATE_RECEIVED"
    )

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

    if not chat_id:
        return

    if not text:
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
            "أرسل لي فكرة فيديو قصيرة وسأحاول "
            "تحويلها إلى فيديو سينمائي."
        )

        return

    # -----------------------------------------
    # HF TEST 3
    # -----------------------------------------

    if text == "/hftest3":

        send_message(
            chat_id,
            "🔎 أفحص Hugging Face وواجهة الـ API..."
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
                "✅ HF API اشتغل.\n\n"
                + "Endpoints:\n"
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
    # HF TEST 4
    # -----------------------------------------

    if text == "/hftest4":

        send_message(
            chat_id,
            "🔎 أقرأ باراميترات /generate..."
        )

        try:

            client = create_hf_client()

            api = get_hf_api_dict(
                client
            )

            endpoint = get_generate_endpoint(
                api
            )

            params = get_parameter_list(
                endpoint
            )

            lines = []

            for p in params:

                lines.append(
                    f"- {p.get('name')} "
                    f"| default={p.get('default')} "
                    f"| type={p.get('type')} "
                    f"| choices={p.get('choices')}"
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
    # HF TEST 5
    # -----------------------------------------

    if text == "/hftest5":

        send_message(
            chat_id,
            "🎥 بدأت اختبار Wan مباشر...\n"
            "اصبر، هذا الاختبار ممكن يأخذ وقت."
        )

        test_video = None

        try:

            test_video, duration, size = run_hf_test()

            send_message(
                chat_id,
                "✅ Wan اشتغل بنجاح!\n\n"
                f"⏱️ المدة: {duration:.2f} ثانية\n"
                f"📦 الحجم: {size / 1024 / 1024:.2f} MB\n\n"
                "الآن المشكلة ليست في توليد الفيديو."
            )

            send_video(
                chat_id,
                test_video,
                caption="🎥 HF Wan Test"
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ اختبار Wan فشل.\n\n"
                + safe_error_text(error)
                + "\n\n"
                "انسخ لي هذه الرسالة كاملة."
            )

        finally:

            if test_video and os.path.exists(
                test_video
            ):

                try:
                    os.remove(
                        test_video
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
            "🎥 تجهيز الفيديو...\n"
            "⏳ قد يستغرق التوليد عدة دقائق."
        )

        final_video = create_reel(
            text
        )

        send_message(
            chat_id,
            "🎙️ تم تجهيز الصوت.\n"
            "📝 تم تجهيز الكابشن.\n"
            "📤 أرسل لك الفيديو الآن..."
        )

        result = send_video(
            chat_id,
            final_video,
            caption="🎬 تم إنشاء الفيديو"
        )

        if not result:
            raise RuntimeError(
                "Telegram failed to send final video"
            )

        log.info(
            "TELEGRAM_FINAL_VIDEO_SENT"
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
            "الخطأ تم تسجيله في Render Logs.\n"
            "إذا كنت تختبر المشكلة أرسل لي نتيجة /hftest5."
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
            "groq_model": GROQ_MODEL
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
        "SETTING_TELEGRAM_WEBHOOK=%s",
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

    log.info("=" * 80)
    log.info("ABOSARAJ BOT STARTING")
    log.info("=" * 80)

    if not BOT_TOKEN:
        log.warning(
            "BOT_TOKEN is missing"
        )

    if not GROQ_API_KEY:
        log.warning(
            "GROQ_API_KEY is missing"
        )

    if not HF_TOKEN:
        log.warning(
            "HF_TOKEN is missing"
        )

    log.info(
        "PORT=%s",
        PORT
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    log.info(
        "GROQ_MODEL=%s",
        GROQ_MODEL
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
