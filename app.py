import os
import re
import json
import uuid
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

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PIXABAY_API_KEY = (os.getenv("PIXABAY_API_KEY") or "").strip()
PORT = int(os.getenv("PORT", "10000"))

SCENES = 6
SCENE_SECONDS = 5
TOTAL_SECONDS = SCENES * SCENE_SECONDS
TRANSITION_SECONDS = 0.35
CLIP_SECONDS = SCENE_SECONDS + (
    (SCENES - 1) * TRANSITION_SECONDS / SCENES
)

WIDTH = 720
HEIGHT = 1280
FPS = 25

VOICE = (os.getenv("TTS_VOICE") or "ar-SA-HamedNeural").strip()
VOICE_RATE = (os.getenv("TTS_RATE") or "-8%").strip()

MAX_STORY_LENGTH = 5000
MAX_DOWNLOAD_BYTES = 35 * 1024 * 1024
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
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("zil")
flask_app = Flask(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ZIL-VideoBot/1.3"})

busy_users = set()
busy_lock = threading.Lock()

jobs = {}
jobs_lock = threading.Lock()

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


# ============================================================
# JOB STATUS AND LOGGING
# ============================================================

def update_job(job_id, status, detail=None):
    with jobs_lock:
        entry = jobs.setdefault(job_id, {})
        entry["status"] = status

        if detail is not None:
            entry["detail"] = str(detail)[:500]

    log.info(
        "JOB %s | %s | %s",
        job_id,
        status,
        str(detail or ""),
    )


# ============================================================
# HELPERS
# ============================================================

def check_binary(name):
    return shutil.which(name) is not None


def run_command(command, timeout=120):
    log.info("RUN COMMAND: %s", " ".join(map(str, command[:5])))

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command timed out after {timeout} seconds."
        ) from exc

    if result.returncode != 0:
        details = (result.stderr or "")[-1800:]
        log.error("COMMAND FAILED: %s", details)

        raise RuntimeError(
            f"Command failed ({result.returncode}): {details}"
        )

    return result


def validate_environment():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not PIXABAY_API_KEY:
        missing.append("PIXABAY_API_KEY")

    for binary in ("ffmpeg", "ffprobe"):
        if not check_binary(binary):
            missing.append(binary)

    if missing:
        raise RuntimeError(
            "Missing required configuration/tools: "
            + ", ".join(missing)
        )

    log.info("STARTUP CHECK PASSED")


def clean_story(text):
    text = (text or "").strip()

    text = re.sub(
        r"^/(?:zil|test|ظل)(?:@\w+)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
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
        raise ValueError("اكتب القصة بعد الأمر.")

    if len(sentences) >= SCENES:
        result = []

        for i in range(SCENES):
            start = (i * len(sentences)) // SCENES
            end = ((i + 1) * len(sentences)) // SCENES

            result.append(
                " ".join(sentences[start:end]).strip()
            )

        return result

    words = story.split()
    result = []

    for i in range(SCENES):
        start = round(i * len(words) / SCENES)
        end = round((i + 1) * len(words) / SCENES)

        result.append(
            " ".join(words[start:end]).strip()
        )

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


# ============================================================
# PIXABAY
# ============================================================

def pixabay_search(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY غير موجود في Render.")

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
        timeout=API_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pixabay search failed: HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Pixabay returned invalid JSON.") from exc

    candidates = []

    for video in payload.get("hits", []):
        videos = video.get("videos") or {}
        duration = int(video.get("duration") or 0)

        if duration and duration < 3:
            continue

        for quality in ("large", "medium", "small", "tiny"):
            item = videos.get(quality) or {}
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

    log.info("PIXABAY RESULTS: %s", len(result))

    return result


def probe_video(path):
    result = run_command(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,width,height",
            "-of", "json",
            str(path),
        ],
        timeout=25,
    )

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
    temp_path = output_path.with_name(
        output_path.stem + ".part.mp4"
    )

    try:
        with SESSION.get(
            url,
            stream=True,
            timeout=(15, DOWNLOAD_TIMEOUT),
            allow_redirects=True,
        ) as response:
            response.raise_for_status()

            length = response.headers.get("Content-Length")

            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("Video exceeds size limit.")

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
                            "Video exceeded download size limit."
                        )

                    handle.write(chunk)

        if (
            temp_path.stat().st_size < 30000
            or not probe_video(temp_path)
        ):
            raise RuntimeError("Downloaded file is not a valid video.")

        temp_path.replace(output_path)

        log.info("VIDEO DOWNLOADED: %s", output_path.name)

        return output_path

    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def obtain_clip(scene_text, index, workdir):
    queries = list(dict.fromkeys([
        search_terms(scene_text, index),
        "cinematic dramatic scene",
        "cinematic landscape",
    ]))

    last_error = None
    output = workdir / f"source_{index:02d}.mp4"

    for query in queries:
        try:
            urls = pixabay_search(query)
        except Exception as exc:
            last_error = exc

            log.warning(
                "SCENE %s SEARCH ERROR: %s",
                index + 1,
                str(exc)[:200],
            )

            continue

        for candidate_number, url in enumerate(
            urls[:4],
            start=1,
        ):
            try:
                output.unlink(missing_ok=True)
                download_clip(url, output)

                log.info(
                    "SCENE %s CLIP READY",
                    index + 1,
                )

                return output

            except Exception as exc:
                last_error = exc

                log.warning(
                    "SCENE %s CANDIDATE %s FAILED: %s",
                    index + 1,
                    candidate_number,
                    str(exc)[:200],
                )

    raise RuntimeError(
        f"تعذر تنزيل مقطع صالح للمشهد {index + 1} من Pixabay. "
        f"آخر خطأ: {str(last_error)[:250]}"
    )


# ============================================================
# ARABIC CAPTIONS
# ============================================================

def find_font():
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path

    raise RuntimeError(
        "خط العربية غير موجود. ثبّت fonts-dejavu-core في Dockerfile."
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
            font=font,
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
        (0, 0, 0, 0),
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
            WIDTH - 100,
        )

        if len(lines) <= 3:
            break

        font_size -= 2

    font = ImageFont.truetype(
        font_path,
        max(font_size, 24),
    )

    if not lines:
        lines = [text]

    line_heights = []

    for line in lines:
        box = draw.textbbox(
            (0, 0),
            shape_arabic(line),
            font=font,
            stroke_width=1,
        )

        line_heights.append(max(1, box[3] - box[1]))

    spacing = 12
    pad_y = 20

    panel_h = (
        sum(line_heights)
        + spacing * (len(lines) - 1)
        + pad_y * 2
    )

    panel = Image.new(
        "RGBA",
        (WIDTH - 40, panel_h),
        (0, 0, 0, 0),
    )

    panel_draw = ImageDraw.Draw(panel)

    panel_draw.rounded_rectangle(
        (0, 0, panel.width - 1, panel.height - 1),
        radius=22,
        fill=(0, 0, 0, 175),
        outline=(255, 255, 255, 50),
        width=2,
    )

    y = pad_y

    for i, line in enumerate(lines):
        shaped = shape_arabic(line)

        box = panel_draw.textbbox(
            (0, 0),
            shaped,
            font=font,
            stroke_width=1,
        )

        x = (panel.width - (box[2] - box[0])) // 2

        panel_draw.text(
            (x, y),
            shaped,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 220),
        )

        y += line_heights[i] + spacing

    top = max(55, HEIGHT - panel_h - 115)

    image.alpha_composite(panel, (20, top))
    image.save(output_path)

    return output_path


# ============================================================
# VIDEO RENDERING
# ============================================================

def build_scene(source_path, caption_path, output_path):
    update_message = f"scene output {output_path.name}"
    log.info("BUILD SCENE: %s", update_message)

    filter_complex = (
        f"[0:v]"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"eq=contrast=1.035:saturation=1.06:brightness=0.005,"
        f"zoompan=z='min(zoom+0.00008,1.02)':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"fps={FPS},setsar=1,"
        f"trim=duration={CLIP_SECONDS:.6f},"
        f"setpts=PTS-STARTPTS[base];"
        f"[1:v]format=rgba[cap];"
        f"[base][cap]overlay=0:0:shortest=1,"
        f"format=yuv420p[out]"
    )

    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-stream_loop", "-1",
            "-i", str(source_path),
            "-loop", "1",
            "-framerate", str(FPS),
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
            str(output_path),
        ],
        timeout=180,
    )

    if (
        not output_path.exists()
        or output_path.stat().st_size < 30000
        or not probe_video(output_path)
    ):
        raise RuntimeError("تعذر تجهيز أحد مشاهد الفيديو.")

    return output_path


def media_duration(path):
    result = run_command(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=25,
    )

    try:
        return float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "تعذّر تحديد مدة ملف الوسائط."
        ) from exc


def concatenate_scenes(scene_paths, workdir):
    if len(scene_paths) != SCENES:
        raise RuntimeError("عدد مشاهد الفيديو غير صحيح.")

    inputs = []

    for path in scene_paths:
        inputs.extend(["-i", str(path)])

    pieces = []

    for i in range(SCENES):
        pieces.append(
            f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,"
            f"fps={FPS},format=yuv420p[v{i}]"
        )

    current = "v0"
    offset_step = CLIP_SECONDS - TRANSITION_SECONDS

    for i in range(1, SCENES):
        out_label = f"xf{i}"
        offset = offset_step * i

        pieces.append(
            f"[{current}][v{i}]"
            f"xfade=transition=fade:"
            f"duration={TRANSITION_SECONDS:.3f}:"
            f"offset={offset:.6f}[{out_label}]"
        )

        current = out_label

    output = workdir / "video_only.mp4"

    log.info("MERGING %s SCENES", len(scene_paths))

    run_command(
        [
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
            str(output),
        ],
        timeout=240,
    )

    if not output.exists() or not probe_video(output):
        raise RuntimeError("تعذر دمج مشاهد الفيديو.")

    duration = media_duration(output)

    if duration < TOTAL_SECONDS - 0.25:
        raise RuntimeError(
            f"مدة الفيديو بعد الدمج قصيرة: {duration:.2f} ثانية."
        )

    return output


# ============================================================
# AUDIO
# ============================================================

async def edge_tts_to_file(text, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE,
    )

    await communicate.save(str(output_path))


def make_voice(text, output_path):
    try:
        asyncio.run(
            edge_tts_to_file(text, output_path)
        )

        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path

    except Exception as exc:
        log.warning("Edge TTS failed: %s", str(exc)[:200])

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
            slow=False,
        ).save(str(output_path))

        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path

    except Exception as exc:
        log.warning("gTTS fallback failed: %s", str(exc)[:200])

    raise RuntimeError(
        "تعذّر إنشاء الراوي العربي. تحقق من اتصال Render."
    )


def create_narration(story, workdir):
    raw = workdir / "narration_raw.mp3"
    output = workdir / "narration.m4a"

    log.info("CREATING ARABIC NARRATION")

    make_voice(story, raw)
    duration = media_duration(raw)

    tempo = max(1.0, duration / TOTAL_SECONDS)

    if tempo > 1.35:
        raise RuntimeError(
            "القصة أطول من مدة الفيديو المناسبة. "
            "اختصرها إلى نحو 60 كلمة ثم أعد المحاولة."
        )

    audio_filter = (
        f"atempo={tempo:.4f},"
        "aresample=44100,"
        "apad,"
        f"atrim=0:{TOTAL_SECONDS},"
        "loudnorm=I=-18:TP=-2:LRA=7"
    )

    run_command(
        [
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
            str(output),
        ],
        timeout=120,
    )

    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("فشل تجهيز ملف الراوي العربي.")

    return output


def create_ambient_audio(workdir):
    output = workdir / "ambient.m4a"

    filter_complex = (
        "[0:a]volume=0.030[a0];"
        "[1:a]volume=0.012[a1];"
        "[2:a]volume=0.008[a2];"
        "[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,"
        "lowpass=f=700,highpass=f=45,"
        "afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={TOTAL_SECONDS - 2}:d=2,"
        "volume=0.8[m]"
    )

    run_command(
        [
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
            "-filter_complex", filter_complex,
            "-map", "[m]",
            "-t", str(TOTAL_SECONDS),
            "-ac", "2",
            "-ar", "44100",
            "-c:a", "aac",
            "-b:a", "96k",
            str(output),
        ],
        timeout=90,
    )

    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("تعذر إنشاء الخلفية الصوتية.")

    return output


def mux_final_video(video_path, narration_path, ambient_path, output_path):
    log.info("MIXING FINAL VIDEO AND AUDIO")

    filter_complex = (
        "[0:a]asplit=2[voice][side];"
        "[1:a]volume=1.0[bed];"
        "[bed][side]sidechaincompress="
        "threshold=0.025:ratio=7:attack=25:release=450[duck];"
        "[voice][duck]amix=inputs=2:duration=first:normalize=0,"
        "alimiter=limit=0.92,aresample=44100[a]"
    )

    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(narration_path),
            "-i", str(ambient_path),
            "-i", str(video_path),
            "-filter_complex", filter_complex,
            "-map", "2:v:0",
            "-map", "[a]",
            "-t", str(TOTAL_SECONDS),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            str(output_path),
        ],
        timeout=180,
    )

    if (
        not output_path.exists()
        or output_path.stat().st_size < 100000
        or not probe_video(output_path)
    ):
        raise RuntimeError("التحقق من الفيديو النهائي فشل.")

    duration = media_duration(output_path)

    if not (
        TOTAL_SECONDS - 0.3
        <= duration
        <= TOTAL_SECONDS + 0.5
    ):
        raise RuntimeError(
            f"مدة الفيديو النهائي غير صحيحة: {duration:.2f} ثانية."
        )

    return output_path


# ============================================================
# GENERATE VIDEO
# ============================================================

def generate_video(story, workdir, job_id):
    update_job(job_id, "validating_story")

    if not PIXABAY_API_KEY:
        raise RuntimeError(
            "PIXABAY_API_KEY غير موجود في إعدادات Render."
        )

    captions = make_scene_texts(story)
    scene_paths = []

    for i, caption in enumerate(captions):
        update_job(
            job_id,
            f"scene_{i + 1}_of_{SCENES}",
        )

        log.info(
            "JOB %s: RENDERING SCENE %s/%s",
            job_id,
            i + 1,
            SCENES,
        )

        source = obtain_clip(
            caption or story,
            i,
            workdir,
        )

        caption_path = workdir / f"caption_{i:02d}.png"
        scene_path = workdir / f"scene_{i:02d}.mp4"

        create_caption_image(
            caption or "",
            caption_path,
        )

        build_scene(
            source,
            caption_path,
            scene_path,
        )

        scene_paths.append(scene_path)

    update_job(job_id, "merging_scenes")

    video = concatenate_scenes(
        scene_paths,
        workdir,
    )

    update_job(job_id, "creating_narration")
    voice = create_narration(story, workdir)

    update_job(job_id, "creating_ambient_audio")
    ambient = create_ambient_audio(workdir)

    update_job(job_id, "mixing_final_video")

    final_path = mux_final_video(
        video,
        voice,
        ambient,
        workdir / "zil_final.mp4",
    )

    update_job(job_id, "video_ready")

    return final_path


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def reply(update, text):
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def send_video(update, path):
    if not update.effective_message:
        return

    log.info("SENDING VIDEO TO TELEGRAM")

    with open(path, "rb") as video_file:
        await update.effective_message.reply_video(
            video=video_file,
            caption="تم إنشاء فيديو ظل.",
            supports_streaming=True,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
            pool_timeout=30,
        )

    log.info("VIDEO SENT SUCCESSFULLY")


async def generate_for_user(update, story):
    user = update.effective_user

    if not user:
        log.warning("UPDATE HAS NO EFFECTIVE USER")
        return

    uid = user.id

    log.info(
        "GENERATION REQUEST RECEIVED: user=%s",
        uid,
    )

    with busy_lock:
        if uid in busy_users:
            already_busy = True
        else:
            busy_users.add(uid)
            already_busy = False

    if already_busy:
        await reply(
            update,
            "طلبك السابق ما زال قيد التنفيذ. انتظر حتى ينتهي.",
        )
        return

    job_id = uuid.uuid4().hex[:10]
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    update_job(job_id, "request_received")

    try:
        story = clean_story(story)

        if len(story) < 10:
            await reply(
                update,
                "اكتب قصة أطول بعد الأمر، مثال:\n"
                "/zil بطل غامض يصل إلى القصر وينقذ الأميرة.",
            )
            update_job(job_id, "rejected_short_story")
            return

        log.info(
            "JOB %s: STORY ACCEPTED (%s characters)",
            job_id,
            len(story),
        )

        await reply(
            update,
            "بدأت صناعة فيديو ظل.\n"
            "6 مشاهد، انتقالات ناعمة، فيديو عمودي، "
            "راوي عربي وخلفية صوتية.\n\n"
            "قد تستغرق العملية عدة دقائق. "
            "سيتم إبلاغك عند حدوث خطأ.",
        )

        loop = asyncio.get_running_loop()

        log.info(
            "JOB %s: STARTING BACKGROUND VIDEO GENERATION",
            job_id,
        )

        final_path = await loop.run_in_executor(
            None,
            generate_video,
            story,
            workdir,
            job_id,
        )

        update_job(job_id, "sending_video")

        await send_video(update, final_path)

        update_job(job_id, "completed")

    except Exception as exc:
        log.exception("GENERATION FAILED: job=%s", job_id)

        update_job(
            job_id,
            "failed",
            str(exc),
        )

        message = str(exc)

        if "PIXABAY_API_KEY" in message:
            friendly = (
                "فشل إنشاء الفيديو: مفتاح PIXABAY_API_KEY "
                "غير موجود في Render."
            )
        elif "تعذر تنزيل" in message or "Pixabay" in message:
            friendly = (
                "تعذر الحصول على مقاطع فيديو صالحة من Pixabay. "
                "تحقق من المفتاح واتصال Render."
            )
        elif "القصة أطول" in message:
            friendly = message
        elif "الراوي العربي" in message:
            friendly = "تعذر إنشاء الراوي العربي. تحقق من اتصال Render."
        else:
            friendly = (
                f"فشل إنشاء الفيديو. رقم العملية: {job_id}\n"
                "افحص Render Logs لمعرفة المرحلة التي توقفت."
            )

        try:
            await reply(update, friendly)
        except Exception:
            log.exception(
                "Could not send error reply for job %s",
                job_id,
            )

    finally:
        with busy_lock:
            busy_users.discard(uid)

        shutil.rmtree(workdir, ignore_errors=True)

        log.info("JOB %s: CLEANUP FINISHED", job_id)


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.info("START COMMAND RECEIVED")

    await reply(
        update,
        "أهلًا بك في ظل ZIL.\n\n"
        "أرسل قصتك مباشرة، أو استخدم:\n"
        "/zil قصتك هنا\n\n"
        "الأوامر:\n"
        "/start - تشغيل البوت\n"
        "/status - حالة الخدمة\n"
        "/test - فيديو تجريبي\n"
        "/zil - إنشاء فيديو من قصتك\n"
        "/ظل - الأمر العربي لإنشاء فيديو",
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    with busy_lock:
        active = len(busy_users)

    with jobs_lock:
        recent = list(jobs.items())[-5:]

    recent_text = "\n".join(
        f"{jid}: {data.get('status', 'unknown')}"
        for jid, data in recent
    ) or "لا توجد عمليات مسجلة."

    await reply(
        update,
        "حالة ظل ZIL\n\n"
        f"البوت: {'مهيأ' if BOT_TOKEN else 'الرمز غير موجود'}\n"
        f"Pixabay key: {'موجود' if PIXABAY_API_KEY else 'غير موجود'}\n"
        f"FFmpeg: {'جاهز' if check_binary('ffmpeg') else 'غير موجود'}\n"
        f"FFprobe: {'جاهز' if check_binary('ffprobe') else 'غير موجود'}\n"
        f"المشاهد: {SCENES}\n"
        f"المدة: {TOTAL_SECONDS} ثانية\n"
        f"الدقة: {WIDTH}x{HEIGHT}\n"
        f"الطلبات النشطة: {active}\n\n"
        f"آخر العمليات:\n{recent_text}",
    )


async def zil_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.info("ZIL COMMAND RECEIVED")

    story = " ".join(context.args).strip()

    if (
        not story
        and update.effective_message
        and update.effective_message.text
    ):
        story = re.sub(
            r"^/\S+\s*",
            "",
            update.effective_message.text,
        ).strip()

    if not story:
        await reply(
            update,
            "اكتب القصة بعد الأمر، مثال:\n"
            "/zil بطل غامض يدخل القصر.",
        )
        return

    await generate_for_user(update, story)


async def arabic_zil_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.info("ARABIC ZIL COMMAND RECEIVED")

    message = update.effective_message
    text = message.text if message else ""

    story = re.sub(
        r"^/ظل(?:@\w+)?\s*",
        "",
        text or "",
    ).strip()

    if not story:
        await reply(
            update,
            "اكتب القصة بعد الأمر، مثال:\n"
            "/ظل بطل غامض يدخل القصر.",
        )
        return

    await generate_for_user(update, story)


async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.info("TEST COMMAND RECEIVED")

    story = (
        "في مملكة بعيدة ظهر رجل غامض عند أبواب القصر. "
        "رفض الملك السماح له بالاقتراب من الأميرة. "
        "في الليل ظهر وحش ضخم قرب أسوار المملكة. "
        "تراجع الحراس أمام قوته الهائلة. "
        "وقف الرجل الغامض في طريق الوحش وكشف عن قوة غير متوقعة. "
        "لكن الملك لاحظ علامة قديمة على ذراعه وعرف سرًا خطيرًا."
    )

    log.info(
        "TEST STORY READY: %s characters",
        len(story),
    )

    await generate_for_user(update, story)


async def text_story_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip()

    if text.startswith("/"):
        return

    log.info("PLAIN TEXT STORY RECEIVED")

    await generate_for_user(update, text)


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    log.error(
        "TELEGRAM HANDLER ERROR",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )


# ============================================================
# FLASK
# ============================================================

@flask_app.get("/")
def home():
    return jsonify({
        "service": "ZIL",
        "status": "online",
        "video_seconds": TOTAL_SECONDS,
        "scenes": SCENES,
        "transitions": True,
    })


@flask_app.get("/health")
def health():
    ffmpeg_ok = check_binary("ffmpeg")
    ffprobe_ok = check_binary("ffprobe")

    ok = (
        bool(BOT_TOKEN)
        and ffmpeg_ok
        and ffprobe_ok
    )

    return jsonify({
        "service": "ZIL",
        "status": "healthy" if ok else "degraded",
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "pixabay_key_configured": bool(PIXABAY_API_KEY),
        "video_seconds": TOTAL_SECONDS,
        "scenes": SCENES,
        "transitions": True,
    }), (200 if ok else 503)


@flask_app.get("/jobs")
def jobs_endpoint():
    with jobs_lock:
        snapshot = dict(jobs)

    return jsonify({
        "service": "ZIL",
        "jobs": snapshot,
    })


def run_flask():
    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    validate_environment()

    log.info("BOT STARTUP: environment validated")
    log.info("BOT STARTUP: building Telegram application")

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
            arabic_zil_handler,
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_story_handler,
        )
    )

    telegram_app.add_error_handler(error_handler)

    threading.Thread(
        target=run_flask,
        daemon=True,
        name="zil-flask",
    ).start()

    log.info("BOT STARTUP: Flask thread started")
    log.info("BOT STARTUP: starting Telegram polling")

    telegram_app.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
