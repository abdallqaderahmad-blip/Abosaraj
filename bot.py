import os
import re
import json
import uuid
import time
import shutil
import logging
import tempfile
import subprocess
from pathlib import Path

import requests
import edge_tts

from flask import Flask, request, jsonify

from groq import Groq
from gradio_client import Client


# ============================================================
# CONFIG
# ============================================================

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

SHOT_COUNT = int(os.getenv("SHOT_COUNT", "2"))
SHOT_DURATION = int(os.getenv("SHOT_DURATION", "5"))

# Wan generation
GEN_WIDTH = int(os.getenv("GEN_WIDTH", "576"))
GEN_HEIGHT = int(os.getenv("GEN_HEIGHT", "832"))
GEN_FRAMES = int(os.getenv("GEN_FRAMES", "81"))
GEN_FPS = int(os.getenv("GEN_FPS", "16"))
GEN_STEPS = int(os.getenv("GEN_STEPS", "20"))
GEN_GUIDANCE = float(os.getenv("GEN_GUIDANCE", "5.0"))
GEN_SEED = int(os.getenv("GEN_SEED", "0"))

# Final Reel
FINAL_WIDTH = 720
FINAL_HEIGHT = 1280

# Arabic voice
TTS_VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
)

DEFAULT_CTA = "تابعنا، لأن القصة الجاية أخطر."


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")


# ============================================================
# VALIDATION
# ============================================================

if not BOT_TOKEN:
    log.warning("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    log.warning("GROQ_API_KEY is missing")

if not HF_TOKEN:
    log.warning("HF_TOKEN is missing")


# ============================================================
# CLIENTS
# ============================================================

groq_client = None

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# HELPERS
# ============================================================

def safe_error_text(error):
    """
    يمنع ظهور التوكنات أو المعلومات الحساسة داخل Render Logs.
    """
    text = str(error)

    if BOT_TOKEN:
        text = text.replace(BOT_TOKEN, "[BOT_TOKEN]")

    if GROQ_API_KEY:
        text = text.replace(GROQ_API_KEY, "[GROQ_API_KEY]")

    if HF_TOKEN:
        text = text.replace(HF_TOKEN, "[HF_TOKEN]")

    return text[:4000]


def run_cmd(command, check=True):
    """
    تشغيل FFmpeg أو أي أمر خارجي.
    """
    log.info("RUN_CMD=%s", " ".join(map(str, command)))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        log.error(
            "COMMAND_ERROR=%s",
            result.stderr[-5000:]
        )

        if check:
            raise RuntimeError(
                "COMMAND_FAILED: " +
                result.stderr[-2000:]
            )

    return result


def telegram_api(method, data=None):
    """
    Telegram Bot API.
    """
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN_MISSING")

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        json=data or {},
        timeout=60
    )

    try:
        result = response.json()
    except Exception:
        result = {
            "ok": False,
            "description": response.text
        }

    if not result.get("ok"):
        log.error(
            "TELEGRAM_API_ERROR method=%s result=%s",
            method,
            result
        )

    return result


def send_message(chat_id, text):
    return telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def download_telegram_file(file_id, destination):
    """
    تنزيل ملف من Telegram.
    """
    info = telegram_api(
        "getFile",
        {
            "file_id": file_id
        }
    )

    if not info.get("ok"):
        raise RuntimeError(
            "TELEGRAM_GET_FILE_FAILED"
        )

    file_path = info["result"]["file_path"]

    url = (
        f"https://api.telegram.org/file/bot"
        f"{BOT_TOKEN}/{file_path}"
    )

    response = requests.get(
        url,
        timeout=120
    )

    response.raise_for_status()

    with open(destination, "wb") as f:
        f.write(response.content)

    return destination


# ============================================================
# TELEGRAM VIDEO SEND
# ============================================================

def send_video(
    chat_id,
    video_path,
    caption
):
    """
    إرسال الفيديو النهائي إلى Telegram.
    """

    if not os.path.exists(video_path):
        raise RuntimeError(
            "VIDEO_FILE_NOT_FOUND"
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendVideo"
    )

    with open(video_path, "rb") as video_file:

        response = requests.post(
            url,
            data={
                "chat_id": str(chat_id),
                "caption": caption
            },
            files={
                "video": (
                    os.path.basename(video_path),
                    video_file,
                    "video/mp4"
                )
            },
            timeout=600
        )

    try:
        result = response.json()
    except Exception:
        result = {
            "ok": False,
            "description": response.text
        }

    if not result.get("ok"):
        raise RuntimeError(
            "TELEGRAM_SEND_VIDEO_FAILED: "
            + str(result)
        )

    return result


# ============================================================
# GROQ JSON CLEANER
# ============================================================

def extract_json_object(raw):
    """
    يحاول استخراج JSON من رد Groq حتى لو أضاف Markdown.
    """

    if not raw:
        raise RuntimeError(
            "GROQ_EMPTY_RESPONSE"
        )

    raw = raw.strip()

    # إزالة ```json ... ```
    raw = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        raw,
        flags=re.IGNORECASE
    )

    raw = re.sub(
        r"\s*```\s*$",
        "",
        raw
    )

    raw = raw.strip()

    start = raw.find("{")

    if start == -1:
        raise RuntimeError(
            "GROQ_NO_JSON_OBJECT"
        )

    # البحث عن نهاية JSON بطريقة لا تتأثر
    # بالأقواس الموجودة داخل النصوص
    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(raw)):

        char = raw[i]

        if in_string:

            if escaped:
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == '"':
                in_string = False

            continue

        if char == '"':
            in_string = True

        elif char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                return raw[start:i + 1]

    raise RuntimeError(
        "GROQ_UNTERMINATED_JSON"
    )


# ============================================================
# STORYBOARD
# ============================================================

def create_storyboard(user_idea):
    """
    إنشاء قصة ومشاهد من Groq.

    الإصلاح الأساسي هنا:
    - JSON صارم
    - عدم السماح بـ Markdown
    - استخراج JSON
    - محاولة إصلاح مشاكل شائعة
    - تسجيل مكان الخطأ
    """

    if not groq_client:
        raise RuntimeError(
            "GROQ_API_KEY_MISSING"
        )

    system_prompt = """
You are a professional cinematic short-video director.

Create a suspenseful Arabic story for TikTok and Instagram Reels.

The story must feel cinematic, realistic and engaging.

IMPORTANT OUTPUT RULES:

Return ONLY one valid JSON object.

DO NOT return Markdown.

DO NOT use ```.

DO NOT write any explanation before or after the JSON.

Every JSON string MUST be valid JSON.

If you need quotation marks inside Arabic text, use single quotation marks instead of double quotation marks.

Do not put raw newline characters inside string values.

Use this exact structure:

{
  "title": "short Arabic title",
  "hook": "short Arabic hook",
  "narration": "complete Arabic narration",
  "cta": "short Arabic CTA",
  "scenes": [
    {
      "scene": 1,
      "duration": 5,
      "prompt": "detailed English cinematic video prompt",
      "narration": "Arabic narration for this scene"
    }
  ]
}

Rules:

- Exactly the requested number of scenes.
- Every scene is exactly 5 seconds.
- Every scene must contain narration.
- Video prompts must be in English.
- Prompts must describe real moving cinematic video.
- Never describe a still image.
- Use natural camera movement.
- Use realistic lighting.
- Maintain visual continuity.
- Keep the same main character when possible.
- The story must flow continuously.
- Start with a strong hook.
- Increase tension.
- End with a twist or reveal.
- Fictional stories must not be presented as real events.
- Keep Arabic narration short enough for its scene.
- Avoid excessive dialogue.
"""


    user_prompt = f"""
Create a suspenseful cinematic story based on this idea:

{user_idea}

Create exactly {SHOT_COUNT} scenes.

Each scene must be exactly {SHOT_DURATION} seconds.

Return ONLY valid JSON.
"""


    try:

        log.info(
            "GROQ_STORYBOARD_START idea=%s",
            user_idea[:500]
        )

        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            temperature=0.65,
            max_tokens=5000
        )

        raw = (
            response.choices[0]
            .message
            .content
            .strip()
        )

        log.info(
            "GROQ_RAW_RESPONSE_START"
        )

        log.info(
            "%s",
            raw[:10000]
        )

        log.info(
            "GROQ_RAW_RESPONSE_END"
        )

        # استخراج JSON
        json_text = extract_json_object(raw)

        log.info(
            "GROQ_JSON_EXTRACTED_LENGTH=%s",
            len(json_text)
        )

        try:

            storyboard = json.loads(
                json_text
            )

        except json.JSONDecodeError as error:

            log.error(
                "GROQ_JSON_ERROR line=%s column=%s char=%s",
                error.lineno,
                error.colno,
                error.pos
            )

            start = max(
                0,
                error.pos - 500
            )

            end = min(
                len(json_text),
                error.pos + 1000
            )

            log.error(
                "GROQ_BAD_JSON_CONTEXT=%s",
                json_text[start:end]
            )

            raise RuntimeError(
                "GROQ_INVALID_JSON: "
                f"line {error.lineno} "
                f"column {error.colno}"
            )

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not isinstance(
            storyboard,
            dict
        ):
            raise RuntimeError(
                "GROQ_STORYBOARD_NOT_OBJECT"
            )

        scenes = storyboard.get(
            "scenes"
        )

        if not isinstance(
            scenes,
            list
        ):
            raise RuntimeError(
                "GROQ_SCENES_NOT_LIST"
            )

        if not scenes:
            raise RuntimeError(
                "GROQ_NO_SCENES"
            )

        cleaned_scenes = []

        for index, scene in enumerate(
            scenes[:SHOT_COUNT],
            start=1
        ):

            if not isinstance(
                scene,
                dict
            ):
                continue

            prompt = str(
                scene.get(
                    "prompt",
                    ""
                )
            ).strip()

            narration = str(
                scene.get(
                    "narration",
                    ""
                )
            ).strip()

            if not prompt:
                prompt = (
                    "Cinematic realistic moving video, "
                    "natural camera movement, "
                    "dramatic lighting, "
                    "realistic environment, "
                    "high detail, "
                    "photorealistic"
                )

            if not narration:
                narration = (
                    "لكن شيئًا غريبًا بدأ يحدث."
                )

            cleaned_scenes.append(
                {
                    "scene": index,
                    "duration": SHOT_DURATION,
                    "prompt": prompt,
                    "narration": narration
                }
            )

        if not cleaned_scenes:
            raise RuntimeError(
                "GROQ_EMPTY_CLEANED_SCENES"
            )

        storyboard["scenes"] = (
            cleaned_scenes
        )

        storyboard["title"] = str(
            storyboard.get(
                "title",
                "قصة غامضة"
            )
        ).strip()

        storyboard["hook"] = str(
            storyboard.get(
                "hook",
                ""
            )
        ).strip()

        storyboard["narration"] = str(
            storyboard.get(
                "narration",
                ""
            )
        ).strip()

        storyboard["cta"] = str(
            storyboard.get(
                "cta",
                DEFAULT_CTA
            )
        ).strip()

        log.info(
            "STORYBOARD_OK scenes=%s title=%s",
            len(cleaned_scenes),
            storyboard["title"]
        )

        return storyboard

    except Exception as error:

        log.error(
            "STORYBOARD_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise


# ============================================================
# HF WAN CLIENT
# ============================================================

def create_hf_client():
    """
    إنشاء Gradio client لـ Wan.
    """

    log.info(
        "HF_CLIENT_CREATE space=%s",
        HF_SPACE
    )

    if HF_TOKEN:
        client = Client(
            HF_SPACE,
            hf_token=HF_TOKEN
        )
    else:
        client = Client(
            HF_SPACE
        )

    log.info(
        "HF_CLIENT_READY type=%s",
        type(client).__name__
    )

    return client


# ============================================================
# HF API DISCOVERY
# ============================================================

def get_hf_api_dict(client):

    log.info(
        "HF_VIEW_API_START"
    )

    try:

        api_dict = client.view_api(
            return_format="dict"
        )

    except TypeError:

        api_dict = client.view_api(
            return_format="dict"
        )

    if not isinstance(
        api_dict,
        dict
    ):
        raise RuntimeError(
            "HF_API_SCHEMA_INVALID"
        )

    log.info(
        "HF_API_KEYS=%s",
        list(api_dict.keys())
    )

    return api_dict


def get_generate_endpoint(
    api_dict
):
    """
    إيجاد /generate.
    """

    named = api_dict.get(
        "named_endpoints",
        {}
    )

    endpoint = named.get(
        "/generate"
    )

    if endpoint:
        return endpoint

    # fallback
    for name, data in named.items():

        if (
            "generate" in
            str(name).lower()
        ):
            return data

    raise RuntimeError(
        "HF_GENERATE_ENDPOINT_NOT_FOUND"
    )


# ============================================================
# HF ARGUMENT BUILDER
# ============================================================

def build_generate_arguments(
    endpoint,
    prompt
):
    """
    يبني arguments حسب schema الحقيقي
    الخاص بـ Wan API.

    مهم:
    model_key وليس model.
    """

    parameters = endpoint.get(
        "parameters",
        []
    )

    args = []

    negative_prompt = (
        "blurry, low quality, "
        "distorted, deformed, "
        "static image, text, watermark, "
        "bad anatomy, duplicate objects"
    )

    for parameter in parameters:

        name = str(
            parameter.get(
                "parameter_name",
                ""
            )
        )

        name_lower = name.lower()

        has_default = parameter.get(
            "parameter_has_default",
            False
        )

        default = parameter.get(
            "parameter_default"
        )

        type_info = parameter.get(
            "type",
            {}
        )

        choices = type_info.get(
            "enum",
            []
        )

        # ----------------------------------------------------
        # MODEL
        # ----------------------------------------------------

        if name_lower in {
            "model_key",
            "model",
            "model_name",
            "checkpoint",
            "checkpoint_name"
        }:

            if choices:

                if "wan-base" in choices:
                    value = "wan-base"

                else:

                    non_nsfw = [
                        x for x in choices
                        if "nsfw" not in str(x).lower()
                    ]

                    if non_nsfw:
                        value = non_nsfw[0]
                    else:
                        value = choices[0]

            else:
                value = (
                    default
                    if has_default
                    else "wan-base"
                )

            args.append(value)

        # ----------------------------------------------------
        # PROMPT
        # ----------------------------------------------------

        elif name_lower in {
            "prompt",
            "text",
            "text_prompt"
        }:

            args.append(prompt)

        # ----------------------------------------------------
        # NEGATIVE PROMPT
        # ----------------------------------------------------

        elif (
            "negative" in name_lower
            and "prompt" in name_lower
        ):

            args.append(
                negative_prompt
            )

        # ----------------------------------------------------
        # WIDTH
        # ----------------------------------------------------

        elif name_lower == "width":

            args.append(
                min(
                    GEN_WIDTH,
                    832
                )
            )

        # ----------------------------------------------------
        # HEIGHT
        # ----------------------------------------------------

        elif name_lower == "height":

            args.append(
                min(
                    GEN_HEIGHT,
                    832
                )
            )

        # ----------------------------------------------------
        # FRAMES
        # ----------------------------------------------------

        elif name_lower in {
            "num_frames",
            "frames",
            "video_frames"
        }:

            args.append(
                GEN_FRAMES
            )

        # ----------------------------------------------------
        # STEPS
        # ----------------------------------------------------

        elif name_lower == "steps":

            args.append(
                GEN_STEPS
            )

        # ----------------------------------------------------
        # GUIDANCE
        # ----------------------------------------------------

        elif name_lower in {
            "guidance_scale",
            "guidance"
        }:

            args.append(
                GEN_GUIDANCE
            )

        # ----------------------------------------------------
        # SEED
        # ----------------------------------------------------

        elif name_lower in {
            "seed",
            "random_seed"
        }:

            args.append(
                GEN_SEED
            )

        # ----------------------------------------------------
        # LORA
        # ----------------------------------------------------

        elif name_lower in {
            "lora_scale",
            "lora_strength"
        }:

            if (
                "lora" in name_lower
            ):
                # wan-base لا يحتاج LoRA
                if has_default:
                    args.append(
                        default
                    )
                else:
                    args.append(
                        1.0
                    )

        # ----------------------------------------------------
        # CUSTOM CHECKPOINT
        # ----------------------------------------------------

        elif (
            "custom" in name_lower
            and (
                "checkpoint" in name_lower
                or "ckpt" in name_lower
            )
        ):

            args.append(
                None
            )

        # ----------------------------------------------------
        # BOOLEAN
        # ----------------------------------------------------

        elif (
            type_info.get("type")
            == "boolean"
        ):

            args.append(False)

        # ----------------------------------------------------
        # CHOICE
        # ----------------------------------------------------

        elif choices:

            args.append(
                choices[0]
            )

        # ----------------------------------------------------
        # DEFAULT
        # ----------------------------------------------------

        elif has_default:

            args.append(
                default
            )

        # ----------------------------------------------------
        # NUMBER
        # ----------------------------------------------------

        elif type_info.get(
            "type"
        ) == "number":

            args.append(0)

        # ----------------------------------------------------
        # INTEGER
        # ----------------------------------------------------

        elif type_info.get(
            "type"
        ) == "integer":

            args.append(0)

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        else:

            args.append(None)

    log.info(
        "HF_ARGUMENT_COUNT=%s",
        len(args)
    )

    return args


# ============================================================
# WAN VIDEO GENERATION
# ============================================================

def generate_ai_video(
    prompt,
    output_dir
):

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    client = create_hf_client()

    api_dict = get_hf_api_dict(
        client
    )

    endpoint = get_generate_endpoint(
        api_dict
    )

    args = build_generate_arguments(
        endpoint,
        prompt
    )

    endpoint_name = "/generate"

    log.info(
        "HF_GENERATION_START"
    )

    log.info(
        "HF_ENDPOINT=%s",
        endpoint_name
    )

    log.info(
        "HF_PROMPT=%s",
        prompt[:1000]
    )

    try:

        result = client.predict(
            *args,
            api_name=endpoint_name
        )

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

    log.info(
        "HF_GENERATION_RESULT_TYPE=%s",
        type(result).__name__
    )

    # --------------------------------------------------------
    # Gradio result can be:
    # filepath
    # tuple
    # list
    # dict
    # --------------------------------------------------------

    video_source = None

    if isinstance(
        result,
        str
    ):

        video_source = result

    elif isinstance(
        result,
        (list, tuple)
    ):

        for item in result:

            if isinstance(
                item,
                str
            ) and os.path.exists(item):

                video_source = item
                break

            if isinstance(
                item,
                dict
            ):

                candidate = (
                    item.get("path")
                    or item.get("url")
                )

                if (
                    candidate
                    and os.path.exists(
                        candidate
                    )
                ):
                    video_source = candidate
                    break

    elif isinstance(
        result,
        dict
    ):

        video_source = (
            result.get("path")
            or result.get("video")
            or result.get("url")
        )

    if not video_source:

        raise RuntimeError(
            "HF_VIDEO_RESULT_NOT_FOUND"
        )

    # --------------------------------------------------------
    # URL result
    # --------------------------------------------------------

    if (
        isinstance(
            video_source,
            str
        )
        and video_source.startswith(
            "http"
        )
    ):

        downloaded = os.path.join(
            output_dir,
            f"wan_{uuid.uuid4().hex}.mp4"
        )

        response = requests.get(
            video_source,
            timeout=300
        )

        response.raise_for_status()

        with open(
            downloaded,
            "wb"
        ) as f:
            f.write(
                response.content
            )

        video_source = downloaded

    if not os.path.exists(
        video_source
    ):
        raise RuntimeError(
            "HF_VIDEO_FILE_MISSING"
        )

    final_path = os.path.join(
        output_dir,
        f"scene_{uuid.uuid4().hex}.mp4"
    )

    shutil.copy2(
        video_source,
        final_path
    )

    log.info(
        "HF_VIDEO_READY=%s",
        final_path
    )

    return final_path


# ============================================================
# VIDEO NORMALIZATION
# ============================================================

def normalize_video(
    input_path,
    output_path
):

    command = [
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
        "16",

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

    run_cmd(command)

    return output_path


# ============================================================
# CONCAT VIDEO
# ============================================================

def concat_videos(
    video_paths,
    output_path,
    work_dir
):

    concat_file = os.path.join(
        work_dir,
        "videos.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in video_paths:

            safe_path = path.replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    command = [
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        concat_file,

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

    run_cmd(command)

    return output_path


# ============================================================
# TTS
# ============================================================

async def generate_tts_async(
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

    import asyncio

    if not text.strip():
        raise RuntimeError(
            "TTS_EMPTY_TEXT"
        )

    asyncio.run(
        generate_tts_async(
            text,
            output_path
        )
    )

    if not os.path.exists(
        output_path
    ):
        raise RuntimeError(
            "TTS_OUTPUT_MISSING"
        )

    return output_path


# ============================================================
# MEDIA DURATION
# ============================================================

def get_duration(
    file_path
):

    command = [
        "ffprobe",
        "-v",
        "error",

        "-show_entries",
        "format=duration",

        "-of",
        "default=noprint_wrappers=1:nokey=1",

        file_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        return float(
            result.stdout.strip()
        )
    except Exception:
        return 0.0


# ============================================================
# CONCAT AUDIO
# ============================================================

def concat_audio(
    audio_paths,
    output_path,
    work_dir
):

    concat_file = os.path.join(
        work_dir,
        "audio.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in audio_paths:

            safe_path = path.replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    command = [
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        concat_file,

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        output_path
    ]

    run_cmd(command)

    return output_path


# ============================================================
# MUX AUDIO
# ============================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
):

    command = [
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

    run_cmd(command)

    return output_path


# ============================================================
# ARABIC FONT
# ============================================================

def find_arabic_font():

    fonts = [
        "Noto Sans Arabic",
        "Noto Naskh Arabic",
        "Noto Sans",
        "DejaVu Sans"
    ]

    for font in fonts:

        result = subprocess.run(
            [
                "fc-match",
                "-f",
                "%{file}",
                font
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        path = result.stdout.strip()

        if (
            path
            and os.path.exists(path)
        ):
            log.info(
                "ARABIC_FONT=%s",
                path
            )
            return path

    return None


# ============================================================
# SRT TIME
# ============================================================

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
        (seconds % 3600) // 60
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
        millis = 0

    if secs >= 60:
        minutes += 1
        secs = 0

    if minutes >= 60:
        hours += 1
        minutes = 0

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{millis:03d}"
    )


# ============================================================
# SRT
# ============================================================

def create_srt(
    subtitle_items,
    output_path
):

    with open(
        output_path,
        "w",
        encoding="utf-8-sig"
    ) as f:

        for index, item in enumerate(
            subtitle_items,
            start=1
        ):

            start = item["start"]
            end = item["end"]
            text = item["text"]

            f.write(
                f"{index}\n"
            )

            f.write(
                f"{format_srt_time(start)} --> "
                f"{format_srt_time(end)}\n"
            )

            f.write(
                f"{text}\n\n"
            )

    return output_path


# ============================================================
# BURN CAPTIONS
# ============================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):

    font_path = find_arabic_font()

    subtitle_filter = (
        "subtitles="
        + srt_path.replace(
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

    if font_path:

        fonts_dir = os.path.dirname(
            font_path
        )

        subtitle_filter += (
            ":fontsdir="
            + fonts_dir.replace(
                "\\",
                "/"
            )
        )

    command = [
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
        "23",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "copy",

        "-movflags",
        "+faststart",

        output_path
    ]

    run_cmd(command)

    return output_path


# ============================================================
# CREATE REEL
# ============================================================

def create_reel(
    user_idea
):

    work_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    log.info(
        "REEL_WORK_DIR=%s",
        work_dir
    )

    try:

        # ====================================================
        # 1. GROQ STORYBOARD
        # ====================================================

        storyboard = create_storyboard(
            user_idea
        )

        scenes = storyboard[
            "scenes"
        ]

        log.info(
            "REEL_SCENES=%s",
            len(scenes)
        )

        # ====================================================
        # 2. WAN SCENES
        # ====================================================

        normalized_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            prompt = scene[
                "prompt"
            ]

            log.info(
                "SCENE_%s_GENERATION_START",
                index
            )

            raw_video = generate_ai_video(
                prompt,
                work_dir
            )

            normalized_path = os.path.join(
                work_dir,
                f"normalized_{index}.mp4"
            )

            normalize_video(
                raw_video,
                normalized_path
            )

            normalized_videos.append(
                normalized_path
            )

            log.info(
                "SCENE_%s_READY",
                index
            )

        if not normalized_videos:
            raise RuntimeError(
                "NO_GENERATED_VIDEOS"
            )

        # ====================================================
        # 3. CONCAT VIDEO
        # ====================================================

        video_path = os.path.join(
            work_dir,
            "video_no_audio.mp4"
        )

        concat_videos(
            normalized_videos,
            video_path,
            work_dir
        )

        # ====================================================
        # 4. TTS PER SCENE
        # ====================================================

        audio_paths = []
        subtitle_items = []

        current_time = 0.0

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            narration = str(
                scene.get(
                    "narration",
                    ""
                )
            ).strip()

            if not narration:
                continue

            audio_path = os.path.join(
                work_dir,
                f"voice_{index}.mp3"
            )

            log.info(
                "TTS_%s_START",
                index
            )

            generate_tts(
                narration,
                audio_path
            )

            duration = get_duration(
                audio_path
            )

            if duration <= 0:
                duration = float(
                    SHOT_DURATION
                )

            start = current_time

            end = (
                current_time
                + duration
            )

            subtitle_items.append(
                {
                    "start": start,
                    "end": end,
                    "text": narration
                }
            )

            audio_paths.append(
                audio_path
            )

            current_time = end

            log.info(
                "TTS_%s_DURATION=%.2f",
                index,
                duration
            )

        if not audio_paths:
            raise RuntimeError(
                "NO_TTS_AUDIO"
            )

        # ====================================================
        # 5. CTA
        # ====================================================

        cta = storyboard.get(
            "cta",
            DEFAULT_CTA
        ).strip()

        video_duration = get_duration(
            video_path
        )

        if (
            cta
            and video_duration > 0
        ):

            # لا نريد CTA خارج الفيديو
            cta_start = max(
                0,
                video_duration - 2.5
            )

            cta_end = video_duration

            subtitle_items.append(
                {
                    "start": cta_start,
                    "end": cta_end,
                    "text": cta
                }
            )

        # ====================================================
        # 6. CONCAT AUDIO
        # ====================================================

        audio_path = os.path.join(
            work_dir,
            "voice_all.m4a"
        )

        concat_audio(
            audio_paths,
            audio_path,
            work_dir
        )

        # ====================================================
        # 7. MUX
        # ====================================================

        muxed_path = os.path.join(
            work_dir,
            "muxed.mp4"
        )

        mux_audio(
            video_path,
            audio_path,
            muxed_path
        )

        # ====================================================
        # 8. SRT
        # ====================================================

        srt_path = os.path.join(
            work_dir,
            "captions.srt"
        )

        create_srt(
            subtitle_items,
            srt_path
        )

        # ====================================================
        # 9. BURN ARABIC CAPTIONS
        # ====================================================

        final_path = os.path.join(
            work_dir,
            "ABOSARAJ_FINAL.mp4"
        )

        burn_captions(
            muxed_path,
            srt_path,
            final_path
        )

        # ====================================================
        # 10. CHECK FINAL
        # ====================================================

        if not os.path.exists(
            final_path
        ):
            raise RuntimeError(
                "FINAL_VIDEO_NOT_CREATED"
            )

        final_size = os.path.getsize(
            final_path
        )

        final_duration = get_duration(
            final_path
        )

        log.info(
            "FINAL_VIDEO_READY "
            "duration=%.2f size=%s",
            final_duration,
            final_size
        )

        return (
            final_path,
            storyboard,
            work_dir
        )

    except Exception as error:

        log.error(
            "CREATE_REEL_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        # نحذف المجلد هنا فقط عند الفشل
        try:
            shutil.rmtree(
                work_dir,
                ignore_errors=True
            )
        except Exception:
            pass

        raise


# ============================================================
# TELEGRAM MESSAGE HANDLER
# ============================================================

def process_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    if not chat_id:
        return

    text = message.get(
        "text",
        ""
    ).strip()

    # ========================================================
    # START
    # ========================================================

    if text == "/start":

        send_message(
            chat_id,
            (
                "🎬 أهلاً بك في Abosaraj\n\n"
                "أرسل لي فكرة القصة فقط، "
                "وسأحوّلها إلى فيديو قصير سينمائي.\n\n"
                "مثال:\n"
                "روبوت يكتشف أن صاحبه حذف جزءاً "
                "من ذاكرته قبل أن يختفي."
            )
        )

        return

    # ========================================================
    # HF TEST 3
    # ========================================================

    if text == "/hftest3":

        send_message(
            chat_id,
            "🧪 اختبار Hugging Face API بدأ..."
        )

        try:

            client = create_hf_client()

            api_dict = get_hf_api_dict(
                client
            )

            endpoint = get_generate_endpoint(
                api_dict
            )

            send_message(
                chat_id,
                (
                    "✅ HF API شغال\n"
                    f"Endpoint: /generate\n"
                    f"Parameters: "
                    f"{len(endpoint.get('parameters', []))}"
                )
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HF TEST3 ERROR\n\n"
                + safe_error_text(error)
            )

        return

    # ========================================================
    # HFT4
    # ========================================================

    if text == "/hftest4":

        send_message(
            chat_id,
            "🧪 فحص Wan API..."
        )

        try:

            client = create_hf_client()

            api_dict = get_hf_api_dict(
                client
            )

            endpoint = get_generate_endpoint(
                api_dict
            )

            names = []

            for p in endpoint.get(
                "parameters",
                []
            ):
                names.append(
                    p.get(
                        "parameter_name"
                    )
                )

            send_message(
                chat_id,
                (
                    "✅ Wan API موجود\n\n"
                    + "\n".join(
                        str(x)
                        for x in names
                    )
                )
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HFT4 ERROR\n\n"
                + safe_error_text(error)
            )

        return

    # ========================================================
    # HFT5
    # ========================================================

    if text == "/hftest5":

        send_message(
            chat_id,
            "🎬 اختبار Wan بدأ...\n"
            "هذا الاختبار يستخدم مشهداً واحداً."
        )

        test_dir = tempfile.mkdtemp(
            prefix="abosaraj_test_"
        )

        try:

            start_time = time.time()

            video = generate_ai_video(
                (
                    "A cinematic realistic robot "
                    "standing alone in a dark "
                    "futuristic laboratory, "
                    "slowly looking toward the camera, "
                    "subtle natural body movement, "
                    "dramatic cinematic lighting, "
                    "photorealistic moving video"
                ),
                test_dir
            )

            duration = get_duration(
                video
            )

            elapsed = (
                time.time()
                - start_time
            )

            send_message(
                chat_id,
                (
                    "🚀 TEST5 نجح 🎬🔥\n\n"
                    f"🎞️ مدة الفيديو: "
                    f"{duration:.2f}s\n"
                    f"⏱ الزمن: "
                    f"{elapsed:.2f}s\n"
                    "🤖 Model: wan-base"
                )
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ TEST5 ERROR\n\n"
                + safe_error_text(error)
            )

        finally:

            shutil.rmtree(
                test_dir,
                ignore_errors=True
            )

        return

    # ========================================================
    # EMPTY MESSAGE
    # ========================================================

    if not text:

        send_message(
            chat_id,
            "✍️ أرسل فكرة القصة كنص."
        )

        return

    # ========================================================
    # STORY GENERATION
    # ========================================================

    send_message(
        chat_id,
        (
            "🎬 وصلت الفكرة.\n\n"
            "🧠 بناء القصة والمشاهد...\n"
            "🎥 توليد الفيديو...\n"
            "🎙️ تجهيز الصوت العربي...\n"
            "📝 تجهيز الكابشن...\n"
            "⏳ اصبر عليّ شوي..."
        )
    )

    try:

        final_path, storyboard, work_dir = (
            create_reel(text)
        )

        title = storyboard.get(
            "title",
            "قصة جديدة"
        )

        duration = get_duration(
            final_path
        )

        caption = (
            f"🎬 {title}\n\n"
            f"⏱ {duration:.1f} ثانية\n"
            "🎙️ صوت عربي رجالي\n"
            "📝 كابشن عربي\n"
            "📱 9:16\n\n"
            "تابعنا، لأن القصة الجاية أخطر."
        )

        send_video(
            chat_id,
            final_path,
            caption
        )

        # بعد نجاح الإرسال
        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )

    except Exception as error:

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        send_message(
            chat_id,
            (
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                "تم تسجيل الخطأ في Render Logs."
            )
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if not update:
            return jsonify(
                {
                    "ok": True
                }
            )

        log.info(
            "TELEGRAM_WEBHOOK_UPDATE_RECEIVED"
        )

        message = update.get(
            "message"
        )

        if message:
            process_message(
                message
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
                "ok": True
            }
        )


# ============================================================
# HEALTH
# ============================================================

@app.route("/")
def home():

    return (
        "Abosaraj AI Reel Bot is running 🎬"
    )


@app.route("/health")
def health():

    return jsonify(
        {
            "status": "ok",
            "service": "Abosaraj",
            "wan_space": HF_SPACE,
            "shot_count": SHOT_COUNT,
            "shot_duration": SHOT_DURATION
        }
    )


# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook():

    if not BOT_TOKEN:
        log.warning(
            "WEBHOOK_NOT_SET: BOT_TOKEN missing"
        )
        return

    render_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if not render_url:

        log.warning(
            "RENDER_EXTERNAL_URL missing"
        )

        return

    webhook_url = (
        render_url.rstrip("/")
        + "/telegram/webhook"
    )

    log.info(
        "SETTING_WEBHOOK=%s",
        webhook_url
    )

    result = telegram_api(
        "setWebhook",
        {
            "url": webhook_url,
            "drop_pending_updates": True
        }
    )

    log.info(
        "SET_WEBHOOK_RESULT=%s",
        result
    )


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":

    log.info(
        "========================================"
    )

    log.info(
        "ABOSARAJ STARTING"
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
        "HF_SPACE=%s",
        HF_SPACE
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
        "========================================"
    )

    # إعطاء Flask فرصة بسيطة ثم ضبط Webhook
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
