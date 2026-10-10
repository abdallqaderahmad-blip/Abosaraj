import os
import sys
import re
import time
import uuid
import asyncio
import logging
import threading
import subprocess
import tempfile
import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL CONFIGURATION
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PIXABAY_API_KEY = (os.getenv("PIXABAY_API_KEY", "").strip() or os.getenv("PIXABAY_KEY", "").strip())
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))
PIXABAY_API = "https://pixabay.com/api/videos/"

SCENE_COUNT = 18
SCENE_DURATION = 5
VIDEO_DURATION = SCENE_COUNT * SCENE_DURATION  # exactly 90 seconds target
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024

# Narration is intentionally slower, but individual lines are kept short
# enough to fit their own five-second scene.
TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
TTS_RATE = os.getenv("TTS_RATE", "-15%")

BASE_DIR = Path(tempfile.gettempdir()) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("ZIL")
app = Flask(__name__)
JOBS = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOBS = 0

# Scene search is story-shaped, but Pixabay stock footage cannot guarantee the
# same actors/characters across shots. SFX are synthesized locally by FFmpeg;
# they are stylized effects, not recordings of real animals or real impacts.
SCENE_BLUEPRINTS = [
    ("cinematic mysterious kingdom mountains castle", "في مملكة بعيدة، كان سر قديم يقترب من الظهور.", "mystery"),
    ("dark medieval castle exterior cinematic", "خلف أسوار القصر، كان الجميع يخشى ما لا يعرفه.", "castle"),
    ("mysterious man walking cloak cinematic silhouette", "ثم وصل رجل غامض، يخفي قوة لا يريد لأحد رؤيتها.", "steps"),
    ("medieval palace beautiful princess royal hall", "رأت الأميرة فيه شيئًا مختلفًا عن كل من عرفتهم.", "romance"),
    ("medieval king throne room serious king", "لكن الملك رفض اقترابه، وكأن ماضيه يحمل خطرًا.", "castle"),
    ("princess looking toward mysterious man dramatic", "لم تتراجع الأميرة، بينما ازدادت الشكوك حول الغريب.", "tension"),
    ("dark forest storm clouds ominous cinematic", "وفجأة، اهتزت الأرض وغطّى الهدير أطراف المملكة.", "storm"),
    ("large tiger roaring close up wildlife", "ظهر نمر هائل، وزمجر حتى ارتجفت بوابات القصر.", "beast"),
    ("tiger running charging wildlife dramatic", "اندفع الوحش نحو القصر، ولم يعد أمام الحراس وقت.", "beast"),
    ("medieval guards running with swords castle", "تراجع الحراس، ووقف الملك عاجزًا أمام الخطر.", "steps"),
    ("mysterious warrior standing facing giant beast", "عندها تقدّم الرجل بهدوء، وكأنه كان ينتظر هذه اللحظة.", "tension"),
    ("tiger attack action wildlife dust dramatic", "انقضّ النمر، فاشتعلت المواجهة وسط الغبار والصراخ.", "fight"),
    ("fantasy warrior fighting giant beast cinematic", "تفادى الضربة الأولى، ثم ردّ بقوة لم يتوقعها أحد.", "fight"),
    ("epic action impact dust ground cinematic", "دوّى الاصطدام، وتراجعت خطوات الوحش لأول مرة.", "impact"),
    ("blue magical energy lightning fantasy warrior", "بدأت طاقة غريبة تتوهّج حوله، وانكشف جزء من سره.", "magic"),
    ("epic fantasy battle energy burst smoke", "تجمّد الجميع حين أدركوا أن ضعفه كان مجرد تمويه.", "magic"),
    ("giant tiger defeated lying ground cinematic", "سقط الوحش، لكن الرجل أخفى قوته قبل أن يراه الملك.", "impact"),
    ("dark castle night mysterious silhouette cliffhanger", "وفي تلك اللحظة، ظهر أثر جديد… سرّ أخطر ينتظرهم.", "cliffhanger"),
]

# =========================================================
# UTILITIES
# =========================================================
def command_exists(name):
    try:
        return subprocess.run(["which", name], capture_output=True, text=True, timeout=5).returncode == 0
    except Exception:
        return False


def run_command(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Unknown command error")[-3000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {error}")
    return result


def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)
            JOBS[job_id]["updated_at"] = time.time()


def get_active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


def notify_admin(message):
    if not ADMIN_CHAT_ID or not BOT_TOKEN:
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": message[:3500]}, timeout=15,
        )
        response.raise_for_status()
    except Exception:
        log.exception("Admin notification failed")


def report_error(job_id, error):
    log.exception("Job %s failed: %s", job_id, error)
    update_job(job_id, status="failed", stage="failed", error=str(error)[:2000])
    threading.Thread(
        target=notify_admin,
        args=(f"ZIL VIDEO ERROR\n\nJob: {job_id}\nError: {str(error)[:2500]}",),
        daemon=True,
    ).start()


def probe_duration(path):
    if not command_exists("ffprobe"):
        return None
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=30)
    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return None

# =========================================================
# STORY / SEARCH MATCHING
# =========================================================
def choose_scene_blueprint(story, index):
    """Use the 18-beat dramatic arc, and bias searches toward words in the user's story."""
    query, narration, effect = SCENE_BLUEPRINTS[index]
    lower = story.lower()
    # Slightly prioritize recognizable user-story events while retaining a coherent arc.
    if any(word in lower for word in ("نمر", "أسد", "وحش", "tiger", "beast", "lion")) and index in (7, 8, 11, 12, 13, 16):
        query = "large tiger beast roaring charging action wildlife cinematic"
    if any(word in lower for word in ("أميرة", "princess")) and index in (3, 5):
        query = "princess royal medieval palace cinematic"
    if any(word in lower for word in ("ملك", "الملك", "king")) and index in (4, 9):
        query = "medieval king throne room castle guards cinematic"
    if any(word in lower for word in ("قوة", "خارق", "سحر", "magic", "power")) and index in (14, 15):
        query = "fantasy magical energy lightning warrior cinematic"
    return query, narration, effect


def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing in Render Environment.")
    response = requests.get(PIXABAY_API, params={
        "key": PIXABAY_API_KEY, "q": query, "per_page": 10,
        "safesearch": "true", "video_type": "film",
    }, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "hits" not in data:
        raise RuntimeError("Unexpected Pixabay API response.")
    # Pick a usable clip from the returned results, preferring medium/large.
    for hit in data.get("hits", []):
        videos = hit.get("videos") or {}
        for quality in ("medium", "large", "small", "tiny"):
            url = (videos.get(quality) or {}).get("url", "")
            if url and urlparse(url).scheme == "https":
                return url
    raise RuntimeError(f"No downloadable video found for: {query}")


def download_video(url, destination):
    total = 0
    max_bytes = 150 * 1024 * 1024
    with requests.get(url, stream=True, timeout=(20, 90)) as response:
        response.raise_for_status()
        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("Downloaded video exceeds 150 MB.")
                output.write(chunk)
    if total < 10000:
        raise RuntimeError("Downloaded video is too small.")
    return destination

# =========================================================
# VIDEO + SUBTITLE PROCESSING
# =========================================================
def normalize_scene(source, destination):
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-t", str(SCENE_DURATION), "-vf",
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps={VIDEO_FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ], timeout=180)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("FFmpeg produced an empty scene.")
    return destination


def add_arabic_caption(source, caption, destination, workdir, index):
    """Burn a short Arabic caption when Pillow + Arabic shaping are installed; otherwise keep video intact."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning("Arabic subtitle dependencies unavailable; continuing without burned subtitles.")
        return source
    try:
        image = Image.new("RGBA", (VIDEO_WIDTH, 170), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 31) if Path(font_path).exists() else ImageFont.load_default()
        reshaped = get_display(arabic_reshaper.reshape(caption))
        # Keep caption compact enough to fit the width.
        if draw.textbbox((0, 0), reshaped, font=font)[2] > VIDEO_WIDTH - 48:
            font = ImageFont.truetype(font_path, 25) if Path(font_path).exists() else font
        bbox = draw.textbbox((0, 0), reshaped, font=font, stroke_width=1)
        tw = bbox[2] - bbox[0]
        x = max(18, (VIDEO_WIDTH - tw) // 2)
        y = 45
        draw.rounded_rectangle((12, 22, VIDEO_WIDTH - 12, 125), radius=20, fill=(0, 0, 0, 160))
        draw.text((x, y), reshaped, font=font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 220))
        png_path = workdir / f"caption_{index}.png"
        image.save(png_path)
        run_command([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(png_path),
            "-filter_complex", "[0:v][1:v]overlay=0:H-170:format=auto[v]",
            "-map", "[v]", "-t", str(SCENE_DURATION), "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
            "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination),
        ], timeout=120)
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Could not burn Arabic subtitle for scene %s", index)
    return source

# =========================================================
# ARABIC NARRATION — PER-SCENE SYNC
# =========================================================
def create_narration(text, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        import edge_tts
        async def generate():
            communicate = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
            await communicate.save(str(destination))
        asyncio.run(generate())
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Edge TTS failed; trying gTTS.")
    try:
        from gtts import gTTS
        gTTS(text=text, lang="ar", slow=False).save(str(destination))
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("gTTS failed.")
    raise RuntimeError("Arabic narration generation failed.")


def fit_audio_to_scene(source, destination, duration=SCENE_DURATION):
    """Fit each voice line into its own scene: moderate speed-up if needed, then pad with silence."""
    src_duration = probe_duration(source)
    if not src_duration or src_duration <= 0:
        raise RuntimeError(f"Could not read narration duration: {source}")
    # Keep acceleration modest; shorter scripts should leave breathing room.
    speed = min(1.65, max(1.0, src_duration / (duration - 0.12)))
    atempo = f"atempo={speed:.4f}," if speed > 1.001 else ""
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", f"{atempo}apad=pad_dur={duration},atrim=duration={duration},asetpts=PTS-STARTPTS",
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", str(destination),
    ], timeout=90)
    return destination

# =========================================================
# SOUND DESIGN: per-scene generated effects + background bed
# =========================================================
def make_scene_sfx(effect_type, destination, duration=SCENE_DURATION):
    """Create stylized synthetic effects with FFmpeg, no external audio API/key needed."""
    duration = float(duration)
    # These are deliberately stylized sound textures, not claims of real recorded tiger/impact audio.
    if effect_type == "beast":
        filt = "highpass=f=45,lowpass=f=900,tremolo=f=7:d=0.8,volume=0.38,afade=t=in:d=0.15,afade=t=out:st=3.8:d=1.0"
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={duration}"
    elif effect_type in ("fight", "impact"):
        filt = "highpass=f=55,lowpass=f=1800,volume=0.55,afade=t=in:d=0.02,afade=t=out:st=0.7:d=0.7"
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
    elif effect_type == "magic":
        source = f"sine=frequency=95:sample_rate=44100:duration={duration}"
        filt = "tremolo=f=3:d=0.75,chorus=0.5:0.7:45:0.35:0.25:2,volume=0.24,afade=t=in:d=0.8,afade=t=out:st=3.6:d=1.2"
    elif effect_type == "storm":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        filt = "lowpass=f=500,volume=0.18,tremolo=f=0.25:d=0.4,afade=t=in:d=0.8,afade=t=out:st=3.5:d=1.3"
    elif effect_type in ("tension", "mystery", "cliffhanger"):
        source = f"sine=frequency=72:sample_rate=44100:duration={duration}"
        filt = "tremolo=f=2:d=0.55,lowpass=f=500,volume=0.15,afade=t=in:d=0.8,afade=t=out:st=3.6:d=1.2"
    elif effect_type == "steps":
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={duration}"
        filt = "lowpass=f=240,volume=0.16,tremolo=f=1.7:d=0.85,afade=t=in:d=0.1,afade=t=out:st=3.7:d=1.0"
    elif effect_type == "romance":
        source = f"sine=frequency=440:sample_rate=44100:duration={duration}"
        filt = "vibrato=f=4:d=0.15,lowpass=f=1200,volume=0.055,afade=t=in:d=0.8,afade=t=out:st=3.5:d=1.2"
    elif effect_type == "castle":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        filt = "highpass=f=100,lowpass=f=700,volume=0.07,afade=t=in:d=0.5,afade=t=out:st=3.7:d=1.0"
    else:
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={duration}"
        filt = "lowpass=f=1000,volume=0.08,afade=t=in:d=0.4,afade=t=out:st=3.8:d=1.0"
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", source, "-af", filt,
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination),
    ], timeout=60)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Could not create SFX: {effect_type}")
    return destination


def make_background_music(destination, duration):
    # A subtle generated drone/ambience bed. For true cinematic music, replace this
    # with a properly licensed music track; this is a lightweight fallback texture.
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=55:sample_rate=44100:duration={duration}",
        "-af", "volume=0.045,tremolo=f=0.18:d=0.25,afade=t=in:d=2,afade=t=out:st=86:d=4",
        "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(destination),
    ], timeout=90)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Background audio generation failed.")
    return destination


def concatenate_media(paths, destination, workdir, name):
    list_file = workdir / f"{name}_concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for path in paths:
            # Work directory is generated from hex job id, so no apostrophes expected.
            f.write(f"file '{Path(path).resolve().as_posix()}'\n")
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
        "-safe", "0", "-i", str(list_file), "-t", str(VIDEO_DURATION),
        "-c", "copy", str(destination),
    ], timeout=180)
    return destination


def mix_final_audio(video, voice_track, sfx_track, music_track, output):
    # Every input is padded/trimmed to 90s, and amix does not cut video to voice length.
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(voice_track), "-i", str(sfx_track), "-i", str(music_track),
        "-filter_complex",
        "[1:a]volume=1.0,apad,atrim=duration=90[voice];"
        "[2:a]volume=0.48,apad,atrim=duration=90[sfx];"
        "[3:a]volume=0.30,apad,atrim=duration=90[music];"
        "[voice][sfx][music]amix=inputs=3:duration=longest:dropout_transition=0,"
        "alimiter=limit=0.92,atrim=duration=90[aout]",
        "-map", "0:v:0", "-map", "[aout]", "-t", "90",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
    ], timeout=240)
    if not output.exists() or output.stat().st_size < 10000:
        raise RuntimeError("Final video file is missing or too small.")
    return output

# =========================================================
# TELEGRAM ASYNC BRIDGE
# =========================================================
def send_coroutine(application, coroutine, timeout=360):
    loop = application.bot_data.get("event_loop")
    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError("Telegram event loop is not available.")
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return future.result(timeout=timeout)


async def send_video_to_user(application, chat_id, video_path, job_id):
    with open(video_path, "rb") as video_file:
        await application.bot.send_video(
            chat_id=chat_id, video=video_file,
            caption=f"تم إنشاء فيديو ظل ZIL السينمائي.\nالمدة المستهدفة: 90 ثانية\nرقم العملية: {job_id}",
            supports_streaming=True, read_timeout=180, write_timeout=180,
            connect_timeout=30, pool_timeout=30,
        )


async def send_text_to_user(application, chat_id, message):
    await application.bot.send_message(chat_id=chat_id, text=message[:3500])

# =========================================================
# VIDEO GENERATION JOB
# =========================================================
def build_video(job_id, chat_id, story, telegram_app):
    global ACTIVE_JOBS
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        update_job(job_id, status="running", stage="preflight")
        if not command_exists("ffmpeg") or not command_exists("ffprobe"):
            raise RuntimeError("FFmpeg/FFprobe missing; check Dockerfile installs ffmpeg.")
        if not PIXABAY_API_KEY:
            raise RuntimeError("PIXABAY_API_KEY is missing.")
        story = (story or "").strip()[:MAX_STORY_LENGTH]
        if not story:
            raise RuntimeError("Story is empty.")

        scene_videos, voice_clips, sfx_clips = [], [], []
        captions = []
        for index in range(SCENE_COUNT):
            scene_no = index + 1
            query, narration_line, effect_type = choose_scene_blueprint(story, index)
            captions.append(narration_line)
            update_job(job_id, stage=f"scene_{scene_no}_download", progress=round(index / SCENE_COUNT * 100))
            try:
                video_url = search_pixabay_video(query)
            except Exception as err:
                log.warning("Scene %s search failed (%s), trying related fallback", scene_no, err)
                fallback_query = "cinematic medieval fantasy castle warrior dramatic"
                video_url = search_pixabay_video(fallback_query)
            raw_path = workdir / f"raw_{scene_no}.mp4"
            normalized = workdir / f"scene_{scene_no}_base.mp4"
            captioned = workdir / f"scene_{scene_no}.mp4"
            download_video(video_url, raw_path)
            normalize_scene(raw_path, normalized)
            add_arabic_caption(normalized, narration_line, captioned, workdir, scene_no)
            if captioned.exists() and captioned.stat().st_size > 1000:
                scene_videos.append(captioned)
            else:
                scene_videos.append(normalized)
            raw_path.unlink(missing_ok=True)

            # Make a short narration segment for this exact scene, then fit it to five seconds.
            voice_raw = workdir / f"voice_{scene_no}_raw.mp3"
            voice_fit = workdir / f"voice_{scene_no}.m4a"
            create_narration(narration_line, voice_raw)
            fit_audio_to_scene(voice_raw, voice_fit, SCENE_DURATION)
            voice_clips.append(voice_fit)

            sfx_path = workdir / f"sfx_{scene_no}.m4a"
            make_scene_sfx(effect_type, sfx_path, SCENE_DURATION)
            sfx_clips.append(sfx_path)
            update_job(job_id, stage=f"scene_{scene_no}_complete", progress=round(scene_no / SCENE_COUNT * 100))

        update_job(job_id, stage="concatenate_video")
        silent_video = workdir / "silent_video.mp4"
        concatenate_media(scene_videos, silent_video, workdir, "video")

        update_job(job_id, stage="concatenate_voice")
        voice_track = workdir / "voice_90s.m4a"
        concatenate_media(voice_clips, voice_track, workdir, "voice")

        update_job(job_id, stage="concatenate_sfx")
        sfx_track = workdir / "sfx_90s.m4a"
        concatenate_media(sfx_clips, sfx_track, workdir, "sfx")

        update_job(job_id, stage="background_music")
        music_track = workdir / "ambient_90s.m4a"
        make_background_music(music_track, VIDEO_DURATION)

        update_job(job_id, stage="mix_audio")
        final_path = workdir / "ZIL_video.mp4"
        mix_final_audio(silent_video, voice_track, sfx_track, music_track, final_path)

        duration = probe_duration(final_path)
        if duration is None or abs(duration - VIDEO_DURATION) > 0.25:
            raise RuntimeError(f"Final duration check failed: {duration} seconds; expected 90 seconds.")

        update_job(job_id, status="sending", stage="sending_video", output=str(final_path), size=final_path.stat().st_size, duration=duration)
        if final_path.stat().st_size > MAX_VIDEO_SIZE:
            send_coroutine(telegram_app, send_text_to_user(telegram_app, chat_id,
                f"اكتمل الفيديو ومدته {duration:.2f} ثانية، لكن حجمه تجاوز حد الإرسال. رقم العملية: {job_id}"), timeout=45)
            update_job(job_id, status="completed", stage="completed_file_too_large")
            return
        send_coroutine(telegram_app, send_video_to_user(telegram_app, chat_id, final_path, job_id), timeout=360)
        update_job(job_id, status="completed", stage="completed", duration=duration)
        log.info("Job %s completed: %.3f seconds", job_id, duration)
    except Exception as error:
        report_error(job_id, error)
        try:
            send_coroutine(telegram_app, send_text_to_user(telegram_app, chat_id,
                f"تعذر إكمال الفيديو.\nرقم العملية: {job_id}\nاستخدم /last_error لمعرفة السبب."), timeout=45)
        except Exception:
            log.exception("Could not notify user about failure.")
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)

# =========================================================
# TELEGRAM COMMANDS
# =========================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        "أهلًا بك في ظل ZIL.\n\n"
        "/test - إنشاء فيديو تجريبي 90 ثانية\n"
        "/make نص القصة - إنشاء فيديو من قصتك\n"
        "/status - حالة العمليات\n"
        "/diagnose - فحص النظام\n"
        "/last_error - آخر خطأ\n"
        "/health - حالة الخدمة"
    )


async def start_job(update, context, story):
    global ACTIVE_JOBS
    if not update.message or not update.effective_chat:
        return
    story = (story or "").strip()[:MAX_STORY_LENGTH]
    if not story:
        await update.message.reply_text("أرسل نص القصة أولًا.")
        return
    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            busy = True
        else:
            busy = False
            job_id = uuid.uuid4().hex[:10]
            JOBS[job_id] = {
                "id": job_id, "chat_id": update.effective_chat.id,
                "status": "queued", "stage": "queued", "story": story[:500],
                "created_at": time.time(), "updated_at": time.time(),
            }
            ACTIVE_JOBS += 1
    if busy:
        await update.message.reply_text("يوجد فيديو قيد المعالجة. حاول مرة أخرى لاحقًا.")
        return
    await update.message.reply_text(
        f"بدأت صناعة فيديو ظل ZIL.\nرقم العملية: {job_id}\n"
        "18 مشهدًا × 5 ثوانٍ = 90 ثانية، راوي عربي، ترجمة عربية، ومؤثرات متزامنة.\n"
        "قد تستغرق العملية وقتًا لأن كل مشهد يُبحث عنه ويُعالج منفصلًا."
    )
    try:
        threading.Thread(target=build_video, args=(job_id, update.effective_chat.id, story, context.application), daemon=True).start()
    except Exception as error:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)
        report_error(job_id, error)
        await update.message.reply_text("تعذر بدء عملية إنشاء الفيديو.")


async def test_command(update, context):
    story = (
        "في مملكة غامضة، يصل رجل يخفي قوة خارقة. تقع الأميرة في حبه، لكن الملك يرفض العلاقة. "
        "يظهر نمر عملاق أمام القصر، فيواجهه الرجل ويكشف جزءًا من قوته المخفية، ثم يظهر سر جديد يهدد المملكة."
    )
    await start_job(update, context, story)


async def make_command(update, context):
    story = " ".join(context.args).strip()
    if not story:
        await update.message.reply_text("اكتب القصة بعد الأمر:\n/make وصل رجل غامض إلى القصر...")
        return
    await start_job(update, context, story)


async def status_command(update, context):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        recent = list(JOBS.values())[-5:]
    lines = [
        "حالة ظل ZIL", f"العمليات النشطة: {active}",
        "Pixabay: جاهز" if PIXABAY_API_KEY else "Pixabay: مفتاح مفقود",
        "FFmpeg: جاهز" if command_exists("ffmpeg") else "FFmpeg: غير موجود",
        "FFprobe: جاهز" if command_exists("ffprobe") else "FFprobe: غير موجود", "", "آخر العمليات:",
    ]
    for job in reversed(recent):
        lines.append(f"\n{job['id']}\nالحالة: {job.get('status')}\nالمرحلة: {job.get('stage')}\nالتقدم: {job.get('progress', 0)}%")
        if job.get("duration"):
            lines.append(f"المدة: {job['duration']:.2f} ثانية")
        if job.get("error"):
            lines.append(f"الخطأ: {job['error'][:350]}")
    await update.message.reply_text("\n".join(lines)[:3900])


async def last_error_command(update, context):
    with JOBS_LOCK:
        failed = [job for job in JOBS.values() if job.get("status") == "failed"]
    if not failed:
        await update.message.reply_text("لا توجد أخطاء مسجلة حاليًا.")
        return
    job = failed[-1]
    await update.message.reply_text(
        f"آخر خطأ في ظل ZIL\n\nالعملية: {job['id']}\nالمرحلة: {job.get('stage')}\nالخطأ:\n{job.get('error', 'غير معروف')[:2500]}"
    )


async def diagnose_command(update, context):
    results = [
        f"BOT_TOKEN: {'OK' if BOT_TOKEN else 'MISSING'}",
        f"PIXABAY_API_KEY: {'OK' if PIXABAY_API_KEY else 'MISSING'}",
        f"ADMIN_CHAT_ID: {'OK' if ADMIN_CHAT_ID else 'OPTIONAL/MISSING'}",
        f"Python: {sys.version.split()[0]}",
        f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}",
        f"FFprobe: {'OK' if command_exists('ffprobe') else 'MISSING'}",
        f"Work directory: {'OK' if BASE_DIR.exists() else 'MISSING'}",
        f"Duration target: {VIDEO_DURATION}s ({SCENE_COUNT} scenes)",
        "SFX: generated stylized local effects (not real recordings)",
    ]
    if PIXABAY_API_KEY:
        try:
            response = requests.get(PIXABAY_API, params={"key": PIXABAY_API_KEY, "q": "nature", "per_page": 3, "safesearch": "true"}, timeout=15)
            results.append(f"Pixabay API: OK (hits={len(response.json().get('hits', []))})" if response.status_code == 200 else f"Pixabay API: HTTP {response.status_code}")
        except Exception as error:
            results.append(f"Pixabay API: ERROR {str(error)[:250]}")
    await update.message.reply_text("تشخيص ظل ZIL\n\n" + "\n".join(results))


async def health_command(update, context):
    await update.message.reply_text(
        f"ظل ZIL يعمل.\nPython: {sys.version.split()[0]}\n"
        f"FFmpeg: {'OK' if command_exists('ffmpeg') else 'MISSING'}\n"
        f"FFprobe: {'OK' if command_exists('ffprobe') else 'MISSING'}\n"
        f"Pixabay: {'OK' if PIXABAY_API_KEY else 'MISSING'}\nالمدة المستهدفة: {VIDEO_DURATION} ثانية"
    )

# =========================================================
# FLASK ENDPOINTS
# =========================================================
@app.get("/")
def home():
    return jsonify({"service": "ZIL", "status": "running", "target_duration_seconds": VIDEO_DURATION})


@app.get("/health")
def health():
    return jsonify({
        "status": "ok", "ffmpeg": command_exists("ffmpeg"),
        "ffprobe": command_exists("ffprobe"), "pixabay_configured": bool(PIXABAY_API_KEY),
        "active_jobs": get_active_jobs(), "target_duration_seconds": VIDEO_DURATION,
    })


def run_web():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# =========================================================
# STARTUP
# =========================================================
def main():
    log.info("Starting ZIL service; target duration=%s seconds", VIDEO_DURATION)
    log.info("Python: %s", sys.version)
    log.info("Port: %s", PORT)
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN is missing in Render Environment.")
        sys.exit(1)
    if not command_exists("ffmpeg") or not command_exists("ffprobe"):
        log.critical("FFmpeg/FFprobe missing. Check Dockerfile.")
        sys.exit(1)
    threading.Thread(target=run_web, daemon=True).start()

    async def save_event_loop(application):
        application.bot_data["event_loop"] = asyncio.get_running_loop()

    telegram_app = Application.builder().token(BOT_TOKEN).post_init(save_event_loop).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_handler(CommandHandler("make", make_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("diagnose", diagnose_command))
    telegram_app.add_handler(CommandHandler("last_error", last_error_command))
    telegram_app.add_handler(CommandHandler("health", health_command))
    log.info("Telegram polling starting")
    telegram_app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
