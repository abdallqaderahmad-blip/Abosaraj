import os
import json
import time
import asyncio
import shutil
import tempfile
import threading
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

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

# ---- Production format ----

SHOT_COUNT = 4
SHOT_DURATION = 5

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

TTS_VOICE = "ar-SA-HamedNeural"

# ---- WaveSpeed ----

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"

IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
IMAGE_EDIT_MODEL = "wavespeed-ai/z-image-turbo/image-to-image"

VIDEO_MODEL = (
    "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
)

# =========================================================
# APP
# =========================================================

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)

logging_lock = threading.Lock()


def log(message):
    with logging_lock:
        print(
            f"[ABOSARAJ] {time.strftime('%H:%M:%S')} "
            f"{message}",
            flush=True
        )


# =========================================================
# HTTP
# =========================================================

def auth_headers():
    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type": "application/json"
    }


def get_headers():
    return {
        "Authorization": f"Bearer {WAVESPEED_API_KEY}"
    }


# =========================================================
# WAVESPEED
# =========================================================

def wavespeed_submit(model, payload):
    """
    Submit ONE task.

    IMPORTANT:
    We never blindly repeat this POST.
    WaveSpeed warns that a disconnected response may still
    mean the task was accepted/billed.
    """

    url = f"{WAVESPEED_BASE}/{model}"

    log(f"WaveSpeed submit: {model}")

    response = requests.post(
        url,
        headers=auth_headers(),
        json=payload,
        timeout=(10, 60)
    )

    response.raise_for_status()

    body = response.json()

    if body.get("code") != 200:
        raise RuntimeError(
            body.get("message", "WaveSpeed task failed")
        )

    data = body["data"]

    task_id = data.get("id")

    if not task_id:
        raise RuntimeError(
            f"WaveSpeed returned no task id: {body}"
        )

    log(f"Task created: {task_id}")

    return task_id


def wavespeed_wait(task_id, timeout=900):
    """
    Poll the result.
    GET requests are safe to retry.
    """

    url = (
        f"{WAVESPEED_BASE}"
        f"/predictions/{task_id}/result"
    )

    started = time.time()

    while True:

        if time.time() - started > timeout:
            raise TimeoutError(
                f"WaveSpeed timeout: {task_id}"
            )

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        response.raise_for_status()

        body = response.json()

        if body.get("code") != 200:
            raise RuntimeError(body)

        data = body["data"]

        status = str(
            data.get("status", "")
        ).lower()

        log(
            f"Task {task_id}: {status}"
        )

        if status == "completed":

            outputs = data.get("outputs")

            if not outputs:
                raise RuntimeError(
                    f"No outputs: {body}"
                )

            return outputs[0]

        if status in (
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        ):
            raise RuntimeError(
                f"WaveSpeed task {task_id} "
                f"failed: {body}"
            )

        time.sleep(2)


# =========================================================
# WAVESPEED FILE UPLOAD
# =========================================================

def upload_to_wavespeed(path):
    """
    Upload a local image to WaveSpeed and return
    its download_url.
    """

    path = Path(path)

    size = path.stat().st_size

    log(
        f"Uploading reference: "
        f"{path.name} ({size} bytes)"
    )

    ticket_response = requests.post(
        f"{WAVESPEED_BASE}/media/uploads",
        headers=auth_headers(),
        json={
            "filename": path.name,
            "size": size
        },
        timeout=30
    )

    ticket_response.raise_for_status()

    ticket = ticket_response.json()

    if ticket.get("code") != 200:
        raise RuntimeError(ticket)

    data = ticket["data"]

    upload_info = data["upload"]

    with path.open("rb") as f:

        upload_response = requests.put(
            upload_info["url"],
            headers=upload_info["headers"],
            data=f,
            timeout=300
        )

    upload_response.raise_for_status()

    return data["download_url"]


# =========================================================
# IMAGE GENERATION
# =========================================================

def generate_character_reference(
    character_prompt,
    output_path
):
    """
    Generate the master character image.
    """

    task = wavespeed_submit(
        IMAGE_MODEL,
        {
            "prompt": character_prompt,
            "size": "1024*1536",
            "output_format": "jpeg",
            "seed": 24117
        }
    )

    url = wavespeed_wait(task)

    download_file(
        url,
        output_path
    )

    return output_path


def generate_scene_image(
    character_url,
    prompt,
    output_path,
    seed
):
    """
    Generate a scene image while using the same
    character reference.
    """

    task = wavespeed_submit(
        IMAGE_EDIT_MODEL,
        {
            "prompt": prompt,
            "image": character_url,
            "size": "1024*1536",
            "strength": 0.42,
            "seed": seed,
            "output_format": "jpeg"
        }
    )

    url = wavespeed_wait(task)

    download_file(
        url,
        output_path
    )

    return output_path


# =========================================================
# VIDEO GENERATION
# =========================================================

def generate_video(
    scene_image_url,
    video_prompt,
    seed
):
    """
    Image -> cinematic video.
    """

    task = wavespeed_submit(
        VIDEO_MODEL,
        {
            "prompt": video_prompt,

            "image": scene_image_url,

            "duration": SHOT_DURATION,

            "seed": seed,

            "negative_prompt": (
                "text, subtitles, watermark, logo, "
                "bad anatomy, deformed face, "
                "extra fingers, duplicate people, "
                "melting face, flicker, jitter, "
                "low quality, blurry, distorted hands"
            )
        }
    )

    return wavespeed_wait(task)


# =========================================================
# DOWNLOAD
# =========================================================

def download_file(url, path):

    log(f"Downloading: {url}")

    response = requests.get(
        url,
        timeout=180
    )

    response.raise_for_status()

    Path(path).write_bytes(
        response.content
    )

    return path


# =========================================================
# GROQ — STORY + DIRECTING
# =========================================================

def create_story(user_idea):

    prompt = f"""
أنت الآن:

كاتب سيناريو +
مخرج سينمائي +
مدير تصوير +
مشرف استمرارية شخصية.

نريد إنتاج حلقة عربية قصيرة جداً
من مسلسل Microdrama.

النوع:
Mystery / Suspense / Psychological Thriller.

الفكرة التي أعطاها المستخدم:

{user_idea}

اكتب الحلقة على شكل 4 لقطات فقط.

كل لقطة = 5 ثوانٍ.

الهدف ليس عمل slideshow.

كل لقطة يجب أن تبدو كجزء من فيلم حقيقي:
- حركة شخصية
- حركة كاميرا
- حركة بيئة
- إضاءة سينمائية
- عمق مجال
- composition
- cinematic pacing

يجب الحفاظ على نفس الشخصية في جميع اللقطات.

أريد JSON فقط.

الصيغة:

{{
  "title": "...",

  "hook": "...",

  "character": {{
    "identity": "...",
    "age": "...",
    "face": "...",
    "hair": "...",
    "clothes": "...",
    "colors": "..."
  }},

  "visual_style": "...",

  "character_image_prompt": "...",

  "scenes": [
    {{
      "narration_ar": "...",
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "..."
    }},

    {{
      "narration_ar": "...",
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "..."
    }},

    {{
      "narration_ar": "...",
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "..."
    }},

    {{
      "narration_ar": "...",
      "scene_image_prompt": "...",
      "video_prompt": "...",
      "camera": "...",
      "sound": "..."
    }}
  ]
}}

قواعد:

1. narration_ar عربي طبيعي ومثير.
2. لا تكتب أكثر من جملة أو جملتين في اللقطة.
3. الصورة والوصف البصري بالإنجليزية.
4. الفيديو prompt بالإنجليزية.
5. نفس الشخصية تماماً.
6. نفس الملابس والألوان إلا إذا القصة تحتاج تغييراً.
7. لا توجد كتابة داخل الصورة.
8. لا توجد شعارات.
9. لا توجد watermarks.
10. لا تستخدم شخصيات أطفال.
11. لا تستخدم gore.
12. لا تستخدم محتوى جنسي.
13. لا تستخدم لقطات ثابتة فقط.
14. video_prompt يجب أن يصف الحركة.
15. camera يجب أن يصف حركة الكاميرا.
16. sound يصف المؤثر الصوتي المطلوب.
17. أول لقطة يجب أن تحتوي Hook.
18. اللقطة الرابعة يجب أن تحتوي كشفاً أو cliffhanger.
19. الأسلوب:
realistic cinematic Arabic drama,
professional film lighting,
natural human skin,
shallow depth of field,
realistic camera movement,
high production value,
vertical composition.
"""

    response = groq.chat.completions.create(

        model=GROQ_MODEL,

        temperature=0.75,

        response_format={
            "type": "json_object"
        },

        messages=[
            {
                "role": "system",
                "content": prompt
            },
            {
                "role": "user",
                "content": user_idea
            }
        ]
    )

    result = json.loads(
        response.choices[0]
        .message.content
    )

    scenes = result.get("scenes", [])

    if len(scenes) != SHOT_COUNT:
        raise RuntimeError(
            "Groq did not return 4 scenes"
        )

    return result


# =========================================================
# ARABIC TTS
# =========================================================

async def tts_async(text, output):

    communicator = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicator.save(
        str(output)
    )


def create_tts(text, output):

    asyncio.run(
        tts_async(
            text,
            output
        )
    )


# =========================================================
# FFMPEG
# =========================================================

def run_ffmpeg(args):

    command = [
        "ffmpeg",
        "-y"
    ] + args

    log(
        "FFmpeg: "
        + " ".join(map(str, command))
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr[-5000:]
        )


# =========================================================
# NORMALIZE VIDEO
# =========================================================

def normalize_video(
    source,
    output
):

    run_ffmpeg([
        "-i",
        str(source),

        "-vf",
        (
            f"scale={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}"
        ),

        "-r",
        str(VIDEO_FPS),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-an",

        str(output)
    ])


# =========================================================
# CONCAT VIDEO
# =========================================================

def concat_videos(
    videos,
    output
):

    list_file = (
        output.parent /
        "video_list.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for video in videos:

            safe_path = (
                str(video)
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    run_ffmpeg([
        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(list_file),

        "-c",
        "copy",

        str(output)
    ])


# =========================================================
# CONCAT AUDIO
# =========================================================

def concat_audio(
    audios,
    output
):

    list_file = (
        output.parent /
        "audio_list.txt"
    )

    with list_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        for audio in audios:

            safe_path = (
                str(audio)
                .replace("'", "'\\''")
            )

            f.write(
                f"file '{safe_path}'\n"
            )

    run_ffmpeg([
        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(list_file),

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(output)
    ])


# =========================================================
# CREATE SIMPLE CINEMATIC AMBIENCE
# =========================================================

def create_ambience(
    output,
    duration
):

    """
    Generates a very subtle cinematic ambience
    locally with FFmpeg.

    No external music API is required.
    """

    run_ffmpeg([
        "-f",
        "lavfi",

        "-i",
        (
            "anoisesrc="
            "color=brown:"
            "amplitude=0.025:"
            f"duration={duration}"
        ),

        "-af",
        (
            "lowpass=f=900,"
            "highpass=f=80,"
            "volume=0.55"
        ),

        "-c:a",
        "aac",

        "-b:a",
        "96k",

        str(output)
    ])


# =========================================================
# MIX VOICE + AMBIENCE
# =========================================================

def mix_audio(
    voice,
    ambience,
    output
):

    run_ffmpeg([
        "-i",
        str(voice),

        "-i",
        str(ambience),

        "-filter_complex",
        (
            "[0:a]volume=1.0[voice];"
            "[1:a]volume=0.16[amb];"
            "[voice][amb]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=2"
        ),

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(output)
    ])


# =========================================================
# SRT
# =========================================================

def seconds_to_srt(seconds):

    hours = int(
        seconds // 3600
    )

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = int(
        seconds % 60
    )

    milliseconds = int(
        (seconds - int(seconds)) * 1000
    )

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{milliseconds:03}"
    )


def create_srt(
    scenes,
    output
):

    lines = []

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        start = (
            index - 1
        ) * SHOT_DURATION

        end = (
            index
        ) * SHOT_DURATION

        lines.append(
            str(index)
        )

        lines.append(
            f"{seconds_to_srt(start)} "
            f"--> "
            f"{seconds_to_srt(end)}"
        )

        lines.append(
            scene["narration_ar"]
        )

        lines.append("")

    output.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# =========================================================
# ADD ARABIC CAPTIONS
# =========================================================

def add_captions(
    video,
    srt,
    output
):

    subtitle_path = (
        str(srt)
        .replace("\\", "/")
        .replace(":", "\\:")
    )

    style = (
        "FontName=Arial,"
        "FontSize=22,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=70"
    )

    run_ffmpeg([
        "-i",
        str(video),

        "-vf",
        (
            f"subtitles='{subtitle_path}':"
            f"force_style='{style}'"
        ),

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(output)
    ])


# =========================================================
# FINAL AUDIO
# =========================================================

def add_audio(
    video,
    audio,
    output
):

    run_ffmpeg([
        "-i",
        str(video),

        "-i",
        str(audio),

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

        "-shortest",

        str(output)
    ])


# =========================================================
# COMPLETE EPISODE
# =========================================================

def create_episode(
    user_idea,
    workdir
):

    log("================================")
    log("CREATING EPISODE")
    log("================================")

    # -----------------------------------------------------
    # 1. STORY
    # -----------------------------------------------------

    story = create_story(
        user_idea
    )

    log(
        f"TITLE: {story['title']}"
    )

    # -----------------------------------------------------
    # 2. MASTER CHARACTER
    # -----------------------------------------------------

    character_image = (
        workdir /
        "character.jpg"
    )

    generate_character_reference(
        story["character_image_prompt"],
        character_image
    )

    # Upload master character reference
    character_url = upload_to_wavespeed(
        character_image
    )

    # -----------------------------------------------------
    # 3. SCENES
    # -----------------------------------------------------

    scene_videos = []

    for index, scene in enumerate(
        story["scenes"],
        start=1
    ):

        log(
            f"========== SCENE {index}/4 =========="
        )

        # ---------------------------------------------
        # Scene image
        # ---------------------------------------------

        scene_image = (
            workdir /
            f"scene_{index}.jpg"
        )

        scene_prompt = (
            story["visual_style"]
            + "\n"
            + story["character"]["identity"]
            + "\n"
            + scene["scene_image_prompt"]
            + "\n"
            + "Maintain the exact same main character "
              "identity, face, hair, clothing and colors."
        )

        generate_scene_image(
            character_url,
            scene_prompt,
            scene_image,
            24117 + index
        )

        # ---------------------------------------------
        # Upload scene image
        # ---------------------------------------------

        scene_url = upload_to_wavespeed(
            scene_image
        )

        # ---------------------------------------------
        # Video
        # ---------------------------------------------

        video_prompt = (
            scene["video_prompt"]
            + "\n"
            + scene["camera"]
            + "\n"
            + "Natural cinematic movement."
            + "\n"
            + "Keep the character identity consistent."
            + "\n"
            + "No text, no subtitles, no logos."
        )

        video_url = generate_video(
            scene_url,
            video_prompt,
            50000 + index
        )

        raw_video = (
            workdir /
            f"raw_{index}.mp4"
        )

        normalized_video = (
            workdir /
            f"scene_{index}.mp4"
        )

        download_file(
            video_url,
            raw_video
        )

        normalize_video(
            raw_video,
            normalized_video
        )

        scene_videos.append(
            normalized_video
        )

    # -----------------------------------------------------
    # 4. JOIN VIDEO
    # -----------------------------------------------------

    joined_video = (
        workdir /
        "joined.mp4"
    )

    concat_videos(
        scene_videos,
        joined_video
    )

    # -----------------------------------------------------
    # 5. ARABIC VOICE
    # -----------------------------------------------------

    voice_files = []

    for index, scene in enumerate(
        story["scenes"],
        start=1
    ):

        audio = (
            workdir /
            f"voice_{index}.mp3"
        )

        create_tts(
            scene["narration_ar"],
            audio
        )

        voice_files.append(
            audio
        )

    voice_track = (
        workdir /
        "voice.mp3"
    )

    concat_audio(
        voice_files,
        voice_track
    )

    # -----------------------------------------------------
    # 6. AMBIENCE / EFFECT BED
    # -----------------------------------------------------

    ambience = (
        workdir /
        "ambience.m4a"
    )

    create_ambience(
        ambience,
        SHOT_COUNT * SHOT_DURATION
    )

    mixed_audio = (
        workdir /
        "mixed_audio.m4a"
    )

    mix_audio(
        voice_track,
        ambience,
        mixed_audio
    )

    # -----------------------------------------------------
    # 7. AUDIO + VIDEO
    # -----------------------------------------------------

    voiced_video = (
        workdir /
        "voiced.mp4"
    )

    add_audio(
        joined_video,
        mixed_audio,
        voiced_video
    )

    # -----------------------------------------------------
    # 8. ARABIC CAPTIONS
    # -----------------------------------------------------

    srt = (
        workdir /
        "captions.srt"
    )

    create_srt(
        story["scenes"],
        srt
    )

    final_video = (
        workdir /
        "final.mp4"
    )

    add_captions(
        voiced_video,
        srt,
        final_video
    )

    log("================================")
    log("EPISODE COMPLETE")
    log("================================")

    return final_video, story


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=180
    )

    response.raise_for_status()

    body = response.json()

    if not body.get("ok"):
        raise RuntimeError(body)

    return body


def send_message(
    chat_id,
    text
):

    telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_video(
    chat_id,
    path,
    caption=""
):

    with open(
        path,
        "rb"
    ) as video:

        telegram_api(
            "sendVideo",

            data={
                "chat_id": chat_id,
                "caption": caption[:1024]
            },

            files={
                "video": (
                    "abosaraj.mp4",
                    video,
                    "video/mp4"
                )
            }
        )


# =========================================================
# MESSAGE PROCESSING
# =========================================================

def process_message(
    chat_id,
    text
):

    workdir = Path(
        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        send_message(
            chat_id,
            "🎬 بدأت صناعة الحلقة...\n\n"
            "🧠 كتابة القصة\n"
            "👤 تثبيت الشخصية\n"
            "🎨 بناء المشاهد\n"
            "🎥 توليد الحركة السينمائية\n"
            "🎙️ الصوت العربي\n"
            "📝 النص العربي\n"
            "🎧 المؤثرات\n"
            "✂️ المونتاج"
        )

        final_video, story = (
            create_episode(
                text,
                workdir
            )
        )

        send_video(
            chat_id,
            final_video,
            (
                f"🎬 {story['title']}\n\n"
                f"{story['hook']}"
            )
        )

        log(
            f"Sent episode to {chat_id}"
        )

    except Exception as e:

        log(
            f"ERROR: {repr(e)}"
        )

        send_message(
            chat_id,
            "❌ صار خطأ أثناء صناعة الحلقة.\n\n"
            "افتح Logs في Render وابعتلي آخر "
            "الأسطر."
        )

    finally:

        shutil.rmtree(
            workdir,
            ignore_errors=True
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Abosaraj cinematic engine OK",
        200
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return {
        "status": "ok",
        "image_model": IMAGE_MODEL,
        "video_model": VIDEO_MODEL,
        "shots": SHOT_COUNT,
        "duration": SHOT_DURATION
    }


@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    message = (
        update.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    chat_id = chat.get("id")

    text = message.get("text")

    if not chat_id or not text:
        return "ok", 200

    if text.startswith("/start"):

        send_message(
            chat_id,
            "🔥 أهلاً بك في Abosaraj.\n\n"
            "اكتب فكرة الحلقة، مثال:\n\n"
            "رجل يسمع صوت زوجته المتوفاة "
            "كل ليلة الساعة 3:17."
        )

        return "ok", 200

    threading.Thread(
        target=process_message,
        args=(
            chat_id,
            text
        ),
        daemon=True
    ).start()

    return "ok", 200


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if not RENDER_EXTERNAL_URL:

        log(
            "RENDER_EXTERNAL_URL missing"
        )

        return

    webhook_url = (
        f"{RENDER_EXTERNAL_URL}"
        "/telegram/webhook"
    )

    try:

        telegram_api(
            "setWebhook",
            {
                "url": webhook_url
            }
        )

        log(
            f"Webhook: {webhook_url}"
        )

    except Exception as e:

        log(
            f"Webhook error: {e}"
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log("==============================")
    log("ABOSARAJ CINEMATIC ENGINE")
    log("==============================")

    log(
        f"Image: {IMAGE_MODEL}"
    )

    log(
        f"Video: {VIDEO_MODEL}"
    )

    log(
        f"Shots: {SHOT_COUNT} x {SHOT_DURATION}s"
    )

    log(
        f"TTS: {TTS_VOICE}"
    )

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
