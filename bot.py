import os
import re
import json
import time
import asyncio
import shutil
import tempfile
import threading
import subprocess
import logging
from pathlib import Path

import requests
import edge_tts

from flask import Flask, request, jsonify
from groq import Groq


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
).strip()

# Keep true while testing.
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in (
    "1", "true", "yes", "on"
)

SHOT_COUNT = 4
SHOT_DURATION = 5
FPS = 24
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280

# Keep 0 during normal testing.
# In production, set to 1 for the first real generation test.
PRODUCTION_SCENE_LIMIT = int(
    os.getenv("PRODUCTION_SCENE_LIMIT", "0")
)

CLEANUP_WORKDIR = os.getenv(
    "CLEANUP_WORKDIR", "true"
).lower() in ("1", "true", "yes", "on")

# Rate-limit safety.
GROQ_MAX_RETRIES = 2
GROQ_RETRY_DELAY = 20

# Maximum output size for each small scene request.
SCENE_MAX_TOKENS = 500
METADATA_MAX_TOKENS = 250

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

WAVESPEED_API_BASE = "https://api.wavespeed.ai/api/v3"

WAVESPEED_IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
WAVESPEED_VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"

# Optional features are disabled by default.
LIPSYNC_ENABLED = False
SOUND_DESIGN_ENABLED = False
MUSIC_ENABLED = False

HTTP_TIMEOUT = 45

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")

# =========================================================
# CLIENTS
# =========================================================

groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

# =========================================================
# GLOBAL STATE
# =========================================================

processing_chats = set()
processing_lock = threading.Lock()

bot_started_at = time.time()

# =========================================================
# CHARACTERS
# =========================================================

CHARACTERS = {
    "male_lead": {
        "name": "الرجل الغامض",
        "voice": "ar-SY-LaithNeural",
        "rate": "-10%",
        "pitch": "-3Hz",
        "description": (
            "رجل عربي غامض، ملامح ثابتة، شعر داكن، "
            "ملابس سينمائية داكنة."
        ),
        "speech": True,
    },
    "princess": {
        "name": "الأميرة",
        "voice": "ar-SA-ZariyahNeural",
        "rate": "-6%",
        "pitch": "+1Hz",
        "description": (
            "أميرة عربية، ملامح واضحة، "
            "ملابس ملكية أنيقة."
        ),
        "speech": True,
    },
    "king": {
        "name": "الملك",
        "voice": "ar-EG-ShakirNeural",
        "rate": "-8%",
        "pitch": "-4Hz",
        "description": (
            "ملك عربي مسن، مهيب، "
            "يرتدي ثيابًا ملكية."
        ),
        "speech": True,
    },
    "guard": {
        "name": "الحارس",
        "voice": "ar-IQ-BasselNeural",
        "rate": "-2%",
        "pitch": "-1Hz",
        "description": (
            "حارس قوي يرتدي درعًا، "
            "شخصية جادة."
        ),
        "speech": True,
    },
    "wolf": {
        "name": "الذئب الأبيض",
        "voice": None,
        "rate": None,
        "pitch": None,
        "description": (
            "جرو ذئب أبيض صغير، "
            "فراء أبيض وعيون واضحة."
        ),
        "speech": False,
    },
}

CHARACTER_IDS = list(CHARACTERS.keys())

# Compact character guide: never resend long character biographies.
CHARACTER_GUIDE = """
الشخصيات المتاحة:
male_lead: الرجل الغامض.
princess: الأميرة.
king: الملك.
guard: الحارس.
wolf: الذئب الأبيض، لا يتكلم، يصدر أصواتًا حيوانية فقط.

القواعد:
- لا يوجد راوي مطلقًا.
- لا تضف شخصيات جديدة إلا عند الضرورة القصوى.
- كل مشهد يحتوي على حوار قصير أو حدث بصري مفهوم.
- speaker_id يجب أن يكون من قائمة الشخصيات.
- إذا لم يوجد حوار، اجعل dialogue فارغًا وspeaker_id فارغًا.
- حوار عربي طبيعي ومختصر.
- لا تجعل الذئب يتكلم بلغة البشر.
"""

# =========================================================
# BASIC HELPERS
# =========================================================

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(value):
    value = re.sub(r"[^\w\-]+", "_", str(value), flags=re.UNICODE)
    return value[:80] or "story"


def run_command(command, timeout=180):
    log.info("RUN: %s", " ".join(map(str, command)))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        log.error("COMMAND ERROR: %s", result.stderr[-3000:])
        raise RuntimeError(
            f"Command failed: {' '.join(map(str, command))}\n"
            f"{result.stderr[-1500:]}"
        )

    return result


def ensure_ffmpeg():
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is not installed. Add ffmpeg to the Render environment."
        )

    if not shutil.which("ffprobe"):
        raise RuntimeError(
            "ffprobe is not installed. Add ffmpeg to the Render environment."
        )


def probe_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])

    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def read_json(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8")
    )


# =========================================================
# TELEGRAM
# =========================================================

def telegram_request(method, payload=None, files=None, timeout=60):
    url = f"{TELEGRAM_API}/{method}"

    response = requests.post(
        url,
        data=payload or {},
        files=files,
        timeout=timeout
    )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            f"Telegram returned non-JSON response: "
            f"{response.status_code}"
        )

    if not response.ok or not data.get("ok"):
        raise RuntimeError(
            f"Telegram {method} failed: {data}"
        )

    return data


def send_message(chat_id, text):
    # Telegram messages have a 4096-character limit.
    text = str(text or "")

    if len(text) > 3900:
        text = text[:3900] + "\n..."

    try:
        return telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text
            }
        )
    except Exception:
        log.exception("Failed to send Telegram message")
        return None


def send_video(chat_id, video_path, caption=""):
    with open(video_path, "rb") as file_obj:
        return telegram_request(
            "sendVideo",
            {
                "chat_id": chat_id,
                "caption": caption[:900],
                "supports_streaming": "true",
            },
            files={
                "video": file_obj
            },
            timeout=240
        )


def get_file_download_url(file_id):
    result = telegram_request(
        "getFile",
        {"file_id": file_id}
    )

    file_path = result["result"]["file_path"]

    return (
        f"https://api.telegram.org/file/bot"
        f"{BOT_TOKEN}/{file_path}"
    )


# =========================================================
# GROQ JSON
# =========================================================

def extract_json(text):
    text = (text or "").strip()

    # Remove common Markdown wrappers.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])

    raise ValueError("Model did not return valid JSON")


def groq_json_request(
    system_prompt,
    user_prompt,
    max_tokens=400,
    temperature=0.6
):
    last_error = None

    for attempt in range(GROQ_MAX_RETRIES + 1):
        try:
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
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content

            if not content:
                raise ValueError("Empty model response")

            return extract_json(content)

        except Exception as exc:
            last_error = exc
            error_text = str(exc).lower()

            is_rate_limit = (
                "rate_limit" in error_text
                or "tokens per minute" in error_text
                or "tpm" in error_text
                or "429" in error_text
            )

            is_request_too_large = (
                "413" in error_text
                or "request too large" in error_text
            )

            # Large requests must not be retried unchanged.
            if is_request_too_large:
                log.error(
                    "Groq request too large; not retrying unchanged."
                )
                break

            if attempt >= GROQ_MAX_RETRIES:
                break

            delay = GROQ_RETRY_DELAY

            if is_rate_limit:
                delay = max(delay, 30)

            log.warning(
                "Groq request failed, attempt %s/%s; "
                "waiting %s seconds. Error: %s",
                attempt + 1,
                GROQ_MAX_RETRIES + 1,
                delay,
                exc
            )

            time.sleep(delay)

    raise RuntimeError(f"Groq request failed: {last_error}")


# =========================================================
# STORY METADATA
# =========================================================

def generate_story_metadata(story_text):
    system_prompt = """
أنت كاتب سيناريو سينمائي.
أعد JSON فقط.
لا تستخدم راويًا.
اجعل العنوان عربيًا قصيرًا.
"""

    user_prompt = f"""
حوّل فكرة المستخدم إلى بيانات مختصرة لفيلم قصير.

الفكرة:
{story_text[:1200]}

أعد:
{{
  "title": "عنوان عربي",
  "genre": "النوع",
  "visual_style": "وصف بصري مختصر",
  "summary": "ملخص من جملة واحدة"
}}
"""

    try:
        data = groq_json_request(
            system_prompt,
            user_prompt,
            max_tokens=METADATA_MAX_TOKENS,
            temperature=0.6
        )

        title = str(data.get("title") or "حكاية غامضة")
        genre = str(data.get("genre") or "دراما خيالية")
        style = str(data.get("visual_style") or "سينمائي واقعي")
        summary = str(data.get("summary") or story_text[:200])

        return {
            "title": title[:100],
            "genre": genre[:100],
            "visual_style": style[:300],
            "summary": summary[:300]
        }

    except Exception:
        log.exception("Metadata generation failed; using fallback")

        return {
            "title": "الحكاية الغامضة",
            "genre": "دراما خيالية",
            "visual_style": "سينمائي واقعي، إضاءة درامية",
            "summary": story_text[:250]
        }


# =========================================================
# SCENE FALLBACK
# =========================================================

def fallback_scene(scene_number, story_text, metadata):
    actions = [
        "يظهر البطل في مكان غامض، ويتأمل ما يحدث حوله.",
        "تظهر الأميرة وتواجه البطل بسؤال مهم.",
        "يقترب الخطر، ويحاول الحارس حماية الشخصيات.",
        "يحدث تحول مفاجئ، وتبقى نهاية المشهد مثيرة."
    ]

    dialogue_options = [
        ("male_lead", "هناك شيء غير طبيعي في هذا المكان."),
        ("princess", "أريد أن أعرف الحقيقة مهما كان الثمن."),
        ("guard", "يجب أن نتحرك الآن، الخطر يقترب."),
        ("male_lead", "هذه ليست النهاية، بل بداية الحقيقة.")
    ]

    index = max(0, min(scene_number - 1, 3))
    speaker, dialogue = dialogue_options[index]

    return {
        "scene_number": scene_number,
        "visual_description": actions[index],
        "camera": "لقطة سينمائية متوسطة مع حركة كاميرا بطيئة",
        "lighting": "إضاءة درامية ناعمة",
        "mood": "غموض وتشويق",
        "speaker_id": speaker,
        "dialogue": dialogue,
        "sound_effect": "",
        "continuity_note": (
            f"المشهد {scene_number} من القصة: {metadata.get('title', '')}"
        )
    }


# =========================================================
# SCENE VALIDATION
# =========================================================

def validate_scene(data, scene_number, story_text, metadata):
    if not isinstance(data, dict):
        return fallback_scene(scene_number, story_text, metadata)

    scene = fallback_scene(scene_number, story_text, metadata)

    scene["visual_description"] = str(
        data.get("visual_description")
        or scene["visual_description"]
    )[:700]

    scene["camera"] = str(
        data.get("camera")
        or scene["camera"]
    )[:250]

    scene["lighting"] = str(
        data.get("lighting")
        or scene["lighting"]
    )[:250]

    scene["mood"] = str(
        data.get("mood")
        or scene["mood"]
    )[:150]

    scene["continuity_note"] = str(
        data.get("continuity_note")
        or scene["continuity_note"]
    )[:250]

    speaker_id = str(data.get("speaker_id") or "").strip()
    dialogue = str(data.get("dialogue") or "").strip()

    if speaker_id not in CHARACTER_IDS:
        speaker_id = ""

    # The wolf cannot speak human language.
    if speaker_id == "wolf":
        speaker_id = ""
        dialogue = ""

    if not dialogue:
        speaker_id = ""

    scene["speaker_id"] = speaker_id
    scene["dialogue"] = dialogue[:500]

    scene["sound_effect"] = str(
        data.get("sound_effect") or ""
    )[:200]

    scene["scene_number"] = scene_number

    return scene


# =========================================================
# SMALL INDEPENDENT SCENE REQUESTS
# =========================================================

def generate_one_scene(
    scene_number,
    story_text,
    metadata,
    previous_scene_summary=""
):
    # IMPORTANT:
    # We never send the entire story history or character bible here.
    # Only the short story summary and the previous scene's one-line note.
    system_prompt = """
أنت كاتب مشاهد سينمائية عربية محترف.
أعد كائن JSON واحدًا فقط.
لا تستخدم راويًا أو تعليقًا صوتيًا.
الحوار يجب أن يكون قصيرًا وطبيعيًا.
استخدم معرفات الشخصيات المحددة فقط.
لا تجعل الذئب يتحدث.
لا تكرر شرح القصة بالكامل.
"""

    user_prompt = f"""
اكتب المشهد رقم {scene_number} من أصل 4.

عنوان الفيلم: {metadata["title"]}
النوع: {metadata["genre"]}
الأسلوب البصري: {metadata["visual_style"]}

ملخص القصة:
{metadata["summary"][:250]}

فكرة المستخدم المختصرة:
{story_text[:450]}

الشخصيات:
male_lead = الرجل الغامض.
princess = الأميرة.
king = الملك.
guard = الحارس.
wolf = ذئب أبيض صغير لا يتكلم.

معلومة الاستمرارية من المشهد السابق:
{previous_scene_summary[:180] or "هذا هو المشهد الأول."}

المطلوب:
- مشهد واحد فقط.
- وصف بصري عملي يمكن تصويره.
- جملة حوار واحدة قصيرة إذا كان الحوار مناسبًا.
- لا تضع أي كلام للراوي.
- لا تضف مفاتيح JSON أخرى.

أعد JSON بهذا الشكل:
{{
  "visual_description": "وصف بصري",
  "camera": "حركة الكاميرا",
  "lighting": "الإضاءة",
  "mood": "المزاج",
  "speaker_id": "male_lead أو princess أو king أو guard أو فارغ",
  "dialogue": "جملة الحوار أو نص فارغ",
  "sound_effect": "مؤثر صوتي مختصر أو فارغ",
  "continuity_note": "جملة قصيرة تساعد على استمرار القصة"
}}
"""

    try:
        data = groq_json_request(
            system_prompt,
            user_prompt,
            max_tokens=SCENE_MAX_TOKENS,
            temperature=0.7
        )

        return validate_scene(
            data,
            scene_number,
            story_text,
            metadata
        )

    except Exception as exc:
        log.exception(
            "Scene %s generation failed; using fallback: %s",
            scene_number,
            exc
        )

        return fallback_scene(
            scene_number,
            story_text,
            metadata
        )


# =========================================================
# STORY ENGINE
# =========================================================

def generate_story(story_text):
    story_text = str(story_text or "").strip()

    if not story_text:
        raise ValueError("القصة فارغة.")

    metadata = generate_story_metadata(story_text)

    scenes = []
    previous_summary = ""

    for scene_number in range(1, SHOT_COUNT + 1):
        log.info("Generating scene %s/%s", scene_number, SHOT_COUNT)

        scene = generate_one_scene(
            scene_number=scene_number,
            story_text=story_text,
            metadata=metadata,
            previous_scene_summary=previous_summary
        )

        scenes.append(scene)

        previous_summary = (
            f"المشهد {scene_number}: "
            f"{scene.get('visual_description', '')[:100]}. "
            f"الحوار: {scene.get('dialogue', '')[:80]}"
        )

    # Always return exactly four scenes.
    while len(scenes) < SHOT_COUNT:
        number = len(scenes) + 1
        scenes.append(
            fallback_scene(number, story_text, metadata)
        )

    return {
        "title": metadata["title"],
        "genre": metadata["genre"],
        "visual_style": metadata["visual_style"],
        "summary": metadata["summary"],
        "original_story": story_text[:4000],
        "scenes": scenes[:SHOT_COUNT],
        "created_at": now()
    }


# =========================================================
# TTS / AUDIO
# =========================================================

async def _edge_tts_save(text, voice, rate, pitch, output_path):
    communicator = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch
    )

    await communicator.save(str(output_path))


def normalize_pitch(pitch):
    """
    Return a valid Edge TTS pitch string.
    Examples: +1Hz, -3Hz, +0Hz.
    """
    match = re.fullmatch(
        r"\s*([+-]?\d+)\s*Hz\s*",
        str(pitch or "+0Hz"),
        flags=re.IGNORECASE
    )

    if not match:
        return "+0Hz"

    value = int(match.group(1))

    # Keep pitch changes conservative.
    value = max(-10, min(10, value))

    return f"{value:+d}Hz"


def normalize_rate(rate):
    match = re.fullmatch(
        r"\s*([+-]?\d+)\s*%\s*",
        str(rate or "+0%"),
    )

    if not match:
        return "+0%"

    value = int(match.group(1))
    value = max(-30, min(30, value))

    return f"{value:+d}%"


def create_silence(output_path, duration=SHOT_DURATION):
    run_command([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-c:a", "pcm_s16le",
        str(output_path)
    ])


def normalize_audio_to_wav(input_path, output_path):
    run_command([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "pcm_s16le",
        str(output_path)
    ])


def synthesize_dialogue(scene, output_dir):
    dialogue = (scene.get("dialogue") or "").strip()
    speaker_id = scene.get("speaker_id") or ""

    output_mp3 = output_dir / f"scene_{scene['scene_number']}_voice.mp3"
    output_wav = output_dir / f"scene_{scene['scene_number']}_voice.wav"

    if not dialogue or speaker_id not in CHARACTERS:
        create_silence(output_wav, SHOT_DURATION)
        return output_wav

    character = CHARACTERS[speaker_id]
    voice = character.get("voice")

    if not voice:
        create_silence(output_wav, SHOT_DURATION)
        return output_wav

    rate = normalize_rate(character.get("rate"))
    pitch = normalize_pitch(character.get("pitch"))

    try:
        asyncio.run(
            _edge_tts_save(
                dialogue,
                voice,
                rate,
                pitch,
                output_mp3
            )
        )

        if (
            not output_mp3.exists()
            or output_mp3.stat().st_size < 100
        ):
            raise RuntimeError("TTS produced an empty audio file")

        normalize_audio_to_wav(output_mp3, output_wav)

        if (
            not output_wav.exists()
            or output_wav.stat().st_size < 100
        ):
            raise RuntimeError("Audio conversion produced an empty file")

        return output_wav

    except Exception as exc:
        log.exception(
            "TTS failed for scene %s: %s",
            scene["scene_number"],
            exc
        )

        create_silence(output_wav, SHOT_DURATION)
        return output_wav


def build_dialogue_track(scenes, output_dir):
    audio_files = []

    for scene in scenes:
        audio_files.append(
            synthesize_dialogue(scene, output_dir)
        )

    list_file = output_dir / "audio_concat.txt"

    lines = []

    for audio_path in audio_files:
        escaped = str(audio_path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")

    list_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    output_audio = output_dir / "dialogue_track.wav"

    run_command([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:a", "pcm_s16le",
        "-ar", "44100",
        "-ac", "2",
        str(output_audio)
    ])

    return output_audio


# =========================================================
# TEST VIDEO
# =========================================================

def create_test_scene_video(scene, output_dir):
    """
    Creates a simple local test video.
    This is NOT an AI-generated scene and does NOT call WaveSpeed.
    """
    number = scene["scene_number"]
    output_path = output_dir / f"scene_{number}.mp4"

    # A subtle animated abstract background.
    # No paid generation API is called in this function.
    duration = SHOT_DURATION

    filter_complex = (
        "color=c=0x111827:s=720x1280:r=24,"
        "format=yuv420p,"
        "drawbox=x=40:y=300:w=640:h=680:"
        "color=0x243247@0.8:t=fill,"
        "drawbox=x=70:y=340:w=580:h=600:"
        "color=0x172033@0.9:t=fill"
    )

    run_command([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", filter_complex,
        "-t", str(duration),
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ])

    return output_path


def concatenate_videos(video_paths, output_dir):
    list_file = output_dir / "videos_concat.txt"

    lines = []

    for path in video_paths:
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")

    list_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    output_video = output_dir / "video_concat.mp4"

    run_command([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_video)
    ])

    return output_video


def mux_audio_video(video_path, audio_path, output_dir):
    output_path = output_dir / "final_video.mp4"

    run_command([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path)
    ])

    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise RuntimeError("Final video is missing or too small")

    return output_path


# =========================================================
# WAVESPEED GUARD
# =========================================================

def require_production_mode():
    if TEST_MODE:
        raise RuntimeError(
            "WaveSpeed generation blocked because TEST_MODE=true."
        )

    if not WAVESPEED_API_KEY:
        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )


def wavespeed_headers():
    require_production_mode()

    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type": "application/json"
    }


def wavespeed_submit(model, payload):
    """
    This function is intentionally unreachable in TEST_MODE.
    Confirm the model-specific request schema against the current
    WaveSpeed documentation before enabling real generation.
    """
    require_production_mode()

    url = f"{WAVESPEED_API_BASE}/{model}"

    response = requests.post(
        url,
        headers=wavespeed_headers(),
        json=payload,
        timeout=HTTP_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            f"WaveSpeed submission failed ({response.status_code}): "
            f"{response.text[:1500]}"
        )

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError("WaveSpeed returned an invalid response")

    return data


def wavespeed_get_result(task_id):
    require_production_mode()

    url = (
        f"{WAVESPEED_API_BASE}/predictions/"
        f"{task_id}/result"
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {WAVESPEED_API_KEY}"
        },
        timeout=HTTP_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            f"WaveSpeed polling failed ({response.status_code}): "
            f"{response.text[:1000]}"
        )

    return response.json()


# =========================================================
# PIPELINE
# =========================================================

def create_story_video(story, output_dir):
    ensure_ffmpeg()

    scenes = story["scenes"]

    # Generate and validate all dialogue before video processing.
    send_audio = build_dialogue_track(scenes, output_dir)

    video_scenes = scenes

    if not TEST_MODE and PRODUCTION_SCENE_LIMIT > 0:
        video_scenes = scenes[:PRODUCTION_SCENE_LIMIT]

    video_paths = []

    if TEST_MODE:
        log.info(
            "TEST_MODE=true: creating local placeholder scenes. "
            "WaveSpeed is disabled."
        )

        for scene in video_scenes:
            video_paths.append(
                create_test_scene_video(scene, output_dir)
            )

    else:
        # Do not pretend production generation is implemented here.
        # The model payload schema must be verified first.
        raise RuntimeError(
            "Real image/video generation is not enabled in this safe "
            "replacement yet. Keep TEST_MODE=true until the current "
            "WaveSpeed model request schema is verified and integrated."
        )

    combined_video = concatenate_videos(
        video_paths,
        output_dir
    )

    final_video = mux_audio_video(
        combined_video,
        send_audio,
        output_dir
    )

    return final_video


# =========================================================
# CHAT PROCESSING
# =========================================================

def process_story_for_chat(chat_id, story_text):
    chat_key = str(chat_id)

    with processing_lock:
        if chat_key in processing_chats:
            send_message(
                chat_id,
                "⏳ في عملية شغالة حاليًا. استنى لحد ما تخلص."
            )
            return

        processing_chats.add(chat_key)

    workdir = None

    try:
        send_message(
            chat_id,
            "🎬 بدأنا تجهيز القصة.\n"
            "رح نولّد أربعة مشاهد ونختبر الحوار والصوت."
        )

        workdir = Path(
            tempfile.mkdtemp(prefix="abosaraj_")
        )

        send_message(
            chat_id,
            "🧠 جاري كتابة القصة وتقسيمها إلى أربعة مشاهد..."
        )

        story = generate_story(story_text)

        write_json(
            workdir / "story.json",
            story
        )

        log.info(
            "Story generated: %s | scenes=%s",
            story["title"],
            len(story["scenes"])
        )

        send_message(
            chat_id,
            f"✅ تم تجهيز القصة: {story['title']}\n"
            f"عدد المشاهد: {len(story['scenes'])}\n"
            "🔊 جاري تجهيز الأصوات..."
        )

        send_message(
            chat_id,
            "🎞️ جاري تجهيز الفيديو التجريبي المحلي..."
            if TEST_MODE
            else "🎞️ جاري تجهيز الفيديو..."
        )

        final_video = create_story_video(
            story,
            workdir
        )

        send_video(
            chat_id,
            str(final_video),
            caption=(
                f"🎬 {story['title']}\n"
                f"النوع: {story['genre']}\n"
                + (
                    "وضع الاختبار: فيديو تجريبي محلي، "
                    "وليس توليد صور AI."
                    if TEST_MODE
                    else ""
                )
            )
        )

        send_message(
            chat_id,
            "✅ اكتملت العملية.\n"
            "تم إنشاء أربعة مشاهد واختبار الصوت وتجميع الفيديو."
        )

    except Exception as exc:
        log.exception("Story processing failed")

        send_message(
            chat_id,
            "❌ تعذر إكمال العملية.\n"
            f"السبب: {str(exc)[:1200]}"
        )

    finally:
        if workdir and CLEANUP_WORKDIR:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                log.exception("Workdir cleanup failed")

        with processing_lock:
            processing_chats.discard(chat_key)


def launch_story(chat_id, story_text):
    thread = threading.Thread(
        target=process_story_for_chat,
        args=(chat_id, story_text),
        daemon=True
    )
    thread.start()


# =========================================================
# TELEGRAM UPDATE HANDLER
# =========================================================

def handle_telegram_update(update):
    if not isinstance(update, dict):
        return

    message = (
        update.get("message")
        or update.get("edited_message")
    )

    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if chat_id is None:
        return

    text = (message.get("text") or "").strip()

    if not text:
        send_message(
            chat_id,
            "ابعثلي فكرة القصة كنص مكتوب، وأنا بحوّلها إلى أربعة مشاهد."
        )
        return

    if text.startswith("/start"):
        send_message(
            chat_id,
            "🎬 أهلًا بك في Abosaraj AI Cinematic Story Bot.\n\n"
            "ابعثلي فكرة قصة، وسأجهز أربعة مشاهد بحوار عربي "
            "وأصوات الشخصيات.\n\n"
            "أوامر البوت:\n"
            "/start — البداية\n"
            "/help — المساعدة\n"
            "/status — حالة البوت\n"
            "/test — اختبار القصة والصوت والفيديو المحلي\n\n"
            "ملاحظة: وضع الاختبار لا يولّد صورًا أو فيديو حقيقيًا "
            "عبر WaveSpeed."
        )
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            "أرسل فكرة قصة بالعربية.\n"
            "سيتم إنشاء أربعة مشاهد، وتجهيز الحوار والأصوات، "
            "ثم إنشاء فيديو تجريبي محلي."
        )
        return

    if text.startswith("/status"):
        uptime = int(time.time() - bot_started_at)

        send_message(
            chat_id,
            "🟢 البوت يعمل.\n"
            f"TEST_MODE: {TEST_MODE}\n"
            f"Groq model: {GROQ_MODEL}\n"
            f"Uptime: {uptime} seconds\n"
            f"WaveSpeed generation: "
            f"{'BLOCKED' if TEST_MODE else 'NOT ENABLED'}"
        )
        return

    if text.startswith("/test"):
        launch_story(
            chat_id,
            "قصة خيالية قصيرة عن رجل غامض يلتقي أميرة "
            "في قلعة قديمة، ويظهر ذئب أبيض صغير يحمل سرًا."
        )
        return

    # Any other text is treated as a story idea.
    launch_story(chat_id, text)


# =========================================================
# FLASK ROUTES
# =========================================================

@app.get("/")
def index():
    return jsonify({
        "service": "Abosaraj AI Cinematic Story Bot",
        "status": "online",
        "test_mode": TEST_MODE,
        "model": GROQ_MODEL,
        "scenes": SHOT_COUNT,
        "wavespeed_enabled": not TEST_MODE
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "uptime_seconds": int(time.time() - bot_started_at),
        "test_mode": TEST_MODE
    })


@app.get("/status")
def status():
    with processing_lock:
        active = len(processing_chats)

    return jsonify({
        "status": "ok",
        "active_jobs": active,
        "test_mode": TEST_MODE,
        "model": GROQ_MODEL
    })


@app.get("/test")
def http_test():
    # Do not add the chat to processing_chats here.
    # process_story_for_chat handles its own lock.
    launch_story(
        "HTTP_TEST",
        "قصة اختبار عن أميرة ورجل غامض داخل قلعة قديمة."
    )

    return jsonify({
        "started": True,
        "test_mode": TEST_MODE
    })


@app.post("/webhook")
@app.post("/telegram/webhook")
def telegram_webhook():
    try:
        update = request.get_json(silent=True) or {}

        handle_telegram_update(update)

        return jsonify({"ok": True})

    except Exception:
        log.exception("Webhook handler failed")

        # Return 200 to avoid repeated Telegram delivery for
        # unexpected processing errors in the handler.
        return jsonify({"ok": True})


# =========================================================
# TELEGRAM WEBHOOK SETUP
# =========================================================

def configure_telegram_webhook():
    if not RENDER_EXTERNAL_URL:
        log.warning(
            "RENDER_EXTERNAL_URL is empty; webhook was not configured."
        )
        return

    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"

    try:
        result = telegram_request(
            "setWebhook",
            {
                "url": webhook_url,
                "drop_pending_updates": "false"
            }
        )

        log.info("Telegram webhook configured: %s", result)

    except Exception:
        log.exception("Could not configure Telegram webhook")


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("Starting Abosaraj AI Cinematic Story Bot")
    log.info("TEST_MODE=%s", TEST_MODE)
    log.info("GROQ_MODEL=%s", GROQ_MODEL)
    log.info("SCENES=%s", SHOT_COUNT)
    log.info("=" * 60)

    ensure_ffmpeg()

    if not TEST_MODE:
        log.warning(
            "Production mode selected, but real WaveSpeed video "
            "generation remains deliberately disabled pending "
            "verification of the current model API schema."
        )

    configure_telegram_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
