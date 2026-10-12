import os, json, time, threading, re, tempfile
from pathlib import Path
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
PIAPI_API_KEY = (os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","")).strip()
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

from groq import Groq
groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = Flask(__name__)
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

def log(m): print(f"[HMISC] {m}", flush=True)

def extract_url(j):
    s = json.dumps(j)
    m = re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    data = j.get("data",{}) or {}
    out = data.get("output",{})
    if isinstance(out, dict):
        for kk in ("image_url","url","image"):
            v = out.get(kk)
            if isinstance(v, list) and v: return v[0]
            if isinstance(v, str) and v.startswith("http"): return v
    return None

def download_file(url, path):
    path=Path(path)
    with requests.get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_wait(tid):
    # هون كان الخطأ - لازم X-API-KEY كابيتال
    headers = {"X-API-KEY": PIAPI_API_KEY}
    url = f"{PIAPI_BASE}/{tid}"
    for _ in range(120):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code==401:
            raise RuntimeError(f"401 Unauthorized - راجع التوكن. Body: {r.text[:300]}")
        body = r.json()
        data = body.get("data") or body
        status = str(data.get("status","")).lower()
        log(f"poll {status}")
        if status in ("completed","complete","success","succeeded"):
            u = extract_url(body)
            if not u: raise RuntimeError(f"No URL {body}")
            return u
        if status in ("failed","failure","error","canceled","cancelled","rejected"):
            raise RuntimeError(f"Failed {body}")
        time.sleep(3)
    raise TimeoutError(tid)

def gen_hmisc():
    fallback = {
        "hikma": "لا تصنع هموماً لم يكتبها الله عليك",
        "full": "ما كتبه الله لك سيأتيك",
        "scene": "top yellow bean drowning hands up, bottom same bean sitting calm on sandy seabed bubbles"
    }
    if not groq: return fallback
    try:
        res = groq.chat.completions.create(
            model=GROQ_MODEL, temperature=0.8,
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":'{"hikma":"7 كلمات","full":"30 كلمة","scene":"english top bottom yellow bean"}'}]
        )
        return json.loads(res.choices[0].message.content)
    except: return fallback

def create_task(hikma, scene):
    headers = {
        "X-API-KEY": PIAPI_API_KEY, # <--- كابيتال مهم
        "Content-Type": "application/json"
    }
    prompt = f"hmisc style exact replica vintage beige paper watercolor ink crosshatch small yellow bean thin black limbs {scene} Arabic text '{hikma}' masterpiece NOT 3d"
    payload = {
        "model": "Qubico/flux1-dev",
        "task_type": "txt2img",
        "input": {"prompt": prompt, "width": 832, "height": 1216, "num_images": 1, "guidance_scale": 3.5, "num_inference_steps": 28}
    }
    log(f"POST {PIAPI_BASE} key_len={len(PIAPI_API_KEY)}")
    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    if r.status_code==401:
        log(f"401 {r.text[:500]}")
        raise RuntimeError("401 - التوكن غلط. روح piapi.ai/workspace/b/api-keys وانسخ الـ API Key من جديد")
    r.raise_for_status()
    tid = r.json().get("data",{}).get("task_id")
    log(f"task_id {tid}")
    return tid

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
def send_text(cid, t): requests.post(f"{TELEGRAM_API}/sendMessage", data={"chat_id":cid,"text":t}, timeout=20)
def send_photo(cid, path, cap):
    with open(path,"rb") as f:
        requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id":cid,"caption":cap}, files={"photo":f}, timeout=120)

processing=set()
def process(cid):
    try:
        if cid in processing: return
        processing.add(cid)
        send_text(cid, "🎨 بصنع hmisc... 25 ثانية")
        d = gen_hmisc()
        tid = create_task(d["hikma"], d["scene"])
        url = piapi_wait(tid)
        p = Path(tempfile.gettempdir())/f"h_{int(time.time())}.jpg"
        download_file(url, p)
        send_photo(cid, p, f"{d['hikma']}\n\n{d['full']}\n\n#hmisc")
        p.unlink(missing_ok=True)
    except Exception as e:
        log(f"ERR {e}")
        send_text(cid, f"❌ {str(e)[:800]}")
    finally:
        processing.discard(cid)

@app.get("/")
def home(): return f"OK key={len(PIAPI_API_KEY)}",200
@app.post("/telegram/webhook")
def wh():
    upd=request.get_json(silent=True) or {}
    msg=upd.get("message") or {}
    chat=(msg.get("chat") or {}).get("id")
    txt=(msg.get("text") or "")
    if chat and ("/hikma" in txt or "/start" in txt):
        threading.Thread(target=process, args=(chat,), daemon=True).start()
    return "OK",200

if __name__=="__main__":
    if RENDER_EXTERNAL_URL and BOT_TOKEN:
        try: requests.post(f"{TELEGRAM_API}/setWebhook", data={"url": RENDER_EXTERNAL_URL+"/telegram/webhook"})
        except: pass
    app.run(host="0.0.0.0", port=PORT)
