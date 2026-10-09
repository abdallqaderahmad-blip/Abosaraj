import os, json, time, shutil, tempfile, threading, subprocess
from pathlib import Path
import requests
from flask import Flask, request

def envbool(k,d):
    v = os.getenv(k, str(d)).lower()
    return v in ("1","true","yes","on")

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")
TEST_MODE = envbool("TEST_MODE", False)

print("BOOT TOKEN=" + str(bool(BOT_TOKEN)) + " WAVE=" + str(bool(WAVESPEED_API_KEY)), flush=True)

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    print("Groq OK", flush=True)
except Exception as e:
    print("WARN " + str(e), flush=True)
    groq = None

VIDEO_WIDTH = 480
VIDEO_HEIGHT = 832
VIDEO_FPS = 24
IMAGE_SIZE = "480*832"

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"
IMAGE_MODEL = "wavespeed-ai/z-image/turbo"

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()

def log(m):
    with lock:
        print("[REAL] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def auth_headers():
    return {"Authorization": "Bearer " + WAVESPEED_API_KEY, "Content-Type": "application/json"}
def get_headers():
    return {"Authorization": "Bearer " + WAVESPEED_API_KEY}

def http_get(url, **k):
    last=None
   
