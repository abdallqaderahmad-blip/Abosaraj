import os
import re
import sys
import json
import time
import uuid
import random
import asyncio
import logging
import threading
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL - corrected, cost-conscious 90-second video bot
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
PIXABAY_API_KEY = (
    os.getenv("PIXABAY_API_KEY", "").strip()
    or os.getenv("PIXABAY_KEY", "").strip()
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
PIXABAY_API_URL = "https://pixabay.com/api/videos/"
PORT = int(os.getenv("PORT", "10000"))

SCENE_COUNT = 18
SCENE_DURATION = 5
VIDEO_DURATION = SCENE_COUNT * SCENE_DURATION
VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS = 720, 1280, 24
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "5000"))
GROQ_RETRIES = int(os.getenv("GROQ_RETRIES", "4"))
GROQ_MAX_WAIT = int(os.getenv("GROQ_MAX_WAIT", "60"))
TTS_VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
TTS_RATE = os.getenv("TTS_RATE", "-5%")

BASE_DIR = Path(os.getenv("ZIL_DATA_DIR", tempfile.gettempdir())) / "zil_video_jobs"
BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = BASE_DIR / "zil.log"
STATE_FILE = BASE_DIR / "jobs_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("ZIL")
app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZIL-Video-Bot/3.0"})
JOBS = {}
JOBS_LOCK = threading.RLock()
ACTIVE_JOBS = 0
EFFECTS = {
    "mystery", "castle", "steps", "romance", "tension", "storm",
    "beast", "fight", "impact", "magic", "cliffhanger",
}


# ----------------------- Persistence -----------------------
def save_state_locked():
    """Caller must hold JOBS_LOCK. Persist small job metadata, not media."""
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        safe = {}
        for key, value in JOBS.items():
            safe[key] = {
                k: v for k, v in value.items()
                if k not in {"narration"}  # avoid storing unnecessary text
            }
        tmp.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        log.exception("Could not persist job state")


def load_state():
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, job in data.items():
                    if isinstance(job, dict):
                        # A running job cannot survive a process restart.
                        if job.get("status") in {"running", "queued"}:
                            job["status"] = "interrupted"
                            job["stage"] = "interrupted_after_restart"
                            job["error"] = "Render restarted while this job was running."
                        JOBS[key] = job
    except Exception:
        log.exception("Could not load persisted job state")


def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)
            JOBS[job_id]["updated_at"] = time.time()
            save_state_locked()


def get_active_jobs():
    with JOBS_LOCK:
        return ACTIVE_JOBS


# ----------------------- Utilities -------------------------
def command_exists(name):
    try:
        result = subprocess.run(["which", name], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def run_command(command, timeout=180):
    result = subprocess.run(
        [str(x) for x in command], capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "Unknown error")[-3000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {details}")
    return result


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def probe_duration(path):
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=25)
    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return None


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("AI response did not contain valid JSON.")


def report_error(job_id, error):
    log.exception("Job %s failed: %s", job_id, error)
    update_job(job_id, status="failed", stage="failed", error=str(error)[:2500])


# ----------------------- Groq ------------------------------
def groq_wait_seconds(response, attempt):
    message = ""
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except Exception:
        message = response.text or ""
    wait = None
    for pattern in (r"try again in\s+([0-9.]+)\s*s",
                    r"retry after\s+([0-9.]+)",
                    r"wait\s+([0-9.]+)\s*s"):
        match = re.search(pattern, message, re.I)
        if match:
            try:
                wait = float(match.group(1))
                break
            except ValueError:
                pass
    try:
        header_wait = float(response.headers.get("retry-after", ""))
        wait = max(wait or 0, header_wait)
    except ValueError:
        pass
    if wait is None:
        wait = min(8 * (2 ** attempt), GROQ_MAX_WAIT)
    # Never silently shorten a server-requested wait.
    return max(1, min(wait + 1.5, GROQ_MAX_WAIT))


def groq_chat(messages, max_tokens=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")
    last_error = "unknown error"
    for attempt in range(max(1, GROQ_RETRIES)):
        try:
            response = HTTP.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.25,
                    "max_tokens": max_tokens or GROQ_MAX_TOKENS,
                },
                timeout=(20, 120),
            )
            if response.status_code == 429:
                last_error = f"HTTP 429: {response.text[:900]}"
                wait = groq_wait_seconds(response, attempt)
                log.warning("Groq rate limited; attempt %s/%s, waiting %.1fs",
                            attempt + 1, GROQ_RETRIES, wait)
                if attempt < GROQ_RETRIES - 1:
                    time.sleep(wait + random.uniform(0.2, 0.8))
                    continue
                break
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}: {response.text[:1200]}"
                if response.status_code in (400, 401, 403, 404):
                    raise RuntimeError(f"Groq API {last_error}")
                if attempt < GROQ_RETRIES - 1:
                    time.sleep(min(4 * (attempt + 1), 12))
                    continue
                break
            payload = response.json()
            choices = payload.get("choices") or []
            if not choices:
                raise RuntimeError("Groq returned no choices.")
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
            if not str(content).strip():
                raise RuntimeError("Groq returned empty content.")
            return str(content)
        except requests.RequestException as exc:
            last_error = f"Groq connection error: {exc}"
            if attempt < GROQ_RETRIES - 1:
                time.sleep(min(4 * (attempt + 1), 12))
    raise RuntimeError(f"Groq request failed after retries. Last error: {last_error}")


# ----------------------- Story -----------------------------
def create_story_package(idea):
    system_prompt = f"""
أنت كاتب سيناريو محترف للفانتازيا والتشويق. أخرج JSON صالحًا فقط.
أنشئ قصة عربية من {SCENE_COUNT} مشهدًا بالضبط، مدة كل مشهد خمس ثوانٍ.
لكل مشهد الحقول:
narration: جملة عربية قصيرة قابلة للإلقاء خلال 5 ثوانٍ.
visual_prompt: وصف بصري بالإنجليزية يصلح للبحث عن فيديو واقعي.
search_query: كلمات إنجليزية واضحة للبحث في مكتبة فيديوهات.
effect: واحد فقط من القائمة:
mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger
حافظ على تسلسل الأحداث، ونهاية مشوقة معلقة، ولا تضف أسماء كثيرة.
مجموع السرد من 80 إلى 230 كلمة. اجعل الجمل قصيرة حتى لا تُقطع عند تركيب الصوت.
أخرج المفاتيح title و story و scenes فقط. لا تضع Markdown.
شكل كل مشهد:
{{"narration":"...", "visual_prompt":"...", "search_query":"...", "effect":"mystery"}}
"""
    user_prompt = f"فكرة القصة:\n{str(idea)[:MAX_STORY_LENGTH]}\nأعد JSON مع {SCENE_COUNT} مشهدًا."
    last_error = None
    for validation_attempt in range(2):
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}]
        if validation_attempt:
            messages.append({"role": "user", "content":
                f"أعد JSON فقط، بالضبط {SCENE_COUNT} مشهدًا. اجعل كل narration قصيرة جدًا."})
        raw = groq_chat(messages)
        try:
            data = extract_json(raw)
            scenes = data.get("scenes") if isinstance(data, dict) else None
            if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
                raise ValueError(f"Expected {SCENE_COUNT} scenes.")
            clean = []
            for i, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    raise ValueError(f"Scene {i+1} is invalid.")
                narration = re.sub(r"\s+", " ", str(scene.get("narration", ""))).strip()
                visual = re.sub(r"\s+", " ", str(scene.get("visual_prompt", ""))).strip()
                query = re.sub(r"[^A-Za-z0-9 ,'-]", "", str(scene.get("search_query", ""))).strip()
                effect = str(scene.get("effect", "mystery")).lower().strip()
                if not narration:
                    raise ValueError(f"Scene {i+1} has no narration.")
                if not visual:
                    visual = "cinematic fantasy scene, realistic live action"
                if len(query.split()) < 3:
                    query = " ".join(re.findall(r"[A-Za-z0-9]+", visual)[:8])
                if len(query.split()) < 3:
                    query = "cinematic fantasy dramatic scene"
                if effect not in EFFECTS:
                    effect = "mystery"
                clean.append({"narration": narration[:180],
                              "visual_prompt": visual[:350],
                              "search_query": query[:120],
                              "effect": effect})
            total = word_count(" ".join(x["narration"] for x in clean))
            if not 65 <= total <= 230:
                raise ValueError(f"Narration word count unsuitable: {total}.")
            return {
                "title": str(data.get("title", "حكاية ظل"))[:100],
                "story": str(data.get("story", idea))[:2000],
                "scenes": clean,
                "narration": " ".join(x["narration"] for x in clean),
            }
        except Exception as exc:
            last_error = exc
            log.warning("Story validation %s failed: %s", validation_attempt + 1, exc)
    raise RuntimeError(f"Could not validate story JSON: {last_error}")


# ----------------------- Pixabay --------------------------
def search_pixabay_video(query):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing.")
    response = HTTP.get(
        PIXABAY_API_URL,
        params={"key": PIXABAY_API_KEY, "q": query, "per_page": 10, "safesearch": "true"},
        timeout=(15, 35),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Pixabay HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    for hit in data.get("hits", []):
        videos = hit.get("videos", {})
        for quality in ("medium", "large", "small", "tiny"):
            url = videos.get(quality, {}).get("url", "")
            if url and urlparse(url).scheme == "https":
                return url
    raise RuntimeError(f"No Pixabay video found for query: {query}")


def download_video(url, destination):
    total = 0
    with HTTP.get(url, stream=True, timeout=(20, 90)) as response:
        response.raise_for_status()
        with open(destination, "wb") as output:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 150 * 1024 * 1024:
                    raise RuntimeError("Downloaded source video exceeds 150 MB.")
                output.write(chunk)
    if total < 10000:
        raise RuntimeError("Downloaded video is too small.")
    return destination


# ----------------------- Video and subtitles --------------
def normalize_scene(source, destination):
    vf = (f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
          f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps={VIDEO_FPS},format=yuv420p")
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(source), "-vf", vf, "-t", str(SCENE_DURATION), "-an",
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                 "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 180)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError("Scene normalization failed.")
    return destination


def add_arabic_caption(source, caption, destination, workdir, index):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning("Subtitle dependencies missing; continuing without captions.")
        return source
    try:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        font = None
        for fp in font_candidates:
            if Path(fp).exists():
                font = ImageFont.truetype(fp, 28)
                break
        if font is None:
            font = ImageFont.load_default()
        overlay = Image.new("RGBA", (VIDEO_WIDTH, 150), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        words = (caption or "").split()
        lines, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            shaped = get_display(arabic_reshaper.reshape(candidate))
            if current and draw.textbbox((0, 0), shaped, font=font)[2] > VIDEO_WIDTH - 70:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        lines = lines[:2]
        draw.rounded_rectangle((10, 8, VIDEO_WIDTH - 10, 140), radius=15, fill=(0, 0, 0, 170))
        for i, line in enumerate(lines):
            shaped = get_display(arabic_reshaper.reshape(line))
            box = draw.textbbox((0, 0), shaped, font=font)
            x = max(10, (VIDEO_WIDTH - (box[2] - box[0])) // 2)
            draw.text((x, 28 + i * 45), shaped, font=font,
                      fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
        png = workdir / f"caption_{index}.png"
        overlay.save(png)
        run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(source), "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(png),
                     "-filter_complex", "[0:v][1:v]overlay=0:H-150:format=auto[v]",
                     "-map", "[v]", "-t", str(SCENE_DURATION), "-an",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
                     "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS), str(destination)], 120)
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Subtitle render failed for scene %s", index)
    return source


# ----------------------- Voice and sound -------------------
def create_narration(text, destination):
    destination.unlink(missing_ok=True)
    try:
        import edge_tts
        async def generate():
            await edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE).save(str(destination))
        asyncio.run(generate())
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("Edge TTS failed.")
    try:
        from gtts import gTTS
        gTTS(text=text, lang="ar", slow=False).save(str(destination))
        if destination.exists() and destination.stat().st_size > 1000:
            return destination
    except Exception:
        log.exception("gTTS fallback failed.")
    raise RuntimeError("Arabic TTS failed for this scene.")


def fit_audio_to_scene(source, destination):
    duration = probe_duration(source)
    if not duration or duration <= 0:
        raise RuntimeError("Could not determine narration duration.")
    # Avoid aggressive speech compression. If too long, slow TTS rate is not enough:
    # fit gently up to 1.35x, then trim only as a last resort.
    speed = min(1.35, max(1.0, duration / 4.85))
    filters = []
    if speed > 1.01:
        filters.append(f"atempo={speed:.4f}")
    filters += [f"apad=pad_dur={SCENE_DURATION}", f"atrim=duration={SCENE_DURATION}", "asetpts=PTS-STARTPTS"]
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(source), "-af", ",".join(filters),
                 "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "96k",
                 str(destination)], 90)
    return destination


def make_scene_sfx(kind, destination):
    # Synthetic fallback effects, not recordings of real animals or impacts.
    if kind == "beast":
        source = f"anoisesrc=color=brown:sample_rate=44100:duration={SCENE_DURATION}"
        af = "highpass=f=45,lowpass=f=850,tremolo=f=5:d=0.75,volume=0.22"
    elif kind in ("fight", "impact"):
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={SCENE_DURATION}"
        af = "highpass=f=50,lowpass=f=1600,volume=0.25,afade=t=in:d=0.02,afade=t=out:st=0.65:d=0.8"
    elif kind == "magic":
        source = f"sine=frequency=95:sample_rate=44100:duration={SCENE_DURATION}"
        af = "tremolo=f=3:d=0.75,volume=0.12,afade=t=in:d=0.7,afade=t=out:st=3.5:d=1.2"
    elif kind == "storm":
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={SCENE_DURATION}"
        af = "lowpass=f=420,volume=0.10,tremolo=f=0.25:d=0.4"
    elif kind in ("tension", "mystery", "cliffhanger"):
        source = f"sine=frequency=72:sample_rate=44100:duration={SCENE_DURATION}"
        af = "tremolo=f=2:d=0.55,lowpass=f=500,volume=0.08"
    elif kind == "romance":
        source = f"sine=frequency=440:sample_rate=44100:duration={SCENE_DURATION}"
        af = "lowpass=f=1200,volume=0.025"
    else:
        source = f"anoisesrc=color=pink:sample_rate=44100:duration={SCENE_DURATION}"
        af = "lowpass=f=1000,volume=0.045"
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", source, "-af", af, "-ar", "44100",
                 "-ac", "2", "-c:a", "aac", "-b:a", "64k", str(destination)], 60)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Failed to create sound effect: {kind}")
    return destination


def make_background_music(destination):
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i",
                 f"sine=frequency=55:sample_rate=44100:duration={VIDEO_DURATION}",
                 "-af", f"volume=0.025,tremolo=f=0.18:d=0.25,afade=t=in:d=2,afade=t=out:st={VIDEO_DURATION-4}:d=4",
                 "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "64k",
                 str(destination)], 90)
    return destination


# ----------------------- Concatenation and mix -------------
def write_concat_list(paths, destination):
    with open(destination, "w", encoding="utf-8") as f:
        for path in paths:
            resolved = Path(path).resolve().as_posix()
            if "'" in resolved:
                raise RuntimeError("A media path contains an unsupported apostrophe.")
            f.write(f"file '{resolved}'\n")


def concatenate_video(paths, destination, workdir):
    listing = workdir / "video_list.txt"
    write_concat_list(paths, listing)
    # Re-encode to make clips with differing source codecs/resolutions concat safely.
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", str(listing),
                 "-t", str(VIDEO_DURATION), "-an", "-c:v", "libx264",
                 "-preset", "ultrafast", "-crf", "27", "-pix_fmt", "yuv420p",
                 "-r", str(VIDEO_FPS), str(destination)], 300)
    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Video concatenation failed.")
    return destination


def concatenate_audio(paths, destination, workdir, label):
    listing = workdir / f"{label}_list.txt"
    write_concat_list(paths, listing)
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", str(listing),
                 "-t", str(VIDEO_DURATION), "-vn", "-ar", "44100", "-ac", "2",
                 "-c:a", "aac", "-b:a", "96k", str(destination)], 240)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise RuntimeError(f"Audio concatenation failed: {label}")
    return destination


def has_audio_stream(path):
    result = run_command(["ffprobe", "-v", "error", "-select_streams", "a:0",
                          "-show_entries", "stream=codec_type",
                          "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 25)
    return "audio" in result.stdout.lower()


def mix_final_audio(video, voice, sfx, music, destination):
    for label, path in {"video": video, "voice": voice, "sfx": sfx, "music": music}.items():
        if not Path(path).exists() or Path(path).stat().st_size < 1000:
            raise RuntimeError(f"Missing or empty mix input: {label}")
    for label, path in (("voice", voice), ("sfx", sfx), ("music", music)):
        if not has_audio_stream(path):
            raise RuntimeError(f"No audio stream detected in {label}.")
    d = VIDEO_DURATION
    graph = (
        f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume=1.0,apad=whole_dur={d},atrim=duration={d},asetpts=PTS-STARTPTS[v];"
        f"[2:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume=0.24,apad=whole_dur={d},atrim=duration={d},asetpts=PTS-STARTPTS[s];"
        f"[3:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume=0.12,apad=whole_dur={d},atrim=duration={d},asetpts=PTS-STARTPTS[m];"
        "[v][s][m]amix=inputs=3:duration=longest:dropout_transition=2:normalize=0,"
        "alimiter=limit=0.92,"
        f"atrim=duration={d},asetpts=PTS-STARTPTS[aout]"
    )
    run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(video), "-i", str(voice), "-i", str(sfx), "-i", str(music),
                 "-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]",
                 "-t", str(d), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", str(destination)], 300)
    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Final video file is missing or too small.")
    duration = probe_duration(destination)
    if duration is None or abs(duration - d) > 0.5:
        raise RuntimeError(f"Final duration check failed: {duration}")
    return destination


# ----------------------- Telegram bridge ------------------
def send_coroutine(application, coroutine, timeout=360):
    loop = application.bot_data.get("event_loop")
    if loop is None or not loop.is_running():
        coroutine.close()
        raise RuntimeError("Telegram event loop is unavailable.")
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return future.result(timeout=timeout)


async def send_text(application, chat_id, text):
    await application.bot.send_message(chat_id=chat_id, text=text[:3500])


async def send_video(application, chat_id, path, job_id):
    with open(path, "rb") as f:
        await application.bot.send_video(
            chat_id=chat_id, video=f,
            caption=f"اكتمل فيديو ظل ZIL.\nرقم العملية: {job_id}",
            supports_streaming=True, read_timeout=240, write_timeout=240, connect_timeout=30,
        )


# ----------------------- Job pipeline ----------------------
def build_video(job_id, chat_id, idea, telegram_app):
    global ACTIVE_JOBS
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        update_job(job_id, status="running", stage="checking", progress=1)
        if not command_exists("ffmpeg") or not command_exists("ffprobe"):
            raise RuntimeError("FFmpeg or FFprobe is missing.")
        if not PIXABAY_API_KEY:
            raise RuntimeError("PIXABAY_API_KEY is missing.")
        update_job(job_id, stage="writing_story", progress=2)
        package = create_story_package(idea)
        scenes = package["scenes"]
        update_job(job_id, stage="story_ready", progress=5, title=package["title"])
        try:
            send_coroutine(telegram_app, send_text(
                telegram_app, chat_id,
                f"اكتملت كتابة القصة: {package['title']}\n"
                "بدأ تجهيز 18 مشهدًا والراوي العربي والترجمة والمؤثرات."
            ), timeout=45)
        except Exception:
            log.exception("Could not send story-ready message.")

        video_scenes, voice_scenes, sfx_scenes = [], [], []
        for index, scene in enumerate(scenes):
            n = index + 1
            update_job(job_id, stage=f"scene_{n}_search_download", progress=round(5 + index / SCENE_COUNT * 78))
            try:
                url = search_pixabay_video(scene["search_query"])
            except Exception as first_error:
                log.warning("Scene %s primary search failed: %s", n, first_error)
                fallback = " ".join(re.findall(r"[A-Za-z0-9]+", scene["visual_prompt"])[:8])
                if len(fallback.split()) < 3:
                    fallback = "cinematic fantasy dramatic scene"
                url = search_pixabay_video(fallback)

            raw = workdir / f"raw_{n}.mp4"
            normalized = workdir / f"normalized_{n}.mp4"
            captioned = workdir / f"captioned_{n}.mp4"
            download_video(url, raw)
            normalize_scene(raw, normalized)
            caption_result = add_arabic_caption(normalized, scene["narration"], captioned, workdir, n)
            video_scenes.append(Path(caption_result) if Path(caption_result).exists() else normalized)
            raw.unlink(missing_ok=True)

            update_job(job_id, stage=f"scene_{n}_voice", progress=round(5 + index / SCENE_COUNT * 78 + 1))
            raw_voice = workdir / f"voice_raw_{n}.mp3"
            fitted = workdir / f"voice_{n}.m4a"
            create_narration(scene["narration"], raw_voice)
            fit_audio_to_scene(raw_voice, fitted)
            voice_scenes.append(fitted)

            sfx = workdir / f"sfx_{n}.m4a"
            make_scene_sfx(scene["effect"], sfx)
            sfx_scenes.append(sfx)

        update_job(job_id, stage="concatenating", progress=85)
        silent = workdir / "silent_video.mp4"
        voice_track = workdir / "voice_track.m4a"
        sfx_track = workdir / "sfx_track.m4a"
        music_track = workdir / "music_track.m4a"
        final = workdir / "ZIL_video.mp4"
        concatenate_video(video_scenes, silent, workdir)
        concatenate_audio(voice_scenes, voice_track, workdir, "voice")
        concatenate_audio(sfx_scenes, sfx_track, workdir, "sfx")
        make_background_music(music_track)
        update_job(job_id, stage="mixing_audio", progress=94)
        mix_final_audio(silent, voice_track, sfx_track, music_track, final)

        size = final.stat().st_size
        if size > MAX_VIDEO_SIZE:
            # Try a smaller video encode instead of failing immediately.
            compact = workdir / "ZIL_video_compact.mp4"
            run_command(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                         "-i", str(final), "-c:v", "libx264", "-preset", "ultrafast",
                         "-crf", "31", "-vf", "scale=540:960:force_original_aspect_ratio=decrease,"
                         "pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1",
                         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(compact)], 300)
            if compact.exists() and compact.stat().st_size < size:
                final = compact
            if final.stat().st_size > MAX_VIDEO_SIZE:
                raise RuntimeError(f"Final video is {final.stat().st_size // (1024*1024)} MB, above Telegram upload limit.")

        update_job(job_id, stage="sending", progress=98)
        send_coroutine(telegram_app, send_video(telegram_app, chat_id, final, job_id), timeout=420)
        update_job(job_id, status="completed", stage="completed", progress=100,
                   duration=probe_duration(final), output_size=final.stat().st_size)
        log.info("Job %s completed.", job_id)
    except Exception as error:
        report_error(job_id, error)
        try:
            send_coroutine(telegram_app, send_text(
                telegram_app, chat_id,
                f"تعذر إكمال الفيديو.\nرقم العملية: {job_id}\n"
                "تم حفظ الخطأ. أرسل /last_error أو /status للتشخيص."
            ), timeout=45)
        except Exception:
            log.exception("Could not notify user of failure.")
    finally:
        with JOBS_LOCK:
            ACTIVE_JOBS = max(0, ACTIVE_JOBS - 1)


# ----------------------- Commands --------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "أهلًا بك في ظل ZIL.\n\n"
            "/test - إنشاء فيديو تجريبي 90 ثانية\n"
            "/make فكرة القصة - إنشاء فيديو\n"
            "/status - حالة النظام والعمليات\n"
            "/diagnose - فحص الخدمات\n"
            "/last_error - آخر خطأ محفوظ\n"
            "/health - فحص الخدمة"
        )


async def start_job(update, context, idea):
    global ACTIVE_JOBS
    if not update.message or not update.effective_chat:
        return
    idea = (idea or "").strip()[:MAX_STORY_LENGTH]
    if not idea:
        await update.message.reply_text("أرسل فكرة القصة أولًا.")
        return
    with JOBS_LOCK:
        if ACTIVE_JOBS >= MAX_ACTIVE_JOBS:
            job_id = None
        else:
            job_id = uuid.uuid4().hex[:10]
            JOBS[job_id] = {
                "id": job_id, "chat_id": update.effective_chat.id,
                "status": "queued", "stage": "queued",
                "created_at": time.time(), "updated_at": time.time(), "progress": 0,
            }
            ACTIVE_JOBS += 1
            save_state_locked()
    if job_id is None:
        await update.message.reply_text("يوجد فيديو قيد المعالجة. حاول لاحقًا.")
        return
    await update.message.reply_text(f"بدأت عملية إنشاء فيديو ظل ZIL.\nرقم العملية: {job_id}\nالمدة المستهدفة: 90 ثانية.")
    threading.Thread(target=build_video,
                     args=(job_id, update.effective_chat.id, idea, context.application),
                     daemon=True).start()


async def test_command(update, context):
    idea = ("في مملكة غامضة، يصل رجل يخفي قوة خارقة. تقع الأميرة في حبه، "
            "لكن الملك يرفض العلاقة. يظهر نمر عملاق أمام القصر ويهاجم الحراس. "
            "يواجهه الرجل ويكشف جزءًا من قوته المخفية، ثم يظهر سر جديد يهدد المملكة.")
    await start_job(update, context, idea)


async def make_command(update, context):
    idea = " ".join(context.args).strip()
    if not idea:
        await update.message.reply_text("مثال:\n/make رجل غامض يصل إلى مملكة ويخفي قوة أسطورية")
        return
    await start_job(update, context, idea)


async def status_command(update, context):
    with JOBS_LOCK:
        active = ACTIVE_JOBS
        jobs = list(JOBS.values())[-5:]
    lines = [
        "حالة ظل ZIL",
        f"العمليات النشطة: {active}",
        f"Groq: {'مهيأ' if GROQ_API_KEY else 'مفتاح مفقود'}",
        f"Pixabay: {'مهيأ' if PIXABAY_API_KEY else 'مفتاح مفقود'}",
        f"FFmpeg: {'جاهز' if command_exists('ffmpeg') else 'غير موجود'}",
    ]
    for job in reversed(jobs):
        lines.append(f"\n{job.get('id')} | {job.get('status')} | {job.get('stage')} | {job.get('progress', 0)}%")
        if job.get("error"):
            lines.append(str(job["error"])[:450])
    await update.message.reply_text("\n".join(lines)[:3900])


async def last_error_command(update, context):
    with JOBS_LOCK:
        failed = [j for j in JOBS.values() if j.get("error")]
    if not failed:
        await update.message.reply_text("لا يوجد خطأ محفوظ. إذا أعيد تشغيل Render، افحص سجلات الخدمة أيضًا.")
        return
    job = failed[-1]
    await update.message.reply_text(
        f"آخر خطأ في ظل ZIL\nالعملية: {job.get('id')}\n"
        f"المرحلة: {job.get('stage')}\nالخطأ:\n{str(job.get('error', 'غير معروف'))[:2500]}"
    )


async def diagnose_command(update, context):
    results = [
        f"Python: {sys.version.split()[0]}",
        f"Groq key: {'OK' if GROQ_API_KEY else 'MISSING'}",
        f"Pixabay key: {'OK' if PIXABAY_API_KEY else 'MISSING'}",
        f"Groq model: {GROQ_MODEL}",
        f"Groq max tokens: {GROQ_MAX_TOKENS}",
        f"Groq retries: {GROQ_RETRIES}",
        f"FFmpeg: {command_exists('ffmpeg')}",
        f"FFprobe: {command_exists('ffprobe')}",
        f"Target duration: {VIDEO_DURATION}s",
        f"Data directory: {BASE_DIR}",
        f"Persistent state file exists: {STATE_FILE.exists()}",
    ]
    await update.message.reply_text("تشخيص ظل ZIL\n\n" + "\n".join(results))


async def health_command(update, context):
    await update.message.reply_text(
        f"ظل ZIL يعمل.\nFFmpeg: {command_exists('ffmpeg')}\n"
        f"FFprobe: {command_exists('ffprobe')}\n"
        f"Groq configured: {bool(GROQ_API_KEY)}\n"
        f"Pixabay configured: {bool(PIXABAY_API_KEY)}\n"
        f"Target duration: {VIDEO_DURATION} seconds"
    )


async def telegram_error_handler(update, context):
    error = context.error
    if error:
        log.error("TELEGRAM HANDLER ERROR", exc_info=(type(error), error, error.__traceback__))
    else:
        log.error("Telegram handler reported an unknown error.")


# ----------------------- Flask health ----------------------
@app.get("/")
def home():
    return jsonify({"service": "ZIL", "status": "running", "target_duration_seconds": VIDEO_DURATION})


@app.get("/health")
def health_endpoint():
    return jsonify({
        "status": "ok", "ffmpeg": command_exists("ffmpeg"),
        "ffprobe": command_exists("ffprobe"), "groq_configured": bool(GROQ_API_KEY),
        "pixabay_configured": bool(PIXABAY_API_KEY), "active_jobs": get_active_jobs(),
    })


def run_web():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ----------------------- Startup --------------------------
def main():
    load_state()
    log.info("Starting ZIL bot.")
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN is missing.")
        sys.exit(1)
    if not command_exists("ffmpeg") or not command_exists("ffprobe"):
        log.critical("FFmpeg or FFprobe is missing.")
        sys.exit(1)
    threading.Thread(target=run_web, daemon=True).start()

    async def post_init(application):
        application.bot_data["event_loop"] = asyncio.get_running_loop()

    telegram_app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_handler(CommandHandler("make", make_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("diagnose", diagnose_command))
    telegram_app.add_handler(CommandHandler("last_error", last_error_command))
    telegram_app.add_handler(CommandHandler("health", health_command))
    telegram_app.add_error_handler(telegram_error_handler)
    log.info("Telegram polling starting.")
    telegram_app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
