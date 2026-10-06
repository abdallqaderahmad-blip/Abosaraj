import os
import json
import re
import uuid
import shutil
import logging
import threading
import subprocess
from pathlib import Path

import requests
import fal_client

from flask import Flask
from gtts import gTTS

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from groq import Groq


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

PORT = int(os.getenv("PORT", "10000"))

# Groq model
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

# FAL image model
FAL_MODEL = os.getenv(
    "FAL_MODEL",
    "fal-ai/hunyuan-image/v3/text-to-image"
)

# Video settings
SCENE_COUNT = 8
SCENE_DURATION = 8
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280

BASE_DIR = Path("/tmp/abosaraj")
BASE_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK SERVER
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return """
    <html>
        <head>
            <title>Abosaraj Bot</title>
        </head>
        <body>
            <h1>🔥 ABOSARAJ BOT IS ONLINE</h1>
            <p>Video generation system is running.</p>
        </body>
    </html>
    """


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Abosaraj",
        "video_system": "online"
    }


def run_web_server():
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False
    )


# =========================================================
# VALIDATION
# =========================================================

def validate_environment():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    if not FAL_KEY:
        missing.append("FAL_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    os.environ["FAL_KEY"] = FAL_KEY


# =========================================================
# COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔥 أهلاً بك في Abosaraj Bot!\n\n"
        "أنا أحول القصة التي ترسلها إلى فيديو قصصي 🎬\n\n"
        "أرسل لي القصة مباشرة، وسأبدأ صناعة الفيديو.\n\n"
        "الأفضل أن تكون القصة واضحة ومليئة بالتفاصيل.\n\n"
        "الأوامر:\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 أوامر Abosaraj:\n\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة\n\n"
        "🎬 صناعة الفيديو:\n"
        "أرسل أي قصة أو فكرة، وسأحولها إلى فيديو عمودي."
    )


# =========================================================
# GROQ - CREATE SCENES
# =========================================================

def create_scenes(story: str):

    client = Groq(api_key=GROQ_API_KEY)

    system_prompt = f"""
أنت كاتب ومخرج فيديوهات قصيرة محترف.

مهمتك تحويل القصة التي يعطيك إياها المستخدم إلى {SCENE_COUNT}
مشاهد سينمائية مترابطة.

الفيديو النهائي سيكون عمودي 9:16 ومدته حوالي دقيقة أو أكثر.

مهم جدًا:

- لا تغير جوهر القصة.
- حافظ على الشخصيات.
- حافظ على تسلسل الأحداث.
- اجعل كل مشهد واضح بصريًا.
- اجعل المشاهد مناسبة لتوليد الصور بالذكاء الاصطناعي.
- كل مشهد مدته حوالي {SCENE_DURATION} ثوانٍ.
- يجب أن يكون لدينا {SCENE_COUNT} مشاهد.
- اكتب narration باللغة العربية.
- اكتب image_prompt باللغة الإنجليزية.
- لا تستخدم أسماء علامات تجارية أو مشاهير حقيقيين.
- لا تضع نصوصًا داخل الصور.
- اجعل الصورة سينمائية وواقعية.
- حافظ على مظهر الشخصيات بين المشاهد.

أخرج JSON فقط بهذا الشكل:

{{
  "title": "عنوان قصير",
  "scenes": [
    {{
      "scene": 1,
      "narration": "النص العربي الذي سيتم قراءته",
      "image_prompt": "Detailed cinematic image prompt in English"
    }}
  ]
}}
"""

    user_prompt = f"""
القصة:

{story}

حوّل هذه القصة إلى {SCENE_COUNT} مشاهد مترابطة.
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        temperature=0.8,
        max_tokens=7000,
    )

    text = response.choices[0].message.content.strip()

    # Remove markdown JSON fences if model adds them
    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    try:
        data = json.loads(text)
    except Exception as e:
        logger.error("Groq returned invalid JSON: %s", text)
        raise RuntimeError(
            f"Could not parse AI scenes: {e}"
        )

    scenes = data.get("scenes", [])

    if not scenes:
        raise RuntimeError("AI did not generate scenes.")

    # Force maximum 8 scenes
    scenes = scenes[:SCENE_COUNT]

    # If fewer than 8, duplicate last scene
