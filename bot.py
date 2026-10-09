import os
import json
import time
import asyncio
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
import requests
import edge_tts
from flask import Flask, request

# ================= ENV - آمن ما بطفي =================
BOT_TOKEN = os.environ.get("BOT_TOKEN","")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY","")
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

print(f"[BOOT] BOT_TOKEN={bool(BOT_TOKEN)} GROQ={bool(GROQ_API_KEY)} WAVE={bool(WAVESPEED_API_KEY)}", flush=True)

try:
    from groq import Groq
    groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except Exception as e:
    print(f"[BOOT WARN] Groq init fail: {e}", flush=True)
    groq = None

def envbool(key, default):
    return os.getenv(key, str(default)).lower() in ("1","true","yes","on")

# ================= MODES =================
TEST_MODE = envbool("TEST_MODE", True) # خليه true للتجربة المجانية
LIPSYNC_ENABLED = envbool("LIPSYNC_ENABLED", True)
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE","face").lower()
if LIPSYNC_MODE not in ("lips","face","head"): LIPSYNC_MODE="face"
LIPSYNC_EMOTION = os.getenv("LIPSYNC_EMOTION","neutral").lower()
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES","1"))

SOUND_DESIGN_ENABLED = envbool("SOUND_DESIGN_ENABLED", True)
MUSIC_ENABLED = envbool("MUSIC_ENABLED", True)
MMAUDIO_STEPS = int(os.getenv("MMAUDIO_STEPS","25"))
MMAUDIO_GUIDANCE = float(os.getenv("MMAUDIO_GUIDANCE","4.5"))
SFX_VOLUME = float(os.getenv("SFX_VOLUME","0.52"))
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME","0.10"))
VOICE_VOLUME = float(os.getenv("VOICE_VOLUME","1.0"))

# ================= VIDEO =================
SHOT_COUNT = 4
SHOT_DURATION = 5
TOTAL_DURATION = 20
VIDEO_WIDTH = 480
VIDEO_HEIGHT = 832
VIDEO_FPS = 24
IMAGE_SIZE = os.getenv("IMAGE_SIZE","480*832")

VOICE_CONFIG = {
    "male_lead": {"voice": "ar-SY-LaithNeural", "rate": "-10%", "pitch": "-3Hz"},
    "princess": {"voice": "ar-SA-ZariyahNeural", "rate": "-6%", "pitch": "+0Hz"},
    "king": {"voice": "ar-EG-ShakirNeural", "rate": "-8%", "pitch": "-4Hz"},
    "guard": {"voice": "ar-IQ-BasselNeural", "rate": "-2%", "pitch": "-1Hz"},
    "narrator": {"voice": "ar-SA-HamedNeural", "rate": "-8%", "pitch": "-2Hz"}
}

WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3"
IMAGE_MODEL = "wavespeed-ai/z-image/turbo"
VIDEO_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL = "sync/react-1"
SFX_MODEL = "wavespeed-ai/mmaudio-v2"
MUSIC_MODEL = "wavespeed-ai/ace-step/prompt-to-audio"

app = Flask(__name__)
logging_lock = threading.Lock()
processing_chats = set()
processing_lock = threading.Lock()

def log(message):
    with logging_lock:
        print(f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {message}", flush=True)

def auth_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type": "application/json"}
def get_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}"}

def http_get(url, **kwargs):
    last_error=None
    for attempt in range(3):
        try: return requests.get(url, **kwargs)
        except Exception as e:
            last_error=e; log(f"GET retry {attempt+1}/3: {e}"); time.sleep(1.5*(attempt+1))
    raise last_error

def extract_output(value):
    if isinstance(value,str) and value.startswith(("http://","https://")): return value
    if isinstance(value,dict):
        for key in ("url","audio_url","video_url","download_url","output_url"):
            item=value.get(key)
            if isinstance(item,str) and item.startswith(("http://","https://")): return item
        for item in value.values():
            result=extract_output(item)
            if result: return result
    if isinstance(value,list):
        for item in value:
            result=extract_output(item)
            if result: return result
    return None

def wavespeed_submit(model,payload):
    if TEST_MODE: raise RuntimeError("TEST_MODE=true: WaveSpeed disabled.")
    if not WAVESPEED_API_KEY: raise RuntimeError("WAVESPEED_API_KEY is missing.")
    url=f"{WAVESPEED_BASE}/{model}"
    response=requests.post(url, headers=auth_headers(), json=payload, timeout=(15,90))
    response.raise_for_status()
    body=response.json(); data=body.get("data") or body; task_id=data.get("id")
    if not task_id: raise RuntimeError("No task id: "+json.dumps(body, ensure_ascii=False))
    log(f"WaveSpeed task {task_id} -> {model}"); return task_id

def wavespeed_wait(task_id, timeout=900):
    started=time.time(); url=f"{WAVESPEED_BASE}/predictions/{task_id}/result"
    while True:
        if time.time()-started>timeout: raise TimeoutError(f"Timeout {task_id}")
        response=http_get(url, headers=get_headers(), timeout=30); response.raise_for_status()
        body=response.json(); data=body.get("data") or body; status=str(data.get("status","")).lower()
        log(f"Task {task_id}: {status}")
        if status=="completed":
            output=extract_output(data.get("outputs") or data.get("output"))
            if not output: raise RuntimeError("No output: "+json.dumps(body, ensure_ascii=False))
            return output
        if status in ("failed","cancelled","timeout","deleted"): raise RuntimeError("Failed: "+str(data.get("error") or body))
        time.sleep(2)

def upload_to_wavespeed(path):
    path=Path(path)
    response=requests.post(f"{WAVESPEED_BASE}/media/uploads", headers=auth_headers(), json={"filename":path.name,"size":path.stat().st_size}, timeout=30)
    response.raise_for_status(); body=response.json(); data=body.get("data") or {}; upload=data.get("upload") or {}; upload_url=upload.get("url"); download_url=data.get("download_url")
    if not upload_url or not download_url: raise RuntimeError("Invalid upload: "+json.dumps(body, ensure_ascii=False))
    with path.open("rb") as file: response=requests.put(upload_url, headers=upload.get("headers") or {}, data=file, timeout=300)
    response.raise_for_status(); return download_url

def download_file(url,path):
    path=Path(path)
    with http_get(url, timeout=(15,300), stream=True) as response:
        response.raise_for_status()
        with path.open("wb") as file:
            for chunk in response.iter_content(1024*1024):
                if chunk: file.write(chunk)
    if not path.exists() or path.stat().st_size==0: raise RuntimeError(f"Empty download: {path}")
    log(f"Downloaded {path.name}: {path.stat().st_size/1048576:.2f} MB"); return path

# ================= CHARACTER BIBLE - REAL HUMAN UPDATE =================
CAST_BIBLE = """
REAL HUMAN LIVE-ACTION 8K - NOT CARTOON NOT ANIME NOT 3D.

HERO: 29-year-old REAL Levantine man, tall athletic build, olive skin, dark wavy shoulder-length hair, short trimmed beard, intense brown eyes, black medieval fantasy coat with dark leather armor, NO helmet, real human skin pores, calm protective, blue-white energy around hands when using power, photorealistic actor, Hollywood actor.

PRINCESS: 24-year-old REAL Arab woman, olive skin, long dark-brown hair, brown eyes, natural beauty, deep burgundy medieval gown gold embroidery, simple royal jewelry, REAL human actress.

KING: 58-year-old REAL Arab man, broad shoulders, gray-streaked dark beard, stern brown eyes, dark royal robe muted gold, powerful royal presence, REAL actor.

WOLF PUP: Very small realistic white-gray wolf pup, realistic wet fluffy fur, amber/blue-gray eyes, real four-legged anatomy, small injury, NO cartoon.

GIANT TIGER - CRITICAL: ENORMOUS TIGER the size of TWO ELEPHANTS, 4 meters tall at shoulder, 6 meters long, hyper realistic orange with black stripes, massive muscles, huge paws with claws, terrifying roar, photorealistic fur detail, cinematic monster tiger, fills entire frame, guards flee in fear.

VISUAL STYLE: Photorealistic live-action fantasy movie, REAL human actors, real skin pores, natural eyes, realistic hands with 5 fingers, realistic fur, realistic cloth, natural breathing, cinematic lighting, volumetric light, shallow depth of field, Netflix 8K, vertical 9:16 fill full frame NO black borders, NO text, NO subtitles.
"""

REALISM_NEGATIVE = "anime, cartoon, illustration, video game style, plastic skin, toy-like people, doll face, deformed hands, extra fingers, duplicate people, blurry, low quality, CGI fake, mannequin, black screen, black rectangles, black borders, text, subtitles, logos, watermarks"

# ================= STORY GENERATOR - REAL HUMAN =================
def create_story(user_idea):
    system_prompt = f"You are Netflix REAL HUMAN director. {CAST_BIBLE} NEGATIVE: {REALISM_NEGATIVE} Create 20 seconds EXACTLY 4 scenes x5s. SCENE1: King rejects supernatural hero weak. SCENE2: Princess meets hero secretly forest with small white wolf pup he heals it. SCENE3: GIANT TIGER enormous size of two elephants 4 meters attacks palace guards flee hero blue power protects princess wolf. SCENE4: King sees hero chose protection not violence gives challenge warning cliffhanger. One speaker max per scene, wolf never speaks, Arabic dialogue short 5 sec, no subtitles, photorealistic real humans, consistent faces, strong visual. Return JSON only."
    user_prompt = f"USER STORY: {user_idea} Return exactly: {{\"title\": \"...\", \"hook\": \"...\", \"scenes\": [{{\"action\": \"...\", \"dialogue\": [{{\"speaker\": \"king\", \"text\": \"...\", \"emotion\": \"angry\"}}], \"scene_image_prompt\": \"REAL HUMAN...\", \"video_prompt\": \"REAL HUMAN...\", \"camera\": \"...\", \"sound\": \"...\", \"music\": \"...\"}} x4]}} "
    for attempt in range(4):
        try:
            response = groq.chat.completions.create(model=GROQ_MODEL, temperature=0.35, max_completion_tokens=5000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}])
            content = (response.choices[0].message.content or "").strip()
            story = json.loads(content); scenes = story.get("scenes")
            if not isinstance(scenes,list) or len(scenes)!=4: raise RuntimeError("Need 4 scenes")
            for scene in scenes:
                scene.setdefault("action",""); scene.setdefault("scene_image_prompt",""); scene.setdefault("video_prompt",""); scene.setdefault("camera",""); scene.setdefault("sound",""); scene.setdefault("music","")
                dialogue=scene.get("dialogue")
                if not isinstance(dialogue,list): dialogue=[]
                clean=[]
                for item in dialogue:
                    if not isinstance(item,dict): continue
                    speaker=item.get("speaker"); text=str(item.get("text","")).strip()
                    if speaker in VOICE_CONFIG and text: clean.append({"speaker":speaker,"text":text,"emotion":item.get("emotion","neutral")})
                    if len(clean)>=1: break
                scene["dialogue"]=clean
            return story
        except Exception as e:
            log(f"Groq attempt {attempt+1}/4 failed: {e}")
            if attempt==3: raise

async def tts_async(text,config,output):
    communicator = edge_tts.Communicate(text=text, voice=config["voice"], rate=config["rate"], pitch=config["pitch"])
    await communicator.save(str(output))
def make_tts(text,speaker,output):
    asyncio.run(tts_async(text, VOICE_CONFIG[speaker], output))
    if not output.exists() or output.stat().st_size==0: raise RuntimeError("TTS failed")
    return output
def run_cmd(command,timeout=300):
    process=subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if process.returncode!=0: raise RuntimeError("Command failed:\n"+process.stderr[-6000:])
    return process
def ffprobe_duration(path):
    try:
        process=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(process.stdout.strip())
    except: return 0.0
def make_silence(out,dur): run_cmd(["ffmpeg","-y","-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000","-t",str(dur),"-c:a","pcm_s16le",str(out)],60)
def fit_audio(src,out,dur):
    af=f"apad,atrim=0:{dur},asetpts=N/SR/TB"
    run_cmd(["ffmpeg","-y","-i",str(src),"-af",af,"-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)],120); return out
def normalize_video(src,out,dur=5):
    vf=f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},setsar=1"
    run_cmd(["ffmpeg","-y","-i",str(src),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300); return out
def concat_videos(videos,out):
    lf=out.parent/"concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{str(v).replace(chr(39),'_')}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-an","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300); return out
def generate_image(prompt,out):
    full_prompt=f"{CAST_BIBLE} SCENE IMAGE REAL HUMAN 8K PHOTOREALISTIC: {prompt} NEGATIVE: {REALISM_NEGATIVE}"
    tid=wavespeed_submit(IMAGE_MODEL, {"prompt":full_prompt, "size":IMAGE_SIZE, "seed":-1, "output_format":"jpeg"}); url=wavespeed_wait(tid,600); return download_file(url,out)
def generate_video(image_url,prompt,out):
    full=f"REAL HUMAN ACTORS PHOTOREALISTIC 8K LIVE-ACTION CINEMATIC NETFLIX. {CAST_BIBLE} MOTION: {prompt} Real natural movement fill 9:16"
    tid=wavespeed_submit(VIDEO_MODEL, {"image":image_url, "prompt":full, "negative_prompt":REALISM_NEGATIVE, "duration":5, "seed":-1}); url=wavespeed_wait(tid,900); return download_file(url,out)
def generate_lipsync(v_url,a_url,emo,out):
    tid=wavespeed_submit(LIPSYNC_MODEL, {"video":v_url, "audio":a_url, "emotion":emo, "model_mode":LIPSYNC_MODE}); url=wavespeed_wait(tid,900); return download_file(url,out)
def generate_sfx(v_url,prompt,out):
    tid=wavespeed_submit(SFX_MODEL, {"video":v_url, "prompt":prompt or "realistic cinematic wind footsteps tiger roar huge monster", "negative_prompt":"speech dialogue singing music", "num_inference_steps":MMAUDIO_STEPS, "duration":5, "guidance_scale":MMAUDIO_GUIDANCE, "mask_away_clip":False}); url=wavespeed_wait(tid,600); return download_file(url,out)
def generate_music(prompt,out):
    tid=wavespeed_submit(MUSIC_MODEL, {"prompt":prompt, "duration":TOTAL_DURATION}); url=wavespeed_wait(tid,600); return download_file(url,out)
def mix_sfx(files,out):
    if not files: make_silence(out,TOTAL_DURATION); return out
    cmd=["ffmpeg","-y"]; labels=[]
    for i,p in enumerate(files): cmd+=["-i",str(p)]; labels.append(f"[{i}:a]")
    fc="".join(labels)+f"amix=inputs={len(files)}:duration=longest:dropout_transition=0,volume={SFX_VOLUME},apad,atrim=0:{TOTAL_DURATION}[a]"
    cmd+=["-filter_complex",fc,"-map","[a]","-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)]; run_cmd(cmd,180); return out
def create_dialogue_track(items,out):
    if not items: make_silence(out,TOTAL_DURATION); return out
    cmd=["ffmpeg","-y"]; labels=[]
    for i,(idx,aud) in enumerate(items): cmd+=["-i",str(aud)]; delay=idx*SHOT_DURATION*1000; labels.append(f"[{i}:a]adelay={delay}|{delay}[d{i}]")
    inputs="".join(f"[d{i}]" for i in range(len(items)))
    fc=";".join(labels)+f";{inputs}amix=inputs={len(items)}:duration=longest:dropout_transition=0,apad,atrim=0:{TOTAL_DURATION},volume={VOICE_VOLUME}[a]"
    cmd+=["-filter_complex",fc,"-map","[a]","-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)]; run_cmd(cmd,180); return out
def final_mix(video,dialogue,sfx,music,out):
    inputs=["-i",str(video),"-i",str(dialogue),"-i",str(sfx)]
    if music and Path(music).exists(): inputs+=["-i",str(music)]; fc=f"[1:a]volume={VOICE_VOLUME}[voice];[2:a]volume=1[effects];[3:a]volume={MUSIC_VOLUME}[music];[voice][effects][music]amix=inputs=3:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11[audio]"
    else: fc="[1:a]volume=1.0[audio]"
    cmd=["ffmpeg","-y"]+inputs+["-filter_complex",fc,"-map","0:v:0","-map","[audio]","-c:v","copy","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",str(out)]; run_cmd(cmd,300); return out

TELEGRAM_API = "https://api.telegram.org/bot"+BOT_TOKEN
def telegram(method,data=None,files=None,timeout=60):
    r=requests.post(TELEGRAM_API+"/"+method, data=data, files=files, timeout=timeout); r.raise_for_status(); b=r.json()
    if not b.get("ok"): raise RuntimeError("Telegram "+method+": "+str(b)); return b
def send_text(chat_id,text): return telegram("sendMessage", {"chat_id":chat_id, "text":text})
def send_video(chat_id,path,caption):
    try:
        with Path(path).open("rb") as f: return telegram("sendVideo", {"chat_id":chat_id, "caption":caption, "supports_streaming":"true"}, {"video":("episode.mp4",f,"video/mp4")}, 300)
    except:
        with Path(path).open("rb") as f: return telegram("sendDocument", {"chat_id":chat_id, "caption":caption}, {"document":("episode.mp4",f,"video/mp4")}, 300)

def process_story(chat_id,user_idea):
    work=Path(tempfile.mkdtemp(prefix="abosaraj_real_"))
    try:
        send_text(chat_id, "🎬 REAL HUMAN بدأ...\n👤 ممثلين حقيقيين 100% 8K\n🐯 نمر عملاق بحجم فيلين")
        story=create_story(user_idea); videos=[]; sfx_files=[]; dialogue_items=[]; lip_done=0
        for index,scene in enumerate(story["scenes"]):
            no=index+1; log(f"SCENE {no}/4")
            img=work/f"scene_{no}.jpg"; raw=work/f"scene_{no}_raw.mp4"; norm=work/f"scene_{no}.mp4"
            iprompt=f"REAL HUMAN SCENE {no} {scene.get('action','')} {scene.get('scene_image_prompt','')}"
            generate_image(iprompt, img); iurl=upload_to_wavespeed(img)
            vprompt=scene.get("video_prompt","")+" "+scene.get("camera","")
            generate_video(iurl, vprompt, raw); normalize_video(raw, norm, SHOT_DURATION)
            dlg=(scene.get("dialogue") or [])
            if dlg:
                sp=dlg[0].get("speaker"); txt=str(dlg[0].get("text","")).strip(); emo=dlg[0].get("emotion","neutral")
                if sp in VOICE_CONFIG and txt:
                    mp3=work/f"scene_{no}_{sp}.mp3"; wav=work/f"scene_{no}_{sp}.wav"
                    make_tts(txt, sp, mp3); fit_audio(mp3, wav, SHOT_DURATION); dialogue_items.append((index, wav))
                    if LIPSYNC_ENABLED and lip_done < MAX_LIPSYNC_SCENES:
                        try:
                            dur=ffprobe_duration(mp3) or 3.0; dur=max(1.0, min(15.0, dur))
                            lip_aud=work/f"scene_{no}_lip.wav"; fit_audio(mp3, lip_aud, dur)
                            up_aud=upload_to_wavespeed(lip_aud); up_vid=upload_to_wavespeed(norm)
                            lip_v=work/f"scene_{no}_lip.mp4"; generate_lipsync(up_vid, up_aud, emo, lip_v)
                            fixed=work/f"scene_{no}_lip_fixed.mp4"; normalize_video(lip_v, fixed, SHOT_DURATION); shutil.copy2(fixed, norm); lip_done+=1
                        except Exception as e: log(f"L
