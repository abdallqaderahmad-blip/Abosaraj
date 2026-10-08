import os
import json
import time
import asyncio
import shutil
import tempfile
import threading
import subprocess
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
WAVESPEED_API_KEY = os.environ.get(
    "WAVESPEED_API_KEY",
    ""
)

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).rstrip("/")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)


# =========================================================
# SAFE TEST MODE
# =========================================================
#
# TRUE:
#   - NO WaveSpeed API calls
#   - NO WaveSpeed image generation
#   - NO WaveSpeed video generation
#   - Uses local FFmpeg test visuals
#   - Tests Groq + TTS + subtitles + audio + montage
#
# FALSE:
#   - Real WaveSpeed images
#   - Real WaveSpeed videos
#
# IMPORTANT:
# Keep this TRUE until TTS/audio/subtitles are confirmed.
#

TEST_MODE = (
    os.getenv(
        "TEST_MODE",
        "true"
    ).lower()
    in (
        "1",
        "true",
        "yes",
        "on"
    )
)


# =========================================================
# EPISODE FORMAT
# =========================================================

SHOT_COUNT = 4
SHOT_DURATION = 5

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24


# =========================================================
# CHARACTER VOICES
# =========================================================

VOICE_CONFIG = {

    "male_lead": {
        "voice": "ar-SY-LaithNeural",
        "rate": "-10%",
        "pitch": "-3Hz",
    },

    "princess": {
        "voice": "ar-SA-ZariyahNeural",
        "rate": "-6%",
    },

    "king": {
        "voice": "ar-EG-ShakirNeural",
        "rate": "-8%",
        "pitch": "-4Hz",
    },

    "guard": {
        "voice": "ar-IQ-BasselNeural",
        "rate": "-2%",
        "pitch": "-1Hz",
    },

    "narrator": {
        "voice": "ar-SA-HamedNeural",
        "rate": "-8%",
        "pitch": "-2Hz",
    }
}


# =========================================================
# WAVESPEED
# =========================================================

WAVESPEED_BASE = (
    "https://api.wavespeed.ai/api/v3"
)

IMAGE_MODEL = (
    "wavespeed-ai/z-image/turbo"
)

IMAGE_EDIT_MODEL = (
    "wavespeed-ai/z-image-turbo/image-to-image"
)

VIDEO_MODEL = (
    "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)

logging_lock = threading.Lock()


def log(message):

    with logging_lock:

        print(
            f"[ABOSARAJ] "
            f"{time.strftime('%H:%M:%S')} "
            f"{message}",
            flush=True
        )


# =========================================================
# WAVESPEED HEADERS
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
            "WaveSpeed call blocked because "
            "TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:

        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    url = (
        f"{WAVESPEED_BASE}/{model}"
    )

    log(
        f"WaveSpeed submit: {model}"
    )

    response = requests.post(
        url,
        headers=auth_headers(),
        json=payload,
        timeout=(10, 60)
    )

    response.raise_for_status()

    body = response.json()

    if body.get("code") != 200:

        raise RuntimeError(
            body.get(
                "message",
                "WaveSpeed task failed"
            )
        )

    data = body["data"]

    task_id = data.get("id")

    if not task_id:

        raise RuntimeError(
            "WaveSpeed returned no task id: "
            f"{body}"
        )

    log(
        f"Task created: {task_id}"
    )

    return task_id


# =========================================================
# WAVESPEED WAIT
# =========================================================

def wavespeed_wait(
    task_id,
    timeout=900
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed wait blocked because "
            "TEST_MODE=true"
        )

    url = (
        f"{WAVESPEED_BASE}"
        f"/predictions/{task_id}/result"
    )

    started = time.time()

    while True:

        if (
            time.time() - started
            > timeout
        ):

            raise TimeoutError(
                f"WaveSpeed timeout: {task_id}"
            )

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        response.raise_for_status()

        body = response.json()

        if body.get("code") != 200:

            raise RuntimeError(body)

        data = body["data"]

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
                "outputs"
            )

            if not outputs:

                raise RuntimeError(
                    f"No outputs: {body}"
                )

            return outputs[0]

        if status in (
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        ):

            raise RuntimeError(
                f"WaveSpeed task "
                f"{task_id} failed: "
                f"{body}"
            )

        time.sleep(2)


# =========================================================
# WAVESPEED UPLOAD
# =========================================================

def upload_to_wavespeed(path):

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

    size = path.stat().st_size

    log(
        f"Uploading reference: "
        f"{path.name} "
        f"({size} bytes)"
    )

    ticket_response = requests.post(

        f"{WAVESPEED_BASE}/media/uploads",

        headers=auth_headers(),

        json={
            "filename": path.name,
            "size": size
        },

        timeout=30
    )

    ticket_response.raise_for_status()

    ticket = ticket_response.json()

    if ticket.get("code") != 200:

        raise RuntimeError(ticket)

    data = ticket["data"]

    upload_info = data["upload"]

    with path.open("rb") as f:

        upload_response = requests.put(

            upload_info["url"],

            headers=upload_info["headers"],

            data=f,

            timeout=300
        )

    upload_response.raise_for_status()

    return data["download_url"]


# =========================================================
# DOWNLOAD
# =========================================================

def download_file(
    url,
    path
):

    log(
        f"Downloading: {url}"
    )

    response = requests.get(
        url,
        timeout=180
    )

    response.raise_for_status()

    Path(path).write_bytes(
        response.content
    )

    return path


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_cast_reference(
    cast_prompt,
    output_path
):

    if TEST_MODE:

        log(
            "TEST_MODE: skipping CAST image generation."
        )

        return output_path

    log(
        "Generating master CAST reference..."
    )

    task = wavespeed_submit(

        IMAGE_MODEL,

        {
            "prompt":
                cast_prompt,

            "size":
                "1024*1536",

            "output_format":
                "jpeg",

            "seed":
                24117
        }
    )

    url = wavespeed_wait(task)

    download_file(
        url,
        output_path
    )

    return output_path


def generate_scene_image(
    cast_url,
    prompt,
    output_path,
    seed
):

    if TEST_MODE:

        log(
            "TEST_MODE: skipping scene image generation."
        )

        return output_path

    log(
        "Generating scene image..."
    )

    task = wavespeed_submit(

        IMAGE_EDIT_MODEL,

        {
            "prompt":
                prompt,

            "image":
                cast_url,

            "size":
                "1024*1536",

            "strength":
                0.42,

            "seed":
                seed,

            "output_format":
                "jpeg"
        }
    )

    url = wavespeed_wait(task)

    download_file(
        url,
        output_path
    )

    return output_path


# =========================================================
# VIDEO GENERATION
# =========================================================

def generate_video(
    scene_image_url,
    video_prompt,
    seed
):

    if TEST_MODE:

        log(
            "TEST_MODE: skipping WaveSpeed video generation."
        )

        return None

    log(
        "Generating cinematic video..."
    )

    task = wavespeed_submit(

        VIDEO_MODEL,

        {
            "prompt":
                video_prompt,

            "image":
                scene_image_url,

            "duration":
                SHOT_DURATION,

            "seed":
                seed,

            "negative_prompt": (
                "text, subtitles, watermark, logo, "
                "bad anatomy, deformed face, "
                "extra fingers, duplicate people, "
                "melting face, flicker, jitter, "
                "low quality, blurry, distorted hands, "
                "game art, anime, illustration, "
                "plastic skin, posed character, "
                "static camera"
            )
        }
    )

    return wavespeed_wait(task)


# =========================================================
# GROQ JSON SCHEMA
# =========================================================

CHARACTER_SCHEMA = {

    "type": "object",

    "additionalProperties": False,

    "properties": {

        "identity": {
            "type": "string"
        },

        "age": {
            "type": "string"
        },

        "face": {
            "type": "string"
        },

        "hair": {
            "type": "string"
        },

        "clothes": {
            "type": "string"
        },

        "colors": {
            "type": "string"
        }
    },

    "required": [
        "identity",
        "age",
        "face",
        "hair",
        "clothes",
        "colors"
    ]
}


DIALOGUE_SCHEMA = {

    "type": "object",

    "additionalProperties": False,

    "properties": {

        "speaker": {
            "type": "string",

            "enum": [
                "male_lead",
                "princess",
                "king",
                "guard",
                "narrator"
            ]
        },

        "text": {
            "type": "string"
        },

        "emotion": {
            "type": "string"
        }
    },

    "required": [
        "speaker",
        "text",
        "emotion"
    ]
}


SCENE_SCHEMA = {

    "type": "object",

    "additionalProperties": False,

    "properties": {

        "action": {
            "type": "string"
        },

        "dialogue": {

            "type": "array",

            "minItems": 1,

            "maxItems": 2,

            "items":
                DIALOGUE_SCHEMA
        },

        "scene_image_prompt": {
            "type": "string"
        },

        "video_prompt": {
            "type": "string"
        },

        "camera": {
            "type": "string"
        },

        "sound": {
            "type": "string"
        }
    },

    "required": [
        "action",
        "dialogue",
        "scene_image_prompt",
        "video_prompt",
        "camera",
        "sound"
    ]
}


STORY_SCHEMA = {

    "type": "object",

    "additionalProperties": False,

    "properties": {

        "title": {
            "type": "string"
        },

        "hook": {
            "type": "string"
        },

        "cast": {

            "type": "object",

            "additionalProperties": False,

            "properties": {

                "male_lead":
                    CHARACTER_SCHEMA,

                "princess":
                    CHARACTER_SCHEMA,

                "wolf":
                    CHARACTER_SCHEMA,

                "king":
                    CHARACTER_SCHEMA
            },

            "required": [
                "male_lead",
                "princess",
                "wolf",
                "king"
            ]
        },

        "visual_style": {
            "type": "string"
        },

        "cast_reference_prompt": {
            "type": "string"
        },

        "scenes": {

            "type": "array",

            "minItems": 4,

            "maxItems": 4,

            "items":
                SCENE_SCHEMA
        }
    },

    "required": [
        "title",
        "hook",
        "cast",
        "visual_style",
        "cast_reference_prompt",
        "scenes"
    ]
}


# =========================================================
# GROQ STORY
# =========================================================

def create_story(
    user_idea
):

    log(
        "Groq: generating cinematic screenplay..."
    )

    system_prompt = f"""

أنت الآن:

كاتب سيناريو سينمائي عربي،
مخرج،
مدير تصوير،
ومشرف استمرارية.

نريد إنتاج مسلسل Microdrama عربي
سينمائي قصير.

الفكرة الأساسية التي نريد بناء العالم حولها:

- رجل غامض وجذاب يمتلك قوة خارقة
- أميرة تقع في حبه
- والدها الملك يرفض زواجهما
- ذئبة بيضاء صغيرة تصبح مرتبطة بهما
- الذئبة تكبر عبر الحلقات
- لاحقاً تصبح الذئبة قوية وتنقذهما
- هناك سر قديم يربط البطل بالقصر

الفكرة التي أدخلها المستخدم الآن:

{user_idea}

النوع:

Dark Fantasy
Romance
Mystery
Suspense
Supernatural Drama

عدد اللقطات:

{SHOT_COUNT}

مدة كل لقطة:

{SHOT_DURATION} ثوانٍ.

=========================================
قواعد السيناريو
=========================================

لا تكتب قصة عامة.

اكتب مشهداً من مسلسل حقيقي.

كل لقطة يجب أن تحرك القصة.

كل لقطة يجب أن تحتوي على:

- حدث واضح
- حركة شخصيات
- حركة كاميرا
- حركة بيئة
- إضاءة
- إحساس
- صوت/جو
- حوار عندما يكون مناسباً

لا تجعل كل الشخصيات تتكلم.

لا تجعل الحوار شرحاً لما نراه.

الحوار يجب أن يكون طبيعياً
ومختصراً ومناسباً لفيلم.

=========================================
الشخصيات
=========================================

male_lead:

رجل 28-32 سنة.

جذاب.

غامض.

هادئ.

ذكي.

ليس شريراً بشكل مباشر.

شعر داكن.

ملامح رجولية واقعية.

ملابس داكنة أنيقة.

قوة خارقة مخفية.

لا تجعله يبدو كـ game character.

لا armor إلا إذا كان ضرورياً.

princess:

أميرة شابة بالغة.

جميلة.

قوية الشخصية.

رومانسية ولكن ليست ضعيفة.

ملابس ملكية واقعية.

لا مبالغة كرتونية.

wolf:

ذئبة بيضاء صغيرة.

واقعية.

فرو أبيض.

عيون مميزة.

في بداية السلسلة تكون صغيرة.

لا تجعلها كلباً.

king:

رجل ملكي أكبر سناً.

مهيب.

قوي.

ليس شريراً سطحياً.

يعرف شيئاً خطيراً عن البطل.

=========================================
الاستمرارية
=========================================

نفس الوجه.

نفس العمر.

نفس الشعر.

نفس الملابس الأساسية.

نفس الألوان.

نفس هوية الشخصية.

لا تغيّر الشخصيات بين اللقطات.

=========================================
الحوار
=========================================

كل dialogue يجب أن يحتوي:

speaker

text

emotion

speaker يجب أن يكون واحداً من:

male_lead
princess
king
guard
narrator

استخدم maximum شخصيتين متحدثتين
في اللقطة الواحدة.

كل لقطة:

1 أو 2 جمل قصيرة فقط.

الحوار يجب أن يكون مناسباً
لـ {SHOT_DURATION} ثوانٍ.

مهم جداً:

لا تجعل مجموع كلام الشخصيات
في اللقطة الواحدة طويلاً.

استهدف تقريباً
من 5 إلى 14 كلمة عربية
لكل لقطة.

لا تكتب جمل طويلة.

لا تضع أسماء الشخصيات داخل text.

مثال صحيح:

speaker:
male_lead

text:
"لا تخافي... أنا هنا."

emotion:
"calm but mysterious"

=========================================
الذئبة
=========================================

الذئبة لا تتكلم.

استخدم أصواتها فقط ضمن sound:

whimper

growl

howl

breathing

=========================================
الأسلوب البصري
=========================================

realistic cinematic Arabic fantasy drama,

professional film lighting,

realistic human skin,

realistic fabric,

realistic animal fur,

volumetric moonlight,

atmospheric fog,

shallow depth of field,

anamorphic cinematic look,

dramatic rim lighting,

natural body movement,

emotional facial acting,

foreground/background separation,

cinematic camera movement,

high production value,

vertical 9:16 composition.

لا:

anime

cartoon

illustration

game art

plastic skin

overly perfect CGI

watermark

logo

text inside image

=========================================
CAST REFERENCE
=========================================

أنشئ cast reference واحد
يحافظ على شكل:

male_lead
princess
wolf
king

يجب أن يكون:

cinematic character lineup,

realistic faces,

full body where possible,

consistent lighting,

neutral but cinematic environment,

clear separation between characters,

high detail,

photorealistic.

لا تجعل الشخصيات تبدو كأنها
صورة جماعية عشوائية.

=========================================
الحبكة
=========================================

أول لقطة:

HOOK قوي.

اللقطة الرابعة:

CLIFFHANGER قوي.

يجب أن يشعر المشاهد:

"ماذا سيحدث بعد ذلك؟"

لا تحل كل الأسرار.

اترك أسئلة للمشاهد.

لا تستخدم أطفالاً.

لا تستخدم gore.

لا تستخدم محتوى جنسياً.

لا تستخدم شعارات.

لا تستخدم watermarks.

=========================================
OUTPUT
=========================================

أخرج JSON فقط وفق Schema.

لا تضف أي نص خارج JSON.
"""

    user_prompt = f"""

حوّل فكرة المستخدم التالية
إلى حلقة Microdrama سينمائية:

{user_idea}

تذكر:

هذه الحلقة جزء من عالم مستمر.

الشخصيات يجب أن تبدو
كشخصيات مسلسل حقيقية.

الحوار يجب أن يكون جزءاً
من الحدث وليس تعليقاً عليه.

أخرج JSON وفق الـSchema.
"""

    response = groq.chat.completions.create(

        model=GROQ_MODEL,

        temperature=0.7,

        max_tokens=16000,

        response_format={

            "type":
                "json_schema",

            "json_schema": {

                "name":
                    "abosaraj_episode",

                "strict":
                    True,

                "schema":
                    STORY_SCHEMA
            }
        },

        messages=[

            {
                "role":
                    "system",

                "content":
                    system_prompt
            },

            {
                "role":
                    "user",

                "content":
                    user_prompt
            }
        ]
    )

    content = (
        response
        .choices[0]
        .message
        .content
    )

    if not content:

        raise RuntimeError(
            "Groq returned empty content"
        )

    log(
        "Groq screenplay JSON received."
    )

    try:

        result = json.loads(
            content
        )

    except json.JSONDecodeError as e:

        log(
            f"Groq JSON parse error: {e}"
        )

        log(
            f"Raw Groq output: "
            f"{content[:5000]}"
        )

        raise

    scenes = result.get(
        "scenes",
        []
    )

    if len(scenes) != SHOT_COUNT:

        raise RuntimeError(
            f"Groq returned "
            f"{len(scenes)} scenes, "
            f"expected {SHOT_COUNT}"
        )

    return result


# =========================================================
# TTS
# =========================================================

async def tts_async(
    text,
    output,
    voice_config
):

    kwargs = {

        "text":
            text,

        "voice":
            voice_config["voice"],

        "rate":
            voice_config.get(
                "rate",
                "0%"
            )
    }

    # IMPORTANT:
    #
    # Do NOT send pitch when it is not explicitly
    # configured.
    #
    # This prevents the old:
    #
    # ValueError("Invalid pitch '0Hz'")
    #
    # problem.

    pitch = voice_config.get(
        "pitch"
    )

    if pitch:

        kwargs["pitch"] = pitch

    communicator = edge_tts.Communicate(
        **kwargs
    )

    await communicator.save(
        str(output)
    )


def create_tts(
    text,
    output,
    speaker
):

    if speaker not in VOICE_CONFIG:

        raise RuntimeError(
            f"Unknown speaker: {speaker}"
        )

    config = VOICE_CONFIG[speaker]

    log(
        f"TTS: {speaker} -> "
        f"{config['voice']} | "
        f"rate={config.get('rate', '0%')} | "
        f"pitch={config.get('pitch', 'natural')}"
    )

    asyncio.run(

        tts_async(

            text,

            output,

            config
        )
    )


# =========================================================
# FFMPEG
# =========================================================

def run_ffmpeg(
    args
):

    command = [
        "ffmpeg",
        "-y"
    ] + args

    log(
        "FFmpeg: "
        + " ".join(
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
            result.stderr[-5000:]
        )


# =========================================================
# MEDIA DURATION
# =========================================================

def get_media_duration(
    path
):

    command = [

        "ffprobe",

        "-v",
        "error",

        "-show_entries",
        "format=duration",

        "-of",
        "default=noprint_wrappers=1:nokey=1",

        str(path)
    ]

    result = subprocess.run(

        command,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE,

        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr[-3000:]
        )

    try:

        return float(
            result.stdout.strip()
        )

    except ValueError:

        raise RuntimeError(
            f"Invalid media duration: "
            f"{path}"
        )


# =========================================================
# NORMALIZE VIDEO
# =========================================================

def normalize_video(
    source,
    output
):

    run_ffmpeg([

        "-i",
        str(source),

        "-vf",

        (
            f"scale={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}"
        ),

        "-r",
        str(VIDEO_FPS),

        "-t",
        str(SHOT_DURATION),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-an",

        str(output)
    ])


# =========================================================
# LOCAL TEST VIDEO
# =========================================================
#
# This replaces WaveSpeed video generation while
# TEST_MODE=true.
#
# It creates a simple cinematic-looking test frame
# with different background tones and scene number.
#
# No external API is called.
#

def create_test_scene_video(
    scene_index,
    scene,
    output
):

    log(
        f"TEST_MODE: creating local test scene "
        f"{scene_index}/{SHOT_COUNT}"
    )

    # Different test backgrounds per scene.
    #
    # This makes it easy to confirm that all four
    # scenes actually joined correctly.

    backgrounds = [
        "color=c=0x10151f",
        "color=c=0x17120f",
        "color=c=0x111a15",
        "color=c=0x1a101a"
    ]

    background = backgrounds[
        (scene_index - 1)
        % len(backgrounds)
    ]

    duration = SHOT_DURATION

    # Draw scene number.
    #
    # NOTE:
    # These are TEST visuals only.
    # They are NOT production visuals.

    filter_graph = (

        f"{background}:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"r={VIDEO_FPS},"
        f"drawtext="
        f"text='ABOSARAJ TEST - SCENE {scene_index}':"
        f"fontcolor=white:"
        f"fontsize=34:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2"
    )

    run_ffmpeg([

        "-f",
        "lavfi",

        "-i",
        filter_graph,

        "-t",
        str(duration),

        "-r",
        str(VIDEO_FPS),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-an",

        str(output)
    ])

    return output


# =========================================================
# CONCAT VIDEO
# =========================================================

def concat_videos(
    videos,
    output
):

    list_file = (
        output.parent /
        "video_list.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for video in videos:

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

    run_ffmpeg([

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(list_file),

        "-c",
        "copy",

        str(output)
    ])


# =========================================================
# AUDIO NORMALIZATION
# =========================================================

def normalize_audio(
    source,
    output
):

    run_ffmpeg([

        "-i",
        str(source),

        "-vn",

        "-ac",
        "1",

        "-ar",
        "24000",

        "-c:a",
        "pcm_s16le",

        str(output)
    ])

    if not output.exists():

        raise RuntimeError(
            f"Audio normalization failed: "
            f"{output}"
        )

    if output.stat().st_size <= 44:

        raise RuntimeError(
            f"Normalized audio is empty: "
            f"{output}"
        )


# =========================================================
# CONCAT AUDIO
# =========================================================

def concat_audio(
    audios,
    output
):

    normalized_files = []

    total = len(audios)

    log(
        f"Preparing {total} audio files..."
    )

    for index, audio in enumerate(
        audios,
        start=1
    ):

        audio = Path(audio)

        normalized = (

            output.parent /

            f"normalized_audio_{index}.wav"
        )

        normalize_audio(

            audio,

            normalized
        )

        normalized_files.append(
            normalized
        )

    list_file = (

        output.parent /

        "audio_wav_list.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for audio in normalized_files:

            safe_path = (

                str(audio)

                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    run_ffmpeg([

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

        str(output)
    ])

    if not output.exists():

        raise RuntimeError(
            "Audio concatenation failed."
        )

    return output


# =========================================================
# SCENE AUDIO
# =========================================================

def create_scene_audio(
    scene,
    scene_index,
    workdir
):

    dialogue_files = []

    dialogue_meta = []

    dialogue = scene.get(
        "dialogue",
        []
    )

    if not dialogue:

        raise RuntimeError(
            f"Scene {scene_index} "
            f"contains no dialogue."
        )

    for line_index, line in enumerate(
        dialogue,
        start=1
    ):

        speaker = line["speaker"]

        text = line["text"]

        audio = (

            workdir /

            f"scene_{scene_index}_"
            f"line_{line_index}_"
            f"{speaker}.mp3"
        )

        log(
            f"Scene {scene_index}: "
            f"{speaker}: {text}"
        )

        create_tts(

            text,

            audio,

            speaker
        )

        if not audio.exists():

            raise RuntimeError(
                f"TTS failed: {audio}"
            )

        if audio.stat().st_size == 0:

            raise RuntimeError(
                f"TTS generated empty file: "
                f"{audio}"
            )

        duration = get_media_duration(
            audio
        )

        dialogue_files.append(
            audio
        )

        dialogue_meta.append({

            "speaker":
                speaker,

            "text":
                text,

            "duration":
                duration
        })

    # ---------------------------------------------
    # Normalize each line
    # ---------------------------------------------

    normalized_files = []

    for index, audio in enumerate(
        dialogue_files,
        start=1
    ):

        normalized = (

            workdir /

            f"scene_{scene_index}_"
            f"normalized_{index}.wav"
        )

        normalize_audio(

            audio,

            normalized
        )

        normalized_files.append(
            normalized
        )

    # ---------------------------------------------
    # Concat lines
    # ---------------------------------------------

    list_file = (

        workdir /

        f"scene_{scene_index}_audio.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for audio in normalized_files:

            safe_path = (

                str(audio)

                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    scene_audio = (

        workdir /

        f"scene_{scene_index}_dialogue.m4a"
    )

    run_ffmpeg([

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

        str(scene_audio)
    ])

    total_duration = get_media_duration(
        scene_audio
    )

    log(
        f"Scene {scene_index} dialogue "
        f"duration: {total_duration:.2f}s"
    )

    return (
        scene_audio,
        dialogue_meta,
        total_duration
    )


# =========================================================
# FIT AUDIO TO SHOT
# =========================================================

def fit_scene_audio(
    scene_audio,
    total_duration,
    scene_index,
    workdir
):

    if total_duration <= SHOT_DURATION:

        return (
            scene_audio,
            1.0
        )

    log(
        f"Scene {scene_index}: dialogue is "
        f"{total_duration:.2f}s; "
        f"fitting to {SHOT_DURATION}s."
    )

    fitted_audio = (

        workdir /

        f"scene_{scene_index}_fitted.m4a"
    )

    ratio = (

        total_duration /
        SHOT_DURATION
    )

    # Do not compress too aggressively.

    ratio = max(
        1.0,
        min(
            ratio,
            1.35
        )
    )

    tempo = ratio

    run_ffmpeg([

        "-i",
        str(scene_audio),

        "-filter:a",
        f"atempo={tempo:.4f}",

        "-t",
        str(SHOT_DURATION),

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(fitted_audio)
    ])

    return (
        fitted_audio,
        tempo
    )


# =========================================================
# BUILD FULL DIALOGUE TRACK
# =========================================================

def build_dialogue_track(
    scene_audio_files,
    output
):

    list_file = (

        output.parent /

        "scene_dialogue_list.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for audio in scene_audio_files:

            safe_path = (

                str(audio)

                .replace(
                    "'",
                    "'\\''"
                )
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    run_ffmpeg([

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

        str(output)
    ])

    return output


# =========================================================
# CREATE AMBIENCE
# =========================================================

def create_ambience(
    output,
    duration
):

    run_ffmpeg([

        "-f",
        "lavfi",

        "-i",

        (
            "anoisesrc="
            "color=brown:"
            "amplitude=0.018:"
            f"duration={duration}"
        ),

        "-af",

        (
            "lowpass=f=900,"
            "highpass=f=80,"
            "volume=0.45"
        ),

        "-c:a",
        "aac",

        "-b:a",
        "96k",

        str(output)
    ])


# =========================================================
# MIX VOICE + AMBIENCE
# =========================================================

def mix_audio(
    voice,
    ambience,
    output
):

    run_ffmpeg([

        "-i",
        str(voice),

        "-i",
        str(ambience),

        "-filter_complex",

        (
            "[0:a]volume=1.0[voice];"
            "[1:a]volume=0.16[amb];"
            "[voice][amb]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
        ),

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(output)
    ])


# =========================================================
# SRT
# =========================================================

def seconds_to_srt(
    seconds
):

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = int(
        seconds % 60
    )

    milliseconds = int(
        (
            seconds
            - int(seconds)
        ) * 1000
    )

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{milliseconds:03}"
    )


def create_srt(
    scenes,
    scene_dialogue_meta,
    scene_tempo,
    output
):

    lines = []

    global_time = 0.0

    subtitle_index = 1

    for scene_index, scene in enumerate(
        scenes,
        start=1
    ):

        meta = scene_dialogue_meta[
            scene_index - 1
        ]

        tempo = scene_tempo[
            scene_index - 1
        ]

        local_time = 0.0

        for line in meta:

            original_duration = float(
                line["duration"]
            )

            # If the audio was compressed,
            # subtitles must be compressed
            # by the same factor.

            if tempo > 1.0:

                duration = (
                    original_duration /
                    tempo
                )

            else:

                duration = original_duration

            start = (
                global_time
                + local_time
            )

            end = (
                start
                + duration
            )

            # Never let a subtitle cross
            # the 5-second scene boundary.

            scene_end = (
                global_time
                + SHOT_DURATION
            )

            end = min(
                end,
                scene_end - 0.03
            )

            if end > start:

                text = line["text"]

                lines.append(
                    str(subtitle_index)
                )

                lines.append(

                    f"{seconds_to_srt(start)} "
                    f"--> "
                    f"{seconds_to_srt(end)}"
                )

                lines.append(
                    text
                )

                lines.append("")

                subtitle_index += 1

            local_time += duration

        global_time += SHOT_DURATION

    output.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# =========================================================
# ADD ARABIC CAPTIONS
# =========================================================

def add_captions(
    video,
    srt,
    output
):

    subtitle_path = (

        str(srt)

        .replace(
            "\\",
            "/"
        )

        .replace(
            ":",
            "\\:"
        )
    )

    style = (

        "FontName=DejaVu Sans,"

        "FontSize=22,"

        "PrimaryColour=&H00FFFFFF,"

        "OutlineColour=&H00000000,"

        "BorderStyle=1,"

        "Outline=2,"

        "Shadow=1,"

        "Alignment=2,"

        "MarginV=70"
    )

    run_ffmpeg([

        "-i",
        str(video),

        "-vf",

        (
            f"subtitles='{subtitle_path}':"
            f"force_style='{style}'"
        ),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-t",
        str(
            SHOT_COUNT *
            SHOT_DURATION
        ),

        str(output)
    ])


# =========================================================
# FINAL AUDIO
# =========================================================

def add_audio(
    video,
    audio,
    output
):

    run_ffmpeg([

        "-i",
        str(video),

        "-i",
        str(audio),

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

        "-t",
        str(
            SHOT_COUNT *
            SHOT_DURATION
        ),

        str(output)
    ])


# =========================================================
# COMPLETE EPISODE
# =========================================================

def create_episode(
    user_idea,
    workdir
):

    log(
        "================================"
    )

    log(
        "CREATING CINEMATIC EPISODE"
    )

    log(
        "================================"
    )

    log(
        f"TEST_MODE = {TEST_MODE}"
    )

    if TEST_MODE:

        log(
            "🛡️ WaveSpeed is COMPLETELY DISABLED."
        )

    # =====================================================
    # 1. STORY
    # =====================================================

    story = create_story(
        user_idea
    )

    log(
        f"TITLE: {story['title']}"
    )

    log(
        f"HOOK: {story['hook']}"
    )

    # =====================================================
    # 2. CAST REFERENCE
    # =====================================================

    cast_image = (

        workdir /

        "cast_reference.jpg"
    )

    if not TEST_MODE:

        generate_cast_reference(

            story[
                "cast_reference_prompt"
            ],

            cast_image
        )

        cast_url = (

            upload_to_wavespeed(
                cast_image
            )
        )

    else:

        cast_url = None

        log(
            "TEST_MODE: CAST generation skipped."
        )

    # =====================================================
    # 3. SCENES / VIDEO
    # =====================================================

    scene_videos = []

    for index, scene in enumerate(

        story["scenes"],

        start=1
    ):

        log(
            f"========== "
            f"SCENE {index}/{SHOT_COUNT} "
            f"=========="
        )

        cast = story["cast"]

        male = cast["male_lead"]

        princess = cast["princess"]

        wolf = cast["wolf"]

        king = cast["king"]

        continuity_prompt = f"""

CAST CONTINUITY:

MALE LEAD:
Identity: {male['identity']}
Age: {male['age']}
Face: {male['face']}
Hair: {male['hair']}
Clothes: {male['clothes']}
Colors: {male['colors']}

PRINCESS:
Identity: {princess['identity']}
Age: {princess['age']}
Face: {princess['face']}
Hair: {princess['hair']}
Clothes: {princess['clothes']}
Colors: {princess['colors']}

WHITE WOLF:
Identity: {wolf['identity']}
Age: {wolf['age']}
Face: {wolf['face']}
Hair/Fur: {wolf['hair']}
Clothes: {wolf['clothes']}
Colors: {wolf['colors']}

KING:
Identity: {king['identity']}
Age: {king['age']}
Face: {king['face']}
Hair: {king['hair']}
Clothes: {king['clothes']}
Colors: {king['colors']}

Preserve exact recurring identities.
Do not randomly redesign characters.
"""

        scene_prompt = (

            story["visual_style"]

            + "\n"

            + continuity_prompt

            + "\n"

            + "SCENE ACTION:\n"

            + scene["action"]

            + "\n"

            + "SCENE VISUAL:\n"

            + scene[
                "scene_image_prompt"
            ]

            + "\n"

            + """
CINEMATIC REQUIREMENTS:

photorealistic,

cinematic film still,

realistic human skin,

realistic fabric,

realistic animal fur,

volumetric lighting,

atmospheric fog,

shallow depth of field,

anamorphic look,

dramatic rim light,

natural poses,

natural facial expressions,

foreground/background separation,

vertical 9:16,

high production value.

No text.

No subtitles.

No logo.

No watermark.

No anime.

No cartoon.

No illustration.

No game art.
"""
        )

        scene_image = (

            workdir /

            f"scene_{index}.jpg"
        )

        # ---------------------------------------------
        # REAL MODE
        # ---------------------------------------------

        if not TEST_MODE:

            generate_scene_image(

                cast_url,

                scene_prompt,

                scene_image,

                24117 + index
            )

            scene_url = (

                upload_to_wavespeed(
                    scene_image
                )
            )

            video_prompt = (

                scene["video_prompt"]

                + "\n"

                + scene["camera"]

                + "\n"

                + """
Cinematic natural movement.

Characters must move naturally.

Facial expressions must remain realistic.

Camera movement should be subtle and film-like.

Environmental movement:

wind,

cloth movement,

hair movement,

fog,

leaves,

natural light changes.

No frozen poses.

No sudden morphing.

No text.

No subtitles.

No logos.
"""
            )

            video_url = generate_video(

                scene_url,

                video_prompt,

                50000 + index
            )

            raw_video = (

                workdir /

                f"raw_{index}.mp4"
            )

            normalized_video = (

                workdir /

                f"scene_{index}.mp4"
            )

            download_file(

                video_url,

                raw_video
            )

            normalize_video(

                raw_video,

                normalized_video
            )

        # ---------------------------------------------
        # TEST MODE
        # ---------------------------------------------

        else:

            normalized_video = (

                workdir /

                f"scene_{index}.mp4"
            )

            create_test_scene_video(

                index,

                scene,

                normalized_video
            )

        scene_videos.append(
            normalized_video
        )

    # =====================================================
    # 4. JOIN VIDEO
    # =====================================================

    joined_video = (

        workdir /

        "joined.mp4"
    )

    concat_videos(

        scene_videos,

        joined_video
    )

    # =====================================================
    # 5. CHARACTER DIALOGUE AUDIO
    # =====================================================

    scene_audio_files = []

    scene_dialogue_meta = []

    scene_tempo = []

    for index, scene in enumerate(

        story["scenes"],

        start=1
    ):

        log(
            f"Building dialogue audio "
            f"for scene {index}..."
        )

        (
            scene_audio,
            dialogue_meta,
            total_duration
        ) = create_scene_audio(

            scene,

            index,

            workdir
        )

        (
            scene_audio,
            tempo
        ) = fit_scene_audio(

            scene_audio,

            total_duration,

            index,

            workdir
        )

        scene_audio_files.append(
            scene_audio
        )

        scene_dialogue_meta.append(
            dialogue_meta
        )

        scene_tempo.append(
            tempo
        )

    # =====================================================
    # 6. FULL DIALOGUE TRACK
    # =====================================================

    voice_track = (

        workdir /

        "voice.m4a"
    )

    build_dialogue_track(

        scene_audio_files,

        voice_track
    )

    # =====================================================
    # 7. AMBIENCE
    # =====================================================

    ambience = (

        workdir /

        "ambience.m4a"
    )

    create_ambience(

        ambience,

        SHOT_COUNT
        * SHOT_DURATION
    )

    # =====================================================
    # 8. MIX
    # =====================================================

    mixed_audio = (

        workdir /

        "mixed_audio.m4a"
    )

    mix_audio(

        voice_track,

        ambience,

        mixed_audio
    )

    # =====================================================
    # 9. AUDIO + VIDEO
    # =====================================================

    voiced_video = (

        workdir /

        "voiced.mp4"
    )

    add_audio(

        joined_video,

        mixed_audio,

        voiced_video
    )

    # =====================================================
    # 10. SUBTITLES
    # =====================================================

    srt = (

        workdir /

        "captions.srt"
    )

    create_srt(

        story["scenes"],

        scene_dialogue_meta,

        scene_tempo,

        srt
    )

    # =====================================================
    # 11. FINAL VIDEO
    # =====================================================

    final_video = (

        workdir /

        "final.mp4"
    )

    add_captions(

        voiced_video,

        srt,

        final_video
    )

    log(
        "================================"
    )

    log(
        "EPISODE COMPLETE"
    )

    log(
        "================================"
    )

    log(
        f"TEST_MODE = {TEST_MODE}"
    )

    return final_video, story


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(
    method,
    data=None,
    files=None
):

    url = (

        "https://api.telegram.org/"

        f"bot{BOT_TOKEN}/{method}"
    )

    response = requests.post(

        url,

        data=data,

        files=files,

        timeout=180
    )

    response.raise_for_status()

    body = response.json()

    if not body.get("ok"):

        raise RuntimeError(body)

    return body


def send_message(
    chat_id,
    text
):

    telegram_api(

        "sendMessage",

        {
            "chat_id":
                chat_id,

            "text":
                text
        }
    )


def send_video(
    chat_id,
    path,
    caption=""
):

    with open(
        path,
        "rb"
    ) as video:

        telegram_api(

            "sendVideo",

            data={

                "chat_id":
                    chat_id,

                "caption":
                    caption[:1024]
            },

            files={

                "video": (

                    "abosaraj.mp4",

                    video,

                    "video/mp4"
                )
            }
        )


# =========================================================
# MESSAGE PROCESSING
# =========================================================

def process_message(
    chat_id,
    text
):

    workdir = Path(

        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        if TEST_MODE:

            start_message = (

                "🧪 ABOSARAJ TEST MODE\n\n"

                "🛡️ WaveSpeed: OFF\n"

                "💰 تكلفة WaveSpeed: $0\n\n"

                "🧠 كتابة السيناريو\n"

                "🎭 بناء الشخصيات\n"

                "🎙️ اختبار أصوات الشخصيات\n"

                "📝 اختبار الترجمة العربية\n"

                "🎧 اختبار الصوت والـAmbience\n"

                "🎬 اختبار FFmpeg\n"

                "📱 اختبار إرسال الفيديو"
            )

        else:

            start_message = (

                "🎬 بدأت صناعة الحلقة...\n\n"

                "🧠 كتابة السيناريو\n"

                "🎭 بناء الشخصيات\n"

                "🎨 تثبيت الـ Cast\n"

                "🎥 بناء المشاهد\n"

                "🎬 الحركة السينمائية\n"

                "🎙️ أصوات الشخصيات\n"

                "📝 الترجمة العربية\n"

                "🎧 الجو والمؤثرات\n"

                "✂️ المونتاج"
            )

        send_message(

            chat_id,

            start_message
        )

        final_video, story = (

            create_episode(

                text,

                workdir
            )
        )

        if TEST_MODE:

            caption = (

                "🧪 ABOSARAJ TEST\n\n"

                f"🎬 {story['title']}\n\n"

                f"{story['hook']}\n\n"

                "🛡️ WaveSpeed: OFF\n"

                "💰 WaveSpeed cost: $0"
            )

        else:

            caption = (

                f"🎬 {story['title']}\n\n"

                f"{story['hook']}"
            )

        send_video(

            chat_id,

            final_video,

            caption
        )

        log(
            f"Sent episode to {chat_id}"
        )

    except Exception as e:

        log(
            f"ERROR: {repr(e)}"
        )

        try:

            send_message(

                chat_id,

                "❌ صار خطأ أثناء صناعة الحلقة.\n\n"

                "راجع Logs في Render."
            )

        except Exception as telegram_error:

            log(
                "Telegram error while "
                f"sending failure message: "
                f"{repr(telegram_error)}"
            )

    finally:

        shutil.rmtree(

            workdir,

            ignore_errors=True
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Abosaraj cinematic engine OK",
        200
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return {

        "status":
            "ok",

        "test_mode":
            TEST_MODE,

        "wavespeed_enabled":
            not TEST_MODE,

        "image_model":
            IMAGE_MODEL,

        "video_model":
            VIDEO_MODEL,

        "groq_model":
            GROQ_MODEL,

        "shots":
            SHOT_COUNT,

        "duration":
            SHOT_DURATION,

        "voices":
            VOICE_CONFIG
    }


@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    update = (

        request.get_json(
            silent=True
        )

        or {}
    )

    message = (

        update.get("message")

        or {}
    )

    chat = (

        message.get("chat")

        or {}
    )

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text"
    )

    if not chat_id or not text:

        return "ok", 200

    if text.startswith(
        "/start"
    ):

        if TEST_MODE:

            mode_text = (
                "🧪 Test Mode شغال\n"
                "🛡️ WaveSpeed OFF\n"
                "💰 $0 استهلاك WaveSpeed"
            )

        else:

            mode_text = (
                "🎬 Production Mode شغال"
            )

        send_message(

            chat_id,

            "🔥 أهلاً بك في Abosaraj.\n\n"

            f"{mode_text}\n\n"

            "اكتب فكرة الحلقة.\n\n"

            "مثال:\n\n"

            "أميرة تهرب من قصر أبيها "
            "بعد أن يقع قلبها في حب رجل غامض "
            "يمتلك قوة محرمة، "
            "وتجد في الغابة ذئبة بيضاء صغيرة."
        )

        return "ok", 200

    threading.Thread(

        target=process_message,

        args=(
            chat_id,
            text
        ),

        daemon=True

    ).start()

    return "ok", 200


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if not RENDER_EXTERNAL_URL:

        log(
            "RENDER_EXTERNAL_URL missing"
        )

        return

    webhook_url = (

        f"{RENDER_EXTERNAL_URL}"

        "/telegram/webhook"
    )

    try:

        telegram_api(

            "setWebhook",

            {
                "url":
                    webhook_url
            }
        )

        log(
            f"Webhook: {webhook_url}"
        )

    except Exception as e:

        log(
            f"Webhook error: {e}"
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log(
        "=============================="
    )

    log(
        "ABOSARAJ CINEMATIC ENGINE"
    )

    log(
        "=============================="
    )

    log(
        f"TEST_MODE: {TEST_MODE}"
    )

    if TEST_MODE:

        log(
            "🛡️ SAFE TEST MODE ENABLED"
        )

        log(
            "🛡️ ALL WAVESPEED CALLS ARE DISABLED"
        )

        log(
            "💰 WAVESPEED COST FOR THIS TEST: $0"
        )

    else:

        log(
            "🔥 PRODUCTION MODE ENABLED"
        )

        if not WAVESPEED_API_KEY:

            log(
                "WARNING: WAVESPEED_API_KEY missing"
            )

    log(
        f"Image: {IMAGE_MODEL}"
    )

    log(
        f"Video: {VIDEO_MODEL}"
    )

    log(
        f"Groq: {GROQ_MODEL}"
    )

    log(
        f"Shots: "
        f"{SHOT_COUNT} x "
        f"{SHOT_DURATION}s"
    )

    log(
        "VOICE CAST:"
    )

    for character, config in (
        VOICE_CONFIG.items()
    ):

        log(
            f"  {character}: "
            f"{config['voice']} "
            f"rate={config.get('rate', '0%')} "
            f"pitch={config.get('pitch', 'natural')}"
        )

    setup_webhook()

    app.run(

        host="0.0.0.0",

        port=PORT
    )
