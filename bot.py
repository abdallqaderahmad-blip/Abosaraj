import os, json, time, asyncio, shutil, tempfile, threading, subprocess
from pathlib import Path
import requests
import edge_tts
from flask import Flask, request
from groq import Groq

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

def envbool(k,d): return os.getenv(k,str(d)).lower() in ("1","true","yes","on")
TEST_MODE = envbool("TEST_MODE", False)
LIPSYNC_ENABLED = envbool("LIPSYNC_ENABLED", True)
LIPSYNC_MODE = os.getenv("LIPSYNC_MODE","face").lower()
MAX_LIPSYNC_SCENES = int(os.getenv("MAX_LIPSYNC_SCENES","4"))
SOUND_DESIGN_ENABLED = envbool("SOUND_DESIGN_ENABLED", True)
MUSIC_ENABLED = envbool("MUSIC_ENABLED", True)

SHOT_DURATION=5; TOTAL_DURATION=20
VIDEO_WIDTH=480; VIDEO_HEIGHT=832; VIDEO_FPS=24
IMAGE_SIZE="480*832"

VOICE_CONFIG = {
 "male_lead": {"voice":"ar-SY-LaithNeural","rate":"-10%","pitch":"-3Hz"},
 "princess": {"voice":"ar-SA-ZariyahNeural","rate":"-6%","pitch":"+0Hz"},
 "king": {"voice":"ar-EG-ShakirNeural","rate":"-8%","pitch":"-4Hz"},
}

WAVESPEED_BASE="https://api.wavespeed.ai/api/v3"
IMAGE_MODEL="wavespeed-ai/z-image/turbo"
VIDEO_MODEL="wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL="sync/react-1"
SFX_MODEL="wavespeed-ai/mmaudio-v2"
MUSIC_MODEL="wavespeed-ai/ace-step/prompt-to-audio"

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)
lock = threading.Lock()
processing_chats=set()
plock=threading.Lock()

def log(m):
    with lock: print(f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {m}", flush=True)
def auth_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}", "Content-Type":"application/json"}
def get_headers(): return {"Authorization": f"Bearer {WAVESPEED_API_KEY}"}
def http_get(url, **k):
    last=None
    for i in range(3):
        try: return requests.get(url, **k)
        except Exception as e: last=e; time.sleep(1.5*(i+1))
    raise last
def extract_output(v):
    if isinstance(v,str) and v.startswith(("http://","https://")): return v
    if isinstance(v,dict):
        for kk in ("url","audio_url","video_url","download_url","output_url"):
            it=v.get(kk)
            if isinstance(it,str) and it.startswith(("http://","https://")): return it
        for it in v.values():
            r=extract_output(it)
            if r: return r
    if isinstance(v,list):
        for it in v:
            r=extract_output(it)
            if r: return r
    return None
def wavespeed_submit(model,payload):
    if TEST_MODE: raise RuntimeError("TEST_MODE=true")
    url = f"{WAVESPEED_BASE}/{model}"
    r = requests.post(url, headers=auth_headers(), json=payload, timeout=(15,90))
    r.raise_for_status()
    body=r.json(); data=body.get("data") or body; tid=data.get("id")
    if not tid: raise RuntimeError(json.dumps(body, ensure_ascii=False))
    return tid
def wavespeed_wait(tid, timeout=900):
    started=time.time(); url=f"{WAVESPEED_BASE}/predictions/{tid}/result"
    while True:
        if time.time()-started>timeout: raise TimeoutError(tid)
        r=http_get(url, headers=get_headers(), timeout=30); r.raise_for_status()
        body=r.json(); data=body.get("data") or body
        status=str(data.get("status","")).lower()
        log(f"Task {tid}: {status}")
        if status=="completed":
            out=extract_output(data.get("outputs") or data.get("output"))
            if not out: raise RuntimeError(json.dumps(body, ensure_ascii=False))
            return out
        if status in ("failed","cancelled","timeout","deleted"): raise RuntimeError(str(data.get("error") or body))
        time.sleep(2)
def upload_to_wavespeed(path):
    path=Path(path)
    r=requests.post(f"{WAVESPEED_BASE}/media/uploads", headers=auth_headers(), json={"filename":path.name,"size":path.stat().st_size}, timeout=30)
    r.raise_for_status(); body=r.json(); data=body.get("data") or {}; up=data.get("upload") or {}; uu=up.get("url"); du=data.get("download_url")
    with path.open("rb") as f: rr=requests.put(uu, headers=up.get("headers") or {}, data=f, timeout=300); rr.raise_for_status()
    return du
def download_file(url, path):
    path=Path(path)
    with http_get(url, timeout=(15,300), stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for c in r.iter_content(1024*1024):
                if c: f.write(c)
    return path

CAST_BIBLE = "REALISTIC NETFLIX LIVE-ACTION FANTASY 8K HERO 29y man tall athletic olive dark wavy hair beard brown eyes black coat leather armor pretends weak hides blue-white energy PRINCESS 24y Arab princess burgundy gown WOLF small white-gray pup amber eyes real fur GIANT TIGER ENORMOUS size TWO ELEPHANTS 4m tall hyper realistic orange black stripes massive muscles terrifying STYLE photorealistic vertical 9:16 no cartoon no text no black borders"

def create_story(user_idea):
    system_prompt = f"You are Netflix screenwriter. 4 scenes x5 sec. {CAST_BIBLE} S1 king rejects weak hero S2 princess meets hero secretly forest with white wolf love S3 GIANT TIGER size two elephants attacks palace guards flee critical hero glows blue reveals hidden power S4 Epic fight hero vs giant tiger defeats with energy blast protects princess wolf king shocked respects. One speaker per scene wolf never speaks Arabic short dialogue max 7 words Return JSON only."
    user_prompt = f"USER IDEA: {user_idea} Return title and 4 scenes"
    for _ in range(4):
        try:
            res=groq.chat.completions.create(model=GROQ_MODEL, temperature=0.35, max_completion_tokens=5000, response_format={"type":"json_object"}, messages=[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}])
            story=json.loads(res.choices[0].message.content.strip())
            scenes=story.get("scenes")
            if isinstance(scenes,list) and len(scenes)==4:
                for sc in scenes:
                    sc.setdefault("action",""); sc.setdefault("scene_image_prompt",""); sc.setdefault("video_prompt",""); sc.setdefault("camera",""); sc.setdefault("sound",""); sc.setdefault("music","")
                    dlg=sc.get("dialogue") or []; cd=[]
                    for it in dlg:
                        if isinstance(it,dict) and it.get("speaker") in VOICE_CONFIG and str(it.get("text","")).strip():
                            cd.append({"speaker":it.get("speaker"),"text":str(it.get("text")).strip(),"emotion":it.get("emotion","neutral")}); break
                    sc["dialogue"]=cd
                return story
        except Exception as e: log(f"Groq fail {e}"); time.sleep(1)
    raise RuntimeError("Story gen failed")

async def tts_async(text,config,output):
    com=edge_tts.Communicate(text=text, voice=config["voice"], rate=config["rate"], pitch=config["pitch"]); await com.save(str(output))
def make_tts(text,speaker,output):
    asyncio.run(tts_async(text, VOICE_CONFIG[speaker], output)); return output
def run_cmd(cmd, timeout=300):
    p=subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode!=0: raise RuntimeError(p.stderr[-6000:])
    return p
def ffprobe_duration(path):
    try:
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True); return float(p.stdout.strip())
    except: return 0.0
def make_silence(out,dur): run_cmd(["ffmpeg","-y","-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000","-t",str(dur),"-c:a","pcm_s16le",str(out)],60)
def fit_audio(src,out,dur): run_cmd(["ffmpeg","-y","-i",str(src),"-af",f"apad,atrim=0:{dur},asetpts=N/SR/TB","-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)],120); return out
def normalize_video(src,out,dur=5):
    vf=f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},setsar=1"
    run_cmd(["ffmpeg","-y","-i",str(src),"-vf",vf,"-an","-t",str(dur),"-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300); return out
def concat_videos(videos,out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos:
            safe=str(v).replace("'", "_")
            f.write(f"file '{safe}'\n")
    run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-an","-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",str(out)],300); return out
def generate_image(prompt,out):
    tid=wavespeed_submit(IMAGE_MODEL, {"prompt":prompt, "size":IMAGE_SIZE, "seed":-1, "output_format":"jpeg"}); url=wavespeed_wait(tid,600); return download_file(url,out)
def generate_video(image_url,prompt,out):
    full=f"LIVE-ACTION PHOTOREALISTIC NETFLIX FANTASY. {CAST_BIBLE} SCENE: {prompt} Real human actors natural movement cinematic camera fill vertical frame no black borders."
    tid=wavespeed_submit(VIDEO_MODEL, {"image":image_url, "prompt":full, "negative_prompt":"black screen black borders cartoon anime plastic skin deformed hands text subtitle logo", "duration":5, "seed":-1}); url=wavespeed_wait(tid,900); return download_file(url,out)
def generate_lipsync(v_url,a_url,emo,out):
    tid=wavespeed_submit(LIPSYNC_MODEL, {"video":v_url, "audio":a_url, "emotion":emo, "model_mode":LIPSYNC_MODE}); url=wavespeed_wait(tid,900); return download_file(url,out)
def generate_sfx(v_url,prompt,out):
    tid=wavespeed_submit(SFX_MODEL, {"video":v_url, "prompt":prompt or "realistic cinematic environment wind footsteps cloth", "negative_prompt":"speech dialogue singing music", "num_inference_steps":25, "duration":5, "guidance_scale":4.5, "mask_away_clip":False}); url=wavespeed_wait(tid,600); return download_file(url,out)
def generate_music(prompt,out):
    tid=wavespeed_submit(MUSIC_MODEL, {"prompt":prompt, "duration":TOTAL_DURATION}); url=wavespeed_wait(tid,600); return download_file(url,out)
def mix_sfx(files,out):
    if not files: make_silence(out,TOTAL_DURATION); return out
    cmd=["ffmpeg","-y"]; labels=[]
    for i,p in enumerate(files): cmd+=["-i",str(p)]; labels.append(f"[{i}:a]")
    fc="".join(labels)+f"amix=inputs={len(files)}:duration=longest:dropout_transition=0,volume=0.52,apad,atrim=0:{TOTAL_DURATION}[a]"
    cmd+=["-filter_complex",fc,"-map","[a]","-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)]; run_cmd(cmd,180); return out
def create_dialogue_track(items,out):
    if not items: make_silence(out,TOTAL_DURATION); return out
    cmd=["ffmpeg","-y"]; labels=[]
    for i,(idx,aud) in enumerate(items): cmd+=["-i",str(aud)]; delay=idx*SHOT_DURATION*1000; labels.append(f"[{i}:a]adelay={delay}|{delay}[d{i}]")
    inputs="".join(f"[d{i}]" for i in range(len(items)))
    fc=";".join(labels)+";"+inputs+f"amix=inputs={len(items)}:duration=longest:dropout_transition=0,apad,atrim=0:{TOTAL_DURATION},volume=1.0[a]"
    cmd+=["-filter_complex",fc,"-map","[a]","-ar","48000","-ac","2","-c:a","pcm_s16le",str(out)]; run_cmd(cmd,180); return out
def final_mix(video,dialogue,sfx,music,out):
    inputs=["-i",str(video),"-i",str(dialogue),"-i",str(sfx)]
    if music and Path(music).exists(): inputs+=["-i",str(music)]; fc="[1:a]volume=1.0[voice];[2:a]volume=1[effects];[3:a]volume=0.10[music];[voice][effects][music]amix=inputs=3:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11[audio]"
    else: fc="[1:a]volume=1.0[audio]"
    cmd=["ffmpeg","-y"]+inputs+["-filter_complex",fc,"-map","0:v:0","-map","[audio]","-c:v","copy","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",str(out)]; run_cmd(cmd,300); return out
def add_arabic_subtitles(video_in, dialogue_text, out):
    safe = dialogue_text.replace("'", "").replace(":", "").replace("\n", " ")
    vf = f"drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf:text='{safe}':fontcolor=white:fontsize=26:box=1:boxcolor=black@0.6:boxborderw=6:x=(w-text_w)/2:y=h-110"
    run_cmd(["ffmpeg","-y","-i",str(video_in),"-vf",vf,"-c:a","copy",str(out)])

TELEGRAM_API=f"https://api.telegram.org/bot{BOT_TOKEN}"
def telegram(method,data=None,files=None,timeout=60):
    r=requests.post(f"{TELEGRAM_API}/{method}", data=data, files=files, timeout=timeout); r.raise_for_status(); b=r.json()
    if not b.get("ok"): raise RuntimeError(f"Telegram {method}: {b}")
    return b
def send_text(chat_id,text): return telegram("sendMessage", {"chat_id":chat_id, "text":text})
def send_video(chat_id,path,caption):
    try:
        with Path(path).open("rb") as f: return telegram("sendVideo", {"chat_id":chat_id, "caption":caption, "supports_streaming":"true"}, {"video":("episode.mp4",f,"video/mp4")}, 300)
    except Exception as e:
        with Path(path).open("rb") as f: return telegram("sendDocument", {"chat_id":chat_id, "caption":caption}, {"document":("episode.mp4",f,"video/mp4")}, 300)

def process_story(chat_id,user_idea):
    work=Path(tempfile.mkdtemp(prefix="abosaraj_"))
    try:
        send_text(chat_id, "🎬 بدأ انتاج NETFLIX TIGER...\n🐯 نمر ضخم بحجم فيلين\n🐺 ذئبة موالفة\n⚡ قوة خارقة مخفية")
        story=create_story(user_idea)
        videos=[]; sfx_files=[]; dialogue_items=[]; lip_done=0
        for index,scene in enumerate(story["scenes"]):
            no=index+1; log(f"SCENE {no}/4")
            img=work / f"scene_{no}.jpg"; raw=work / f"scene_{no}_raw.mp4"; norm=work / f"scene_{no}.mp4"
            iprompt=f"{CAST_BIBLE} SCENE {no} ACTION: {scene.get('action','')} VISUAL: {scene.get('scene_image_prompt','')} Vertical cinematic full-frame no black borders no text"
            generate_image(iprompt, img); iurl=upload_to_wavespeed(img)
            vprompt=f"{scene.get('video_prompt','')} Camera: {scene.get('camera','')}"
            generate_video(iurl, vprompt, raw); normalize_video(raw, norm, SHOT_DURATION)
            dlg=(scene.get("dialogue") or [])
            if dlg:
                sp=dlg[0].get("speaker"); txt=str(dlg[0].get("text","")).strip(); emo=dlg[0].get("emotion","neutral")
                if sp in VOICE_CONFIG and txt:
                    mp3=work / f"scene_{no}_{sp}.mp3"; wav=work / f"scene_{no}_{sp}.wav"
                    make_tts(txt, sp, mp3); fit_audio(mp3, wav, SHOT_DURATION); dialogue_items.append((index, wav))
                    try: subbed=work / f"scene_{no}_sub.mp4"; add_arabic_subtitles(norm, txt, subbed); shutil.copy2(subbed, norm)
                    except Exception as e: log(f"subtitle fail {e}")
                    if LIPSYNC_ENABLED and lip_done < MAX_LIPSYNC_SCENES:
                        try:
                            dur=ffprobe_duration(mp3) or 3.0; dur=max(1.0, min(15.0, dur))
                            lip_aud=work / f"scene_{no}_lip.wav"; fit_audio(mp3, lip_aud, dur)
                            up_aud=upload_to_wavespeed(lip_aud); up_vid=upload_to_wavespeed(norm)
                            lip_v=work / f"scene_{no}_lip.mp4"; generate_lipsync(up_vid, up_aud, emo, lip_v)
                            fixed=work / f"scene_{no}_lip_fixed.mp4"; normalize_video(lip_v, fixed, SHOT_DURATION); shutil.copy2(fixed, norm); lip_done+=1
                        except Exception as e: log(f"Lip-sync fail {e}")
            videos.append(norm)
            if SOUND_DESIGN_ENABLED:
                try: vurl=upload_to_wavespeed(norm); sfx=work / f"scene_{no}_sfx.wav"; generate_sfx(vurl, scene.get("sound",""), sfx); sfx_files.append(sfx)
                except Exception as e: log(f"SFX fail {e}")
        silent=work / "silent.mp4"; concat_videos(videos, silent)
        d_track=work / "dialogue.wav"; create_dialogue_track(dialogue_items, d_track)
        s_track=work / "sfx.wav"; mix_sfx(sfx_files, s_track)
        m_file=None
        if MUSIC_ENABLED:
            try: m_file=work / "music.mp3"; generate_music("dark romantic medieval fantasy cinematic orchestral mysterious emotional strings deep drums heroic supernatural no vocals", m_file)
            except Exception as e: log(f"Music fail {e}"); m_file=None
        final=work / "Abosaraj_Tiger.mp4"; final_mix(silent, d_track, s_track, m_file, final)
        send_text(chat_id, f"✅ اكتمل\n🎬 {story.get('title','Tiger Battle')}\n🐯 نمر ضخم\n👄 Lip-sync {lip_done}/4"); send_video(chat_id, final, f"🎬 {story.get('title','')} \n🐯 vs ⚡ Giant Tiger Netflix 20s")
    except Exception as e:
        log(f"ERROR {repr(e)}")
        try: send_text(chat_id, "❌ خطأ:\n"+str(e)[:3500])
        except: pass
    finally: shutil.rmtree(work, ignore_errors=True);
    with plock: processing_chats.discard(chat_id)

def handle_update(update):
    msg=update.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    if text=="/start": send_text(chat, "🎬 أبو سراج Netflix Tiger جاهز\n🐯 نمر بحجم فيلين\nابعت فكرة القصة"); return
    if text=="/ping": send_text(chat, "🟢 شغال"); return
    if text=="/health": send_text(chat, f"🟢 Online\nTEST_MODE={TEST_MODE}\nLIPSYNC={LIPSYNC_ENABLED}\n{VIDEO_WIDTH}x{VIDEO_HEIGHT}"); return
    if text=="/test": idea="رجل غامض بقوة خارقة زرقاء يتظاهر بالضعف امام الملك الذي يرفضه لحب ابنته الاميرة لديه ذئبة بيضاء صغيرة موالفة فجأة يهجم نمر ضخم جدا بحجم فيلين على القصر في اللحظة الحاسمة يكشف البطل عن قوته الحقيقية المخفية ويواجه النمر العملاق ويهزمه ليحمي الاميرة دراما نتفلكس واقعية ترجمة عربية حركة شفاه"
    else: idea=text
    if not idea: return
    with plock:
        if chat in processing_chats: send_text(chat, "⏳ في انتاج شغال"); return
        processing_chats.add(chat)
    threading.Thread(target=process_story, args=(chat, idea), daemon=True).start()

@app.get("/")
def home(): return "Abosaraj Tiger Alive",200
@app.get("/health")
def health(): return {"ok":True, "test_mode":TEST_MODE, "lipsync":LIPSYNC_ENABLED, "video_size":f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}"},200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK",200

def setup_webhook():
    if TEST_MODE: log("TEST_MODE true skip webhook"); return
    if not RENDER_EXTERNAL_URL: log("RENDER_EXTERNAL_URL missing"); return
    url=f"{RENDER_EXTERNAL_URL}/telegram/webhook"
    try: telegram("setWebhook", {"url":url, "drop_pending_updates":"true"}); log(f"Webhook {url}")
    except Exception as e: log(f"Webhook fail {e}")

if __name__=="__main__":
    log("================================")
    log(f"TEST_MODE={TEST_MODE} LIPSYNC={LIPSYNC_ENABLED} MAX={MAX_LIPSYNC_SCENES}")
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
