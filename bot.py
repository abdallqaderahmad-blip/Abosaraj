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
WAVESPEED_API_KEY = os.environ["WAVESPEED_API_KEY"]

PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).rstrip("/")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

# =========================================================
# EPISODE FORMAT
# =========================================================
#
# CURRENT TEST:
# 4 shots x 5 seconds = 20 seconds
#
# After the complete pipeline works:
# 12 shots x 5 seconds = 60 seconds
#

SHOT_COUNT = 4
SHOT_DURATION = 5

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

TTS_VOICE = "ar-SA-HamedNeural"


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
# HTTP HEADERS
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
# WAVESPEED FILE UPLOAD
# =========================================================

def upload_to_wavespeed(path):

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

def generate_character_reference(
    character_prompt,
    output_path
):

    log(
        "Generating master character..."
    )

    task = wavespeed_submit(

        IMAGE_MODEL,

        {
            "prompt":
                character_prompt,

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
    character_url,
    prompt,
    output_path,
    seed
):

    log(
        "Generating scene image..."
    )

    task = wavespeed_submit(

        IMAGE_EDIT_MODEL,

        {
            "prompt":
                prompt,

            "image":
                character_url,

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
                "low quality, blurry, distorted hands"
            )
        }
    )

    return wavespeed_wait(task)


# =========================================================
# GROQ JSON SCHEMA
# =========================================================

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

        "character": {

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
        },

        "visual_style": {
            "type": "string"
        },

        "character_image_prompt": {
            "type": "string"
        },

        "scenes": {

            "type": "array",

            "minItems": 4,

            "maxItems": 4,

            "items": {

                "type": "object",

                "additionalProperties": False,

                "properties": {

                    "narration_ar": {
                        "type": "string"
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
                    "narration_ar",
                    "scene_image_prompt",
                    "video_prompt",
                    "camera",
                    "sound"
                ]
            }
        }
    },

    "required": [
        "title",
        "hook",
        "character",
        "visual_style",
        "character_image_prompt",
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
        "Groq: generating story..."
    )

    system_prompt = f"""
أنت كاتب سيناريو ومخرج سينمائي
ومدير تصوير ومشرف استمرارية.

نريد إنتاج Microdrama عربي قصير.

النوع:

Mystery / Suspense /
Psychological Thriller.

الفكرة:

{user_idea}

عدد اللقطات المطلوب بالضبط:

{SHOT_COUNT}

مدة كل لقطة:

{SHOT_DURATION} ثوانٍ.

الهدف ليس slideshow.

كل لقطة يجب أن تبدو كجزء
من فيلم حقيقي.

كل لقطة يجب أن تحتوي على:

- حركة شخصية
- حركة كاميرا
- حركة بيئة
- إضاءة سينمائية
- عمق مجال
- composition
- cinematic pacing

يجب الحفاظ على نفس الشخصية
في جميع اللقطات.

الشخصية الرئيسية واحدة فقط.

لا تستخدم أطفالاً.

لا تستخدم gore.

لا تستخدم محتوى جنسياً.

لا توجد شعارات.

لا توجد watermarks.

لا توجد كتابة داخل الصور.

narration_ar يجب أن يكون
عربياً طبيعياً ومثيراً.

كل لقطة:
جملة أو جملتان فقط.

scene_image_prompt بالإنجليزية.

video_prompt بالإنجليزية.

camera بالإنجليزية.

sound بالإنجليزية.

أول لقطة يجب أن تحتوي Hook.

اللقطة الرابعة يجب أن تحتوي
كشفاً أو cliffhanger.

الأسلوب البصري:

realistic cinematic Arabic drama,
professional film lighting,
natural human skin,
shallow depth of field,
realistic camera movement,
high production value,
vertical composition.

أنت مسؤول عن الاستمرارية.

نفس الوجه.

نفس العمر.

نفس الشعر.

نفس الملابس.

نفس الألوان.

نفس الشخصية.

لا تغير الشخصية بين اللقطات.
"""

    user_prompt = f"""
حوّل الفكرة التالية إلى حلقة
Microdrama سينمائية:

{user_idea}

أخرج النتيجة وفق الـJSON Schema المقدم.
"""

    response = groq.chat.completions.create(

        model=GROQ_MODEL,

        temperature=0.7,

        max_tokens=12000,

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
        "Groq JSON received."
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

    log(
        f"Groq story ready: "
        f"{result.get('title')}"
    )

    return result


# =========================================================
# ARABIC TTS
# =========================================================

async def tts_async(
    text,
    output
):

    communicator = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicator.save(
        str(output)
    )


def create_tts(
    text,
    output
):

    asyncio.run(
        tts_async(
            text,
            output
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
# CONCAT AUDIO — FIXED
# =========================================================

def concat_audio(
    audios,
    output
):

    """
    Edge-TTS produces MP3 files.

    We do NOT concatenate the MP3 files directly.

    Every MP3 is first normalized to:
        WAV
        PCM s16le
        24 kHz
        mono

    Then all WAV files are concatenated.

    Finally the result is encoded to AAC.
    """

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

        log(
            f"Normalizing audio "
            f"{index}/{total}: "
            f"{audio.name}"
        )

        run_ffmpeg([

            "-i",
            str(audio),

            "-vn",

            "-ac",
            "1",

            "-ar",
            "24000",

            "-c:a",
            "pcm_s16le",

            str(normalized)
        ])

        if not normalized.exists():

            raise RuntimeError(
                f"Audio normalization "
                f"failed: {normalized}"
            )

        if normalized.stat().st_size <= 44:

            raise RuntimeError(
                f"Normalized audio is empty: "
                f"{normalized}"
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

    log(
        "Concatenating normalized WAV files..."
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
            "Audio concatenation produced "
            "no output file."
        )

    if output.stat().st_size == 0:

        raise RuntimeError(
            "Audio concatenation produced "
            "an empty file."
        )

    log(
        f"Audio concatenation complete: "
        f"{output.name}"
    )

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
            "amplitude=0.025:"
            f"duration={duration}"
        ),

        "-af",

        (
            "lowpass=f=900,"
            "highpass=f=80,"
            "volume=0.55"
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
    output
):

    lines = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        start = (
            index - 1
        ) * SHOT_DURATION

        end = (
            index
        ) * SHOT_DURATION

        lines.append(
            str(index)
        )

        lines.append(
            f"{seconds_to_srt(start)} "
            f"--> "
            f"{seconds_to_srt(end)}"
        )

        lines.append(
            scene["narration_ar"]
        )

        lines.append("")

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
        "FontName=Arial,"
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

        "-shortest",

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
        "CREATING EPISODE"
    )

    log(
        "================================"
    )

    # -----------------------------------------------------
    # 1. STORY
    # -----------------------------------------------------

    story = create_story(
        user_idea
    )

    log(
        f"TITLE: {story['title']}"
    )

    # -----------------------------------------------------
    # 2. MASTER CHARACTER
    # -----------------------------------------------------

    character_image = (
        workdir /
        "character.jpg"
    )

    generate_character_reference(

        story[
            "character_image_prompt"
        ],

        character_image
    )

    character_url = (
        upload_to_wavespeed(
            character_image
        )
    )

    # -----------------------------------------------------
    # 3. SCENES
    # -----------------------------------------------------

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

        # ---------------------------------------------
        # Scene image
        # ---------------------------------------------

        scene_image = (
            workdir /
            f"scene_{index}.jpg"
        )

        scene_prompt = (

            story["visual_style"]

            + "\n"

            + story["character"]["identity"]

            + "\nAge: "

            + story["character"]["age"]

            + "\nFace: "

            + story["character"]["face"]

            + "\nHair: "

            + story["character"]["hair"]

            + "\nClothes: "

            + story["character"]["clothes"]

            + "\nColors: "

            + story["character"]["colors"]

            + "\n"

            + scene[
                "scene_image_prompt"
            ]

            + "\n"

            + (
                "Maintain the exact same "
                "main character identity, "
                "face, hair, clothing and colors."
            )
        )

        generate_scene_image(

            character_url,

            scene_prompt,

            scene_image,

            24117 + index
        )

        # ---------------------------------------------
        # Upload scene image
        # ---------------------------------------------

        scene_url = (
            upload_to_wavespeed(
                scene_image
            )
        )

        # ---------------------------------------------
        # Video
        # ---------------------------------------------

        video_prompt = (

            scene["video_prompt"]

            + "\n"

            + scene["camera"]

            + "\n"

            + "Natural cinematic movement."

            + "\n"

            + (
                "Keep the character "
                "identity consistent."
            )

            + "\n"

            + (
                "No text, no subtitles, "
                "no logos."
            )
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

        scene_videos.append(
            normalized_video
        )

    # -----------------------------------------------------
    # 4. JOIN VIDEO
    # -----------------------------------------------------

    joined_video = (
        workdir /
        "joined.mp4"
    )

    concat_videos(
        scene_videos,
        joined_video
    )

    # -----------------------------------------------------
    # 5. ARABIC VOICE
    # -----------------------------------------------------

    voice_files = []

    for index, scene in enumerate(

        story["scenes"],

        start=1
    ):

        audio = (
            workdir /
            f"voice_{index}.mp3"
        )

        log(
            f"Creating Arabic voice "
            f"{index}/{SHOT_COUNT}..."
        )

        create_tts(

            scene[
                "narration_ar"
            ],

            audio
        )

        if not audio.exists():

            raise RuntimeError(
                f"TTS did not create "
                f"file: {audio}"
            )

        if audio.stat().st_size == 0:

            raise RuntimeError(
                f"TTS created empty file: "
                f"{audio}"
            )

        voice_files.append(
            audio
        )

    voice_track = (
        workdir /
        "voice.m4a"
    )

    concat_audio(
        voice_files,
        voice_track
    )

    # -----------------------------------------------------
    # 6. AMBIENCE
    # -----------------------------------------------------

    ambience = (
        workdir /
        "ambience.m4a"
    )

    create_ambience(

        ambience,

        SHOT_COUNT
        * SHOT_DURATION
    )

    mixed_audio = (
        workdir /
        "mixed_audio.m4a"
    )

    mix_audio(

        voice_track,

        ambience,

        mixed_audio
    )

    # -----------------------------------------------------
    # 7. AUDIO + VIDEO
    # -----------------------------------------------------

    voiced_video = (
        workdir /
        "voiced.mp4"
    )

    add_audio(

        joined_video,

        mixed_audio,

        voiced_video
    )

    # -----------------------------------------------------
    # 8. CAPTIONS
    # -----------------------------------------------------

    srt = (
        workdir /
        "captions.srt"
    )

    create_srt(
        story["scenes"],
        srt
    )

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

        send_message(

            chat_id,

            "🎬 بدأت صناعة الحلقة...\n\n"

            "🧠 كتابة القصة\n"

            "👤 تثبيت الشخصية\n"

            "🎨 بناء المشاهد\n"

            "🎥 توليد الحركة السينمائية\n"

            "🎙️ الصوت العربي\n"

            "📝 النص العربي\n"

            "🎧 المؤثرات\n"

            "✂️ المونتاج"
        )

        final_video, story = (

            create_episode(

                text,

                workdir
            )
        )

        send_video(

            chat_id,

            final_video,

            (
                f"🎬 {story['title']}\n\n"
                f"{story['hook']}"
            )
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

        "image_model":
            IMAGE_MODEL,

        "video_model":
            VIDEO_MODEL,

        "groq_model":
            GROQ_MODEL,

        "shots":
            SHOT_COUNT,

        "duration":
            SHOT_DURATION
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

        send_message(

            chat_id,

            "🔥 أهلاً بك في Abosaraj.\n\n"

            "اكتب فكرة الحلقة، مثال:\n\n"

            "رجل يسمع صوت زوجته المتوفاة "
            "كل ليلة الساعة 3:17."
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
        f"TTS: {TTS_VOICE}"
    )

    setup_webhook()

    app.run(

        host="0.0.0.0",

        port=PORT
    )
