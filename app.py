import os
import re
import json
import uuid
import time
import shutil
import logging
import asyncio
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import edge_tts


# =========================================================
# 1. CONFIGURATION
# =========================================================

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PEXELS_KEY = (os.getenv("PEXELS_KEY") or "").strip()

PORT = int(os.getenv("PORT", "10000"))

SCENES = 6
SECONDS_PER_SCENE = 5
TOTAL_SECONDS = SCENES * SECONDS_PER_SCENE

WIDTH = 720
HEIGHT = 1280
FPS = 25

VOICE = os.getenv(
    "TTS_VOICE",
    "ar-SA-HamedNeural"
).strip()

VOICE_RATE = os.getenv(
    "TTS_RATE",
    "-8%"
).strip()

VIDEO_TIMEOUT = 45
PEXELS_TIMEOUT = 20

MAX_STORY_LENGTH = 5000
MAX_DOWNLOAD_BYTES = 35 * 1024 * 1024

BASE_DIR = Path("/tmp/zil")
BASE_DIR.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
]

busy_users = set()
busy_lock = threading.Lock()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("zil")

app = Flask(__name__)


# =========================================================
# 2. CHECK REQUIRED TOOLS
# =========================================================

def check_binary(name):
    return shutil.which(name) is not None


def run_command(command, timeout=120):
    """
    Run a subprocess without exposing environment secrets.
    """
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )

    if result.returncode != 0:
        error_text = (result.stderr or "")[-1800:]
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            f"{error_text}"
        )

    return result


def validate_environment():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not check_binary("ffmpeg"):
        missing.append("ffmpeg")

    if not check_binary("ffprobe"):
        missing.append("ffprobe")

    if missing:
        raise RuntimeError(
            "Missing required configuration/tools: "
            + ", ".join(missing)
        )


# =========================================================
# 3. HTTP SESSION
# =========================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "ZIL-VideoBot/1.0",
})


# =========================================================
# 4. STORY PROCESSING
# =========================================================

def clean_story(text):
    text = (text or "").strip()

    text = re.sub(
        r"^/(?:zil|ظل|test)(?:@\w+)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > MAX_STORY_LENGTH:
        text = text[:MAX_STORY_LENGTH]

    return text


def split_sentences(text):
    """
    Split a story at natural sentence boundaries.
    Avoid splitting Arabic words into arbitrary fragments.
    """
    text = clean_story(text)

    if not text:
        return []

    parts = re.split(
        r"(?<=[.!؟?。])\s+|[\n\r]+",
        text,
    )

    parts = [
        item.strip(" \t،,;؛")
        for item in parts
        if item.strip(" \t،,;؛")
    ]

    return parts


def make_scene_texts(story):
    """
    Create six readable scene captions.
    Preserve sentence boundaries whenever possible.
    """
    sentences = split_sentences(story)

    if not sentences:
        raise ValueError(
            "اكتب القصة بعد الأمر، حتى أستطيع إنشاء المشاهد."
        )

    if len(sentences) >= SCENES:
        result = sentences[:SCENES]

        if len(sentences) > SCENES:
            result[-1] = (
                result[-1]
                + " "
                + " ".join(sentences[SCENES:])
            )

        return result

    words = story.split()

    if not words:
        raise ValueError("القصة فارغة.")

    # Distribute words approximately evenly across six scenes.
    result = []
    total_words = len(words)

    for i in range(SCENES):
        start = round(i * total_words / SCENES)
        end = round((i + 1) * total_words / SCENES)

        segment = " ".join(words[start:end]).strip()

        if segment:
            result.append(segment)

    # If the story is too short, reuse the last complete segment.
    while len(result) < SCENES:
        result.append(result[-1] if result else story)

    return result[:SCENES]


# =========================================================
# 5. PEXELS SEARCH
# =========================================================

ARABIC_TO_ENGLISH = {
    "قصر": "royal castle",
    "ملك": "king royal palace",
    "امير": "medieval prince",
    "أمير": "medieval prince",
    "اميرة": "princess castle",
    "أميرة": "princess castle",
    "حب": "romantic couple",
    "حب": "romantic couple",
    "غابة": "dark forest",
    "ليل": "night cinematic",
    "ليل": "night cinematic",
    "قتال": "martial arts fight",
    "معركة": "battle cinematic",
    "نمر": "tiger wildlife",
    "أسد": "lion wildlife",
    "اسد": "lion wildlife",
    "ذئب": "wolf wildlife",
    "ذئب": "wolf wildlife",
    "وحش": "monster fantasy",
    "سيف": "sword medieval",
    "مطر": "rain storm",
    "مطر": "rain storm",
    "بحر": "ocean waves",
    "جبل": "mountain landscape",
    "مدينة": "city night",
    "سيارة": "sports car cinematic",
    "فضاء": "space stars",
    "سر": "mysterious person",
    "غامض": "mysterious man",
    "قوة": "dramatic action",
    "خطر": "dark dramatic scene",
    "دم": "dramatic thriller",
    "سحر": "fantasy magic",
    "طيران": "aerial cinematic",
    "سقوط": "dramatic fall",
}


def search_terms(text, scene_index):
    normalized = text.lower()

    for arabic_word, english_query in ARABIC_TO_ENGLISH.items():
        if arabic_word in normalized:
            return english_query

    defaults = [
        "cinematic dramatic landscape",
        "mysterious man cinematic",
        "dark forest cinematic",
        "royal castle cinematic",
        "dramatic storm landscape",
        "cinematic night city",
    ]

    return defaults[scene_index % len(defaults)]


def pexels_search(query):
    if not PEXELS_KEY:
        raise RuntimeError(
            "PEXELS_KEY غير موجود في إعدادات Render."
        )

    response = SESSION.get(
        "https://api.pexels.com/videos/search",
        headers={
            "Authorization": PEXELS_KEY,
        },
        params={
            "query": query,
            "orientation": "portrait",
            "size": "small",
            "per_page": 10,
        },
        timeout=PEXELS_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pexels search failed: HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("Pexels returned invalid JSON.")

    videos = payload.get("videos", [])

    if not videos:
        return None

    candidates = []

    for video in videos:
        duration = video.get("duration", 0)

        if duration and duration < 3:
            continue

        for file_info in video.get("video_files", []):
            link = file_info.get("link")

            if not link:
                continue

            parsed = urlparse(link)

            if parsed.scheme != "https":
                continue

            if not parsed.hostname:
                continue

            width = file_info.get("width") or 0
            height = file_info.get("height") or 0
            quality = file_info.get("quality", "")

            # Prefer portrait video with a reasonable resolution.
            portrait_bonus = 1000 if height > width else 0
            resolution_score = min(width, 1080) + min(height, 1920)

            quality_bonus = (
                100 if quality == "sd" else 0
            )

            score = (
                portrait_bonus
                + resolution_score
                + quality_bonus
            )

            candidates.append((score, link))

    if not candidates:
        return None

    candidates.sort(reverse=True, key=lambda item: item[0])

    return candidates[0][1]


# =========================================================
# 6. DOWNLOAD AND VALIDATE VIDEO
# =========================================================

def probe_video(path):
    path = str(path)

    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_type,width,height,duration",
        "-of", "json",
        path,
    ], timeout=25)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False

    streams = data.get("streams", [])

    if not streams:
        return False

    stream = streams[0]

    if stream.get("codec_type") != "video":
        return False

    if int(stream.get("width") or 0) < 100:
        return False

    if int(stream.get("height") or 0) < 100:
        return False

    return True


def download_clip(url, output_path):
    output_path = Path(output_path)
    temp_path = output_path.with_suffix(".part")

    try:
        with SESSION.get(
            url,
            stream=True,
            timeout=(15, VIDEO_TIMEOUT),
            allow_redirects=True,
        ) as response:

            response.raise_for_status()

            final_host = urlparse(response.url).hostname

            if not final_host:
                raise RuntimeError(
                    "Invalid final video URL."
                )

            content_length = response.headers.get(
                "Content-Length"
            )

            if content_length:
                try:
                    if int(content_length) > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(
                            "Video file is larger than the download limit."
                        )
                except ValueError:
                    pass

            total = 0

            with open(temp_path, "wb") as file:
                for chunk in response.iter_content(
                    chunk_size=256 * 1024
                ):
                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(
                            "Video exceeded the download size limit."
                        )

                    file.write(chunk)

        if temp_path.stat().st_size < 30_000:
            raise RuntimeError(
                "Downloaded video is unexpectedly small."
            )

        if not probe_video(temp_path):
            raise RuntimeError(
                "Downloaded file is not a valid video."
            )

        temp_path.replace(output_path)

        return True

    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

        raise


def obtain_clip(scene_text, index, workdir):
    """
    Search several queries. Never replace a missing clip
    with a black screen or a blank color background.
    """
    queries = [
        search_terms(scene_text, index),
        "cinematic dramatic scene",
        "cinematic landscape",
    ]

    attempted = set()
    errors = []

    for query in queries:
        if query in attempted:
            continue

        attempted.add(query)

        try:
            url = pexels_search(query)

            if not url:
                errors.append(f"No results for: {query}")
                continue

            path = workdir / f"source_{index:02d}.mp4"

            download_clip(url, path)

            log.info(
                "Scene %s downloaded and validated.",
                index + 1,
            )

            return path

        except Exception as exc:
            # Do not log URLs, tokens, or full request objects.
            log.warning(
                "Scene %s failed for query '%s': %s",
                index + 1,
                query,
                str(exc)[:250],
            )

            errors.append(query)

    raise RuntimeError(
        f"تعذر تنزيل فيديو صالح للمشهد {index + 1}. "
        "تحقق من صلاحية PEXELS_KEY واتصال Render."
    )


# =========================================================
# 7. ARABIC CAPTION RENDERING
# =========================================================

def find_font():
    for font_path in FONT_CANDIDATES:
        if Path(font_path).exists():
            return font_path

    raise RuntimeError(
        "لم يتم العثور على خط مناسب. "
        "ثبّت fonts-dejavu-core في Dockerfile."
    )


def shape_arabic(text):
    text = (text or "").strip()

    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        log.exception("Arabic shaping failed.")
        return text


def wrap_text(draw, text, font, max_width):
    """
    Wrap text using shaped Arabic display text.
    """
    words = text.split()

    if not words:
        return []

    lines = []
    current = ""

    for word in words:
        candidate = (
            current + " " + word
        ).strip()

        shaped_candidate = shape_arabic(candidate)

        box = draw.textbbox(
            (0, 0),
            shaped_candidate,
            font=font,
            stroke_width=0,
        )

        width = box[2] - box[0]

        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def create_caption_image(text, output_path):
    """
    Render a transparent overlay with readable Arabic.
    """
    image = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(image)

    font_path = find_font()
    font_size = 40

    while font_size >= 25:
        font = ImageFont.truetype(
            font_path,
            font_size,
        )

        lines = wrap_text(
            draw,
            text,
            font,
            WIDTH - 100,
        )

        if len(lines) <= 4:
            break

        font_size -= 2

    if not lines:
        lines = [text]

    line_heights = []

    for line in lines:
        shaped = shape_arabic(line)

        bbox = draw.textbbox(
            (0, 0),
            shaped,
            font=font,
            stroke_width=1,
        )

        line_heights.append(
            max(1, bbox[3] - bbox[1])
        )

    spacing = 15
    padding_x = 28
    padding_y = 22

    text_height = (
        sum(line_heights)
        + spacing * max(0, len(lines) - 1)
    )

    panel_height = text_height + padding_y * 2

    panel_top = HEIGHT - panel_height - 110

    panel_top = max(
        50,
        min(panel_top, HEIGHT - panel_height - 35),
    )

    panel = Image.new(
        "RGBA",
        (WIDTH - 40, panel_height),
        (0, 0, 0, 0),
    )

    panel_draw = ImageDraw.Draw(panel)

    panel_draw.rounded_rectangle(
        (0, 0, panel.width - 1, panel.height - 1),
        radius=24,
        fill=(0, 0, 0, 170),
        outline=(255, 255, 255, 45),
        width=2,
    )

    y = padding_y

    for index, line in enumerate(lines):
        shaped = shape_arabic(line)

        bbox = panel_draw.textbbox(
            (0, 0),
            shaped,
            font=font,
            stroke_width=1,
        )

        text_width = bbox[2] - bbox[0]

        x = (panel.width - text_width) // 2

        panel_draw.text(
            (x, y),
            shaped,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 220),
        )

        y += line_heights[index] + spacing

    image.alpha_composite(
        panel,
        (20, panel_top),
    )

    image.save(output_path)

    return output_path


# =========================================================
# 8. NORMALIZE EACH VIDEO SCENE
# =========================================================

def build_scene(source_path, caption_path, output_path):
    """
    Convert each source clip into a uniform 5-second vertical
    H.264 segment with no black padding.
    """
    filter_complex = (
        f"[0:v]"
        f"scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"fps={FPS},"
        f"setsar=1,"
        f"trim=duration={SECONDS_PER_SCENE},"
        f"setpts=PTS-STARTPTS"
        f"[base];"
        f"[1:v]"
        f"format=rgba"
        f"[caption];"
        f"[base][caption]"
        f"overlay=0:0:shortest=1,"
        f"format=yuv420p"
        f"[out]"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-stream_loop", "-1",
        "-i", str(source_path),
        "-loop", "1",
        "-i", str(caption_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-an",
        "-t", str(SECONDS_PER_SCENE),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ], timeout=150)

    if not output_path.exists():
        raise RuntimeError("Scene render failed.")

    if output_path.stat().st_size < 30_000:
        raise RuntimeError("Rendered scene is too small.")

    return output_path


# =========================================================
# 9. ARABIC NARRATION
# =========================================================

async def edge_tts_to_file(text, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE,
    )

    await communicate.save(str(output_path))


def make_voice(text, output_path):
    """
    Keep Edge TTS as the primary voice engine.
    Fall back to gTTS only if Edge TTS fails.
    """
    try:
        asyncio.run(
            edge_tts_to_file(text, output_path)
        )

        if (
            output_path.exists()
            and output_path.stat().st_size > 1000
        ):
            return output_path

        raise RuntimeError(
            "Edge TTS produced an empty audio file."
        )

    except Exception as exc:
        log.warning(
            "Edge TTS failed; trying gTTS: %s",
            str(exc)[:250],
        )

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False,
        ).save(str(output_path))

        if (
            output_path.exists()
            and output_path.stat().st_size > 1000
        ):
            return output_path

    except Exception as exc:
        log.error(
            "gTTS fallback failed: %s",
            str(exc)[:250],
        )

    raise RuntimeError(
        "تعذّر إنشاء الراوي العربي. "
        "تحقق من اتصال Render ومكتبات الصوت."
    )


def normalize_audio(input_path, output_path, duration):
    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(input_path),
        "-vn",
        "-af",
        (
            "aresample=44100,"
            "loudnorm=I=-18:TP=-2:LRA=7,"
            "apad"
        ),
        "-t", str(duration),
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_path),
    ], timeout=120)

    return output_path


def create_narration(scene_texts, workdir):
    audio_segments = []

    for index, text in enumerate(scene_texts):
        raw_audio = workdir / f"voice_raw_{index:02d}.mp3"
        normalized = workdir / f"voice_{index:02d}.m4a"

        make_voice(text, raw_audio)

        normalize_audio(
            raw_audio,
            normalized,
            SECONDS_PER_SCENE,
        )

        audio_segments.append(normalized)

    concat_file = workdir / "voice_concat.txt"

    with open(concat_file, "w", encoding="utf-8") as file:
        for segment in audio_segments:
            safe_path = str(segment).replace("'", "'\\''")

            file.write(
                f"file '{safe_path}'\n"
            )

    output_path = workdir / "narration.m4a"

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-t", str(TOTAL_SECONDS),
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_path),
    ], timeout=120)

    return output_path


# =========================================================
# 10. LOCAL CINEMATIC AMBIENCE
# =========================================================

def create_ambient_audio(workdir):
    """
    Create a simple, quiet synthesized atmospheric bed.
    This is not a licensed commercial music track.
    """
    output_path = workdir / "ambient.m4a"

    filter_complex = (
        "[0:a]volume=0.025[a0];"
        "[1:a]volume=0.018[a1];"
        "[2:a]volume=0.012[a2];"
        "[a0][a1][a2]"
        "amix=inputs=3:duration=longest:normalize=0,"
        "lowpass=f=900,"
        "afade=t=in:st=0:d=2,"
        "afade=t=out:st=27:d=3,"
        "volume=0.7"
        "[mix]"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i",
        "sine=frequency=110:sample_rate=44100:duration=30",
        "-f", "lavfi",
        "-i",
        "sine=frequency=164.81:sample_rate=44100:duration=30",
        "-f", "lavfi",
        "-i",
        "sine=frequency=220:sample_rate=44100:duration=30",
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-t", str(TOTAL_SECONDS),
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "aac",
        "-b:a", "96k",
        str(output_path),
    ], timeout=90)

    return output_path


# =========================================================
# 11. FINAL VIDEO AND AUDIO MIX
# =========================================================

def concatenate_scenes(scene_paths, workdir):
    concat_file = workdir / "scenes.txt"

    with open(concat_file, "w", encoding="utf-8") as file:
        for path in scene_paths:
            safe_path = str(path).replace("'", "'\\''")

            file.write(
                f"file '{safe_path}'\n"
            )

    output_path = workdir / "video_only.mp4"

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-an",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-movflags", "+faststart",
        str(output_path),
    ], timeout=180)

    return output_path


def mux_final_video(video_path, narration_path, ambient_path, output_path):
    """
    Narration is the main audio. Ambient audio stays quieter.
    """
    filter_complex = (
        "[0:a]volume=1.0[voice];"
        "[1:a]volume=0.22[amb];"
        "[voice][amb]"
        "amix=inputs=2:duration=longest:normalize=0,"
        "alimiter=limit=0.95"
        "[audio]"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(narration_path),
        "-i", str(ambient_path),
        "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", "2:v:0",
        "-map", "[audio]",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(output_path),
    ], timeout=150)

    if not output_path.exists():
        raise RuntimeError(
            "Final video was not created."
        )

    if output_path.stat().st_size < 100_000:
        raise RuntimeError(
            "Final video is unexpectedly small."
        )

    if not probe_video(output_path):
        raise RuntimeError(
            "Final video failed validation."
        )

    return output_path


# =========================================================
# 12. VIDEO GENERATION PIPELINE
# =========================================================

def generate_video(story, workdir):
    """
    Generate six validated video scenes, Arabic narration,
    captions, and a quiet atmospheric soundtrack.
    """
    if not PEXELS_KEY:
        raise RuntimeError(
            "مفتاح PEXELS_KEY غير موجود في Render."
        )

    scene_texts = make_scene_texts(story)

    scene_paths = []

    for index, caption in enumerate(scene_texts):
        log.info(
            "Preparing scene %s/%s",
            index + 1,
            SCENES,
        )

        source = obtain_clip(
            caption,
            index,
            workdir,
        )

        caption_path = workdir / f"caption_{index:02d}.png"
        scene_path = workdir / f"scene_{index:02d}.mp4"

        create_caption_image(
            caption,
            caption_path,
        )

        build_scene(
            source,
            caption_path,
            scene_path,
        )

        scene_paths.append(scene_path)

    video_only = concatenate_scenes(
        scene_paths,
        workdir,
    )

    narration = create_narration(
        scene_texts,
        workdir,
    )

    ambient = create_ambient_audio(workdir)

    final_path = workdir / "zil_final.mp4"

    mux_final_video(
        video_only,
        narration,
        ambient,
        final_path,
    )

    return final_path


# =========================================================
# 13. TELEGRAM UTILITIES
# =========================================================

async def send_long_message(update, text):
    if update.message:
        await update.message.reply_text(text)


async def safe_send_video(update, video_path):
    if not update.message:
        return

    with open(video_path, "rb") as video:
        await update.message.reply_video(
            video=video,
            caption="تم إنشاء فيديو ظل.",
            supports_streaming=True,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
        )


async def generate_for_user(update, story, context):
    if not update.effective_user:
        return

    user_id = update.effective_user.id

    with busy_lock:
        if user_id in busy_users:
            await send_long_message(
                update,
                "طلبك السابق ما زال قيد التنفيذ. انتظر حتى ينتهي."
            )
            return

        busy_users.add(user_id)

    job_id = uuid.uuid4().hex[:10]
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        story = clean_story(story)

        if not story:
            await send_long_message(
                update,
                "اكتب قصتك بعد الأمر، مثال:\n"
                "/zil في مملكة بعيدة ظهر بطل غامض وأنقذ الأميرة."
            )
            return

        if len(story) < 10:
            await send_long_message(
                update,
                "القصة قصيرة جدًا. اكتب جملة أو أكثر حتى أتمكن من تقسيمها إلى مشاهد."
            )
            return

        await send_long_message(
            update,
            "بدأت صناعة فيديو ظل.\n"
            "سيتم تنزيل مقاطع حقيقية، وإضافة الراوي العربي والنص والموسيقى الخلفية.\n"
            "قد تستغرق العملية عدة دقائق."
        )

        loop = asyncio.get_running_loop()

        final_path = await loop.run_in_executor(
            None,
            generate_video,
            story,
            workdir,
        )

        await safe_send_video(
            update,
            final_path,
        )

    except Exception as exc:
        log.exception(
            "Video generation failed. Job=%s",
            job_id,
        )

        error_message = str(exc)

        # Keep user-facing errors short and never reveal credentials.
        if "PEXELS_KEY" in error_message:
            friendly = (
                "فشل إنشاء الفيديو: مفتاح Pexels غير موجود.\n"
                "تحقق من PEXELS_KEY في إعدادات Render."
            )
        elif "Pexels" in error_message or "تنزيل" in error_message:
            friendly = (
                "فشل الحصول على مقاطع فيديو صالحة.\n"
                "تحقق من صلاحية PEXELS_KEY، ثم جرّب مرة أخرى."
            )
        elif "راوي" in error_message or "TTS" in error_message:
            friendly = (
                "تعذّر إنشاء الصوت العربي.\n"
                "تحقق من اتصال Render وحاول مرة أخرى."
            )
        else:
            friendly = (
                "حدث خطأ أثناء صناعة الفيديو.\n"
                f"رقم العملية: {job_id}\n"
                "راجع Render Logs لمعرفة السبب."
            )

        try:
            await send_long_message(
                update,
                friendly,
            )
        except Exception:
            log.exception(
                "Could not send failure message."
            )

    finally:
        with busy_lock:
            busy_users.discard(user_id)

        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass


# =========================================================
# 14. TELEGRAM COMMANDS
# =========================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "أهلًا بك في ظل ZIL.\n\n"
        "أرسل قصة أو استخدم:\n"
        "/zil قصتك هنا\n\n"
        "الأوامر:\n"
        "/start - تشغيل البوت\n"
        "/status - حالة الخدمة\n"
        "/test - اختبار إنشاء فيديو\n"
        "/zil - إنشاء فيديو من قصتك\n\n"
        "ينشئ البوت فيديو عموديًا من مقاطع Pexels "
        "مع راوي عربي ونص ومؤثر صوتي خلفي بسيط."
    )

    await send_long_message(
        update,
        message,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pexels_status = (
        "موجود"
        if PEXELS_KEY
        else "غير موجود"
    )

    ffmpeg_status = (
        "جاهز"
        if check_binary("ffmpeg")
        else "غير موجود"
    )

    ffprobe_status = (
        "جاهز"
        if check_binary("ffprobe")
        else "غير موجود"
    )

    with busy_lock:
        active_jobs = len(busy_users)

    text = (
        "حالة ظل ZIL\n\n"
        f"البوت: {'مهيأ' if BOT_TOKEN else 'رمز البوت غير موجود'}\n"
        f"Pexels key: {pexels_status}\n"
        f"FFmpeg: {ffmpeg_status}\n"
        f"FFprobe: {ffprobe_status}\n"
        f"المشاهد: {SCENES}\n"
        f"المدة: {TOTAL_SECONDS} ثانية\n"
        f"الدقة: {WIDTH}x{HEIGHT}\n"
        f"الطلبات النشطة: {active_jobs}\n\n"
        "ملاحظة: وجود مفتاح Pexels لا يضمن أنه صالح؛ "
        "يتم التأكد منه عند طلب الفيديو."
    )

    await send_long_message(
        update,
        text,
    )


async def zil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = " ".join(context.args).strip()

    if not story:
        await send_long_message(
            update,
            "اكتب القصة بعد الأمر، مثال:\n"
            "/zil بطل غامض يدخل القصر وينقذ الأميرة من خطر مجهول."
        )
        return

    await generate_for_user(
        update,
        story,
        context,
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    test_story = (
        "في مملكة بعيدة، ظهر رجل غامض عند أبواب القصر. "
        "رفض الملك السماح له بالاقتراب من الأميرة. "
        "في الليل، ظهر وحش ضخم قرب أسوار المملكة. "
        "تراجع الحراس أمام قوته الهائلة. "
        "وقف الرجل الغامض في طريق الوحش وكشف عن قوة غير متوقعة. "
        "لكن الملك لاحظ علامة قديمة على ذراعه وعرف سرًا خطيرًا."
    )

    await generate_for_user(
        update,
        test_story,
        context,
    )


async def text_story_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if text.startswith("/"):
        return

    await generate_for_user(
        update,
        text,
        context,
    )


# =========================================================
# 15. FLASK HEALTH ENDPOINTS
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "ZIL",
        "status": "online",
        "video_seconds": TOTAL_SECONDS,
        "scenes": SCENES,
    })


@app.route("/health", methods=["GET"])
def health():
    healthy = (
        bool(BOT_TOKEN)
        and check_binary("ffmpeg")
        and check_binary("ffprobe")
    )

    return jsonify({
        "service": "ZIL",
        "status": "healthy" if healthy else "degraded",
        "ffmpeg": check_binary("ffmpeg"),
        "ffprobe": check_binary("ffprobe"),
        "pexels_key_configured": bool(PEXELS_KEY),
        "video_seconds": TOTAL_SECONDS,
    }), (200 if healthy else 503)


# =========================================================
# 16. STARTUP
# =========================================================

def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )


def main():
    validate_environment()

    if not PEXELS_KEY:
        log.warning(
            "PEXELS_KEY is not configured; video generation will fail."
        )

    log.info("Starting ZIL Telegram bot.")

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )

    telegram_app.add_handler(
        CommandHandler("status", status_command)
    )

    telegram_app.add_handler(
        CommandHandler("test", test_command)
    )

    telegram_app.add_handler(
        CommandHandler("zil", zil_command)
    )

    # Telegram command names cannot contain Arabic letters.
    # This handler allows the Arabic alias /ظل.
    telegram_app.add_handler(
        MessageHandler(
            filters.Regex(r"^/ظل(?:@\w+)?(?:\s|$)"),
            text_story_handler,
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_story_handler,
        )
    )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
        name="zil-flask",
    )

    flask_thread.start()

    # Run polling on the main thread.
    # Do not log the Telegram token or the API URL.
    telegram_app.run_polling(
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    main()
