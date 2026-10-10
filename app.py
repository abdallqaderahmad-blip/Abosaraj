import os
import re
import json
import uuid
import shutil
import logging
import asyncio
import threading
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
import edge_tts


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY")
    or os.getenv("PIXABAY_KEY")
    or ""
).strip()

PORT = int(os.getenv("PORT", "10000"))

SCENES = 6
SCENE_SECONDS = 5
TOTAL_SECONDS = 30

TRANSITION_SECONDS = 0.35
CLIP_SECONDS = (
    TOTAL_SECONDS + (SCENES - 1) * TRANSITION_SECONDS
) / SCENES

WIDTH = 720
HEIGHT = 1280
FPS = 25

VOICE = os.getenv(
    "TTS_VOICE", "ar-SA-HamedNeural"
).strip()

VOICE_RATE = os.getenv("TTS_RATE", "-8%").strip()

MAX_STORY_LENGTH = 5000
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

API_TIMEOUT = 25
DOWNLOAD_TIMEOUT = 45

BASE_DIR = Path("/tmp/zil")
BASE_DIR.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

log = logging.getLogger("zil")
flask_app = Flask(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ZIL-VideoBot/2.0"})

# Persistent only while this process is running.
jobs = {}
jobs_lock = threading.Lock()

busy_users = set()
busy_lock = threading.Lock()

executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="zil-video"
)


ARABIC_TO_ENGLISH = {
    "قصر": "royal castle",
    "ملك": "king royal palace",
    "امير": "medieval prince",
    "أمير": "medieval prince",
    "اميرة": "princess castle",
    "أميرة": "princess castle",
    "حب": "romantic couple",
    "غابة": "dark forest",
    "ليل": "night cinematic",
    "قتال": "martial arts fight",
    "معركة": "battle cinematic",
    "نمر": "tiger wildlife",
    "أسد": "lion wildlife",
    "اسد": "lion wildlife",
    "ذئب": "wolf wildlife",
    "وحش": "monster fantasy",
    "سيف": "sword medieval",
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
    "سحر": "fantasy magic",
    "طيران": "aerial cinematic",
    "سقوط": "dramatic fall",
}


# =========================================================
# LOGGING AND JOB STATUS
# =========================================================

def update_job(job_id, status=None, stage=None, error=None):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return

        if status:
            job["status"] = status

        if stage:
            job["stage"] = stage

        if error:
            job["error"] = str(error)[:500]

        job["updated_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.gmtime()
        )


def check_binary(name):
    return shutil.which(name) is not None


def run_command(command, timeout=180):
    log.info("RUN COMMAND: %s", " ".join(map(str, command[:5])))

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False
        )
    except subprocess.TimeoutExpired as exc:
        log.error("COMMAND TIMEOUT after %s seconds", timeout)
        raise RuntimeError(
            f"FFmpeg command timed out after {timeout} seconds"
        ) from exc

    if result.returncode != 0:
        details = (result.stderr or "")[-2500:]
        log.error(
            "COMMAND FAILED returncode=%s details=%s",
            result.returncode,
            details
        )
        raise RuntimeError(
            f"Command failed ({result.returncode}): {details}"
        )

    return result


def validate_environment():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    for binary in ("ffmpeg", "ffprobe"):
        if not check_binary(binary):
            missing.append(binary)

    if missing:
        raise RuntimeError(
            "Missing configuration/tools: " + ", ".join(missing)
        )


def clean_story(text):
    text = (text or "").strip()

    text = re.sub(
        r"^/(?:zil|test|ظل)(?:@\w+)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s+", " ", text).strip()

    return text[:MAX_STORY_LENGTH]


def split_sentences(text):
    text = clean_story(text)

    if not text:
        return []

    parts = re.split(r"(?<=[.!؟?。])\s+", text)

    return [
        part.strip(" \t،,;؛")
        for part in parts
        if part.strip(" \t،,;؛")
    ]


def make_scene_texts(story):
    sentences = split_sentences(story)

    if not sentences:
        raise ValueError("القصة فارغة.")

    if len(sentences) >= SCENES:
        result = []

        for i in range(SCENES):
            start = i * len(sentences) // SCENES
            end = (i + 1) * len(sentences) // SCENES

            result.append(
                " ".join(sentences[start:end]).strip()
            )

        return result

    words = story.split()
    result = []

    for i in range(SCENES):
        start = round(i * len(words) / SCENES)
        end = round((i + 1) * len(words) / SCENES)

        result.append(" ".join(words[start:end]).strip())

    return result


def search_terms(text, index):
    for word, query in ARABIC_TO_ENGLISH.items():
        if word in text:
            return query

    defaults = [
        "cinematic dramatic landscape",
        "mysterious man cinematic",
        "dark forest cinematic",
        "royal castle cinematic",
        "dramatic storm landscape",
        "cinematic night city",
    ]

    return defaults[index % len(defaults)]


# =========================================================
# PIXABAY
# =========================================================

def pixabay_search(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY غير موجود.")

    log.info("PIXABAY SEARCH: %s", query)

    response = SESSION.get(
        "https://pixabay.com/api/videos/",
        params={
            "key": PIXABAY_API_KEY,
            "q": query,
            "video_type": "film",
            "safesearch": "true",
            "per_page": 15,
            "page": 1,
        },
        timeout=API_TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pixabay search failed: HTTP {response.status_code}"
        )

    payload = response.json()
    hits = payload.get("hits", [])

    log.info("PIXABAY RESULTS: %s", len(hits))

    candidates = []

    for video in hits:
        duration = int(video.get("duration") or 0)

        if duration and duration < 3:
            continue

        video_files = video.get("videos") or {}

        for quality in ("large", "medium", "small", "tiny"):
            item = video_files.get(quality) or {}
            link = item.get("url")

            if not link:
                continue

            parsed = urlparse(link)

            if parsed.scheme != "https" or not parsed.hostname:
                continue

            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)

            score = (
                (100000 if height > width else 0)
                + min(width, 1080)
                + min(height, 1920)
            )

            candidates.append((score, link))
            break

    candidates.sort(key=lambda item: item[0], reverse=True)

    result = []
    seen = set()

    for _, link in candidates:
        if link not in seen:
            seen.add(link)
            result.append(link)

    return result


def probe_video(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type,width,height",
        "-of", "json",
        str(path)
    ], timeout=30)

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except (ValueError, TypeError):
        return False

    if not streams:
        return False

    stream = streams[0]

    return (
        stream.get("codec_type") == "video"
        and int(stream.get("width") or 0) >= 100
        and int(stream.get("height") or 0) >= 100
    )


def download_clip(url, output_path):
    output_path = Path(output_path)
    temp_path = output_path.with_suffix(".part")

    try:
        with SESSION.get(
            url,
            stream=True,
            timeout=(15, DOWNLOAD_TIMEOUT),
            allow_redirects=True
        ) as response:

            response.raise_for_status()

            length = response.headers.get("Content-Length")

            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError(
                    "Video file exceeds 80MB download limit."
                )

            total = 0

            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(
                    chunk_size=256 * 1024
                ):
                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(
                            "Video exceeded 80MB download limit."
                        )

                    handle.write(chunk)

        if temp_path.stat().st_size < 30000:
            raise RuntimeError("Downloaded video is too small.")

        if not probe_video(temp_path):
            raise RuntimeError("Downloaded file is not a valid video.")

        temp_path.replace(output_path)

        log.info(
            "VIDEO DOWNLOADED: %s bytes",
            output_path.stat().st_size
        )

        return output_path

    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def obtain_clip(scene_text, index, workdir):
    queries = list(dict.fromkeys([
        search_terms(scene_text, index),
        "cinematic dramatic scene",
        "cinematic landscape"
    ]))

    last_error = None
    output = workdir / f"source_{index:02d}.mp4"
    candidate_count = 0

    for query in queries:
        try:
            urls = pixabay_search(query) or []
        except Exception as exc:
            last_error = exc

            log.exception(
                "PIXABAY SEARCH FAILED scene=%s query=%s",
                index + 1,
                query
            )

            continue

        for url in urls[:4]:
            candidate_count += 1

            try:
                output.unlink(missing_ok=True)

                download_clip(url, output)

                log.info(
                    "SCENE %s SOURCE READY candidate=%s",
                    index + 1,
                    candidate_count
                )

                return output

            except Exception as exc:
                last_error = exc

                log.warning(
                    "CANDIDATE FAILED scene=%s candidate=%s error=%s",
                    index + 1,
                    candidate_count,
                    str(exc)[:300]
                )

    raise RuntimeError(
        f"تعذر تنزيل فيديو صالح للمشهد {index + 1}. "
        f"آخر خطأ: {str(last_error)[:300] if last_error else 'لا توجد نتائج'}"
    )


# =========================================================
# ARABIC CAPTIONS
# =========================================================

def find_font():
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path

    raise RuntimeError(
        "لم يتم العثور على الخط العربي. "
        "تأكد من تثبيت fonts-dejavu-core في Dockerfile."
    )


def shape_arabic(text):
    return get_display(arabic_reshaper.reshape(text or ""))


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        candidate = (current + " " + word).strip()

        box = draw.textbbox(
            (0, 0),
            shape_arabic(candidate),
            font=font
        )

        if box[2] - box[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def create_caption_image(text, output_path):
    image = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    if not (text or "").strip():
        image.save(output_path)
        return output_path

    draw = ImageDraw.Draw(image)
    font_path = find_font()

    font_size = 40
    lines = []

    while font_size >= 24:
        font = ImageFont.truetype(font_path, font_size)

        lines = wrap_text(
            draw,
            text,
            font,
            WIDTH - 100
        )

        if len(lines) <= 3:
            break

        font_size -= 2

    if not lines:
        lines = [text]

    line_heights = []

    for line in lines:
        box = draw.textbbox(
            (0, 0),
            shape_arabic(line),
            font=font,
            stroke_width=1
        )

        line_heights.append(max(1, box[3] - box[1]))

    spacing = 12
    padding_y = 20

    panel_height = (
        sum(line_heights)
        + spacing * (len(lines) - 1)
        + padding_y * 2
    )

    panel = Image.new(
        "RGBA",
        (WIDTH - 40, panel_height),
        (0, 0, 0, 0)
    )

    panel_draw = ImageDraw.Draw(panel)

    panel_draw.rounded_rectangle(
        (0, 0, panel.width - 1, panel.height - 1),
        radius=22,
        fill=(0, 0, 0, 175),
        outline=(255, 255, 255, 50),
        width=2
    )

    y = padding_y

    for i, line in enumerate(lines):
        shaped = shape_arabic(line)

        box = panel_draw.textbbox(
            (0, 0),
            shaped,
            font=font,
            stroke_width=1
        )

        x = (
            panel.width - (box[2] - box[0])
        ) // 2

        panel_draw.text(
            (x, y),
            shaped,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 220)
        )

        y += line_heights[i] + spacing

    top = max(55, HEIGHT - panel_height - 115)

    image.alpha_composite(panel, (20, top))
    image.save(output_path)

    return output_path


# =========================================================
# VIDEO RENDERING
# =========================================================

def build_scene(source_path, caption_path, output_path):
    filter_complex = (
        f"[0:v]"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"eq=contrast=1.035:saturation=1.06:brightness=0.005,"
        f"fps={FPS},setsar=1,"
        f"trim=duration={CLIP_SECONDS:.6f},"
        f"setpts=PTS-STARTPTS[base];"
        f"[1:v]format=rgba[cap];"
        f"[base][cap]overlay=0:0:shortest=1,"
        f"format=yuv420p[out]"
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
        "-t", f"{CLIP_SECONDS:.6f}",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ], timeout=180)

    if (
        not output_path.exists()
        or output_path.stat().st_size < 30000
        or not probe_video(output_path)
    ):
        raise RuntimeError("تعذر تجهيز أحد مشاهد الفيديو.")

    log.info("SCENE CLIP READY: %s", output_path.name)

    return output_path


def media_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], timeout=30)

    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError):
        raise RuntimeError("تعذر تحديد مدة الملف.")


def concatenate_scenes(scene_paths, workdir):
    if len(scene_paths) != SCENES:
        raise RuntimeError("عدد المشاهد غير صحيح.")

    inputs = []

    for path in scene_paths:
        inputs.extend(["-i", str(path)])

    pieces = []

    for i in range(SCENES):
        pieces.append(
            f"[{i}:v]fps={FPS},format=yuv420p[v{i}]"
        )

    current = "v0"
    offset_step = CLIP_SECONDS - TRANSITION_SECONDS

    for i in range(1, SCENES):
        output_label = f"xf{i}"
        offset = offset_step * i

        pieces.append(
            f"[{current}][v{i}]"
            f"xfade=transition=fade:"
            f"duration={TRANSITION_SECONDS:.3f}:"
            f"offset={offset:.6f}"
            f"[{output_label}]"
        )

        current = output_label

    output = workdir / "video_only.mp4"

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *inputs,
        "-filter_complex", ";".join(pieces),
        "-map", f"[{current}]",
        "-an",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-movflags", "+faststart",
        str(output)
    ], timeout=240)

    if not output.exists() or not probe_video(output):
        raise RuntimeError("فشل دمج المشاهد.")

    duration = media_duration(output)

    log.info("MERGED VIDEO DURATION: %.2f", duration)

    if duration < TOTAL_SECONDS - 0.3:
        raise RuntimeError(
            f"الفيديو أقصر من المطلوب: {duration:.2f} ثانية."
        )

    return output


# =========================================================
# ARABIC NARRATION
# =========================================================

async def edge_tts_to_file(text, output_path):
    await edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE
    ).save(str(output_path))


def make_voice(text, output_path):
    try:
        asyncio.run(edge_tts_to_file(text, output_path))

        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path

    except Exception:
        log.exception("EDGE TTS FAILED")

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False
        ).save(str(output_path))

        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path

    except Exception:
        log.exception("GTTS FALLBACK FAILED")

    raise RuntimeError("تعذر إنشاء الراوي العربي.")


def create_narration(story, workdir):
    raw = workdir / "narration_raw.mp3"
    output = workdir / "narration.m4a"

    make_voice(story, raw)

    duration = media_duration(raw)
    tempo = max(1.0, duration / TOTAL_SECONDS)

    if tempo > 1.35:
        raise RuntimeError(
            "القصة طويلة جدًا لفيديو 30 ثانية. "
            "اختصر النص إلى نحو 60 كلمة."
        )

    audio_filter = (
        f"atempo={tempo:.4f},"
        "aresample=44100,"
        "apad,"
        f"atrim=0:{TOTAL_SECONDS},"
        "loudnorm=I=-18:TP=-2:LRA=7"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(raw),
        "-vn",
        "-af", audio_filter,
        "-t", str(TOTAL_SECONDS),
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output)
    ], timeout=120)

    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("فشل تجهيز ملف الراوي.")

    return output


def create_ambient_audio(workdir):
    output = workdir / "ambient.m4a"

    audio_filter = (
        "[0:a]volume=0.030[a0];"
        "[1:a]volume=0.012[a1];"
        "[2:a]volume=0.008[a2];"
        "[a0][a1][a2]"
        "amix=inputs=3:duration=longest:normalize=0,"
        "lowpass=f=700,highpass=f=45,"
        "afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={TOTAL_SECONDS - 2}:d=2,"
        "volume=0.8[m]"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=55:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-f", "lavfi",
        "-i", f"sine=frequency=82.41:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-f", "lavfi",
        "-i", f"sine=frequency=110:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-filter_complex", audio_filter,
        "-map", "[m]",
        "-t", str(TOTAL_SECONDS),
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "aac",
        "-b:a", "96k",
        str(output)
    ], timeout=90)

    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("تعذر إنشاء الخلفية الصوتية.")

    return output


def mux_final_video(video_path, narration_path, ambient_path, output_path):
    audio_filter = (
        "[0:a]asplit=2[voice][side];"
        "[1:a]volume=1.0[bed];"
        "[bed][side]"
        "sidechaincompress=threshold=0.025:ratio=7:"
        "attack=25:release=450[duck];"
        "[voice][duck]"
        "amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.92,aresample=44100[a]"
    )

    run_command([
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(narration_path),
        "-i", str(ambient_path),
        "-i", str(video_path),
        "-filter_complex", audio_filter,
        "-map", "2:v:0",
        "-map", "[a]",
        "-t", str(TOTAL_SECONDS),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(output_path)
    ], timeout=180)

    if (
        not output_path.exists()
        or output_path.stat().st_size < 100000
        or not probe_video(output_path)
    ):
        raise RuntimeError("فشل التحقق من الفيديو النهائي.")

    duration = media_duration(output_path)

    if not (
        TOTAL_SECONDS - 0.3
        <= duration
        <= TOTAL_SECONDS + 0.5
    ):
        raise RuntimeError(
            f"مدة الفيديو النهائي غير صحيحة: {duration:.2f}"
        )

    return output_path


# =========================================================
# COMPLETE VIDEO PIPELINE
# =========================================================

def generate_video(story, workdir, job_id):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY غير موجود.")

    captions = make_scene_texts(story)
    scene_paths = []

    for i, caption in enumerate(captions):
        update_job(
            job_id,
            status="running",
            stage=f"scene_{i + 1}_of_{SCENES}"
        )

        log.info(
            "JOB %s: RENDERING SCENE %s/%s",
            job_id, i + 1, SCENES
        )

        source = obtain_clip(
            caption or story,
            i,
            workdir
        )

        caption_path = workdir / f"caption_{i:02d}.png"
        scene_path = workdir / f"scene_{i:02d}.mp4"

        create_caption_image(caption or "", caption_path)
        build_scene(source, caption_path, scene_path)

        scene_paths.append(scene_path)

    update_job(job_id, stage="merging_scenes")
    log.info("JOB %s: MERGING SCENES", job_id)

    video = concatenate_scenes(scene_paths, workdir)

    update_job(job_id, stage="creating_arabic_narration")
    log.info("JOB %s: CREATING NARRATION", job_id)

    voice = create_narration(story, workdir)

    update_job(job_id, stage="creating_background_audio")

    ambient = create_ambient_audio(workdir)

    update_job(job_id, stage="final_audio_mux")

    final_path = workdir / "zil_final.mp4"

    mux_final_video(
        video,
        voice,
        ambient,
        final_path
    )

    update_job(job_id, stage="video_validated")

    log.info("JOB %s: FINAL VIDEO READY", job_id)

    return final_path


# =========================================================
# TELEGRAM
# =========================================================

async def reply(update, text):
    message = update.effective_message

    if message:
        await message.reply_text(text)


async def send_video(update, path):
    message = update.effective_message

    if not message:
        raise RuntimeError("رسالة تيليجرام غير موجودة لإرسال الفيديو.")

    log.info("SENDING VIDEO: %s", path)

    with open(path, "rb") as video_file:
        await message.reply_video(
            video=video_file,
            caption="تم إنشاء فيديو ظل بنجاح.",
            supports_streaming=True,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30
        )


async def generate_for_user(update, story):
    user = update.effective_user

    if not user:
        log.error("GENERATION REQUEST HAS NO EFFECTIVE USER")
        return

    uid = user.id
    story = clean_story(story)

    if len(story) < 10:
        await reply(
            update,
            "اكتب قصة أطول، مثال:\n"
            "/zil بطل غامض يصل إلى القصر وينقذ الأميرة."
        )
        return

    with busy_lock:
        if uid in busy_users:
            already_busy = True
        else:
            busy_users.add(uid)
            already_busy = False

    if already_busy:
        await reply(
            update,
            "طلبك السابق ما زال قيد التنفيذ. "
            "استخدم /status لمعرفة حالته."
        )
        return

    job_id = uuid.uuid4().hex[:10]
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "user_id": uid,
            "status": "queued",
            "stage": "request_received",
            "error": None,
            "created_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.gmtime()
            ),
            "updated_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.gmtime()
            )
        }

    log.info(
        "GENERATION REQUEST RECEIVED job=%s user=%s chars=%s",
        job_id, uid, len(story)
    )

    try:
        await reply(
            update,
            f"بدأت صناعة فيديو ظل.\n"
            f"رقم العملية: {job_id}\n\n"
            f"6 مشاهد، انتقالات ناعمة، فيديو عمودي، "
            f"راوي عربي وخلفية صوتية.\n\n"
            f"استخدم /status لمتابعة العملية."
        )

        update_job(
            job_id,
            status="running",
            stage="starting_video_generation"
        )

        loop = asyncio.get_running_loop()

        final_path = await loop.run_in_executor(
            executor,
            generate_video,
            story,
            workdir,
            job_id
        )

        update_job(
            job_id,
            status="sending_video",
            stage="uploading_to_telegram"
        )

        await send_video(update, final_path)

        update_job(
            job_id,
            status="completed",
            stage="video_sent"
        )

        log.info("JOB %s COMPLETED", job_id)

    except Exception as exc:
        log.exception(
            "GENERATION FAILED job=%s",
            job_id
        )

        update_job(
            job_id,
            status="failed",
            stage="error",
            error=f"{type(exc).__name__}: {exc}"
        )

        message = str(exc)

        if "PIXABAY_API_KEY" in message:
            friendly = (
                "مفتاح Pixabay غير موجود. "
                "تحقق من PIXABAY_API_KEY في Render."
            )
        elif "Pixabay" in message or "تنزيل فيديو" in message:
            friendly = (
                f"فشل تنزيل مقطع من Pixabay.\n"
                f"رقم العملية: {job_id}\n"
                f"تحقق من Render Logs."
            )
        elif "القصة طويلة" in message:
            friendly = message
        elif "الراوي" in message:
            friendly = (
                f"فشل إنشاء الراوي العربي.\n"
                f"رقم العملية: {job_id}"
            )
        else:
            friendly = (
                f"فشل إنشاء الفيديو.\n"
                f"رقم العملية: {job_id}\n"
                f"الخطأ: {message[:250]}"
            )

        try:
            await reply(update, friendly)
        except Exception:
            log.exception(
                "FAILED TO SEND GENERATION ERROR TO TELEGRAM job=%s",
                job_id
            )

    finally:
        with busy_lock:
            busy_users.discard(uid)

        # Keep job information for /status and diagnostics.
        # Remove temporary video files after Telegram upload finishes.
        shutil.rmtree(workdir, ignore_errors=True)


async def start_command(update, context):
    await reply(
        update,
        "أهلًا بك في ظل ZIL.\n\n"
        "أرسل قصتك مباشرة أو استخدم:\n"
        "/zil قصتك هنا\n\n"
        "/start - تشغيل البوت\n"
        "/status - حالة الخدمة والعمليات\n"
        "/test - فيديو تجريبي\n"
        "/zil - إنشاء فيديو\n"
        "/ظل - إنشاء فيديو بالعربية"
    )


async def status_command(update, context):
    with busy_lock:
        active_users = len(busy_users)

    with jobs_lock:
        recent_jobs = list(jobs.values())[-5:]

    lines = [
        "حالة ظل ZIL",
        "",
        f"البوت: {'مهيأ' if BOT_TOKEN else 'رمز البوت غير موجود'}",
        f"Pixabay key: {'موجود' if PIXABAY_API_KEY else 'غير موجود'}",
        f"FFmpeg: {'جاهز' if check_binary('ffmpeg') else 'غير موجود'}",
        f"FFprobe: {'جاهز' if check_binary('ffprobe') else 'غير موجود'}",
        f"المشاهد: {SCENES}",
        f"المدة: {TOTAL_SECONDS} ثانية",
        f"الدقة: {WIDTH}x{HEIGHT}",
        f"المستخدمون النشطون: {active_users}",
        "",
        "آخر العمليات:"
    ]

    if not recent_jobs:
        lines.append("لا توجد عمليات مسجلة.")
    else:
        for job in reversed(recent_jobs):
            lines.extend([
                "",
                f"رقم: {job['job_id']}",
                f"الحالة: {job['status']}",
                f"المرحلة: {job['stage']}",
                f"آخر تحديث: {job['updated_at']}"
            ])

            if job.get("error"):
                lines.append(
                    f"الخطأ: {job['error'][:180]}"
                )

    await reply(update, "\n".join(lines))


async def zil_command(update, context):
    message = update.effective_message

    story = " ".join(context.args).strip()

    if not story and message and message.text:
        story = re.sub(
            r"^/zil(?:@\w+)?\s*",
            "",
            message.text,
            flags=re.IGNORECASE
        ).strip()

    if not story:
        await reply(
            update,
            "اكتب القصة بعد الأمر:\n"
            "/zil بطل غامض يدخل القصر."
        )
        return

    await generate_for_user(update, story)


async def arabic_zil_handler(update, context):
    message = update.effective_message
    text = message.text if message else ""

    story = re.sub(
        r"^/ظل(?:@\w+)?\s*",
        "",
        text or ""
    ).strip()

    if not story:
        await reply(
            update,
            "اكتب القصة بعد الأمر:\n"
            "/ظل بطل غامض يدخل القصر."
        )
        return

    await generate_for_user(update, story)


async def test_command(update, context):
    log.info("TEST COMMAND RECEIVED")

    story = (
        "في مملكة بعيدة ظهر رجل غامض عند أبواب القصر. "
        "رفض الملك السماح له بالاقتراب من الأميرة. "
        "في الليل ظهر وحش ضخم قرب أسوار المملكة. "
        "تراجع الحراس أمام قوته الهائلة. "
        "وقف الرجل الغامض في طريق الوحش وكشف عن قوة غير متوقعة. "
        "لكن الملك لاحظ علامة قديمة على ذراعه وعرف سرًا خطيرًا."
    )

    await generate_for_user(update, story)


async def text_story_handler(update, context):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip()

    if text.startswith("/"):
        return

    await generate_for_user(update, text)


# =========================================================
# TELEGRAM ERROR HANDLER
# =========================================================

async def telegram_error_handler(update, context):
    error = context.error

    log.error(
        "TELEGRAM HANDLER ERROR",
        exc_info=(
            type(error),
            error,
            error.__traceback__
        ) if error else None
    )

    if update:
        log.error(
            "FAILED UPDATE: %s",
            str(update)[:1500]
        )


# =========================================================
# FLASK HEALTH
# =========================================================

@flask_app.get("/")
def home():
    return jsonify({
        "service": "ZIL",
        "status": "online",
        "scenes": SCENES,
        "video_seconds": TOTAL_SECONDS
    })


@flask_app.get("/health")
def health():
    ffmpeg_ok = check_binary("ffmpeg")
    ffprobe_ok = check_binary("ffprobe")

    ok = bool(BOT_TOKEN) and ffmpeg_ok and ffprobe_ok

    return jsonify({
        "service": "ZIL",
        "status": "healthy" if ok else "degraded",
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "pixabay_key_configured": bool(PIXABAY_API_KEY),
        "scenes": SCENES,
        "video_seconds": TOTAL_SECONDS
    }), (200 if ok else 503)


@flask_app.get("/jobs")
def jobs_endpoint():
    with jobs_lock:
        recent_jobs = list(jobs.values())[-20:]

    return jsonify({
        "count": len(recent_jobs),
        "jobs": recent_jobs
    })


def run_flask():
    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False
    )


# =========================================================
# STARTUP
# =========================================================

def main():
    validate_environment()

    log.info("STARTING ZIL BOT")
    log.info("Pixabay key configured: %s", bool(PIXABAY_API_KEY))
    log.info("FFmpeg ready: %s", check_binary("ffmpeg"))
    log.info("FFprobe ready: %s", check_binary("ffprobe"))

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

    telegram_app.add_handler(
        MessageHandler(
            filters.Regex(r"^/ظل(?:@\w+)?(?:\s|$)"),
            arabic_zil_handler
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_story_handler
        )
    )

    telegram_app.add_error_handler(
        telegram_error_handler
    )

    threading.Thread(
        target=run_flask,
        daemon=True,
        name="zil-flask"
    ).start()

    log.info("TELEGRAM POLLING STARTING")

    telegram_app.run_polling(
        drop_pending_updates=False
    )


if __name__ == "__main__":
    main()
