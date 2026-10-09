import os, json, time, asyncio, shutil, tempfile, threading, subprocess, logging
from pathlib import Path
import requests
import edge_tts
from flask import Flask, request
from groq import Groq

# ========== ENV ==========
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY","")
FAL_KEY = os.getenv("FAL_KEY","") # optional fallback
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

def envbool(k,d): return os.getenv(k,str(d)).lower() in ("1","true","yes","on")
TEST_MODE = envbool("TEST_MODE", False)
LIPSYNC_ENABLED = envbool("LIPSYNC_ENABLED", True)
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE","face").lower()
LIPSYNC_EMOTION = os.getenv("LIPSYNC_EMOTION","neutral").lower()
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES","4"))
SOUND_DESIGN_ENABLED = envbool("SOUND_DESIGN_ENABLED", True)
MUSIC_ENABLED = envbool("MUSIC_ENABLED", True)

SHOT_COUNT=4; SHOT_DURATION=5; TOTAL_DURATION=20
VIDEO_WIDTH=480; VIDEO_HEIGHT=832; VIDEO_FPS=24
IMAGE_SIZE="480*832"

VOICE_CONFIG = {
 "male_lead": {"voice":"ar-SY-LaithNeural","rate":"-10%","pitch":"-3Hz"},
 "princess": {"voice":"ar-SA-ZariyahNeural","rate":"-6%","pitch":"+0Hz"},
 "king": {"voice":"ar-EG-ShakirNeural","rate":"-8%","pitch":"-4Hz"},
 "guard": {"voice":"ar-IQ-BasselNeural","rate":"-2%","pitch":"-1Hz"},
 "narrator": {"voice":"ar-SA-HamedNeural","rate":"-8%","pitch":"-2Hz"},
}

WAVESPEED_BASE="https://api.wavespeed.ai/api/v3"
IMAGE_MODEL="wavespeed-ai/z-image/turbo"
VIDEO_MODEL="wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL="sync/react-1"
SFX_MODEL="wavespeed-ai/mmaudio-v2"
MUSIC_MODEL="wavespeed-ai/ace-step/prompt-to-audio"

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)
logging_lock=threading.Lock()
processing_chats=set()
processing_lock=threading.Lock()

def log(m):
 with logging_lock: print(f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {m}", flush=True)
def auth_headers(): return {"Authorization":f"Bearer {WAVESPEED_API_KEY}","Content-Type":"application/json"}
def get_headers(): return {"Authorization":f"Bearer {WAVESPEED_API_KEY}"}
def http_get(url,**k):
 last=None
 for i in range(3):
  try: return requests.get(url,**k)
  except Exception as e: last=e; time.sleep(1.5*(i+1))
 raise last

def extract_output(v):
 if isinstance(v,str) and v.startswith(("http://","https://")): return v
 if isinstance(v,dict):
  for kk in ("url","audio_url","video_url","download_url","output_url"):
   it=v.get(kk)
   if isinstance(it,str) and it.startswith(("http://","https://")): return it
  for it in v.values():
   r=extract_output(it)
   if r: return r
 if isinstance(v,list):
  for it in v:
   r=extract_output(it)
   if r: return r
 return None

def wavespeed_submit(model,payload):
 if TEST_MODE: raise RuntimeError("TEST_MODE=true")
 url=f"{WAVESPEED_BASE}/{model}"
 r=requests.post(url
