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
# ABOSARAJ AI CINEMATIC STORY BOT
# VERSION: 2026-10-09-CINEMATIC-01
#
# IMPORTANT DESIGN:
#
# NO NARRATOR.
#
# Every scene is performed by a CHARACTER.
#
# Each character has:
#   - fixed identity
#   - fixed voice
#   - personality
#   - emotional behavior
#   - speaking style
#
# Story -> Character screenplay -> Scene visuals
#       -> Character TTS -> Video -> SFX -> Music
#       -> Final mix -> Arabic title
#
# COST-SAFE DESIGN:
#
# TEST_MODE=true by default.
#
# When TEST_MODE=true:
#   - NO WaveSpeed task
#   - NO WaveSpeed upload
#   - NO image generation
#   - NO video generation
#   - NO lip-sync
#   - NO paid SFX
#   - NO paid music
#
# Edge-TTS is used for dialogue testing.
#
# =========================================================


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


def envbool(key, default="false"):
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
# COST CONTROL
# =========================================================

# KEEP TRUE UNTIL THE WHOLE PIPELINE IS VERIFIED.
TEST_MODE = envbool(
    "TEST_MODE",
    "true"
)

# Number of scenes.
# Four scenes = four video generations in production.
SHOT_COUNT = int(
    os.getenv(
        "SHOT_COUNT",
        "4"
    )
)

SHOT_DURATION = int(
    os.getenv(
        "SHOT_DURATION",
        "5"
    )
)

# During the first paid test, keep this to 1.
# 0 = all scenes.
PRODUCTION_SCENE_LIMIT = int(
    os.getenv(
        "PRODUCTION_SCENE_LIMIT",
        "0"
    )
)

# Lip sync costs additional generation/upload operations.
# KEEP OFF initially.
LIPSYNC_ENABLED = envbool(
    "LIPSYNC_ENABLED",
    "false"
)

MAX_LIPSYNC_SCENES = int(
    os.getenv(
        "MAX_LIPSYNC_SCENES",
        "0"
    )
)

LIPSYNC_MODE = os.getenv(
    "LIPSYNC_MODE",
    "face"
).lower()

LIPSYNC_EMOTION = os.getenv(
    "LIPSYNC_EMOTION",
    "neutral"
).lower()


# =========================================================
# SOUND DESIGN
# =========================================================

# Paid MMAudio.
# KEEP OFF while testing.
SOUND_DESIGN_ENABLED = envbool(
    "SOUND_DESIGN_ENABLED",
    "false"
)

MMAUDIO_STEPS = int(
    os.getenv(
        "MMAUDIO_STEPS",
        "20"
    )
)

MMAUDIO_GUIDANCE = float(
    os.getenv(
        "MMAUDIO_GUIDANCE",
        "4.0"
    )
)

# Paid music generation.
# KEEP OFF while testing.
MUSIC_ENABLED = envbool(
    "MUSIC_ENABLED",
    "false"
)

MUSIC_VOLUME = float(
    os.getenv(
        "MUSIC_VOLUME",
        "0.10"
    )
)

SFX_VOLUME = float(
    os.getenv(
        "SFX_VOLUME",
        "0.55"
    )
)

AMBIENCE_VOLUME = float(
    os.getenv(
        "AMBIENCE_VOLUME",
        "0.10"
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

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

TOTAL_DURATION = (
    SHOT_COUNT *
    SHOT_DURATION
)


# =========================================================
# WAVESPEED
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

processing_lock = threading.Lock()

processing_chats = set()


# =========================================================
# LOG
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
# CHARACTER BIBLE
# =========================================================

CHARACTER_BIBLE = """
CHARACTER CONTINUITY BIBLE

The production is a live-action cinematic fantasy microdrama.

There is NO NARRATOR.

Characters communicate through their own spoken dialogue.

==================================================
CHARACTER 1 — MALE LEAD
==================================================

ID:
male_lead

Visual identity:

A handsome mysterious Middle Eastern fantasy man,
late 20s to early 30s,
strong masculine face,
dark slightly long wavy black hair,
deep brown eyes,
light olive skin,
short well-groomed dark beard,
athletic lean build,
dark charcoal medieval-fantasy coat,
black leather details,
dark boots,
subtle ancient supernatural silver ornament.

Personality:

quiet,
confident,
protective,
mysterious,
intelligent,
emotionally controlled.

Speaking style:

short,
deep,
controlled,
confident,
natural Arabic dialogue.

Never sounds like a narrator.

==================================================
CHARACTER 2 — PRINCESS
==================================================

ID:
princess

Visual identity:

Beautiful Middle Eastern / Arabian princess,
mid 20s,
warm olive skin,
large expressive brown eyes,
long dark brown wavy hair,
elegant royal facial features,
slim natural build,
deep burgundy medieval-fantasy royal dress,
subtle gold embroidery,
delicate royal jewelry.

Personality:

intelligent,
brave,
emotional,
kind,
curious,
strong when necessary.

Speaking style:

natural,
emotional,
expressive,
sometimes soft,
sometimes frightened,
sometimes determined.

Never sounds like a narrator.

==================================================
CHARACTER 3 — KING
==================================================

ID:
king

Visual identity:

Powerful Middle Eastern king,
late 50s,
strong mature face,
gray-black beard,
dark brown eyes,
olive skin,
broad shoulders,
heavy dark royal robe,
deep burgundy and black colors,
subtle gold royal embroidery,
heavy royal ring.

Personality:

authoritative,
protective,
secretive,
proud,
politically intelligent.

Speaking style:

slow,
deep,
commanding,
controlled.

Never sounds like a narrator.

==================================================
CHARACTER 4 — GUARD
==================================================

ID:
guard

Visual identity:

Professional royal guard,
Middle Eastern appearance,
late 20s to late 30s,
athletic build,
dark royal armor,
black leather,
burgundy cloth details,
realistic metal armor.

Personality:

loyal,
serious,
obedient,
alert.

Speaking style:

direct,
short,
commanding.

Never sounds like a narrator.

==================================================
CHARACTER 5 — WHITE WOLF PUP
==================================================

ID:
wolf

Very small female white wolf pup.

Realistic thick white fur.
Slightly gray ears.
Blue-gray eyes.
Small young proportions.
Realistic canine anatomy.

The wolf NEVER speaks human language.

Allowed sounds:

whimper,
growl,
soft howl,
breathing.

==================================================
ABSOLUTE RULE
==================================================

NO NARRATOR.

Do not create:

narrator
voice-over narrator
exposition narrator
storyteller

The viewer must understand the story through:

dialogue,
acting,
visual action,
environment,
sound effects,
music,
facial expressions,
camera work.

==================================================
CONTINUITY
==================================================

Never redesign characters.

Never change:

face,
hair,
age,
clothing,
colors,
body proportions,
jewelry,
beard,
fur.

No duplicate main characters.

No random extra main characters.

==================================================
VISUAL STYLE
==================================================

Live action.

Photorealistic fantasy.

Real human skin.
Real fabric.
Real fur.
Realistic eyes.
Realistic hands.
Realistic anatomy.

Professional cinematic lighting.

Volumetric light.

Natural shadows.

Atmospheric fog.

Realistic environment.

Shallow depth of field.

High-end fantasy film photography.

Vertical 9:16.

NOT:

anime
cartoon
illustration
game art
plastic skin
CGI-looking humans
text
logos
watermarks
subtitles
black bars
letterbox
pillarbox
"""


# =========================================================
# VOICE CONFIG
# =========================================================
#
# NO NARRATOR.
#
# Every human character has a fixed voice.
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
    }
}


VALID_SPEAKERS = {
    "male_lead",
    "princess",
    "king",
    "guard",
    "wolf"
}


# =========================================================
# PERSONALITY BIBLE
# =========================================================

CHARACTER_PERSONALITIES = {

    "male_lead":
        "mysterious, protective, confident, controlled",

    "princess":
        "intelligent, brave, emotional, kind",

    "king":
        "authoritative, secretive, proud, protective",

    "guard":
        "loyal, serious, obedient, alert",

    "wolf":
        "innocent, frightened, loyal"
}


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

        except Exception as error:

            last_error = error

            log(
                "HTTP GET retry "
                f"{attempt + 1}/3: "
                f"{repr(error)}"
            )

            if attempt < 2:

                time.sleep(
                    2 * (attempt + 1)
                )

    raise last_error


def http_post_no_retry(
    url,
    **kwargs
):

    try:

        return requests.post(
            url,
            **kwargs
        )

    except Exception as error:

        log(
            "HTTP POST failed WITHOUT retry: "
            f"{repr(error)}"
        )

        raise


# =========================================================
# WAVESPEED SUBMIT
# =========================================================

def wavespeed_submit(
    model,
    payload
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed blocked: TEST_MODE=true"
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
        f"WAVESPEED CREATE ONCE: {model}"
    )

    response = http_post_no_retry(
        url,
        headers=auth_headers(),
        json=payload,
        timeout=(10, 120)
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
        f"WAVESPEED TASK CREATED: {task_id}"
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
            "WaveSpeed polling blocked: TEST_MODE=true"
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
                f"WaveSpeed timeout: {task_id}"
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
            f"TASK {task_id}: {status}"
        )

        if status == "completed":

            outputs = data.get(
                "outputs"
            )

            if not outputs:

                raise RuntimeError(
                    "Task completed without outputs."
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
                    "Unknown output object: "
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

            raise RuntimeError(
                "WaveSpeed task failed: "
                + json.dumps(
                    data,
                    ensure_ascii=False
                )
            )

        time.sleep(2)


# =========================================================
# WAVESPEED UPLOAD
# =========================================================

def upload_to_wavespeed(
    path
):

    if TEST_MODE:

        raise RuntimeError(
            "WaveSpeed upload blocked: TEST_MODE=true"
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

    ticket_response = http_post_no_retry(
        f"{WAVESPEED_BASE}/media/uploads",
        headers=auth_headers(),
        json={
            "filename":
                path.name,
            "size":
                path.stat().st_size
        },
        timeout=(10, 60)
    )

    ticket_response.raise_for_status()

    body = ticket_response.json()

    if body.get("code") != 200:

        raise RuntimeError(
            "Upload ticket failed: "
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
            "Invalid upload response: "
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


# =========================================================
# DOWNLOAD
# =========================================================

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
            "Downloaded file does not exist."
        )

    if path.stat().st_size <= 0:

        raise RuntimeError(
            "Downloaded file is empty."
        )

    return path


# =========================================================
# GROQ HELPERS
# =========================================================

def clean_json_text(
    content
):

    content = (
        content or ""
    ).strip()

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

    return content


# =========================================================
# STORY ENGINE
# =========================================================

def create_story(
    user_idea
):

    system_prompt = f"""
أنت الآن تعمل كـ:

كاتب سيناريو سينمائي
+
مخرج
+
مصمم شخصيات
+
مشرف استمرارية
+
مصمم حوار
+
مصمم مشاهد
+
مشرف صوت.

نحن نصنع Microdrama عربي قصير
يشبه فيلمًا حقيقيًا.

IMPORTANT:

لا يوجد راوي إطلاقًا.

لا Narrator.

لا Voice-over يشرح الأحداث.

القصة يجب أن يفهمها المشاهد من:

الشخصيات
الحوار
التصرفات
الصورة
الكاميرا
المؤثرات
الموسيقى
المشاعر.

{CHARACTER_BIBLE}

==================================================
USER STORY
==================================================

{user_idea}

==================================================
STRUCTURE
==================================================

EXACTLY {SHOT_COUNT} scenes.

Each scene:
approximately {SHOT_DURATION} seconds.

Total:
approximately {TOTAL_DURATION} seconds.

Structure:

Scene 1:
Strong visual hook.

Scene 2:
Conflict grows.

Scene 3:
Important reveal or danger.

Scene 4:
Cliffhanger.

==================================================
DIALOGUE
==================================================

Each scene must have ONE primary speaker.

This is important for clean voice identity
and future lip-sync.

The speaker must be a real character.

Allowed:

male_lead
princess
king
guard
wolf

NEVER:

narrator

The wolf can only make animal sounds.

Dialogue must be natural.

Do NOT write exposition.

BAD:

"أنا ذاهب إلى القصر لأن الملك غاضب."

BETTER:

"إذا رجعت للقصر الآن... سيقتلونني."

The dialogue should feel like actors
talking to each other.

Keep dialogue short enough for the scene.

==================================================
EMOTION
==================================================

Every dialogue segment needs:

emotion

Examples:

fear
anger
whisper
love
confusion
determination
surprise
sadness
confidence
threat
panic
relief

==================================================
CHARACTER PERFORMANCE
==================================================

For each scene specify:

action
speaker
dialogue
emotion
facial_expression
body_language

==================================================
VISUAL
==================================================

Each scene must specify:

scene_image_prompt
video_prompt
camera

The visual prompt must explicitly identify
which recurring characters are visible.

Do not create characters who are not in the story.

==================================================
SOUND
==================================================

sound contains:

environment
Foley
movement
objects
animal sounds
impacts
wind
leaves
cloth
metal
supernatural effects

DO NOT put dialogue inside sound.

==================================================
MUSIC
==================================================

music is instrumental only.

No vocals.
No lyrics.
No spoken words.

==================================================
TITLE
==================================================

Create a short Arabic title.

The final video will display this title
at the bottom during the final seconds.

==================================================
JSON ONLY
==================================================
"""

    user_prompt = f"""
حوّل هذه الفكرة إلى فيلم قصير:

{user_idea}

أعد JSON فقط بهذا الشكل:

{{
  "title": "عنوان عربي قصير",
  "genre": "نوع القصة",
  "visual_style": "وصف الأسلوب",
  "cast": [
    {{
      "id": "male_lead",
      "role": "...",
      "personality": "...",
      "speaking_style": "..."
    }}
  ],
  "scenes": [
    {{
      "scene_number": 1,
      "purpose": "hook",
      "duration": {SHOT_DURATION},
      "visible_characters": [
        "princess"
      ],
      "action": "...",
      "speaker": "princess",
      "dialogue": "...",
      "emotion": "...",
      "facial_expression": "...",
      "body_language": "...",
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "...",
      "music": "..."
    }}
  ]
}}

CRITICAL:

scenes = EXACTLY {SHOT_COUNT}

لا تزيد.
لا تنقص.

speaker لا يمكن أن يكون narrator.

لا يوجد narrator في أي مكان.

القصة يجب أن تكون حوارية سينمائية.

كل شخصية تتكلم بنفس شخصيتها.

لا تجعل الجميع يتكلم بنفس الأسلوب.
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
REPAIR THE JSON.

Previous attempt was invalid.

You MUST return:

EXACTLY {SHOT_COUNT} scenes.

NO narrator.

Every scene must have:

scene_number
purpose
duration
visible_characters
action
speaker
dialogue
emotion
facial_expression
body_language
scene_image_prompt
video_prompt
camera
sound
music

Allowed speakers:

male_lead
princess
king
guard
wolf

Never use narrator.

Original idea:

{user_idea}

{CHARACTER_BIBLE}
"""

            log(
                f"GROQ STORY ATTEMPT "
                f"{attempt + 1}/{max_attempts}"
            )

            response = (
                groq
                .chat
                .completions
                .create(
                    model=GROQ_MODEL,
                    temperature=0.35,
                    response_format={
                        "type":
                            "json_object"
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

            story = json.loads(
                clean_json_text(
                    content
                )
            )

            if not isinstance(
                story,
                dict
            ):

                raise RuntimeError(
                    "Story JSON is not an object."
                )

            scenes = story.get(
                "scenes"
            )

            if not isinstance(
                scenes,
                list
            ):

                raise RuntimeError(
                    "scenes is not a list."
                )

            if len(scenes) != SHOT_COUNT:

                raise RuntimeError(
                    "Wrong scene count: "
                    f"{len(scenes)}"
                )

            # ---------------------------------------------
            # Validate scenes
            # ---------------------------------------------

            for index, scene in enumerate(
                scenes
            ):

                if not isinstance(
                    scene,
                    dict
                ):

                    raise RuntimeError(
                        f"Scene {index + 1} invalid."
                    )

                speaker = str(
                    scene.get(
                        "speaker",
                        ""
                    )
                ).strip().lower()

                if speaker == "narrator":

                    raise RuntimeError(
                        "NARRATOR DETECTED."
                    )

                if speaker not in VALID_SPEAKERS:

                    raise RuntimeError(
                        f"Invalid speaker: {speaker}"
                    )

                scene["speaker"] = speaker

                scene["dialogue"] = str(
                    scene.get(
                        "dialogue",
                        ""
                    )
                ).strip()

                # A human scene must actually speak.
                # Wolf may use an animal sound.
                if not scene["dialogue"]:

                    if speaker == "wolf":

                        scene["dialogue"] = (
                            "whimper"
                        )

                    else:

                        raise RuntimeError(
                            f"Scene {index + 1} "
                            "has no dialogue."
                        )

                scene["duration"] = (
                    SHOT_DURATION
                )

                scene["action"] = str(
                    scene.get(
                        "action",
                        ""
                    )
                ).strip()

                if not scene["action"]:

                    scene["action"] = (
                        "Natural cinematic character action."
                    )

                scene["emotion"] = str(
                    scene.get(
                        "emotion",
                        "neutral"
                    )
                ).strip()

                scene["facial_expression"] = str(
                    scene.get(
                        "facial_expression",
                        "natural expression"
                    )
                ).strip()

                scene["body_language"] = str(
                    scene.get(
                        "body_language",
                        "natural body movement"
                    )
                ).strip()

                scene["scene_image_prompt"] = (
                    str(
                        scene.get(
                            "scene_image_prompt",
                            ""
                        )
                    ).strip()
                )

                scene["video_prompt"] = (
                    str(
                        scene.get(
                            "video_prompt",
                            ""
                        )
                    ).strip()
                )

                scene["camera"] = str(
                    scene.get(
                        "camera",
                        ""
                    )
                ).strip()

                scene["sound"] = str(
                    scene.get(
                        "sound",
                        ""
                    )
                ).strip()

                scene["music"] = str(
                    scene.get(
                        "music",
                        ""
                    )
                ).strip()

                # -----------------------------------------
                # Continuity injection
                # -----------------------------------------

                character_description = (
                    get_character_description(
                        speaker
                    )
                )

                scene["scene_image_prompt"] = (
                    CHARACTER_BIBLE
                    + "\n\n"
                    + "PRIMARY SPEAKER:\n"
                    + character_description
                    + "\n\n"
                    + "SCENE:\n"
                    + scene[
                        "scene_image_prompt"
                    ]
                )

                scene["video_prompt"] = (
                    CHARACTER_BIBLE
                    + "\n\n"
                    + "PRIMARY SPEAKER:\n"
                    + character_description
                    + "\n\n"
                    + "ACTION:\n"
                    + scene[
                        "action"
                    ]
                    + "\n\n"
                    + "FACIAL EXPRESSION:\n"
                    + scene[
                        "facial_expression"
                    ]
                    + "\n\n"
                    + "BODY LANGUAGE:\n"
                    + scene[
                        "body_language"
                    ]
                    + "\n\n"
                    + "VIDEO MOTION:\n"
                    + scene[
                        "video_prompt"
                    ]
                )

            story["scenes"] = scenes

            story["title"] = str(
                story.get(
                    "title",
                    "حكاية غامضة"
                )
            ).strip()

            story["title"] = (
                story["title"]
                or "حكاية غامضة"
            )

            log(
                "GROQ STORY ACCEPTED"
            )

            return story

        except Exception as error:

            last_error = error

            log(
                "GROQ STORY ERROR: "
                f"{repr(error)}"
            )

            if attempt < (
                max_attempts - 1
            ):

                time.sleep(2)

    raise RuntimeError(
        "Story generation failed: "
        f"{last_error}"
    )


# =========================================================
# CHARACTER DESCRIPTION
# =========================================================

def get_character_description(
    speaker
):

    descriptions = {

        "male_lead":
            """
Male lead:
handsome mysterious Middle Eastern man,
late 20s to early 30s,
dark slightly long wavy black hair,
deep brown eyes,
light olive skin,
short dark beard,
athletic lean body,
dark charcoal fantasy coat,
black leather details,
dark boots,
silver supernatural ornament.
""",

        "princess":
            """
Princess:
beautiful Middle Eastern Arabian woman,
mid 20s,
warm olive skin,
large expressive brown eyes,
long dark brown wavy hair,
burgundy medieval fantasy royal dress,
subtle gold embroidery,
delicate royal jewelry.
""",

        "king":
            """
King:
powerful Middle Eastern man,
late 50s,
gray-black beard,
dark brown eyes,
olive skin,
broad shoulders,
heavy dark royal robe,
burgundy and black colors,
gold royal embroidery.
""",

        "guard":
            """
Royal guard:
athletic Middle Eastern man,
late 20s to late 30s,
dark royal armor,
black leather,
burgundy cloth details,
realistic metal armor.
""",

        "wolf":
            """
Small female white wolf pup,
thick white fur,
slightly gray ears,
blue-gray eyes,
small young proportions,
realistic canine anatomy.
"""
    }

    return descriptions.get(
        speaker,
        ""
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
            "Command failed: "
            f"code={result.returncode}"
        )

    return result


# =========================================================
# FFPROBE
# =========================================================

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
# AUDIO
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
        "2",
        "-ar",
        "48000",
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
# EDGE TTS
# =========================================================

async def _tts(
    text,
    voice,
    output,
    rate="-5%",
    pitch="+0Hz"
):

    communicator = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )

    await communicator.save(
        str(output)
    )


def create_voice_audio(
    text,
    speaker,
    output
):

    # Wolf is not human TTS.
    if speaker == "wolf":

        raise RuntimeError(
            "Wolf requires animal SFX, "
            "not human TTS."
        )

    config = VOICE_CONFIG.get(
        speaker
    )

    if not config:

        raise RuntimeError(
            f"No voice configured for {speaker}"
        )

    log(
        f"TTS [{speaker}] "
        f"{text}"
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
                "pitch",
                "+0Hz"
            )
        )
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
            "amplitude=0.012:"
            "sample_rate=48000",
            "-af",
            "highpass=f=35,"
            "lowpass=f=5000,"
            "volume=0.12,"
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
# TEST VIDEO
# =========================================================

def create_test_scene_video(
    index,
    scene,
    output
):

    speaker = scene.get(
        "speaker",
        "princess"
    )

    backgrounds = [
        ("0x15101c", "0x42294d"),
        ("0x101c25", "0x1f4554"),
        ("0x20160f", "0x55341d"),
        ("0x111b14", "0x274d32")
    ]

    a, b = backgrounds[
        index % len(backgrounds)
    ]

    # ---------------------------------------------
    # Speaker badge text.
    #
    # This is ONLY for TEST MODE so we can visually
    # verify who owns each scene.
    #
    # Production scenes do NOT contain this badge.
    # ---------------------------------------------

    safe_speaker = (
        speaker
        .replace(
            ":",
            ""
        )
        .replace(
            "'",
            ""
        )
    )

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
        f"color={b}@0.38:"
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

    final_prompt = f"""
{CHARACTER_BIBLE}

Create one vertical 9:16
photorealistic live-action
cinematic fantasy frame.

SCENE:

{prompt}

The primary speaking character must
look exactly consistent with the
character bible.

Natural human proportions.

Natural hands.

Natural eyes.

Natural skin.

Real fabric.

Real environment.

No text.
No subtitles.
No logo.
No watermark.
No black bars.
No letterbox.
No pillarbox.
"""

    task_id = wavespeed_submit(
        IMAGE_MODEL,
        {
            "prompt":
                final_prompt,
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

    final_prompt = f"""
{CHARACTER_BIBLE}

SCENE ACTION:

{prompt}

Create realistic cinematic motion.

The speaking character performs
natural facial movement.

Natural eye movement.
Blinking.
Breathing.
Head movement.
Shoulder movement.
Hands.
Fingers.
Hair movement.
Cloth movement.

Camera movement should match the emotion.

Use:

slow push-in
tracking
subtle handheld
close-up
medium shot
wide shot

only when appropriate.

Preserve character identity.

No morphing.

No extra characters.

No duplicate characters.

No deformed hands.

No text.

No subtitles.

No logo.

No watermark.

No black bars.
No letterbox.
No pillarbox.
"""

    task_id = wavespeed_submit(
        VIDEO_MODEL,
        {
            "image":
                image_url,

            "prompt":
                final_prompt,

            "duration":
                SHOT_DURATION,

            "resolution":
                "480p",

            "negative_prompt":
                "text, subtitles, watermark, "
                "logo, black bars, letterbox, "
                "pillarbox, deformed hands, "
                "extra fingers, duplicate characters, "
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
# LIPSYNC
# =========================================================

def generate_lipsync_video(
    video,
    audio,
    emotion,
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

    mode = (
        LIPSYNC_MODE
        if LIPSYNC_MODE in allowed_modes
        else "face"
    )

    allowed_emotions = {
        "happy",
        "sad",
        "angry",
        "disgusted",
        "surprised",
        "neutral"
    }

    emotion_value = (
        emotion
        if emotion in allowed_emotions
        else "neutral"
    )

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
                emotion_value
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
# SFX
# =========================================================

def generate_scene_sfx(
    video,
    sound_prompt,
    index,
    workdir
):

    if (
        TEST_MODE
        or not SOUND_DESIGN_ENABLED
        or not video
    ):

        return None

    video_url = upload_to_wavespeed(
        video
    )

    prompt = f"""
Create cinematic synchronized
sound effects for this exact video.

SOUND CUE:

{sound_prompt}

Only sound effects.

Environment.
Foley.
Footsteps.
Cloth.
Hair.
Wood.
Metal.
Wind.
Leaves.
Animal sounds.
Wolf breathing.
Wolf whimper.
Wolf growl.
Wolf howl.
Whoosh.
Impact.
Supernatural energy.
Low rumble.
Riser.
Stinger.

Synchronize sounds with visible movement.

NO dialogue.
NO speech.
NO narration.
NO music.
NO singing.
NO lyrics.
NO voice.
"""

    task_id = wavespeed_submit(
        SFX_MODEL,
        {
            "video":
                video_url,

            "prompt":
                prompt,

            "duration":
                SHOT_DURATION,

            "num_inference_steps":
                int(MMAUDIO_STEPS),

            "guidance_scale":
                float(MMAUDIO_GUIDANCE),

            "negative_prompt":
                "speech, dialogue, narration, "
                "voice, music, singing, lyrics"
        }
    )

    raw = (
        workdir /
        f"sfx_{index:02d}_raw"
    )

    output = (
        workdir /
        f"sfx_{index:02d}.wav"
    )

    url = wavespeed_wait(
        task_id,
        900
    )

    download_file(
        url,
        raw
    )

    normalize_audio_to_wav(
        raw,
        output,
        SHOT_DURATION
    )

    return output


# =========================================================
# BUILD DIALOGUE TRACK
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

        if (
            audio
            and Path(audio).exists()
        ):

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
# BUILD SFX TRACK
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
    )

    return f"""
Instrumental cinematic fantasy score.

Arabic dark fantasy.

Mystery.
Romance.
Danger.
Suspense.
Supernatural tension.

Title:
{story.get('title', '')}

The music must support dialogue.

Use:

low strings,
cello,
oud-like plucked textures,
subtle Arabic percussion,
atmospheric pads,
deep cinematic bass,
sparse piano,
dark supernatural textures.

Gradually increase tension.

No vocals.
No lyrics.
No spoken words.
No narration.

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
        TEST_MODE
        or not MUSIC_ENABLED
    ):

        return None

    task_id = wavespeed_submit(
        MUSIC_MODEL,
        {
            "prompt":
                build_music_prompt(
                    story,
                    scenes
                ),

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
# AUDIO MIX
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

    video_filter = (
        f"scale={VIDEO_WIDTH}:"
        f"{VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:"
        f"{VIDEO_HEIGHT}:"
        "(iw-ow)/2:"
        "(ih-oh)/2,"
        "setsar=1,"
        "format=yuv420p"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_file),
            "-vf",
            video_filter,
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

    if not files:

        raise RuntimeError(
            "No videos to concatenate."
        )

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
# ARABIC TITLE
# =========================================================
#
# The title is shown ONLY at the end.
#
# No subtitles.
# No dialogue text.
#
# We use FFmpeg drawtext.
#
# If the server has DejaVu Sans installed,
# Arabic rendering should work when FFmpeg was built
# with FriBidi support.
# =========================================================

def find_font():

    candidates = [

        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans.ttf",

        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans-Bold.ttf",

        "/usr/share/fonts/truetype/freefont/"
        "FreeSans.ttf",

        "/usr/share/fonts/truetype/noto/"
        "NotoSansArabic-Regular.ttf",

        "/usr/share/fonts/truetype/noto/"
        "NotoSansArabic-Bold.ttf"
    ]

    for candidate in candidates:

        if Path(candidate).exists():

            return candidate

    return None


def escape_drawtext(
    text
):

    return (
        str(text)
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
        .replace(
            "%",
            "\\%"
        )
    )


def add_arabic_title(
    video,
    title,
    duration,
    output
):

    title = str(
        title or "حكاية"
    ).strip()

    font = find_font()

    if not font:

        log(
            "Arabic title font not found. "
            "Keeping video without title."
        )

        shutil.copyfile(
            video,
            output
        )

        return output

    escaped_title = escape_drawtext(
        title
    )

    # Last 2.5 seconds.
    title_start = max(
        0,
        duration - 2.5
    )

    drawtext = (
        "drawtext="
        f"fontfile='{font}':"
        f"text='{escaped_title}':"
        "fontcolor=white:"
        "fontsize=42:"
        "borderw=3:"
        "bordercolor=black@0.75:"
        "x=(w-text_w)/2:"
        "y=h-145:"
        f"enable='gte(t,{title_start})':"
        "alpha='if(lt(t,"
        f"{title_start}+0.5),"
        f"(t-{title_start})/0.5,"
        "if(gt(t,"
        f"{duration}-0.5),"
        f"({duration}-t)/0.5,1))'"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vf",
            drawtext,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
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
# DEFAULT TEST STORY
# =========================================================

def default_test_story():

    return """
الأميرة تهرب من القصر في منتصف الليل
بعد أن يقرر الملك إجبارها على الزواج.

في الغابة تجد ذئبة بيضاء صغيرة مصابة.

يظهر الرجل الغامض الذي كانت قد رأته
من قبل، ويحاول إقناعها بالعودة.

ترفض الأميرة.

يصل حارس من القصر ويأمرها بالعودة.

يقف الرجل أمامها لحمايتها،
ويظهر جزء من قوته الخارقة.

قبل النهاية، ترفع الذئبة رأسها
وتنظر إلى الرجل وكأنها تعرفه.

ثم تطلق عواءً غريبًا.

يتغير وجه الرجل فجأة.

تنتهي الحلقة قبل أن نعرف
ما سر الذئبة والرجل.
"""


# =========================================================
# PRODUCE EPISODE
# =========================================================

def produce_episode(
    story,
    workdir
):

    workdir = Path(
        workdir
    )

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
            "Invalid story scenes."
        )

    if len(scenes) != SHOT_COUNT:

        raise RuntimeError(
            "Production expected "
            f"{SHOT_COUNT} scenes, got "
            f"{len(scenes)}"
        )

    # ---------------------------------------------
    # Cost-safe scene limit
    # ---------------------------------------------

    if PRODUCTION_SCENE_LIMIT > 0:

        scenes = scenes[
            :min(
                PRODUCTION_SCENE_LIMIT,
                len(scenes)
            )
        ]

    count = len(
        scenes
    )

    duration = (
        count *
        SHOT_DURATION
    )

    log(
        f"PRODUCTION: "
        f"{count} scenes / "
        f"{duration}s"
    )

    # =====================================================
    # DIALOGUE
    # =====================================================

    scene_audio = []

    for index, scene in enumerate(
        scenes
    ):

        speaker = scene.get(
            "speaker"
        )

        dialogue = str(
            scene.get(
                "dialogue",
                ""
            )
        ).strip()

        emotion = str(
            scene.get(
                "emotion",
                "neutral"
            )
        ).strip()

        log(
            f"SCENE {index + 1} "
            f"SPEAKER={speaker} "
            f"EMOTION={emotion}"
        )

        output = (
            workdir /
            f"scene_{index:02d}_voice.wav"
        )

        # ---------------------------------------------
        # Wolf:
        # no human TTS.
        # ---------------------------------------------

        if speaker == "wolf":

            # Simple local animal-like placeholder
            # during testing.
            #
            # Production can replace this with
            # generated animal SFX.
            run_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine="
                    "frequency=420:"
                    "sample_rate=48000",
                    "-af",
                    "volume=0.05,"
                    "afade=t=in:st=0:d=0.15,"
                    f"afade=t=out:st={max(0, SHOT_DURATION-0.5)}:d=0.5",
                    "-t",
                    str(SHOT_DURATION),
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(output)
                ],
                120
            )

        else:

            raw_mp3 = (
                workdir /
                f"scene_{index:02d}_voice.mp3"
            )

            create_voice_audio(
                dialogue,
                speaker,
                raw_mp3
            )

            normalize_audio_to_wav(
                raw_mp3,
                output
            )

            fit_audio(
                output,
                output,
                SHOT_DURATION
            )

        scene_audio.append(
            (
                speaker,
                output,
                emotion
            )
        )

    # =====================================================
    # VIDEO
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
            f"SPEAKER = "
            f"{scene.get('speaker')}"
        )

        log(
            f"DIALOGUE = "
            f"{scene.get('dialogue')}"
        )

        log(
            "================================"
        )

        base = (
            workdir /
            f"scene_{index:02d}_base.mp4"
        )

        normalized = (
            workdir /
            f"scene_{index:02d}_normalized.mp4"
        )

        lipsync_video = (
            workdir /
            f"scene_{index:02d}_lipsync.mp4"
        )

        # ---------------------------------------------
        # TEST
        # ---------------------------------------------

        if TEST_MODE:

            create_test_scene_video(
                index,
                scene,
                base
            )

        # ---------------------------------------------
        # REAL
        # ---------------------------------------------

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

        # =================================================
        # LIPSYNC
        # =================================================

        source_video = base

        speaker, audio, emotion = (
            scene_audio[index]
        )

        should_lipsync = (
            LIPSYNC_ENABLED
            and not TEST_MODE
            and MAX_LIPSYNC_SCENES > 0
            and index < MAX_LIPSYNC_SCENES
            and speaker in {
                "male_lead",
                "princess",
                "king",
                "guard"
            }
        )

        if should_lipsync:

            log(
                f"LIPSYNC ATTEMPT "
                f"scene {index + 1}"
            )

            try:

                result = (
                    generate_lipsync_video(
                        base,
                        audio,
                        emotion,
                        lipsync_video
                    )
                )

                if (
                    result
                    and Path(result).exists()
                    and Path(result).stat().st_size > 0
                ):

                    source_video = result

                    log(
                        f"LIPSYNC SUCCESS "
                        f"scene {index + 1}"
                    )

                else:

                    log(
                        "LIPSYNC INVALID. "
                        "Using original video."
                    )

            except Exception as error:

                log(
                    "LIPSYNC FAILED: "
                    f"{repr(error)}"
                )

                log(
                    "FALLBACK -> ORIGINAL VIDEO"
                )

        # =================================================
        # NORMALIZE
        # =================================================

        normalize_scene_video(
            source_video,
            normalized
        )

        scene_videos.append(
            normalized
        )

        # =================================================
        # SFX
        # =================================================

        if (
            SOUND_DESIGN_ENABLED
            and not TEST_MODE
        ):

            try:

                sfx = generate_scene_sfx(
                    normalized,
                    scene.get(
                        "sound",
                        ""
                    ),
                    index,
                    workdir
                )

            except Exception as error:

                log(
                    f"SFX FAILED scene "
                    f"{index + 1}: "
                    f"{repr(error)}"
                )

                sfx = None

        else:

            sfx = None

        sfx_files.append(
            sfx
        )

    # =====================================================
    # CONCAT VIDEO
    # =====================================================

    concat_video = (
        workdir /
        "episode_video.mp4"
    )

    concat_videos(
        scene_videos,
        concat_video
    )

    # =====================================================
    # AUDIO TRACKS
    # =====================================================

    dialogue_track = (
        build_dialogue_track(
            [
                (
                    item[0],
                    item[1]
                )
                for item in scene_audio
            ],
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

    # =====================================================
    # MUSIC
    # =====================================================

    if (
        MUSIC_ENABLED
        and not TEST_MODE
    ):

        try:

            music = generate_music(
                story,
                scenes,
                duration,
                workdir
            )

        except Exception as error:

            log(
                "MUSIC FAILED: "
                f"{repr(error)}"
            )

            music = None

    else:

        music = None

    # =====================================================
    # AMBIENCE
    # =====================================================

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

    video_with_audio = (
        workdir /
        "video_with_audio.mp4"
    )

    add_audio_to_video(
        concat_video,
        final_audio,
        duration,
        video_with_audio
    )

    # =====================================================
    # TITLE
    # =====================================================

    final_video = (
        workdir /
        "ABOSARAJ_FINAL.mp4"
    )

    add_arabic_title(
        video_with_audio,
        story.get(
            "title",
            "حكاية"
        ),
        duration,
        final_video
    )

    # =====================================================
    # VALIDATION
    # =====================================================

    if not final_video.exists():

        raise RuntimeError(
            "Final video does not exist."
        )

    size = (
        final_video.stat().st_size
    )

    if size <= 0:

        raise RuntimeError(
            "Final video is empty."
        )

    final_duration = (
        ffprobe_duration(
            final_video
        )
    )

    if final_duration <= 0:

        raise RuntimeError(
            "Invalid final duration."
        )

    log(
        "========================================"
    )

    log(
        "FINAL VIDEO READY"
    )

    log(
        f"TITLE={story.get('title')}"
    )

    log(
        f"DURATION={final_duration:.2f}s"
    )

    log(
        f"SIZE={size / 1024 / 1024:.2f} MB"
    )

    log(
        "NARRATOR=OFF"
    )

    log(
        "SUBTITLES=OFF"
    )

    log(
        "ARABIC_TITLE=ON"
    )

    log(
        "========================================"
    )

    return {
        "video":
            final_video,

        "duration":
            final_duration,

        "scene_count":
            count,

        "title":
            story.get(
                "title",
                "حكاية"
            )
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
# STATUS
# =========================================================

def production_status_text():

    count = (
        SHOT_COUNT
        if PRODUCTION_SCENE_LIMIT <= 0
        else min(
            PRODUCTION_SCENE_LIMIT,
            SHOT_COUNT
        )
    )

    duration = (
        count *
        SHOT_DURATION
    )

    return (
        f"🎬 Scenes: {count}\n"
        f"⏱ Duration: {duration}s\n"
        f"🧪 Test Mode: "
        f"{'ON' if TEST_MODE else 'OFF'}\n"
        f"🎭 Character Voices: ON\n"
        f"🎙 Narrator: OFF\n"
        f"👄 Lip-sync: "
        f"{'ON' if LIPSYNC_ENABLED and not TEST_MODE else 'OFF'}\n"
        f"🔊 SFX: "
        f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'OFF'}\n"
        f"🎵 Music: "
        f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'OFF'}\n"
        "📝 Subtitles: OFF\n"
        "🏷 Arabic Title: ON\n"
        "⬛ Padding: OFF\n"
        "💰 Paid WaveSpeed in TEST: OFF"
    )


# =========================================================
# DEFAULT TEST MESSAGE
# =========================================================

def test_intro():

    if TEST_MODE:

        return (
            "🧪 TEST MODE شغال.\n\n"
            "ما راح نستهلك أي WaveSpeed "
            "generation.\n\n"
            "🎭 أصوات الشخصيات: ON\n"
            "🎙 الراوي: OFF\n"
            "📝 Subtitles: OFF\n"
            "🏷 Arabic Title: ON\n"
            "💰 WaveSpeed: OFF"
        )

    return (
        "⚠️ TEST_MODE=false\n\n"
        "هذا الاختبار قد يستهلك "
        "رصيد WaveSpeed."
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
                "⏳ في حلقة قيد المعالجة بالفعل."
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
            "🎬 Abosaraj بدأ...\n\n"
            "🎭 بناء الشخصيات\n"
            "✍️ كتابة الحوار\n"
            "🎥 بناء المشاهد\n"
            "🎙️ أصوات الشخصيات\n"
            "🔊 تصميم الصوت\n"
            "🎵 الموسيقى\n"
            "🏷️ العنوان العربي\n\n"
            + test_intro()
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

        # =================================================
        # LOG CHARACTER TIMELINE
        # =================================================

        for scene in story.get(
            "scenes",
            []
        ):

            log(
                "TIMELINE "
                f"{scene.get('scene_number')} "
                f"| {scene.get('speaker')} "
                f"| {scene.get('emotion')} "
                f"| {scene.get('dialogue')}"
            )

        send_message(
            chat_id,
            "✅ السيناريو جاهز.\n\n"
            f"📖 {story.get('title')}\n"
            f"🎬 {len(story.get('scenes', []))} مشاهد\n\n"
            "هلا نبدأ الإنتاج."
        )

        # =================================================
        # PRODUCE
        # =================================================

        result = produce_episode(
            story,
            workdir
        )

        caption = (
            "🎬 ABOSARAJ\n\n"
            f"📖 {result['title']}\n"
            f"🎞️ {result['scene_count']} مشاهد\n"
            f"⏱️ {result['duration']:.1f} ثانية\n\n"
            "🎭 Character Voices: ON\n"
            "🎙 Narrator: OFF\n"
            f"🔊 SFX: "
            f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'OFF'}\n"
            f"🎵 Music: "
            f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'OFF'}\n"
            "🏷️ Arabic Title: ON\n"
            "📝 Subtitles: OFF"
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
            "✅ الفيلم القصير جاهز."
        )

    except Exception as error:

        log(
            "PRODUCTION ERROR: "
            f"{repr(error)}"
        )

        try:

            send_message(
                chat_id,
                "❌ صار خطأ أثناء الإنتاج:\n\n"
                f"{type(error).__name__}: "
                f"{error}\n\n"
                "إذا كنا في TEST_MODE، "
                "ابعتلي آخر Render Logs."
            )

        except Exception as telegram_error:

            log(
                "Telegram send error: "
                f"{repr(telegram_error)}"
            )

    finally:

        with processing_lock:

            processing_chats.discard(
                chat_id
            )

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

            except Exception as error:

                log(
                    "Cleanup error: "
                    f"{repr(error)}"
                )

        else:

            log(
                f"WORKDIR KEPT: {workdir}"
            )


# =========================================================
# THREAD
# =========================================================

def start_processing(
    chat_id,
    idea
):

    threading.Thread(
        target=
        process_story_for_chat,
        args=(
            chat_id,
            idea
        ),
        daemon=True
    ).start()


# =========================================================
# FLASK HOME
# =========================================================

@app.get("/")
def home():

    return {
        "status":
            "ok",

        "service":
            "Abosaraj",

        "version":
            "2026-10-09-CINEMATIC-01",

        "test_mode":
            TEST_MODE,

        "narrator":
            False,

        "character_voices":
            True,

        "sound_design":
            SOUND_DESIGN_ENABLED,

        "music":
            MUSIC_ENABLED,

        "lipsync":
            LIPSYNC_ENABLED,

        "subtitles":
            False,

        "arabic_title":
            True,

        "padding":
            False,

        "wavespeed_paid_calls_in_test":
            False
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return {
        "status":
            "healthy",

        "service":
            "abosaraj",

        "version":
            "2026-10-09-CINEMATIC-01",

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

        "narrator":
            False,

        "character_voices":
            True,

        "sound_design":
            SOUND_DESIGN_ENABLED,

        "music":
            MUSIC_ENABLED,

        "lipsync":
            LIPSYNC_ENABLED,

        "scene_count":
            SHOT_COUNT,

        "scene_duration":
            SHOT_DURATION,

        "total_duration":
            TOTAL_DURATION,

        "production_scene_limit":
            PRODUCTION_SCENE_LIMIT,

        "subtitles":
            False,

        "arabic_title":
            True,

        "padding":
            False
    }


# =========================================================
# TEST ENDPOINT
# =========================================================

@app.get("/test")
def test_endpoint():

    chat_id = "HTTP_TEST"

    with processing_lock:

        if chat_id in processing_chats:

            return {
                "ok":
                    False,

                "status":
                    "already_running"
            }, 409

        processing_chats.add(
            chat_id
        )

    def worker():

        try:

            process_story_for_chat(
                chat_id,
                default_test_story()
            )

        except Exception as error:

            log(
                "HTTP TEST ERROR: "
                f"{repr(error)}"
            )

    threading.Thread(
        target=worker,
        daemon=True
    ).start()

    return {
        "ok":
            True,

        "status":
            "started",

        "test_mode":
            TEST_MODE,

        "narrator":
            False,

        "scenes":
            SHOT_COUNT,

        "duration":
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
        "TELEGRAM UPDATE "
        f"chat={chat_id} "
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
    # START
    # =====================================================

    if text in (
        "/start",
        "/help"
    ):

        send_message(
            chat_id,
            "🎬 أهلاً في Abosaraj.\n\n"
            "أنا أحول فكرة القصة إلى "
            "فيلم قصير سينمائي.\n\n"
            "🎭 كل شخصية بصوتها.\n"
            "🧠 كل شخصية بشخصيتها.\n"
            "🎙️ لا يوجد راوي.\n"
            "🎥 المشاهد مرتبطة بالحوار.\n"
            "🔊 مؤثرات صوتية.\n"
            "🎵 موسيقى.\n"
            "🏷️ عنوان عربي في النهاية.\n\n"
            "أرسل فكرة القصة.\n\n"
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
            test_intro()
            + "\n\n"
            "ابدأ الاختبار."
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
            "🤖 ABOSARAJ STATUS\n\n"
            + production_status_text()
            + "\n\n"
            "WaveSpeed: "
            + (
                "READY"
                if WAVESPEED_API_KEY
                else "NO KEY"
            )
            + "\n"
            "Groq: "
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
    # NORMAL STORY
    # =====================================================

    if len(text) < 10:

        send_message(
            chat_id,
            "اكتب فكرة القصة بتفاصيل أكثر شوي."
        )

        return {
            "ok":
                True
        }

    send_message(
        chat_id,
        "🎬 وصلت الفكرة.\n\n"
        "🎭 الشخصيات\n"
        "✍️ الحوار\n"
        "🎥 المشاهد\n"
        "🎙️ الأصوات\n\n"
        "بدأت المعالجة..."
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
            "TEST_MODE=true "
            "WEBHOOK SETUP SKIPPED"
        )

        return

    if not RENDER_EXTERNAL_URL:

        log(
            "RENDER_EXTERNAL_URL missing."
        )

        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL}"
        "/webhook"
    )

    try:

        response = http_post_no_retry(
            (
                "https://api.telegram.org/"
                f"bot{BOT_TOKEN}/setWebhook"
            ),
            data={
                "url":
                    webhook_url
            },
            timeout=30
        )

        response.raise_for_status()

        log(
            "WEBHOOK SET: "
            f"{webhook_url}"
        )

    except Exception as error:

        log(
            "WEBHOOK SETUP FAILED: "
            f"{repr(error)}"
        )


# =========================================================
# STARTUP
# =========================================================

def print_startup():

    log(
        "=========================================="
    )

    log(
        "ABOSARAJ AI CINEMATIC STORY BOT"
    )

    log(
        "VERSION="
        "2026-10-09-CINEMATIC-01"
    )

    log(
        "=========================================="
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

        "NARRATOR":
            False,

        "CHARACTER_VOICES":
            True,

        "SOUND_DESIGN":
            SOUND_DESIGN_ENABLED,

        "MUSIC":
            MUSIC_ENABLED,

        "LIPSYNC":
            LIPSYNC_ENABLED,

        "MAX_LIPSYNC_SCENES":
            MAX_LIPSYNC_SCENES,

        "SHOT_COUNT":
            SHOT_COUNT,

        "SHOT_DURATION":
            SHOT_DURATION,

        "TOTAL_DURATION":
            TOTAL_DURATION,

        "PRODUCTION_SCENE_LIMIT":
            PRODUCTION_SCENE_LIMIT,

        "SUBTITLES":
            False,

        "ARABIC_TITLE":
            True,

        "VIDEO_PADDING":
            False,

        "WAVESPEED_POST_AUTO_RETRY":
            False
    }

    for key, value in values.items():

        log(
            f"{key}={value}"
        )

    log(
        "STORY_ENGINE="
        "CHARACTER_DIALOGUE"
    )

    log(
        "NARRATOR=OFF"
    )

    log(
        "CHARACTER_VOICE_LOCK=ON"
    )

    log(
        "CHARACTER_PERSONALITY=ON"
    )

    log(
        "EMOTION=ON"
    )

    log(
        "SCENE_TIMELINE=ON"
    )

    log(
        "ARABIC_TITLE=ON"
    )

    log(
        "SUBTITLES=OFF"
    )

    log(
        "VIDEO_NORMALIZATION=CROP"
    )

    log(
        "PAID_WAVESPEED_TEST=BLOCKED"
    )

    log(
        "WAVESPEED_POST_AUTO_RETRY=OFF"
    )

    log(
        "=========================================="
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
