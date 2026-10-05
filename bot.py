import os
import json
import asyncio
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI
from fal_client import subscribe

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from flask import Flask
import threading

# سيرفر وهمي عشان Render
web_app = Flask(__name__)
@web_app.route('/')
def home():
    return "Abosaraj Bot is Live!"

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

threading.Thread(target=run_web, daemon=True).start()
# =========================
# SETTINGS
# =========================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY missing")

if not FAL_KEY:
    raise RuntimeError("FAL_KEY missing")

os.environ["FAL_KEY"] = FAL_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

VIDEO_MODEL = "fal-ai/kling-video/o3/pro/text-to-video"

# =========================
# BASIC COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 أهلاً بك في AI Video Studio\n\n"
        "أرسل لي قصتك مباشرة، وأنا أحولها إلى فيديو.\n\n"
        "مثال:\n"
        "شاب فقير يعيش في قرية صغيرة، ويحلم أن يصبح غنياً..."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 طريقة الاستخدام:\n\n"
        "أرسل القصة فقط.\n\n"
        "مثال:\n"
        "كان هناك شاب يعيش في قرية...\n\n"
        "وسأقوم بـ:\n"
        "1️⃣ تقسيم القصة إلى مشاهد\n"
        "2️⃣ كتابة وصف سينمائي لكل مشهد\n"
        "3️⃣ توليد الفيديو\n"
        "4️⃣ عمل تعليق صوتي\n"
        "5️⃣ جمع المشاهد\n"
        "6️⃣ إرسال الفيديو النهائي لك"
    )


# =========================
# CREATE SCRIPT
# =========================

def create_scenes(story):

    prompt = f"""
You are a professional cinematic AI video director.

Convert the following story into exactly 8 cinematic video scenes.

The final video must be at least 60 seconds.

Each scene should be approximately 8-10 seconds.

IMPORTANT:
- Keep the same characters throughout the entire story.
- Describe character appearance consistently.
- Describe environment.
- Describe camera movement.
- Describe lighting.
- Describe emotion.
- Make every scene visually interesting.
- Do not add text inside the generated video.
- Do not add text inside the generated video.
- Do not change the story.
- Make prompts suitable for an AI video generator.

Return ONLY valid JSON.

Format:
{{
  "title": "short title",
  "narration": "complete narration in Arabic",
  "scenes": [
    {{
      "scene": 1,
      "duration": 10,
      "prompt": "cinematic English video prompt"
    }}
  ]
}}
Story:
{story}
"""

    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt
    )
