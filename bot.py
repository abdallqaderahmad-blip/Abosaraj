import os
import json
import asyncio
import logging
import threading
import subprocess
import tempfile
import shutil

import requests
from flask import Flask
from gtts import gTTS
from groq import Groq
import fal_client

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

FAL_MODEL = os.getenv(
    "FAL_MODEL",
    "fal-ai/hunyuan-image/v3/text-to-image"
)


# =========================================================
# CHECK ENVIRONMENT VARIABLES
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing
