import os
import json
import re
import uuid
import shutil
import logging
import threading
import subprocess
import asyncio
from pathlib import Path
from urllib.parse import urlparse

import requests
import fal_client

from flask import Flask
from gtts import gTTS

from telegram import Update
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

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

FAL_MODEL = os.getenv(
    "FAL_MODEL",
    "fal-ai/hunyuan-image/v3/text-to-image"
)

# ---------------------------------------------------------
# VIDEO
# ---------------------------------------------------------

SCENE_COUNT = 8
SCENE_DURATION = 8

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280

VIDEO_FPS = 30

BASE_DIR = Path("/tmp/abosaraj")
BASE_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK
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
    logger.info("Starting Flask server on port %s", PORT)

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

    logger.info("Environment variables OK")


def check_ffmpeg():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            raise RuntimeError("FFmpeg is not working.")

        first_line = result.stdout.splitlines()[0]
        logger.info("FFmpeg OK: %s", first_line)

    except FileNotFoundError:
        raise RuntimeError(
            "FFmpeg was not found inside the Docker container."
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🔥 أهلاً بك في Abosaraj Bot!\n\n"
        "أنا أحول القصة التي ترسلها إلى فيديو قصصي 🎬\n\n"
        "أرسل القصة مباشرة وسأبدأ العمل عليها.\n\n"
        "الفيديو سيكون عمودي 9:16 ومدته حوالي دقيقة.\n\n"
        "الأوامر:\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 Abosaraj Bot\n\n"
        "🎬 أرسل لي أي قصة وسأحولها إلى فيديو قصصي.\n\n"
        "الفيديو يتكون من عدة مشاهد مع:\n"
        "🎨 صور مولدة بالذكاء الاصطناعي\n"
        "🔊 تعليق صوتي عربي\n"
        "🎬 مونتاج تلقائي\n\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة"
    )


# =========================================================
# GROQ - CREATE SCENES
# =========================================================

def create_scenes(story: str):

    logger.info("Starting Groq scene generation")

    client = Groq(api_key=GROQ_API_KEY)

    system_prompt = f"""
أنت كاتب ومخرج فيديوهات قصصية قصيرة محترف.

حوّل القصة التي يعطيك إياها المستخدم إلى {SCENE_COUNT}
مشاهد سينمائية مترابطة.

المطلوب:

- الحفاظ على جوهر القصة.
- عدم
