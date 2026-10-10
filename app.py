import os
import re
import uuid
import shutil
import logging
import asyncio
import threading
import subprocess
from pathlib import Path

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL MICRO DRAMA V2
# Free-first: Telegram + Pexels + Edge TTS + FFmpeg
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("zil")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PEXELS_KEY = (os.getenv("PEXELS_KEY") or "").strip()
PORT = int(os.getenv("PORT", "10000"))

SCENES = 6
SECONDS = 5
WIDTH = 720
HEIGHT = 1280
FPS = 25

CAPTIONS_ENABLED = (
    os.getenv("CAPTIONS_ENABLED", "false").strip().lower() == "true"
)

MAX_CLIP_BYTES = 25_000_000
TMP = Path("/tmp/zil")
TMP.mkdir(parents=True, exist_ok=True)

busy = set()
busy_lock = threading.Lock()

flask_app = Flask(__name__)

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")


# =========================================================
# TELEGRAM
# =========================================================

def telegram_call(method, data=None, files=None, timeout=30):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=data,
        files=files,
        timeout=timeout
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result


def send_message(chat_id, text):
    try:
        telegram_call(
            "sendMessage",
            {"chat_id": chat_id, "text": text},
            timeout=20
        )
    except Exception:
        log.exception("Failed to send Telegram message")


# =========================================================
# FFMPEG
# =========================================================

def run_ffmpeg(args, timeout=90):
    process = subprocess.run(
        list(map(str, args)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg error: " + process.stderr[-900:]
        )

    return process


# =========================================================
# FONT
# =========================================================

def get_font():
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]

    for font in candidates:
        if font.exists() and font.stat().st_size > 10000:
            return str(font)

    cached = Path("/tmp/Amiri-Bold.ttf")

    if cached.exists() and cached.stat().st_size > 10000:
        return str(cached)

    try:
        url = (
            "https://raw.githubusercontent.com/google/fonts/"
            "main/ofl/amiri/Amiri-Bold.ttf"
        )

        response = requests.get(url, timeout=15)

        if response.status_code == 200 and len(response.content) > 10000:
            cached.write_bytes(response.content)
            return str(cached)

    except Exception:
        log.warning("Arabic font download unavailable")

    return None


# =========================================================
# STORY INPUT
# =========================================================

def parse_story(raw):
    raw = re.sub(
        r"^/(?:ظل|zil)(?:@\w+)?\s*",
        "",
        raw or "",
        flags=re.I
    ).strip()

    parts = [
        re.sub(r"\s+", " ", item).strip()
        for item in raw.split("|")
    ]

    parts = [item for item in parts if item]

    if len(parts) < 3:
        raise ValueError(
            "أرسل ثلاثة أجزاء للقصة وافصل بينها بعلامة |"
        )

    return parts[:3]


# =========================================================
# STORYBOARD
# =========================================================

def create_storyboard(parts):
    beginning, conflict, danger = parts

    return [
        {
            "type": "hook",
            "narration": f"في تلك الليلة، بدأ كل شيء عندما {beginning}.",
            "queries": [
                "mysterious man entering castle cinematic",
                "dark fantasy castle entrance",
                "medieval castle gate dramatic"
            ]
        },
        {
            "type": "reaction",
            "narration": (
                "لم يعرف أحد حقيقة هذا الرجل، "
                "لكن ظهوره غيّر كل شيء."
            ),
            "queries": [
                "mysterious man cinematic close up",
                "royal court dramatic scene",
                "medieval crowd reaction"
            ]
        },
        {
            "type": "conflict",
            "narration": f"ثم بدأت المواجهة: {conflict}.",
            "queries": [
                "angry king throne room cinematic",
                "princess dramatic portrait",
                "medieval royal confrontation"
            ]
        },
        {
            "type": "danger",
            "narration": f"وفجأة، ظهر الخطر: {danger}.",
            "queries": [
                "tiger roaring close up wildlife",
                "large predator dramatic wildlife",
                "dangerous wild animal running"
            ]
        },
        {
            "type": "action",
            "narration": (
                "هرب الجميع، لكن الرجل الغامض "
                "بقي واقفًا أمام الخطر."
            ),
            "queries": [
                "warrior standing against beast cinematic",
                "man running dramatic action",
                "fighter silhouette smoke cinematic"
            ]
        },
        {
            "type": "cliffhanger",
            "narration": (
                "وعندما ظنوا أن كل شيء انتهى، "
                "ظهرت علامة كشفت أن سره أخطر مما تخيلوا."
            ),
            "queries": [
                "mysterious man eyes cinematic close up",
                "dark fantasy glowing hands",
                "mysterious silhouette smoke cinematic"
            ]
        }
    ]


# =========================================================
# PEXELS SEARCH
# =========================================================

def search_pexels(queries, used_ids):
    if not PEXELS_KEY:
        return None

    for query in queries:
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                params={
                    "query": query,
                    "per_page": 12,
                    "orientation": "portrait"
                },
                headers={"Authorization": PEXELS_KEY},
                timeout=20
            )

            if response.status_code != 200:
                log.warning(
                    "Pexels status=%s query=%s",
                    response.status_code,
                    query
                )
                continue

            videos = response.json().get("videos", [])
            ranked = []

            for video in videos:
                video_id = video.get("id")

                if video_id in used_ids:
                    continue

                usable_files = []

                for file_info in video.get("video_files", []):
                    link = file_info.get("link", "")
                    width = int(file_info.get("width") or 0)
                    height = int(file_info.get("height") or 0)

                    if not link.startswith("https://"):
                        continue

                    if width < 320 or height < 320:
                        continue

                    ratio = width / max(height, 1)

                    if 0.48 <= ratio <= 0.85:
                        score = 100
                    elif ratio < 1.1:
                        score = 50
                    else:
                        score = 20

                    score += min(width, 1080) / 50

                    usable_files.append((score, file_info))

                if usable_files:
                    usable_files.sort(
                        key=lambda item: item[0],
                        reverse=True
                    )

                    ranked.append(
                        (
                            usable_files[0][0],
                            video,
                            usable_files[0][1]
                        )
                    )

            ranked.sort(key=lambda item: item[0], reverse=True)

            if ranked:
                _, video, file_info = ranked[0]

                used_ids.add(video.get("id"))

                return file_info["link"]

        except Exception:
            log.exception("Pexels search failed")

    return None


# =========================================================
# DOWNLOAD AND VALIDATE VIDEO
# =========================================================

def download_clip(url, output_path):
    temporary = output_path.with_suffix(".part")
    total_bytes = 0

    try:
        with requests.get(
            url,
            stream=True,
            timeout=(10, 40),
            headers={"User-Agent": "Mozilla/5.0"}
        ) as response:

            if response.status_code != 200:
                return False

            content_length = int(
                response.headers.get("Content-Length") or 0
            )

            if content_length > MAX_CLIP_BYTES:
                return False

            with temporary.open("wb") as file:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue

                    total_bytes += len(chunk)

                    if total_bytes > MAX_CLIP_BYTES:
                        break

                    file.write(chunk)

        if (
            total_bytes <= MAX_CLIP_BYTES
            and temporary.exists()
            and temporary.stat().st_size > 80000
        ):
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_type",
                    "-of", "csv=p=0",
                    str(temporary)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15
            )

            if probe.returncode == 0 and "video" in probe.stdout:
                temporary.replace(output_path)
                return True

    except Exception:
        log.exception("Clip download failed")

    temporary.unlink(missing_ok=True)
    return False


# =========================================================
# FALLBACK
# =========================================================

def create_fallback_clip(output_path, index):
    colors = [
        "0x101827",
        "0x21152b",
        "0x17242b",
        "0x2b1b1b",
        "0x101a2b",
        "0x251c31"
    ]

    run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i",
        f"color=c={colors[index % len(colors)]}:"
        f"s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}",
        "-vf", "format=yuv420p",
        "-t", str(SECONDS),
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "27",
        str(output_path)
    ], timeout=30)


# =========================================================
# OPTIONAL ARABIC CAPTIONS
# =========================================================

def create_caption(text, output_path, font_path):
    from PIL import Image, ImageDraw, ImageFont

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        display_text = get_display(
            arabic_reshaper.reshape(text[:110])
        )

    except Exception:
        display_text = text[:110]

    width = 680

    try:
        font = (
            ImageFont.truetype(font_path, 32)
            if font_path
            else ImageFont.load_default()
        )
    except Exception:
        font = ImageFont.load_default()

    scratch = Image.new("RGBA", (width, 100))
    scratch_draw = ImageDraw.Draw(scratch)

    lines = []
    current_line = ""

    for word in display_text.split():
        trial = (current_line + " " + word).strip()

        if (
            scratch_draw.textlength(trial, font=font) > width - 36
            and current_line
        ):
            lines.append(current_line)
            current_line = word
        else:
            current_line = trial

    if current_line:
        lines.append(current_line)

    lines = lines[:3] or [display_text]

    height = len(lines) * 45 + 24

    image = Image.new(
        "RGBA",
        (width, height),
        (0, 0, 0, 165)
    )

    draw = ImageDraw.Draw(image)

    for index, line in enumerate(lines):
        box = draw.textbbox(
            (0, 0),
            line,
            font=font,
            stroke_width=1
        )

        x = (width - (box[2] - box[0])) // 2

        draw.text(
            (x, 10 + index * 45),
            line,
            font=font,
            fill="white",
            stroke_width=1,
            stroke_fill="black"
        )

    image.save(output_path)


# =========================================================
# VIDEO SCENE PROCESSING
# =========================================================

def build_scene(raw, caption_path, output_path, index):
    scaled = output_path.with_name(f"scaled_{index}.mp4")

    # Crop to fill the vertical frame and add a subtle zoom.
    video_filter = (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:"
        "force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='min(zoom+0.0007,1.035)':d=1:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS},"
        "eq=contrast=1.07:saturation=0.98:brightness=-0.01,"
        "format=yuv420p"
    )

    run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(raw),
        "-vf", video_filter,
        "-t", str(SECONDS),
        "-r", str(FPS),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "25",
        str(scaled)
    ], timeout=75)

    if CAPTIONS_ENABLED:
        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(scaled),
            "-i", str(caption_path),
            "-filter_complex",
            "[0:v][1:v]overlay=(W-w)/2:H-h-85:format=auto",
            "-t", str(SECONDS),
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "25",
            "-pix_fmt", "yuv420p",
            str(output_path)
        ], timeout=75)

    else:
        shutil.copy2(scaled, output_path)

    scaled.unlink(missing_ok=True)


# =========================================================
# ARABIC VOICE
# =========================================================

async def create_voice(text, output_path):
    try:
        import edge_tts

        await edge_tts.Communicate(
            text,
            "ar-SA-HamedNeural",
            rate="-8%",
            pitch="-2Hz"
        ).save(str(output_path))

        if output_path.exists() and output_path.stat().st_size > 1500:
            return

    except Exception:
        log.warning("Edge TTS failed; trying gTTS")

    try:
        from gtts import gTTS

        gTTS(text=text, lang="ar").save(str(output_path))

        if output_path.exists() and output_path.stat().st_size > 1000:
            return

    except Exception:
        log.exception("gTTS failed")

    raise RuntimeError("تعذر إنشاء الصوت العربي.")


# =========================================================
# VIDEO PRODUCTION
# =========================================================

def make_video(chat_id, parts):
    workdir = TMP / str(uuid.uuid4())
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        storyboard = create_storyboard(parts)

        send_message(
            chat_id,
            "🎬 بدأ ظل تجهيز الحلقة: 6 مشاهد، "
            "30 ثانية، باستخدام الخدمات الحالية فقط."
        )

        font = get_font()
        used_video_ids = set()
        clips = []
        fallback_count = 0

        for index, scene in enumerate(storyboard):
            send_message(
                chat_id,
                f"🎞️ تجهيز المشهد {index + 1}/{SCENES}..."
            )

            raw = workdir / f"raw{index}.mp4"
            caption = workdir / f"caption{index}.png"
            output = workdir / f"scene{index}.mp4"

            video_url = search_pexels(
                scene["queries"],
                used_video_ids
            )

            if not (
                video_url
                and download_clip(video_url, raw)
            ):
                create_fallback_clip(raw, index)
                fallback_count += 1

            create_caption(
                scene["narration"],
                caption,
                font
            )

            build_scene(
                raw,
                caption,
                output,
                index
            )

            clips.append(output)

        # Join all six video scenes.
        concat_file = workdir / "concat.txt"

        with concat_file.open("w", encoding="utf-8") as file:
            for clip in clips:
                file.write(f"file '{clip.resolve()}'\n")

        silent_video = workdir / "silent.mp4"

        run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "25",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            "-an",
            str(silent_video)
        ], timeout=150)

        # Generate narration for each scene.
        voice_files = []

        for index, scene in enumerate(storyboard):
            voice_file = workdir / f"voice{index}.mp3"

            asyncio.run(
                create_voice(
                    scene["narration"],
                    voice_file
                )
            )

            voice_files.append(voice_file)

        audio_list = workdir / "audio.txt"

        with audio_list.open("w", encoding="utf-8") as file:
            for voice_file in voice_files:
                file.write(f"file '{voice_file.resolve()}'\n")

        narration = workdir / "narration.mp3"

        run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(audio_list),
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            str(narration)
        ], timeout=90)

        final_video = workdir / "ZIL_FINAL.mp4"

        run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(silent_video),
            "-i", str(narration),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", str(SCENES * SECONDS),
            "-movflags", "+faststart",
            str(final_video)
        ], timeout=90)

        if (
            not final_video.exists()
            or final_video.stat().st_size < 80000
        ):
            raise RuntimeError("ملف الفيديو النهائي غير صالح.")

        # Telegram Bot API upload limit is checked conservatively.
        if final_video.stat().st_size > 49 * 1024 * 1024:
            raise RuntimeError(
                "حجم الفيديو كبير جدًا للإرسال الآمن عبر البوت."
            )

        with final_video.open("rb") as video_file:
            telegram_call(
                "sendVideo",
                data={
                    "chat_id": chat_id,
                    "caption": "🎬 ظل | Micro Drama — 30 ثانية",
                    "supports_streaming": "true"
                },
                files={
                    "video": (
                        "zil_microdrama.mp4",
                        video_file,
                        "video/mp4"
                    )
                },
                timeout=180
            )

        send_message(
            chat_id,
            "✅ انتهى الفيديو: عمودي 9:16، "
            "30 ثانية، 6 مشاهد وتعليق صوتي عربي."
        )

        if not PEXELS_KEY:
            send_message(
                chat_id,
                "⚠️ PEXELS_KEY غير مضبوط في Render. "
                "أضف مفتاح Pexels مجانيًا للحصول على مقاطع فعلية."
            )

        elif fallback_count:
            send_message(
                chat_id,
                f"ℹ️ استخدمت خلفيات بديلة في "
                f"{fallback_count} مشاهد لعدم توفر مقاطع مناسبة."
            )

        if not CAPTIONS_ENABLED:
            send_message(
                chat_id,
                "ℹ️ النصوص على الشاشة متوقفة افتراضيًا. "
                "يمكن تفعيلها من Render عبر CAPTIONS_ENABLED=true."
            )

    except Exception as error:
        log.exception("Video production failed")

        send_message(
            chat_id,
            "❌ فشل إنتاج الفيديو:\n" + str(error)[:500]
        )

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

        with busy_lock:
            busy.discard(chat_id)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎬 أهلاً بك في ظل — النسخة المجانية المطوّرة.\n\n"
        "أرسل القصة من ثلاثة أجزاء:\n"
        "/ظل بداية القصة|تصاعد الأحداث|الخطر أو المفاجأة\n\n"
        "مثال:\n"
        "/ظل دخل رجل غامض إلى قصر الملك|"
        "رفض الملك زواجه من الأميرة|"
        "ظهر نمر ضخم فتقدم الرجل وكشف جزءًا من قوته\n\n"
        "ينتج البوت فيديو عموديًا مدته 30 ثانية مع تعليق عربي. "
        "يعتمد على مقاطع Pexels المتاحة، ولا يولّد ممثلين جددًا "
        "أو يزامن الشفاه."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ ظل يعمل.\n"
        f"Pexels API: {'مضبوط' if PEXELS_KEY else 'غير مضبوط'}\n"
        f"المدة: {SCENES * SECONDS} ثانية\n"
        f"عدد المشاهد: {SCENES}\n"
        f"النصوص على الشاشة: "
        f"{'مفعّلة' if CAPTIONS_ENABLED else 'متوقفة'}\n"
        "توليد مشاهد جديدة بالذكاء الاصطناعي غير مفعّل."
    )


async def launch(update, parts):
    chat_id = update.effective_chat.id

    with busy_lock:
        if chat_id in busy:
            await update.effective_message.reply_text(
                "⏳ يوجد فيديو قيد التجهيز لهذه المحادثة. انتظر حتى ينتهي."
            )
            return

        busy.add(chat_id)

    await update.effective_message.reply_text(
        "🚀 استلمت القصة. بدأ تجهيز الفيديو."
    )

    threading.Thread(
        target=make_video,
        args=(chat_id, parts),
        daemon=True
    ).start()


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await launch(
        update,
        [
            "دخل رجل غامض إلى ساحة القصر وظن الجميع أنه ضعيف",
            "سخر الملك منه ورفض أن يقترب من الأميرة التي أحبته",
            "ظهر نمر هائل أمام الحراس فتقدم الرجل بهدوء وكأنه يخفي قوة مرعبة"
        ]
    )


async def zil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.effective_message.text or ""

    try:
        parts = parse_story(raw)

    except ValueError as error:
        await update.effective_message.reply_text(
            f"❌ {error}\n"
            "الصيغة: /ظل جزء أول|جزء ثاني|جزء ثالث"
        )
        return

    await launch(update, parts)


# =========================================================
# BOT APPLICATION
# =========================================================

bot = Application.builder().token(BOT_TOKEN).build()

bot.add_handler(CommandHandler("start", start))
bot.add_handler(CommandHandler("help", start))
bot.add_handler(CommandHandler("status", status))
bot.add_handler(CommandHandler("test", test))
bot.add_handler(CommandHandler(["zil", "ظل"], zil))


# =========================================================
# RENDER HEALTH ENDPOINTS
# =========================================================

@flask_app.get("/")
def home():
    return "ZIL Micro Drama V2 is running."


@flask_app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "project": "ZIL",
        "version": "2.0",
        "pexels_key": bool(PEXELS_KEY),
        "scenes": SCENES,
        "scene_seconds": SECONDS,
        "captions_enabled": CAPTIONS_ENABLED,
        "ai_video_generation": False
    })


def telegram_polling():
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            data={"drop_pending_updates": "true"},
            timeout=15
        )

    except Exception:
        log.exception("Could not clear old webhook")

    bot.run_polling(
        drop_pending_updates=True,
        close_loop=False
    )


if __name__ == "__main__":
    threading.Thread(
        target=telegram_polling,
        daemon=True,
        name="telegram-polling"
    ).start()

    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False
    )
