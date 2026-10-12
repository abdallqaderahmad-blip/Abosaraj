import os, json, time, threading, re, tempfile, shutil
from pathlib import Path
import requests
from flask import Flask, request

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","").strip()
PIAPI_API_KEY = (os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","")).strip()
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

if not BOT_TOKEN or not PIAPI_API_KEY:
    print("❌ لازم BOT_TOKEN و PIAPI_API_KEY")

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except:
    groq = None

app = Flask(__name__)
PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

# ===== Helpers =====
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
        for kk in ("image_url","url","image","images"):
            v = out.get(kk)
            if isinstance(v, list) and v: return v[0]
            if isinstance(v, str) and v.startswith("http"): return v
    # بعض الموديلات ترجع list مباشرة
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
            raise TimeoutError("Timeout "+tid)
        r = http_get(url, headers=headers, timeout=30)
        body = r.json()
        data = body.get("data") or body
        status = str(data.get("status","")).lower()
        if status in ("completed","success","succeed"):
            out = extract_url(body)
            if not out:
                raise RuntimeError(f"No output in {str(body)[:1000]}")
            return out
        if status in ("failed","cancelled","error"):
            raise RuntimeError(f"PiAPI failed: {str(body)[:1500]}")
        time.sleep(3)

# ===== توليد حكمة =====
def generate_hikma():
    fallback = {
        "hikma": "لا تصنع هموماً لم يكتبها الله عليك",
        "full": "لا تنتظر ما في أيدي الناس، فما كتبه الله لك سيأتيك، فاطمئن، رزقك لن يخطئ طريقه إليك",
        "scene_en": "top: yellow bean character drowning with hands up above water splashing, bottom: same yellow character sitting calm peaceful on sandy seabed underwater with bubbles and seaweed"
    }
    if not groq:
        return fallback
    try:
        prompt = """
        أنت كاتب hmisc. أرجع JSON فقط:
        {
          "hikma": "حكمة قصيرة مؤثرة 7 كلمات",
          "full": "نص طويل 25-30 كلمة يشرح الحكمة بأسلوب مطمئن",
          "scene_en": "وصف بالانجليزي لمشهدين top و bottom لشخصية صفراء bean مثل: drowning, sitting on sand, looking at sky, etc"
        }
        """
        res = groq.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.8,
            max_completion_tokens=2000,
            response_format={"type":"json_object"},
            messages=[{"role":"user","content":prompt}]
        )
        j = json.loads(res.choices[0].message.content)
        return {
            "hikma": j.get("hikma", fallback["hikma"]),
            "full": j.get("full", fallback["full"]),
            "scene_en": j.get("scene_en", fallback["scene_en"])
        }
    except Exception as e:
        log(f"Groq fail {e}")
        return fallback

# ===== توليد صورة hmisc بستايل أصلي 100% =====
def generate_hmisc_piapi(hikma_short, scene_en):
    # هذا الـ prompt السحري اللي بيجيب نفس ستايل hmisc الأصلي
    prompt = f"""
    hmisc style exact replica, masterpiece,
    vintage textured beige paper with old stains and paper grain,
    two-panel vertical comic layout,
    watercolor and ink illustration with crosshatch shading and ink outline,
    small yellow bean-shaped character with thin black stick arms and legs,
    soft watercolor yellow body with shading and crosshatch,
    {scene_en},
    Arabic calligraphy text at top '{hikma_short}',
    highly detailed, cozy melancholic, deep meaning, children's book illustration,
    NOT 3d, NOT clay, NOT plastic, NOT simple blob, NOT cartoon, watercolor painting
    """

    # نستخدم flux1-dev - هو اللي بيجيب hmisc، schnell زبالة
    payload = {
        "model": "Qubico/flux1-dev",
        "task_type": "txt2img",
        "input": {
            "prompt": prompt.strip()[:1500],
            "width": 832,
            "height": 1216,
            "num_images": 1,
            "guidance_scale": 3.5,
            "num_inference_steps": 28
        }
    }

    headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
    log(f"🎨 Sending PiAPI dev model... {hikma_short[:30]}")

    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    tid = data.get("data",{}).get("task_id")
    if not tid:
        raise RuntimeError(f"No task_id {data}")

    log(f"Task {tid} waiting...")
    img_url = piapi_wait(tid, 300)
    log(f"✅ Image URL {img_url[:80]}")

    tmp = Path(tempfile.gettempdir()) / f"hmisc_{int(time.time()*1000)}.jpg"
    download_file(img_url, tmp)
    return tmp

# ===== Telegram =====
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
def telegram(method, data=None, files=None, timeout=60):
    r = requests.post(f"{TELEGRAM_API}/{method}", data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()

def send_text(chat_id, text):
    try: return telegram("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception as e: log(f"send_text fail {e}")

def send_photo(chat_id, path, caption=""):
    try:
        with Path(path).open("rb") as f:
            return telegram("sendPhoto", {"chat_id": chat_id, "caption": caption}, {"photo": f}, 120)
    except Exception as e:
        log(f"send_photo fail {e}")

# ===== Logic =====
processing = set()
plock = threading.Lock()

def process_hikma(chat_id):
    work = None
    try:
        with plock:
            if chat_id in processing:
                send_text(chat_id, "⏳ بعمل وحدة، ثواني...")
                return
            processing.add(chat_id)

        send_text(chat_id, "🎨 بصنع حكمة hmisc أصلية...\n⏳ 20-30 ثانية (Flux Dev)\n💰 رصيد 20 رنجت = ~50 صورة بهالجودة")

        d = generate_hikma()
        log(f"Hikma: {d['hikma']}")

        img_path = generate_hmisc_piapi(d["hikma"], d["scene_en"])

        caption = f"{d['hikma']}\n\n{d['full']}\n\n#hmisc #حكمة"

        send_photo(chat_id, img_path, caption)
        send_text(chat_id, f"✅ جاهزة!\n/hikma للثانية\n/ping للرصيد")

        # تنظيف
        try: Path(img_path).unlink(missing_ok=True)
        except: pass

    except Exception as e:
        log(f"ERROR {repr(e)}")
        try: send_text(chat_id, f"❌ خطأ:\n{str(e)[:1000]}")
        except: pass
    finally:
        with plock: processing.discard(chat_id)

def handle_update(update):
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat:
        return

    if text.startswith("/start"):
        send_text(chat, "🎨 Hmisc Factory - PiAPI المدفوع ✅\n\nستايل vintage مائي أصلي 100%\n/hikma - يصنع حكمة بلوحتين\n/ping - حالة البوت")
        return

    if text.startswith("/ping"):
        send_text(chat, f"🟢 Online\nPiAPI: {'OK' if PIAPI_API_KEY else 'NO KEY'}\nGroq: {'OK' if groq else 'NO'}")
        return

    if "/hikma" in text or text == "/test":
        threading.Thread(target=process_hikma, args=(chat,), daemon=True).start()
        return

# ===== Flask =====
@app.get("/")
def home(): return "Hmisc PiAPI Bot Alive - 20RM Ready", 200

@app.get("/health")
def health(): return {"ok": True, "piapi": bool(PIAPI_API_KEY)}, 200

@app.post("/telegram/webhook")
def webhook():
    upd = request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN:
        return
    url = RENDER_EXTERNAL_URL + "/telegram/webhook"
    try:
        telegram("setWebhook", {"url": url, "drop_pending_updates": "true"})
        log(f"Webhook set {url}")
    except Exception as e:
        log(f"Webhook fail {e}")

if __name__ == "__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
