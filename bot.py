import os, json, time, shutil, tempfile, threading, subprocess, base64
from pathlib import Path
import requests
from flask import Flask, request

def envbool(k,d):
    v = os.getenv(k, str(d)).lower()
    return v in ("1","true","yes","on")

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PIAPI_API_KEY = os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","") or os.getenv("WAVESPEED_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")
TEST_MODE = envbool("TEST_MODE", False)

print("BOOT TOKEN=" + str(bool(BOT_TOKEN)) + " PIAPI=" + str(bool(PIAPI_API_KEY)), flush=True)

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    print("Groq OK", flush=True)
except Exception as e:
    print("WARN " + str(e), flush=True)
    groq = None

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 24

PIAPI_BASE = "https://api.piapi.ai/api/v1/task"

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()

def log(m):
    with lock:
        print("[KLING] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def http_get(url, **k):
    last=None
    for i in range(3):
        try:
            return requests.get(url, **k)
        except Exception as e:
            last=e
            time.sleep(1)
    raise last

def extract_mp4_from_json(j):
    import re
    s = json.dumps(j)
    m = re.search(r'https?://[^\s"\']+\.mp4[^\s"\']*', s)
    if m:
        return m.group(0)
    # try common fields
    data = j.get("data",{})
    out = data.get("output",{})
    if isinstance(out, dict):
        for kk in ("video_url","url","video","download_url"):
            if out.get(kk): return out.get(kk)
    if isinstance(out, list) and out:
        if isinstance(out[0], str): return out[0]
        if isinstance(out[0], dict): return out[0].get("url") or out[0].get("video_url")
    return None

def piapi_submit(model, payload_image_path, prompt, duration=5):
    b64 = base64.b64encode(Path(payload_image_path).read_bytes()).decode()
    payload = {
        "model": "kling",
        "task_type": "kling-v2-1-i2v",
        "input": {
            "image": f"data:image/jpeg;base64,{b64}",
            "prompt": prompt[:500],
            "duration": str(duration),
            "aspect_ratio": "9:16",
            "cfg_scale": 0.5
        }
    }
    headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
    log(f"Submit kling {prompt[:100]}")
    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=90)
    if r.status_code>=400:
        log(f"PIAPI ERROR {r.status_code} {r.text[:3000]}")
        r.raise_for_status()
    body=r.json()
    tid = body.get("data",{}).get("task_id") or body.get("task_id")
    if not tid:
        raise RuntimeError("no id " + json.dumps(body)[:1000])
    log(f"Task {tid}")
    return tid

def piapi_wait(tid, timeout=900):
    started=time.time()
    url = f"{PIAPI_BASE}/{tid}"
    headers = {"x-api-key": PIAPI_API_KEY}
    while True:
        if time.time()-started>timeout:
            raise TimeoutError(tid)
        r=http_get(url, headers=headers, timeout=30)
        r.raise_for_status()
        body=r.json()
        data=body.get("data") or body
        status=str(data.get("status","")).lower()
        log(f"Task {tid} {status}")
        if status in ("completed","success","succeed"):
            out = extract_mp4_from_json(body)
            if not out:
                raise RuntimeError("no output "+json.dumps(body)[:1000])
            return out
        if status in ("failed","cancelled","timeout","deleted"):
            raise RuntimeError(str(data.get("error") or body)[:3000])
        time.sleep(5)

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
    # مجاني 100% عبر Pollinations بدل WaveSpeed
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt[:400] + ' REAL HUMAN PHOTOREALISTIC 8K vertical 9:16 fill frame not cartoon')}"
    r=requests.get(url, timeout=120)
    r.raise_for_status()
    Path(out).write_bytes(r.content)
    return Path(out)

def generate_video(image_path, prompt, out):
    short_prompt = (prompt[:150] + " REAL HUMAN LIVE ACTION cinematic natural movement").strip()
    last_err=None
    try:
        log(f"Trying kling-v2-1-i2v")
        tid=piapi_submit("kling-v2-1-i2v", image_path, short_prompt, 5)
        url=piapi_wait(tid, 900)
        return download_file(url, out)
    except Exception as e:
        log(f"Model kling FAILED: {str(e)[:1000]}")
        last_err=e
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
        send_text(chat_id, "🎬 REAL HUMAN بدأ... 8K Kling")
        story=create_story(user_idea)
        videos=[]
        for index, scene in enumerate(story["scenes"]):
            no=index+1
            log(f"SCENE {no}/4")
            img=work / f"scene_{no}.jpg"
            raw=work / f"scene_{no}_raw.mp4"
            norm=work / f"scene_{no}.mp4"
            generate_image(scene.get("scene_image_prompt","") or scene.get("action",""), img)
            generate_video(img, scene.get("video_prompt","") or scene.get("action",""), raw)
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
        send_text(chat, "🎬 REAL HUMAN جاهز 8K Kling\nابعت فكرة")
        return
    if text=="/ping":
        send_text(chat, f"🟢 شغال PIAPI={bool(PIAPI_API_KEY)}")
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
