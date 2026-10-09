import os, json, time, asyncio, shutil, tempfile, threading, subprocess
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
LIPSYNC_ENABLED = envbool("LIPSYNC_ENABLED", False)
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE","face").lower()
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES","1"))

print("BOOT TOKEN=" + str(bool(BOT_TOKEN)) + " GROQ=" + str(bool(GROQ_API_KEY)) + " WAVE=" + str(bool(WAVESPEED_API_KEY)), flush=True)

try:
    import edge_tts
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    print("Groq OK", flush=True)
except Exception as e:
    print("WARN " + str(e), flush=True)
    groq = None
    edge_tts = None

SHOT_DURATION = 5
VIDEO_WIDTH = 480
VIDEO_HEIGHT = 832
VIDEO_FPS = 24
IMAGE_SIZE = "480*832"

VOICE_CONFIG = {
    "male_lead": {"voice": "ar-SY-LaithNeural", "rate": "-10%", "pitch": "-3Hz"},
    "princess": {"voice": "ar-SA-ZariyahNeural", "rate": "-6%", "pitch": "+0Hz"},
    "king": {"voice": "ar-EG-ShakirNeural", "rate": "-8%", "pitch": "-4Hz"},
}

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"
IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL = "sync/react-1"

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
    log("Submit " + model + " payload=" + json.dumps(payload)[:500])
    r=requests.post(url, headers=auth_headers(), json=payload, timeout=90)
    if r.status_code>=400:
        log("WAVESPEED ERROR " + str(r.status_code) + " " + r.text[:2000])
        r.raise_for_status()
    body=r.json()
    data=body.get("data") or body
    tid=data.get("id")
    if not tid:
        raise RuntimeError(json.dumps(body, ensure_ascii=False)[:2000])
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
                raise RuntimeError(json.dumps(body, ensure_ascii=False)[:2000])
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

CAST_BIBLE = "REAL HUMAN ACTORS PHOTOREALISTIC ULTRA REALISTIC 8K LIVE-ACTION CINEMATIC NOT CARTOON NOT ANIME NOT 3D REAL SKIN TEXTURE SKIN PORES NATURAL EYES REALISTIC HANDS FIVE FINGERS HERO Real 29 year old Levantine handsome man tall athletic olive skin dark wavy hair short light beard brown eyes black coat leather armor blue-white superpower PRINCESS Real 24 year old Arab beautiful woman olive skin long brown hair burgundy royal gown KING Real 58 year old Arab man gray beard royal robe WOLF Small realistic white-gray wolf pup GIANT TIGER ENORMOUS TIGER size of TWO ELEPHANTS 4 meters tall 6 meters long hyper realistic orange black stripes massive terrifying real fur STYLE Vertical 9:16 fill full frame no black borders no text cinematic lighting Netflix drama"

def create_story(user_idea):
    if not groq:
        raise RuntimeError("GROQ missing")
    system_prompt = "You are Netflix REAL HUMAN director. STYLE: " + CAST_BIBLE + " Create 4 scenes x5s total 20s REAL HUMAN ONLY. S1 King rejects hero throne. S2 Princess meets hero forest night with small white wolf pup love. S3 GIANT TIGER enormous two elephants attacks palace hero blue power protects princess wolf. S4 Epic fight hero vs giant tiger wins king shocked. Return JSON title scenes with action, scene_image_prompt, video_prompt, dialogue max 7 words, one speaker per scene."
    user_prompt = "USER IDEA: " + user_idea + " REAL HUMAN ONLY"
    for _ in range(4):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.35, max_completion_tokens=5000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}])
            story=json.loads(res.choices[0].message.content.strip())
            scenes=story.get("scenes")
            if isinstance(scenes,list) and len(scenes)==4:
                return story
        except Exception as e:
            log("Groq fail " + str(e))
            time.sleep(1)
    raise RuntimeError("Story fail")

async def tts_async(text, config, output):
    com=edge_tts.Communicate(text=text, voice=config["voice"], rate=config["rate"], pitch=config["pitch"])
    await com.save(str(output))

def make_tts(text, speaker, output):
    asyncio.run(tts_async(text, VOICE_CONFIG[speaker], output))
    return output

def run_cmd(cmd, timeout=300):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0:
        raise RuntimeError(p.stderr[-3000:])
    return p

def fit_audio(src, out, dur):
    af="apad,atrim=0:" + str(dur) + ",asetpts=N/SR/TB"
    run_cmd(["ffmpeg","-y","-i",str(src),"-af",af,"-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)],120)
    return out

def normalize_video(src, out, dur=5):
    vf="scale=" + str(VIDEO_WIDTH) + ":" + str(VIDEO_HEIGHT) + ":force_original_aspect_ratio=increase,crop=" + str(VIDEO_WIDTH) + ":" + str(VIDEO_HEIGHT) + ",fps=" + str(VIDEO_FPS) + ",setsar=1"
    run_cmd(["ffmpeg","-y","-i",str(src),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300)
    return out

def concat_videos(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos:
            safe=str(v).replace("'", "_")
            f.write("file '" + safe + "'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-an","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300)
    return out

def generate_image(prompt, out):
    payload={
        "prompt": CAST_BIBLE + " IMAGE: " + prompt,
        "size": IMAGE_SIZE,
        "output_format": "jpeg"
    }
    tid=wavespeed_submit(IMAGE_MODEL, payload)
    url=wavespeed_wait(tid, 600)
    return download_file(url, out)

def generate_video(image_url, prompt, out):
    payload={
        "image": image_url,
        "prompt": prompt + " REAL HUMAN ACTORS PHOTOREALISTIC 8K LIVE-ACTION vertical video 9:16 fill full frame no black borders natural movement realistic skin pores realistic hands",
        "duration": 5
    }
    tid=wavespeed_submit(VIDEO_MODEL, payload)
    url=wavespeed_wait(tid, 900)
    return download_file(url, out)

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
        send_text(chat_id, "🎬 REAL HUMAN بدأ...\n👤 8K ممثلين حقيقيين\n🐯 نمر عملاق بحجم فيلين")
        story=create_story(user_idea)
        videos=[]
        for index, scene in enumerate(story["scenes"]):
            no=index+1
            log("SCENE " + str(no) + "/4")
            img=work / ("scene_" + str(no) + ".jpg")
            raw=work / ("scene_" + str(no) + "_raw.mp4")
            norm=work / ("scene_" + str(no) + ".mp4")
            generate_image(scene.get("scene_image_prompt","") or scene.get("action",""), img)
            iurl=upload_to_wavespeed(img)
            generate_video(iurl, scene.get("video_prompt","") or scene.get("action",""), raw)
            normalize_video(raw, norm, 5)
            videos.append(norm)
            send_text(chat_id, "✅ مشهد " + str(no) + "/4 جاهز")
        silent=work / "silent.mp4"
        concat_videos(videos, silent)
        final=work / "final.mp4"
        run_cmd(["ffmpeg","-y","-i",str(silent),"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(final)],120)
        send_text(chat_id, "✅ اكتمل REAL HUMAN\n🎬 " + story.get("title",""))
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
        send_text(chat, "🎬 REAL HUMAN جاهز\n👤 ممثلين حقيقيين 8K\n🐯 نمر عملاق\nابعت فكرة")
        return
    if text=="/ping":
        send_text(chat, "🟢 شغال REAL HUMAN")
        return
    if text=="/clear":
        with plock:
            processing_chats.clear()
        send_text(chat, "✅ تم مسح busy")
        return
    idea=text
    if text=="/test":
        idea="رجل حقيقي غامض وسيم بقوة خارقة زرقاء يتظاهر بالضعف امام الملك الحقيقي الذي يرفضه لحب ابنته الاميرة الحقيقية الجميلة لديه ذئبة بيضاء صغيرة موالفة واقعية فجأة يهجم نمر ضخم جدا بحجم فيلين 4 متر على القصر في اللحظة الحاسمة يكشف البطل عن قوته الحقيقية المخفية ويواجه النمر العملاق ويهزمه ليحمي الاميرة دراما نتفلكس واقعية جدا ممثلين حقيقيين"
    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج شغال - ابعت /clear لو علق")
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
        log("TEST_MODE true")
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
