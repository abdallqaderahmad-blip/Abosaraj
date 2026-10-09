import os, re, json, time, asyncio, logging, tempfile, subprocess, threading
from pathlib import Path
from typing import Any

import requests
import edge_tts
from flask import Flask, request, jsonify
from groq import Groq

# ============================================================
# ABOSARAJ — safe-by-default Telegram story-to-reel bot
# TEST_MODE=true means: test Telegram + Groq story generation only.
# It NEVER submits paid WaveSpeed jobs while TEST_MODE is true.
# ============================================================

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("abosaraj")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("1", "true", "yes", "on")
ENABLE_PAID_VIDEO = os.getenv("ENABLE_PAID_VIDEO", "false").lower() in ("1", "true", "yes", "on")
VIDEO_MODEL = os.getenv("WAVESPEED_VIDEO_MODEL", "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast").strip()
IMAGE_MODEL = os.getenv("WAVESPEED_IMAGE_MODEL", "wavespeed-ai/z-image/turbo").strip()
SCENE_COUNT = 4
SCENE_SECONDS = 5
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/abosaraj"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
WAVESPEED_API = "https://api.wavespeed.ai/api/v3"

CHARACTERS = """
Continuity bible: one mysterious, attractive, superpowered adult male hero; one adult princess; one king; one guard; one small white wolf that never speaks. Keep their faces, hair, clothes, age, and colors consistent in every scene. Live-action photorealistic fantasy cinema, realistic skin and fabric, cinematic lighting, natural body/camera motion, no cartoon/anime, no text or logos in generated footage. Vertical social-video composition.
""".strip()

VOICE = {
    "male_lead": ("ar-SY-LaithNeural", "-10%", "-3Hz"),
    "princess": ("ar-SA-ZariyahNeural", "-6%", "+1Hz"),
    "king": ("ar-EG-ShakirNeural", "-8%", "-4Hz"),
    "guard": ("ar-IQ-BasselNeural", "-2%", "-1Hz"),
    "narrator": ("ar-SA-HamedNeural", "-8%", "-2Hz"),
}

SYSTEM_PROMPT = f"""You are the story editor for Abosaraj, an original Arabic cinematic fantasy short-drama series.
Return ONLY valid JSON. Create exactly {SCENE_COUNT} connected scenes, each exactly {SCENE_SECONDS} seconds.
Structure: scene 1 HOOK; scene 2 ESCALATION; scene 3 REVEAL_OR_DANGER; scene 4 CLIFFHANGER. Do not resolve the whole story.
Use the continuity bible: {CHARACTERS}
No graphic violence, no subtitles embedded into images, no text overlays in generated footage. Dialogue must be concise Arabic, naturally speakable in about 3-4 seconds. Only one speaker per scene. Allowed speaker values: male_lead, princess, king, guard, narrator. The wolf never speaks.
Schema: {{"title":"Arabic short title","logline":"one sentence","scenes":[{{"number":1,"beat":"HOOK","visual_prompt":"Detailed English visual prompt for one shot; include character continuity and motion","dialogue_ar":"Arabic dialogue","speaker":"male_lead","sfx_prompt":"short English sound design description","music_mood":"short English music mood"}}]}}
Use beats exactly HOOK, ESCALATION, REVEAL_OR_DANGER, CLIFFHANGER in order. Do not add fields. Avoid long dialogue. Keep visual prompts specific and filmable."""


def tg(method: str, payload=None, timeout=25):
    if not TELEGRAM_API:
        raise RuntimeError("BOT_TOKEN غير مضبوط في Environment Variables")
    r = requests.post(f"{TELEGRAM_API}/{method}", json=payload or {}, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Telegram returned non-JSON HTTP {r.status_code}")
    if not r.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {str(data)[:500]}")
    return data.get("result")


def send_message(chat_id, text, reply_markup=None):
    # Telegram message limit is 4096 characters.
    text = str(text or "")
    for i in range(0, len(text), 3900):
        payload = {"chat_id": chat_id, "text": text[i:i+3900], "disable_web_page_preview": True}
        if reply_markup and i == 0:
            payload["reply_markup"] = reply_markup
        tg("sendMessage", payload)


def safe_json_from_text(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end+1])
        raise ValueError("النموذج لم يرجع JSON صالحًا")


def validate_story(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("القصة ليست كائن JSON")
    scenes = obj.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
        raise ValueError(f"لازم القصة تحتوي بالضبط {SCENE_COUNT} مشاهد")
    beats = ["HOOK", "ESCALATION", "REVEAL_OR_DANGER", "CLIFFHANGER"]
    allowed = set(VOICE)
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError(f"المشهد {i+1} غير صالح")
        scene["number"] = i + 1
        scene["beat"] = beats[i]
        for field in ("visual_prompt", "dialogue_ar", "speaker", "sfx_prompt", "music_mood"):
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                raise ValueError(f"المشهد {i+1}: الحقل {field} ناقص")
        if scene["speaker"] not in allowed:
            scene["speaker"] = "narrator"
        scene["visual_prompt"] = (scene["visual_prompt"] + ". " + CHARACTERS + ". Vertical 9:16 cinematic live-action fantasy shot, no text, no subtitles, no logos.")[:3000]
        scene["dialogue_ar"] = scene["dialogue_ar"].strip()[:350]
    obj["title"] = str(obj.get("title") or "حكاية غامضة")[:100]
    obj["logline"] = str(obj.get("logline") or "سرّ يغيّر مصير المملكة.")[:500]
    obj["scenes"] = scenes
    return obj


def generate_story(user_story: str) -> dict:
    if not client:
        raise RuntimeError("GROQ_API_KEY غير مضبوط في Environment Variables")
    user_story = user_story.strip()
    if len(user_story) < 8:
        raise ValueError("اكتب فكرة أطول شوي، على الأقل جملة مفهومة.")
    if len(user_story) > 8000:
        user_story = user_story[:8000]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "حوّل هذه الفكرة إلى المشاهد الأربعة المطلوبة، مع الحفاظ على عناصرها الأساسية:\n" + user_story},
    ]
    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.65,
                max_completion_tokens=4500,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            return validate_story(safe_json_from_text(content))
        except Exception as e:
            last_error = e
            log.exception("Groq story attempt %s failed", attempt + 1)
            messages.append({"role": "user", "content": "أعد الإجابة الآن كـ JSON صالح فقط، مع أربعة مشاهد كاملة مطابقة للمخطط."})
    raise RuntimeError(f"تعذر توليد السيناريو من Groq: {str(last_error)[:350]}")


def story_summary(story: dict) -> str:
    lines = [f"🎬 {story['title']}", story.get("logline", ""), "", "المشاهد التجريبية (كل مشهد 5 ثوانٍ):"]
    for s in story["scenes"]:
        lines.append(f"\n{s['number']}. {s['beat']} — {s['speaker']}\nالصورة: {s['visual_prompt'][:450]}\nالحوار: {s['dialogue_ar']}")
    if TEST_MODE or not ENABLE_PAID_VIDEO:
        lines += ["", "🧪 وضع الاختبار: تم توليد السيناريو فقط. لم يتم إرسال أي طلب فيديو مدفوع إلى WaveSpeed."]
    return "\n".join(lines)


def wavespeed_submit(model: str, payload: dict) -> dict:
    """Submit exactly once. Never auto-resubmit a POST after timeout: that could double-charge."""
    if not WAVESPEED_API_KEY:
        raise RuntimeError("WAVESPEED_API_KEY غير مضبوط")
    url = f"{WAVESPEED_API}/{model.lstrip('/')}"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=(15, 90))
    except requests.RequestException as e:
        raise RuntimeError(f"انقطع الاتصال أثناء إرسال المهمة؛ لن نعيد الإرسال تلقائيًا لتجنب تكلفة مزدوجة. تحقق من سجل WaveSpeed. {e}")
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:1000]}
    if not r.ok:
        raise RuntimeError(f"WaveSpeed HTTP {r.status_code}: {str(body)[:700]}")
    data = body.get("data", body)
    if not data.get("id"):
        raise RuntimeError(f"WaveSpeed لم يرجع task id: {str(body)[:700]}")
    return data


def wavespeed_wait(task_id: str, timeout_seconds=600) -> str:
    url = f"{WAVESPEED_API}/predictions/{task_id}/result"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        r = requests.get(url, headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}"}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"WaveSpeed result HTTP {r.status_code}: {r.text[:500]}")
        body = r.json(); data = body.get("data", body)
        status = str(data.get("status", "")).lower()
        if status == "completed":
            outputs = data.get("outputs") or []
            if isinstance(outputs, str): outputs = [outputs]
            if outputs and isinstance(outputs[0], str): return outputs[0]
            raise RuntimeError(f"WaveSpeed completed without output URL: {str(data)[:500]}")
        if status in {"failed", "cancelled", "canceled", "timeout", "deleted"}:
            raise RuntimeError(f"WaveSpeed task {status}: {str(data.get('error') or data)[:700]}")
        time.sleep(3)
    raise TimeoutError(f"انتهت مهلة الانتظار لمهمة WaveSpeed {task_id}. لن يتم إرسالها مرة ثانية تلقائيًا.")


def download_file(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk: f.write(chunk)
    if dest.stat().st_size < 1024:
        raise RuntimeError("الملف الناتج فارغ أو غير مكتمل")


def create_image(prompt: str) -> str:
    # This path is reached only after both explicit paid-mode switches are enabled.
    task = wavespeed_submit(IMAGE_MODEL, {"prompt": prompt, "size": "480*832", "seed": -1})
    return wavespeed_wait(task["id"])


def create_scene_video(image_url: str, prompt: str, index: int) -> str:
    payload = {"prompt": prompt, "image": image_url, "duration": SCENE_SECONDS, "seed": -1,
               "negative_prompt": "cartoon, anime, subtitles, text, watermark, logo, deformed face, extra limbs, flicker, low quality"}
    task = wavespeed_submit(VIDEO_MODEL, payload)
    log.info("Scene %s submitted as WaveSpeed task %s", index, task["id"])
    return wavespeed_wait(task["id"])


def run_async(coro):
    return asyncio.run(coro)


def create_voice(text: str, speaker: str, dest: Path):
    voice, rate, pitch = VOICE.get(speaker, VOICE["narrator"])
    async def go():
        tts = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
        await tts.save(str(dest))
    run_async(go())


def ffmpeg(*args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("FFmpeg error: " + (p.stderr or "")[-1200:])


def create_paid_video(story: dict, chat_id: int):
    """Optional paid path; guarded by TEST_MODE=false AND ENABLE_PAID_VIDEO=true."""
    if TEST_MODE or not ENABLE_PAID_VIDEO:
        raise RuntimeError("الإنتاج المدفوع مغلق. اضبط TEST_MODE=false وENABLE_PAID_VIDEO=true بعد اختبار البيئة.")
    if not WAVESPEED_API_KEY:
        raise RuntimeError("WAVESPEED_API_KEY غير مضبوط")
    job_dir = Path(tempfile.mkdtemp(prefix="abosaraj_", dir=str(WORK_DIR)))
    clips = []
    try:
        send_message(chat_id, "🎥 تم اعتماد السيناريو. بدأ الإنتاج المدفوع: 4 مشاهد. كل مشهد طلب فيديو، وقد ينشأ طلب صورة أيضًا؛ راجع أسعار النماذج قبل التفعيل.")
        for i, scene in enumerate(story["scenes"], 1):
            send_message(chat_id, f"⏳ تجهيز المشهد {i}/{SCENE_COUNT}…")
            image_url = create_image(scene["visual_prompt"])
            video_url = create_scene_video(image_url, scene["visual_prompt"], i)
            clip = job_dir / f"scene_{i}.mp4"
            download_file(video_url, clip)
            clips.append(clip)
        # Normalize each scene to a common vertical canvas and 5 seconds; no silent assumption about source fps/size.
        normalized = []
        for i, clip in enumerate(clips, 1):
            out = job_dir / f"norm_{i}.mp4"
            ffmpeg("-i", clip, "-t", str(SCENE_SECONDS), "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,setsar=1", "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out)
            normalized.append(out)
        concat_file = job_dir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in normalized), encoding="utf-8")
        silent_video = job_dir / "silent.mp4"
        ffmpeg("-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", silent_video)
        # Per-scene speech; each voice clip is padded/cut to its own 5-second scene to prevent drift.
        audio_parts = []
        for i, scene in enumerate(story["scenes"], 1):
            raw = job_dir / f"voice_{i}_raw.mp3"
            wav = job_dir / f"voice_{i}.wav"
            create_voice(scene["dialogue_ar"], scene["speaker"], raw)
            ffmpeg("-i", raw, "-t", str(SCENE_SECONDS), "-af", "apad=pad_dur=5", "-ar", "44100", "-ac", "2", wav)
            audio_parts.append(wav)
        audio_concat = job_dir / "audio_concat.txt"
        audio_concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in audio_parts), encoding="utf-8")
        speech = job_dir / "speech.wav"
        ffmpeg("-f", "concat", "-safe", "0", "-i", audio_concat, "-c:a", "pcm_s16le", speech)
        final = job_dir / "final.mp4"
        # No auto-generated music/SFX here: avoids falsely claiming those tracks exist.
        ffmpeg("-i", silent_video, "-i", speech, "-map", "0:v:0", "-map", "1:a:0", "-t", "20", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", final)
        with open(final, "rb") as f:
            r = requests.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id": str(chat_id), "caption": f"🎬 {story['title']}"[:1000]}, files={"video": ("abosaraj.mp4", f, "video/mp4")}, timeout=180)
        if not r.ok or not r.json().get("ok"):
            raise RuntimeError(f"Telegram sendVideo failed: {r.text[:500]}")
        send_message(chat_id, "✅ انتهى الفيديو. ملاحظة: مزامنة الشفاه، الموسيقى السينمائية ومؤثرات MMAudio ليست مفعّلة في هذه النسخة؛ الصوت المرفق هو الحوار فقط.")
    finally:
        # Keep temp files briefly only for debugging if KEEP_WORK_FILES=true.
        if os.getenv("KEEP_WORK_FILES", "false").lower() not in ("1", "true", "yes"):
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)


def process_story(chat_id: int, user_story: str):
    send_message(chat_id, "🧠 عم ببني السيناريو وأراجع المشاهد الأربعة…")
    try:
        story = generate_story(user_story)
        send_message(chat_id, story_summary(story))
        if not TEST_MODE and ENABLE_PAID_VIDEO:
            create_paid_video(story, chat_id)
        else:
            send_message(chat_id, "الخطوة التالية: بعد التأكد من السيناريو وإعدادات المفاتيح، يمكن فتح الإنتاج المدفوع يدويًا. لم يتم استهلاك رصيد فيديو في هذه الجولة.")
    except Exception as e:
        log.exception("STORY_PROCESS_ERROR")
        send_message(chat_id, f"❌ صار خطأ أثناء معالجة القصة:\n{str(e)[:900]}\n\nجرّب /status لمعرفة حالة الإعدادات.")


def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return
    text = (message.get("text") or "").strip()
    if not text:
        send_message(chat_id, "ابعث القصة كنص مكتوب حاليًا، وبحوّلها إلى سيناريو من 4 مشاهد.")
        return
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command in ("/start", "/help"):
        send_message(chat_id, "🎬 أهلًا بك في Abosaraj.\n\nابعث فكرة القصة كنص، وسأجهّز 4 مشاهد مترابطة مع حوار عربي.\n\n/start — البداية\n/status — فحص الإعدادات\n/test — اختبار Groq والسيناريو دون فيديو مدفوع\n\nالوضع الآمن الافتراضي لا يستهلك رصيد WaveSpeed.")
    elif command == "/status":
        status = ["🩺 Abosaraj status", f"Groq key: {'configured' if GROQ_API_KEY else 'MISSING'}", f"Telegram token: {'configured' if BOT_TOKEN else 'MISSING'}", f"WaveSpeed key: {'configured' if WAVESPEED_API_KEY else 'not configured'}", f"TEST_MODE: {TEST_MODE}", f"ENABLE_PAID_VIDEO: {ENABLE_PAID_VIDEO}", f"Webhook base URL: {'configured' if RENDER_EXTERNAL_URL else 'MISSING'}", f"FFmpeg: {'available' if subprocess.run(['sh','-c','command -v ffmpeg >/dev/null 2>&1']).returncode == 0 else 'MISSING'}"]
        send_message(chat_id, "\n".join(status))
    elif command == "/test":
        process_story(chat_id, "في ساحة ملكية غامضة، يصل رجل غريب برفقة ذئب أبيض صغير. تتعرف الأميرة إلى علامة على يده، فيرتبك الملك ويأمر الحارس بإغلاق البوابات. قبل أن يُقبض عليه، يهمس الغريب أن الخطر الحقيقي داخل القصر.")
    elif command in ("/cancel",):
        send_message(chat_id, "لا توجد مهمة قابلة للإلغاء في هذه النسخة أثناء عملها. لن أرسل طلبات جديدة تلقائيًا.")
    else:
        # Keep webhook responsive; story generation is run in a worker thread.
        threading.Thread(target=process_story, args=(chat_id, text), daemon=True).start()


@app.get("/")
def root():
    return "Abosaraj is online", 200

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "Abosaraj", "test_mode": TEST_MODE, "paid_video_enabled": ENABLE_PAID_VIDEO, "groq_configured": bool(GROQ_API_KEY), "telegram_configured": bool(BOT_TOKEN), "wavespeed_configured": bool(WAVESPEED_API_KEY)})

@app.post("/telegram/webhook")
def telegram_webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 403
    update = request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(update,), daemon=True).start()
    return jsonify({"ok": True})


def configure_webhook():
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN missing; webhook setup skipped")
        return
    if not RENDER_EXTERNAL_URL:
        log.warning("RENDER_EXTERNAL_URL missing; set it to https://your-service.onrender.com. Webhook not configured.")
        return
    payload = {"url": RENDER_EXTERNAL_URL + "/telegram/webhook", "allowed_updates": ["message", "edited_message"]}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    try:
        result = tg("setWebhook", payload)
        log.info("Telegram webhook configured: %s", result)
        info = tg("getWebhookInfo")
        log.info("Telegram webhook info: %s", info)
    except Exception:
        log.exception("Could not configure Telegram webhook; check BOT_TOKEN and RENDER_EXTERNAL_URL")


def startup_checks():
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN is missing")
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY is missing")
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, timeout=10)
    except Exception:
        log.warning("FFmpeg missing; required only for the paid video path")
    log.info("TEST_MODE=%s ENABLE_PAID_VIDEO=%s MODEL=%s VIDEO_MODEL=%s", TEST_MODE, ENABLE_PAID_VIDEO, GROQ_MODEL, VIDEO_MODEL)

if __name__ == "__main__":
    startup_checks()
    configure_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
