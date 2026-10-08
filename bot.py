import os
import re
import json
import time
import uuid
import asyncio
import logging
import tempfile
import subprocess
from pathlib import Path

import requests
import edge_tts
from flask import Flask, request
from groq import Groq


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.environ["WAVESPEED_API_KEY"]

PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

VIDEO_SHOTS = 4
SHOT_DURATION = 5

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FINAL_FPS = 16

TTS_VOICE = "ar-SA-HamedNeural"

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"
IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)

processed_updates = set()


# =========================================================
# BASIC HELPERS
# =========================================================

def run_cmd(cmd):
    log.info("CMD: %s", " ".join(map(str, cmd)))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])

    return result.stdout


def download_file(url, path):
    r = requests.get(url, timeout=120)
    r.raise_for_status()

    with open(path, "wb") as f:
        f.write(r.content)

    return path


# =========================================================
# WAVESPEED
# =========================================================

def wavespeed_headers():
    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type": "application/json"
    }


def wavespeed_submit(model, payload):
    url = f"{WAVESPEED_BASE}/{model}"

    r = requests.post(
        url,
        headers=wavespeed_headers(),
        json=payload,
        timeout=(10, 60)
    )

    r.raise_for_status()

    body = r.json()

    if body.get("code") not in (None, 200):
        raise RuntimeError(body.get("message", "WaveSpeed submit failed"))

    data = body.get("data", body)

    task_id = data.get("id")

    if not task_id:
        raise RuntimeError(f"No WaveSpeed task id: {body}")

    return task_id


def wavespeed_result(task_id, timeout=600):
    url = f"{WAVESPEED_BASE}/predictions/{task_id}/result"

    started = time.time()

    while time.time() - started < timeout:

        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {WAVESPEED_API_KEY}"
            },
            timeout=30
        )

        r.raise_for_status()

        body = r.json()
        data = body.get("data", body)

        status = data.get("status", "").lower()

        log.info("WaveSpeed %s -> %s", task_id, status)

        if status == "completed":
            outputs = data.get("outputs") or data.get("output")

            if not outputs:
                raise RuntimeError(f"Completed but no output: {body}")

            if isinstance(outputs, list):
                return outputs[0]

            return outputs

        if status in {
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        }:
            raise RuntimeError(f"WaveSpeed failed: {body}")

        time.sleep(2)

    raise TimeoutError("WaveSpeed generation timeout")


def generate_image(prompt):
    log.info("Generating reference image")

    task_id = wavespeed_submit(
        IMAGE_MODEL,
        {
            "prompt": prompt,
            "size": "1024*1536",
            "output_format": "jpeg"
        }
    )

    return wavespeed_result(task_id)


def generate_video(image_url, prompt):
    log.info("Generating Wan video")

    task_id = wavespeed_submit(
        VIDEO_MODEL,
        {
            "prompt": prompt,
            "image": image_url,
            "duration": SHOT_DURATION,
            "negative_prompt": (
                "text, subtitles, watermark, logo, distorted face, "
                "extra fingers, deformed hands, duplicate person, "
                "flicker, low quality, blurry"
            ),
            "seed": 12345
        }
    )

    return wavespeed_result(task_id)


# =========================================================
# GROQ STORY ENGINE
# =========================================================

def create_story(user_idea):
    system_prompt = """
أنت كاتب ومخرج مسلسل عربي قصير جداً.

اكتب قصة Microdrama عربية من 20 ثانية تقريباً.
النوع:
Mystery + Suspense + Cinematic.

القصة يجب أن تبدأ بخطاف قوي جداً.
يجب أن يكون هناك سر أو سؤال يجعل المشاهد يريد معرفة النهاية.

لا تستخدم أسلوب قصص الأطفال.
لا تجعل القصة تعليمية.
لا تستخدم دماء أو مشاهد جنسية أو عنف شديد.

نريد هوية مسلسل عربية سينمائية.

المطلوب JSON فقط بهذا الشكل:

{
  "title": "...",
  "hook": "...",
  "character": "...",
  "visual_identity": "...",
  "scenes": [
    {
      "narration_ar": "...",
      "image_prompt": "...",
      "video_prompt": "..."
    },
    {
      "narration_ar": "...",
      "image_prompt": "...",
      "video_prompt": "..."
    },
    {
      "narration_ar": "...",
      "image_prompt": "...",
      "video_prompt": "..."
    },
    {
      "narration_ar": "...",
      "image_prompt": "...",
      "video_prompt": "..."
    }
  ]
}

قواعد مهمة:

- 4 مشاهد فقط.
- كل مشهد مصمم لفيديو 5 ثوانٍ.
- narration_ar بالعربية.
- image_prompt بالإنجليزية.
- video_prompt بالإنجليزية.
- نفس الشخصية يجب أن تبقى بنفس العمر والوجه والملابس والألوان.
- image_prompt يجب أن يحتوي على وصف الشخصية والهوية البصرية.
- cinematic lighting.
- realistic human appearance.
- vertical social-media composition.
- لا تضع أي كتابة داخل الصور.
- المشاهد يجب أن تتصل ببعضها.
- النهاية يجب أن تكشف شيئاً أو تترك سؤالاً قوياً.
"""

    user_prompt = (
        f"فكرة المستخدم:\n{user_idea}\n\n"
        "حوّلها إلى حلقة غموض وتشويق سينمائية قصيرة."
    )

    response = groq.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.8,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    )

    data = json.loads(response.choices[0].message.content)

    if len(data.get("scenes", [])) != VIDEO_SHOTS:
        raise RuntimeError("Groq did not return exactly 4 scenes")

    return data


# =========================================================
# TTS
# =========================================================

async def make_tts(text, output):
    communicate = edge_tts.Communicate(
        text,
        TTS_VOICE
    )

    await communicate.save(output)


def generate_tts(text, output):
    asyncio.run(make_tts(text, output))


# =========================================================
# VIDEO PROCESSING
# =========================================================

def normalize_video(input_path, output_path):
    run_cmd([
        "ffmpeg",
        "-y",
        "-i", str(input_path),

        "-vf",
        (
            f"scale={FINAL_WIDTH}:-2,"
            f"crop={FINAL_WIDTH}:{FINAL_HEIGHT}:"
            f"(in_w-{FINAL_WIDTH})/2:"
            f"(in_h-{FINAL_HEIGHT})/2"
        ),

        "-r", str(FINAL_FPS),

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",

        "-an",

        str(output_path)
    ])


def concat_videos(videos, output):
    list_file = output.parent / "videos.txt"

    with open(list_file, "w", encoding="utf-8") as f:
        for video in videos:
            f.write(f"file '{video}'\n")

    run_cmd([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output)
    ])


def concat_audio(audio_files, output):
    list_file = output.parent / "audio.txt"

    with open(list_file, "w", encoding="utf-8") as f:
        for audio in audio_files:
            f.write(f"file '{audio}'\n")

    run_cmd([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:a", "aac",
        "-b:a", "128k",
        str(output)
    ])


def add_audio(video, audio, output):
    run_cmd([
        "ffmpeg",
        "-y",
        "-i", str(video),
        "-i", str(audio),

        "-map", "0:v:0",
        "-map", "1:a:0",

        "-c:v", "copy",
        "-c:a", "aac",

        "-shortest",

        str(output)
    ])


# =========================================================
# CAPTIONS
# =========================================================

def make_srt(story, srt_path):
    lines = []
    current = 0.0

    for i, scene in enumerate(story["scenes"], 1):

        start = current
        end = current + SHOT_DURATION

        def ts(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)

            return f"{h:02}:{m:02}:{s:02},{ms:03}"

        lines.append(
            f"{i}\n"
            f"{ts(start)} --> {ts(end)}\n"
            f"{scene['narration_ar']}\n"
        )

        current = end

    srt_path.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def add_captions(video, srt, output):
    subtitle_path = str(srt).replace("\\", "/").replace(":", "\\:")

    vf = (
        f"subtitles='{subtitle_path}':"
        "force_style="
        "'FontName=Arial,FontSize=22,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,"
        "Alignment=2,MarginV=70'"
    )

    try:
        run_cmd([
            "ffmpeg",
            "-y",
            "-i", str(video),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "24",
            "-c:a", "copy",
            str(output)
        ])

        return True

    except Exception as e:
        log.warning("Caption rendering failed: %s", e)
        return False


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    r = requests.post(
        url,
        data=data,
        files=files,
        timeout=120
    )

    r.raise_for_status()

    body = r.json()

    if not body.get("ok"):
        raise RuntimeError(body)

    return body


def send_message(chat_id, text):
    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_video(chat_id, video_path, caption=""):
    with open(video_path, "rb") as f:
        telegram(
            "sendVideo",
            data={
                "chat_id": chat_id,
                "caption": caption[:1024]
            },
            files={
                "video": (
                    "episode.mp4",
                    f,
                    "video/mp4"
                )
            }
        )


# =========================================================
# COMPLETE VIDEO PIPELINE
# =========================================================

def create_episode(user_idea, workdir):

    story = create_story(user_idea)

    log.info("TITLE: %s", story["title"])

    raw_videos = []
    normalized_videos = []
    audio_files = []

    # -----------------------------------------------------
    # 1. Generate 4 images
    # 2. Animate each image with Wan
    # -----------------------------------------------------

    for index, scene in enumerate(story["scenes"], 1):

        log.info("SCENE %s/4", index)

        image_url = generate_image(
            scene["image_prompt"]
        )

        log.info("IMAGE: %s", image_url)

        video_url = generate_video(
            image_url,
            scene["video_prompt"]
        )

        raw_video = workdir / f"raw_{index}.mp4"
        normalized = workdir / f"scene_{index}.mp4"

        download_file(video_url, raw_video)

        normalize_video(
            raw_video,
            normalized
        )

        raw_videos.append(raw_video)
        normalized_videos.append(normalized)

    # -----------------------------------------------------
    # Join video
    # -----------------------------------------------------

    joined_video = workdir / "joined.mp4"

    concat_videos(
        normalized_videos,
        joined_video
    )

    # -----------------------------------------------------
    # Arabic TTS
    # -----------------------------------------------------

    for index, scene in enumerate(story["scenes"], 1):

        audio = workdir / f"audio_{index}.mp3"

        generate_tts(
            scene["narration_ar"],
            audio
        )

        audio_files.append(audio)

    joined_audio = workdir / "audio.mp3"

    concat_audio(
        audio_files,
        joined_audio
    )

    # -----------------------------------------------------
    # Add audio
    # -----------------------------------------------------

    voiced_video = workdir / "voiced.mp4"

    add_audio(
        joined_video,
        joined_audio,
        voiced_video
    )

    # -----------------------------------------------------
    # Captions
    # -----------------------------------------------------

    srt = workdir / "captions.srt"

    make_srt(
        story,
        srt
    )

    final_video = workdir / "final.mp4"

    if not add_captions(
        voiced_video,
        srt,
        final_video
    ):
        final_video = voiced_video

    return final_video, story


# =========================================================
# TELEGRAM MESSAGE PROCESSOR
# =========================================================

def process_message(chat_id, text):

    workdir = Path(
        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        send_message(
            chat_id,
            "🎬 بدأت تجهيز الحلقة...\n"
            "🧠 القصة → 🎨 المشاهد → 🎥 الفيديو → 🎙️ الصوت"
        )

        final_video, story = create_episode(
            text,
            workdir
        )

        send_video(
            chat_id,
            final_video,
            caption=f"🎬 {story['title']}\n\n"
                    f"{story['hook']}"
        )

        log.info(
            "Episode completed for chat %s",
            chat_id
        )

    except Exception as e:

        log.exception("Episode failed")

        send_message(
            chat_id,
            "❌ صار خطأ أثناء إنتاج الفيديو.\n"
            "شوف Logs في Render لمعرفة السبب."
        )

    finally:

        try:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass


# =========================================================
# WEBHOOK
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return "Abosaraj is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "service": "abosaraj",
        "video_model": VIDEO_MODEL,
        "image_model": IMAGE_MODEL
    }, 200


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():

    update = request.get_json(
        silent=True
    ) or {}

    update_id = update.get("update_id")

    if update_id in processed_updates:
        return "ok", 200

    if update_id is not None:
        processed_updates.add(update_id)

        if len(processed_updates) > 1000:
            processed_updates.clear()

    message = update.get("message") or {}

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    text = message.get("text")

    if not chat_id or not text:
        return "ok", 200

    # Ignore commands
    if text.startswith("/start"):
        send_message(
            chat_id,
            "🔥 أهلاً بك في Abosaraj.\n\n"
            "اكتب فكرة القصة، وأنا أحولها لحلقة غموض سينمائية."
        )
        return "ok", 200

    # Run in background so Telegram webhook responds immediately
    import threading

    threading.Thread(
        target=process_message,
        args=(chat_id, text),
        daemon=True
    ).start()

    return "ok", 200


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if not RENDER_EXTERNAL_URL:
        log.warning(
            "RENDER_EXTERNAL_URL is missing. "
            "Webhook was not configured."
        )
        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL}"
        "/telegram/webhook"
    )

    try:

        telegram(
            "setWebhook",
            {
                "url": webhook_url
            }
        )

        log.info(
            "Telegram webhook configured: %s",
            webhook_url
        )

    except Exception:
        log.exception(
            "Failed to configure Telegram webhook"
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log.info("===================================")
    log.info("ABOSARAJ STARTING")
    log.info("Image: %s", IMAGE_MODEL)
    log.info("Video: %s", VIDEO_MODEL)
    log.info("Shots: %s x %ss", VIDEO_SHOTS, SHOT_DURATION)
    log.info("===================================")

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
