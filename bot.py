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
    for i in range(3):
        try:
            return requests.get(url, **k)
        except Exception as e:
            last=e
            time.sleep(1)
    raise last

def extract_output(v):
    if isinstance(v,str) and v.startswith(("http://","https://")):
        return v
    if isinstance(v,dict):
        for kk in ("url","audio_url","video_url","download_url","output_url"):
            it=v.get(kk)
            if isinstance(it,str) and it.startswith(("http://","https://")):
                return it
        for it in v.values():
            r=extract_output(it)
            if r:
                return r
    if isinstance(v,list):
        for it in v:
            r=extract_output(it)
            if r:
                return r
    return None

def wavespeed_submit(model, payload):
    url=WAVESPEED_BASE + "/" + model
    log("Submit " + model + " " + json.dumps(payload)[:500])
    r=requests.post(url, headers=auth_headers(), json=payload, timeout=90)
    if r.status_code>=400:
        log("WAVESPEED ERROR " + str(r.status_code) + " " + r.text[:3000])
        r.raise_for_status()
    body=r.json()
    data=body.get("data") or body
    tid=data.get("id")
    if not tid:
        raise RuntimeError("no id " + json.dumps(body)[:1000])
    log("Task " + tid)
    return tid

def wavespeed_wait(tid, timeout=900):
    started=time.time()
    url=WAVESPEED_BASE + "/predictions/" + tid + "/result"
    while True:
        if time.time()-started>timeout:
            raise TimeoutError(tid)
        r=http_get(url, headers=get_headers(), timeout=30)
        r.raise_for_status()
        body=r.json()
        data=body.get("data") or body
        status=str(data.get("status","")).lower()
        log("Task " + tid + " " + status)
        if status=="completed":
            out=extract_output(data.get("outputs") or data.get("output"))
            if not out:
                raise RuntimeError("no output")
            return out
        if status in ("failed","cancelled","timeout","deleted"):
            raise RuntimeError(str(data.get("error") or body)[:3000])
        time.sleep(3)

def upload_to_wavespeed(path):
    path=Path(path)
    r=requests.post(WAVESPEED_BASE + "/media/uploads", headers=auth_headers(), json={"filename": path.name, "size": path.stat().st_size}, timeout=30)
    r.raise_for_status()
    body=r.json()
    data=body.get("data") or {}
    up=data.get("upload") or {}
    uu=up.get("url")
    du=data.get("download_url")
    log("Upload URL " + str(du)[:200])
    with path.open("rb") as f:
        rr=requests.put(uu, headers=up.get("headers") or {}, data=f, timeout=300)
        rr.raise_for_status()
    return du

def download_file(url, path):
    path=Path(path)
    with http_get(url, timeout=300, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c:
                    f.write(c)
    return path

def create_story(user_idea):
    system_prompt = "REAL HUMAN director 4 scenes S1 King rejects hero S2 Princess meets hero forest with small white wolf pup S3 GIANT TIGER enormous two elephants attacks palace hero blue power S4 Hero vs giant tiger wins Return JSON title scenes action scene_image_prompt video_prompt"
    user_prompt = "USER IDEA: " + user_idea
    for _ in range(4):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.35, max_completion_tokens=5000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}])
            story=json.loads(res.choices[0].message.content.strip())
            if len(story.get("scenes",[]))==4:
                return story
        except Exception as e:
            log("Groq fail " + str(e))
            time.sleep(1)
    raise RuntimeError("Story fail")

def run_cmd(cmd, timeout=300):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0:
        raise RuntimeError(p.stderr[-3000:])
    return p

def normalize_video(src, out, dur=5):
    vf=f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},setsar=1"
    run_cmd(["ffmpeg","-y","-i",str(src),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300)
    return out

def concat_videos(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos:
            f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-an","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300)
    return out

def generate_image(prompt, out):
    payload={"prompt": (prompt[:350] + " REAL HUMAN PHOTOREALISTIC 8K vertical 9:16 fill frame not cartoon"), "size": IMAGE_SIZE, "output_format": "jpeg"}
    tid=wavespeed_submit(IMAGE_MODEL, payload)
    url=wavespeed_wait(tid, 600)
    return download_file(url, out)

def generate_video(image_url, prompt, out):
    short_prompt = (prompt[:150] + " REAL HUMAN LIVE ACTION cinematic natural movement").strip()
    last_err=None
    attempts = [
        ("wavespeed-ai/wan-2.2/i2v-480p-ultra-fast", {"image": image_url, "prompt": short_prompt, "duration": 5}),
        ("wavespeed-ai/wan-2.2/i2v-480p", {"image": image_url, "prompt": short_prompt, "duration": 5}),
        ("bytedance/seedance-v1-pro-i2v-480p", {"image": image_url, "prompt": short_prompt, "duration": 5, "aspect_ratio": "9:16", "camera_fixed": False}),
        ("bytedance/seedance-v1-lite-i2v-480p", {"image": image_url, "prompt": short_prompt, "duration": 5, "aspect_ratio": "9:16", "camera_fixed": False}),
    ]
    for model, payload in attempts:
        try:
            log(f"Trying {model}")
            tid=wavespeed_submit(model, payload)
            url=wavespeed_wait(tid, 900)
            return download_file(url, out)
        except Exception as e:
            log(f"Model {model} FAILED: {str(e)[:1000]}")
            last_err=e
            continue
    raise last_err

TELEGRAM_API="https://api.telegram.org/bot" + BOT_TOKEN
def telegram(method, data=None, files=None, timeout=60):
    r=requests.post(TELEGRAM_API + "/" + method, data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()
def send_text(chat_id, text):
    return telegram("sendMessage", {"chat_id": chat_id, "text": text})
def send_video(chat_id, path, caption):
    with Path(path).open("rb") as f:
        return telegram("sendVideo", {"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, {"video": ("episode.mp4", f, "video/mp4")}, 300)

def process_story(chat_id, user_idea):
    work=Path(tempfile.mkdtemp(prefix="abosaraj_"))
    try:
        send_text(chat_id, "🎬 REAL HUMAN بدأ... 8K")
        story=create_story(user_idea)
        videos=[]
        for index, scene in enumerate(story["scenes"]):
            no=index+1
            log(f"SCENE {no}/4")
            img=work / f"scene_{no}.jpg"
            raw=work / f"scene_{no}_raw.mp4"
            norm=work / f"scene_{no}.mp4"
            generate_image(scene.get("scene_image_prompt","") or scene.get("action",""), img)
            iurl=upload_to_wavespeed(img)
            generate_video(iurl, scene.get("video_prompt","") or scene.get("action",""), raw)
            normalize_video(raw, norm, 5)
            videos.append(norm)
            send_text(chat_id, f"✅ مشهد {no}/4 جاهز")
        silent=work / "silent.mp4"
        concat_videos(videos, silent)
        final=work / "final.mp4"
        run_cmd(["ffmpeg","-y","-i",str(silent),"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(final)],120)
        send_text(chat_id, "✅ اكتمل " + story.get("title",""))
        send_video(chat_id, final, story.get("title","REAL HUMAN 8K"))
    except Exception as e:
        log("ERROR " + repr(e))
        try:
            send_text(chat_id, "❌ خطأ:\n" + str(e)[:3000])
        except:
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with plock:
            processing_chats.discard(chat_id)

def handle_update(update):
    msg=update.get("message") or {}
    chat=(msg.get("chat") or {}).get("id")
    text=(msg.get("text") or "").strip()
    if not chat:
        return
    if text=="/start":
        send_text(chat, "🎬 REAL HUMAN جاهز 8K\nابعت فكرة")
        return
    if text=="/ping":
        send_text(chat, "🟢 شغال")
        return
    if text=="/clear":
        with plock:
            processing_chats.clear()
        send_text(chat, "✅ تم مسح busy")
        return
    idea=text
    if text=="/test":
        idea="رجل حقيقي وسيم بقوة زرقاء يتظاهر بالضعف الملك يرفضه الاميرة تحبه ذئبة بيضاء صغيرة نمر عملاق بحجم فيلين 4 متر يهجم القصر البطل يكشف قوته ويهزمه"
    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج - ابعت /clear")
            return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, idea), daemon=True).start()

@app.get("/")
def home():
    return "REAL HUMAN Alive", 200
@app.get("/health")
def health():
    return {"ok": True}, 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if TEST_MODE:
        return
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN:
        return
    url=RENDER_EXTERNAL_URL + "/telegram/webhook"
    try:
        telegram("setWebhook", {"url": url, "drop_pending_updates": "true"})
        log("Webhook " + url)
    except Exception as e:
        log("Webhook fail " + str(e))

if __name__=="__main__":
    log("================================")
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
