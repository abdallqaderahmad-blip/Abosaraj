import os
import re
import json
import uuid
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests
import edge_tts

from flask import Flask
from groq import Groq

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b"
)

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY"
)

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "flux"
)


# =========================================================
# VIDEO SETTINGS
# =========================================================

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FPS = 30

# أول اختبار فقط
SCENE_COUNT = 2

MIN_VIDEO_SECONDS = 60
MAX_VIDEO_SECONDS = 120

VOICE = "ar-SA-HamedNeural"


# =========================================================
# WORK DIRECTORY
# =========================================================

WORK_DIR = Path("/tmp/abosaraj")

WORK_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# THREADING
# =========================================================

executor = ThreadPoolExecutor(
    max_workers=1
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("Abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():

    return "Abosaraj Story Video Engine is alive"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "version": "story-pollinations-v2",
        "video_engine": "Pollinations Image + FFmpeg",
        "scenes": SCENE_COUNT,
        "width": FINAL_WIDTH,
        "height": FINAL_HEIGHT
    }


def run_flask():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host
