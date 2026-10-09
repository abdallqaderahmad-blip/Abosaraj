import os, re, json, time, asyncio, shutil, tempfile, threading, subprocess, logging
from pathlib import Path
import requests, edge_tts
from flask import Flask, request, jsonify
from groq import Groq

# ==================== CONFIG ====================
BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
GROQ_API_KEY = os.environ["GROQ_API_KEY"].strip()
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
TEST_MODE = os.getenv("TEST_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
CLEANUP_WORKDIR = os.getenv("CLEANUP_WORKDIR", "true").strip().lower() in {"1", "true", "yes", "on"}
SHOT_COUNT, SHOT_DURATION, FPS = 4, 5, 24
VIDEO_WIDTH, VIDEO_HEIGHT = 720, 1280
TITLE_SECONDS = 2.2
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Noto Sans Arabic")
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "18"))
HTTP_TIMEOUT, DOWNLOAD_TIMEOUT = 60, 180
WAVESPEED_POLL_SECONDS, WAVESPEED_MAX_WAIT_SECONDS = 3, 600
GROQ_MAX_RETRIES, GROQ_RETRY_DELAY = 2, 4
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WAVESPEED_API_BASE = "https://api.wavespeed.ai/api/v3"
WAVESPEED_IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
WAVESPEED_VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("abosaraj")
groq_client, app = Groq(api_key=GROQ_API_KEY), Flask(__name__)
processing_chats, processing_lock = set(), threading.Lock()
bot_started_at = time.time()
CHARACTERS = {
    "male_lead": {"name": "الرجل الغامض", "voice": "ar-SY-LaithNeural", "rate": "-10%", "pitch": "-3Hz"},
    "princess": {"name": "الأميرة", "voice": "ar-SA-ZariyahNeural", "rate": "-6%", "pitch": "+1Hz"},
    "king": {"name": "الملك", "voice": "ar-EG-ShakirNeural", "rate": "-8%", "pitch": "-4Hz"},
    "guard": {"name": "الحارس", "voice": "ar-IQ-BasselNeural", "rate": "-2%", "pitch": "-1Hz"},
    "wolf": {"name": "الذئب الأبيض", "voice": None, "rate": None, "pitch": None},
}
CHARACTER_IDS = list(CHARACTERS)

# ==================== GENERAL ====================
def now(): return time.strftime("%Y-%m-%d %H:%M:%S")
def normalize_text(value, limit=500): return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]
def write_json(path, data): Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def run_command(command, timeout=240):
    log.info("RUN: %s", " ".join(map(str, command)))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode:
        log.error("Command error: %s", result.stderr[-2500:])
        raise RuntimeError(f"Command failed: {' '.join(map(str, command))}\n{result.stderr[-1200:]}")
    return result

def ensure_ffmpeg():
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary): raise RuntimeError(f"{binary} غير مثبت على Render.")

def probe(path):
    data = json.loads(run_command(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-show_entries", "format=duration,size", "-of", "json", str(path)]).stdout or "{}")
    streams, fmt = data.get("streams", []), data.get("format", {})
    return {"duration": float(fmt.get("duration") or 0), "size": int(fmt.get("size") or 0),
            "has_video": any(s.get("codec_type") == "video" for s in streams),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}

def assert_valid_media(path, min_size=1000, require_video=False, require_audio=False):
    path = Path(path)
    if not path.exists() or path.stat().st_size < min_size: raise RuntimeError(f"ملف الوسائط مفقود أو فارغ: {path.name}")
    info = probe(path)
    if info["duration"] <= 0 or (require_video and not info["has_video"]) or (require_audio and not info["has_audio"]):
        raise RuntimeError(f"ملف وسائط غير صالح: {path.name}")
    return info

# ==================== TELEGRAM ====================
def telegram_request(method, payload=None, files=None, timeout=60):
    response = requests.post(f"{TELEGRAM_API}/{method}", data=payload or {}, files=files, timeout=timeout)
    try: data = response.json()
    except Exception as exc: raise RuntimeError(f"Telegram returned non-JSON HTTP {response.status_code}") from exc
    if not response.ok or not data.get("ok"): raise RuntimeError(f"Telegram {method} failed: {data}")
    return data

def send_message(chat_id, text):
    try: return telegram_request("sendMessage", {"chat_id": chat_id, "text": str(text or "")[:3900]})
    except Exception: log.exception("Telegram sendMessage failed"); return None

def send_video(chat_id, path, caption=""):
    with open(path, "rb") as f:
        return telegram_request("sendVideo", {"chat_id": chat_id, "caption": caption[:900], "supports_streaming": "true"}, {"video": f}, 300)

# ==================== GROQ JSON ====================
def extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try: return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start: return json.loads(text[start:end + 1])
        raise ValueError("Groq لم يُرجع JSON صالحًا")

def groq_json_request(system_prompt, user_prompt, max_tokens=400, temperature=0.5):
    """Request JSON from Groq GPT-OSS without letting reasoning consume the whole output budget."""
    last_error = None
    for attempt in range(GROQ_MAX_RETRIES + 1):
        try:
            # GPT-OSS can return an empty message.content when a small max_tokens budget
            # is spent on internal reasoning. Hide reasoning and use the current parameter.
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt + " أخرج JSON صالحًا فقط دون Markdown."},
                    {"role": "user", "content": user_prompt + "\nتذكير: أخرج كائن JSON واحدًا صالحًا فقط."},
                ],
                max_completion_tokens=max(700, max_tokens * 2),
                temperature=temperature,
                reasoning_effort="low",
                include_reasoning=False,
            )
            if not response.choices:
                raise ValueError("Groq returned no choices")
            choice = response.choices[0]
            message = choice.message
            content = message.content
            if not content or not str(content).strip():
                finish_reason = getattr(choice, "finish_reason", None)
                usage = getattr(response, "usage", None)
                completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
                log.warning(
                    "Groq empty content: model=%s finish_reason=%s completion_tokens=%s reasoning_present=%s",
                    GROQ_MODEL, finish_reason, completion_tokens, bool(getattr(message, "reasoning", None))
                )
                raise ValueError(f"Groq returned empty content (finish_reason={finish_reason})")
            return extract_json(str(content))
        except Exception as exc:
            last_error = exc
            err = str(exc).lower()
            if attempt >= GROQ_MAX_RETRIES or any(x in err for x in ("401", "403", "model_not_found", "invalid_api_key")):
                break
            delay = 20 if any(x in err for x in ("429", "rate_limit", "tokens per minute", "tpm")) else GROQ_RETRY_DELAY
            log.warning("Groq attempt %s failed; retrying in %ss: %s", attempt + 1, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"Groq request failed: {last_error}")

def generate_story_metadata(story_text):
    try:
        data = groq_json_request("أنت كاتب سيناريو عربي. أعد JSON فقط.",
            f'حوّل الفكرة إلى بيانات فيلم قصير. الفكرة: {story_text[:800]}\nاستخدم هذا الشكل: {{"title":"عنوان عربي قصير","genre":"النوع","visual_style":"cinematic photorealistic","summary":"ملخص بجملة واحدة"}}', 220, 0.4)
    except Exception:
        log.exception("Metadata generation failed; using safe defaults")
        data = {}
    return {"title": normalize_text(data.get("title"), 90) or "الحكاية الغامضة",
            "genre": normalize_text(data.get("genre"), 80) or "خيال وتشويق",
            "visual_style": normalize_text(data.get("visual_style"), 220) or "cinematic photorealistic, realistic characters",
            "summary": normalize_text(data.get("summary"), 260) or story_text[:250]}

def fallback_scene(number, metadata):
    defaults = [
        ("male_lead", "هناك شيء غريب يحدث هنا.", "A mysterious man arrives at an ancient castle gate at night, surrounded by drifting fog."),
        ("princess", "لن أغادر قبل أن أعرف الحقيقة.", "An Arab princess stands in a torch-lit stone corridor, looking anxiously toward the castle gate."),
        ("guard", "ابتعدوا! هناك خطر يقترب.", "A medieval guard raises a torch at the castle entrance as ominous shadows approach."),
        ("male_lead", "الآن فهمت السر، لكن الوقت ينفد.", "The mysterious man and princess discover a secret door glowing blue inside the castle wall."),
    ]
    speaker, dialogue, visual = defaults[max(0, min(number - 1, 3))]
    return {"scene_number": number, "speaker_id": speaker, "dialogue": dialogue, "visual_description": visual,
            "camera": "slow cinematic dolly-in, subtle natural movement", "lighting": "dramatic torchlight, volumetric fog, realistic shadows",
            "mood": "mystery and suspense", "continuity_note": f"Scene {number} of {metadata['title']}"}

def generate_one_scene(number, story_text, metadata, previous_note):
    fallback = fallback_scene(number, metadata)
    prompt = f'''Write scene {number} of 4. Title: {metadata['title']}. Genre: {metadata['genre']}. Style: {metadata['visual_style']}. Summary: {metadata['summary'][:180]}. User idea: {story_text[:250]}. Previous scene: {previous_note[:100] or 'first scene'}. Allowed speaker_id: male_lead, princess, king, guard, wolf (wolf never speaks). Return JSON: {"visual_description":"detailed visual prompt","camera":"camera motion in English","lighting":"lighting in English","mood":"mood in English","speaker_id":"one allowed id","dialogue":"short Arabic dialogue","continuity_note":"short note"}'''
    try:
        data = groq_json_request("You are a cinematic screenwriter. Return valid JSON only. No narrator. Wolf does not speak.", prompt, 380, 0.5)
        speaker = normalize_text(data.get("speaker_id"), 30)
        if speaker not in CHARACTER_IDS: speaker = fallback["speaker_id"]
        dialogue = "" if speaker == "wolf" else normalize_text(data.get("dialogue"), 180)
        return {"scene_number": number, "speaker_id": speaker if dialogue else "", "dialogue": dialogue,
                "visual_description": normalize_text(data.get("visual_description"), 850) or fallback["visual_description"],
                "camera": normalize_text(data.get("camera"), 180) or fallback["camera"],
                "lighting": normalize_text(data.get("lighting"), 180) or fallback["lighting"],
                "mood": normalize_text(data.get("mood"), 100) or fallback["mood"],
                "continuity_note": normalize_text(data.get("continuity_note"), 160) or fallback["continuity_note"]}
    except Exception:
        log.exception("Scene %s failed; using fallback scene", number)
        return fallback

def generate_story(story_text):
    story_text = normalize_text(story_text, 4000)
    if not story_text: raise ValueError("القصة فارغة.")
    metadata = generate_story_metadata(story_text)
    scenes, previous = [], ""
    for n in range(1, SHOT_COUNT + 1):
        log.info("Generating script scene %s/%s", n, SHOT_COUNT)
        scene = generate_one_scene(n, story_text, metadata, previous)
        scenes.append(scene)
        previous = f"{scene['visual_description'][:100]} Dialogue: {scene['dialogue'][:60]}"
    return {**metadata, "original_story": story_text, "scenes": scenes, "created_at": now()}

# ==================== TTS / AUDIO ====================
async def edge_tts_save(text, voice, rate, pitch, path):
    await edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch).save(str(path))

def create_silence(path, duration=SHOT_DURATION):
    run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-t", str(duration), "-c:a", "pcm_s16le", str(path)])

def normalize_audio(input_path, output_path):
    run_command(["ffmpeg", "-y", "-i", str(input_path), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(output_path)])

def synthesize_dialogue(scene, outdir):
    mp3, wav = outdir / f"voice_{scene['scene_number']}.mp3", outdir / f"voice_{scene['scene_number']}.wav"
    character = CHARACTERS.get(scene.get("speaker_id", "")); dialogue = scene.get("dialogue", "").strip()
    if not dialogue or not character or not character.get("voice"):
        create_silence(wav); return wav
    try:
        asyncio.run(edge_tts_save(dialogue, character["voice"], character["rate"], character["pitch"], mp3))
        if not mp3.exists() or mp3.stat().st_size < 100: raise RuntimeError("Empty TTS output")
        normalize_audio(mp3, wav); assert_valid_media(wav, 100, require_audio=True)
    except Exception:
        log.exception("TTS failed for scene %s", scene["scene_number"]); create_silence(wav)
    return wav

def build_audio_track(scenes, outdir):
    files = [synthesize_dialogue(s, outdir) for s in scenes]
    listing = outdir / "audio_concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in files), encoding="utf-8")
    output = outdir / "dialogue_track.wav"
    run_command(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output)])
    assert_valid_media(output, require_audio=True)
    return output

# ==================== WAVESPEED ====================
def require_production_mode():
    if TEST_MODE: raise RuntimeError("TEST_MODE=true: التوليد المدفوع متوقف. اضبط TEST_MODE=false على Render.")
    if not WAVESPEED_API_KEY: raise RuntimeError("WAVESPEED_API_KEY غير موجود في Environment.")

def unwrap_data(data): return data.get("data", data) if isinstance(data, dict) and isinstance(data.get("data", data), dict) else data

def wavespeed_submit(model, payload):
    require_production_mode()
    # No blind retry for paid POST requests: the task may already have been billed.
    r = requests.post(f"{WAVESPEED_API_BASE}/{model}", headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=HTTP_TIMEOUT)
    if not r.ok: raise RuntimeError(f"WaveSpeed submit HTTP {r.status_code}: {r.text[:900]}")
    task = unwrap_data(r.json()); task_id = task.get("id") if isinstance(task, dict) else None
    if not task_id: raise RuntimeError(f"WaveSpeed response missing task id: {str(task)[:600]}")
    log.info("WaveSpeed task submitted: model=%s id=%s", model, task_id)
    return task_id

def wavespeed_wait(task_id):
    require_production_mode(); deadline = time.time() + WAVESPEED_MAX_WAIT_SECONDS
    url = f"{WAVESPEED_API_BASE}/predictions/{task_id}/result"
    while time.time() < deadline:
        r = requests.get(url, headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}"}, timeout=HTTP_TIMEOUT)
        if not r.ok: raise RuntimeError(f"WaveSpeed result HTTP {r.status_code}: {r.text[:700]}")
        result = unwrap_data(r.json()); status = str(result.get("status", "")).lower()
        if status == "completed":
            outputs = result.get("outputs") or []
            if outputs: return outputs
            raise RuntimeError("WaveSpeed task completed without outputs")
        if status in {"failed", "cancelled", "timeout", "deleted"}: raise RuntimeError(f"WaveSpeed task {status}: {result.get('error') or str(result)[:600]}")
        time.sleep(WAVESPEED_POLL_SECONDS)
    raise TimeoutError(f"WaveSpeed task {task_id} timed out")

def output_url(outputs):
    for item in outputs:
        if isinstance(item, str) and item.startswith(("https://", "http://")): return item
        if isinstance(item, dict):
            for key in ("url", "video", "image", "download_url"):
                if isinstance(item.get(key), str) and item[key].startswith(("https://", "http://")): return item[key]
    raise RuntimeError(f"No media URL in WaveSpeed outputs: {str(outputs)[:500]}")

def download_output(url, path, min_size=1000):
    if not isinstance(url, str) or not url.startswith(("https://", "http://")): raise RuntimeError("Invalid output URL")
    with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as r:
        if not r.ok: raise RuntimeError(f"Media download HTTP {r.status_code}")
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(262144):
                if chunk: f.write(chunk); total += len(chunk)
    if total < min_size: raise RuntimeError(f"Downloaded file too small: {total} bytes")
    return Path(path)

def generate_scene_image(scene, story, outdir):
    prompt = ("Vertical 9:16 cinematic photorealistic film still, detailed natural anatomy and realistic faces, no text, no subtitles, no watermark. "
              f"Title: {story['title']}. Scene: {scene['visual_description']}. Style: {story['visual_style']}. Lighting: {scene['lighting']}. Mood: {scene['mood']}. Camera: {scene['camera']}. Keep subjects centered.")
    task = wavespeed_submit(WAVESPEED_IMAGE_MODEL, {"prompt": prompt, "size": "768*1360", "output_format": "jpeg"})
    path = download_output(output_url(wavespeed_wait(task)), outdir / f"scene_{scene['scene_number']}.jpg", 5000)
    run_command(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "null", "-"])
    return path

def upload_image_to_wavespeed(path):
    require_production_mode(); path = Path(path)
    content_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    r = requests.post(f"{WAVESPEED_API_BASE}/media/uploads", headers={"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"}, json={"filename": path.name, "size": path.stat().st_size, "content_type": content_type}, timeout=HTTP_TIMEOUT)
    if not r.ok: raise RuntimeError(f"WaveSpeed upload ticket HTTP {r.status_code}: {r.text[:700]}")
    ticket = unwrap_data(r.json()); upload = ticket.get("upload") if isinstance(ticket, dict) else None; download_url = ticket.get("download_url") if isinstance(ticket, dict) else None
    if not isinstance(upload, dict) or not upload.get("url") or not download_url: raise RuntimeError(f"Invalid WaveSpeed upload ticket: {str(ticket)[:700]}")
    with path.open("rb") as f: put = requests.put(upload["url"], headers=upload.get("headers") or {}, data=f, timeout=DOWNLOAD_TIMEOUT)
    if not put.ok: raise RuntimeError(f"WaveSpeed storage upload HTTP {put.status_code}: {put.text[:500]}")
    return download_url

def generate_scene_video(scene, story, image_path, outdir):
    prompt = f"Cinematic continuous shot. {scene['visual_description']}. Motion: {scene['camera']}. Lighting: {scene['lighting']}. Mood: {scene['mood']}. Subtle realistic movement; preserve composition and character identity. No text, subtitles, logos or watermark."
    payload = {"prompt": prompt, "image": upload_image_to_wavespeed(image_path), "duration": SHOT_DURATION, "seed": -1,
               "negative_prompt": "text, subtitles, watermark, logo, distorted face, extra limbs, flicker, still image"}
    task = wavespeed_submit(WAVESPEED_VIDEO_MODEL, payload)
    path = download_output(output_url(wavespeed_wait(task)), outdir / f"scene_{scene['scene_number']}_ai.mp4", 10000)
    info = assert_valid_media(path, 10000, require_video=True)
    if info["duration"] < 3: raise RuntimeError(f"Generated clip too short: {info['duration']:.2f}s")
    return path

# ==================== SUBTITLES / FINAL VIDEO ====================
def srt_time(seconds):
    ms = int(max(0, seconds) * 1000); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_subtitle_file(story, scenes, outdir):
    entries = [f"1\n{srt_time(0)} --> {srt_time(TITLE_SECONDS)}\n{story['title']}\n"]
    for i, scene in enumerate(scenes):
        text = normalize_text(scene.get("dialogue"), 220)
        if text:
            start, end = i * SHOT_DURATION + 0.35, (i + 1) * SHOT_DURATION - 0.25
            entries.append(f"{len(entries)+1}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    path = outdir / "captions_ar.srt"; path.write_text("\n".join(entries), encoding="utf-8-sig"); return path

def escape_subtitle_path(path): return str(Path(path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

def render_final_video(scene_paths, audio_path, story, outdir):
    normalized = []
    for i, path in enumerate(scene_paths, 1):
        out = outdir / f"normalized_{i}.mp4"
        run_command(["ffmpeg", "-y", "-i", str(path), "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={FPS},format=yuv420p", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-t", str(SHOT_DURATION), "-movflags", "+faststart", str(out)], 300)
        assert_valid_media(out, 5000, require_video=True); normalized.append(out)
    listing = outdir / "scenes_concat.txt"; listing.write_text("".join(f"file '{p.resolve()}'\n" for p in normalized), encoding="utf-8")
    base = outdir / "base_video.mp4"
    run_command(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(base)], 300)
    srt = make_subtitle_file(story, story["scenes"], outdir)
    vf = f"subtitles='{escape_subtitle_path(srt)}':force_style='FontName={SUBTITLE_FONT},FontSize={SUBTITLE_FONT_SIZE},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=70'"
    final = outdir / "final_video.mp4"
    run_command(["ffmpeg", "-y", "-i", str(base), "-i", str(audio_path), "-vf", vf, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-af", "apad", "-t", str(SHOT_COUNT * SHOT_DURATION), "-movflags", "+faststart", str(final)], 420)
    info = assert_valid_media(final, 50000, require_video=True, require_audio=True)
    if info["duration"] < SHOT_COUNT * SHOT_DURATION - 1: raise RuntimeError(f"Final video too short: {info['duration']:.2f}s")
    run_command(["ffmpeg", "-v", "error", "-i", str(final), "-vf", "select=eq(n\\,0)", "-frames:v", "1", "-f", "null", "-"])
    return final

def create_story_video(story, outdir):
    ensure_ffmpeg(); require_production_mode()
    audio = build_audio_track(story["scenes"], outdir); clips = []
    for scene in story["scenes"]:
        log.info("Generating AI scene %s/%s", scene["scene_number"], SHOT_COUNT)
        image = generate_scene_image(scene, story, outdir)
        clips.append(generate_scene_video(scene, story, image, outdir))
    if len(clips) != SHOT_COUNT: raise RuntimeError(f"Expected {SHOT_COUNT} clips; got {len(clips)}")
    return render_final_video(clips, audio, story, outdir)

# ==================== CHAT JOB ====================
def process_story_for_chat(chat_id, story_text):
    key = str(chat_id)
    with processing_lock:
        if key in processing_chats: send_message(chat_id, "⏳ في عملية شغالة حاليًا. استنى لحد ما تخلص."); return
        processing_chats.add(key)
    workdir = None
    try:
        require_production_mode()
        send_message(chat_id, "🎬 بدأ إنشاء الفيديو الحقيقي: كتابة القصة، توليد الصور وتحريكها، الصوت والترجمة. قد يستغرق عدة دقائق.")
        workdir = Path(tempfile.mkdtemp(prefix="abosaraj_"))
        story = generate_story(story_text); write_json(workdir / "story.json", story)
        send_message(chat_id, f"🧠 القصة جاهزة: {story['title']}\n🎨 سيجري توليد 4 صور وتحريكها الآن.")
        final = create_story_video(story, workdir); info = assert_valid_media(final, 50000, require_video=True, require_audio=True)
        send_video(chat_id, final, f"🎬 {story['title']}\nالنوع: {story['genre']}\nالمدة: {info['duration']:.1f} ثانية")
        send_message(chat_id, "✅ اكتمل الفيديو بعد فحص الفيديو والصوت والترجمة.")
    except Exception as exc:
        log.exception("Story processing failed")
        send_message(chat_id, f"❌ فشل إنشاء الفيديو، ولم يتم إرسال ملف ناقص.\nالسبب: {str(exc)[:900]}")
    finally:
        if workdir and CLEANUP_WORKDIR: shutil.rmtree(workdir, ignore_errors=True)
        with processing_lock: processing_chats.discard(key)

def launch_story(chat_id, text): threading.Thread(target=process_story_for_chat, args=(chat_id, text), daemon=True).start()

def handle_telegram_update(update):
    if not isinstance(update, dict): return
    message = update.get("message") or update.get("edited_message")
    if not message: return
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None: return
    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        send_message(chat_id, "🎬 أهلًا بك في Abosaraj AI Cinematic Story Bot.\n\nأرسل فكرة قصة بالعربية لتحويلها إلى فيديو AI بأربع مشاهد، حوار وصوت عربي وعنوان وترجمة.\n\n/status — حالة البوت\n/help — المساعدة"); return
    if text.startswith("/help"):
        send_message(chat_id, "أرسل فكرة قصة نصية. التوليد الحقيقي يحتاج TEST_MODE=false ومفتاح WaveSpeed صالحًا."); return
    if text.startswith("/status"):
        with processing_lock: active = len(processing_chats)
        send_message(chat_id, f"🟢 البوت يعمل\nTEST_MODE={TEST_MODE}\nWaveSpeed key configured={'yes' if bool(WAVESPEED_API_KEY) else 'no'}\nActive jobs={active}\nModel={GROQ_MODEL}"); return
    if not text: send_message(chat_id, "ابعث فكرة القصة كنص مكتوب."); return
    launch_story(chat_id, text)

# ==================== FLASK / WEBHOOK ====================
@app.get("/")
def index():
    return jsonify({"service": "Abosaraj AI Cinematic Story Bot", "status": "online", "test_mode": TEST_MODE, "real_generation_enabled": not TEST_MODE and bool(WAVESPEED_API_KEY), "scenes": SHOT_COUNT})

@app.get("/health")
def health(): return jsonify({"status": "ok", "uptime_seconds": int(time.time() - bot_started_at), "test_mode": TEST_MODE})

@app.get("/status")
def status():
    with processing_lock: active = len(processing_chats)
    return jsonify({"status": "ok", "active_jobs": active, "test_mode": TEST_MODE, "wavespeed_key_configured": bool(WAVESPEED_API_KEY), "model": GROQ_MODEL})

@app.post("/webhook")
@app.post("/telegram/webhook")
def telegram_webhook():
    try: handle_telegram_update(request.get_json(silent=True) or {})
    except Exception: log.exception("Webhook handler failed")
    return jsonify({"ok": True})

def configure_telegram_webhook():
    if not RENDER_EXTERNAL_URL: log.warning("RENDER_EXTERNAL_URL empty; webhook not configured."); return
    try:
        telegram_request("setWebhook", {"url": f"{RENDER_EXTERNAL_URL}/webhook", "drop_pending_updates": "false"})
        log.info("Telegram webhook configured")
    except Exception: log.exception("Could not configure Telegram webhook")

if __name__ == "__main__":
    log.info("Starting Abosaraj AI Cinematic Story Bot | TEST_MODE=%s | MODEL=%s | WAVESPEED_KEY=%s", TEST_MODE, GROQ_MODEL, bool(WAVESPEED_API_KEY))
    ensure_ffmpeg(); configure_telegram_webhook(); app.run(host="0.0.0.0", port=PORT, threaded=True)
