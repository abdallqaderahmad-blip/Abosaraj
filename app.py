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
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIGURATION
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("zil")

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PEXELS_KEY = (os.getenv("PEXELS_KEY") or "").strip()
PORT = int(os.getenv("PORT", "10000"))

SCENES = 6
SECONDS = 5
W = 720
H = 1280
FPS = 25

TMP = Path("/tmp/zil")
TMP.mkdir(parents=True, exist_ok=True)

busy = set()
busy_lock = threading.Lock()

flask_app = Flask(__name__)

if not BOT_TOKEN:
    raise RuntimeError(
        "Missing BOT_TOKEN environment variable in Render."
    )


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, data=None, files=None, timeout=30):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=timeout,
    )

    response.raise_for_status()
    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {payload}"
        )

    return payload


def say(chat_id, text):
    try:
        tg(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
            timeout=15,
        )
    except Exception:
        log.exception("Telegram message failed")


# =========================================================
# FFMPEG
# =========================================================

def ffmpeg(args, timeout=60):
    process = subprocess.run(
        list(map(str, args)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg error: " + process.stderr[-700:]
        )

    return process


# =========================================================
# ARABIC FONT
# =========================================================

def get_font():
    candidates = [
        Path(
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf"
        ),
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 10000:
            return str(path)

    font_path = Path("/tmp/Amiri-Bold.ttf")

    try:
        response = requests.get(
            "https://raw.githubusercontent.com/google/fonts/"
            "main/ofl/amiri/Amiri-Bold.ttf",
            timeout=15,
        )

        if (
            response.status_code == 200
            and len(response.content) > 10000
        ):
            font_path.write_bytes(response.content)
            return str(font_path)

    except Exception:
        log.exception("Font download failed")

    return None


# =========================================================
# STORY PROCESSING
# =========================================================

def parse_story(raw):
    parts = [
        re.sub(r"\s+", " ", part).strip()
        for part in raw.split("|")
    ]

    parts = [part for part in parts if part]

    if len(parts) < 2:
        raise ValueError(
            "اكتب القصة على مراحل وافصل بينها بعلامة |"
        )

    parts = parts[:3]

    while len(parts) < 3:
        parts.append(parts[-1])

    return parts


def scene_texts(parts):
    words = "، ".join(parts).split()

    if not words:
        words = ["قصة", "غامضة"]

    output = []

    for index in range(SCENES):
        start = round(index * len(words) / SCENES)
        end = round((index + 1) * len(words) / SCENES)

        text = " ".join(words[start:end])

        if not text:
            text = words[-1]

        output.append(text)

    return output


# =========================================================
# PEXELS SEARCH
# =========================================================

def search_terms(text, index):
    text_lower = text.lower()
    queries = []

    if any(
        word in text_lower
        for word in [
            "ملك", "قصر", "امير", "أمير",
            "اميرة", "أميرة", "عرش", "مملكة"
        ]
    ):
        queries += [
            "medieval castle cinematic",
            "royal throne room",
            "medieval warrior dramatic",
        ]

    if any(
        word in text_lower
        for word in [
            "نمر", "اسد", "أسد", "وحش",
            "ذئب", "قتال", "معركة", "خطر"
        ]
    ):
        queries += [
            "tiger close up wildlife",
            "wild animal dramatic",
            "warrior fighting cinematic",
        ]

    if any(
        word in text_lower
        for word in [
            "حب", "تحب", "اميرة", "أميرة",
            "فتاة", "امرأة", "بنت"
        ]
    ):
        queries += [
            "woman dramatic portrait",
            "elegant woman cinematic",
            "man and woman dramatic scene",
        ]

    if any(
        word in text_lower
        for word in [
            "قوة", "قوي", "سر", "غامض",
            "خارق", "بطل"
        ]
    ):
        queries += [
            "mysterious man cinematic portrait",
            "strong man dramatic close up",
            "man silhouette smoke",
        ]

    if any(
        word in text_lower
        for word in [
            "فقير", "يتيم", "جائع",
            "خبز", "شارع", "عامل"
        ]
    ):
        queries += [
            "lonely man city street cinematic",
            "poor man walking street",
            "emotional man portrait",
        ]

    defaults = [
        [
            "cinematic mysterious man",
            "dark castle cinematic",
        ],
        [
            "cinematic woman portrait",
            "foggy forest cinematic",
        ],
        [
            "dramatic man portrait",
            "stormy sky cinematic",
        ],
    ]

    queries += defaults[index % 3]

    return list(dict.fromkeys(queries))[:6]


def pexels_search(queries):
    if not PEXELS_KEY:
        return None

    for query in queries:
        try:
            response = requests.get(
                "https://api.pexels.com/videos/search",
                params={
                    "query": query,
                    "per_page": 8,
                    "orientation": "portrait",
                    "size": "small",
                },
                headers={
                    "Authorization": PEXELS_KEY,
                },
                timeout=15,
            )

            if response.status_code != 200:
                log.warning(
                    "Pexels status %s",
                    response.status_code,
                )
                continue

            videos = response.json().get("videos", [])

            for video in videos:
                video_files = sorted(
                    video.get("video_files", []),
                    key=lambda item: abs(
                        (item.get("width") or 720) - 720
                    ),
                )

                for video_file in video_files:
                    link = video_file.get("link", "")
                    width = video_file.get("width") or 0

                    if (
                        link.startswith("https://")
                        and 320 <= width <= 1920
                    ):
                        return link

        except Exception:
            log.exception("Pexels search failed")

    return None


# =========================================================
# DOWNLOAD VIDEO
# =========================================================

def download_clip(
    url,
    output_path,
    limit=25_000_000,
):
    temporary_path = output_path.with_suffix(".part")
    total = 0

    try:
        with requests.get(
            url,
            stream=True,
            timeout=(10, 35),
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        ) as response:

            if response.status_code != 200:
                return False

            with temporary_path.open("wb") as file:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > limit:
                        break

                    file.write(chunk)

        if (
            total <= limit
            and temporary_path.exists()
            and temporary_path.stat().st_size > 80000
        ):
            temporary_path.replace(output_path)
            return True

    except Exception:
        log.exception("Clip download failed")

    temporary_path.unlink(missing_ok=True)
    return False


# =========================================================
# FALLBACK VIDEO
# =========================================================

def fallback_clip(output_path, index):
    colors = [
        "0x101827",
        "0x21152b",
        "0x17242b",
        "0x2b1b1b",
        "0x101a2b",
        "0x251c31",
    ]

    color = colors[index % len(colors)]

    ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"color=c={color}:"
                f"s={W}x{H}:r={FPS}:d={SECONDS}"
            ),
            "-vf",
            "format=yuv420p",
            "-t",
            str(SECONDS),
            "-r",
            str(FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            str(output_path),
        ],
        timeout=25,
    )


# =========================================================
# ARABIC CAPTIONS
# =========================================================

def caption_png(text, output_path, font):
    from PIL import Image, ImageDraw, ImageFont

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        text = get_display(
            arabic_reshaper.reshape(text[:100])
        )

    except Exception:
        text = text[:100]

    width = 680
    height = 230

    try:
        image_font = (
            ImageFont.truetype(font, 35)
            if font
            else ImageFont.load_default()
        )
    except Exception:
        image_font = ImageFont.load_default()

    image = Image.new(
        "RGBA",
        (width, height),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(image)

    lines = []
    current_line = ""

    for word in text.split():
        trial = (current_line + " " + word).strip()

        if (
            draw.textlength(trial, font=image_font)
            > width - 30
            and current_line
        ):
            lines.append(current_line)
            current_line = word
        else:
            current_line = trial

    if current_line:
        lines.append(current_line)

    lines = lines[:3] or [text]

    panel_height = len(lines) * 47 + 25

    panel = Image.new(
        "RGBA",
        (width, panel_height),
        (0, 0, 0, 175),
    )

    panel_draw = ImageDraw.Draw(panel)

    for index, line in enumerate(lines):
        box = panel_draw.textbbox(
            (0, 0),
            line,
            font=image_font,
            stroke_width=1,
        )

        text_width = box[2] - box[0]
        x = (width - text_width) // 2

        panel_draw.text(
            (x, 10 + index * 47),
            line,
            font=image_font,
            fill="white",
            stroke_width=1,
            stroke_fill="black",
        )

    image.alpha_composite(
        panel,
        (0, height - panel_height),
    )

    image.save(output_path)


# =========================================================
# ARABIC VOICE
# =========================================================

async def make_voice(text, output_path):
    try:
        import edge_tts

        await edge_tts.Communicate(
            text,
            "ar-SA-ZariyahNeural",
            rate="-8%",
            pitch="+0Hz",
        ).save(str(output_path))

        if (
            output_path.exists()
            and output_path.stat().st_size > 1500
        ):
            return

    except Exception:
        log.exception("Edge TTS failed")

    try:
        from gtts import gTTS

        gTTS(
            text=text,
            lang="ar",
        ).save(str(output_path))

        if (
            output_path.exists()
            and output_path.stat().st_size > 1000
        ):
            return

    except Exception:
        log.exception("gTTS failed")

    raise RuntimeError(
        "تعذر إنشاء الصوت العربي. تحقق من اتصال الخدمة."
    )


# =========================================================
# BUILD EACH SCENE
# =========================================================

def build_scene(raw, caption, output_path, index):
    scaled = output_path.with_name(
        f"scaled_{index}.mp4"
    )

    ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-vf",
            (
                f"scale={W}:{H}:"
                "force_original_aspect_ratio=increase,"
                f"crop={W}:{H},"
                "eq=contrast=1.08:"
                "saturation=0.92:"
                "brightness=-0.02,"
                "format=yuv420p"
            ),
            "-t",
            str(SECONDS),
            "-r",
            str(FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            str(scaled),
        ],
        timeout=45,
    )

    ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(scaled),
            "-i",
            str(caption),
            "-filter_complex",
            "[0:v][1:v]overlay=(W-w)/2:H-h-70:format=auto",
            "-t",
            str(SECONDS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        timeout=45,
    )

    scaled.unlink(missing_ok=True)


# =========================================================
# COMPLETE VIDEO JOB
# =========================================================

def make_video(chat_id, parts):
    work_dir = TMP / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        say(
            chat_id,
            "🎬 ظل بدأ تجهيز فيديو عمودي من 6 مشاهد "
            "باستخدام الأدوات المربوطة حاليًا فقط.",
        )

        font = get_font()
        captions = scene_texts(parts)
        clips = []

        for index, caption in enumerate(captions):
            say(
                chat_id,
                f"🔎 تجهيز المشهد {index + 1}/{SCENES}…",
            )

            raw = work_dir / f"raw{index}.mp4"
            caption_file = work_dir / f"caption{index}.png"
            output = work_dir / f"scene{index}.mp4"

            story_part = parts[min(index // 2, 2)]

            url = pexels_search(
                search_terms(
                    story_part + " " + caption,
                    index,
                )
            )

            downloaded = (
                url is not None
                and download_clip(url, raw)
            )

            if not downloaded:
                fallback_clip(raw, index)

            caption_png(
                caption,
                caption_file,
                font,
            )

            build_scene(
                raw,
                caption_file,
                output,
                index,
            )

            clips.append(output)

        # Join video scenes
        concat_file = work_dir / "concat.txt"

        with concat_file.open(
            "w",
            encoding="utf-8",
        ) as file:
            for clip in clips:
                file.write(
                    "file '" + str(clip.resolve()) + "'\n"
                )

        silent_video = work_dir / "silent.mp4"

        ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "25",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(FPS),
                "-an",
                str(silent_video),
            ],
            timeout=90,
        )

        # Generate Arabic narration clips
        voices = []

        for index, text in enumerate(captions):
            voice = work_dir / f"voice{index}.mp3"

            asyncio.run(
                make_voice(text, voice)
            )

            voices.append(voice)

        audio_list = work_dir / "audio.txt"

        with audio_list.open(
            "w",
            encoding="utf-8",
        ) as file:
            for voice in voices:
                file.write(
                    "file '" + str(voice.resolve()) + "'\n"
                )

        narration = work_dir / "narration.mp3"

        ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(audio_list),
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(narration),
            ],
            timeout=60,
        )

        # Combine video and Arabic narration
        final_video = work_dir / "ZIL_FINAL.mp4"

        ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(silent_video),
                "-i",
                str(narration),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-t",
                str(SCENES * SECONDS),
                "-movflags",
                "+faststart",
                str(final_video),
            ],
            timeout=60,
        )

        if (
            not final_video.exists()
            or final_video.stat().st_size < 80000
        ):
            raise RuntimeError(
                "ملف الفيديو النهائي غير صالح."
            )

        with final_video.open("rb") as video_file:
            tg(
                "sendVideo",
                data={
                    "chat_id": chat_id,
                    "caption": (
                        "🎬 ظل | Micro Drama\n"
                        + " | ".join(parts)
                    )[:900],
                    "supports_streaming": "true",
                },
                files={
                    "video": (
                        "zil_microdrama.mp4",
                        video_file,
                        "video/mp4",
                    )
                },
                timeout=180,
            )

        say(
            chat_id,
            "✅ انتهى الفيديو: 30 ثانية، عمودي 9:16، "
            "6 مشاهد، صوت عربي ونص عربي.",
        )

        if not PEXELS_KEY:
            say(
                chat_id,
                "ℹ️ لم يتم ضبط PEXELS_KEY في Render؛ "
                "لذلك ستظهر خلفيات بديلة بدل لقطات Pexels.",
            )

    except Exception as error:
        log.exception("Video job failed")

        say(
            chat_id,
            "❌ فشل إنتاج الفيديو:\n"
            + str(error)[:600],
        )

    finally:
        shutil.rmtree(
            work_dir,
            ignore_errors=True,
        )

        with busy_lock:
            busy.discard(chat_id)


# =========================================================
# BOT COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "🎬 أهلاً بك في ظل!\n\n"
        "اصنع دراما قصيرة من قصتك باستخدام الأدوات المتاحة.\n\n"
        "الأوامر:\n"
        "/start — بدء الاستخدام\n"
        "/test — تجربة قصة جاهزة\n"
        "/status — حالة البوت\n\n"
        "لإنشاء فيديو أرسل:\n"
        "/zil بداية القصة|تصاعد الأحداث|المفاجأة أو الخطر\n\n"
        "يمكنك أيضًا استخدام /ظل متبوعًا بالقصة.\n\n"
        "مثال:\n"
        "/zil دخل رجل غامض إلى قصر الملك ووقعت الأميرة "
        "في حبه|رفض الملك زواجه منها وطالبه بإثبات قوته"
        "|ظهر نمر ضخم فتقدم الرجل وكشف جزءًا من قوته\n\n"
        "🎥 مدة الفيديو المستهدفة 30 ثانية، مع صوت عربي "
        "ولقطات Pexels عند توفر المفتاح، أو خلفيات بديلة.\n\n"
        "ملاحظة: هذه النسخة لا تولّد ممثلين جددًا "
        "ولا توفر مزامنة شفاه."
    )

    await update.effective_message.reply_text(message)


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "✅ حالة ظل: يعمل\n"
        f"Pexels key: {'موجود' if PEXELS_KEY else 'غير موجود'}\n"
        "الفيديو: 30 ثانية / 6 مشاهد\n"
        "الصوت: عربي عبر Edge TTS مع gTTS كخيار احتياطي\n"
        "التوليد السينمائي الكامل بالذكاء الاصطناعي "
        "غير متاح في الأدوات الحالية."
    )

    await update.effective_message.reply_text(message)


async def launch(update, parts):
    chat_id = update.effective_chat.id

    with busy_lock:
        if chat_id in busy:
            await update.effective_message.reply_text(
                "⏳ يوجد فيديو قيد التجهيز لهذه المحادثة. "
                "انتظر حتى ينتهي."
            )
            return

        busy.add(chat_id)

    await update.effective_message.reply_text(
        "🚀 استلمت القصة، بدأ تجهيز الفيديو."
    )

    threading.Thread(
        target=make_video,
        args=(chat_id, parts),
        daemon=True,
    ).start()


async def test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await launch(
        update,
        [
            "دخل رجل غامض إلى ساحة القصر وظن الجميع أنه ضعيف",
            "سخر الملك منه ورفض أن يقترب من الأميرة التي أحبته",
            "ظهر نمر هائل أمام الحراس فتقدم الرجل بهدوء "
            "وكأنه يخفي قوة مرعبة",
        ],
    )


async def zil(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message_text = (
        update.effective_message.text or ""
    )

    raw = re.sub(
        r"^/(?:ظل|zil)(?:@\w+)?\s*",
        "",
        message_text,
        flags=re.I,
    ).strip()

    try:
        parts = parse_story(raw)

    except ValueError as error:
        await update.effective_message.reply_text(
            f"❌ {error}\n"
            "الصيغة:\n"
            "/zil جزء أول|جزء ثاني|جزء ثالث\n"
            "أو /ظل"
        )
        return

    await launch(update, parts)


# =========================================================
# REGISTER HANDLERS
# =========================================================

bot = Application.builder().token(BOT_TOKEN).build()

bot.add_handler(
    CommandHandler("start", start)
)

bot.add_handler(
    CommandHandler("help", start)
)

bot.add_handler(
    CommandHandler("status", status)
)

bot.add_handler(
    CommandHandler("test", test)
)

# Telegram command names must be Latin.
# This official command works: /zil
bot.add_handler(
    CommandHandler("zil", zil)
)

# Arabic /ظل is processed as a text message.
# This prevents the invalid-command startup error.
bot.add_handler(
    MessageHandler(
        filters.Regex(r"^/ظل(?:@\w+)?(?:\s|$)"),
        zil,
    )
)


# =========================================================
# RENDER HEALTH ENDPOINTS
# =========================================================

@flask_app.get("/")
def home():
    return "ZIL Micro Drama service is running."


@flask_app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "project": "ZIL",
            "version": "1.1",
            "pexels_key": bool(PEXELS_KEY),
            "scenes": SCENES,
            "scene_seconds": SECONDS,
            "ai_video_generation": False,
        }
    )


# =========================================================
# START SERVICE
# =========================================================

def telegram_polling():
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            data={
                "drop_pending_updates": "true",
            },
            timeout=15,
        )

    except Exception:
        log.exception(
            "Could not clear old webhook"
        )

    bot.run_polling(
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    threading.Thread(
        target=telegram_polling,
        daemon=True,
        name="telegram-polling",
    ).start()

    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )
