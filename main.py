import os, json, time, threading, re, tempfile
from pathlib import Path
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
# يقرأ من الاثنين PIAPI و KLING
_raw_key = os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","") or ""
PIAPI_API_KEY = _raw_key.strip().replace("\n","").replace(" ","").replace("\r","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq = None

app = Flask(__name__)
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

def log(m): print(f"[HMISC] {time.strftime('%H:%M:%S')} {m}", flush=True)

def http_get(url, **k):
    for _ in range(3):
        try: return requests.get(url, **k)
        except: time.sleep(1)
    raise

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
    if isinstance(data.get("output"), list) and data["output"]:
        return data["output"][0]
    return None

def download_file(url, path):
    path = Path(path)
    with http_get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_wait(tid, timeout=300):
    url = f"{PIAPI_BASE}/{tid}"
    headers = {"x-api-key": PIAPI_API_KEY}
    started = time.time()
    while True:
        if time.time()-started > timeout:
            raise TimeoutError(tid)
        r = http_get(url, headers=headers, timeout=30)
        if r.status_code == 401:
            raise RuntimeError("401 Unauthorized - التوكن غلط، جدد التوكن من piapi.ai")
        body = r.json()
        data = body.get("data") or body
        status = str(data.get("status","")).lower()
        if status in ("completed","success","succeed"):
            out = extract_url(body)
            if not out: raise RuntimeError(f"No output {str(body)[:1000]}")
            return out
        if status in ("failed","cancelled","error"):
            raise RuntimeError(f"PiAPI failed: {str(body)[:1500]}")
        time.sleep(3)

def generate_hikma():
    fallback = {
        "hikma": "لا تصنع هموماً لم يكتبها الله عليك",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك",
        "scene_en": "top: yellow bean character drowning with hands up, bottom: same character sitting calm on sandy seabed with bubbles"
    }
    if not groq: return fallback
    try:
        res = groq.chat.completions.create(
            model=GROQ_MODEL, temperature=0.8, max_completion_tokens=2000,
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":'JSON {"hikma":"حكمة 7 كلمات","full":"نص 30 كلمة","scene_en":"top bottom description for yellow bean"}'}]
        )
        j = json.loads(res.choices[0].message.content)
        return {"hikma": j.get("hikma", fallback["hikma"]), "full": j.get("full", fallback["full"]), "scene_en": j.get("scene_en", fallback["scene_en"])}
    except: return fallback

def generate_hmisc_piapi(hikma_short, scene_en):
    if not PIAPI_API_KEY:
        raise RuntimeError("PIAPI_API_KEY غير موجود في Render Environment")

    log(f"Token OK len={len(PIAPI_API_KEY)} start={PIAPI_API_KEY[:6]}...")

    prompt = f"hmisc style exact replica, vintage textured beige paper with stains, two-panel vertical comic, watercolor ink crosshatch, small yellow bean character thin black stick arms legs yellow watercolor body, {scene_en}, Arabic text '{hikma_short}', masterpiece NOT 3d NOT plastic"

    payload = {
        "model": "Qubico/flux1-dev",
        "task_type": "txt2img",
        "input": {"prompt": prompt[:1500], "width": 832, "height": 1216, "num_images": 1, "guidance_scale": 3.5, "num_inference_steps": 28}
    }
    headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
    log("Sending PiAPI...")

    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    if r.status_code == 401:
        log(f"401 Body: {r.text[:500]}")
        raise RuntimeError("401 Unauthorized - التوكن غلط أو منتهي. روح piapi.ai واعمل Create New Key")
    r.raise_for_status()
    tid = r.json().get("data",{}).get("task_id")
    if not tid: raise RuntimeError(f"No task_id {r.json()}")

    img_url = piapi_wait(tid, 300)
    tmp = Path(tempfile.gettempdir()) / f"hmisc_{int(time.time()*1000)}.jpg"
    download_file(img_url, tmp)
    return tmp

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
def telegram(method, data=None, files=None, timeout=60):
    r = requests.post(f"{TELEGRAM_API}/{method}", data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()
def send_text(chat_id, text):
    try: return telegram("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception as e: log(f"send_text {e}")
def send_photo(chat_id, path, caption=""):
    try:
        with Path(path).open("rb") as f:
            return telegram("sendPhoto", {"chat_id": chat_id, "caption": caption}, {"photo": f}, 120)
    except Exception as e: log(f"send_photo {e}")

processing=set()
plock=threading.Lock()

def process_hikma(chat_id):
    try:
        with plock:
            if chat_id in processing:
                send_text(chat_id, "⏳ ثواني..."); return
            processing.add(chat_id)
        send_text(chat_id, "🎨 بصنع hmisc أصلي... 25 ثانية")
        d = generate_hikma()
        img = generate_hmisc_piapi(d["hikma"], d["scene_en"])
        send_photo(chat_id, img, f"{d['hikma']}\n\n{d['full']}\n\n#hmisc")
        try: Path(img).unlink(missing_ok=True)
        except: pass
    except Exception as e:
        log(f"ERROR {repr(e)}")
        send_text(chat_id, f"❌ {str(e)[:1000]}")
    finally:
        with plock: processing.discard(chat_id)

def handle_update(update):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat: return
    if text.startswith("/start"):
        send_text(chat, "🎨 Hmisc PiAPI مربوط ✅\n/hikma - صورة\n/ping - فحص")
        return
    if text.startswith("/ping"):
        status = f"PiAPI key len={len(PIAPI_API_KEY)} - {'OK' if PIAPI_API_KEY else 'MISSING'}"
        send_text(chat, f"🟢 {status}")
        return
    if "/hikma" in text or text=="/test":
        threading.Thread(target=process_hikma, args=(chat,), daemon=True).start()
        return

@app.get("/")
def home(): return f"Hmisc PiAPI Alive - Key:{len(PIAPI_API_KEY)} chars", 200
@app.get("/health")
def health(): return {"ok": True, "piapi_len": len(PIAPI_API_KEY), "has_groq": bool(groq)}, 200
@app.post("/telegram/webhook")
def webhook():
    upd = request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try:
        requests.post(f"{TELEGRAM_API}/setWebhook", data={"url": RENDER_EXTERNAL_URL+"/telegram/webhook","drop_pending_updates":"true"})
    except: pass

if __name__ == "__main__":
    log(f"Starting - PiAPI len {len(PIAPI_API_KEY)} - BOT {len(BOT_TOKEN)}")
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
