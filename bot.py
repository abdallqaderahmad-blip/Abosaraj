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
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

WAVESPEED_API_KEY = os.getenv(
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
# MODES
# =========================================================

TEST_MODE = envbool(
    "TEST_MODE",
    "true"
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
# AUDIO SETTINGS
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
# VIDEO SETTINGS
# =========================================================

SHOT_COUNT = 4
SHOT_DURATION = 5

TOTAL_DURATION = (
    SHOT_COUNT *
    SHOT_DURATION
)

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
        "rate": "-6%",
        "pitch": "+0Hz"
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
# FLASK / GROQ
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# GLOBAL STATE
# =========================================================

logging_lock = threading.Lock()

processing_chats = set()

processing_lock = threading.Lock()


# =========================================================
# LOGGING
# =========================================================

def log(message):

    with logging_lock:

        print(
            "[ABOSARAJ]"
            f" {time.strftime('%H:%M:%S')}"
            f" {message}",
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


def http_post(
    url,
    **kwargs
):

    last_error = None

    for attempt in range(3):

        try:

            return requests.post(
                url,
                **kwargs
            )

        except Exception as e:

            last_error = e

            log(
                f"HTTP POST retry "
                f"{attempt + 1}/3: "
                f"{repr(e)}"
            )

            if attempt < 2:

                time.sleep(
                    2 * (attempt + 1)
                )

    raise last_error


def http_get(
    url,
    **kwargs
):

    last_error = None

    for attempt in range(3):

        try:

            return requests.get(
                url,
                **kwargs
            )

        except Exception as e:

            last_error = e

            log(
                f"HTTP GET retry "
                f"{attempt + 1}/3: "
                f"{repr(e)}"
            )

            if attempt < 2:

                time.sleep(
                    2 * (attempt + 1)
                )

    raise last_error


# =========================================================
# WAVESPEED
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

    response = http_post(
        url,
        headers=auth_headers(),
        json=payload,
        timeout=(10, 60)
    )

    response.raise_for_status()

    body = response.json()

    if body.get("code") != 200:

        raise RuntimeError(
            "WaveSpeed submit failed: "
            + json.dumps(
                body,
                ensure_ascii=False
            )
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
            "WaveSpeed returned no task ID: "
            + json.dumps(
                body,
                ensure_ascii=False
            )
        )

    log(
        f"WaveSpeed task created: "
        f"{task_id}"
    )

    return task_id


def wavespeed_wait(
    task_id,
    timeout=900
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed polling blocked "
            "because TEST_MODE=true"
        )

    started = time.time()

    result_url = (
        f"{WAVESPEED_BASE}/"
        f"predictions/"
        f"{task_id}/result"
    )

    while True:

        elapsed = (
            time.time() -
            started
        )

        if elapsed > timeout:

            raise TimeoutError(
                "WaveSpeed timeout: "
                f"{task_id}"
            )

        response = http_get(
            result_url,
            headers=get_headers(),
            timeout=30
        )

        response.raise_for_status()

        body = response.json()

        if body.get("code") != 200:

            raise RuntimeError(
                "WaveSpeed result error: "
                + json.dumps(
                    body,
                    ensure_ascii=False
                )
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
            f"Task {task_id}: "
            f"{status}"
        )

        if status == "completed":

            outputs = data.get(
                "outputs"
            )

            if not outputs:

                raise RuntimeError(
                    "WaveSpeed completed "
                    "without outputs: "
                    + json.dumps(
                        body,
                        ensure_ascii=False
                    )
                )

            first = outputs[0]

            if isinstance(
                first,
                dict
            ):

                for key in (
                    "url",
                    "audio_url",
                    "video_url",
                    "download_url"
                ):

                    if first.get(key):

                        return first[key]

                raise RuntimeError(
                    "Unknown WaveSpeed "
                    "output object: "
                    + json.dumps(
                        first,
                        ensure_ascii=False
                    )
                )

            return first

        if status in (
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        ):

            error = data.get(
                "error"
            )

            raise RuntimeError(
                "WaveSpeed task failed: "
                f"{error or body}"
            )

        time.sleep(2)


def upload_to_wavespeed(
    path
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed upload blocked "
            "because TEST_MODE=true"
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

    ticket_response = http_post(
        f"{WAVESPEED_BASE}/"
        "media/uploads",
        headers=auth_headers(),
        json={
            "filename":
                path.name,
            "size":
                path.stat().st_size
        },
        timeout=30
    )

    ticket_response.raise_for_status()

    body = ticket_response.json()

    if body.get("code") != 200:

        raise RuntimeError(
            "WaveSpeed upload ticket failed: "
            + json.dumps(
                body,
                ensure_ascii=False
            )
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
            "Invalid WaveSpeed upload response: "
            + json.dumps(
                body,
                ensure_ascii=False
            )
        )

    with path.open(
        "rb"
    ) as file:

        uploaded = requests.put(
            upload_url,
            headers=upload_headers,
            data=file,
            timeout=300
        )

    uploaded.raise_for_status()

    return download_url


def download_file(
    url,
    path
):

    path = Path(path)

    response = http_get(
        url,
        timeout=180,
        stream=True
    )

    response.raise_for_status()

    with path.open(
        "wb"
    ) as file:

        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):

            if chunk:

                file.write(
                    chunk
                )

    if not path.exists():

        raise RuntimeError(
            "Download failed: "
            f"{path}"
        )

    if path.stat().st_size <= 0:

        raise RuntimeError(
            "Downloaded empty file: "
            f"{path}"
        )

    return path


# =========================================================
# GROQ STORY
# =========================================================

def create_story(
    user_idea
):

    system = f"""
أنت كاتب سيناريو عربي ومخرج سينمائي
ومدير تصوير ومشرف استمرارية
ومصمم صوت.

أنت تنتج Microdrama عربي قصير جداً.

النوع:

Dark Fantasy + Romance + Mystery +
Suspense + Supernatural Drama.

العالم:

رجل غامض جذاب يمتلك قوة خارقة.

أميرة تقع في حبه.

ملك يعرف سراً خطيراً عنه.

ذئبة بيضاء صغيرة لا تتكلم.

الفكرة الأصلية:

{user_idea}

==================================================
SCENE COUNT
==================================================

يجب أن يكون:

EXACTLY {SHOT_COUNT} scenes.

كل Scene مدته:
{SHOT_DURATION} seconds.

إجمالي الحلقة:
{TOTAL_DURATION} seconds.

ممنوع:
2 scenes
3 scenes
5 scenes

يجب أن يكون العدد:
{SHOT_COUNT}

==================================================
STRUCTURE
==================================================

Scene 1:
HOOK

Scene 2:
ESCALATION

Scene 3:
REVEAL أو DANGER

Scene 4:
CLIFFHANGER

لا تحل السر بالكامل.

==================================================
VISUAL STYLE
==================================================

realistic cinematic Arabic fantasy drama,
photorealistic,
professional film lighting,
realistic skin,
realistic fabric,
realistic fur,
volumetric moonlight,
fog,
shallow depth of field,
anamorphic cinematic look,
rim light,
natural body movement,
natural head movement,
eye movement,
blinking,
mouth movement,
hands,
fingers,
breathing,
cloth movement,
hair movement,
realistic hands and eyes,
vertical 9:16,
high production value.

Negative:

anime,
cartoon,
illustration,
game art,
plastic skin,
text,
logo,
watermark.

==================================================
DIALOGUE
==================================================

كل Scene يحتوي متحدثاً بشرياً واحداً فقط.

الحوار قصير.

لا تجعل الحوار أطول من أن يناسب
5 ثوانٍ.

الذئبة لا تتكلم.

يمكن للذئبة استخدام:

whimper,
growl,
howl,
breathing.

==================================================
SOUND
==================================================

sound يجب أن يكون وصفاً تنفيذياً
للـ Foley وSound Design فقط.

يشمل عند الحاجة:

environment,
Foley,
footsteps,
cloth,
hair,
metal,
wood,
wolf sounds,
wind,
leaves,
whoosh,
impact,
supernatural power,
riser,
stinger.

ممنوع وضع الموسيقى داخل sound.

==================================================
MUSIC
==================================================

music يجب أن يكون وصفاً للموسيقى
السينمائية الآلية فقط.

ممنوع:

vocals,
lyrics,
spoken words.

==================================================
OUTPUT
==================================================

JSON فقط.

لا Markdown.

لا شرح.

لا كلام خارج JSON.

المفتاح scenes يجب أن يحتوي
بالضبط {SHOT_COUNT} عناصر.
"""

    user_prompt = f"""
حوّل الفكرة التالية إلى Microdrama سينمائي
من EXACTLY {SHOT_COUNT} scenes.

الفكرة:

{user_idea}

يجب أن تكون البنية:

1. Hook
2. Escalation
3. Reveal/Danger
4. Cliffhanger

أعد JSON بهذا الشكل:

{{
  "title": "...",
  "hook": "...",
  "visual_style": "...",
  "cast_reference_prompt": "...",
  "scenes": [
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "male_lead",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "princess",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "guard",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "male_lead",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }}
  ]
}}

speaker يجب أن يكون واحداً من:

male_lead
princess
king
guard
narrator

IMPORTANT:

scenes MUST contain exactly
{SHOT_COUNT} elements.
"""

    max_attempts = 4

    last_error = None

    for attempt in range(
        max_attempts
    ):

        try:

            prompt = user_prompt

            if attempt > 0:

                prompt = f"""
المحاولة السابقة أعادت عدداً خاطئاً
من المشاهد.

هذه المرة يجب الالتزام حرفياً:

EXACTLY {SHOT_COUNT} scenes.

لا تدمج المشاهد.

لا تحذف مشاهد.

لا تضف مشاهد.

استخدم:

1 = Hook
2 = Escalation
3 = Reveal/Danger
4 = Cliffhanger

الفكرة:

{user_idea}

أعد JSON فقط.

البنية:

{{
  "title": "...",
  "hook": "...",
  "visual_style": "...",
  "cast_reference_prompt": "...",
  "scenes": [
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "...",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "...",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "...",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }},
    {{
      "action": "...",
      "dialogue": [
        {{
          "speaker": "...",
          "text": "...",
          "emotion": "..."
        }}
      ],
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }}
  ]
}}
"""

            log(
                "Groq story attempt "
                f"{attempt + 1}/"
                f"{max_attempts}"
            )

            response = (
                groq
                .chat
                .completions
                .create(
                    model=GROQ_MODEL,
                    temperature=0.45,
                    response_format={
                        "type":
                            "json_object"
                    },
                    messages=[
                        {
                            "role":
                                "system",
                            "content":
                                system
                        },
                        {
                            "role":
                                "user",
                            "content":
                                prompt
                        }
                    ]
                )
            )

            content = (
                response
                .choices[0]
                .message
                .content
            )

            if not content:

                raise RuntimeError(
                    "Groq returned empty response."
                )

            content = content.strip()

            if content.startswith(
                "```"
            ):

                content = (
                    content
                    .replace(
                        "```json",
                        ""
                    )
                    .replace(
                        "```",
                        ""
                    )
                    .strip()
                )

            story = json.loads(
                content
            )

            if not isinstance(
                story,
                dict
            ):

                raise RuntimeError(
                    "Groq JSON is not object."
                )

            scenes = story.get(
                "scenes",
                []
            )

            if not isinstance(
                scenes,
                list
            ):

                raise RuntimeError(
                    "scenes is not list."
                )

            log(
                "Groq returned "
                f"{len(scenes)} scenes"
            )

            if len(scenes) != SHOT_COUNT:

                raise RuntimeError(
                    "Wrong scene count: "
                    f"{len(scenes)}"
                )

            valid_speakers = {
                "male_lead",
                "princess",
                "king",
                "guard",
                "narrator"
            }

            for index, scene in enumerate(
                scenes
            ):

                if not isinstance(
                    scene,
                    dict
                ):

                    raise RuntimeError(
                        f"Scene {index + 1} "
                        "is invalid."
                    )

                scene["action"] = str(
                    scene.get(
                        "action",
                        ""
                    )
                ).strip()

                if not scene["action"]:

                    scene["action"] = (
                        "Cinematic continuation."
                    )

                scene["scene_image_prompt"] = (
                    str(
                        scene.get(
                            "scene_image_prompt",
                            ""
                        )
                    ).strip()
                )

                if not scene[
                    "scene_image_prompt"
                ]:

                    scene[
                        "scene_image_prompt"
                    ] = story.get(
                        "visual_style",
                        ""
                    )

                scene["video_prompt"] = (
                    str(
                        scene.get(
                            "video_prompt",
                            ""
                        )
                    ).strip()
                )

                if not scene[
                    "video_prompt"
                ]:

                    scene[
                        "video_prompt"
                    ] = (
                        "Natural cinematic movement, "
                        "realistic breathing, eyes, "
                        "face, body, cloth and hair."
                    )

                scene["camera"] = str(
                    scene.get(
                        "camera",
                        ""
                    )
                ).strip()

                if not scene["camera"]:

                    scene["camera"] = (
                        "cinematic medium shot, "
                        "slow controlled camera movement"
                    )

                scene["sound"] = str(
                    scene.get(
                        "sound",
                        ""
                    )
                ).strip()

                if not scene["sound"]:

                    scene["sound"] = (
                        "night environment, wind, "
                        "leaves and subtle Foley"
                    )

                scene["music"] = str(
                    scene.get(
                        "music",
                        ""
                    )
                ).strip()

                if not scene["music"]:

                    scene["music"] = (
                        "dark cinematic Arabic "
                        "fantasy instrumental tension"
                    )

                dialogue = scene.get(
                    "dialogue",
                    []
                )

                if not isinstance(
                    dialogue,
                    list
                ):

                    dialogue = []

                if dialogue:

                    d = dialogue[0]

                    if not isinstance(
                        d,
                        dict
                    ):

                        d = {
                            "speaker":
                                "narrator",
                            "text":
                                "",
                            "emotion":
                                "neutral"
                        }

                else:

                    d = {
                        "speaker":
                            "narrator",
                        "text":
                            "",
                        "emotion":
                            "neutral"
                    }

                speaker = d.get(
                    "speaker",
                    "narrator"
                )

                if speaker not in valid_speakers:

                    speaker = "narrator"

                text_value = str(
                    d.get(
                        "text",
                        ""
                    )
                ).strip()

                emotion = str(
                    d.get(
                        "emotion",
                        "neutral"
                    )
                ).strip()

                scene["dialogue"] = [
                    {
                        "speaker":
                            speaker,
                        "text":
                            text_value,
                        "emotion":
                            emotion
                    }
                ]

            story["scenes"] = scenes

            log(
                "Groq story accepted: "
                f"{len(scenes)} scenes"
            )

            return story

        except Exception as error:

            last_error = error

            log(
                "Groq attempt failed: "
                f"{repr(error)}"
            )

            if attempt < (
                max_attempts - 1
            ):

                time.sleep(2)

    raise RuntimeError(
        "Groq story generation failed: "
        f"{last_error}"
    )


# =========================================================
# FFMPEG
# =========================================================

def run_cmd(
    command,
    timeout=300
):

    log(
        "CMD: "
        + " ".join(
            map(
                str,
                command
            )
        )
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:

        log(
            result.stderr[-8000:]
        )

        raise RuntimeError(
            "Command failed with code "
            f"{result.returncode}"
        )

    return result


def ffprobe_duration(
    path
):

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:"
            "nokey=1",
            str(path)
        ],
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


# =========================================================
# AUDIO HELPERS
# =========================================================

def normalize_audio_to_wav(
    input_file,
    output_file,
    duration=None
):

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-sample_fmt",
        "s16"
    ]

    if duration:

        command += [
            "-t",
            str(duration)
        ]

    command += [
        str(output_file)
    ]

    run_cmd(
        command,
        120
    )

    return output_file


def fit_audio(
    input_file,
    output_file,
    duration
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),
            "-af",
            "apad,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB",
            "-t",
            str(duration),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_file)
        ],
        120
    )

    return output_file


def silent(
    duration,
    output_file
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc="
            "channel_layout=stereo:"
            "sample_rate=48000",
            "-t",
            str(duration),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_file)
        ],
        120
    )

    return output_file


# =========================================================
# TTS
# =========================================================

async def _tts(
    text,
    voice,
    output,
    rate="-5%",
    pitch=None
):

    kwargs = {
        "text":
            text,
        "voice":
            voice,
        "rate":
            rate
    }

    if pitch:

        kwargs["pitch"] = pitch

    communicator = edge_tts.Communicate(
        **kwargs
    )

    await communicator.save(
        str(output)
    )


def create_voice_audio(
    text,
    speaker,
    output
):

    config = VOICE_CONFIG.get(
        speaker,
        VOICE_CONFIG["narrator"]
    )

    log(
        f"TTS {speaker}: {text}"
    )

    asyncio.run(
        _tts(
            text,
            config["voice"],
            output,
            config.get(
                "rate",
                "-5%"
            ),
            config.get(
                "pitch"
            )
        )
    )

    return output


# =========================================================
# TEST VIDEO
# =========================================================

def create_test_scene_video(
    index,
    output
):

    colors_a = [
        "0x17101f",
        "0x10202a",
        "0x201710",
        "0x111c14"
    ]

    colors_b = [
        "0x382345",
        "0x193d4a",
        "0x4b3018",
        "0x203c25"
    ]

    a = colors_a[
        index % len(colors_a)
    ]

    b = colors_b[
        index % len(colors_b)
    ]

    filter_graph = (
        f"color=c={a}:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"r={VIDEO_FPS}:"
        f"d={SHOT_DURATION},"
        "format=yuv420p,"
        "drawbox="
        "x='100+80*sin(t)':"
        "y='250+120*cos(t*0.7)':"
        "w=520:"
        "h=700:"
        f"color={b}@0.35:"
        "t=fill,"
        "drawbox="
        "x='220+100*cos(t*0.5)':"
        "y='500+70*sin(t)':"
        "w=280:"
        "h=280:"
        "color=white@0.07:"
        "t=fill"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            filter_graph,
            "-t",
            str(SHOT_DURATION),
            "-r",
            str(VIDEO_FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(output)
        ],
        120
    )

    return output


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_image(
    prompt,
    output
):

    if TEST_MODE:

        return None

    if not prompt.strip():

        raise RuntimeError(
            "Empty image prompt."
        )

    task_id = wavespeed_submit(
        IMAGE_MODEL,
        {
            "prompt":
                prompt,
            "size":
                "720*1280"
        }
    )

    url = wavespeed_wait(
        task_id,
        600
    )

    return download_file(
        url,
        output
    )


def generate_cast_reference(
    story,
    output
):

    prompt = story.get(
        "cast_reference_prompt"
    )

    if not prompt:

        prompt = """
Photorealistic cinematic Arabic
fantasy cast reference.

Exactly four recurring characters:

handsome mysterious supernatural man,

beautiful Arabian princess,

small white female wolf pup,

powerful Arab king.

Consistent identity,
consistent clothing,
consistent proportions.

Realistic skin,
realistic fabric,
realistic fur,
moonlight,
fog,
anamorphic film look,
high production value,
vertical 9:16.

No text.
No labels.
No logo.
No watermark.
No anime.
No cartoon.
No illustration.
No game art.
"""

    return generate_image(
        prompt,
        output
    )


# =========================================================
# VIDEO GENERATION
# =========================================================

def generate_scene_video(
    image_path,
    prompt,
    output
):

    if TEST_MODE:

        return None

    image_url = upload_to_wavespeed(
        image_path
    )

    full_prompt = f"""
{prompt}

Natural cinematic motion.

Subtle eyes.
Blinking.
Facial expression.
Mouth movement.
Breathing.
Head movement.
Shoulders.
Hands.
Fingers.
Body weight.
Walking when appropriate.
Hair.
Cloth.
Fog.
Animal fur.

Preserve exact identity,
face,
clothing,
anatomy,
environment.

No morphing.
No extra fingers.
No deformed hands.
No duplicated characters.
No text.
No watermark.
No logo.

Photorealistic cinematic
vertical 9:16.
"""

    task_id = wavespeed_submit(
        VIDEO_MODEL,
        {
            "image":
                image_url,
            "prompt":
                full_prompt,
            "duration":
                SHOT_DURATION,
            "resolution":
                "480p",
            "negative_prompt":
                "text, watermark, logo, "
                "deformed hands, extra fingers, "
                "duplicate characters, "
                "bad anatomy, morphing"
        }
    )

    url = wavespeed_wait(
        task_id,
        900
    )

    return download_file(
        url,
        output
    )


# =========================================================
# LIP SYNC
# =========================================================

def generate_lipsync_video(
    video,
    audio,
    output
):

    if (
        TEST_MODE
        or not LIPSYNC_ENABLED
    ):

        return video

    video_url = upload_to_wavespeed(
        video
    )

    audio_url = upload_to_wavespeed(
        audio
    )

    allowed_modes = {
        "lips",
        "face",
        "head"
    }

    mode = LIPSYNC_MODE

    if mode not in allowed_modes:

        mode = "face"

    allowed_emotions = {
        "happy",
        "sad",
        "angry",
        "disgusted",
        "surprised",
        "neutral"
    }

    emotion = DEFAULT_LIPSYNC_EMOTION

    if emotion not in allowed_emotions:

        emotion = "neutral"

    task_id = wavespeed_submit(
        LIPSYNC_MODEL,
        {
            "video":
                video_url,
            "audio":
                audio_url,
            "model_mode":
                mode,
            "emotion":
                emotion
        }
    )

    url = wavespeed_wait(
        task_id,
        900
    )

    return download_file(
        url,
        output
    )


# =========================================================
# MMAUDIO SFX
# =========================================================

def generate_scene_sfx(
    video,
    sound_prompt,
    index,
    workdir
):

    if (
        not SOUND_DESIGN_ENABLED
        or TEST_MODE
        or not video
    ):

        return None

    video_url = upload_to_wavespeed(
        video
    )

    prompt = f"""
Create cinematic synchronized
sound design for this exact
5-second video.

Visible-action cue sheet:

{sound_prompt}

Include only realistic
cinematic sound design:

environment,
Foley,
footsteps,
cloth,
hair,
wood,
metal,
animal sounds,
wolf breathing,
wolf whimper,
wolf growl,
wolf howl when visible,
wind,
leaves,
whooshes,
impacts,
supernatural energy,
low rumbles,
risers,
stingers.

Match timing to visible movement.

NO speech.
NO dialogue.
NO narration.
NO singing.
NO lyrics.
NO music.
NO melody.
NO voice.

Professional cinematic film sound.
Natural dynamics.
No clipping.
"""

    payload = {
        "video":
            video_url,

        "prompt":
            prompt,

        "duration":
            SHOT_DURATION,

        # Correct current WaveSpeed MMAudio API name
        "num_inference_steps":
            MMAUDIO_STEPS,

        "guidance_scale":
            MMAUDIO_GUIDANCE,

        "negative_prompt":
            "speech, dialogue, narration, "
            "singing, lyrics, music, melody, "
            "voice, distortion, clipping, "
            "digital noise",

        "mask_away_clip":
            False
    }

    task_id = wavespeed_submit(
        SFX_MODEL,
        payload
    )

    raw = (
        workdir /
        f"sfx_{index:02d}_raw"
    )

    normalized = (
        workdir /
        f"sfx_{index:02d}_norm.wav"
    )

    url = wavespeed_wait(
        task_id,
        900
    )

    download_file(
        url,
        raw
    )

    return normalize_audio_to_wav(
        raw,
        normalized,
        SHOT_DURATION
    )


# =========================================================
# DIALOGUE TRACK
# =========================================================

def build_dialogue_track(
    scene_audio,
    duration,
    workdir
):

    valid = []

    for index, item in enumerate(
        scene_audio
    ):

        if not item:

            continue

        speaker, audio = item

        if audio and Path(audio).exists():

            valid.append(
                (
                    index,
                    audio
                )
            )

    output = (
        workdir /
        "dialogue_track.wav"
    )

    if not valid:

        return silent(
            duration,
            output
        )

    command = [
        "ffmpeg",
        "-y"
    ]

    filters = []
    labels = []

    for n, (
        index,
        audio
    ) in enumerate(
        valid
    ):

        command += [
            "-i",
            str(audio)
        ]

        label = f"d{n}"

        delay = (
            index *
            SHOT_DURATION *
            1000
        )

        filters.append(
            f"[{n}:a]"
            f"adelay={delay}|{delay},"
            "aresample=48000,"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo"
            f"[{label}]"
        )

        labels.append(
            f"[{label}]"
        )

    if len(labels) == 1:

        filters.append(
            f"{labels[0]}"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[d]"
        )

    else:

        filters.append(
            "".join(labels)
            + f"amix="
            f"inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[d]"
        )

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[d]",
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output)
    ]

    run_cmd(
        command,
        180
    )

    return output


# =========================================================
# SFX TRACK
# =========================================================

def build_sfx_track(
    files,
    duration,
    workdir
):

    valid = []

    for index, file in enumerate(
        files
    ):

        if (
            file
            and Path(file).exists()
        ):

            valid.append(
                (
                    index,
                    file
                )
            )

    output = (
        workdir /
        "sfx_track.wav"
    )

    if not valid:

        return silent(
            duration,
            output
        )

    command = [
        "ffmpeg",
        "-y"
    ]

    filters = []
    labels = []

    for n, (
        index,
        file
    ) in enumerate(
        valid
    ):

        command += [
            "-i",
            str(file)
        ]

        label = f"s{n}"

        delay = (
            index *
            SHOT_DURATION *
            1000
        )

        filters.append(
            f"[{n}:a]"
            f"adelay={delay}|{delay},"
            "aresample=48000,"
            f"volume={SFX_VOLUME},"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo"
            f"[{label}]"
        )

        labels.append(
            f"[{label}]"
        )

    if len(labels) == 1:

        filters.append(
            f"{labels[0]}"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[s]"
        )

    else:

        filters.append(
            "".join(labels)
            + f"amix="
            f"inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[s]"
        )

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[s]",
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output)
    ]

    run_cmd(
        command,
        180
    )

    return output


# =========================================================
# LOCAL AMBIENCE
# =========================================================

def create_local_ambience(
    duration,
    output
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc="
            "color=brown:"
            "amplitude=0.015:"
            "sample_rate=48000",
            "-af",
            "highpass=f=35,"
            "lowpass=f=5000,"
            "volume=0.18,"
            f"atrim=0:{duration}",
            "-t",
            str(duration),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output)
        ],
        120
    )

    return output


# =========================================================
# MUSIC
# =========================================================

def build_music_prompt(
    story,
    scenes
):

    cues = "\n".join(
        f"Scene {i + 1}: "
        f"{scene.get('music', '')}"
        for i, scene in enumerate(
            scenes
        )
        if scene.get("music")
    )

    return f"""
Instrumental cinematic score for
Arabic dark fantasy romance mystery.

Title:
{story.get('title', 'Dark Arabic Fantasy')}

Mood:

romantic mystery,
supernatural dread,
ancient secret,
night forest,
royal palace,
forbidden love,
danger,
emotional tension,
slow escalation,
unresolved cliffhanger.

Instrumentation:

oud-like plucked texture,
low cinematic strings,
deep cello,
soft frame drum,
subtle Arabic percussion,
atmospheric pads,
distant choir-like texture
WITHOUT WORDS,
deep sub bass,
sparse piano,
metallic supernatural textures.

Begin intimate and mysterious.

Gradually increase tension.

Use darker harmonic movement.

Build toward supernatural revelation.

End with a strong unresolved cliffhanger.

Absolutely instrumental.

No vocals.
No lyrics.
No spoken words.

Scene cues:

{cues}
"""


def generate_music(
    story,
    scenes,
    duration,
    workdir
):

    if (
        not MUSIC_ENABLED
        or TEST_MODE
    ):

        return None

    prompt = build_music_prompt(
        story,
        scenes
    )

    payload = {
        "prompt":
            prompt,

        "duration":
            int(
                max(
                    5,
                    min(
                        240,
                        duration
                    )
                )
            ),

        "instrumental":
            True,

        "seed":
            24117
    }

    task_id = wavespeed_submit(
        MUSIC_MODEL,
        payload
    )

    raw = (
        workdir /
        "music_raw"
    )

    output = (
        workdir /
        "music.wav"
    )

    url = wavespeed_wait(
        task_id,
        900
    )

    download_file(
        url,
        raw
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-af",
            f"aresample=48000,"
            f"volume={MUSIC_VOLUME},"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB",
            "-t",
            str(duration),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output)
        ],
        180
    )

    return output


# =========================================================
# FINAL AUDIO MIX
# =========================================================

def mix_final_audio(
    dialogue,
    sfx,
    music,
    ambience,
    duration,
    output
):

    inputs = [
        dialogue,
        sfx,
        music,
        ambience
    ]

    command = [
        "ffmpeg",
        "-y"
    ]

    for item in inputs:

        if (
            item
            and Path(item).exists()
        ):

            command += [
                "-i",
                str(item)
            ]

        else:

            command += [
                "-f",
                "lavfi",
                "-t",
                str(duration),
                "-i",
                "anullsrc="
                "channel_layout=stereo:"
                "sample_rate=48000"
            ]

    filters = [

        "[0:a]"
        "aresample=48000,"
        f"volume={VOICE_VOLUME},"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[v]",

        "[1:a]"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[s]",

        "[2:a]"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[m]",

        "[3:a]"
        "aresample=48000,"
        f"volume={AMBIENCE_VOLUME},"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[a]",

        "[v][s][m][a]"
        "amix="
        "inputs=4:"
        "duration=longest:"
        "dropout_transition=0,"
        "alimiter="
        "limit=0.95:"
        "attack=5:"
        "release=50,"
        "aresample=48000"
        "[mix]"
    ]

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[mix]",
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output)
    ]

    run_cmd(
        command,
        240
    )

    return output


# =========================================================
# VIDEO NORMALIZATION
# =========================================================

def normalize_scene_video(
    input_file,
    output_file
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),
            "-vf",
            f"scale={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "(ow-iw)/2:"
            "(oh-ih)/2,"
            "format=yuv420p",
            "-r",
            str(VIDEO_FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-t",
            str(SHOT_DURATION),
            str(output_file)
        ],
        180
    )

    return output_file


# =========================================================
# CONCAT
# =========================================================

def concat_videos(
    files,
    output
):

    list_file = (
        Path(output).parent /
        "video_concat.txt"
    )

    lines = []

    for video in files:

        absolute = str(
            Path(video)
            .resolve()
        )

        absolute = (
            absolute
            .replace(
                "'",
                "'\\''"
            )
        )

        lines.append(
            f"file '{absolute}'"
        )

    list_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    run_cmd(
        [
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
            str(output)
        ],
        180
    )

    return output


# =========================================================
# ADD AUDIO
# =========================================================

def add_audio_to_video(
    video,
    audio,
    duration,
    output
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
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
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
            str(output)
        ],
        240
    )

    return output


# =========================================================
# SUBTITLES
# =========================================================

def ass_escape(
    value
):

    return (
        str(value)
        .replace(
            "\\",
            "\\\\"
        )
        .replace(
            "{",
            "\\{"
        )
        .replace(
            "}",
            "\\}"
        )
    )


def ass_time(
    seconds
):

    seconds = max(
        0,
        float(seconds)
    )

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    remaining = (
        seconds % 60
    )

    sec = int(
        remaining
    )

    centiseconds = int(
        round(
            (
                remaining -
                sec
            ) * 100
        )
    )

    if centiseconds >= 100:

        sec += 1
        centiseconds = 0

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{sec:02d}."
        f"{centiseconds:02d}"
    )


def build_scene_meta(
    scenes
):

    result = []

    for index, scene in enumerate(
        scenes
    ):

        dialogue = scene.get(
            "dialogue",
            []
        )

        if not dialogue:

            continue

        text = str(
            dialogue[0].get(
                "text",
                ""
            )
        ).strip()

        if not text:

            continue

        result.append(
            {
                "start":
                    index *
                    SHOT_DURATION,

                "end":
                    (
                        index + 1
                    ) *
                    SHOT_DURATION,

                "text":
                    text
            }
        )

    return result


def create_ass(
    metadata,
    output
):

    lines = [

        "[Script Info]",

        "ScriptType: v4.00+",

        "PlayResX: 720",

        "PlayResY: 1280",

        "",

        "[V4+ Styles]",

        "Format: Name, Fontname, "
        "Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, "
        "MarginV, Encoding",

        "Style: Default,Arial,15,"
        "&H00FFFFFF,&H00FFFFFF,"
        "&H80000000,&H00000000,"
        "0,0,0,0,100,100,0,0,"
        "1,1,1,2,55,55,65,1",

        "",

        "[Events]",

        "Format: Layer, Start, End, "
        "Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text"
    ]

    for item in metadata:

        lines.append(
            "Dialogue: 0,"
            f"{ass_time(item['start'])},"
            f"{ass_time(item['end'])},"
            "Default,,0,0,0,,"
            f"{ass_escape(item['text'])}"
        )

    Path(output).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    return output


def burn_subtitles(
    video,
    ass,
    output
):

    ass_path = str(
        Path(ass)
        .resolve()
    )

    escaped = (
        ass_path
        .replace(
            "\\",
            "\\\\"
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

    duration = ffprobe_duration(
        video
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"subtitles='{escaped}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-t",
            str(duration),
            str(output)
        ],
        240
    )

    return output


# =========================================================
# PRODUCE EPISODE
# =========================================================

def produce_episode(
    story,
    workdir
):

    workdir = Path(workdir)

    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    scenes = story.get(
        "scenes",
        []
    )

    if not isinstance(
        scenes,
        list
    ):

        raise RuntimeError(
            "Story scenes invalid."
        )

    if len(scenes) != SHOT_COUNT:

        raise RuntimeError(
            "Production requires exactly "
            f"{SHOT_COUNT} scenes, got "
            f"{len(scenes)}"
        )

    if PRODUCTION_SCENE_LIMIT > 0:

        scenes = scenes[
            :min(
                PRODUCTION_SCENE_LIMIT,
                len(scenes)
            )
        ]

    if not scenes:

        raise RuntimeError(
            "No scenes available."
        )

    count = len(
        scenes
    )

    duration = (
        count *
        SHOT_DURATION
    )

    log(
        f"Producing {count} scenes "
        f"({duration}s)"
    )

    # =====================================================
    # CAST REFERENCE
    # =====================================================

    if not TEST_MODE:

        generate_cast_reference(
            story,
            workdir /
            "cast_reference.png"
        )

    # =====================================================
    # DIALOGUE
    # Generate ONCE per scene
    # =====================================================

    scene_audio = []

    for index, scene in enumerate(
        scenes
    ):

        dialogue = scene.get(
            "dialogue",
            []
        )

        if dialogue:

            d = dialogue[0]

            text = str(
                d.get(
                    "text",
                    ""
                )
            ).strip()

            speaker = d.get(
                "speaker",
                "narrator"
            )

        else:

            text = ""
            speaker = "narrator"

        output = (
            workdir /
            f"scene_{index:02d}_"
            "voice_fit.wav"
        )

        if text:

            mp3 = (
                workdir /
                f"scene_{index:02d}_"
                "voice.mp3"
            )

            wav = (
                workdir /
                f"scene_{index:02d}_"
                "voice.wav"
            )

            create_voice_audio(
                text,
                speaker,
                mp3
            )

            normalize_audio_to_wav(
                mp3,
                wav
            )

            fit_audio(
                wav,
                output,
                SHOT_DURATION
            )

        else:

            silent(
                SHOT_DURATION,
                output
            )

        scene_audio.append(
            (
                speaker,
                output
            )
        )

    # =====================================================
    # VIDEO SCENES
    # =====================================================

    scene_videos = []
    sfx_files = []

    for index, scene in enumerate(
        scenes
    ):

        log(
            "================================"
        )

        log(
            f"SCENE {index + 1}/{count}"
        )

        log(
            "================================"
        )

        base = (
            workdir /
            f"scene_{index:02d}_base.mp4"
        )

        lipsync_output = (
            workdir /
            f"scene_{index:02d}_"
            "lipsync.mp4"
        )

        normalized = (
            workdir /
            f"scene_{index:02d}_"
            "normalized.mp4"
        )

        # -------------------------------------------------
        # TEST VIDEO
        # -------------------------------------------------

        if TEST_MODE:

            create_test_scene_video(
                index,
                base
            )

        else:

            image = (
                workdir /
                f"scene_{index:02d}.png"
            )

            generate_image(
                scene.get(
                    "scene_image_prompt",
                    ""
                ),
                image
            )

            generate_scene_video(
                image,
                scene.get(
                    "video_prompt",
                    ""
                ),
                base
            )

        # -------------------------------------------------
        # LIPSYNC
        # -------------------------------------------------

        speaker, audio = (
            scene_audio[index]
        )

        should_lipsync = (
            LIPSYNC_ENABLED
            and not TEST_MODE
            and index < MAX_LIPSYNC_SCENES
            and speaker in {
                "male_lead",
                "princess",
                "king",
                "guard",
                "narrator"
            }
            and ffprobe_duration(
                audio
            ) > 0
        )

        if should_lipsync:

            generate_lipsync_video(
                base,
                audio,
                lipsync_output
            )

            source_video = (
                lipsync_output
            )

        else:

            source_video = base

        # -------------------------------------------------
        # NORMALIZE
        # -------------------------------------------------

        normalize_scene_video(
            source_video,
            normalized
        )

        scene_videos.append(
            normalized
        )

        # -------------------------------------------------
        # SFX
        # -------------------------------------------------

        if (
            SOUND_DESIGN_ENABLED
            and not TEST_MODE
        ):

            sfx = generate_scene_sfx(
                normalized,
                scene.get(
                    "sound",
                    ""
                ),
                index,
                workdir
            )

        else:

            sfx = None

        sfx_files.append(
            sfx
        )

    # =====================================================
    # CONCAT
    # =====================================================

    concat = (
        workdir /
        "episode_video.mp4"
    )

    concat_videos(
        scene_videos,
        concat
    )

    # =====================================================
    # AUDIO
    # =====================================================

    dialogue_track = (
        build_dialogue_track(
            scene_audio,
            duration,
            workdir
        )
    )

    sfx_track = (
        build_sfx_track(
            sfx_files,
            duration,
            workdir
        )
    )

    if (
        MUSIC_ENABLED
        and not TEST_MODE
    ):

        music = generate_music(
            story,
            scenes,
            duration,
            workdir
        )

    else:

        music = None

    ambience = (
        workdir /
        "ambience.wav"
    )

    create_local_ambience(
        duration,
        ambience
    )

    # =====================================================
    # FINAL AUDIO
    # =====================================================

    final_audio = (
        workdir /
        "final_audio.m4a"
    )

    mix_final_audio(
        dialogue_track,
        sfx_track,
        music,
        ambience,
        duration,
        final_audio
    )

    # =====================================================
    # VIDEO + AUDIO
    # =====================================================

    video_audio = (
        workdir /
        "video_audio.mp4"
    )

    add_audio_to_video(
        concat,
        final_audio,
        duration,
        video_audio
    )

    # =====================================================
    # SUBTITLES
    # =====================================================

    ass = (
        workdir /
        "subtitles.ass"
    )

    create_ass(
        build_scene_meta(
            scenes
        ),
        ass
    )

    final = (
        workdir /
        "ABOSARAJ_FINAL.mp4"
    )

    burn_subtitles(
        video_audio,
        ass,
        final
    )

    # =====================================================
    # VALIDATE
    # =====================================================

    if not final.exists():

        raise RuntimeError(
            "Final video does not exist."
        )

    file_size = (
        final.stat().st_size
    )

    if file_size <= 0:

        raise RuntimeError(
            "Final video is empty."
        )

    final_duration = (
        ffprobe_duration(
            final
        )
    )

    if final_duration <= 0:

        raise RuntimeError(
            "Final video duration invalid."
        )

    log(
        "================================"
    )

    log(
        "FINAL VIDEO READY"
    )

    log(
        f"Path: {final}"
    )

    log(
        f"Duration: "
        f"{final_duration:.2f}s"
    )

    log(
        f"Size: "
        f"{file_size / 1024 / 1024:.2f} MB"
    )

    log(
        "================================"
    )

    return {
        "video":
            final,

        "duration":
            final_duration,

        "scene_count":
            count
    }


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    payload=None,
    files=None
):

    response = requests.post(
        (
            "https://api.telegram.org/"
            f"bot{BOT_TOKEN}/{method}"
        ),
        data=payload or {},
        files=files,
        timeout=180
    )

    response.raise_for_status()

    body = response.json()

    if not body.get("ok"):

        raise RuntimeError(
            "Telegram API error: "
            + json.dumps(
                body,
                ensure_ascii=False
            )
        )

    return body


def send_message(
    chat_id,
    text
):

    return telegram_api(
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

    path = Path(path)

    with path.open(
        "rb"
    ) as video:

        return telegram_api(
            "sendVideo",
            {
                "chat_id":
                    chat_id,

                "caption":
                    caption,

                "supports_streaming":
                    "true"
            },
            {
                "video": (
                    path.name,
                    video,
                    "video/mp4"
                )
            }
        )


# =========================================================
# TEST STORY
# =========================================================

def default_test_story():

    return """
أميرة تهرب ليلًا من القصر بعد أن يقرر
والدها الملك تزويجها لرجل لا تحبه.

في الغابة تجد ذئبة بيضاء صغيرة مصابة
وتحاول مساعدتها.

يظهر أمامها رجل غامض وجذاب كانت قد
رأته من قبل، ويخبرها أن عليها العودة
للقصر لأن رجال الملك يبحثون عنها.

ترفض العودة وتكتشف أن الرجل يمتلك قوة
خارقة يحاول إخفاءها.

يصل أحد حراس الملك ويأمر الأميرة بالعودة،
لكن الرجل يقف أمامها ويحميها بطريقة
تكشف جزءًا من قوته.

قبل نهاية المشهد ترفع الذئبة الصغيرة
رأسها وتنظر للرجل وكأنها تعرفه منذ زمن،
ثم تطلق عواءً غريبًا يجعل وجه الرجل
يتغير فجأة.

من أين تعرف الذئبة هذا الرجل؟
"""


# =========================================================
# STATUS
# =========================================================

def production_status_text():

    scene_count = (
        SHOT_COUNT
        if PRODUCTION_SCENE_LIMIT <= 0
        else min(
            PRODUCTION_SCENE_LIMIT,
            SHOT_COUNT
        )
    )

    duration = (
        scene_count *
        SHOT_DURATION
    )

    return (
        f"Scenes: {scene_count}\n"
        f"Duration: {duration}s\n"
        f"Test Mode: "
        f"{'ON' if TEST_MODE else 'OFF'}\n"
        f"Cinematic SFX: "
        f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'OFF'}\n"
        f"Cinematic Score: "
        f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'OFF'}\n"
        f"Lip-sync: "
        f"{'ON' if LIPSYNC_ENABLED and not TEST_MODE else 'OFF'}"
    )


# =========================================================
# PROCESS
# =========================================================

def process_story_for_chat(
    chat_id,
    idea
):

    with processing_lock:

        if chat_id in processing_chats:

            send_message(
                chat_id,
                "⏳ عندي حلقة قيد "
                "المعالجة بالفعل."
            )

            return

        processing_chats.add(
            chat_id
        )

    workdir = Path(
        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        send_message(
            chat_id,
            "🎬 Abosaraj بدأ الإنتاج...\n\n"
            "✍️ السيناريو\n"
            "🎭 الشخصيات\n"
            "🎥 المشاهد\n"
            "🎙️ الأصوات\n"
            "🔊 Sound Design\n"
            "🎵 الموسيقى\n"
            "📝 الترجمة\n\n"
            "استنى شوي..."
        )

        # =================================================
        # STORY
        # =================================================

        story = create_story(
            idea
        )

        story_file = (
            workdir /
            "story.json"
        )

        story_file.write_text(
            json.dumps(
                story,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        send_message(
            chat_id,
            "🎬 السيناريو جاهز.\n"
            f"📖 {story.get('title', 'Untitled')}\n\n"
            "هلا بنبني الحلقة."
        )

        # =================================================
        # PRODUCTION
        # =================================================

        result = produce_episode(
            story,
            workdir
        )

        caption = (
            "🎬 ABOSARAJ\n\n"
            f"📖 {story.get('title', 'حلقة جديدة')}\n"
            f"🎞️ {result['scene_count']} مشاهد\n"
            f"⏱️ {result['duration']:.1f} ثانية\n\n"
            "🎙️ Multi-Character Voices: ON\n"
            f"🔊 Cinematic SFX: "
            f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'OFF'}\n"
            f"🎵 Cinematic Score: "
            f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'OFF'}\n"
            "📝 Arabic Subtitles: ON"
        )

        if (
            LIPSYNC_ENABLED
            and not TEST_MODE
        ):

            caption += (
                "\n👄 Lip Sync: ON"
            )

        send_video(
            chat_id,
            result["video"],
            caption
        )

        send_message(
            chat_id,
            "✅ الحلقة وصلت."
        )

    except Exception as error:

        log(
            "PRODUCTION ERROR: "
            + repr(error)
        )

        try:

            send_message(
                chat_id,
                "❌ صار خطأ:\n"
                f"{type(error).__name__}: "
                f"{error}\n\n"
                "ابعتلي آخر Render Logs."
            )

        except Exception as telegram_error:

            log(
                "Telegram error: "
                + repr(telegram_error)
            )

    finally:

        with processing_lock:

            processing_chats.discard(
                chat_id
            )

        # =================================================
        # OPTIONAL CLEANUP
        # =================================================

        cleanup = envbool(
            "CLEANUP_WORKDIR",
            "false"
        )

        if cleanup:

            try:

                shutil.rmtree(
                    workdir,
                    ignore_errors=True
                )

                log(
                    f"Workdir cleaned: "
                    f"{workdir}"
                )

            except Exception as error:

                log(
                    "Cleanup error: "
                    + repr(error)
                )

        else:

            log(
                f"Workdir kept: "
                f"{workdir}"
            )


def start_processing(
    chat_id,
    idea
):

    thread = threading.Thread(
        target=
        process_story_for_chat,
        args=(
            chat_id,
            idea
        ),
        daemon=True
    )

    thread.start()


# =========================================================
# FLASK
# =========================================================

@app.get("/")
def home():

    return {
        "status":
            "ok",

        "service":
            "Abosaraj",

        "version":
            "2026-10-09-02",

        "test_mode":
            TEST_MODE,

        "sound_design":
            SOUND_DESIGN_ENABLED,

        "music":
            MUSIC_ENABLED,

        "lipsync":
            LIPSYNC_ENABLED
    }


@app.get("/health")
def health():

    return {
        "status":
            "healthy",

        "service":
            "abosaraj",

        "version":
            "2026-10-09-02",

        "test_mode":
            TEST_MODE,

        "wavespeed_configured":
            bool(
                WAVESPEED_API_KEY
            ),

        "groq_configured":
            bool(
                GROQ_API_KEY
            ),

        "sound_design_enabled":
            SOUND_DESIGN_ENABLED,

        "music_enabled":
            MUSIC_ENABLED,

        "lipsync_enabled":
            LIPSYNC_ENABLED,

        "lipsync_mode":
            LIPSYNC_MODE,

        "max_lipsync_scenes":
            MAX_LIPSYNC_SCENES,

        "production_scene_limit":
            PRODUCTION_SCENE_LIMIT,

        "scene_count":
            SHOT_COUNT,

        "scene_duration":
            SHOT_DURATION,

        "total_duration":
            TOTAL_DURATION
    }


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post("/webhook")
@app.post("/telegram/webhook")
def webhook():

    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    message = (
        update.get(
            "message"
        )
        or {}
    )

    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text"
    )

    log(
        "Telegram update received: "
        f"chat={chat_id}, "
        f"text={text!r}"
    )

    if not chat_id:

        return {
            "ok":
                True
        }

    if not text:

        return {
            "ok":
                True
        }

    text = text.strip()

    # =====================================================
    # START / HELP
    # =====================================================

    if text in (
        "/start",
        "/help"
    ):

        send_message(
            chat_id,
            "🎬 أهلاً في Abosaraj.\n\n"
            "ابعثلي فكرة القصة وأنا أحولها "
            "إلى Microdrama سينمائي.\n\n"
            "أو اكتب /test لتشغيل قصة الاختبار.\n\n"
            "الأوامر:\n"
            "/test\n"
            "/status"
        )

        return {
            "ok":
                True
        }

    # =====================================================
    # TEST
    # =====================================================

    if text == "/test":

        send_message(
            chat_id,
            "🧪 اختبار Abosaraj بدأ."
        )

        start_processing(
            chat_id,
            default_test_story()
        )

        return {
            "ok":
                True
        }

    # =====================================================
    # STATUS
    # =====================================================

    if text == "/status":

        send_message(
            chat_id,
            "🤖 Abosaraj Status\n\n"
            + production_status_text()
            + "\n\nWaveSpeed: "
            + (
                "READY"
                if WAVESPEED_API_KEY
                else "NO KEY"
            )
            + "\nGroq: "
            + (
                "READY"
                if GROQ_API_KEY
                else "NO KEY"
            )
        )

        return {
            "ok":
                True
        }

    # =====================================================
    # SHORT TEXT
    # =====================================================

    if len(text) < 10:

        send_message(
            chat_id,
            "اكتب فكرة قصة أطول شوي."
        )

        return {
            "ok":
                True
        }

    # =====================================================
    # NORMAL STORY
    # =====================================================

    send_message(
        chat_id,
        "🎬 وصلت الفكرة.\n"
        "ببدأ تحويلها إلى سيناريو سينمائي..."
    )

    start_processing(
        chat_id,
        text
    )

    return {
        "ok":
            True
    }


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if TEST_MODE:

        log(
            "TEST_MODE=true — "
            "webhook setup skipped."
        )

        return

    if not RENDER_EXTERNAL_URL:

        log(
            "RENDER_EXTERNAL_URL "
            "not configured."
        )

        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL}"
        "/webhook"
    )

    try:

        response = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{BOT_TOKEN}/"
                "setWebhook"
            ),
            data={
                "url":
                    webhook_url
            },
            timeout=30
        )

        log(
            "Webhook response: "
            + response.text
        )

        response.raise_for_status()

    except Exception as error:

        log(
            "Webhook setup failed: "
            + repr(error)
        )


# =========================================================
# STARTUP
# =========================================================

def print_startup():

    log(
        "========================================"
    )

    log(
        "ABOSARAJ AI VIDEO BOT"
    )

    log(
        "CODE_VERSION="
        "2026-10-09-FINAL-CHECK-02"
    )

    log(
        "========================================"
    )

    values = {

        "TEST_MODE":
            TEST_MODE,

        "WAVESPEED":
            bool(
                WAVESPEED_API_KEY
            ),

        "GROQ":
            bool(
                GROQ_API_KEY
            ),

        "SOUND_DESIGN":
            SOUND_DESIGN_ENABLED,

        "MUSIC":
            MUSIC_ENABLED,

        "LIPSYNC":
            LIPSYNC_ENABLED,

        "LIPSYNC_MODE":
            LIPSYNC_MODE,

        "MAX_LIPSYNC_SCENES":
            MAX_LIPSYNC_SCENES,

        "PRODUCTION_SCENE_LIMIT":
            PRODUCTION_SCENE_LIMIT,

        "SHOT_COUNT":
            SHOT_COUNT,

        "SHOT_DURATION":
            SHOT_DURATION,

        "TOTAL_DURATION":
            TOTAL_DURATION,

        "MMAUDIO_STEPS":
            MMAUDIO_STEPS,

        "MMAUDIO_GUIDANCE":
            MMAUDIO_GUIDANCE
    }

    for key, value in values.items():

        log(
            f"{key}={value}"
        )

    log(
        "AUDIO_PIPELINE="
        "SINGLE_TTS_GENERATION"
    )

    log(
        "GROQ_MODE=json_object"
    )

    log(
        "GROQ_SCENE_RETRY=4"
    )

    log(
        "WAVESPEED_MMAUDIO="
        "num_inference_steps"
    )

    log(
        "========================================"
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print_startup()

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
