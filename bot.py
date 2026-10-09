import os, json, time, shutil, tempfile, threading, subprocess, base64, re
from pathlib import Path
import requests
from flask import Flask, request

def envbool(k,d):
    v = os.getenv(k, str(d)).lower()
    return v in ("1","true","yes","on")

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
PIAPI_API_KEY = os.getenv("PIAPI_API_KEY","") or os.getenv("KLING_API_KEY","")
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

def extract_url(j):
    s = json.dumps(j)
    m = re.search(r'https?://[^\s"\']+\.mp4[^\s"\']*', s)
    if m: return m.group(0)
    m2 = re.search(r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)[^\s"\']*', s)
    if m2: return m2.group(0)
    data = j.get("data",{})
    out = data.get("output",{})
    if isinstance(out, dict):
        for kk in ("video_url","url","video","download_url","image_url","image"):
            if out.get(kk): return out.get(kk)
    if isinstance(out, list) and out:
        if isinstance(out[0], str): return out[0]
        if isinstance(out[0], dict): return out[0].get("url") or out[0].get("video_url")
    return None

def download_file(url, path):
    path=Path(path)
    with http_get(url, timeout=300, stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

def piapi_wait(tid, timeout=900, kind="video"):
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
        log(f"Task {tid} {status} {kind}")
        if status in ("completed","success","succeed"):
            out = extract_url(body)
            if not out:
                raise RuntimeError("no output "+json.dumps(body)[:1000])
            return out
        if status in ("failed","cancelled","timeout","deleted"):
            raise RuntimeError(str(data.get("error") or body)[:2000])
        time.sleep(5)

def upload_to_catbox(path):
    path = Path(path)
    for name, func in [
        ("catbox", lambda: requests.post("https://catbox.moe/user/api.php", data={"reqtype":"fileupload"}, files={"fileToUpload": open(path,"rb")}, timeout=20)),
        ("0x0", lambda: requests.post("https://0x0.st", files={"file": open(path,"rb")}, timeout=20)),
        ("fileio", lambda: requests.post("https://file.io", files={"file": open(path,"rb")}, timeout=20)),
    ]:
        try:
            log(f"Trying {name}...")
            r = func()
            if r.status_code==200:
                txt = r.text.strip()
                if "http" in txt:
                    log(f"{name} OK {txt[:80]}")
                    return txt
                try:
                    link = r.json().get("link") or r.json().get("data",{}).get("url")
                    if link:
                        log(f"{name} OK {link[:80]}")
                        return link
                except: pass
        except Exception as e:
            log(f"{name} fail {e}")
    log("Using base64 fallback")
    return f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode()}"

def generate_image(prompt, out):
    payload = {
        "model": "Qubico/flux1-schnell",
        "task_type": "txt2img",
        "input": {
            "prompt": prompt[:500],
            "width": 720,
            "height": 1280,
            "num_images": 1
        }
    }
    headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=60)
    log(f"Flux submit {r.status_code}")
    r.raise_for_status()
    tid = r.json().get("data",{}).get("task_id")
    img_url = piapi_wait(tid, 300, "image")
    return download_file(img_url, out)

def generate_video(image_path, prompt, out):
    img_url = upload_to_catbox(image_path)
    log(f"Upload {img_url[:80]}")
    payload = {
        "model": "kling",
        "task_type": "video_generation",
        "input": {
            "prompt": prompt[:500],
            "image_url": img_url,
            "duration": "5",
            "aspect_ratio": "9:16"
        }
    }
    headers = {"x-api-key": PIAPI_API_KEY, "Content-Type":"application/json"}
    r = requests.post(PIAPI_BASE, headers=headers, json=payload, timeout=90)
    log(f"Kling submit {r.status_code}")
    r.raise_for_status()
    tid = r.json().get("data",{}).get("task_id")
    vurl = piapi_wait(tid, 900, "video")
    return download_file(vurl, out)

def create_story(user_idea):
    system_prompt = """
You are CINEMATIC NETFLIX director. Create 4 scenes ULTRA MODERN.

STORY CORE:
Handsome real man with hidden blue power pretends weak. King humiliates him. Princess loves him. Forest with small white wolf pup. GIANT TIGER 4m like two elephants attacks palace. Hero reveals power.

REQUIREMENTS:
- Characters: near-human realistic models, not cartoon, photorealistic but stylized like high-end CGI (like Arcane/Love Death Robots) - beautiful humans
- Lighting: volumetric lighting, god rays, cinematic fog, atmospheric particles, dramatic shadows, Netflix quality
- Each scene MUST have dialogue - character talking
- Add Netflix-style translation: Arabic and English subtitles

Return JSON:
{
 "title": "title",
 "scenes": [
  {
   "action": "description",
   "scene_image_prompt": "REAL HUMAN near-human model photorealistic beautiful man/woman detailed face, cinematic volumetric lighting god rays fog atmosphere particles dramatic shadows 8K vertical 9:16... SCENE SPECIFIC",
   "video_prompt": "REAL HUMAN near-human model talking character mouth moving speaking, cinematic camera dolly, volumetric light atmosphere fog particles, emotional performance... SCENE SPECIFIC",
   "dialogue_ar": "حوار عربي قصير 8 كلمات",
   "dialogue_en": "short english dialogue 8 words",
   "character": "hero or king or princess or tiger"
  }
 ]
}

Scenes:
S1: throne room volumetric light dust particles king shouting, hero sad but hiding power
S2: forest night moonlight god rays fog, princess and hero close, small white wolf pup between them romantic dialogue
S3: palace gate giant tiger 4m smashing cinematic disaster volumetric smoke debris atmosphere
S4: hero blue power glowing volumetric energy, fights tiger epic slow motion particles, princess hug

Make dialogue emotional dramatic like Netflix.
"""
    for _ in range(3):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.5, max_completion_tokens=6000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":"IDEA: "+user_idea}])
            story=json.loads(res.choices[0].message.content.strip())
            if len(story.get("scenes",[]))==4:
                return story
        except Exception as e:
            log("Groq fail "+str(e))
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

def add_netflix_subtitles(video_path, dialogue_ar, dialogue_en, out_path):
    # ترجمة نتفلكس ستايل: أبيض مع أسود، خط واضح، أسفل
    # نستخدم drawtext مرتين عربي + انجليزي
    # نهرب النص
    def esc(t):
        return t.replace(":", "\\:").replace("'", "").replace('"',"")
    ar = esc(dialogue_ar or "")
    en = esc(dialogue_en or "")
    # لو ما في خط عربي في السيرفر نستخدم DejaVu
    vf = f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{ar}':fontcolor=white:fontsize=32:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-120,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{en}':fontcolor=yellow:fontsize=24:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-80"
    try:
        run_cmd(["ffmpeg","-y","-i",str(video_path),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)],60)
        return out_path
    except Exception as e:
        log(f"Subtitle fail {e} - using video without sub")
        shutil.copy(video_path, out_path)
        return out_path

def concat_videos(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos:
            f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300)
    return out

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
    work=Path(tempfile.mkdtemp(prefix="netflix_"))
    try:
        send_text(chat_id, "🎬 Netflix دراما بدأ\n✨ إضاءة سينمائية + ضباب + جزيئات\n💬 شخصيات تتكلم + ترجمة نتفلكس\n🧑 نماذج قريبة من البشر CGI فاخر")
        story=create_story(user_idea)
        videos=[]
        for index, scene in enumerate(story["scenes"]):
            no=index+1
            log(f"SCENE {no}/4 {scene.get('character')} - {scene.get('dialogue_ar')}")
            img=work / f"scene_{no}.jpg"
            raw=work / f"scene_{no}_raw.mp4"
            norm=work / f"scene_{no}.mp4"
            subbed=work / f"scene_{no}_sub.mp4"

            # 1. صورة سينمائية بإضاءة وأجواء
            generate_image(scene.get("scene_image_prompt",""), img)

            # 2. فيديو شخصية تتحدث + حركة
            generate_video(img, scene.get("video_prompt",""), raw)
            normalize_video(raw, norm, 5)

            # 3. ترجمة نتفلكس
            add_netflix_subtitles(norm, scene.get("dialogue_ar",""), scene.get("dialogue_en",""), subbed)

            videos.append(subbed)
            send_text(chat_id, f"✅ مشهد {no}/4 جاهز\n🎭 {scene.get('character')}: {scene.get('dialogue_ar')}")

        silent=work / "silent.mp4"
        concat_videos(videos, silent)
        final=work / "final.mp4"
        run_cmd(["ffmpeg","-y","-i",str(silent),"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",str(final)],120)
        send_text(chat_id, "✅ فيلم Netflix اكتمل: " + story.get("title",""))
        send_video(chat_id, final, f"🎬 {story.get('title','')} - Netflix Drama - إضاءة سينمائية + ترجمة")
    except Exception as e:
        log("ERROR " + repr(e))
        try:
            send_text(chat_id, "❌ خطأ:\n" + str(e)[:2000])
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
    if not chat: return
    if text=="/start":
        send_text(chat, "🎬 Netflix بوت\n✨ إضاءة سينمائية + ضباب\n💬 شخصيات تتكلم + ترجمة\n🧑 نماذج بشرية فاخرة CGI\n\nالقصة: بطل قوة زرقاء، ملك، أميرة، ذئبة بيضاء، نمر عملاق\n/test للتجربة")
        return
    if text=="/ping":
        send_text(chat, f"🟢 Netflix Bot شغال")
        return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat, "✅ تم مسح busy")
        return
    idea=text
    if text=="/test":
        idea="رجل حقيقي وسيم بقوة زرقاء مخفية يتظاهر بالضعف الملك يهينه الأميرة تحبه في الغابة مع ذئبة بيضاء صغيرة نمر عملاق بحجم فيلين 4 متر يهجم البطل يكشف قوته دراما نتفلكس سينمائية"
    with plock:
        if chat in processing_chats:
            send_text(chat, "⏳ في انتاج")
            return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, idea), daemon=True).start()

@app.get("/")
def home(): return "Netflix Drama Bot Alive", 200
@app.get("/health")
def health(): return {"ok": True}, 200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK", 200

def setup_webhook():
    if TEST_MODE: return
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
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
