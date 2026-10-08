import os,json,time,asyncio,shutil,tempfile,threading,subprocess
from pathlib import Path
import requests,edge_tts
from flask import Flask,request
from groq import Groq

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
LIPSYNC_MODE=os.getenv("LIPSYNC_MODE","face").lower()
DEFAULT_LIPSYNC_EMOTION=os.getenv("LIPSYNC_EMOTION","neutral")
MAX_LIPSYNC_SCENES=int(os.getenv("MAX_LIPSYNC_SCENES","1"))
PRODUCTION_SCENE_LIMIT=int(os.getenv("PRODUCTION_SCENE_LIMIT","0"))

SOUND_DESIGN_ENABLED=envbool("SOUND_DESIGN_ENABLED","true")
MMAUDIO_STEPS=int(os.getenv("MMAUDIO_STEPS","25"))
MMAUDIO_GUIDANCE=float(os.getenv("MMAUDIO_GUIDANCE","4.5"))

MUSIC_ENABLED=envbool("MUSIC_ENABLED","true")
MUSIC_VOLUME=float(os.getenv("MUSIC_VOLUME","0.12"))
SFX_VOLUME=float(os.getenv("SFX_VOLUME","0.62"))
AMBIENCE_VOLUME=float(os.getenv("AMBIENCE_VOLUME","0.14"))
VOICE_VOLUME=float(os.getenv("VOICE_VOLUME","1.0"))

SHOT_COUNT=4
SHOT_DURATION=5
TOTAL_DURATION=SHOT_COUNT*SHOT_DURATION

VIDEO_WIDTH=720
VIDEO_HEIGHT=1280
VIDEO_FPS=24

VOICE_CONFIG={
    "male_lead":{
        "voice":"ar-SY-LaithNeural",
        "rate":"-10%",
        "pitch":"-3Hz"
    },
    "princess":{
        "voice":"ar-SA-ZariyahNeural",
        "rate":"-6%"
    },
    "king":{
        "voice":"ar-EG-ShakirNeural",
        "rate":"-8%",
        "pitch":"-4Hz"
    },
    "guard":{
        "voice":"ar-IQ-BasselNeural",
        "rate":"-2%",
        "pitch":"-1Hz"
    },
    "narrator":{
        "voice":"ar-SA-HamedNeural",
        "rate":"-8%",
        "pitch":"-2Hz"
    }
}

WAVESPEED_BASE="https://api.wavespeed.ai/api/v3"

IMAGE_MODEL="wavespeed-ai/z-image/turbo"
VIDEO_MODEL="wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
LIPSYNC_MODEL="sync/react-1"
SFX_MODEL="wavespeed-ai/mmaudio-v2"
MUSIC_MODEL="wavespeed-ai/ace-step/prompt-to-audio"

app=Flask(__name__)
groq=Groq(api_key=GROQ_API_KEY)

logging_lock=threading.Lock()
processing_chats=set()

def log(x):
    with logging_lock:
        print(
            f"[ABOSARAJ] {time.strftime('%H:%M:%S')} {x}",
            flush=True
        )

def auth_headers():
    return {
        "Authorization":f"Bearer {WAVESPEED_API_KEY}",
        "Content-Type":"application/json"
    }

def get_headers():
    return {
        "Authorization":f"Bearer {WAVESPEED_API_KEY}"
    }

# =========================================================
# WAVESPEED
# =========================================================

def wavespeed_submit(model,payload):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed while TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:
        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    r=requests.post(
        f"{WAVESPEED_BASE}/{model}",
        headers=auth_headers(),
        json=payload,
        timeout=(10,60)
    )

    r.raise_for_status()
    b=r.json()

    if b.get("code")!=200:
        raise RuntimeError(
            b.get("message","WaveSpeed task failed")
        )

    tid=b.get("data",{}).get("id")

    if not tid:
        raise RuntimeError(
            f"WaveSpeed returned no task id: {b}"
        )

    log(f"WaveSpeed task created: {tid}")
    return tid

def wavespeed_wait(tid,timeout=900):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed polling while TEST_MODE=true"
        )

    started=time.time()

    while True:
        if time.time()-started>timeout:
            raise TimeoutError(
                f"WaveSpeed timeout: {tid}"
            )

        r=requests.get(
            f"{WAVESPEED_BASE}/predictions/{tid}/result",
            headers=get_headers(),
            timeout=30
        )

        r.raise_for_status()
        b=r.json()

        if b.get("code")!=200:
            raise RuntimeError(b)

        d=b["data"]
        status=str(
            d.get("status","")
        ).lower()

        log(f"Task {tid}: {status}")

        if status=="completed":
            out=d.get("outputs")

            if not out:
                raise RuntimeError(
                    f"No outputs: {b}"
                )

            first=out[0]

            if isinstance(first,dict):
                for k in (
                    "url",
                    "audio_url",
                    "video_url",
                    "download_url"
                ):
                    if first.get(k):
                        return first[k]

            return first

        if status in (
            "failed",
            "cancelled",
            "timeout",
            "deleted"
        ):
            raise RuntimeError(
                f"WaveSpeed failed: {b}"
            )

        time.sleep(2)

def upload_to_wavespeed(path):
    if TEST_MODE:
        raise RuntimeError(
            "BLOCKED: WaveSpeed upload while TEST_MODE=true"
        )

    if not WAVESPEED_API_KEY:
        raise RuntimeError(
            "WAVESPEED_API_KEY is missing."
        )

    path=Path(path)

    r=requests.post(
        f"{WAVESPEED_BASE}/media/uploads",
        headers=auth_headers(),
        json={
            "filename":path.name,
            "size":path.stat().st_size
        },
        timeout=30
    )

    r.raise_for_status()
    b=r.json()

    if b.get("code")!=200:
        raise RuntimeError(b)

    d=b["data"]

    with path.open("rb") as f:
        u=requests.put(
            d["upload"]["url"],
            headers=d["upload"]["headers"],
            data=f,
            timeout=300
        )

    u.raise_for_status()

    return d["download_url"]

def download_file(url,path):
    r=requests.get(
        url,
        timeout=180
    )

    r.raise_for_status()

    Path(path).write_bytes(
        r.content
    )

    return path

# =========================================================
# GROQ STORY
# =========================================================

def create_story(user_idea):

    system=f"""
أنت كاتب سيناريو عربي ومخرج ومدير تصوير
ومشرف استمرارية ومصمم صوت سينمائي.

اصنع Microdrama عربي:
Dark Fantasy + Romance + Mystery +
Suspense + Supernatural Drama.

العالم:

رجل غامض جذاب يمتلك قوة خارقة.
أميرة تقع في حبه.
ملك يعرف سراً خطيراً عنه.
ذئبة بيضاء صغيرة لا تتكلم.

الفكرة:

{user_idea}

اصنع بالضبط {SHOT_COUNT} لقطات.
كل لقطة مدتها {SHOT_DURATION} ثوانٍ.

الأسلوب البصري:

realistic cinematic Arabic fantasy drama,
photorealistic,
professional film lighting,
realistic skin,
realistic fabric,
realistic fur,
volumetric moonlight,
fog,
shallow depth of field,
anamorphic cinematic look,
rim light,
natural body movement,
natural head movement,
eye movement,
blinking,
mouth movement,
hands,
fingers,
breathing,
cloth movement,
hair movement,
realistic hands and eyes,
vertical 9:16,
high production value.

ممنوع:

anime,
cartoon,
illustration,
game art,
plastic skin,
text,
logo,
watermark.

قواعد الحوار:

كل لقطة تحتوي متحدثاً بشرياً واحداً فقط.

الحوار قصير جداً ومناسب لمدة 5 ثوانٍ.

الذئبة لا تتكلم.

الذئبة تستخدم فقط:
whimper,
growl,
howl,
breathing.

sound:

اكتب وصفاً تنفيذياً للصوت فقط.

يشمل:
environment,
Foley,
footsteps,
cloth,
hair,
metal,
wood,
wolf sounds,
wind,
leaves,
whoosh,
impact,
supernatural power,
riser,
stinger.

لا تضع موسيقى داخل sound.

music:

اكتب وصفاً للموسيقى السينمائية الآلية فقط.

ممنوع:
vocals,
lyrics,
spoken words.

يجب أن يكون هناك تصعيد درامي.

ابدأ بـ hook.

ثم escalation.

ثم reveal أو danger.

ثم cliffhanger.

لا تحل السر بالكامل.

أعد JSON فقط.
"""

    user_prompt=f"""
حوّل الفكرة التالية إلى {SHOT_COUNT} لقطات
مدة كل لقطة {SHOT_DURATION} ثوانٍ.

الفكرة:

{user_idea}

أعد JSON فقط بهذا الشكل:

{{
  "title":"...",
  "hook":"...",
  "visual_style":"...",
  "cast_reference_prompt":"...",
  "scenes":[
    {{
      "action":"...",
      "dialogue":[
        {{
          "speaker":"male_lead",
          "text":"...",
          "emotion":"..."
        }}
      ],
      "scene_image_prompt":"...",
      "video_prompt":"...",
      "camera":"...",
      "sound":"...",
      "music":"..."
    }}
  ]
}}

speaker يجب أن يكون واحداً من:

male_lead
princess
king
guard
narrator

يجب أن تحتوي scenes على {SHOT_COUNT} عناصر بالضبط.
"""

    last_error=None

    for attempt in range(2):

        try:

            r=groq.chat.completions.create(
                model=GROQ_MODEL,
                temperature=.65,
                response_format={
                    "type":"json_object"
                },
                messages=[
                    {
                        "role":"system",
                        "content":system
                    },
                    {
                        "role":"user",
                        "content":user_prompt
                    }
                ]
            )

            content=r.choices[0].message.content

            if not content:
                raise RuntimeError(
                    "Groq returned empty story."
                )

            content=content.strip()

            if content.startswith("```"):
                content=content.replace(
                    "```json",""
                ).replace(
                    "```",""
                ).strip()

            story=json.loads(content)

            scenes=story.get("scenes",[])

            if len(scenes)!=SHOT_COUNT:
                raise RuntimeError(
                    f"Wrong scene count: {len(scenes)}"
                )

            for i,s in enumerate(scenes):

                if not isinstance(s,dict):
                    raise RuntimeError(
                        f"Scene {i+1} invalid."
                    )

                ds=s.get("dialogue",[])

                if not isinstance(ds,list) or not ds:
                    s["dialogue"]=[{
                        "speaker":"narrator",
                        "text":"",
                        "emotion":"neutral"
                    }]
                    continue

                d=ds[0]

                if d.get("speaker") not in (
                    "male_lead",
                    "princess",
                    "king",
                    "guard",
                    "narrator"
                ):
                    d["speaker"]="narrator"

                d["text"]=str(
                    d.get("text","")
                ).strip()

                d["emotion"]=str(
                    d.get("emotion","neutral")
                )

            return story

        except Exception as e:

            last_error=e

            log(
                f"Groq attempt {attempt+1} failed: "
                f"{repr(e)}"
            )

            if attempt==0:
                time.sleep(1)

    raise RuntimeError(
        f"Groq story generation failed: {last_error}"
    )

# =========================================================
# FFMPEG HELPERS
# =========================================================

def run_cmd(cmd,timeout=300):

    log(
        "CMD: "+" ".join(
            map(str,cmd)
        )
    )

    r=subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout
    )

    if r.returncode:

        log(
            r.stderr[-5000:]
        )

        raise RuntimeError(
            f"Command failed: {r.returncode}"
        )

    return r

def ffprobe_duration(path):

    r=subprocess.run(
        [
            "ffprobe",
            "-v","error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        return float(
            r.stdout.strip()
        )
    except:
        return 0.0

def normalize_audio_to_wav(
    inp,
    out,
    duration=None
):

    cmd=[
        "ffmpeg",
        "-y",
        "-i",
        str(inp),
        "-ac","1",
        "-ar","24000",
        "-sample_fmt","s16"
    ]

    if duration:
        cmd+=[
            "-t",
            str(duration)
        ]

    cmd+=[str(out)]

    run_cmd(
        cmd,
        120
    )

    return out

def ass_escape(x):

    return (
        str(x)
        .replace("\\","\\\\")
        .replace("{","\\{")
        .replace("}","\\}")
    )

def ass_time(s):

    s=max(
        0,
        float(s)
    )

    h=int(
        s//3600
    )

    m=int(
        (s%3600)//60
    )

    sec=s%60
    w=int(sec)

    cs=int(
        round(
            (sec-w)*100
        )
    )

    if cs>=100:
        w+=1
        cs=0

    return (
        f"{h}:{m:02d}:{w:02d}.{cs:02d}"
    )

def create_ass(meta,out):

    lines=[
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,15,&H00FFFFFF,&H00FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,2,55,55,65,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    for x in meta:

        lines.append(
            f"Dialogue: 0,"
            f"{ass_time(x['start'])},"
            f"{ass_time(x['end'])},"
            f"Default,,0,0,0,,"
            f"{ass_escape(x['text'])}"
        )

    Path(out).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    return out

# =========================================================
# TTS
# =========================================================

async def _tts(
    text,
    voice,
    out,
    rate="-5%",
    pitch=None
):

    kw={
        "text":text,
        "voice":voice,
        "rate":rate
    }

    if pitch:
        kw["pitch"]=pitch

    await edge_tts.Communicate(
        **kw
    ).save(
        str(out)
    )

def create_voice_audio(
    text,
    speaker,
    out
):

    c=VOICE_CONFIG.get(
        speaker,
        VOICE_CONFIG["narrator"]
    )

    log(
        f"TTS: {speaker} | {text}"
    )

    asyncio.run(
        _tts(
            text,
            c["voice"],
            out,
            c.get("rate","-5%"),
            c.get("pitch")
        )
    )

    return out

def fit_audio(
    inp,
    out,
    duration
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(inp),
            "-af",
            f"apad,atrim=0:{duration},"
            "asetpts=N/SR/TB",
            "-t",
            str(duration),
            "-ar","48000",
            "-ac","2",
            str(out)
        ],
        120
    )

    return out

def silent(
    duration,
    out
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f","lavfi",
            "-i",
            "anullsrc="
            "channel_layout=stereo:"
            "sample_rate=48000",
            "-t",
            str(duration),
            "-ar","48000",
            "-ac","2",
            str(out)
        ],
        120
    )

    return out

# =========================================================
# DIALOGUE TRACK
# =========================================================

def build_dialogue_track(
    scenes,
    duration,
    workdir
):

    items=[]

    for i,scene in enumerate(scenes):

        ds=scene.get(
            "dialogue",
            []
        )

        if not ds:
            continue

        d=ds[0]

        text=str(
            d.get("text","")
        ).strip()

        speaker=d.get(
            "speaker",
            "narrator"
        )

        if not text:
            continue

        mp3=Path(workdir)/f"d{i}.mp3"
        wav=Path(workdir)/f"d{i}.wav"
        fit=Path(workdir)/f"d{i}_fit.wav"

        create_voice_audio(
            text,
            speaker,
            mp3
        )

        normalize_audio_to_wav(
            mp3,
            wav
        )

        fit_audio(
            wav,
            fit,
            SHOT_DURATION
        )

        items.append(
            (i,fit)
        )

    if not items:

        return silent(
            duration,
            Path(workdir)/"dialogue_track.wav"
        )

    cmd=[
        "ffmpeg",
        "-y"
    ]

    filters=[]
    labels=[]

    for n,(i,p) in enumerate(items):

        cmd+=[
            "-i",
            str(p)
        ]

        lab=f"a{n}"

        delay=i*SHOT_DURATION*1000

        filters.append(
            f"[{n}:a]"
            f"adelay={delay}|{delay},"
            "aresample=48000,"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo"
            f"[{lab}]"
        )

        labels.append(
            f"[{lab}]"
        )

    if len(labels)==1:

        filters.append(
            f"{labels[0]}"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[d]"
        )

    else:

        filters.append(
            "".join(labels)+
            f"amix="
            f"inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[d]"
        )

    out=Path(workdir)/"dialogue_track.wav"

    cmd+=[
        "-filter_complex",
        ";".join(filters),
        "-map","[d]",
        "-t",str(duration),
        "-ar","48000",
        "-ac","2",
        str(out)
    ]

    run_cmd(
        cmd,
        180
    )

    return out

# =========================================================
# TEST VIDEO
# =========================================================

def create_test_scene_video(
    i,
    out
):

    a=[
        "0x17101f",
        "0x10202a",
        "0x201710",
        "0x111c14"
    ][i%4]

    b=[
        "0x382345",
        "0x193d4a",
        "0x4b3018",
        "0x203c25"
    ][i%4]

    f=(
        f"color=c={a}:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"r={VIDEO_FPS}:"
        f"d={SHOT_DURATION},"
        "format=yuv420p,"
        "drawbox="
        "x='100+80*sin(t)':"
        "y='250+120*cos(t*0.7)':"
        "w=520:h=700:"
        f"color={b}@0.35:t=fill,"
        "drawbox="
        "x='220+100*cos(t*0.5)':"
        "y='500+70*sin(t)':"
        "w=280:h=280:"
        "color=white@0.07:t=fill"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f","lavfi",
            "-i",f,
            "-t",str(SHOT_DURATION),
            "-r",str(VIDEO_FPS),
            "-an",
            "-c:v","libx264",
            "-preset","veryfast",
            "-pix_fmt","yuv420p",
            str(out)
        ]
    )

    return out

# =========================================================
# IMAGE / VIDEO
# =========================================================

def generate_image(
    prompt,
    out
):

    if TEST_MODE:
        return None

    tid=wavespeed_submit(
        IMAGE_MODEL,
        {
            "prompt":prompt,
            "size":"720*1280"
        }
    )

    return download_file(
        wavespeed_wait(tid,600),
        out
    )

def generate_cast_reference(
    story,
    out
):

    p=story.get(
        "cast_reference_prompt"
    ) or """
Photorealistic cinematic Arabic fantasy cast reference.
Exactly four recurring characters:
handsome mysterious supernatural man,
beautiful Arabian princess,
small white female wolf pup,
powerful Arab king.

Consistent identity,
clothing and proportions.

Realistic skin,
fabric,
fur,
moonlight,
fog,
anamorphic film look,
high production value,
vertical 9:16.

No text,
labels,
logo,
watermark,
anime,
cartoon,
illustration,
game art.
"""

    return generate_image(
        p,
        out
    )

def generate_scene_video(
    image_path,
    prompt,
    out
):

    if TEST_MODE:
        return None

    image_url=upload_to_wavespeed(
        image_path
    )

    p=f"""
{prompt}

Natural cinematic motion:
subtle eyes,
blinking,
facial expression,
mouth,
breathing,
head,
shoulders,
hands,
fingers,
body weight,
walking when appropriate,
hair,
cloth,
fog,
animal fur.

Preserve exact identity,
face,
clothing,
anatomy,
environment.

No morphing,
extra fingers,
deformed hands,
duplicated characters,
text,
watermark,
logo.

Photorealistic cinematic 9:16.
"""

    tid=wavespeed_submit(
        VIDEO_MODEL,
        {
            "image":image_url,
            "prompt":p,
            "duration":SHOT_DURATION,
            "resolution":"480p"
        }
    )

    return download_file(
        wavespeed_wait(tid,900),
        out
    )

# =========================================================
# LIPSYNC
# =========================================================

def generate_lipsync_video(
    video,
    audio,
    out
):

    if (
        TEST_MODE
        or not LIPSYNC_ENABLED
    ):
        return video

    vu=upload_to_wavespeed(
        video
    )

    au=upload_to_wavespeed(
        audio
    )

    tid=wavespeed_submit(
        LIPSYNC_MODEL,
        {
            "video":vu,
            "audio":au,
            "model_mode":LIPSYNC_MODE,
            "emotion":DEFAULT_LIPSYNC_EMOTION
        }
    )

    return download_file(
        wavespeed_wait(tid,900),
        out
    )

# =========================================================
# SCENE SFX
# =========================================================

def generate_scene_sfx(
    video,
    sound,
    idx,
    workdir
):

    if (
        not SOUND_DESIGN_ENABLED
        or TEST_MODE
        or not video
    ):
        return None

    vu=upload_to_wavespeed(
        video
    )

    prompt=f"""
Create cinematic synchronized sound design
for this exact 5-second video.

Follow visible actions precisely.

Cue sheet:

{sound}

Include realistic:

environment,
Foley,
footsteps,
cloth,
hair,
wood,
metal,
animal sounds,
wolf breathing,
wolf whimper,
wolf growl,
wolf howl when visible,
wind,
leaves,
whooshes,
impacts,
supernatural energy,
low rumbles,
risers,
stingers,
spatial perspective.

Match exact timing and physical movement.

NO speech,
NO dialogue,
NO narration,
NO singing,
NO lyrics,
NO music,
NO melody,
NO voice.

Professional cinematic film sound.
Natural dynamic range.
No clipping.
"""

    payload={
        "video":vu,
        "prompt":prompt,
        "duration":SHOT_DURATION,
        "steps":MMAUDIO_STEPS,
        "guidance_scale":MMAUDIO_GUIDANCE,
        "negative_prompt":
            "speech, dialogue, narration, "
            "singing, lyrics, music, melody, "
            "voice, distortion, clipping, "
            "digital noise"
    }

    tid=wavespeed_submit(
        SFX_MODEL,
        payload
    )

    raw=Path(workdir)/f"sfx_{idx:02d}.wav"
    norm=Path(workdir)/f"sfx_{idx:02d}_norm.wav"

    download_file(
        wavespeed_wait(tid,900),
        raw
    )

    return normalize_audio_to_wav(
        raw,
        norm,
        SHOT_DURATION
    )

# =========================================================
# SFX TRACK
# =========================================================

def build_sfx_track(files,duration,workdir):

    valid=[]

    for i,p in enumerate(files):
        if p and Path(p).exists():
            valid.append((i,p))

    if not valid:
        return silent(
            duration,
            Path(workdir)/"sfx_track.wav"
        )

    cmd=["ffmpeg","-y"]
    filters=[]
    labels=[]

    for n,(i,p) in enumerate(valid):

        cmd += [
            "-i",
            str(p)
        ]

        lab=f"s{n}"
        delay=i*SHOT_DURATION*1000

        filters.append(
            f"[{n}:a]"
            f"adelay={delay}|{delay},"
            "aresample=48000,"
            f"volume={SFX_VOLUME},"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo"
            f"[{lab}]"
        )

        labels.append(
            f"[{lab}]"
        )

    if len(labels)==1:

        filters.append(
            f"{labels[0]}"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[s]"
        )

    else:

        filters.append(
            "".join(labels)+
            f"amix="
            f"inputs={len(labels)}:"
            "duration=longest:"
            "dropout_transition=0,"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB[s]"
        )

    out=Path(workdir)/"sfx_track.wav"

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map","[s]",
        "-t",str(duration),
        "-ar","48000",
        "-ac","2",
        str(out)
    ]

    run_cmd(
        cmd,
        180
    )

    return out


# =========================================================
# LOCAL AMBIENCE
# =========================================================

def create_local_ambience(
    duration,
    out
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f","lavfi",
            "-i",
            "anoisesrc="
            "color=brown:"
            "amplitude=0.015:"
            "sample_rate=48000",
            "-af",
            "highpass=f=35,"
            "lowpass=f=5000,"
            "volume=0.18,"
            f"atrim=0:{duration}",
            "-t",str(duration),
            "-ar","48000",
            "-ac","2",
            str(out)
        ],
        120
    )

    return out


# =========================================================
# MUSIC
# =========================================================

def build_music_prompt(
    story,
    scenes
):

    cues="\n".join(
        f"Scene {i+1}: {s.get('music','')}"
        for i,s in enumerate(scenes)
        if s.get("music")
    )

    return f"""
Instrumental cinematic score for Arabic
dark fantasy romance mystery.

Title:
{story.get('title','Dark Arabic Fantasy')}

Mood:
romantic mystery,
supernatural dread,
ancient secret,
night forest,
royal palace,
forbidden love,
danger,
emotional tension,
slow escalation,
unresolved cliffhanger.

Instrumentation:

oud-like plucked texture,
low cinematic strings,
deep cello,
soft frame drum,
subtle Arabic percussion,
atmospheric pads,
distant choir-like texture WITHOUT WORDS,
deep sub bass,
sparse piano,
metallic supernatural textures.

Begin intimate and mysterious.

Gradually increase tension.

Use darker harmonic movement.

Build toward supernatural revelation.

End with a strong unresolved cliffhanger.

Absolutely instrumental.

No vocals.
No lyrics.
No spoken words.

Scene cues:

{cues}
"""


def generate_music(
    story,
    scenes,
    duration,
    workdir
):

    if (
        not MUSIC_ENABLED
        or TEST_MODE
    ):
        return None

    payload={
        "prompt":build_music_prompt(
            story,
            scenes
        ),
        "duration":int(
            max(
                5,
                min(240,duration)
            )
        ),
        "instrumental":True,
        "seed":24117
    }

    tid=wavespeed_submit(
        MUSIC_MODEL,
        payload
    )

    raw=Path(workdir)/"music_raw.wav"
    out=Path(workdir)/"music.wav"

    download_file(
        wavespeed_wait(tid,900),
        raw
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",str(raw),
            "-af",
            f"aresample=48000,"
            f"volume={MUSIC_VOLUME},"
            f"atrim=0:{duration},"
            "asetpts=N/SR/TB",
            "-t",str(duration),
            "-ar","48000",
            "-ac","2",
            str(out)
        ],
        180
    )

    return out


# =========================================================
# FINAL AUDIO MIX
# =========================================================

def mix_final_audio(
    dialogue,
    sfx,
    music,
    ambience,
    duration,
    out
):

    paths=[
        dialogue,
        sfx,
        music,
        ambience
    ]

    cmd=["ffmpeg","-y"]

    for p in paths:

        if p and Path(p).exists():

            cmd += [
                "-i",
                str(p)
            ]

        else:

            cmd += [
                "-f","lavfi",
                "-t",str(duration),
                "-i",
                "anullsrc="
                "channel_layout=stereo:"
                "sample_rate=48000"
            ]

    filters=[
        f"[0:a]"
        f"aresample=48000,"
        f"volume={VOICE_VOLUME},"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[v]",

        f"[1:a]"
        "aresample=48000,"
        "atrim=0:"
        f"{duration},"
        "asetpts=N/SR/TB[s]",

        f"[2:a]"
        "aresample=48000,"
        "atrim=0:"
        f"{duration},"
        "asetpts=N/SR/TB[m]",

        f"[3:a]"
        f"aresample=48000,"
        f"volume={AMBIENCE_VOLUME},"
        f"atrim=0:{duration},"
        "asetpts=N/SR/TB[a]",

        "[v][s][m][a]"
        "amix=inputs=4:"
        "duration=longest:"
        "dropout_transition=0,"
        "alimiter="
        "limit=0.95:"
        "attack=5:"
        "release=50,"
        "aresample=48000"
        "[aout]"
    ]

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map","[aout]",
        "-t",str(duration),
        "-ar","48000",
        "-ac","2",
        "-c:a","aac",
        "-b:a","192k",
        str(out)
    ]

    run_cmd(
        cmd,
        240
    )

    return out


# =========================================================
# VIDEO NORMALIZATION
# =========================================================

def normalize_scene_video(
    inp,
    out
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",str(inp),
            "-vf",
            f"scale={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:"
            f"{VIDEO_HEIGHT}:"
            "(ow-iw)/2:"
            "(oh-ih)/2,"
            "format=yuv420p",
            "-r",str(VIDEO_FPS),
            "-an",
            "-c:v","libx264",
            "-preset","veryfast",
            "-crf","18",
            "-pix_fmt","yuv420p",
            "-t",str(SHOT_DURATION),
            str(out)
        ],
        180
    )

    return out


# =========================================================
# CONCAT VIDEO
# =========================================================

def concat_videos(
    files,
    out
):

    list_file=(
        Path(out).parent/
        "video_concat.txt"
    )

    lines=[]

    for video in files:

        p=str(
            Path(video)
            .resolve()
        ).replace(
            "'",
            "'\\''"
        )

        lines.append(
            "file '"+p+"'"
        )

    list_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f","concat",
            "-safe","0",
            "-i",str(list_file),
            "-c","copy",
            str(out)
        ],
        180
    )

    return out


# =========================================================
# ADD AUDIO
# =========================================================

def add_audio_to_video(
    video,
    audio,
    duration,
    out
):

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",str(video),
            "-i",str(audio),
            "-map","0:v:0",
            "-map","1:a:0",
            "-c:v","copy",
            "-c:a","aac",
            "-b:a","192k",
            "-ar","48000",
            "-ac","2",
            "-t",str(duration),
            "-movflags","+faststart",
            str(out)
        ],
        240
    )

    return out


# =========================================================
# SUBTITLE META
# =========================================================

def build_scene_meta(
    scenes
):

    out=[]

    for i,s in enumerate(scenes):

        ds=s.get(
            "dialogue",
            []
        )

        if (
            ds
            and str(
                ds[0].get(
                    "text",
                    ""
                )
            ).strip()
        ):

            out.append(
                {
                    "start":
                        i*SHOT_DURATION,
                    "end":
                        (i+1)*SHOT_DURATION,
                    "text":
                        str(
                            ds[0]["text"]
                        ).strip()
                }
            )

    return out


# =========================================================
# BURN SUBTITLES
# =========================================================

def burn_subtitles(
    video,
    ass,
    out
):

    esc=str(
        Path(ass)
    ).replace(
        "\\",
        "\\\\"
    ).replace(
        ":",
        "\\:"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",str(video),
            "-vf",
            f"subtitles='{esc}'",
            "-c:v","libx264",
            "-preset","veryfast",
            "-crf","19",
            "-pix_fmt","yuv420p",
            "-c:a","copy",
            "-t",
            str(
                ffprobe_duration(video)
            ),
            str(out)
        ],
        240
    )

    return out


# =========================================================
# PRODUCE EPISODE
# =========================================================

def produce_episode(
    story,
    workdir
):

    workdir=Path(workdir)
    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    scenes=story.get(
        "scenes",
        []
    )

    if PRODUCTION_SCENE_LIMIT>0:
        scenes=scenes[
            :PRODUCTION_SCENE_LIMIT
        ]

    if not scenes:
        raise RuntimeError(
            "No scenes available."
        )

    count=len(scenes)
    duration=count*SHOT_DURATION

    log(
        f"Producing {count} scenes "
        f"({duration}s)"
    )

    # -----------------------------------------------------
    # CAST
    # -----------------------------------------------------

    if not TEST_MODE:

        generate_cast_reference(
            story,
            workdir/"cast_reference.png"
        )

    # -----------------------------------------------------
    # DIALOGUE AUDIO
    # -----------------------------------------------------

    dialogue_scene_audio=[]

    for i,scene in enumerate(scenes):

        ds=scene.get(
            "dialogue",
            []
        )

        if ds:

            d=ds[0]

            text=str(
                d.get(
                    "text",
                    ""
                )
            ).strip()

            speaker=d.get(
                "speaker",
                "narrator"
            )

        else:

            text=""
            speaker="narrator"

        out=(
            workdir/
            f"scene_{i:02d}_voice_fit.wav"
        )

        if text:

            mp3=(
                workdir/
                f"scene_{i:02d}_voice.mp3"
            )

            wav=(
                workdir/
                f"scene_{i:02d}_voice.wav"
            )

            create_voice_audio(
                text,
                speaker,
                mp3
            )

            normalize_audio_to_wav(
                mp3,
                wav
            )

            fit_audio(
                wav,
                out,
                SHOT_DURATION
            )

        else:

            silent(
                SHOT_DURATION,
                out
            )

        dialogue_scene_audio.append(
            (
                speaker,
                out
            )
        )

    # -----------------------------------------------------
    # SCENES
    # -----------------------------------------------------

    scene_videos=[]
    sfx_files=[]

    for i,scene in enumerate(scenes):

        log(
            f"========== "
            f"SCENE {i+1}/{count} "
            f"=========="
        )

        base=(
            workdir/
            f"scene_{i:02d}_base.mp4"
        )

        final_scene=(
            workdir/
            f"scene_{i:02d}_final.mp4"
        )

        norm=(
            workdir/
            f"scene_{i:02d}_normalized.mp4"
        )

        # -------------------------------------------------
        # VIDEO
        # -------------------------------------------------

        if TEST_MODE:

            create_test_scene_video(
                i,
                base
            )

        else:

            image=(
                workdir/
                f"scene_{i:02d}.png"
            )

            generate_image(
                scene.get(
                    "scene_image_prompt",
                    ""
                ),
                image
            )

            generate_scene_video(
                image,
                scene.get(
                    "video_prompt",
                    ""
                ),
                base
            )

        # -------------------------------------------------
        # LIPSYNC
        # -------------------------------------------------

        speaker,audio=(
            dialogue_scene_audio[i]
        )

        if (
            LIPSYNC_ENABLED
            and not TEST_MODE
            and i<MAX_LIPSYNC_SCENES
            and speaker in {
                "male_lead",
                "princess",
                "king",
                "guard",
                "narrator"
            }
        ):

            generate_lipsync_video(
                base,
                audio,
                final_scene
            )

        else:

            shutil.copyfile(
                base,
                final_scene
            )

        # -------------------------------------------------
        # NORMALIZE
        # -------------------------------------------------

        normalize_scene_video(
            final_scene,
            norm
        )

        scene_videos.append(
            norm
        )

        # -------------------------------------------------
        # SFX
        # -------------------------------------------------

        if (
            SOUND_DESIGN_ENABLED
            and not TEST_MODE
        ):

            sfx=generate_scene_sfx(
                norm,
                scene.get(
                    "sound",
                    ""
                ),
                i,
                workdir
            )

        else:

            sfx=None

        sfx_files.append(
            sfx
        )

    # -----------------------------------------------------
    # CONCAT
    # -----------------------------------------------------

    concat=(
        workdir/
        "episode_video.mp4"
    )

    concat_videos(
        scene_videos,
        concat
    )

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    dialogue=build_dialogue_track(
        scenes,
        duration,
        workdir
    )

    sfx=build_sfx_track(
        sfx_files,
        duration,
        workdir
    )

    if (
        MUSIC_ENABLED
        and not TEST_MODE
    ):

        music=generate_music(
            story,
            scenes,
            duration,
            workdir
        )

    else:

        music=None

    ambience=(
        workdir/
        "ambience.wav"
    )

    create_local_ambience(
        duration,
        ambience
    )

    # -----------------------------------------------------
    # MIX
    # -----------------------------------------------------

    final_audio=(
        workdir/
        "final_audio.m4a"
    )

    mix_final_audio(
        dialogue,
        sfx,
        music,
        ambience,
        duration,
        final_audio
    )

    # -----------------------------------------------------
    # VIDEO + AUDIO
    # -----------------------------------------------------

    video_audio=(
        workdir/
        "video_audio.mp4"
    )

    add_audio_to_video(
        concat,
        final_audio,
        duration,
        video_audio
    )

    # -----------------------------------------------------
    # SUBTITLES
    # -----------------------------------------------------

    ass=(
        workdir/
        "subtitles.ass"
    )

    create_ass(
        build_scene_meta(
            scenes
        ),
        ass
    )

    final=(
        workdir/
        "ABOSARAJ_FINAL.mp4"
    )

    burn_subtitles(
        video_audio,
        ass,
        final
    )

    fd=ffprobe_duration(
        final
    )

    if (
        fd<=0
        or not final.exists()
    ):
        raise RuntimeError(
            "Final video invalid or missing."
        )

    log(
        f"FINAL VIDEO READY: "
        f"{final} ({fd:.2f}s)"
    )

    return {
        "video":final,
        "duration":fd,
        "scene_count":count
    }


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    payload=None,
    files=None
):

    r=requests.post(
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}",
        data=payload or {},
        files=files,
        timeout=180
    )

    r.raise_for_status()

    b=r.json()

    if not b.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {b}"
        )

    return b

def send_message(
    chat_id,
    text
):

    return telegram_api(
        "sendMessage",
        {
            "chat_id":chat_id,
            "text":text
        }
    )

def send_video(
    chat_id,
    path,
    caption=""
):

    with open(
        path,
        "rb"
    ) as f:

        return telegram_api(
            "sendVideo",
            {
                "chat_id":chat_id,
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


# =========================================================
# TEST STORY
# =========================================================

def default_test_story():

    return """
أميرة تهرب ليلًا من القصر بعد أن يقرر والدها
الملك تزويجها لرجل لا تحبه.

في الغابة تجد ذئبة بيضاء صغيرة مصابة
وتحاول مساعدتها.

يظهر أمامها رجل غامض وجذاب كانت قد رأته
من قبل، ويخبرها أن عليها العودة للقصر
لأن رجال الملك يبحثون عنها.

ترفض العودة وتكتشف أن الرجل يمتلك قوة
خارقة يحاول إخفاءها.

يصل أحد حراس الملك ويأمر الأميرة بالعودة،
لكن الرجل يقف أمامها ويحميها بطريقة تكشف
جزءًا من قوته.

قبل نهاية المشهد ترفع الذئبة الصغيرة رأسها
وتنظر للرجل وكأنها تعرفه منذ زمن،
ثم تطلق عواءً غريبًا يجعل وجه الرجل
يتغير فجأة.

من أين تعرف الذئبة هذا الرجل؟
"""


# =========================================================
# STATUS
# =========================================================

def production_status_text():

    n=(
        SHOT_COUNT
        if PRODUCTION_SCENE_LIMIT==0
        else PRODUCTION_SCENE_LIMIT
    )

    d=n*SHOT_DURATION

    return (
        f"Scenes: {n}\n"
        f"Duration: {d}s\n"
        f"Cinematic SFX: "
        f"{'ON' if SOUND_DESIGN_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
        f"Cinematic Score: "
        f"{'ON' if MUSIC_ENABLED and not TEST_MODE else 'TEST-SILENT'}\n"
        f"Lip-sync: "
        f"{'ON' if LIPSYNC_ENABLED and not TEST_MODE else 'OFF'}"
    )


# =========================================================
# PROCESS
# =========================================================

def process_story_for_chat(
    chat_id,
    idea
):

    if chat_id in processing_chats:

        send_message(
            chat_id,
            "⏳ عندي حلقة قيد المعالجة بالفعل."
        )

        return

    processing_chats.add(
        chat_id
    )

    workdir=Path(
        tempfile.mkdtemp(
            prefix="abosaraj_"
        )
    )

    try:

        send_message(
            chat_id,
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

        story=create_story(
            idea
        )

        (
            workdir/
            "story.json"
        ).write_text(
            json.dumps(
                story,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        send_message(
            chat_id,
            "🎬 السيناريو جاهز.\n"
            f"📖 {story.get('title','Untitled')}\n\n"
            "هلا بنبني الحلقة."
        )

        result=produce_episode(
            story,
            workdir
        )

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

        if (
            LIPSYNC_ENABLED
            and not TEST_MODE
        ):
            caption+="\n👄 Lip Sync: ON"

        send_video(
            chat_id,
            result["video"],
            caption
        )

        send_message(
            chat_id,
            "✅ الحلقة وصلت."
        )

    except Exception as e:

        log(
            "PRODUCTION ERROR: "
            +repr(e)
        )

        try:

            send_message(
                chat_id,
                "❌ صار خطأ:\n"
                f"{type(e).__name__}: {e}\n\n"
                "ابعتلي آخر Render Logs."
            )

        except Exception as x:

            log(
                "Telegram error: "
                +repr(x)
            )

    finally:

        processing_chats.discard(
            chat_id
        )

        log(
            f"Workdir kept: {workdir}"
        )


def start_processing(
    chat_id,
    idea
):

    threading.Thread(
        target=process_story_for_chat,
        args=(chat_id,idea),
        daemon=True
    ).start()


# =========================================================
# FLASK
# =========================================================

@app.get("/")
def home():

    return {
        "status":"ok",
        "service":"Abosaraj",
        "test_mode":TEST_MODE,
        "sound_design":
            SOUND_DESIGN_ENABLED,
        "music":
            MUSIC_ENABLED,
        "lipsync":
            LIPSYNC_ENABLED
    }


@app.get("/health")
def health():

    return {
        "status":"healthy",
        "service":"abosaraj",
        "test_mode":TEST_MODE,
        "wavespeed_configured":
            bool(WAVESPEED_API_KEY),
        "groq_configured":
            bool(GROQ_API_KEY),
        "sound_design_enabled":
            SOUND_DESIGN_ENABLED,
        "music_enabled":
            MUSIC_ENABLED,
        "lipsync_enabled":
            LIPSYNC_ENABLED,
        "lipsync_mode":
            LIPSYNC_MODE,
        "max_lipsync_scenes":
            MAX_LIPSYNC_SCENES,
        "production_scene_limit":
            PRODUCTION_SCENE_LIMIT
    }


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post("/webhook")
@app.post("/telegram/webhook")
def webhook():

    update=request.get_json(
        silent=True
    ) or {}

    message=(
        update.get("message")
        or {}
    )

    chat=(
        message.get("chat")
        or {}
    )

    chat_id=chat.get(
        "id"
    )

    text=message.get(
        "text"
    )

    log(
        f"Telegram update received: "
        f"chat={chat_id}, "
        f"text={text!r}"
    )

    if not chat_id or not text:

        return {
            "ok":True
        }

    text=text.strip()

    if text in (
        "/start",
        "/help"
    ):

        send_message(
            chat_id,
            "🎬 أهلاً في Abosaraj.\n\n"
            "ابعثلي فكرة القصة وأنا أحولها "
            "إلى Microdrama سينمائي.\n\n"
            "أو اكتب /test لتشغيل قصة الاختبار."
        )

        return {
            "ok":True
        }

    if text=="/test":

        send_message(
            chat_id,
            "🧪 اختبار Abosaraj بدأ."
        )

        start_processing(
            chat_id,
            default_test_story()
        )

        return {
            "ok":True
        }

    if text=="/status":

        send_message(
            chat_id,
            "🤖 Abosaraj Status\n\n"
            +production_status_text()
            +f"\n\nWan: "
            f"{'READY' if WAVESPEED_API_KEY else 'NO KEY'}"
            +f"\nGroq: "
            f"{'READY' if GROQ_API_KEY else 'NO KEY'}"
        )

        return {
            "ok":True
        }

    if len(text)<10:

        send_message(
            chat_id,
            "اكتب فكرة قصة أطول شوي."
        )

        return {
            "ok":True
        }

    send_message(
        chat_id,
        "🎬 وصلت الفكرة. "
        "ببدأ تحويلها إلى سيناريو سينمائي..."
    )

    start_processing(
        chat_id,
        text
    )

    return {
        "ok":True
    }


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    if TEST_MODE:

        log(
            "TEST_MODE=true — "
            "webhook setup skipped."
        )

        return

    if not RENDER_EXTERNAL_URL:

        log(
            "RENDER_EXTERNAL_URL "
            "not configured."
        )

        return

    try:

        r=requests.post(
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/setWebhook",
            data={
                "url":
                    f"{RENDER_EXTERNAL_URL}/webhook"
            },
            timeout=30
        )

        log(
            "Webhook response: "
            +r.text
        )

    except Exception as e:

        log(
            "Webhook setup failed: "
            +repr(e)
        )


# =========================================================
# STARTUP
# =========================================================

def print_startup():

    log(
        "========================================"
    )

    log(
        "ABOSARAJ AI VIDEO BOT"
    )

    log(
        "CODE_VERSION=2026-10-09-FINAL-CHECK-01"
    )

    log(
        "========================================"
    )

    values={
        "TEST_MODE":
            TEST_MODE,
        "SOUND_DESIGN":
            SOUND_DESIGN_ENABLED,
        "MUSIC":
            MUSIC_ENABLED,
        "LIPSYNC":
            LIPSYNC_ENABLED,
        "LIPSYNC_MODE":
            LIPSYNC_MODE,
        "MAX_LIPSYNC_SCENES":
            MAX_LIPSYNC_SCENES,
        "PRODUCTION_SCENE_LIMIT":
            PRODUCTION_SCENE_LIMIT,
        "SHOT_COUNT":
            SHOT_COUNT,
        "SHOT_DURATION":
            SHOT_DURATION,
        "TOTAL_DURATION":
            TOTAL_DURATION,
        "MMAUDIO_STEPS":
            MMAUDIO_STEPS,
        "MMAUDIO_GUIDANCE":
            MMAUDIO_GUIDANCE
    }

    for k,v in values.items():

        log(
            f"{k}={v}"
        )

    log(
        "AUDIO_PIPELINE=WAV_NO_MP3_CONCAT"
    )

    log(
        "GROQ_MODE=json_object"
    )

    log(
        "========================================"
    )


# =========================================================
# MAIN
# =========================================================

if __name__=="__main__":

    print_startup()

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
