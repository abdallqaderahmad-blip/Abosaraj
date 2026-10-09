import os, json, time, asyncio, shutil, tempfile, threading, subprocess
from pathlib import Path
import requests
import edge_tts
from flask import Flask, request
from groq import Groq

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

def envbool(k,d): return os.getenv(k,str(d)).lower() in ("1","true","yes","on")
TEST_MODE = envbool("TEST_MODE", False)
LIPSYNC_ENABLED = envbool("LIPSYNC_ENABLED", True)
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE","face").lower()
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES","4"))
SOUND_DESIGN_ENABLED = envbool("SOUND_DESIGN_ENABLED", True)
MUSIC_ENABLED = envbool("MUSIC_ENABLED", True)

SHOT_DURATION=5; TOTAL_DURATION=20
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
lock = threading.Lock()
processing_chats=set()
plock=threading.Lock()

def log(m):
    with lock: print(f"[ABOSARAJ-REAL] {time.strftime('%H:%M:%S')} {m}", flush=True)
def auth_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type":"application/json"}
def get_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}"}
def http_get(url, **k):
    last=None
    for i in range(3):
        try: return requests.get(url, **k)
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
    url = f"{WAVESPEED_BASE}/{model}"
    r = requests.post(url, headers=auth_headers(), json=payload, timeout=(15,90))
    r.raise_for_status()
    body=r.json(); data=body.get("data") or body; tid=data.get("id")
    if not tid: raise RuntimeError(json.dumps(body, ensure_ascii=False))
    log(f"Task {tid} -> {model}"); return tid
def wavespeed_wait(tid, timeout=900):
    started=time.time(); url=f"{WAVESPEED_BASE}/predictions/{tid}/result"
    while True:
        if time.time()-started>timeout: raise TimeoutError(tid)
        r=http_get(url, headers=get_headers(), timeout=30); r.raise_for_status()
        body=r.json(); data=body.get("data") or body; status=str(data.get("status","")).lower()
        log(f"Task {tid}: {status}")
        if status=="completed":
            out=extract_output(data.get("outputs") or data.get("output"))
            if not out: raise RuntimeError(json.dumps(body, ensure_ascii=False))
            return out
        if status in ("failed","cancelled","timeout","deleted"): raise RuntimeError(str(data.get("error") or body))
        time.sleep(2)
def upload_to_wavespeed(path):
    path=Path(path)
    r=requests.post(f"{WAVESPEED_BASE}/media/uploads", headers=auth_headers(), json={"filename":path.name,"size":path.stat().st_size}, timeout=30)
    r.raise_for_status(); body=r.json(); data=body.get("data") or {}; up=data.get("upload") or {}; uu=up.get("url"); du=data.get("download_url")
    if not uu or not du: raise RuntimeError(json.dumps(body, ensure_ascii=False))
    with path.open("rb") as f: rr=requests.put(uu, headers=up.get("headers") or {}, data=f, timeout=300); rr.raise_for_status()
    return du
def download_file(url, path):
    path=Path(path)
    with http_get(url, timeout=(15,300), stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

CAST_BIBLE = """
REAL HUMAN ACTORS, PHOTOREALISTIC, ULTRA REALISTIC, 8K, LIVE-ACTION, CINEMATIC, NOT CARTOON, NOT ANIME, NOT 3D, NOT ILLUSTRATION, NOT AI LOOK.
REAL SKIN TEXTURE, SKIN PORES, NATURAL EYES, REALISTIC HANDS, FIVE FINGERS, NATURAL LIGHT, REAL PEOPLE.
HERO: Real 29 year old Levantine handsome man, tall athletic, olive skin, dark wavy hair short, light beard, brown eyes, black coat leather armor, pretends weak, hides blue-white superpower energy inside, strong jawline, natural face.
PRINCESS: Real 24 year old Arab beautiful woman, olive skin, long brown hair, burgundy royal gown gold embroidery, real woman face, expressive eyes.
KING: Real 58 year old Arab man, broad, gray beard, dark royal robe, stern real face, wrinkles.
WOLF: Small realistic white-gray wolf pup, amber eyes, real fur, fluffy, loyal companion, photorealistic animal.
GIANT TIGER: ENORMOUS TIGER, size of TWO ELEPHANTS, 4 meters tall, 6 meters long, hyper realistic, orange black stripes, massive muscles, terrifying, real fur, real tiger face, huge paws.
STYLE: Vertical 9:16, fill full frame, no black borders, no black bars, no text, no subtitles in image, cinematic lighting, Netflix drama, emotional, realistic medieval palace and forest.
"""
REALISM_NEGATIVE = "cartoon, anime, 3d render, illustration, painting, plastic skin, doll face, deformed hands, extra fingers, blurry, low quality, black screen, black borders, black bars, letterbox, text, watermark, logo, subtitle, fake look, cgi, mannequin"

def create_story(user_idea):
    system_prompt = f"You are Netflix REAL HUMAN drama director. MANDATORY STYLE: {CAST_BIBLE} NEGATIVE: {REALISM_NEGATIVE} STORY 4 scenes x5s total 20s REAL HUMAN ACTORS ONLY. S1 King rejects hero who pretends weak in throne room realistic drama. S2 Princess meets hero secretly forest night with small white wolf pup love emotional real humans. S3 GIANT TIGER enormous size two elephants attacks palace guards flee critical terrifying roar hero glows blue-white superpower protect princess wolf. S4 Epic real fight hero vs giant tiger energy blast wins protects princess wolf king shocked apologizes respects. One speaker per scene only wolf never speaks Arabic dialogue max 7 words emotional short. Return JSON only with title and scenes action scene_image_prompt must include REAL HUMAN ACTORS PHOTOREALISTIC video_prompt camera sound music dialogue array speaker text emotion."
    user_prompt = f"USER IDEA: {user_idea} Create Netflix REAL HUMAN story All characters must be REAL PEOPLE not cartoon."
    for _ in range(4):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.35, max_completion_tokens=5000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}])
            story=json.loads(res.choices[0].message.content.strip()); scenes=story.get("scenes")
            if isinstance(scenes,list) and len(scenes)==4:
                for sc in scenes:
                    sc.setdefault("action",""); sc.setdefault("scene_image_prompt",""); sc.setdefault("video_prompt",""); sc.setdefault("camera",""); sc.setdefault("sound",""); sc.setdefault("music","")
                    dlg=sc.get("dialogue") or []; cd=[]
                    for it in dlg:
                        if isinstance(it,dict) and it.get("speaker") in VOICE_CONFIG and str(it.get("text","")).strip():
                            cd.append({"speaker":it.get("speaker"),"text":str(it.get("text")).
