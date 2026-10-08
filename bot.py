import os, json, time, asyncio, shutil, tempfile, threading, subprocess
from pathlib import Path
import requests, edge_tts
from flask import Flask, request
from groq import Groq

# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.environ.get("WAVESPEED_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("1", "true", "yes", "on")
LIPSYNC_ENABLED = os.getenv("LIPSYNC_ENABLED", "false").lower() in ("1", "true", "yes", "on")
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE", "face").lower()
DEFAULT_LIPSYNC_EMOTION = os.getenv("LIPSYNC_EMOTION", "neutral")
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES", "1"))
PRODUCTION_SCENE_LIMIT = int(os.getenv("PRODUCTION_SCENE_LIMIT", "0"))

# Sound design
SOUND_DESIGN_ENABLED = os.getenv("SOUND_DESIGN_ENABLED", "true").lower() in ("1", "true", "yes", "on")
MMAUDIO_STEPS = int(os.getenv("MMAUDIO_STEPS", "25"))
MMAUDIO_GUIDANCE = float(os.getenv("MMAUDIO_GUIDANCE", "4.5"))
MUSIC_ENABLED = os.getenv("MUSIC_ENABLED", "true").lower() in ("1", "true", "yes", "on")
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.12"))
SFX_VOLUME = float(os.getenv("SFX_VOLUME", "0.62"))
AMBIENCE_VOLUME = float(os.getenv("AMBIENCE_VOLUME", "0.14"))
VOICE_VOLUME = float(os.getenv("VOICE_VOLUME", "1.0"))

SHOT_COUNT = 4
SHOT_DURATION = 5
TOTAL_DURATION = SHOT_COUNT * SHOT_DURATION
VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS = 720, 1280, 24

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
    },
}

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"

IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
IMAGE_EDIT_MODEL = "wavespeed-ai/z-image-turbo/image-to-image"
VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL = "sync/react-1"

# Cinematic sound
SFX_MODEL = "wavespeed-ai/mmaudio-v2"
MUSIC_MODEL = "wavespeed-ai/ace-step/prompt-to-audio"

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)
logging_lock = threading.Lock()


def log(message):
    with logging_lock:
        print(
            f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {message}",
            flush=True
        )


def auth_headers():
    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type": "application/json"
    }


def get_headers():
    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}"
    }


def wavespeed_submit(model, payload):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed submit attempted while TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:
        raise RuntimeError("WAVESPEED_API_KEY is missing.")

    url = f"{WAVESPEED_BASE}/{model}"

    log(f"WaveSpeed submit: {model}")

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
            body.get("message", "WaveSpeed task failed")
        )

    task_id = body.get("data", {}).get("id")

    if not task_id:
        raise RuntimeError(
            f"WaveSpeed returned no task id: {body}"
        )

    log(f"WaveSpeed task created: {task_id}")

    return task_id


def wavespeed_wait(task_id, timeout=900):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed polling attempted while TEST_MODE=true"
        )

    url = f"{WAVESPEED_BASE}/predictions/{task_id}/result"

    started = time.time()

    while True:

        if time.time() - started > timeout:
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
            data.get("status", "")
        ).lower()

        log(f"Task {task_id}: {status}")

        if status == "completed":

            outputs = data.get("outputs")

            if not outputs:
                raise RuntimeError(
                    f"No outputs: {body}"
                )

            first = outputs[0]

            if isinstance(first, dict):

                for key in (
                    "url",
                    "audio_url",
                    "video_url",
                    "download_url"
                ):
                    if first.get(key):
                        return first[key]

            return first

        if status in (
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        ):
            raise RuntimeError(
                f"WaveSpeed task failed: {body}"
            )

        time.sleep(2)


def upload_to_wavespeed(path):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed upload attempted while TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:
        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    path = Path(path)

    ticket_response = requests.post(
        f"{WAVESPEED_BASE}/media/uploads",
        headers=auth_headers(),
        json={
            "filename": path.name,
            "size": path.stat().st_size
        },
        timeout=30
    )

    ticket_response.raise_for_status()

    ticket = ticket_response.json()

    if ticket.get("code") != 200:
        raise RuntimeError(ticket)

    data = ticket["data"]

    with path.open("rb") as f:

        upload_response = requests.put(
            data["upload"]["url"],
            headers=data["upload"]["headers"],
            data=f,
            timeout=300
        )

    upload_response.raise_for_status()

    return data["download_url"]


def download_file(url, path):
    log("Downloading media...")

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
# GROQ SCHEMAS
# =========================================================

CHARACTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        k: {"type": "string"}
        for k in (
            "identity",
            "age",
            "face",
            "hair",
            "clothes",
            "colors"
        )
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
            "maxItems": 1,
            "items": DIALOGUE_SCHEMA
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
        },

        "music": {
            "type": "string"
        }
    },

    "required": [
        "action",
        "dialogue",
        "scene_image_prompt",
        "video_prompt",
        "camera",
        "sound",
        "music"
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
                k: CHARACTER_SCHEMA
                for k in (
                    "male_lead",
                    "princess",
                    "wolf",
                    "king"
                )
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
            "items": SCENE_SCHEMA
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
# STORY GENERATION
# =========================================================

def create_story(user_idea):

    log(
        "Groq: generating screenplay + sound cue sheet..."
    )

    system_prompt = f"""
أنت كاتب سيناريو سينمائي عربي، مخرج، مدير تصوير،
ومشرف استمرارية ومصمم صوت سينمائي.

نريد Microdrama عربي:
Dark Fantasy + Romance + Mystery +
Suspense + Supernatural Drama.

العالم:
رجل غامض جذاب بقوة خارقة،
أميرة تحبه،
ملك يعرف سراً خطيراً عنه،
وذئبة بيضاء صغيرة لا تتكلم.

الفكرة:
{user_idea}

عدد اللقطات:
{SHOT_COUNT}

مدة اللقطة:
{SHOT_DURATION} ثوانٍ.

قواعد الصورة والحركة:

realistic cinematic Arabic fantasy drama,
photorealistic,
professional film lighting,
realistic skin/fabric/fur,
volumetric moonlight,
fog,
shallow DOF,
anamorphic look,
rim light,
natural body/head/eye/blink/mouth/
hand/arm/finger/breathing/cloth/hair movement,
realistic hands and eyes,
vertical 9:16,
high production value.

ممنوع:
anime,
cartoon,
illustration,
game art,
plastic skin,
text,
logo,
watermark.

الحوار:
متحدث بشري واحد فقط في كل لقطة،
قصير 5-12 كلمة،
طبيعي ومرتبط بالحدث.

الذئبة لا تتكلم.

صوت الذئبة فقط:
whimper,
growl,
howl,
breathing.

SOUND DESIGN مهم جداً.

حقل sound يجب أن يكون cue sheet صوتي
قابل للتنفيذ، يصف:

البيئة المستمرة،
Foley،
footsteps،
cloth،
حركة الشعر،
metal/wood،
wolf vocalization،
whoosh،
impact،
supernatural power،
risers/stingers،
واتجاه/قرب الصوت عند الحاجة.

اذكر توقيتاً تقريبياً داخل 0-5s
مثل:

[0.0-1.5s]

لا تضع موسيقى داخل sound.

حقل music يصف فقط
score سينمائي instrumental بلا غناء،

ويحدد:
mood،
الآلات،
الطاقة،
وتصعيد المشهد.

كل لقطة:

hook
ثم تصعيد
ثم خطر/كشف
ثم cliffhanger بصري.

لا تحل الأسرار.

JSON فقط.
"""

    user_prompt = (
        f"حوّل الفكرة التالية إلى "
        f"{SHOT_COUNT} لقطات مدة كل منها "
        f"{SHOT_DURATION} ثوانٍ. "
        f"أريد JSON فقط.\n\n"
        f"{user_idea}"
    )

    response = groq.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.75,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "abosaraj_story",
                "strict": True,
                "schema": STORY_SCHEMA
            }
        },
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError(
            "Groq returned empty story."
        )

    story = json.loads(content)

    if len(story.get("scenes", [])) != SHOT_COUNT:
        raise RuntimeError(
            "Groq returned wrong scene count."
        )

    return story


# =========================================================
# FFMPEG HELPERS
# =========================================================

def run_cmd(cmd, timeout=300):
    log(
        "CMD: " +
        " ".join(str(x) for x in cmd)
    )

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        log(result.stderr[-4000:])
        raise RuntimeError(
            f"Command failed with code "
            f"{result.returncode}"
        )

    return result


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        return 0.0

    try:
        return float(
            result.stdout.strip()
        )
    except Exception:
        return 0.0


def normalize_audio_to_wav(
    input_path,
    output_path,
    duration=None
):
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "24000",
        "-sample_fmt",
        "s16"
    ]

    if duration:
        cmd += [
            "-t",
            str(duration)
        ]

    cmd += [
        str(output_path)
    ]

    run_cmd(
        cmd,
        timeout=120
    )

    return output_path


# =========================================================
# ARABIC FONT + SUBTITLES
# =========================================================

def find_arabic_font():

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoKufiArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    for font in candidates:
        if Path(font).exists():
            return font

    return None


def get_subtitle_style(font_path=None):

    font_name = "Arial"

    if font_path:
        font_name = Path(font_path).stem

    return (
        f"FontName={font_name},"
        f"FontSize=15,"
        f"PrimaryColour=&H00FFFFFF,"
        f"SecondaryColour=&H00FFFFFF,"
        f"OutlineColour=&H80000000,"
        f"BackColour=&H00000000,"
        f"Bold=0,"
        f"Italic=0,"
        f"BorderStyle=1,"
        f"Outline=1,"
        f"Shadow=1,"
        f"Alignment=2,"
        f"MarginL=55,"
        f"MarginR=55,"
        f"MarginV=65"
    )


def ass_escape(text):

    text = str(text)

    text = text.replace(
        "\\",
        "\\\\"
    )

    text = text.replace(
        "{",
        "\\{"
    )

    text = text.replace(
        "}",
        "\\}"
    )

    return text


def seconds_to_ass(seconds):

    seconds = max(
        0.0,
        float(seconds)
    )

    hours = int(seconds // 3600)

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = seconds % 60

    whole = int(secs)

    centiseconds = int(
        round((secs - whole) * 100)
    )

    if centiseconds >= 100:
        whole += 1
        centiseconds = 0

    return (
        f"{hours}:{minutes:02d}:"
        f"{whole:02d}.{centiseconds:02d}"
    )


def create_ass(scene_meta, output_path):

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,15,&H00FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,2,55,55,65,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    for item in scene_meta:

        start = float(
            item["start"]
        )

        end = float(
            item["end"]
        )

        text_value = ass_escape(
            item["text"]
        )

        lines.append(
            "Dialogue: 0,"
            f"{seconds_to_ass(start)},"
            f"{seconds_to_ass(end)},"
            "Default,,0,0,0,,"
            f"{text_value}"
        )

    Path(output_path).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    return output_path
   # =========================================================
# TTS
# =========================================================

async def _tts_generate(
    text,
    voice,
    output_path,
    rate="-5%",
    pitch=None
):
    kwargs = {
        "text": text,
        "voice": voice,
        "rate": rate,
    }

    # Edge-TTS يرفض أحياناً pitch=0Hz،
    # لذلك لا نرسل pitch إذا لم يكن مطلوباً.
    if pitch:
        kwargs["pitch"] = pitch

    communicate = edge_tts.Communicate(
        **kwargs
    )

    await communicate.save(
        str(output_path)
    )


def create_voice_audio(
    text,
    speaker,
    output_path
):
    config = VOICE_CONFIG.get(
        speaker,
        VOICE_CONFIG["narrator"]
    )

    voice = config["voice"]
    rate = config.get(
        "rate",
        "-5%"
    )
    pitch = config.get(
        "pitch"
    )

    log(
        f"TTS: {speaker} | {text}"
    )

    asyncio.run(
        _tts_generate(
            text=text,
            voice=voice,
            output_path=output_path,
            rate=rate,
            pitch=pitch
        )
    )

    return output_path


def fit_audio_to_duration(
    input_path,
    output_path,
    duration
):
    duration = float(duration)

    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        (
            "apad,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
        ),
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path)
    ])

    return output_path


# =========================================================
# DIALOGUE AUDIO
# =========================================================

def build_dialogue_track(
    scenes,
    duration,
    workdir
):
    """
    Creates one continuous dialogue track.

    Every scene has exactly one dialogue speaker.
    The individual TTS file is normalized and placed
    at the correct timestamp.
    """

    workdir = Path(workdir)

    scene_audio_files = []

    for index, scene in enumerate(scenes):

        dialogue_items = scene.get(
            "dialogue",
            []
        )

        if not dialogue_items:
            continue

        dialogue = dialogue_items[0]

        text_value = str(
            dialogue.get("text", "")
        ).strip()

        speaker = str(
            dialogue.get(
                "speaker",
                "narrator"
            )
        )

        if not text_value:
            continue

        raw_mp3 = (
            workdir /
            f"dialogue_{index:02d}.mp3"
        )

        wav_file = (
            workdir /
            f"dialogue_{index:02d}.wav"
        )

        fitted_file = (
            workdir /
            f"dialogue_{index:02d}_fit.wav"
        )

        create_voice_audio(
            text_value,
            speaker,
            raw_mp3
        )

        normalize_audio_to_wav(
            raw_mp3,
            wav_file
        )

        fit_audio_to_duration(
            wav_file,
            fitted_file,
            SHOT_DURATION
        )

        scene_audio_files.append(
            (
                index,
                fitted_file
            )
        )

    if not scene_audio_files:

        silent_path = (
            workdir /
            "dialogue_silent.wav"
        )

        run_cmd([
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                "anullsrc="
                "channel_layout=stereo:"
                "sample_rate=48000"
            ),
            "-t",
            str(duration),
            str(silent_path)
        ])

        return silent_path

    # Build each scene at the beginning of a full
    # episode-sized timeline.
    inputs = []

    for index, audio_path in scene_audio_files:

        delay_ms = int(
            index *
            SHOT_DURATION *
            1000
        )

        inputs.append(
            (
                audio_path,
                delay_ms
            )
        )

    cmd = [
        "ffmpeg",
        "-y"
    ]

    for audio_path, _ in inputs:
        cmd += [
            "-i",
            str(audio_path)
        ]

    filter_parts = []

    labels = []

    for idx, (
        audio_path,
        delay_ms
    ) in enumerate(inputs):

        label = f"a{idx}"

        filter_parts.append(
            f"[{idx}:a]"
            f"adelay={delay_ms}|{delay_ms},"
            "aresample=48000,"
            "aformat=sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo"
            f"[{label}]"
        )

        labels.append(
            f"[{label}]"
        )

    if len(labels) == 1:

        filter_parts.append(
            f"{labels[0]}"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
            "[dialogue]"
        )

    else:

        filter_parts.append(
            "".join(labels) +
            f"amix=inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
            "[dialogue]"
        )

    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[dialogue]",
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(workdir / "dialogue_track.wav")
    ]

    run_cmd(
        cmd,
        timeout=180
    )

    return workdir / "dialogue_track.wav"


# =========================================================
# LOCAL AMBIENCE
# =========================================================

def create_local_ambience(
    duration,
    output_path
):
    """
    Very subtle local bed.
    This is only a safety fallback / base ambience.
    MMAudio supplies the actual cinematic event SFX.
    """

    run_cmd([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            "anoisesrc="
            "color=brown:"
            "amplitude=0.015:"
            "sample_rate=48000"
        ),
        "-af",
        (
            "highpass=f=35,"
            "lowpass=f=5000,"
            "volume=0.18,"
            f"atrim=0:{duration}"
        ),
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path)
    ])

    return output_path


# =========================================================
# TEST VIDEO
# =========================================================

def create_test_scene_video(
    scene_index,
    output_path
):
    """
    TEST_MODE intentionally creates a local moving
    placeholder instead of calling WaveSpeed.

    This lets us validate:
      - FFmpeg
      - TTS
      - subtitles
      - audio mixing
      - Telegram
      - final MP4 generation

    without spending API credits.
    """

    color_a = [
        "0x17101f",
        "0x10202a",
        "0x201710",
        "0x111c14"
    ][scene_index % 4]

    color_b = [
        "0x382345",
        "0x193d4a",
        "0x4b3018",
        "0x203c25"
    ][scene_index % 4]

    filter_complex = (
        f"color=c={color_a}:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"r={VIDEO_FPS}:d={SHOT_DURATION},"
        "format=yuv420p,"
        "drawbox="
        "x='100+80*sin(t)':"
        "y='250+120*cos(t*0.7)':"
        "w=520:"
        "h=700:"
        "color="
        f"{color_b}@0.35:"
        "t=fill,"
        "drawbox="
        "x='220+100*cos(t*0.5)':"
        "y='500+70*sin(t)':"
        "w=280:"
        "h=280:"
        "color=white@0.07:"
        "t=fill"
    )

    run_cmd([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        filter_complex,
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
        str(output_path)
    ])

    return output_path


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_image(
    prompt,
    output_path
):
    if TEST_MODE:
        log(
            "TEST_MODE: skipping image generation."
        )

        return None

    payload = {
        "prompt": prompt,
        "size": "720*1280"
    }

    task_id = wavespeed_submit(
        IMAGE_MODEL,
        payload
    )

    url = wavespeed_wait(
        task_id,
        timeout=600
    )

    download_file(
        url,
        output_path
    )

    return output_path


def generate_cast_reference(
    story,
    output_path
):
    prompt = story.get(
        "cast_reference_prompt",
        ""
    )

    if not prompt:
        prompt = """
Create a single photorealistic cinematic
Arabic fantasy cast reference sheet.

Show exactly four recurring characters:

1. handsome mysterious supernatural man
2. beautiful Arabian princess
3. small white female wolf pup
4. powerful Arab king

Full body or three-quarter body,
neutral cinematic composition,
realistic human skin,
realistic fabric,
realistic white wolf fur,
consistent facial identity,
consistent clothing,
consistent proportions.

Dark fantasy palace-and-forest atmosphere.

Professional film lighting,
volumetric moonlight,
soft atmospheric fog,
anamorphic cinematic look,
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
        output_path
    )


# =========================================================
# VIDEO GENERATION
# =========================================================

def generate_scene_video(
    image_path,
    video_prompt,
    output_path
):
    if TEST_MODE:
        return None

    image_url = upload_to_wavespeed(
        image_path
    )

    prompt = f"""
{video_prompt}

Natural cinematic motion:
subtle eye movement,
natural blinking,
facial expression,
natural mouth movement,
breathing,
head movement,
shoulder movement,
hands and fingers,
body weight shift,
walking when appropriate,
hair movement,
cloth movement,
environment movement,
fog movement,
realistic animal fur movement.

Preserve exact character identity,
face,
clothing,
anatomy,
and environment.

No morphing.
No extra fingers.
No deformed hands.
No duplicated characters.
No text.
No watermark.
No logo.

Vertical 9:16.
Photorealistic cinematic film.
"""

    payload = {
        "image": image_url,
        "prompt": prompt,
        "duration": SHOT_DURATION,
        "resolution": "480p"
    }

    task_id = wavespeed_submit(
        VIDEO_MODEL,
        payload
    )

    url = wavespeed_wait(
        task_id,
        timeout=900
    )

    download_file(
        url,
        output_path
    )

    return output_path


# =========================================================
# LIPSYNC
# =========================================================

def is_human_speaker(speaker):
    return speaker in {
        "male_lead",
        "princess",
        "king",
        "guard",
        "narrator"
    }


def generate_lipsync_video(
    video_path,
    audio_path,
    output_path
):
    if TEST_MODE:
        log(
            "TEST_MODE: skipping React-1."
        )
        return video_path

    if not LIPSYNC_ENABLED:
        return video_path

    if LIPSYNC_MODE not in (
        "face",
        "full"
    ):
        log(
            f"Unknown LIPSYNC_MODE={LIPSYNC_MODE}; "
            "using face."
        )

    video_url = upload_to_wavespeed(
        video_path
    )

    audio_url = upload_to_wavespeed(
        audio_path
    )

    payload = {
        "video": video_url,
        "audio": audio_url,
        "model_mode": LIPSYNC_MODE,
        "emotion": DEFAULT_LIPSYNC_EMOTION
    }

    task_id = wavespeed_submit(
        LIPSYNC_MODEL,
        payload
    )

    url = wavespeed_wait(
        task_id,
        timeout=900
    )

    download_file(
        url,
        output_path
    )

    return output_path


# =========================================================
# CINEMATIC SFX — MMAUDIO V2
# =========================================================

def generate_scene_sfx(
    scene_video,
    sound_prompt,
    scene_index,
    workdir
):
    """
    MMAudio v2:
    Generates event-synchronized SFX/ambience
    directly from the final scene video.

    Important:
    This happens AFTER Wan / React-1 so the generated
    sound can follow the actual visible motion.
    """

    if not SOUND_DESIGN_ENABLED:
        return None

    if TEST_MODE:
        log(
            f"TEST_MODE: silent SFX for scene {scene_index + 1}"
        )
        return None

    if not scene_video:
        return None

    video_url = upload_to_wavespeed(
        scene_video
    )

    prompt = f"""
Create cinematic synchronized sound design
for this exact 5-second video.

Follow the visible actions precisely.

Sound cue sheet:
{sound_prompt}

Include realistic:
environment,
room/forest tone,
footsteps,
cloth movement,
hair movement,
wood,
metal,
animal sounds,
wolf breathing/whimper/growl/howl when visible,
wind,
leaves,
whooshes,
impacts,
supernatural energy,
low rumbles,
risers,
stingers,
and spatial perspective.

Make sounds match the exact timing
and physical movement in the video.

Do NOT generate:
speech,
dialogue,
narration,
singing,
lyrics,
music,
melody,
or voice.

Professional cinematic film sound.
Natural dynamic range.
No clipping.
No artificial distortion.
"""

    payload = {
        "video": video_url,
        "prompt": prompt,
        "duration": SHOT_DURATION,
        "steps": MMAUDIO_STEPS,
        "guidance_scale": MMAUDIO_GUIDANCE,
        "negative_prompt": (
            "speech, dialogue, narration, singing, "
            "lyrics, music, melody, voice, "
            "distortion, clipping, digital noise"
        )
    }

    task_id = wavespeed_submit(
        SFX_MODEL,
        payload
    )

    url = wavespeed_wait(
        task_id,
        timeout=900
    )

    raw_audio = (
        Path(workdir) /
        f"sfx_{scene_index:02d}.wav"
    )

    download_file(
        url,
        raw_audio
    )

    normalized = (
        Path(workdir) /
        f"sfx_{scene_index:02d}_norm.wav"
    )

    normalize_audio_to_wav(
        raw_audio,
        normalized,
        SHOT_DURATION
    )

    return normalized


# =========================================================
# MUSIC — ACE-STEP
# =========================================================

def build_music_prompt(
    story,
    scenes
):
    scene_cues = []

    for i, scene in enumerate(scenes):

        cue = str(
            scene.get(
                "music",
                ""
            )
        ).strip()

        if cue:
            scene_cues.append(
                f"Scene {i + 1}: {cue}"
            )

    combined = "\n".join(
        scene_cues
    )

    title = story.get(
        "title",
        "Dark Arabic Fantasy"
    )

    return f"""
Instrumental cinematic score for an Arabic
dark fantasy romance mystery film.

Title:
{title}

Overall mood:
romantic mystery,
supernatural dread,
ancient secret,
night forest,
royal palace,
forbidden love,
danger,
emotional tension,
slow escalation,
cliffhanger.

Instrumentation:
oud-like plucked texture,
low cinematic strings,
deep cello,
soft frame drum,
subtle Arabic percussion,
breathy atmospheric pads,
distant choir-like texture WITHOUT WORDS,
deep sub bass,
sparse piano notes,
metallic supernatural textures.

Structure:
begin intimate and mysterious,
gradually increase tension,
introduce darker harmonic movement,
build toward supernatural revelation,
finish with a strong unresolved cliffhanger.

Absolutely instrumental.
No vocals.
No lyrics.
No spoken words.

Scene cues:
{combined}
"""


def generate_music(
    story,
    scenes,
    duration,
    workdir
):
    if not MUSIC_ENABLED:
        return None

    if TEST_MODE:
        log(
            "TEST_MODE: silent music."
        )
        return None

    prompt = build_music_prompt(
        story,
        scenes
    )

    payload = {
        "prompt": prompt,
        "duration": int(
            max(5, min(240, duration))
        ),
        "instrumental": True,
        "seed": 24117
    }

    task_id = wavespeed_submit(
        MUSIC_MODEL,
        payload
    )

    url = wavespeed_wait(
        task_id,
        timeout=900
    )

    raw_music = (
        Path(workdir) /
        "music_raw.wav"
    )

    download_file(
        url,
        raw_music
    )

    music = (
        Path(workdir) /
        "music.wav"
    )

    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(raw_music),
        "-af",
        (
            "aresample=48000,"
            f"volume={MUSIC_VOLUME},"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
        ),
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(music)
    ])

    return music
    # =========================================================
# AUDIO MIXING
# =========================================================

def create_silent_track(
    duration,
    output_path
):
    run_cmd([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            "anullsrc="
            "channel_layout=stereo:"
            "sample_rate=48000"
        ),
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path)
    ])

    return output_path


def prepare_audio_track(
    input_path,
    output_path,
    duration,
    volume=1.0
):
    if input_path is None or not Path(input_path).exists():
        return create_silent_track(
            duration,
            output_path
        )

    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        (
            "aresample=48000,"
            f"volume={volume},"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
        ),
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path)
    ])

    return output_path


def build_sfx_track(
    sfx_files,
    duration,
    workdir
):
    """
    Places each 5-second MMAudio SFX clip at the
    corresponding scene timestamp.
    """

    workdir = Path(workdir)

    valid_files = [
        item for item in sfx_files
        if item is not None
        and Path(item).exists()
    ]

    if not valid_files:
        return create_silent_track(
            duration,
            workdir / "sfx_track.wav"
        )

    inputs = []

    for index, audio_path in enumerate(
        valid_files
    ):
        delay_ms = int(
            index *
            SHOT_DURATION *
            1000
        )

        inputs.append(
            (
                audio_path,
                delay_ms
            )
        )

    cmd = [
        "ffmpeg",
        "-y"
    ]

    for audio_path, _ in inputs:
        cmd += [
            "-i",
            str(audio_path)
        ]

    filters = []
    labels = []

    for i, (
        audio_path,
        delay_ms
    ) in enumerate(inputs):

        label = f"s{i}"

        filters.append(
            f"[{i}:a]"
            f"adelay={delay_ms}|{delay_ms},"
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
            "asetpts=N/SR/TB"
            "[sfx]"
        )

    else:

        filters.append(
            "".join(labels) +
            f"amix=inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB"
            "[sfx]"
        )

    output = (
        workdir /
        "sfx_track.wav"
    )

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[sfx]",
        "-t",
        str(duration),
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output)
    ]

    run_cmd(
        cmd,
        timeout=180
    )

    return output


def mix_final_audio(
    dialogue_track,
    sfx_track,
    music_track,
    ambience_track,
    duration,
    output_path
):
    """
    Final cinematic mix.

    Voice:
        1.00

    SFX:
        0.62

    Music:
        0.12

    Ambience:
        0.14
    """

    duration = float(duration)

    inputs = []

    input_files = [
        dialogue_track,
        sfx_track,
        music_track,
        ambience_track
    ]

    for item in input_files:

        if item is None:
            item = None

        inputs.append(item)

    cmd = [
        "ffmpeg",
        "-y"
    ]

    for item in inputs:

        if item is None:
            # create a lavfi silent input
            cmd += [
                "-f",
                "lavfi",
                "-t",
                str(duration),
                "-i",
                (
                    "anullsrc="
                    "channel_layout=stereo:"
                    "sample_rate=48000"
                )
            ]

        else:

            cmd += [
                "-i",
                str(item)
            ]

    filter_parts = []

    filter_parts.append(
        "[0:a]"
        f"volume={VOICE_VOLUME},"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB"
        "[voice]"
    )

    filter_parts.append(
        "[1:a]"
        f"volume={SFX_VOLUME},"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB"
        "[sfx]"
    )

    filter_parts.append(
        "[2:a]"
        f"volume={MUSIC_VOLUME},"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB"
        "[music]"
    )

    filter_parts.append(
        "[3:a]"
        f"volume={AMBIENCE_VOLUME},"
        "aresample=48000,"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB"
        "[amb]"
    )

    filter_parts.append(
        "[voice][sfx][music][amb]"
        "amix=inputs=4:"
        "duration=longest:"
        "dropout_transition=0,"
        "alimiter=limit=0.95:"
        "attack=5:"
        "release=50,"
        "aresample=48000,"
        "aformat="
        "sample_fmts=fltp:"
        "sample_rates=48000:"
        "channel_layouts=stereo"
        "[mix]"
    )

    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
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
        str(output_path)
    ]

    run_cmd(
        cmd,
        timeout=240
    )

    return output_path


# =========================================================
# SUBTITLE TRACK
# =========================================================

def build_scene_meta(
    scenes
):
    scene_meta = []

    for index, scene in enumerate(
        scenes
    ):

        dialogue_items = scene.get(
            "dialogue",
            []
        )

        if not dialogue_items:
            continue

        dialogue = dialogue_items[0]

        text_value = str(
            dialogue.get(
                "text",
                ""
            )
        ).strip()

        if not text_value:
            continue

        start = (
            index *
            SHOT_DURATION
        )

        end = (
            start +
            SHOT_DURATION
        )

        scene_meta.append({
            "start": start,
            "end": end,
            "text": text_value
        })

    return scene_meta


def burn_subtitles(
    video_path,
    ass_path,
    output_path
):
    """
    Burns Arabic subtitles into the final video.

    Small white text,
    bottom center,
    thin outline,
    no large background box.
    """

    font_path = find_arabic_font()

    if font_path:
        fonts_dir = str(
            Path(font_path).parent
        )
    else:
        fonts_dir = "/usr/share/fonts"

    # FFmpeg subtitles filter can be sensitive to
    # commas and special characters, so we use the
    # ASS file directly.
    escaped_ass = str(
        Path(ass_path)
    ).replace(
        "\\",
        "\\\\"
    ).replace(
        ":",
        "\\:"
    )

    subtitle_filter = (
        f"subtitles='{escaped_ass}'"
    )

    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
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
        str(
            ffprobe_duration(video_path)
        ),
        str(output_path)
    ])

    return output_path


# =========================================================
# SCENE CONCAT
# =========================================================

def normalize_scene_video(
    input_path,
    output_path
):
    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2,"
            "format=yuv420p"
        ),
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
        str(output_path)
    ])

    return output_path


def concat_videos(
    video_files,
    output_path
):
    """
    Concatenate normalized scene videos.
    """

    list_file = (
        Path(output_path).parent /
        "concat_list.txt"
    )

    lines = []

    for video in video_files:

        absolute_path = Path(
            video
        ).resolve()

        lines.append(
            f"file '{str(absolute_path).replace(chr(39), \"'\\\\''\")}'"
        )

    list_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    run_cmd([
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
        str(output_path)
    ])

    return output_path


# =========================================================
# AUDIO + VIDEO FINAL ASSEMBLY
# =========================================================

def add_audio_to_video(
    video_path,
    audio_path,
    duration,
    output_path
):
    """
    Explicit duration instead of -shortest.
    This prevents a short audio file from accidentally
    cutting the video.
    """

    run_cmd([
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
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-t",
        str(duration),
        "-movflags",
        "+faststart",
        str(output_path)
    ])

    return output_path


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

    response = requests.post(
        url,
        data=payload or {},
        files=files,
        timeout=180
    )

    response.raise_for_status()

    body = response.json()

    if not body.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {body}"
        )

    return body


def send_message(
    chat_id,
    text
):
    return telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_video(
    chat_id,
    video_path,
    caption=""
):
    with open(
        video_path,
        "rb"
    ) as video_file:

        return telegram_api(
            "sendVideo",
            payload={
                "chat_id": chat_id,
                "caption": caption,
                "supports_streaming": "true"
            },
            files={
                "video": (
                    Path(video_path).name,
                    video_file,
                    "video/mp4"
                )
            }
        )


# =========================================================
# PRODUCTION
# =========================================================

def production_status_text(
    scene_count,
    duration
):
    mode = (
        "TEST / LOCAL"
        if TEST_MODE
        else "PRODUCTION"
    )

    sfx_status = (
        "ON"
        if (
            SOUND_DESIGN_ENABLED
            and not TEST_MODE
        )
        else "TEST-SILENT"
    )

    music_status = (
        "ON"
        if (
            MUSIC_ENABLED
            and not TEST_MODE
        )
        else "TEST-SILENT"
    )

    lipsync_status = (
        "ON"
        if (
            LIPSYNC_ENABLED
            and not TEST_MODE
        )
        else "OFF"
    )

    return (
        f"Mode: {mode}\n"
        f"Scenes: {scene_count}\n"
        f"Duration: {duration:.1f}s\n"
        f"Cinematic SFX: {sfx_status}\n"
        f"Cinematic score: {music_status}\n"
        f"Lip-sync: {lipsync_status}"
    )


def produce_episode(
    story,
    workdir
):
    """
    Main production pipeline:

    1. Generate cast reference
    2. Generate scene images
    3. Generate Wan motion
    4. Optional React-1 lip-sync
    5. Generate MMAudio SFX from final scene
    6. Generate coherent ACE-Step score
    7. Generate dialogue
    8. Mix all audio
    9. Concatenate scenes
    10. Burn Arabic subtitles
    11. Attach final audio
    12. Return MP4
    """

    workdir = Path(workdir)
    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    scenes = story.get(
        "scenes",
        []
    )

    if PRODUCTION_SCENE_LIMIT > 0:

        scenes = scenes[
            :PRODUCTION_SCENE_LIMIT
        ]

    if not scenes:
        raise RuntimeError(
            "No scenes available."
        )

    scene_count = len(
        scenes
    )

    actual_duration = (
        scene_count *
        SHOT_DURATION
    )

    log(
        f"Producing {scene_count} scenes "
        f"({actual_duration}s)"
    )

    # -----------------------------------------------------
    # CAST REFERENCE
    # -----------------------------------------------------

    cast_reference = (
        workdir /
        "cast_reference.png"
    )

    if TEST_MODE:
        log(
            "TEST_MODE: skipping cast reference."
        )
    else:
        generate_cast_reference(
            story,
            cast_reference
        )

    # -----------------------------------------------------
    # SCENES
    # -----------------------------------------------------

    scene_videos = []
    scene_sfx_files = []

    # We generate dialogue audio before the scene
    # because React-1 needs the exact audio.
    dialogue_scene_audio = []

    for index, scene in enumerate(
        scenes
    ):

        dialogue_items = scene.get(
            "dialogue",
            []
        )

        if dialogue_items:

            dialogue = dialogue_items[0]

            text_value = str(
                dialogue.get(
                    "text",
                    ""
                )
            ).strip()

            speaker = str(
                dialogue.get(
                    "speaker",
                    "narrator"
                )
            )

        else:

            text_value = ""
            speaker = "narrator"

        raw_mp3 = (
            workdir /
            f"scene_{index:02d}_voice.mp3"
        )

        raw_wav = (
            workdir /
            f"scene_{index:02d}_voice.wav"
        )

        fitted_wav = (
            workdir /
            f"scene_{index:02d}_voice_fit.wav"
        )

        if text_value:

            create_voice_audio(
                text_value,
                speaker,
                raw_mp3
            )

            normalize_audio_to_wav(
                raw_mp3,
                raw_wav
            )

            fit_audio_to_duration(
                raw_wav,
                fitted_wav,
                SHOT_DURATION
            )

            dialogue_scene_audio.append(
                (
                    index,
                    speaker,
                    fitted_wav
                )
            )

        else:

            silence = (
                workdir /
                f"scene_{index:02d}_silent.wav"
            )

            create_silent_track(
                SHOT_DURATION,
                silence
            )

            dialogue_scene_audio.append(
                (
                    index,
                    speaker,
                    silence
                )
            )

    # -----------------------------------------------------
    # IMAGE + VIDEO PER SCENE
    # -----------------------------------------------------

    for index, scene in enumerate(
        scenes
    ):

        log(
            f"========== SCENE {index + 1}/{scene_count} =========="
        )

        image_path = (
            workdir /
            f"scene_{index:02d}.png"
        )

        base_video = (
            workdir /
            f"scene_{index:02d}_wan.mp4"
        )

        final_scene_video = (
            workdir /
            f"scene_{index:02d}_final.mp4"
        )

        normalized_video = (
            workdir /
            f"scene_{index:02d}_normalized.mp4"
        )

        if TEST_MODE:

            create_test_scene_video(
                index,
                base_video
            )

        else:

            prompt = str(
                scene.get(
                    "scene_image_prompt",
                    ""
                )
            )

            generate_image(
                prompt,
                image_path
            )

            generate_scene_video(
                image_path,
                scene.get(
                    "video_prompt",
                    ""
                ),
                base_video
            )

        # -------------------------------------------------
        # REACT-1 LIPSYNC
        # -------------------------------------------------

        use_lipsync = (
            LIPSYNC_ENABLED
            and index < MAX_LIPSYNC_SCENES
            and not TEST_MODE
        )

        dialogue_info = dialogue_scene_audio[
            index
        ]

        speaker = dialogue_info[1]
        audio_path = dialogue_info[2]

        if (
            use_lipsync
            and is_human_speaker(speaker)
        ):

            log(
                f"React-1 lip-sync scene {index + 1}"
            )

            generate_lipsync_video(
                base_video,
                audio_path,
                final_scene_video
            )

        else:

            shutil.copyfile(
                base_video,
                final_scene_video
            )

        # -------------------------------------------------
        # NORMALIZE FINAL SCENE VIDEO
        # -------------------------------------------------

        normalize_scene_video(
            final_scene_video,
            normalized_video
        )

        scene_videos.append(
            normalized_video
        )

        # -------------------------------------------------
        # MMAUDIO — AFTER ALL VISUAL PROCESSING
        # -------------------------------------------------

        if (
            SOUND_DESIGN_ENABLED
            and not TEST_MODE
        ):

            sfx_file = generate_scene_sfx(
                normalized_video,
                scene.get(
                    "sound",
                    ""
                ),
                index,
                workdir
            )

        else:

            sfx_file = None

        scene_sfx_files.append(
            sfx_file
        )

    # -----------------------------------------------------
    # CONCAT VIDEO
    # -----------------------------------------------------

    concat_video = (
        workdir /
        "episode_video.mp4"
    )

    concat_videos(
        scene_videos,
        concat_video
    )

    # -----------------------------------------------------
    # DIALOGUE TRACK
    # -----------------------------------------------------

    dialogue_track = build_dialogue_track(
        scenes,
        actual_duration,
        workdir
    )

    # -----------------------------------------------------
    # SFX TRACK
    # -----------------------------------------------------

    sfx_track = build_sfx_track(
        scene_sfx_files,
        actual_duration,
        workdir
    )

    # -----------------------------------------------------
    # MUSIC
    # -----------------------------------------------------

    if (
        MUSIC_ENABLED
        and not TEST_MODE
    ):

        music_track = generate_music(
            story,
            scenes,
            actual_duration,
            workdir
        )

    else:

        music_track = None

    # -----------------------------------------------------
    # AMBIENCE
    # -----------------------------------------------------

    ambience_track = (
        workdir /
        "ambience.wav"
    )

    create_local_ambience(
        actual_duration,
        ambience_track
    )

    # -----------------------------------------------------
    # FINAL AUDIO
    # -----------------------------------------------------

    final_audio = (
        workdir /
        "final_audio.m4a"
    )

    mix_final_audio(
        dialogue_track,
        sfx_track,
        music_track,
        ambience_track,
        actual_duration,
        final_audio
    )

    # -----------------------------------------------------
    # AUDIO + VIDEO
    # -----------------------------------------------------

    video_with_audio = (
        workdir /
        "video_with_audio.mp4"
    )

    add_audio_to_video(
        concat_video,
        final_audio,
        actual_duration,
        video_with_audio
    )

    # -----------------------------------------------------
    # SUBTITLES
    # -----------------------------------------------------

    scene_meta = build_scene_meta(
        scenes
    )

    ass_file = (
        workdir /
        "subtitles.ass"
    )

    create_ass(
        scene_meta,
        ass_file
    )

    final_video = (
        workdir /
        "ABOSARAJ_FINAL.mp4"
    )

    burn_subtitles(
        video_with_audio,
        ass_file,
        final_video
    )

    # -----------------------------------------------------
    # FINAL VALIDATION
    # -----------------------------------------------------

    final_duration = ffprobe_duration(
        final_video
    )

    if final_duration <= 0:
        raise RuntimeError(
            "Final video has invalid duration."
        )

    if not Path(final_video).exists():
        raise RuntimeError(
            "Final video was not created."
        )

    log(
        f"FINAL VIDEO READY: "
        f"{final_video} "
        f"({final_duration:.2f}s)"
    )

    return {
        "video": final_video,
        "duration": final_duration,
        "scene_count": scene_count
    }


# =========================================================
# TELEGRAM UPDATE HANDLING
# =========================================================

def extract_message_text(update):
    message = update.get(
        "message"
    )

    if not message:
        return None, None

    chat = message.get(
        "chat"
    )

    if not chat:
        return None, None

    chat_id = chat.get(
        "id"
    )

    text_value = message.get(
        "text"
    )

    return chat_id, text_value


def default_test_story():
    return """
أميرة تهرب ليلًا من القصر بعد أن يقرر والدها
الملك تزويجها لرجل لا تحبه.

في الغابة تجد ذئبة بيضاء صغيرة مصابة،
وتحاول مساعدتها.

يظهر أمامها رجل غامض وجذاب كانت قد رأته من قبل،
ويخبرها أن عليها العودة إلى القصر فورًا
لأن رجال الملك يبحثون عنها.

ترفض العودة وتكتشف أن الرجل يمتلك قوة خارقة
يحاول إخفاءها.

يصل أحد حراس الملك ويأمر الأميرة بالعودة،
لكن الرجل يقف أمامها ويحميها بطريقة تكشف
جزءًا من قوته.

قبل نهاية المشهد، ترفع الذئبة الصغيرة رأسها
وتنظر إلى الرجل وكأنها تعرفه منذ زمن،
ثم تطلق عواءً غريبًا يجعل الرجل يتغير وجهه فجأة.

تنتهي الحلقة بسؤال:

من أين تعرف الذئبة هذا الرجل؟
""".strip()
        # =========================================================
# TELEGRAM COMMAND / WEBHOOK
# =========================================================

processing_lock = threading.Lock()
processing_chats = set()


def process_story_for_chat(
    chat_id,
    user_idea
):
    """
    Runs the complete Abosaraj pipeline in a background
    thread so Telegram does not time out.
    """

    if chat_id in processing_chats:
        send_message(
            chat_id,
            "⏳ عندي حلقة قيد المعالجة بالفعل. استنى شوي."
        )
        return

    processing_chats.add(chat_id)

    workdir = Path(
        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        send_message(
            chat_id,
            (
                "🎬 Abosaraj بدأ الإنتاج...\n\n"
                "✍️ كتابة السيناريو\n"
                "🎭 تثبيت الشخصيات\n"
                "🎥 تجهيز المشاهد\n"
                "🎙️ تجهيز الأصوات\n"
                "🔊 تصميم الصوت السينمائي\n"
                "🎵 الموسيقى السينمائية\n"
                "📝 الترجمة العربية\n\n"
                "استنى شوي..."
            )
        )

        story = create_story(
            user_idea
        )

        log(
            f"Story generated: "
            f"{story.get('title', 'Untitled')}"
        )

        # Save story for debugging
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
            (
                "🎬 السيناريو جاهز.\n"
                f"📖 {story.get('title', 'Untitled')}\n\n"
                "هلا بنبني المشاهد والصوت."
            )
        )

        result = produce_episode(
            story,
            workdir
        )

        final_video = result["video"]
        duration = result["duration"]
        scene_count = result["scene_count"]

        caption = (
            "🎬 ABOSARAJ\n\n"
            f"📖 {story.get('title', 'حلقة جديدة')}\n"
            f"🎞️ {scene_count} مشاهد\n"
            f"⏱️ {duration:.1f} ثانية\n\n"
            "🎙️ Multi-Character Voices: ON\n"
            "🔊 Cinematic SFX: "
            + (
                "ON"
                if (
                    SOUND_DESIGN_ENABLED
                    and not TEST_MODE
                )
                else "TEST-SILENT"
            )
            + "\n"
            "🎵 Cinematic Score: "
            + (
                "ON"
                if (
                    MUSIC_ENABLED
                    and not TEST_MODE
                )
                else "TEST-SILENT"
            )
            + "\n"
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
            final_video,
            caption
        )

        send_message(
            chat_id,
            (
                "✅ الحلقة وصلت.\n\n"
                "إذا كانت الحركة والصوت والترجمة "
                "تمام، بنرفع مستوى الإنتاج."
            )
        )

    except Exception as exc:

        log(
            "PRODUCTION ERROR:\n"
            + repr(exc)
        )

        try:
            send_message(
                chat_id,
                (
                    "❌ صار خطأ أثناء الإنتاج.\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    "شوف Render Logs وابعتلي آخر "
                    "30-50 سطر."
                )
            )

        except Exception as telegram_error:

            log(
                "Could not send error to Telegram: "
                + repr(telegram_error)
            )

    finally:

        processing_chats.discard(
            chat_id
        )

        # Keep workdir temporarily for debugging
        # instead of deleting immediately.
        #
        # In Render this can later be changed to
        # shutil.rmtree(workdir, ignore_errors=True)
        #
        # Keeping it during testing makes debugging easier.

        log(
            f"Workdir kept for debugging: {workdir}"
        )


def start_processing(
    chat_id,
    user_idea
):
    thread = threading.Thread(
        target=process_story_for_chat,
        args=(
            chat_id,
            user_idea
        ),
        daemon=True
    )

    thread.start()


# =========================================================
# FLASK ROUTES
# =========================================================

@app.get("/")
def home():

    return {
        "status": "ok",
        "service": "Abosaraj",
        "test_mode": TEST_MODE,
        "sound_design": SOUND_DESIGN_ENABLED,
        "music": MUSIC_ENABLED,
        "lipsync": LIPSYNC_ENABLED
    }


@app.get("/health")
def health():

    return {
        "status": "healthy",
        "service": "abosaraj",
        "test_mode": TEST_MODE,
        "wavespeed_configured": bool(
            WAVESPEED_API_KEY
        ),
        "groq_configured": bool(
            GROQ_API_KEY
        ),
        "sound_design_enabled": SOUND_DESIGN_ENABLED,
        "music_enabled": MUSIC_ENABLED,
        "lipsync_enabled": LIPSYNC_ENABLED,
        "lipsync_mode": LIPSYNC_MODE,
        "max_lipsync_scenes": MAX_LIPSYNC_SCENES,
        "production_scene_limit": PRODUCTION_SCENE_LIMIT
    }


@app.post("/webhook")
def webhook():

    update = request.get_json(
        silent=True
    )

    if not update:
        return {
            "ok": True
        }

    chat_id, text_value = (
        extract_message_text(
            update
        )
    )

    if not chat_id:
        return {
            "ok": True
        }

    if not text_value:
        return {
            "ok": True
        }

    text_value = text_value.strip()

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text_value in (
        "/start",
        "/help"
    ):

        send_message(
            chat_id,
            (
                "🎬 أهلاً في Abosaraj.\n\n"
                "ابعثلي فكرة القصة، وأنا أحولها "
                "إلى Microdrama سينمائي.\n\n"
                "مثال:\n"
                "أميرة تهرب من القصر وتلتقي "
                "برجل غامض وذئبة بيضاء..."
            )
        )

        return {
            "ok": True
        }

    # -----------------------------------------------------
    # TEST
    # -----------------------------------------------------

    if text_value == "/test":

        send_message(
            chat_id,
            (
                "🧪 اختبار Abosaraj بدأ.\n\n"
                "سيستخدم القصة التجريبية "
                "الموجودة في النظام."
            )
        )

        start_processing(
            chat_id,
            default_test_story()
        )

        return {
            "ok": True
        }

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if text_value == "/status":

        status = production_status_text(
            SHOT_COUNT
            if PRODUCTION_SCENE_LIMIT == 0
            else PRODUCTION_SCENE_LIMIT,
            TOTAL_DURATION
            if PRODUCTION_SCENE_LIMIT == 0
            else (
                PRODUCTION_SCENE_LIMIT *
                SHOT_DURATION
            )
        )

        send_message(
            chat_id,
            (
                "🤖 Abosaraj Status\n\n"
                f"{status}\n\n"
                f"Wan: "
                f"{'READY' if WAVESPEED_API_KEY else 'NO KEY'}\n"
                f"Groq: "
                f"{'READY' if GROQ_API_KEY else 'NO KEY'}"
            )
        )

        return {
            "ok": True
        }

    # -----------------------------------------------------
    # NORMAL STORY
    # -----------------------------------------------------

    if len(text_value) < 10:

        send_message(
            chat_id,
            (
                "اكتبلي فكرة قصة أطول شوي "
                "عشان أقدر أبني المشاهد."
            )
        )

        return {
            "ok": True
        }

    send_message(
        chat_id,
        (
            "🎬 وصلت الفكرة.\n"
            "هلا ببدأ تحويلها إلى سيناريو سينمائي..."
        )
    )

    start_processing(
        chat_id,
        text_value
    )

    return {
        "ok": True
    }


# =========================================================
# TELEGRAM WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if TEST_MODE:
        log(
            "TEST_MODE=true — webhook setup skipped."
        )
        return

    if not RENDER_EXTERNAL_URL:
        log(
            "RENDER_EXTERNAL_URL not configured; "
            "cannot configure webhook."
        )
        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL}/webhook"
    )

    try:

        response = requests.post(
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/setWebhook",
            data={
                "url": webhook_url
            },
            timeout=30
        )

        log(
            "Webhook response: "
            + response.text
        )

    except Exception as exc:

        log(
            "Webhook setup failed: "
            + repr(exc)
        )


# =========================================================
# STARTUP
# =========================================================

def print_startup():

    log(
        "========================================"
    )

    log(
        "        ABOSARAJ AI VIDEO BOT"
    )

    log(
        "========================================"
    )

    log(
        f"TEST_MODE={TEST_MODE}"
    )

    log(
        f"SOUND_DESIGN_ENABLED="
        f"{SOUND_DESIGN_ENABLED}"
    )

    log(
        f"MUSIC_ENABLED="
        f"{MUSIC_ENABLED}"
    )

    log(
        f"LIPSYNC_ENABLED="
        f"{LIPSYNC_ENABLED}"
    )

    log(
        f"LIPSYNC_MODE="
        f"{LIPSYNC_MODE}"
    )

    log(
        f"MAX_LIPSYNC_SCENES="
        f"{MAX_LIPSYNC_SCENES}"
    )

    log(
        f"PRODUCTION_SCENE_LIMIT="
        f"{PRODUCTION_SCENE_LIMIT}"
    )

    log(
        f"SHOT_COUNT="
        f"{SHOT_COUNT}"
    )

    log(
        f"SHOT_DURATION="
        f"{SHOT_DURATION}"
    )

    log(
        f"TOTAL_DURATION="
        f"{TOTAL_DURATION}"
    )

    log(
        f"MMAUDIO_STEPS="
        f"{MMAUDIO_STEPS}"
    )

    log(
        f"MMAUDIO_GUIDANCE="
        f"{MMAUDIO_GUIDANCE}"
    )

    log(
        f"VOICE_VOLUME="
        f"{VOICE_VOLUME}"
    )

    log(
        f"SFX_VOLUME="
        f"{SFX_VOLUME}"
    )

    log(
        f"MUSIC_VOLUME="
        f"{MUSIC_VOLUME}"
    )

    log(
        f"AMBIENCE_VOLUME="
        f"{AMBIENCE_VOLUME}"
    )

    log(
        "========================================"
    )


if __name__ == "__main__":

    print_startup()

    # Webhook is only configured when we are in
    # real production mode.
    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
