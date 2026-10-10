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
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import edge_tts

# =========================================================
# CONFIG
# =========================================================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PEXELS_KEY = (os.getenv("PEXELS_KEY") or "").strip()
PORT = int(os.getenv("PORT", "10000"))
SCENES = 6
SCENE_SECONDS = 5
TOTAL_SECONDS = SCENES * SCENE_SECONDS
WIDTH, HEIGHT, FPS = 720, 1280, 25
VOICE = (os.getenv("TTS_VOICE") or "ar-SA-HamedNeural").strip()
VOICE_RATE = (os.getenv("TTS_RATE") or "-8%").strip()
MAX_STORY_LENGTH = 5000
MAX_DOWNLOAD_BYTES = 35 * 1024 * 1024
PEXELS_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 45
BASE_DIR = Path("/tmp/zil")
BASE_DIR.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("zil")
flask_app = Flask(__name__)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ZIL-VideoBot/1.0"})
busy_users = set()
busy_lock = threading.Lock()

ARABIC_TO_ENGLISH = {
    "قصر": "royal castle", "ملك": "king royal palace", "امير": "medieval prince",
    "أمير": "medieval prince", "اميرة": "princess castle", "أميرة": "princess castle",
    "حب": "romantic couple", "غابة": "dark forest", "ليل": "night cinematic",
    "قتال": "martial arts fight", "معركة": "battle cinematic", "نمر": "tiger wildlife",
    "أسد": "lion wildlife", "اسد": "lion wildlife", "ذئب": "wolf wildlife",
    "وحش": "monster fantasy", "سيف": "sword medieval", "مطر": "rain storm",
    "بحر": "ocean waves", "جبل": "mountain landscape", "مدينة": "city night",
    "سيارة": "sports car cinematic", "فضاء": "space stars", "سر": "mysterious person",
    "غامض": "mysterious man", "قوة": "dramatic action", "خطر": "dark dramatic scene",
    "سحر": "fantasy magic", "طيران": "aerial cinematic", "سقوط": "dramatic fall",
}

# =========================================================
# HELPERS
# =========================================================
def check_binary(name):
    return shutil.which(name) is not None


def run_command(command, timeout=120):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        # FFmpeg diagnostics are useful, but do not print credentials/URLs.
        details = (result.stderr or "")[-1200:]
        raise RuntimeError(f"Command failed ({result.returncode}): {details}")
    return result


def validate_environment():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    for binary in ("ffmpeg", "ffprobe"):
        if not check_binary(binary):
            missing.append(binary)
    if missing:
        raise RuntimeError("Missing required configuration/tools: " + ", ".join(missing))


def clean_story(text):
    text = (text or "").strip()
    text = re.sub(r"^/(?:zil|test|ظل)(?:@\w+)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_STORY_LENGTH]


def split_sentences(text):
    text = clean_story(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!؟?。])\s+", text)
    return [p.strip(" \t،,;؛") for p in parts if p.strip(" \t،,;؛")]


def make_scene_texts(story):
    """Split the story into six consecutive chunks, preserving story order."""
    sentences = split_sentences(story)
    if not sentences:
        raise ValueError("اكتب القصة بعد الأمر.")

    # When there are enough sentences, group consecutive sentences evenly.
    if len(sentences) >= SCENES:
        result = []
        for i in range(SCENES):
            start = (i * len(sentences)) // SCENES
            end = ((i + 1) * len(sentences)) // SCENES
            result.append(" ".join(sentences[start:end]).strip())
        return result

    # Shorter stories are split by words in their original order, without repeats.
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
    defaults = ["cinematic dramatic landscape", "mysterious man cinematic", "dark forest cinematic", "royal castle cinematic", "dramatic storm landscape", "cinematic night city"]
    return defaults[index % len(defaults)]

# =========================================================
# PEXELS
# =========================================================
def pexels_search(query):
    if not PEXELS_KEY:
        raise RuntimeError("PEXELS_KEY غير موجود في إعدادات Render.")
    response = SESSION.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "orientation": "portrait", "size": "small", "per_page": 10},
        timeout=PEXELS_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Pexels search failed: HTTP {response.status_code}")
    payload = response.json()
    candidates = []
    for video in payload.get("videos", []):
        duration = video.get("duration") or 0
        if duration and duration < 3:
            continue
        for item in video.get("video_files", []):
            link = item.get("link")
            if not link:
                continue
            parsed = urlparse(link)
            if parsed.scheme != "https" or not parsed.hostname:
                continue
            w, h = item.get("width") or 0, item.get("height") or 0
            # Prefer vertical videos, then resolution.
            score = (100000 if h > w else 0) + min(w, 1080) + min(h, 1920)
            candidates.append((score, link))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def probe_video(path):
    result = run_command(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)], timeout=25)
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except (ValueError, TypeError):
        return False
    if not streams:
        return False
    s = streams[0]
    return s.get("codec_type") == "video" and int(s.get("width") or 0) >= 100 and int(s.get("height") or 0) >= 100


def download_clip(url, output_path):
    output_path = Path(output_path)
    temp_path = output_path.with_suffix(".part")
    try:
        with SESSION.get(url, stream=True, timeout=(15, DOWNLOAD_TIMEOUT), allow_redirects=True) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("Video file exceeds download size limit.")
            total = 0
            with open(temp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Video exceeded download size limit.")
                    handle.write(chunk)
        if temp_path.stat().st_size < 30000 or not probe_video(temp_path):
            raise RuntimeError("Downloaded file is not a valid video.")
        temp_path.replace(output_path)
        return output_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def obtain_clip(scene_text, index, workdir):
    queries = list(dict.fromkeys([search_terms(scene_text, index), "cinematic dramatic scene", "cinematic landscape"]))
    for query in queries:
        try:
            url = pexels_search(query)
            if not url:
                continue
            path = workdir / f"source_{index:02d}.mp4"
            download_clip(url, path)
            log.info("Scene %s source downloaded and validated", index + 1)
            return path
        except Exception as exc:
            log.warning("Scene %s query failed (%s): %s", index + 1, query, str(exc)[:180])
    raise RuntimeError(f"تعذر تنزيل فيديو صالح للمشهد {index + 1}. تحقق من PEXELS_KEY واتصال Render.")

# =========================================================
# ARABIC CAPTIONS
# =========================================================
def find_font():
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    raise RuntimeError("لم يتم العثور على خط عربي. ثبّت fonts-dejavu-core في Dockerfile.")


def shape_arabic(text):
    return get_display(arabic_reshaper.reshape(text or ""))


def wrap_text(draw, text, font, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        box = draw.textbbox((0, 0), shape_arabic(candidate), font=font)
        if box[2] - box[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def create_caption_image(text, output_path):
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_path = find_font()
    font_size = 40
    while font_size >= 24:
        font = ImageFont.truetype(font_path, font_size)
        lines = wrap_text(draw, text, font, WIDTH - 100)
        if len(lines) <= 3:
            break
        font_size -= 2
    if not lines:
        lines = [text]
    line_heights = []
    for line in lines:
        box = draw.textbbox((0, 0), shape_arabic(line), font=font, stroke_width=1)
        line_heights.append(max(1, box[3] - box[1]))
    spacing, pad_y = 12, 20
    panel_h = sum(line_heights) + spacing * (len(lines) - 1) + pad_y * 2
    panel = Image.new("RGBA", (WIDTH - 40, panel_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((0, 0, panel.width - 1, panel.height - 1), radius=22, fill=(0, 0, 0, 175), outline=(255, 255, 255, 50), width=2)
    y = pad_y
    for i, line in enumerate(lines):
        shaped = shape_arabic(line)
        box = pd.textbbox((0, 0), shaped, font=font, stroke_width=1)
        x = (panel.width - (box[2] - box[0])) // 2
        pd.text((x, y), shaped, font=font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 220))
        y += line_heights[i] + spacing
    top = max(55, HEIGHT - panel_h - 115)
    image.alpha_composite(panel, (20, top))
    image.save(output_path)
    return output_path

# =========================================================
# VIDEO SCENES
# =========================================================
def build_scene(source_path, caption_path, output_path):
    fc = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,"
        f"trim=duration={SCENE_SECONDS},setpts=PTS-STARTPTS[base];"
        f"[1:v]format=rgba[cap];[base][cap]overlay=0:0,format=yuv420p[out]"
    )
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-stream_loop", "-1", "-i", str(source_path),
        "-loop", "1", "-i", str(caption_path),
        "-filter_complex", fc, "-map", "[out]", "-an",
        "-t", str(SCENE_SECONDS), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)
    ], timeout=150)
    if not output_path.exists() or output_path.stat().st_size < 30000 or not probe_video(output_path):
        raise RuntimeError("تعذر تجهيز أحد مشاهد الفيديو.")
    return output_path


def concatenate_scenes(scene_paths, workdir):
    concat_file = workdir / "scenes.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in scene_paths:
            f.write(f"file '{str(p)}'\n")
    output = workdir / "video_only.mp4"
    run_command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-an", "-t", str(TOTAL_SECONDS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", str(output)], timeout=180)
    return output

# =========================================================
# NARRATION AND BACKGROUND AUDIO
# =========================================================
async def edge_tts_to_file(text, output_path):
    await edge_tts.Communicate(text=text, voice=VOICE, rate=VOICE_RATE).save(str(output_path))


def make_voice(text, output_path):
    try:
        asyncio.run(edge_tts_to_file(text, output_path))
        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path
    except Exception as exc:
        log.warning("Edge TTS failed: %s", str(exc)[:160])
    try:
        from gtts import gTTS
        gTTS(text=text, lang="ar", slow=False).save(str(output_path))
        if output_path.exists() and output_path.stat().st_size > 1000:
            return output_path
    except Exception as exc:
        log.warning("gTTS fallback failed: %s", str(exc)[:160])
    raise RuntimeError("تعذّر إنشاء الراوي العربي. تحقق من اتصال Render ومكتبات الصوت.")


def create_narration(story, workdir):
    """Render the whole story once to avoid cutting each sentence at 5 seconds."""
    raw = workdir / "narration_raw.mp3"
    output = workdir / "narration.m4a"
    make_voice(story, raw)
    # Fit narration into 30 seconds without speeding it up excessively.
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
        "-vn", "-af", "aresample=44100,apad,atrim=0:30,loudnorm=I=-18:TP=-2:LRA=7",
        "-t", str(TOTAL_SECONDS), "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "128k", str(output)
    ], timeout=120)
    return output


def create_ambient_audio(workdir):
    output = workdir / "ambient.m4a"
    # Quiet synthesized atmospheric tones; not a commercial music track.
    fc = "[0:a]volume=0.025[a0];[1:a]volume=0.018[a1];[2:a]volume=0.012[a2];[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0,lowpass=f=900,afade=t=in:st=0:d=2,afade=t=out:st=27:d=3,volume=0.7[m]"
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=164.81:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={TOTAL_SECONDS}",
        "-filter_complex", fc, "-map", "[m]", "-t", str(TOTAL_SECONDS),
        "-ac", "2", "-ar", "44100", "-c:a", "aac", "-b:a", "96k", str(output)
    ], timeout=90)
    return output


def mux_final_video(video_path, narration_path, ambient_path, output_path):
    fc = "[0:a]volume=1.0[v];[1:a]volume=0.22[b];[v][b]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(narration_path), "-i", str(ambient_path), "-i", str(video_path),
        "-filter_complex", fc, "-map", "2:v:0", "-map", "[a]",
        "-t", str(TOTAL_SECONDS), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output_path)
    ], timeout=150)
    if not output_path.exists() or output_path.stat().st_size < 100000 or not probe_video(output_path):
        raise RuntimeError("التحقق من الفيديو النهائي فشل.")
    return output_path


def generate_video(story, workdir):
    if not PEXELS_KEY:
        raise RuntimeError("PEXELS_KEY غير موجود في إعدادات Render.")
    captions = make_scene_texts(story)
    scene_paths = []
    for i, caption in enumerate(captions):
        log.info("Rendering scene %s/%s", i + 1, SCENES)
        source = obtain_clip(caption or story, i, workdir)
        cap = workdir / f"caption_{i:02d}.png"
        scene = workdir / f"scene_{i:02d}.mp4"
        create_caption_image(caption or "", cap)
        build_scene(source, cap, scene)
        scene_paths.append(scene)
    video = concatenate_scenes(scene_paths, workdir)
    voice = create_narration(story, workdir)
    ambient = create_ambient_audio(workdir)
    final = workdir / "zil_final.mp4"
    return mux_final_video(video, voice, ambient, final)

# =========================================================
# TELEGRAM HANDLERS
# =========================================================
async def reply(update, text):
    if update.effective_message:
        await update.effective_message.reply_text(text)


async def send_video(update, path):
    if update.effective_message:
        with open(path, "rb") as f:
            await update.effective_message.reply_video(video=f, caption="تم إنشاء فيديو ظل.", supports_streaming=True, read_timeout=120, write_timeout=120, connect_timeout=30)


async def generate_for_user(update, story):
    user = update.effective_user
    if not user:
        return
    uid = user.id
    with busy_lock:
        if uid in busy_users:
            await reply(update, "طلبك السابق ما زال قيد التنفيذ. انتظر حتى ينتهي.")
            return
        busy_users.add(uid)
    job_id = uuid.uuid4().hex[:10]
    workdir = BASE_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        story = clean_story(story)
        if len(story) < 10:
            await reply(update, "اكتب قصة أطول بعد الأمر، مثال:\n/zil بطل غامض يصل إلى القصر وينقذ الأميرة.")
            return
        await reply(update, "بدأت صناعة فيديو ظل: 6 مشاهد، 30 ثانية، مقاطع Pexels، نص عربي وراوي عربي. قد تستغرق العملية عدة دقائق.")
        loop = asyncio.get_running_loop()
        final_path = await loop.run_in_executor(None, generate_video, story, workdir)
        await send_video(update, final_path)
    except Exception as exc:
        log.exception("Generation failed. job=%s", job_id)
        msg = str(exc)
        if "PEXELS_KEY" in msg:
            friendly = "فشل إنشاء الفيديو: مفتاح PEXELS_KEY غير موجود في Render."
        elif "Pexels" in msg or "تنزيل" in msg or "مشهد" in msg:
            friendly = "تعذر الحصول على مقاطع فيديو صالحة. تحقق من صلاحية PEXELS_KEY ثم جرّب مجددًا."
        elif "الراوي" in msg or "TTS" in msg:
            friendly = "تعذر إنشاء الراوي العربي. تحقق من اتصال Render."
        else:
            friendly = f"حدث خطأ أثناء إنشاء الفيديو. رقم العملية: {job_id}. راجع Render Logs."
        await reply(update, friendly)
    finally:
        with busy_lock:
            busy_users.discard(uid)
        shutil.rmtree(workdir, ignore_errors=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, "أهلًا بك في ظل ZIL.\n\nأرسل قصتك مباشرة، أو استخدم:\n/zil قصتك هنا\n\nالأوامر:\n/start - تشغيل البوت\n/status - حالة الخدمة\n/test - فيديو تجريبي\n/zil - إنشاء فيديو من قصتك\n/ظل - الأمر العربي لإنشاء فيديو")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with busy_lock:
        active = len(busy_users)
    await reply(update, f"حالة ظل ZIL\n\nالبوت: {'مهيأ' if BOT_TOKEN else 'رمز البوت غير موجود'}\nPexels key: {'موجود' if PEXELS_KEY else 'غير موجود'}\nFFmpeg: {'جاهز' if check_binary('ffmpeg') else 'غير موجود'}\nFFprobe: {'جاهز' if check_binary('ffprobe') else 'غير موجود'}\nالمشاهد: {SCENES}\nالمدة: {TOTAL_SECONDS} ثانية\nالدقة: {WIDTH}x{HEIGHT}\nالطلبات النشطة: {active}\n\nملاحظة: وجود المفتاح لا يثبت صلاحيته؛ يتم اختباره عند التوليد.")


async def zil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Telegram command handler parses arguments into context.args.
    story = " ".join(context.args).strip()
    if not story and update.effective_message and update.effective_message.text:
        story = re.sub(r"^/\S+\s*", "", update.effective_message.text).strip()
    if not story:
        await reply(update, "اكتب القصة بعد الأمر، مثال:\n/zil بطل غامض يدخل القصر.")
        return
    await generate_for_user(update, story)


async def arabic_zil_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text if update.effective_message else ""
    story = re.sub(r"^/ظل(?:@\w+)?\s*", "", text or "").strip()
    if not story:
        await reply(update, "اكتب القصة بعد الأمر، مثال:\n/ظل بطل غامض يدخل القصر.")
        return
    await generate_for_user(update, story)


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = ("في مملكة بعيدة ظهر رجل غامض عند أبواب القصر. "
             "رفض الملك السماح له بالاقتراب من الأميرة. "
             "في الليل ظهر وحش ضخم قرب أسوار المملكة. "
             "تراجع الحراس أمام قوته الهائلة. "
             "وقف الرجل الغامض في طريق الوحش وكشف عن قوة غير متوقعة. "
             "لكن الملك لاحظ علامة قديمة على ذراعه وعرف سرًا خطيرًا.")
    await generate_for_user(update, story)


async def text_story_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.text:
        return
    text = update.effective_message.text.strip()
    if text.startswith("/"):
        return
    await generate_for_user(update, text)

# =========================================================
# FLASK HEALTH
# =========================================================
@flask_app.get("/")
def home():
    return jsonify({"service": "ZIL", "status": "online", "video_seconds": TOTAL_SECONDS, "scenes": SCENES})


@flask_app.get("/health")
def health():
    ok = bool(BOT_TOKEN) and check_binary("ffmpeg") and check_binary("ffprobe")
    return jsonify({"service": "ZIL", "status": "healthy" if ok else "degraded", "ffmpeg": check_binary("ffmpeg"), "ffprobe": check_binary("ffprobe"), "pexels_key_configured": bool(PEXELS_KEY), "video_seconds": TOTAL_SECONDS}), (200 if ok else 503)


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


def main():
    validate_environment()
    log.info("Starting ZIL Telegram bot")
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("test", test_command))
    telegram_app.add_handler(CommandHandler("zil", zil_command))
    # Arabic aliases are handled as text patterns, not CommandHandler names.
    telegram_app.add_handler(MessageHandler(filters.Regex(r"^/ظل(?:@\w+)?(?:\s|$)"), arabic_zil_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_story_handler))
    threading.Thread(target=run_flask, daemon=True, name="zil-flask").start()
    telegram_app.run_polling(drop_pending_updates=True, close_loop=True)


if __name__ == "__main__":
    main()
