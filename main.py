import os, json, time, threading, re, tempfile
from pathlib import Path
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
PIAPI_API_KEY = (os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","") or "").strip()
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

# Groq init آمن - ما يطفي البوت
groq = None
try:
    from groq import Groq
    if GROQ_API_KEY:
        groq = Groq(api_key=GROQ_API_KEY)
        print("[OK] Groq connected")
except Exception as e:
    print(f"[WARN] Groq failed: {e} - using fallback hikma")
    groq = None

app = Flask(__name__)
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

def log(m): print(f"[HMISC] {time.strftime('%H:%M:%S')} {m}", flush=True)

def extract_url(j):
    s = json.dumps(j)
    m = re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m: return m.group(0)
    data = j.get("data",{}) or {}
    out = data.get("output",{}) or {}
    if isinstance(out, dict):
        for k in ("image_url","url","image"):
            v = out.get(k)
            if isinstance(v, list) and v: return v[0]
            if isinstance(v, str) and v.startswith("http"): return v
    if isinstance(data.get("output"), list) and data["output"]:
        return data["output"][0]
    return None

def download_file(url, path):
    path = Path(path)
    with requests.get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_wait(tid):
    headers = {"X-API-KEY": PIAPI_API_KEY} # كابيتال مهم جدا
    url = f"{PIAPI_BASE}/{tid}"
    start = time.time()
    while True:
        if time.time()-start > 300:
            raise TimeoutError("timeout")
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 401:
            raise RuntimeError(f"401 Unauthorized - جدد التوكن من piapi.ai/workspace/api-keys")
        body = r.json()
        data = body.get("data") or body
        status = str(data.get("status","")).lower()
        log(f"poll {tid} -> {status}")
        if status in ("completed","complete","success","succeeded"):
            u = extract_url(body)
            if not u: raise RuntimeError(f"No image URL: {str(body)[:1000]}")
            return u
        if status in ("failed","failure","error","canceled","cancelled","rejected"):
            raise RuntimeError(f"Task failed: {str(body)[:1000]}")
        time.sleep(3)

def generate_hikma():
    fallback = {
        "hikma": "لا تصنع هموماً لم يكتبها الله عليك",
        "full": "لا تنتظر ما في أيدي الناس فما كتبه الله لك سيأتيك في حينه",
        "scene_en": "top: small yellow bean character drowning with hands up in distress, bottom: same yellow bean character sitting calm on sandy seabed with bubbles"
    }
    if not groq:
        return fallback
    try:
        res = groq.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.8,
            max_completion_tokens=2000,
            response_format={"type":"json_object"},
            messages=[{"role":"system","content":"You generate hmisc style wisdom. Return JSON only."},
                      {"role":"user","content":'Return JSON: {"hikma":"حكمة قصيرة 6-7 كلمات","full":"نص أطول 25-35 كلمة يشرح الحكمة","scene_en":"two panel description top bottom small yellow bean character"}'}]
        )
        j = json.loads(res.choices[0].message.content)
        return {
            "hikma": j.get("hikma", fallback["hikma"]),
            "full": j.get("full", fallback["full"]),
            "scene_en": j.get("scene_en", fallback["scene_en"])
        }
    except Exception as e:
        log(f"Groq gen failed {e}")
        return fallback

def generate_hmisc_piapi(hikma_short, scene_en):
    if not PIAPI_API_KEY:
        raise RuntimeError("PIAPI_API_KEY غير موجود في Environment في Render")
    log(f"Key len={len(PIAPI_API_KEY)} start={PIAPI_API_KEY[:6]}... balance 5.22$")

    prompt = f"hmisc style exact replica, vintage textured beige paper with brown stains, two-panel vertical comic layout, watercolor ink crosshatch shading, small yellow bean character with thin black stick arms and legs yellow watercolor body simple face, {scene_en}, Arabic text '{hikma_short}' handwritten, masterpiece, NOT 3d, NOT plastic, NOT glossy"

    payload = {
        "model": "Qubico/flux1-dev",
        "task_type": "txt2img",
        "input": {
            "prompt": prompt[:1500],
            "width": 832,
            "height": 1216,
            "num_images": 1,
            "guidance_scale": 3.5,
            "num_inference_steps": 28
        }
    }
    headers = {"X-API-KEY": PIAPI_API_KEY, "Content-Type": "application/json"}
    log("Sending task to PiAPI...")
    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    if r.status_code == 401:
        log(f"401 Body: {r.text[:800]}")
        raise RuntimeError("401 Unauthorized - روح piapi.ai/workspace/b/api-keys واعمل Copy للـ API Key من جديد")
    r.raise_for_status()
    j = r.json()
    tid = j.get("data",{}).get("task_id")
    if not tid:
        raise RuntimeError(f"No task_id returned: {j}")
    log(f"Task created {tid}")
    img_url = piapi_wait(tid)
    tmp = Path(tempfile.gettempdir()) / f"hmisc_{int(time.time()*1000)}.jpg"
    download_file(img_url, tmp)
    log(f"Downloaded {tmp}")
    return tmp

# Telegram
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
def send_text(chat_id, text):
    if not TELEGRAM_API: return
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        log(f"send_text err {e}")

def send_photo(chat_id, path, caption=""):
    if not TELEGRAM_API: return
    try:
        with Path(path).open("rb") as f:
            requests.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=120)
    except Exception as e:
        log(f"send_photo err {e}")

processing = set()
plock = threading.Lock()

def process_hikma(chat_id):
    try:
        with plock:
            if chat_id in processing:
                send_text(chat_id, "⏳ لسه بصنع وحدة...")
                return
            processing.add(chat_id)
        send_text(chat_id, "🎨 بصنع حكمة hmisc أصلية...\n⏳ 20-30 ثانية (Flux Dev)\n💰 رصيد 5.22 = ~65 صورة بهالجودة")
        d = generate_hikma()
        img = generate_hmisc_piapi(d["hikma"], d["scene_en"])
        send_photo(chat_id, img, f"{d['hikma']}\n\n{d['full']}\n\n#hmisc")
        try: Path(img).unlink(missing_ok=True)
        except: pass
    except Exception as e:
        log(f"process ERROR {repr(e)}")
        send_text(chat_id, f"❌ خطأ:\n{str(e)[:1000]}")
    finally:
        with plock:
            processing.discard(chat_id)

def handle_update(update):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat: return
    if text.startswith("/start"):
        send_text(chat, f"🎨 Hmisc Bot شغال ✅\nرصيد PiAPI: 5.22$\n/hikma - صورة جديدة\n/ping - فحص")
        return
    if text.startswith("/ping"):
        send_text(chat, f"🟢 شغال\nPiAPI: {len(PIAPI_API_KEY)} حرف - {'OK' if PIAPI_API_KEY else 'MISSING'}\nGroq: {'OK' if groq else 'fallback'}")
        return
    if "/hikma" in text or text in ("/test","hikma"):
        threading.Thread(target=process_hikma, args=(chat,), daemon=True).start()
        return

@app.get("/")
def home():
    return f"Hmisc Alive - PiAPI key len {len(PIAPI_API_KEY)} - Groq {bool(groq)}", 200

@app.get("/health")
def health():
    return {"ok": True, "piapi_len": len(PIAPI_API_KEY), "groq": bool(groq)}, 200

@app.post("/telegram/webhook")
def webhook():
    upd = request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try:
        requests.post(f"{TELEGRAM_API}/setWebhook", data={"url": RENDER_EXTERNAL_URL+"/telegram/webhook","drop_pending_updates":"true"}, timeout=20)
        log("Webhook set")
    except Exception as e:
        log(f"Webhook fail {e}")

if __name__ == "__main__":
    log(f"Starting port {PORT} key_len {len(PIAPI_API_KEY)} bot {len(BOT_TOKEN)}")
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
