import os,json,time,asyncio,shutil,tempfile,threading,subprocess
from pathlib import Path
import requests,edge_tts
from flask import Flask,request
from groq import Groq

# ================= CONFIG =================
BOT_TOKEN=os.environ["BOT_TOKEN"]
GROQ_API_KEY=os.environ["GROQ_API_KEY"]
WAVESPEED_API_KEY=os.getenv("WAVESPEED_API_KEY","")
PORT=int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL=os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")
GROQ_MODEL=os.getenv("GROQ_MODEL","openai/gpt-oss-120b")

def envbool(k,d):
    return os.getenv(k,d).lower() in ("1","true","yes","on")

TEST_MODE=envbool("TEST_MODE","true")
LIPSYNC_ENABLED=envbool("LIPSYNC_ENABLED","false")
SOUND_DESIGN_ENABLED=envbool("SOUND_DESIGN_ENABLED","true")
MUSIC_ENABLED=envbool("MUSIC_ENABLED","true")

LIPSYNC_MODE=os.getenv("LIPSYNC_MODE","face")
LIPSYNC_EMOTION=os.getenv("LIPSYNC_EMOTION","neutral")
MAX_LIPSYNC_SCENES=int(os.getenv("MAX_LIPSYNC_SCENES","1"))
PRODUCTION_SCENE_LIMIT=int(os.getenv("PRODUCTION_SCENE_LIMIT","0"))

MMAUDIO_STEPS=int(os.getenv("MMAUDIO_STEPS","25"))
MMAUDIO_GUIDANCE=float(os.getenv("MMAUDIO_GUIDANCE","4.5"))

VOICE_VOLUME=float(os.getenv("VOICE_VOLUME","1.0"))
SFX_VOLUME=float(os.getenv("SFX_VOLUME","0.62"))
MUSIC_VOLUME=float(os.getenv("MUSIC_VOLUME","0.12"))
AMBIENCE_VOLUME=float(os.getenv("AMBIENCE_VOLUME","0.14"))

SHOT_COUNT=4
SHOT_DURATION=5
TOTAL_DURATION=SHOT_COUNT*SHOT_DURATION
W,H,FPS=720,1280,24

VOICES={
    "male_lead":("ar-SY-LaithNeural","-10%","-3Hz"),
    "princess":("ar-SA-ZariyahNeural","-6%",None),
    "king":("ar-EG-ShakirNeural","-8%","-4Hz"),
    "guard":("ar-IQ-BasselNeural","-2%","-1Hz"),
    "narrator":("ar-SA-HamedNeural","-8%","-2Hz")
}

BASE="https://api.wavespeed.ai/api/v3"
IMAGE_MODEL="wavespeed-ai/z-image/turbo"
VIDEO_MODEL="wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL="sync/react-1"
SFX_MODEL="wavespeed-ai/mmaudio-v2"
MUSIC_MODEL="wavespeed-ai/ace-step/prompt-to-audio"

app=Flask(__name__)
groq=Groq(api_key=GROQ_API_KEY)
lock=threading.Lock()
processing=set()

def log(x):
    with lock:
        print(f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {x}",flush=True)

def headers():
    return {
        "Authorization":f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type":"application/json"
    }

def get_headers():
    return {"Authorization":f"Bearer {WAVESPEED_API_KEY}"}

# ================= WAVESPEED =================
def ws_submit(model,payload):
    if TEST_MODE:
        raise RuntimeError("WaveSpeed blocked: TEST_MODE=true")
    if not WAVESPEED_API_KEY:
        raise RuntimeError("WAVESPEED_API_KEY missing")

    r=requests.post(
        f"{BASE}/{model}",
        headers=headers(),json=payload,timeout=(10,60)
    )
    r.raise_for_status()
    b=r.json()

    if b.get("code")!=200:
        raise RuntimeError(b.get("message","WaveSpeed error"))

    tid=b.get("data",{}).get("id")
    if not tid:
        raise RuntimeError(f"No WaveSpeed task id: {b}")

    log(f"WaveSpeed task: {tid}")
    return tid

def ws_wait(tid,timeout=900):
    started=time.time()

    while time.time()-started<timeout:
        r=requests.get(
            f"{BASE}/predictions/{tid}/result",
            headers=get_headers(),timeout=30
        )
        r.raise_for_status()
        b=r.json()

        if b.get("code")!=200:
            raise RuntimeError(b)

        d=b["data"]
        status=str(d.get("status","")).lower()
        log(f"Task {tid}: {status}")

        if status=="completed":
            out=d.get("outputs")
            if not out:
                raise RuntimeError(f"No output: {b}")
            x=out[0]
            if isinstance(x,dict):
                for k in ("url","audio_url","video_url","download_url"):
                    if x.get(k):
                        return x[k]
            return x

        if status in ("failed","cancelled","timeout","deleted"):
            raise RuntimeError(f"WaveSpeed failed: {b}")

        time.sleep(2)

    raise TimeoutError(f"WaveSpeed timeout: {tid}")

def upload(path):
    if TEST_MODE:
        raise RuntimeError("Upload blocked in TEST_MODE")

    p=Path(path)
    r=requests.post(
        f"{BASE}/media/uploads",
        headers=headers(),
        json={"filename":p.name,"size":p.stat().st_size},
        timeout=30
    )
    r.raise_for_status()
    d=r.json()["data"]

    with p.open("rb") as f:
        u=requests.put(
            d["upload"]["url"],
            headers=d["upload"]["headers"],
            data=f,timeout=300
        )
    u.raise_for_status()
    return d["download_url"]

def download(url,path):
    r=requests.get(url,timeout=180)
    r.raise_for_status()
    Path(path).write_bytes(r.content)
    return path

# ================= SCHEMAS =================
CHAR={
    "type":"object","additionalProperties":False,
    "properties":{x:{"type":"string"} for x in
                  ("identity","age","face","hair","clothes","colors")},
    "required":["identity","age","face","hair","clothes","colors"]
}

DIALOGUE={
    "type":"object","additionalProperties":False,
    "properties":{
        "speaker":{"type":"string","enum":
                   ["male_lead","princess","king","guard","narrator"]},
        "text":{"type":"string"},
        "emotion":{"type":"string"}
    },
    "required":["speaker","text","emotion"]
}

SCENE={
    "type":"object","additionalProperties":False,
    "properties":{
        "action":{"type":"string"},
        "dialogue":{"type":"array","minItems":1,"maxItems":1,"items":DIALOGUE},
        "scene_image_prompt":{"type":"string"},
        "video_prompt":{"type":"string"},
        "camera":{"type":"string"},
        "sound":{"type":"string"},
        "music":{"type":"string"}
    },
    "required":[
        "action","dialogue","scene_image_prompt","video_prompt",
        "camera","sound","music"
    ]
}

STORY={
    "type":"object","additionalProperties":False,
    "properties":{
        "title":{"type":"string"},
        "hook":{"type":"string"},
        "cast":{
            "type":"object","additionalProperties":False,
            "properties":{x:CHAR for x in
                          ("male_lead","princess","wolf","king")},
            "required":["male_lead","princess","wolf","king"]
        },
        "visual_style":{"type":"string"},
        "cast_reference_prompt":{"type":"string"},
        "scenes":{
            "type":"array","minItems":4,"maxItems":4,
            "items":SCENE
        }
    },
    "required":[
        "title","hook","cast","visual_style",
        "cast_reference_prompt","scenes"
    ]
}

# ================= GROQ =================
def create_story(idea):
    system=f"""
أنت كاتب سيناريو ومخرج ومصور ومشرف استمرارية ومصمم صوت سينمائي عربي.

اصنع Microdrama عربي:
Dark Fantasy + Romance + Mystery + Suspense + Supernatural Drama.

العالم:
رجل غامض جذاب يمتلك قوة خارقة،
أميرة تقع في حبه،
ملك يعرف سراً خطيراً عنه،
وذئبة بيضاء صغيرة لا تتكلم.

الفكرة:
{idea}

اصنع {SHOT_COUNT} مشاهد، كل مشهد {SHOT_DURATION} ثوانٍ.

الشكل البصري:
realistic cinematic Arabic fantasy drama,
photorealistic, realistic skin, fabric and fur,
professional film lighting, volumetric moonlight,
fog, shallow depth of field, anamorphic cinematic look,
dramatic rim light, realistic hands and eyes,
natural facial acting, breathing, hair and cloth movement,
vertical 9:16, high production value.

ممنوع:
anime, cartoon, illustration, game art, plastic skin,
text, logo, watermark, deformed hands, extra fingers.

كل مشهد:
- متحدث بشري واحد فقط.
- حوار قصير 5-12 كلمة.
- الذئبة لا تتكلم، فقط breathing/whimper/growl/howl.
- sound عبارة عن تصميم صوت تنفيذي متزامن مع الحدث:
environment, Foley, footsteps, cloth, hair, wood, metal,
wolf sounds, wind, leaves, whoosh, impact, supernatural energy,
riser, stinger, والتوقيت التقريبي.
- لا تضع الموسيقى داخل sound.
- music وصف لموسيقى سينمائية instrumental فقط، بلا كلمات أو غناء.
- حافظ على استمرارية الشخصيات.
- لا تحل الأسرار.
- نهاية كل مشهد تدفع للمشهد التالي.
- JSON فقط.
"""

    r=groq.chat.completions.create(
        model=GROQ_MODEL,
        temperature=.75,
        response_format={
            "type":"json_schema",
            "json_schema":{
                "name":"abosaraj_story",
                "strict":True,
                "schema":STORY
            }
        },
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":
             f"حوّل الفكرة إلى {SHOT_COUNT} مشاهد مدة كل منها "
             f"{SHOT_DURATION} ثوانٍ. JSON فقط.\n\n{idea}"}
        ]
    )

    content=r.choices[0].message.content
    if not content:
        raise RuntimeError("Groq returned empty story")

    story=json.loads(content)

    if len(story.get("scenes",[]))!=SHOT_COUNT:
        raise RuntimeError("Wrong scene count")

    return story

# ================= FFMPEG =================
def cmd(c,timeout=300):
    log("CMD: "+" ".join(map(str,c)))
    r=subprocess.run(
        c,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,timeout=timeout
    )
    if r.returncode:
        log(r.stderr[-4000:])
        raise RuntimeError(f"FFmpeg failed: {r.returncode}")
    return r

def duration(path):
    r=subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1",str(path)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True
    )
    try:return float(r.stdout.strip())
    except:return 0

def audio_wav(inp,out,dur=None):
    c=[
        "ffmpeg","-y","-i",str(inp),
        "-ac","1","-ar","24000","-sample_fmt","s16"
    ]
    if dur:c+=["-t",str(dur)]
    c+=[str(out)]
    cmd(c,120)
    return out

def fit_audio(inp,out,dur):
    cmd([
        "ffmpeg","-y","-i",str(inp),
        "-af",f"apad,atrim=0:{dur},asetpts=N/SR/TB",
        "-t",str(dur),"-ar","48000","-ac","2",str(out)
    ],120)
    return out

def silent(dur,out):
    cmd([
        "ffmpeg","-y","-f","lavfi","-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t",str(dur),"-ar","48000","-ac","2",str(out)
    ],120)
    return out

# ================= TTS =================
async def tts(text,voice,out,rate="-5%",pitch=None):
    x={"text":text,"voice":voice,"rate":rate}
    if pitch:x["pitch"]=pitch
    await edge_tts.Communicate(**x).save(str(out))

def make_voice(text,speaker,out):
    voice,rate,pitch=VOICES.get(speaker,VOICES["narrator"])
    log(f"TTS {speaker}: {text}")
    asyncio.run(tts(text,voice,out,rate,pitch))
    return out

def dialogue_track(scenes,dur,workdir):
    items=[]

    for i,s in enumerate(scenes):
        d=(s.get("dialogue") or [{}])[0]
        text=str(d.get("text","")).strip()
        speaker=d.get("speaker","narrator")

        if not text:continue

        mp3=workdir/f"d{i}.mp3"
        wav=workdir/f"d{i}.wav"
        fit=workdir/f"d{i}_fit.wav"

        make_voice(text,speaker,mp3)
        audio_wav(mp3,wav)
        fit_audio(wav,fit,SHOT_DURATION)
        items.append((i,fit))

    if not items:
        return silent(dur,workdir/"dialogue.wav")

    c=["ffmpeg","-y"]
    f=[];labels=[]

    for n,(i,p) in enumerate(items):
        c+=["-i",str(p)]
        lab=f"d{n}"
        delay=i*SHOT_DURATION*1000
        f.append(
            f"[{n}:a]adelay={delay}|{delay},aresample=48000,"
            f"aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo[{lab}]"
        )
        labels.append(f"[{lab}]")

    if len(labels)==1:
        f.append(
            f"{labels[0]}atrim=0:{dur},"
            f"asetpts=N/SR/TB[out]"
        )
    else:
        f.append(
            "".join(labels)+
            f"amix=inputs={len(labels)}:duration=longest:"
            f"dropout_transition=0,atrim=0:{dur},"
            f"asetpts=N/SR/TB[out]"
        )

    out=workdir/"dialogue.wav"
    c+=["-filter_complex",";".join(f),"-map","[out]",
        "-t",str(dur),"-ar","48000","-ac","2",str(out)]
    cmd(c,180)
    return out

# ================= TEST VIDEO =================
def test_scene(i,out):
    a=["0x17101f","0x10202a","0x201710","0x111c14"][i%4]
    b=["0x382345","0x193d4a","0x4b3018","0x203c25"][i%4]

    vf=(
        f"color=c={a}:s={W}x{H}:r={FPS}:d={SHOT_DURATION},"
        "format=yuv420p,"
        "drawbox=x='100+80*sin(t)':y='250+120*cos(t)':"
        "w=520:h=700:"
        f"color={b}@0.35:t=fill,"
        "drawbox=x='220+100*cos(t*.5)':"
        "y='500+70*sin(t)':w=280:h=280:"
        "color=white@0.07:t=fill"
    )

    cmd([
        "ffmpeg","-y","-f","lavfi","-i",vf,
        "-t",str(SHOT_DURATION),"-r",str(FPS),
        "-an","-c:v","libx264","-preset","veryfast",
        "-pix_fmt","yuv420p",str(out)
    ],120)
    return out

# ================= IMAGE / VIDEO =================
def make_image(prompt,out):
    if TEST_MODE:return None
    tid=ws_submit(IMAGE_MODEL,{
        "prompt":prompt,
        "size":"720*1280"
    })
    return download(ws_wait(tid,600),out)

def cast_reference(story,out):
    prompt=story.get("cast_reference_prompt") or """
Photorealistic cinematic Arabic fantasy cast reference.
Exactly four recurring characters:
handsome mysterious supernatural man,
beautiful Arabian princess,
small white female wolf pup,
powerful Arab king.
Consistent identity, clothing and proportions.
Realistic skin, fabric and fur, moonlight, fog,
anamorphic film look, vertical 9:16.
No text, logo, watermark, anime, cartoon or illustration.
"""
    return make_image(prompt,out)

def scene_video(image,prompt,out):
    if TEST_MODE:return None

    image_url=upload(image)

    prompt=f"""
{prompt}

Natural cinematic motion:
eyes, blinking, facial expression, mouth,
breathing, head, shoulders, hands, fingers,
body weight, walking when appropriate,
hair, clothing, fog and animal fur.

Preserve exact identity, face, clothing,
anatomy and environment.

No morphing, extra fingers, deformed hands,
duplicated characters, text, logo or watermark.
Photorealistic cinematic vertical 9:16.
"""

    tid=ws_submit(
        VIDEO_MODEL,
        {
            "image":image_url,
            "prompt":prompt,
            "duration":SHOT_DURATION,
            "resolution":"480p"
        }
    )

    return download(ws_wait(tid,900),out)

# ================= LIP SYNC =================
def lipsync(video,audio,out):
    if TEST_MODE or not LIPSYNC_ENABLED:
        return video

    vu=upload(video)
    au=upload(audio)

    tid=ws_submit(
        LIPSYNC_MODEL,
        {
            "video":vu,
            "audio":au,
            "model_mode":LIPSYNC_MODE,
            "emotion":LIPSYNC_EMOTION
        }
    )

    return download(ws_wait(tid,900),out)
    # ================= CINEMATIC SFX =================
def scene_sfx(video,sound,idx,workdir):
    if not SOUND_DESIGN_ENABLED or TEST_MODE or not video:
        return None

    vu=upload(video)

    prompt=f"""
Create cinematic synchronized sound design for this exact 5-second video.

Follow visible actions precisely.

Cue sheet:
{sound}

Include:
realistic environment, Foley, footsteps, cloth, hair,
wood, metal, wind, leaves, animal sounds,
wolf breathing/whimper/growl/howl when visible,
whooshes, impacts, supernatural energy,
low rumbles, risers, stingers and spatial perspective.

Match timing and physical movement.

NO speech, dialogue, narration, singing, lyrics,
music, melody or voice.

Professional cinematic film sound.
Natural dynamic range. No clipping.
"""

    tid=ws_submit(
        SFX_MODEL,
        {
            "video":vu,
            "prompt":prompt,
            "duration":SHOT_DURATION,
            "steps":MMAUDIO_STEPS,
            "guidance_scale":MMAUDIO_GUIDANCE,
            "negative_prompt":
                "speech, dialogue, narration, singing, lyrics, "
                "music, melody, voice, distortion, clipping, noise"
        }
    )

    raw=workdir/f"sfx_{idx}.wav"
    out=workdir/f"sfx_{idx}_norm.wav"

    download(ws_wait(tid,900),raw)
    return audio_wav(raw,out,SHOT_DURATION)

def sfx_track(files,dur,workdir):
    valid=[(i,p) for i,p in enumerate(files)
           if p and Path(p).exists()]

    if not valid:
        return silent(dur,workdir/"sfx.wav")

    c=["ffmpeg","-y"]
    f=[];labels=[]

    for n,(i,p) in enumerate(valid):
        c+=["-i",str(p)]
        lab=f"s{n}"
        delay=i*SHOT_DURATION*1000

        f.append(
            f"[{n}:a]adelay={delay}|{delay},"
            f"aresample=48000,volume={SFX_VOLUME},"
            f"aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo[{lab}]"
        )
        labels.append(f"[{lab}]")

    if len(labels)==1:
        f.append(
            f"{labels[0]}atrim=0:{dur},"
            f"asetpts=N/SR/TB[out]"
        )
    else:
        f.append(
            "".join(labels)+
            f"amix=inputs={len(labels)}:duration=longest:"
            f"dropout_transition=0,atrim=0:{dur},"
            f"asetpts=N/SR/TB[out]"
        )

    out=workdir/"sfx.wav"

    c+=["-filter_complex",";".join(f),
        "-map","[out]","-t",str(dur),
        "-ar","48000","-ac","2",str(out)]

    cmd(c,180)
    return out

# ================= MUSIC =================
def music_prompt(story,scenes):
    cues="\n".join(
        f"Scene {i+1}: {s.get('music','')}"
        for i,s in enumerate(scenes)
        if s.get("music")
    )

    return f"""
Instrumental cinematic score for Arabic dark fantasy romance mystery.

Title:
{story.get('title','Dark Arabic Fantasy')}

Mood:
romantic mystery, supernatural dread, ancient secret,
night forest, royal palace, forbidden love, danger,
emotional tension, slow escalation, unresolved cliffhanger.

Instrumentation:
oud-like plucked texture, low cinematic strings,
deep cello, soft frame drum, subtle Arabic percussion,
atmospheric pads, distant choir-like texture WITHOUT WORDS,
deep sub bass, sparse piano, metallic supernatural textures.

Begin intimate and mysterious.
Gradually increase tension.
Dark harmonic movement.
Supernatural revelation.
Strong unresolved ending.

Absolutely instrumental.
No vocals, lyrics or spoken words.

Scene cues:
{cues}
"""

def music_track(story,scenes,dur,workdir):
    if not MUSIC_ENABLED or TEST_MODE:
        return None

    tid=ws_submit(
        MUSIC_MODEL,
        {
            "prompt":music_prompt(story,scenes),
            "duration":int(max(5,min(240,dur))),
            "instrumental":True,
            "seed":24117
        }
    )

    raw=workdir/"music_raw.wav"
    out=workdir/"music.wav"

    download(ws_wait(tid,900),raw)

    cmd([
        "ffmpeg","-y","-i",str(raw),
        "-af",
        f"aresample=48000,volume={MUSIC_VOLUME},"
        f"atrim=0:{dur},asetpts=N/SR/TB",
        "-t",str(dur),"-ar","48000","-ac","2",
        str(out)
    ],180)

    return out

# ================= AMBIENCE / MIX =================
def ambience(dur,out):
    cmd([
        "ffmpeg","-y","-f","lavfi","-i",
        "anoisesrc=color=brown:amplitude=0.015:sample_rate=48000",
        "-af",
        f"highpass=f=35,lowpass=f=5000,"
        f"volume={AMBIENCE_VOLUME},atrim=0:{dur}",
        "-t",str(dur),"-ar","48000","-ac","2",str(out)
    ],120)
    return out

def final_mix(dialogue,sfx,music,amb,dur,out):
    paths=[dialogue,sfx,music,amb]
    c=["ffmpeg","-y"]

    for p in paths:
        if p and Path(p).exists():
            c+=["-i",str(p)]
        else:
            c+=[
                "-f","lavfi","-t",str(dur),
                "-i","anullsrc=channel_layout=stereo:"
                "sample_rate=48000"
            ]

    filters=[
        f"[0:a]volume={VOICE_VOLUME},atrim=0:{dur},"
        f"asetpts=N/SR/TB[v]",

        f"[1:a]atrim=0:{dur},"
        f"asetpts=N/SR/TB[s]",

        f"[2:a]atrim=0:{dur},"
        f"asetpts=N/SR/TB[m]",

        f"[3:a]atrim=0:{dur},"
        f"asetpts=N/SR/TB[a]",

        "[v][s][m][a]"
        "amix=inputs=4:duration=longest:"
        "dropout_transition=0,"
        "alimiter=limit=0.95:attack=5:release=50,"
        "aresample=48000[out]"
    ]

    c+=[
        "-filter_complex",";".join(filters),
        "-map","[out]",
        "-t",str(dur),
        "-ar","48000","-ac","2",
        "-c:a","aac","-b:a","192k",
        str(out)
    ]

    cmd(c,240)
    return out

# ================= VIDEO =================
def normalize_video(inp,out):
    cmd([
        "ffmpeg","-y","-i",str(inp),
        "-vf",
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p",
        "-r",str(FPS),
        "-an","-c:v","libx264",
        "-preset","veryfast","-crf","18",
        "-pix_fmt","yuv420p",
        "-t",str(SHOT_DURATION),
        str(out)
    ],180)
    return out

def concat_videos(files,out):
    lf=Path(out).parent/"concat.txt"
    lines=[]

    for x in files:
        p=str(Path(x).resolve()).replace("'","'\\''")
        lines.append("file '"+p+"'")

    lf.write_text("\n".join(lines),encoding="utf-8")

    cmd([
        "ffmpeg","-y",
        "-f","concat","-safe","0",
        "-i",str(lf),
        "-c","copy",str(out)
    ],180)

    return out

def attach_audio(video,audio,dur,out):
    cmd([
        "ffmpeg","-y",
        "-i",str(video),
        "-i",str(audio),
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy",
        "-c:a","aac","-b:a","192k",
        "-ar","48000","-ac","2",
        "-t",str(dur),
        "-movflags","+faststart",
        str(out)
    ],240)
    return out

# ================= SUBTITLES =================
def ass_time(x):
    x=max(0,float(x))
    h=int(x//3600)
    m=int((x%3600)//60)
    s=x%60
    sec=int(s)
    cs=int(round((s-sec)*100))

    if cs>=100:
        sec+=1
        cs=0

    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"

def ass_escape(x):
    return str(x).replace("\\","\\\\").replace("{","\\{").replace("}","\\}")

def make_ass(scenes,out):
    lines=[
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding",
        "Style: Default,Arial,15,&H00FFFFFF,&H00FFFFFF,"
        "&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,"
        "2,55,55,65,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text"
    ]

    for i,s in enumerate(scenes):
        d=(s.get("dialogue") or [{}])[0]
        text=str(d.get("text","")).strip()

        if text:
            lines.append(
                f"Dialogue: 0,{ass_time(i*SHOT_DURATION)},"
                f"{ass_time((i+1)*SHOT_DURATION)},Default,,"
                f"0,0,0,,{ass_escape(text)}"
            )

    Path(out).write_text("\n".join(lines),encoding="utf-8")
    return out

def burn_subtitles(video,ass,out):
    a=str(Path(ass)).replace("\\","\\\\").replace(":","\\:")

    cmd([
        "ffmpeg","-y",
        "-i",str(video),
        "-vf",f"subtitles='{a}'",
        "-c:v","libx264",
        "-preset","veryfast",
        "-crf","19",
        "-pix_fmt","yuv420p",
        "-c:a","copy",
        "-t",str(duration(video)),
        str(out)
    ],240)

    return out

# ================= PRODUCTION =================
def produce(story,workdir):
    workdir=Path(workdir)
    workdir.mkdir(parents=True,exist_ok=True)

    scenes=story.get("scenes",[])

    if PRODUCTION_SCENE_LIMIT>0:
        scenes=scenes[:PRODUCTION_SCENE_LIMIT]

    if not scenes:
        raise RuntimeError("No scenes")

    count=len(scenes)
    dur=count*SHOT_DURATION

    log(f"Producing {count} scenes / {dur}s")

    if not TEST_MODE:
        cast_reference(story,workdir/"cast_reference.png")

    voices=[]

    # -------- dialogue audio --------
    for i,s in enumerate(scenes):
        d=(s.get("dialogue") or [{}])[0]
        text=str(d.get("text","")).strip()
        speaker=d.get("speaker","narrator")

        fit=workdir/f"voice_{i}.wav"

        if text:
            mp3=workdir/f"voice_{i}.mp3"
            wav=workdir/f"voice_{i}_raw.wav"

            make_voice(text,speaker,mp3)
            audio_wav(mp3,wav)
            fit_audio(wav,fit,SHOT_DURATION)
        else:
            silent(SHOT_DURATION,fit)

        voices.append((speaker,fit))

    videos=[]
    sfx=[]

    # -------- scenes --------
    for i,s in enumerate(scenes):
        log(f"========== SCENE {i+1}/{count} ==========")

        base=workdir/f"scene_{i}_base.mp4"
        final=workdir/f"scene_{i}_final.mp4"
        norm=workdir/f"scene_{i}_norm.mp4"

        if TEST_MODE:
            test_scene(i,base)
        else:
            image=workdir/f"scene_{i}.png"
            make_image(s.get("scene_image_prompt",""),image)
            scene_video(image,s.get("video_prompt",""),base)

        speaker,audio=voices[i]

        if (
            LIPSYNC_ENABLED and
            not TEST_MODE and
            i<MAX_LIPSYNC_SCENES and
            speaker in {"male_lead","princess","king","guard","narrator"}
        ):
            lipsync(base,audio,final)
        else:
            shutil.copyfile(base,final)

        normalize_video(final,norm)
        videos.append(norm)

        if SOUND_DESIGN_ENABLED and not TEST_MODE:
            sfx.append(
                scene_sfx(
                    norm,
                    s.get("sound",""),
                    i,
                    workdir
                )
            )
        else:
            sfx.append(None)

    # -------- assemble --------
    episode=workdir/"episode.mp4"
    concat_videos(videos,episode)

    dialogue=dialogue_track(scenes,dur,workdir)
    sfx_audio=sfx_track(sfx,dur,workdir)

    music=(
        music_track(story,scenes,dur,workdir)
        if MUSIC_ENABLED and not TEST_MODE else None
    )

    amb=ambience(dur,workdir/"ambience.wav")

    mixed=final_mix(
        dialogue,
        sfx_audio,
        music,
        amb,
        dur,
        workdir/"final_audio.m4a"
    )

    with_audio=attach_audio(
        episode,mixed,dur,
        workdir/"with_audio.mp4"
    )

    ass=make_ass(
        scenes,
        workdir/"subtitles.ass"
    )

    final=burn_subtitles(
        with_audio,
        ass,
        workdir/"ABOSARAJ_FINAL.mp4"
    )

    d=duration(final)

    if not final.exists() or d<=0:
        raise RuntimeError("Final video invalid")

    log(f"FINAL READY: {final} / {d:.2f}s")

    return {
        "video":final,
        "duration":d,
        "scene_count":count
    }

# ================= TELEGRAM =================
def telegram(method,data=None,files=None):
    r=requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=data or {},
        files=files,
        timeout=180
    )
    r.raise_for_status()
    b=r.json()

    if not b.get("ok"):
        raise RuntimeError(b)

    return b

def send_message(chat,text):
    telegram("sendMessage",{"chat_id":chat,"text":text})

def send_video(chat,path,caption=""):
    with open(path,"rb") as f:
        telegram(
            "sendVideo",
            {
                "chat_id":chat,
                "caption":caption,
                "supports_streaming":"true"
            },
            {
                "video":(
                    Path(path).name,
                    f,
                    "video/mp4"
                )
            }
        )

# ================= STORY =================
def test_story():
    return """
أميرة تهرب ليلًا من القصر بعد أن يقرر والدها الملك تزويجها لرجل لا تحبه.
في الغابة تجد ذئبة بيضاء صغيرة مصابة وتحاول مساعدتها.
يظهر أمامها رجل غامض وجذاب كانت قد رأته من قبل،
ويخبرها أن عليها العودة للقصر لأن رجال الملك يبحثون عنها.
ترفض العودة وتكتشف أن الرجل يمتلك قوة خارقة يحاول إخفاءها.
يصل أحد حراس الملك ويأمر الأميرة بالعودة،
لكن الرجل يقف أمامها ويحميها بطريقة تكشف جزءًا من قوته.
قبل نهاية المشهد ترفع الذئبة الصغيرة رأسها وتنظر للرجل
وكأنها تعرفه منذ زمن، ثم تطلق عواءً غريبًا يجعل وجه الرجل يتغير فجأة.
من أين تعرف الذئبة هذا الرجل؟
"""

def status_text():
    n=SHOT_COUNT if PRODUCTION_SCENE_LIMIT==0 else PRODUCTION_SCENE_LIMIT
    return (
        f"Scenes: {n}\n"
        f"Duration: {n*SHOT_DURATION}s\n"
        f"SFX: {'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
        f"Music: {'ON' if MUSIC_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
        f"Lip-sync: {'ON' if LIPSYNC_ENABLED and not TEST_MODE else 'OFF'}"
    )

# ================= PROCESS =================
def process(chat,idea):
    if chat in processing:
        send_message(chat,"⏳ عندي حلقة قيد المعالجة بالفعل.")
        return

    processing.add(chat)
    workdir=Path(tempfile.mkdtemp(prefix="abosaraj_"))

    try:
        send_message(
            chat,
            "🎬 Abosaraj بدأ الإنتاج...\n\n"
            "✍️ السيناريو\n"
            "🎭 الشخصيات\n"
            "🎥 المشاهد\n"
            "🎙️ الأصوات\n"
            "🔊 Sound Design\n"
            "🎵 الموسيقى\n"
            "📝 الترجمة\n\n"
            "استنى شوي..."
        )

        story=create_story(idea)

        (workdir/"story.json").write_text(
            json.dumps(
                story,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        send_message(
            chat,
            f"🎬 السيناريو جاهز.\n"
            f"📖 {story.get('title','Untitled')}\n\n"
            "هلا بنبني الحلقة."
        )

        result=produce(story,workdir)

        caption=(
            "🎬 ABOSARAJ\n\n"
            f"📖 {story.get('title','حلقة جديدة')}\n"
            f"🎞️ {result['scene_count']} مشاهد\n"
            f"⏱️ {result['duration']:.1f} ثانية\n\n"
            "🎙️ Multi-Character Voices: ON\n"
            f"🔊 Cinematic SFX: "
            f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
            f"🎵 Cinematic Score: "
            f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
            "📝 Arabic Subtitles: ON"
        )

        if LIPSYNC_ENABLED and not TEST_MODE:
            caption+="\n👄 Lip Sync: ON"

        send_video(chat,result["video"],caption)
        send_message(chat,"✅ الحلقة وصلت.")

    except Exception as e:
        log("PRODUCTION ERROR: "+repr(e))

        try:
            send_message(
                chat,
                f"❌ صار خطأ:\n"
                f"{type(e).__name__}: {e}\n\n"
                "ابعتلي آخر Render Logs."
            )
        except Exception as x:
            log("Telegram error: "+repr(x))

    finally:
        processing.discard(chat)
        log(f"Workdir: {workdir}")

def start(chat,idea):
    threading.Thread(
        target=process,
        args=(chat,idea),
        daemon=True
    ).start()

# ================= FLASK =================
@app.get("/")
def home():
    return {
        "status":"ok",
        "service":"Abosaraj",
        "test_mode":TEST_MODE,
        "sound_design":SOUND_DESIGN_ENABLED,
        "music":MUSIC_ENABLED,
        "lipsync":LIPSYNC_ENABLED
    }

@app.get("/health")
def health():
    return {
        "status":"healthy",
        "service":"abosaraj",
        "test_mode":TEST_MODE,
        "wavespeed":bool(WAVESPEED_API_KEY),
        "groq":bool(GROQ_API_KEY),
        "sound_design":SOUND_DESIGN_ENABLED,
        "music":MUSIC_ENABLED,
        "lipsync":LIPSYNC_ENABLED,
        "production_scene_limit":PRODUCTION_SCENE_LIMIT
    }

@app.post("/webhook")
@app.post("/telegram/webhook")
def webhook():
    update=request.get_json(silent=True) or {}
    message=update.get("message") or {}
    chat=message.get("chat") or {}

    chat_id=chat.get("id")
    text=message.get("text")

    log(
        f"Telegram update received: "
        f"chat={chat_id}, text={text!r}"
    )

    if not chat_id or not text:
        return {"ok":True}

    text=text.strip()

    if text in ("/start","/help"):
        send_message(
            chat_id,
            "🎬 أهلاً في Abosaraj.\n\n"
            "ابعثلي فكرة القصة وأنا أحولها إلى Microdrama سينمائي.\n\n"
            "أو اكتب /test لتشغيل قصة الاختبار."
        )
        return {"ok":True}

    if text=="/test":
        send_message(chat_id,"🧪 اختبار Abosaraj بدأ.")
        start(chat_id,test_story())
        return {"ok":True}

    if text=="/status":
        send_message(
            chat_id,
            "🤖 Abosaraj Status\n\n"+
            status_text()+
            f"\n\nWan: {'READY' if WAVESPEED_API_KEY else 'NO KEY'}"+
            f"\nGroq: {'READY' if GROQ_API_KEY else 'NO KEY'}"
        )
        return {"ok":True}

    if len(text)<10:
        send_message(chat_id,"اكتب فكرة قصة أطول شوي.")
        return {"ok":True}

    send_message(
        chat_id,
        "🎬 وصلت الفكرة. ببدأ تحويلها إلى سيناريو سينمائي..."
    )

    start(chat_id,text)
    return {"ok":True}

# ================= START =================
def startup():
    log("========================================")
    log("ABOSARAJ AI VIDEO BOT")
    log("========================================")
    log(f"TEST_MODE={TEST_MODE}")
    log(f"SOUND_DESIGN={SOUND_DESIGN_ENABLED}")
    log(f"MUSIC={MUSIC_ENABLED}")
    log(f"LIPSYNC={LIPSYNC_ENABLED}")
    log(f"SHOT_COUNT={SHOT_COUNT}")
    log(f"SHOT_DURATION={SHOT_DURATION}")
    log(f"MMAUDIO_STEPS={MMAUDIO_STEPS}")
    log(f"MMAUDIO_GUIDANCE={MMAUDIO_GUIDANCE}")
    log("Webhook routes: /webhook + /telegram/webhook")
    log("========================================")

if __name__=="__main__":
    startup()
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
