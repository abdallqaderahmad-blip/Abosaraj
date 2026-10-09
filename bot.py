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
from urllib.parse import urlparse

import requests
import edge_tts
from flask import Flask, request, jsonify
from groq import Groq

# =========================================================
# CONFIGURATION
# =========================================================
BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
GROQ_API_KEY = os.environ["GROQ_API_KEY"].strip()
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

# IMPORTANT: TEST_MODE=true never calls paid WaveSpeed APIs.
# Set TEST_MODE=false in Render only when ready for real generation.
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("1", "true", "yes", "on")

SHOT_COUNT = 4
SHOT_DURATION = 5
FPS = 24
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280

# WaveSpeed task polling
WAVESPEED_POLL_SECONDS = 3
WAVESPEED_MAX_WAIT_SECONDS = 600
HTTP_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 180

CLEANUP_WORKDIR = os.getenv("CLEANUP_WORKDIR", "true").lower() in (
    "1", "true", "yes", "on"
)

GROQ_MAX_RETRIES = 2
GROQ_RETRY_DELAY = 12
SCENE_MAX_TOKENS = 450
METADATA_MAX_TOKENS = 220

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WAVESPEED_API_BASE = "https://api.wavespeed.ai/api/v3"
WAVESPEED_IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
WAVESPEED_VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"

# Audio/video styling
TITLE_SECONDS = 2.2
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Noto Sans Arabic")
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "18"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("abosaraj")

groq_client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

processing_chats = set()
processing_lock = threading.Lock()
bot_started_at = time.time()

CHARACTERS = {
    "male_lead": {"name": "الرجل الغامض", "voice": "ar-SY-LaithNeural", "rate": "-10%", "pitch": "-3Hz"},
    "princess": {"name": "الأميرة", "voice": "ar-SA-ZariyahNeural", "rate": "-6%", "pitch": "+1Hz"},
    "king": {"name": "الملك", "voice": "ar-EG-ShakirNeural", "rate": "-8%", "pitch": "-4Hz"},
    "guard": {"name": "الحارس", "voice": "ar-IQ-BasselNeural", "rate": "-2%", "pitch": "-1Hz"},
    "wolf": {"name": "الذئب الأبيض", "voice": None, "rate": None, "pitch": None},
}
CHARACTER_IDS = list(CHARACTERS.keys())

# =========================================================
# GENERAL HELPERS
# =========================================================
def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def safe_filename(value):
    value = re.sub(r"[^\w\-]+", "_", str(value), flags=re.UNICODE)
    return value[:80] or "story"

def run_command(command, timeout=240):
    log.info("RUN: %s", " ".join(map(str, command)))
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout
    )
    if result.returncode != 0:
        log.error("COMMAND ERROR: %s", result.stderr[-3500:])
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            f"{' '.join(map(str, command))}\n{result.stderr[-1800:]}"
        )
    return result

def ensure_ffmpeg():
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} غير مثبت على Render. ثبّت ffmpeg في بيئة التشغيل.")

def probe(path):
    result = run_command([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height",
        "-show_entries", "format=duration,size",
        "-of", "json", str(path)
    ])
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "video_streams": [s for s in streams if s.get("codec_type") == "video"],
    }

def assert_valid_media(path, min_size=1000, require_video=False, require_audio=False):
    path = Path(path)
    if not path.exists() or path.stat().st_size < min_size:
        raise RuntimeError(f"ملف الوسائط مفقود أو فارغ: {path.name}")
    info = probe(path)
    if info["duration"] <= 0:
        raise RuntimeError(f"مدة الملف غير صالحة: {path.name}")
    if require_video and not info["has_video"]:
        raise RuntimeError(f"الملف لا يحتوي مسار فيديو: {path.name}")
    if require_audio and not info["has_audio"]:
        raise RuntimeError(f"الملف لا يحتوي مسار صوت: {path.name}")
    return info

def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def normalize_text(value, max_len=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_len]

# =========================================================
# TELEGRAM
# =========================================================
def telegram_request(method, payload=None, files=None, timeout=60):
    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        data=payload or {},
        files=files,
        timeout=timeout
    )
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"Telegram response was not JSON: HTTP {response.status_code}") from exc
    if not response.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data

def send_message(chat_id, text):
    try:
        return telegram_request("sendMessage", {
            "chat_id": chat_id, "text": str(text or "")[:3900]
        })
    except Exception:
        log.exception("Failed to send Telegram message")
        return None

def send_video(chat_id, video_path, caption=""):
    with open(video_path, "rb") as file_obj:
        return telegram_request(
            "sendVideo",
            {"chat_id": chat_id, "caption": caption[:900], "supports_streaming": "true"},
            files={"video": file_obj},
            timeout=300
        )

# =========================================================
# GROQ STORY GENERATION
# =========================================================
def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("Groq did not return valid JSON")

def groq_json_request(system_prompt, user_prompt, max_tokens=400, temperature=0.6):
    last_error = None
    for attempt in range(GROQ_MAX_RETRIES + 1):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Groq returned empty content")
            return extract_json(content)
        except Exception as exc:
            last_error = exc
            error_text = str(exc).lower()
            if "413" in error_text or "request too large" in error_text:
                break
            if attempt >= GROQ_MAX_RETRIES:
                break
            delay = GROQ_RETRY_DELAY
            if any(token in error_text for token in ("rate_limit", "tokens per minute", "tpm", "429")):
                delay = max(delay, 25)
            log.warning("Groq attempt %s failed; retrying in %ss: %s", attempt + 1, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"Groq request failed: {last_error}")

def generate_story_metadata(story_text):
    data = groq_json_request(
        "أنت كاتب سيناريو. أعد JSON فقط. العنوان عربي قصير، والملخص جملة واحدة.",
        f"""حوّل فكرة المستخدم إلى بيانات لفيلم قصير.
الفكرة: {story_text[:1000]}
أعد JSON بالشكل:
{{"title":"عنوان عربي قصير","genre":"النوع","visual_style":"وصف بصري","summary":"ملخص جملة واحدة"}}""",
        max_tokens=METADATA_MAX_TOKENS,
        temperature=0.5,
    )
    return {
        "title": normalize_text(data.get("title"), 90) or "الحكاية الغامضة",
        "genre": normalize_text(data.get("genre"), 80) or "خيال وتشويق",
        "visual_style": normalize_text(data.get("visual_style"), 220) or "cinematic photorealistic",
        "summary": normalize_text(data.get("summary"), 260) or story_text[:250],
    }

def fallback_scene(number, metadata):
    defaults = [
        ("male_lead", "هناك شيء غريب يحدث هنا.", "يظهر الرجل الغامض عند بوابة قلعة قديمة ليلًا، والضباب يلتف حول الحجارة."),
        ("princess", "لن أغادر قبل أن أعرف الحقيقة.", "تظهر أميرة عربية داخل ممر حجري مضاء بالمشاعل، وتنظر بقلق نحو البوابة."),
        ("guard", "ابتعدوا! هناك خطر يقترب.", "حارس بملابس تاريخية يرفع مشعلًا ويقف أمام باب القلعة بينما تقترب ظلال غامضة."),
        ("male_lead", "الآن فهمت السر، لكن الوقت ينفد.", "الرجل الغامض والأميرة يقفان أمام باب سري انفتح في جدار القلعة، ويتسلل منه ضوء أزرق."),
    ]
    speaker, dialogue, visual = defaults[max(0, min(number - 1, 3))]
    return {
        "scene_number": number, "speaker_id": speaker, "dialogue": dialogue,
        "visual_description": visual,
        "camera": "slow cinematic dolly-in, subtle natural movement",
        "lighting": "dramatic torchlight, volumetric fog, realistic shadows",
        "mood": "mystery and suspense",
        "continuity_note": f"Scene {number} of {metadata['title']}",
    }

def generate_one_scene(number, story_text, metadata, previous_note):
    prompt = f"""اكتب المشهد {number} من أصل 4 لفيلم قصير.
العنوان: {metadata['title']}
النوع: {metadata['genre']}
الأسلوب: {metadata['visual_style']}
ملخص القصة: {metadata['summary'][:220]}
فكرة المستخدم: {story_text[:350]}
المشهد السابق: {previous_note[:140] or 'المشهد الأول'}
الشخصيات المسموحة: male_lead الرجل الغامض، princess الأميرة، king الملك، guard الحارس، wolf ذئب لا يتكلم.
اكتب وصفًا بصريًا محددًا قابلًا للتوليد، مع جملة حوار عربية قصيرة.
أعد JSON فقط بهذا الشكل:
{{"visual_description":"وصف مفصل للمشهد بالإنجليزية أو العربية","camera":"camera motion in English","lighting":"lighting in English","mood":"mood in English","speaker_id":"male_lead أو princess أو king أو guard أو wolf","dialogue":"جملة عربية قصيرة","continuity_note":"one short continuity note"}}"""
    try:
        data = groq_json_request(
            "أنت مخرج سينمائي وكاتب مشاهد. أعد JSON فقط. لا يوجد راوي. اجعل الوصف البصري واضحًا ومحددًا. الذئب لا يتكلم.",
            prompt, max_tokens=SCENE_MAX_TOKENS, temperature=0.65
        )
        fallback = fallback_scene(number, metadata)
        speaker = normalize_text(data.get("speaker_id"), 30)
        if speaker not in CHARACTER_IDS:
            speaker = fallback["speaker_id"]
        dialogue = normalize_text(data.get("dialogue"), 220)
        if speaker == "wolf":
            dialogue = ""
        return {
            "scene_number": number,
            "speaker_id": speaker if dialogue else "",
            "dialogue": dialogue,
            "visual_description": normalize_text(data.get("visual_description"), 900) or fallback["visual_description"],
            "camera": normalize_text(data.get("camera"), 220) or fallback["camera"],
            "lighting": normalize_text(data.get("lighting"), 220) or fallback["lighting"],
            "mood": normalize_text(data.get("mood"), 120) or fallback["mood"],
            "continuity_note": normalize_text(data.get("continuity_note"), 180) or fallback["continuity_note"],
        }
    except Exception:
        log.exception("Scene %s generation failed; using fallback scene text", number)
        return fallback_scene(number, metadata)

def generate_story(story_text):
    story_text = normalize_text(story_text, 4000)
    if not story_text:
        raise ValueError("القصة فارغة.")
    metadata = generate_story_metadata(story_text)
    scenes, previous_note = [], ""
    for number in range(1, SHOT_COUNT + 1):
        log.info("Generating script scene %s/%s", number, SHOT_COUNT)
        scene = generate_one_scene(number, story_text, metadata, previous_note)
        scenes.append(scene)
        previous_note = f"{scene['visual_description'][:100]} Dialogue: {scene['dialogue'][:70]}"
    return {
        **metadata, "original_story": story_text, "scenes": scenes, "created_at": now()
    }

# =========================================================
# EDGE TTS
# =========================================================
async def edge_tts_save(text, voice, rate, pitch, output_path):
    await edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch).save(str(output_path))

def create_silence(output_path, duration=SHOT_DURATION):
    run_command([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration), "-c:a", "pcm_s16le", str(output_path)
    ])

def normalize_audio_to_wav(input_path, output_path):
    run_command([
        "ffmpeg", "-y", "-i", str(input_path), "-vn",
        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(output_path)
    ])

def synthesize_dialogue(scene, output_dir):
    dialogue = scene.get("dialogue", "").strip()
    speaker_id = scene.get("speaker_id", "")
    output_mp3 = output_dir / f"scene_{scene['scene_number']}_voice.mp3"
    output_wav = output_dir / f"scene_{scene['scene_number']}_voice.wav"
    character = CHARACTERS.get(speaker_id)
    if not dialogue or not character or not character.get("voice"):
        create_silence(output_wav)
        return output_wav
    try:
        rate = character.get("rate", "+0%")
        pitch = character.get("pitch", "+0Hz")
        asyncio.run(edge_tts_save(dialogue, character["voice"], rate, pitch, output_mp3))
        if not output_mp3.exists() or output_mp3.stat().st_size < 100:
            raise RuntimeError("Edge TTS returned an empty audio file")
        normalize_audio_to_wav(output_mp3, output_wav)
        assert_valid_media(output_wav, min_size=100, require_audio=True)
        return output_wav
    except Exception:
        log.exception("TTS failed for scene %s; using silence", scene["scene_number"])
        create_silence(output_wav)
        return output_wav

def build_audio_track(scenes, output_dir):
    audio_files = [synthesize_dialogue(scene, output_dir) for scene in scenes]
    list_file = output_dir / "audio_concat.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in audio_files),
        encoding="utf-8"
    )
    output_audio = output_dir / "dialogue_track.wav"
    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output_audio)
    ])
    assert_valid_media(output_audio, require_audio=True)
    return output_audio

# =========================================================
# WAVESPEED REAL IMAGE + VIDEO GENERATION
# =========================================================
def require_production_mode():
    if TEST_MODE:
        raise RuntimeError("TEST_MODE=true: تم إيقاف التوليد المدفوع. غيّر TEST_MODE=false في Render لتوليد AI حقيقي.")
    if not WAVESPEED_API_KEY:
        raise RuntimeError("WAVESPEED_API_KEY غير موجود في Environment على Render.")

def unwrap_data(data):
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data

def wavespeed_submit(model_id, payload):
    require_production_mode()
    url = f"{WAVESPEED_API_BASE}/{model_id}"
    # Do not blindly retry POST: a disconnected response may still be billed.
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=HTTP_TIMEOUT
    )
    if not response.ok:
        raise RuntimeError(f"WaveSpeed submit HTTP {response.status_code}: {response.text[:1200]}")
    task = unwrap_data(response.json())
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError(f"WaveSpeed response missing task id: {str(task)[:800]}")
    log.info("WaveSpeed task submitted model=%s id=%s", model_id, task_id)
    return task_id

def wavespeed_wait(task_id):
    require_production_mode()
    deadline = time.time() + WAVESPEED_MAX_WAIT_SECONDS
    url = f"{WAVESPEED_API_BASE}/predictions/{task_id}/result"
    while time.time() < deadline:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}"},
            timeout=HTTP_TIMEOUT
        )
        if not response.ok:
            raise RuntimeError(f"WaveSpeed result HTTP {response.status_code}: {response.text[:1000]}")
        result = unwrap_data(response.json())
        status = str(result.get("status", "")).lower()
        if status == "completed":
            outputs = result.get("outputs") or []
            if not outputs:
                raise RuntimeError(f"WaveSpeed completed task without outputs: {str(result)[:800]}")
            return outputs
        if status in ("failed", "cancelled", "timeout", "deleted"):
            raise RuntimeError(f"WaveSpeed task {status}: {result.get('error') or str(result)[:800]}")
        time.sleep(WAVESPEED_POLL_SECONDS)
    raise TimeoutError(f"WaveSpeed task {task_id} exceeded {WAVESPEED_MAX_WAIT_SECONDS}s")

def download_output(url, output_path, min_size=1000):
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise RuntimeError(f"Invalid WaveSpeed output URL: {str(url)[:200]}")
    # Only download output URLs; do not send API key to the output host.
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    if not response.ok:
        raise RuntimeError(f"Could not download generated media: HTTP {response.status_code}")
    total = 0
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(1024 * 256):
            if chunk:
                f.write(chunk)
                total += len(chunk)
    if total < min_size:
        raise RuntimeError(f"Downloaded output is too small ({total} bytes)")
    return Path(output_path)

def generate_scene_image(scene, story, output_dir):
    # Prompt is written in English for reliable visual adherence.
    prompt = (
        "Vertical cinematic film still, 9:16 composition, photorealistic, "
        "high detail, natural anatomy, coherent realistic faces, no text, no subtitles, no watermark. "
        f"Story title: {story['title']}. Scene: {scene['visual_description']}. "
        f"Visual style: {story['visual_style']}. Lighting: {scene['lighting']}. "
        f"Mood: {scene['mood']}. Camera composition: {scene['camera']}. "
        "Keep important characters centered in the vertical frame, cinematic depth of field."
    )
    payload = {
        "prompt": prompt,
        "size": "768*1360",
        "output_format": "jpeg",
    }
    task_id = wavespeed_submit(WAVESPEED_IMAGE_MODEL, payload)
    outputs = wavespeed_wait(task_id)
    image_url = next((item for item in outputs if isinstance(item, str) and item.startswith("http")), None)
    if not image_url:
        raise RuntimeError(f"Image model returned no URL: {str(outputs)[:500]}")
    image_path = output_dir / f"scene_{scene['scene_number']}.jpg"
    download_output(image_url, image_path, min_size=5000)
    # Verify FFmpeg can decode it.
    run_command(["ffmpeg", "-v", "error", "-i", str(image_path), "-frames:v", "1", "-f", "null", "-"])
    return image_path

def generate_scene_video(scene, story, image_path, output_dir):
    prompt = (
        f"Cinematic continuous shot. {scene['visual_description']}. "
        f"Motion: {scene['camera']}. Lighting: {scene['lighting']}. "
        f"Atmosphere: {scene['mood']}. Subtle realistic character movement, "
        "natural motion, preserve the input image composition and character identity. "
        "No written text, no subtitles, no logos, no watermark."
    )
    payload = {
        "prompt": prompt,
        "image": image_path.resolve().as_uri(),
        "duration": SHOT_DURATION,
        "seed": -1,
        "negative_prompt": "text, subtitles, watermark, logo, distorted face, extra limbs, flicker, still image",
    }
    # WAN requires a URL accessible to WaveSpeed. file:// paths are not accessible remotely.
    # Upload image to WaveSpeed's file endpoint is required; see upload helper below.
    payload["image"] = upload_image_to_wavespeed(image_path)
    task_id = wavespeed_submit(WAVESPEED_VIDEO_MODEL, payload)
    outputs = wavespeed_wait(task_id)
    video_url = next((item for item in outputs if isinstance(item, str) and item.startswith("http")), None)
    if not video_url:
        raise RuntimeError(f"Video model returned no URL: {str(outputs)[:500]}")
    video_path = output_dir / f"scene_{scene['scene_number']}_ai.mp4"
    download_output(video_url, video_path, min_size=10000)
    info = assert_valid_media(video_path, min_size=10000, require_video=True)
    if info["duration"] < 3:
        raise RuntimeError(f"Generated scene is unexpectedly short: {info['duration']:.2f}s")
    return video_path

def upload_image_to_wavespeed(image_path):
    """
    Upload image using WaveSpeed's documented ticket + PUT flow.
    The API key is sent only to api.wavespeed.ai, never to the storage URL.
    """
    require_production_mode()
    image_path = Path(image_path)
    content_type = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"

    ticket_response = requests.post(
        f"{WAVESPEED_API_BASE}/media/uploads",
        headers={
            "Authorization": f"Bearer {WAVESPEED_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "filename": image_path.name,
            "size": image_path.stat().st_size,
            "content_type": content_type,
        },
        timeout=HTTP_TIMEOUT,
    )
    if not ticket_response.ok:
        raise RuntimeError(
            f"WaveSpeed upload-ticket failed HTTP {ticket_response.status_code}: "
            f"{ticket_response.text[:1000]}"
        )
    ticket = unwrap_data(ticket_response.json())
    upload_info = ticket.get("upload") if isinstance(ticket, dict) else None
    download_url = ticket.get("download_url") if isinstance(ticket, dict) else None
    if not isinstance(upload_info, dict) or not upload_info.get("url") or not download_url:
        raise RuntimeError(f"Invalid WaveSpeed upload ticket: {str(ticket)[:1000]}")

    with image_path.open("rb") as file_obj:
        upload_response = requests.put(
            upload_info["url"],
            headers=upload_info.get("headers") or {},
            data=file_obj,
            timeout=DOWNLOAD_TIMEOUT,
        )
    if not upload_response.ok:
        raise RuntimeError(
            f"WaveSpeed storage upload failed HTTP {upload_response.status_code}: "
            f"{upload_response.text[:800]}"
        )
    return download_url

# =========================================================
# TEST MODE: OBVIOUSLY MARKED PLACEHOLDER ONLY
# =========================================================
def create_test_scene_video(scene, output_dir):
    output = output_dir / f"scene_{scene['scene_number']}_TEST.mp4"
    # Test mode is deliberately not represented as real AI imagery.
    run_command([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"color=c=0x111827:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}",
        "-t", str(SHOT_DURATION), "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output)
    ])
    return output

# =========================================================
# TITLE + ARABIC SUBTITLES + FINAL ENCODE
# =========================================================
def srt_time(seconds):
    milliseconds = int(max(0, seconds) * 1000)
    hours = milliseconds // 3600000
    milliseconds %= 3600000
    minutes = milliseconds // 60000
    milliseconds %= 60000
    secs = milliseconds // 1000
    milliseconds %= 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

def make_subtitle_file(story, scenes, output_dir):
    srt_path = output_dir / "captions_ar.srt"
    entries = []
    # Title is burned into the video at the start, not only sent as Telegram caption.
    title_end = min(TITLE_SECONDS, 2.5)
    entries.append(
        f"1\n{srt_time(0)} --> {srt_time(title_end)}\n{story['title']}\n"
    )
    for index, scene in enumerate(scenes):
        dialogue = normalize_text(scene.get("dialogue"), 220)
        if not dialogue:
            continue
        start = index * SHOT_DURATION + 0.35
        # Dialogue is kept visible across most of its scene; exact word-level timing is not available.
        end = (index + 1) * SHOT_DURATION - 0.25
        if end <= start:
            continue
        entries.append(
            f"{len(entries) + 1}\n{srt_time(start)} --> {srt_time(end)}\n{dialogue}\n"
        )
    srt_path.write_text("\n".join(entries), encoding="utf-8-sig")
    return srt_path

def escape_subtitle_path(path):
    # FFmpeg filter syntax escaping for POSIX paths.
    return str(Path(path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

def render_final_video(scene_paths, audio_path, story, output_dir):
    list_file = output_dir / "scenes_concat.txt"
    # Re-encode each scene to common dimensions/fps first; avoids concat stream mismatch.
    normalized = []
    for i, path in enumerate(scene_paths, start=1):
        out = output_dir / f"normalized_{i}.mp4"
        run_command([
            "ffmpeg", "-y", "-i", str(path),
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={FPS},format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-t", str(SHOT_DURATION), "-movflags", "+faststart", str(out)
        ], timeout=300)
        assert_valid_media(out, min_size=5000, require_video=True)
        normalized.append(out)

    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in normalized),
        encoding="utf-8"
    )
    base_video = output_dir / "base_video.mp4"
    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(base_video)
    ], timeout=300)

    srt_path = make_subtitle_file(story, story["scenes"], output_dir)
    subtitle_filter = (
        f"subtitles='{escape_subtitle_path(srt_path)}':"
        f"force_style='FontName={SUBTITLE_FONT},FontSize={SUBTITLE_FONT_SIZE},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
        "BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=70'"
    )
    final_path = output_dir / "final_video.mp4"
    run_command([
        "ffmpeg", "-y",
        "-i", str(base_video), "-i", str(audio_path),
        "-vf", subtitle_filter,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-af", "apad", "-t", str(SHOT_COUNT * SHOT_DURATION),
        "-movflags", "+faststart", str(final_path)
    ], timeout=420)

    info = assert_valid_media(final_path, min_size=50000, require_video=True, require_audio=True)
    if info["duration"] < SHOT_COUNT * SHOT_DURATION - 1:
        raise RuntimeError(f"Final video too short ({info['duration']:.2f}s); refusing to send.")
    # Confirm there are actual decoded frames.
    run_command([
        "ffmpeg", "-v", "error", "-i", str(final_path),
        "-vf", "select=eq(n\\,0)", "-frames:v", "1", "-f", "null", "-"
    ])
    return final_path

def create_story_video(story, output_dir):
    ensure_ffmpeg()
    if TEST_MODE:
        raise RuntimeError(
            "لن أصنع فيديو وهميًا وأرسله كأنه حقيقي. TEST_MODE=true حاليًا. "
            "لتوليد صور وفيديو AI فعليين، اضبط TEST_MODE=false وأضف WAVESPEED_API_KEY."
        )
    require_production_mode()

    # Generate dialogue track first, but no blank/placeholder video can pass production.
    audio_track = build_audio_track(story["scenes"], output_dir)
    scene_paths = []
    for scene in story["scenes"]:
        number = scene["scene_number"]
        send_log = f"scene {number}/{SHOT_COUNT}"
        log.info("Generating real AI image and video: %s", send_log)
        image_path = generate_scene_image(scene, story, output_dir)
        video_path = generate_scene_video(scene, story, image_path, output_dir)
        scene_paths.append(video_path)

    if len(scene_paths) != SHOT_COUNT:
        raise RuntimeError(f"Expected {SHOT_COUNT} generated videos, got {len(scene_paths)}")
    return render_final_video(scene_paths, audio_track, story, output_dir)

# =========================================================
# CHAT JOB
# =========================================================
def process_story_for_chat(chat_id, story_text):
    chat_key = str(chat_id)
    with processing_lock:
        if chat_key in processing_chats:
            send_message(chat_id, "⏳ في عملية شغالة حاليًا. استنى لحد ما تخلص.")
            return
        processing_chats.add(chat_key)

    workdir = None
    try:
        if TEST_MODE:
            send_message(
                chat_id,
                "⚠️ وضع الاختبار مفعّل حاليًا؛ لن يتم إنشاء أو إرسال فيديو تجريبي فارغ. "
                "اضبط TEST_MODE=false وWAVESPEED_API_KEY على Render لتوليد الفيديو الحقيقي."
            )
            return

        send_message(chat_id, "🎬 بدأنا إنشاء الفيديو الحقيقي: كتابة القصة، توليد الصور، تحريكها، الصوت والعنوان والترجمة.")
        workdir = Path(tempfile.mkdtemp(prefix="abosaraj_"))
        story = generate_story(story_text)
        write_json(workdir / "story.json", story)
        send_message(chat_id, f"🧠 القصة جاهزة: {story['title']}\n🎨 سيجري توليد 4 صور وتحريكها الآن. قد يستغرق ذلك عدة دقائق.")
        final_video = create_story_video(story, workdir)
        info = assert_valid_media(final_video, min_size=50000, require_video=True, require_audio=True)
        send_video(
            chat_id, str(final_video),
            caption=f"🎬 {story['title']}\nالنوع: {story['genre']}\nالمدة: {info['duration']:.1f} ثانية"
        )
        send_message(chat_id, "✅ اكتمل الفيديو بعد فحص وجود الصورة والصوت والعنوان والترجمة.")
    except Exception as exc:
        log.exception("Story processing failed")
        send_message(chat_id, f"❌ فشل إنشاء الفيديو، ولم يتم إرسال ملف ناقص.\nالسبب: {str(exc)[:1000]}")
    finally:
        if workdir and CLEANUP_WORKDIR:
            shutil.rmtree(workdir, ignore_errors=True)
        with processing_lock:
            processing_chats.discard(chat_key)

def launch_story(chat_id, story_text):
    threading.Thread(
        target=process_story_for_chat,
        args=(chat_id, story_text),
        daemon=True
    ).start()

# =========================================================
# TELEGRAM UPDATE HANDLER
# =========================================================
def handle_telegram_update(update):
    if not isinstance(update, dict):
        return
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return
    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        send_message(
            chat_id,
            "🎬 أهلًا بك في Abosaraj AI Cinematic Story Bot.\n\n"
            "أرسل فكرة قصة بالعربية وسأحوّلها إلى فيديو AI بأربع مشاهد، "
            "حوار وصوت عربي، عنوان وترجمة داخل الفيديو.\n\n"
            "/status — حالة البوت\n/help — المساعدة"
        )
        return
    if text.startswith("/help"):
        send_message(chat_id, "أرسل فكرة قصة نصية. الفيديو الحقيقي يحتاج TEST_MODE=false ومفتاح WaveSpeed صالحًا.")
        return
    if text.startswith("/status"):
        with processing_lock:
            active = len(processing_chats)
        send_message(
            chat_id,
            f"🟢 البوت يعمل\nTEST_MODE={TEST_MODE}\n"
            f"WaveSpeed key configured={'yes' if bool(WAVESPEED_API_KEY) else 'no'}\n"
            f"Active jobs={active}\nModel={GROQ_MODEL}"
        )
        return
    if not text:
        send_message(chat_id, "ابعث فكرة القصة كنص مكتوب.")
        return
    launch_story(chat_id, text)

# =========================================================
# FLASK
# =========================================================
@app.get("/")
def index():
    return jsonify({
        "service": "Abosaraj AI Cinematic Story Bot",
        "status": "online",
        "test_mode": TEST_MODE,
        "real_generation_enabled": (not TEST_MODE and bool(WAVESPEED_API_KEY)),
        "scenes": SHOT_COUNT,
    })

@app.get("/health")
def health():
    return jsonify({"status": "ok", "uptime_seconds": int(time.time() - bot_started_at), "test_mode": TEST_MODE})

@app.get("/status")
def status():
    with processing_lock:
        active = len(processing_chats)
    return jsonify({
        "status": "ok", "active_jobs": active, "test_mode": TEST_MODE,
        "wavespeed_key_configured": bool(WAVESPEED_API_KEY), "model": GROQ_MODEL
    })

@app.post("/webhook")
@app.post("/telegram/webhook")
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    try:
        handle_telegram_update(update)
    except Exception:
        log.exception("Webhook handler failed")
    return jsonify({"ok": True})

def configure_telegram_webhook():
    if not RENDER_EXTERNAL_URL:
        log.warning("RENDER_EXTERNAL_URL is empty; webhook was not configured.")
        return
    try:
        telegram_request("setWebhook", {
            "url": f"{RENDER_EXTERNAL_URL}/webhook",
            "drop_pending_updates": "false"
        })
        log.info("Telegram webhook configured.")
    except Exception:
        log.exception("Could not configure Telegram webhook")

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("Starting Abosaraj AI Cinematic Story Bot")
    log.info("TEST_MODE=%s", TEST_MODE)
    log.info("GROQ_MODEL=%s", GROQ_MODEL)
    log.info("WAVESPEED_KEY_CONFIGURED=%s", bool(WAVESPEED_API_KEY))
    log.info("=" * 60)
    ensure_ffmpeg()
    configure_telegram_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
