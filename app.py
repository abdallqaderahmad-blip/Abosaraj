import os, sys, re, time, uuid, asyncio, logging, threading, subprocess, tempfile, json, random
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================================================
# ZIL CONFIG — keeps the existing Groq + Pixabay + Edge-TTS stack
# =========================================================
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
PIXABAY_API_KEY = (os.getenv('PIXABAY_API_KEY', '').strip() or os.getenv('PIXABAY_KEY', '').strip())
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '').strip()
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '').strip()
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b').strip()
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
PORT = int(os.getenv('PORT', '10000'))
PIXABAY_API = 'https://pixabay.com/api/videos/'

SCENE_COUNT, SCENE_DURATION = 18, 5
VIDEO_DURATION = SCENE_COUNT * SCENE_DURATION
VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS = 720, 1280, 24
MAX_ACTIVE_JOBS = 1
MAX_STORY_LENGTH = 2500
MAX_VIDEO_SIZE = 49 * 1024 * 1024
TTS_VOICE = os.getenv('TTS_VOICE', 'ar-SA-HamedNeural')
TTS_RATE = os.getenv('TTS_RATE', '-10%')
# Groq TPM protection: smaller output and a bounded retry wait.
GROQ_MAX_TOKENS = int(os.getenv('GROQ_MAX_TOKENS', '6000'))
GROQ_RETRIES = int(os.getenv('GROQ_RETRIES', '4'))
GROQ_MAX_WAIT = int(os.getenv('GROQ_MAX_WAIT', '45'))

BASE_DIR = Path(tempfile.gettempdir()) / 'zil_video_jobs'
BASE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
log = logging.getLogger('ZIL')
app = Flask(__name__)
JOBS, JOBS_LOCK = {}, threading.Lock()
ACTIVE_JOBS = 0
HTTP = requests.Session()

EFFECTS = {'mystery','castle','steps','romance','tension','storm','beast','fight','impact','magic','cliffhanger'}
FALLBACK_SCENES = [
('cinematic mysterious kingdom mountains castle','في مملكة بعيدة، كان سر قديم يقترب من الظهور.','mystery'),
('dark medieval castle exterior cinematic','خلف أسوار القصر، كان الجميع يخشى ما لا يعرفه.','castle'),
('mysterious man walking cloak cinematic silhouette','ثم وصل رجل غامض، يخفي قوة لا يريد لأحد رؤيتها.','steps'),
('medieval palace princess royal hall cinematic','رأت الأميرة فيه شيئًا مختلفًا عن كل من عرفتهم.','romance'),
('medieval king throne room serious king','لكن الملك رفض اقترابه، وكأن ماضيه يحمل خطرًا.','castle'),
('princess looking toward mysterious man dramatic','لم تتراجع الأميرة، بينما ازدادت الشكوك حول الغريب.','tension'),
('dark forest storm clouds ominous cinematic','وفجأة، اهتزت الأرض وغطّى الهدير أطراف المملكة.','storm'),
('large tiger roaring close up wildlife','ظهر نمر هائل، وزمجر حتى ارتجفت بوابات القصر.','beast'),
('tiger running charging wildlife dramatic','اندفع الوحش نحو القصر، ولم يعد أمام الحراس وقت.','beast'),
('medieval guards running castle dramatic','تراجع الحراس، ووقف الملك عاجزًا أمام الخطر.','steps'),
('mysterious warrior facing giant beast cinematic','عندها تقدّم الرجل بهدوء، وكأنه كان ينتظر هذه اللحظة.','tension'),
('tiger attack action wildlife dust dramatic','انقضّ النمر، فاشتعلت المواجهة وسط الغبار والصراخ.','fight'),
('fantasy warrior fighting giant beast cinematic','تفادى الضربة الأولى، ثم ردّ بقوة لم يتوقعها أحد.','fight'),
('epic action impact dust ground cinematic','دوّى الاصطدام، وتراجعت خطوات الوحش لأول مرة.','impact'),
('blue magical energy lightning fantasy warrior','بدأت طاقة غريبة تتوهّج حوله، وانكشف جزء من سره.','magic'),
('epic fantasy battle energy burst smoke','تجمّد الجميع حين أدركوا أن ضعفه كان مجرد تمويه.','magic'),
('giant tiger defeated lying ground cinematic','سقط الوحش، لكن الرجل أخفى قوته قبل أن يراه الملك.','impact'),
('dark castle night mysterious silhouette cliffhanger','وفي تلك اللحظة، ظهر أثر جديد… سرّ أخطر ينتظرهم.','cliffhanger')]

# ---------------- Utilities ----------------
def command_exists(name):
    try: return subprocess.run(['which', name], capture_output=True, timeout=5).returncode == 0
    except Exception: return False

def run_command(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {(p.stderr or p.stdout or 'unknown')[-2200:]}")
    return p

def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values); JOBS[job_id]['updated_at'] = time.time()

def get_active_jobs():
    with JOBS_LOCK: return ACTIVE_JOBS

def notify_admin(message):
    if not ADMIN_CHAT_ID or not BOT_TOKEN: return
    try: HTTP.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', json={'chat_id':ADMIN_CHAT_ID,'text':message[:3500]}, timeout=12).raise_for_status()
    except Exception: log.exception('Admin notification failed')

def report_error(job_id, error):
    log.exception('Job %s failed: %s', job_id, error)
    update_job(job_id, status='failed', stage='failed', error=str(error)[:2000])
    threading.Thread(target=notify_admin, args=(f'ZIL VIDEO ERROR\nJob: {job_id}\nError: {str(error)[:2500]}',), daemon=True).start()

def probe_duration(path):
    p = run_command(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)], 25)
    try: return float(p.stdout.strip())
    except Exception: return None

def word_count(s): return len(re.findall(r'\S+', s or ''))

def extract_json(s):
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', (s or '').strip(), flags=re.I)
    try: return json.loads(s)
    except json.JSONDecodeError:
        a,b=s.find('{'),s.rfind('}')
        if a >= 0 and b > a: return json.loads(s[a:b+1])
        raise

# ---------------- AI story and scene plan ----------------
def create_story_package(idea):
    if not GROQ_API_KEY: raise RuntimeError('GROQ_API_KEY is missing in Render Environment.')
    # Compact prompt reduces TPM usage while retaining 18 scene segments.
    system = '''أنت كاتب سيناريو فانتازيا. أخرج JSON صالحًا فقط بلا Markdown.
أنشئ قصة عربية مترابطة من فكرة المستخدم، فيها بداية وتصاعد وخطر وكشف وخطاف للنهاية.
أنشئ 18 مشهدًا بالضبط. كل مشهد يحتوي narration قصيرة مناسبة لنحو 5 ثوانٍ، visual_prompt بالإنجليزية لوصف ما يظهر فعلًا، search_query بالإنجليزية 4-9 كلمات للبحث عن فيديو stock، وeffect واحدة من: mystery, castle, steps, romance, tension, storm, beast, fight, impact, magic, cliffhanger.
اجعل ترتيب narration للمشاهد متتابعًا ومطابقًا للأحداث، مجموعها 155-195 كلمة تقريبًا. لا تضف كتابة داخل الصورة. لا تضف مفاتيح أخرى.
JSON: {"title":"...","story":"...","scenes":[{"narration":"...","visual_prompt":"...","search_query":"...","effect":"mystery"}]}'''
    last_error = None
    for attempt in range(GROQ_RETRIES):
        try:
            r = HTTP.post(GROQ_API_URL, headers={'Authorization':f'Bearer {GROQ_API_KEY}','Content-Type':'application/json'}, json={
                'model':GROQ_MODEL,'messages':[{'role':'system','content':system},{'role':'user','content':'فكرة القصة: '+str(idea)[:MAX_STORY_LENGTH]+'\nأخرج JSON صالحًا فيه 18 مشهدًا بالضبط. اجعل الجمل قصيرة ومتتابعة.'}],
                'temperature':0.45,'max_tokens':GROQ_MAX_TOKENS
            }, timeout=120)
            if r.status_code == 429:
                body = {}
                try: body = r.json().get('error', {})
                except Exception: pass
                msg = str(body.get('message',''))
                # Honor provider retry hint where available; otherwise exponential backoff.
                wait = None
                m = re.search(r'try again in\s+([0-9.]+)s', msg, re.I)
                if m: wait = float(m.group(1)) + 1.0
                retry_after = r.headers.get('retry-after')
                if retry_after:
                    try: wait = max(wait or 0, float(retry_after))
                    except ValueError: pass
                wait = min(GROQ_MAX_WAIT, max(wait or (6 * (2 ** attempt)), 2))
                if attempt == GROQ_RETRIES - 1: raise RuntimeError(f'Groq rate limit persisted after retries. Wait {wait:.1f}s and try again. Details: {msg[:700]}')
                log.warning('Groq 429 TPM/rate limit. Waiting %.1fs before retry %s/%s', wait, attempt+2, GROQ_RETRIES)
                time.sleep(wait + random.uniform(0, 0.8)); continue
            if r.status_code >= 400:
                detail = r.text[:900]
                if r.status_code in (401,403,404): raise RuntimeError(f'Groq API HTTP {r.status_code}: {detail}')
                raise RuntimeError(f'Groq API HTTP {r.status_code}: {detail}')
            choices = r.json().get('choices') or []
            if not choices: raise ValueError('Groq returned no choices')
            content = choices[0].get('message',{}).get('content','')
            if isinstance(content,list): content=''.join(x.get('text','') for x in content if isinstance(x,dict))
            data = extract_json(str(content))
            scenes = data.get('scenes') if isinstance(data,dict) else None
            if not isinstance(scenes,list) or len(scenes) != SCENE_COUNT: raise ValueError(f'Expected 18 scenes; got {len(scenes) if isinstance(scenes,list) else "invalid"}')
            clean=[]
            for i,s in enumerate(scenes):
                if not isinstance(s,dict): raise ValueError(f'Scene {i+1} invalid')
                line=re.sub(r'\s+',' ',str(s.get('narration',''))).strip()
                visual=re.sub(r'\s+',' ',str(s.get('visual_prompt',''))).strip()
                query=re.sub(r'[^a-zA-Z0-9 ,\'-]','',str(s.get('search_query',''))).strip()
                effect=str(s.get('effect','mystery')).lower().strip()
                if not line: raise ValueError(f'Scene {i+1} narration empty')
                if len(query.split()) < 3: query=' '.join(re.findall(r'[A-Za-z0-9]+',visual)[:9])
                if len(query.split()) < 3: query='cinematic fantasy dramatic scene'
                if effect not in EFFECTS: effect='mystery'
                clean.append({'narration':line,'visual_prompt':visual[:500],'search_query':query[:150],'effect':effect})
            count=word_count(' '.join(x['narration'] for x in clean))
            if not 100 <= count <= 230: raise ValueError(f'Scene narration word count outside safe range: {count}')
            return {'title':str(data.get('title') or 'حكاية ظل')[:100], 'story':str(data.get('story') or idea)[:5000], 'narration':' '.join(x['narration'] for x in clean), 'scenes':clean}
        except Exception as e:
            last_error=e
            log.warning('Story attempt %s/%s failed: %s',attempt+1,GROQ_RETRIES,e)
            if isinstance(e,RuntimeError) and any(x in str(e) for x in ('HTTP 401','HTTP 403','HTTP 404')): break
            # For non-429 transient/model-format errors, short bounded delay before retry.
            if attempt < GROQ_RETRIES-1 and '429' not in str(e): time.sleep(min(8, 2*(attempt+1)))
    raise RuntimeError(f'Could not create a valid AI story/narration package after retries: {last_error}')

# ---------------- Pixabay selection ----------------
def search_pixabay_video(query):
    if not PIXABAY_API_KEY: raise RuntimeError('PIXABAY_API_KEY is missing in Render Environment.')
    r=HTTP.get(PIXABAY_API,params={'key':PIXABAY_API_KEY,'q':query,'per_page':10,'safesearch':'true','video_type':'film'},timeout=30)
    if r.status_code >= 400: raise RuntimeError(f'Pixabay API HTTP {r.status_code}: {r.text[:500]}')
    hits=r.json().get('hits',[])
    if not hits: raise RuntimeError(f'No Pixabay results for query: {query}')
    # Prefer larger clips; Pixabay ranking already follows query relevance.
    for hit in hits:
        vids=hit.get('videos') or {}
        for quality in ('large','medium','small','tiny'):
            u=(vids.get(quality) or {}).get('url','')
            if u and urlparse(u).scheme=='https': return u
    raise RuntimeError(f'No downloadable video found for: {query}')

def download_video(url,dest):
    total=0
    with HTTP.get(url,stream=True,timeout=(20,90)) as r:
        r.raise_for_status()
        with open(dest,'wb') as f:
            for chunk in r.iter_content(256*1024):
                if not chunk: continue
                total += len(chunk)
                if total > 150*1024*1024: raise RuntimeError('Downloaded video exceeds 150 MB')
                f.write(chunk)
    if total < 10000: raise RuntimeError('Downloaded video is too small')
    return dest

# ---------------- Video / captions ----------------
def normalize_scene(src,dest):
    vf=f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps={VIDEO_FPS},tpad=stop_mode=clone:stop_duration={SCENE_DURATION},format=yuv420p"
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(src),'-vf',vf,'-t',str(SCENE_DURATION),'-an','-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p','-r',str(VIDEO_FPS),'-movflags','+faststart',str(dest)],180)
    if not dest.exists() or dest.stat().st_size < 1000: raise RuntimeError('FFmpeg produced an empty scene')
    return dest

def add_arabic_caption(src,caption,dest,workdir,index):
    try:
        from PIL import Image,ImageDraw,ImageFont
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        log.warning('Arabic subtitle dependencies missing; continuing without burned subtitles')
        return src
    try:
        font_path='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        font=ImageFont.truetype(font_path,27) if Path(font_path).exists() else ImageFont.load_default()
        words=(caption or '').split(); rows=[]; row=''
        img=Image.new('RGBA',(VIDEO_WIDTH,160),(0,0,0,0)); draw=ImageDraw.Draw(img)
        for word in words:
            cand=(row+' '+word).strip(); shaped=get_display(arabic_reshaper.reshape(cand))
            if row and draw.textbbox((0,0),shaped,font=font)[2] > VIDEO_WIDTH-70: rows.append(row); row=word
            else: row=cand
        if row: rows.append(row)
        if len(rows)>2:
            midpoint=max(1,len(words)//2); rows=[' '.join(words[:midpoint]),' '.join(words[midpoint:])]
            font=ImageFont.truetype(font_path,23) if Path(font_path).exists() else font
        shaped=[get_display(arabic_reshaper.reshape(x)) for x in rows[:2]]
        draw.rounded_rectangle((12,12,VIDEO_WIDTH-12,148),radius=18,fill=(0,0,0,170))
        for i,line in enumerate(shaped):
            box=draw.textbbox((0,0),line,font=font,stroke_width=1); x=max(12,(VIDEO_WIDTH-(box[2]-box[0]))//2)
            draw.text((x,30+i*47),line,font=font,fill=(255,255,255,255),stroke_width=1,stroke_fill=(0,0,0,230))
        png=workdir/f'caption_{index}.png'; img.save(png)
        # Put subtitles at the bottom with a safe margin.
        run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(src),'-loop','1','-framerate',str(VIDEO_FPS),'-i',str(png),'-filter_complex','[0:v][1:v]overlay=0:H-170:format=auto[v]','-map','[v]','-t',str(SCENE_DURATION),'-an','-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p','-r',str(VIDEO_FPS),str(dest)],120)
        return dest if dest.exists() and dest.stat().st_size>1000 else src
    except Exception:
        log.exception('Subtitle rendering failed for scene %s',index); return src

# ---------------- Narration / effects ----------------
def create_narration(text,dest):
    dest.parent.mkdir(parents=True,exist_ok=True); dest.unlink(missing_ok=True)
    try:
        import edge_tts
        async def gen(): await edge_tts.Communicate(text,voice=TTS_VOICE,rate=TTS_RATE).save(str(dest))
        asyncio.run(gen())
        if dest.exists() and dest.stat().st_size>1000: return dest
    except Exception: log.exception('Edge TTS failed; trying gTTS')
    try:
        from gtts import gTTS
        gTTS(text=text,lang='ar',slow=False).save(str(dest))
        if dest.exists() and dest.stat().st_size>1000: return dest
    except Exception: log.exception('gTTS failed')
    raise RuntimeError('Arabic narration generation failed')

def fit_audio_to_scene(src,dest,duration=SCENE_DURATION):
    d=probe_duration(src)
    if not d or d<=0: raise RuntimeError(f'Could not read narration duration: {src}')
    # Allow mild acceleration only; if longer, modestly compress rather than hard cut.
    speed=min(1.45,max(1.0,d/(duration-0.18)))
    af=(f'atempo={speed:.4f},' if speed>1.001 else '')+f'apad=pad_dur={duration},atrim=duration={duration},asetpts=PTS-STARTPTS, loudnorm=I=-19:TP=-2:LRA=7'
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(src),'-af',af,'-ar','44100','-ac','2','-c:a','aac','-b:a','128k',str(dest)],90)
    return dest

def make_scene_sfx(kind,dest,duration=SCENE_DURATION):
    d=float(duration)
    # Synthetic, low-cost dramatic textures. Voice remains dominant in final mix.
    if kind=='beast': src=f'anoisesrc=color=brown:sample_rate=44100:duration={d}'; af='highpass=f=45,lowpass=f=850,tremolo=f=5:d=0.75,volume=0.34,afade=t=in:d=0.15,afade=t=out:st=3.7:d=1.0'
    elif kind in ('fight','impact'): src=f'anoisesrc=color=pink:sample_rate=44100:duration={d}'; af='highpass=f=50,lowpass=f=1600,volume=0.42,afade=t=in:d=0.02,afade=t=out:st=0.65:d=0.8'
    elif kind=='magic': src=f'sine=frequency=95:sample_rate=44100:duration={d}'; af='tremolo=f=3:d=0.75,chorus=0.5:0.7:45:0.35:0.25:2,volume=0.19,afade=t=in:d=0.7,afade=t=out:st=3.5:d=1.2'
    elif kind=='storm': src=f'anoisesrc=color=pink:sample_rate=44100:duration={d}'; af='lowpass=f=420,volume=0.15,tremolo=f=0.25:d=0.4,afade=t=in:d=0.6,afade=t=out:st=3.5:d=1.2'
    elif kind in ('tension','mystery','cliffhanger'): src=f'sine=frequency=72:sample_rate=44100:duration={d}'; af='tremolo=f=2:d=0.55,lowpass=f=500,volume=0.11,afade=t=in:d=0.7,afade=t=out:st=3.5:d=1.2'
    elif kind=='steps': src=f'anoisesrc=color=brown:sample_rate=44100:duration={d}'; af='lowpass=f=240,volume=0.12,tremolo=f=1.7:d=0.85,afade=t=in:d=0.1,afade=t=out:st=3.6:d=1.0'
    elif kind=='romance': src=f'sine=frequency=440:sample_rate=44100:duration={d}'; af='vibrato=f=4:d=0.15,lowpass=f=1200,volume=0.035,afade=t=in:d=0.7,afade=t=out:st=3.4:d=1.2'
    elif kind=='castle': src=f'anoisesrc=color=pink:sample_rate=44100:duration={d}'; af='highpass=f=100,lowpass=f=700,volume=0.055,afade=t=in:d=0.4,afade=t=out:st=3.6:d=1.0'
    else: src=f'anoisesrc=color=pink:sample_rate=44100:duration={d}'; af='lowpass=f=1000,volume=0.06,afade=t=in:d=0.3,afade=t=out:st=3.6:d=1.0'
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi','-i',src,'-af',af,'-ar','44100','-ac','2','-c:a','aac','-b:a','96k',str(dest)],60)
    if not dest.exists() or dest.stat().st_size<1000: raise RuntimeError(f'Could not create SFX: {kind}')
    return dest

def make_background_music(dest,duration):
    # Existing no-cost generated ambient drone retained; not a commercial music recording.
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi','-i',f'sine=frequency=55:sample_rate=44100:duration={duration}','-af','volume=0.035,tremolo=f=0.18:d=0.25,afade=t=in:d=2,afade=t=out:st=86:d=4','-ar','44100','-ac','2','-c:a','aac','-b:a','96k',str(dest)],90)
    return dest

def concatenate_media(paths,dest,workdir,name):
    lst=workdir/f'{name}_concat.txt'
    with open(lst,'w',encoding='utf-8') as f:
        for p in paths: f.write("file '"+Path(p).resolve().as_posix()+"'\n")
    # Re-encode concat to avoid AAC stream-copy timestamp/format mismatches.
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(lst),'-t',str(VIDEO_DURATION),'-c:v','libx264' if name=='video' else 'aac','-preset','ultrafast' if name=='video' else '','-crf','24' if name=='video' else '','-pix_fmt','yuv420p' if name=='video' else '','-c:a','aac' if name=='video' else '','-b:a','128k' if name!='video' else '',str(dest)],180)
    return dest

def concatenate_video(paths,dest,workdir):
    lst=workdir/'video_concat.txt'
    with open(lst,'w',encoding='utf-8') as f:
        for p in paths: f.write("file '"+Path(p).resolve().as_posix()+"'\n")
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(lst),'-t',str(VIDEO_DURATION),'-an','-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p','-r',str(VIDEO_FPS),str(dest)],180)
    return dest

def concatenate_audio(paths,dest,workdir,name):
    lst=workdir/f'{name}_concat.txt'
    with open(lst,'w',encoding='utf-8') as f:
        for p in paths: f.write("file '"+Path(p).resolve().as_posix()+"'\n")
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(lst),'-t',str(VIDEO_DURATION),'-vn','-c:a','aac','-b:a','128k',str(dest)],180)
    return dest

def mix_final_audio(video,voice,sfx,music,out):
    run_command(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(video),'-i',str(voice),'-i',str(sfx),'-i',str(music),'-filter_complex',
      '[1:a]volume=1.0,apad,atrim=duration=90[voice];[2:a]volume=0.34,apad,atrim=duration=90[sfx];[3:a]volume=0.22,apad,atrim=duration=90[music];[voice][sfx][music]amix=inputs=3:duration=longest:dropout_transition=0,alimiter=limit=0.92,atrim=duration=90[aout]',
      '-map','0:v:0','-map','[aout]','-t','90','-c:v','copy','-c:a','aac','-b:a','160k','-movflags','+faststart',str(out)],240)
    if not out.exists() or out.stat().st_size<10000: raise RuntimeError('Final video missing or too small')
    return out

# ---------------- Telegram async bridge ----------------
def send_coroutine(application,coro,timeout=360):
    loop=application.bot_data.get('event_loop')
    if loop is None or not loop.is_running(): coro.close(); raise RuntimeError('Telegram event loop unavailable')
    return asyncio.run_coroutine_threadsafe(coro,loop).result(timeout=timeout)

async def send_text(application,chat_id,message): await application.bot.send_message(chat_id=chat_id,text=message[:3500])
async def send_video(application,chat_id,path,job_id):
    with open(path,'rb') as f:
        await application.bot.send_video(chat_id=chat_id,video=f,caption=f'تم إنشاء فيديو ظل ZIL.\nالمدة المستهدفة: 90 ثانية\nرقم العملية: {job_id}',supports_streaming=True,read_timeout=180,write_timeout=180,connect_timeout=30,pool_timeout=30)

# ---------------- Build video job ----------------
def build_video(job_id,chat_id,story,telegram_app):
    global ACTIVE_JOBS
    wd=BASE_DIR/job_id; wd.mkdir(parents=True,exist_ok=True)
    try:
        update_job(job_id,status='running',stage='preflight',progress=1)
        if not command_exists('ffmpeg') or not command_exists('ffprobe'): raise RuntimeError('FFmpeg/FFprobe missing; check Dockerfile')
        if not PIXABAY_API_KEY: raise RuntimeError('PIXABAY_API_KEY is missing')
        story=(story or '').strip()[:MAX_STORY_LENGTH]
        if not story: raise RuntimeError('Story is empty')
        update_job(job_id,stage='writing_story',progress=2)
        package=create_story_package(story); scenes=package['scenes']
        update_job(job_id,stage='story_ready',progress=5,title=package['title'],story=package['story'],narration=package['narration'],narration_words=word_count(package['narration']))
        try: send_coroutine(telegram_app,send_text(telegram_app,chat_id,f"اكتملت كتابة القصة: {package['title']}\nسيتم تجهيز 18 مشهدًا مع راوي عربي وترجمة ومؤثرات. قد يستغرق العمل وقتًا."),45)
        except Exception: log.warning('Could not send story-ready update')
        videos=[]; voices=[]; sfxs=[]
        for i,scene in enumerate(scenes):
            n=i+1; update_job(job_id,stage=f'scene_{n}_download',progress=round(5+(i/SCENE_COUNT)*78))
            query=scene['search_query']
            try: url=search_pixabay_video(query)
            except Exception as e:
                log.warning('Pixabay query failed for scene %s (%s), trying descriptive fallback',n,e)
                # Use words from visual prompt before generic fallback.
                words=re.findall(r'[A-Za-z0-9]+',scene.get('visual_prompt',''))[:7]
                fallback=' '.join(words) or 'cinematic fantasy dramatic scene'
                try: url=search_pixabay_video(fallback)
                except Exception: url=search_pixabay_video('cinematic fantasy castle warrior dramatic')
            raw=wd/f'raw_{n}.mp4'; base=wd/f'base_{n}.mp4'; cap=wd/f'scene_{n}.mp4'
            download_video(url,raw); normalize_scene(raw,base)
            rendered=add_arabic_caption(base,scene['narration'],cap,wd,n)
            videos.append(rendered if Path(rendered).exists() else base); raw.unlink(missing_ok=True)
            rawvoice=wd/f'voice_raw_{n}.mp3'; fitvoice=wd/f'voice_{n}.m4a'
            create_narration(scene['narration'],rawvoice); fit_audio_to_scene(rawvoice,fitvoice); voices.append(fitvoice)
            fx=wd/f'sfx_{n}.m4a'; make_scene_sfx(scene['effect'],fx); sfxs.append(fx)
            update_job(job_id,stage=f'scene_{n}_complete',progress=round(5+((i+1)/SCENE_COUNT)*78))
        update_job(job_id,stage='concatenate_video',progress=85)
        silent=wd/'silent_video.mp4'; concatenate_video(videos,silent,wd)
        voice=wd/'voice_90s.m4a'; concatenate_audio(voices,voice,wd,'voice')
        sfx=wd/'sfx_90s.m4a'; concatenate_audio(sfxs,sfx,wd,'sfx')
        music=wd/'ambient_90s.m4a'; make_background_music(music,VIDEO_DURATION)
        update_job(job_id,stage='mix_audio',progress=94)
        final=wd/'ZIL_video.mp4'; mix_final_audio(silent,voice,sfx,music,final)
        dur=probe_duration(final)
        if dur is None or abs(dur-VIDEO_DURATION)>0.35: raise RuntimeError(f'Final duration check failed: {dur}; expected 90 seconds')
        update_job(job_id,status='sending',stage='sending_video',progress=98,output=str(final),size=final.stat().st_size,duration=dur)
        if final.stat().st_size>MAX_VIDEO_SIZE:
            send_coroutine(telegram_app,send_text(telegram_app,chat_id,f'اكتمل الفيديو ومدته {dur:.1f} ثانية لكن حجمه أكبر من حد الإرسال. رقم العملية: {job_id}'),45)
            update_job(job_id,status='completed',stage='completed_file_too_large',progress=100); return
        send_coroutine(telegram_app,send_video(telegram_app,chat_id,final,job_id),360)
        update_job(job_id,status='completed',stage='completed',progress=100,duration=dur)
        log.info('Job %s completed: %.2fs',job_id,dur)
    except Exception as e:
        report_error(job_id,e)
        try: send_coroutine(telegram_app,send_text(telegram_app,chat_id,f'تعذر إكمال الفيديو.\nرقم العملية: {job_id}\nاستخدم /last_error لمعرفة السبب.'),45)
        except Exception: log.exception('Could not notify user about failure')
    finally:
        with JOBS_LOCK: ACTIVE_JOBS=max(0,ACTIVE_JOBS-1)

# ---------------- Commands ----------------
async def start_command(update,context):
    if update.message: await update.message.reply_text('أهلًا بك في ظل ZIL.\n\n/test - فيديو تجريبي 90 ثانية\n/make فكرة القصة - كتابة القصة وإنتاج الفيديو\n/status - حالة العمليات\n/diagnose - فحص النظام\n/last_error - آخر خطأ\n/health - حالة الخدمة')

async def start_job(update,context,story):
    global ACTIVE_JOBS
    if not update.message or not update.effective_chat: return
    story=(story or '').strip()[:MAX_STORY_LENGTH]
    if not story: await update.message.reply_text('أرسل فكرة القصة أولًا.'); return
    with JOBS_LOCK:
        if ACTIVE_JOBS>=MAX_ACTIVE_JOBS: busy=True
        else:
            busy=False; jid=uuid.uuid4().hex[:10]
            JOBS[jid]={'id':jid,'chat_id':update.effective_chat.id,'status':'queued','stage':'queued','story':story[:500],'created_at':time.time(),'updated_at':time.time()}; ACTIVE_JOBS+=1
    if busy: await update.message.reply_text('يوجد فيديو قيد المعالجة. حاول مرة أخرى لاحقًا.'); return
    await update.message.reply_text(f'بدأت صناعة فيديو ظل ZIL.\nرقم العملية: {jid}\nستُكتب القصة أولًا، ثم تُجهّز 18 لقطة مع راوي عربي وترجمة ومؤثرات. عند حدود Groq سيحاول البوت الانتظار وإعادة الطلب بعدد محدود من المرات.')
    try: threading.Thread(target=build_video,args=(jid,update.effective_chat.id,story,context.application),daemon=True).start()
    except Exception as e:
        with JOBS_LOCK: ACTIVE_JOBS=max(0,ACTIVE_JOBS-1)
        report_error(jid,e); await update.message.reply_text('تعذر بدء إنشاء الفيديو.')

async def test_command(update,context):
    await start_job(update,context,'في مملكة غامضة، يصل رجل يخفي قوة خارقة. تقع الأميرة في حبه، لكن الملك يرفض العلاقة. يظهر نمر عملاق أمام القصر، فيواجهه الرجل ويكشف جزءًا من قوته المخفية، ثم يظهر سر جديد يهدد المملكة.')
async def make_command(update,context):
    story=' '.join(context.args).strip()
    if not story:
        await update.message.reply_text('اكتب فكرة القصة بعد الأمر، مثال:\n/make رجل غامض يصل إلى مملكة تحكمها أميرة ويخفي قوة أسطورية'); return
    await start_job(update,context,story)

async def status_command(update,context):
    with JOBS_LOCK: active=ACTIVE_JOBS; recent=list(JOBS.values())[-5:]
    lines=['حالة ظل ZIL',f'العمليات النشطة: {active}',f'Pixabay: {"جاهز" if PIXABAY_API_KEY else "مفتاح مفقود"}',f'Groq AI: {"جاهز" if GROQ_API_KEY else "مفتاح مفقود"}',f'FFmpeg: {"جاهز" if command_exists("ffmpeg") else "غير موجود"}',f'FFprobe: {"جاهز" if command_exists("ffprobe") else "غير موجود"}','']
    for j in reversed(recent):
        lines.append(f"\n{j['id']} | {j.get('status')} | {j.get('stage')} | {j.get('progress',0)}%")
        if j.get('error'): lines.append('الخطأ: '+j['error'][:300])
    await update.message.reply_text('\n'.join(lines)[:3900])

async def last_error_command(update,context):
    with JOBS_LOCK: failed=[j for j in JOBS.values() if j.get('status')=='failed']
    if not failed: await update.message.reply_text('لا توجد أخطاء مسجلة حاليًا.'); return
    j=failed[-1]; await update.message.reply_text(f"آخر خطأ في ظل ZIL\nالعملية: {j['id']}\nالمرحلة: {j.get('stage')}\nالخطأ:\n{j.get('error','غير معروف')[:2500]}")

async def diagnose_command(update,context):
    out=[f'BOT_TOKEN: {"OK" if BOT_TOKEN else "MISSING"}',f'PIXABAY_API_KEY: {"OK" if PIXABAY_API_KEY else "MISSING"}',f'GROQ_API_KEY: {"OK" if GROQ_API_KEY else "MISSING"}',f'GROQ_MODEL: {GROQ_MODEL}',f'Groq max output tokens: {GROQ_MAX_TOKENS}',f'Python: {sys.version.split()[0]}',f'FFmpeg: {command_exists("ffmpeg")}',f'FFprobe: {command_exists("ffprobe")}',f'Duration target: {VIDEO_DURATION}s']
    if PIXABAY_API_KEY:
        try:
            r=HTTP.get(PIXABAY_API,params={'key':PIXABAY_API_KEY,'q':'nature','per_page':2},timeout=15); out.append(f'Pixabay API: HTTP {r.status_code}')
        except Exception as e: out.append('Pixabay API error: '+str(e)[:200])
    await update.message.reply_text('تشخيص ظل ZIL\n\n'+'\n'.join(out))
async def health_command(update,context):
    await update.message.reply_text(f'ظل ZIL يعمل.\nPython: {sys.version.split()[0]}\nFFmpeg: {command_exists("ffmpeg")}\nFFprobe: {command_exists("ffprobe")}\nPixabay: {bool(PIXABAY_API_KEY)}\nGroq AI: {bool(GROQ_API_KEY)}\nالمدة المستهدفة: {VIDEO_DURATION} ثانية')

# ---------------- Flask ----------------
@app.get('/')
def home(): return jsonify({'service':'ZIL','status':'running','ai_story_configured':bool(GROQ_API_KEY),'target_duration_seconds':VIDEO_DURATION})
@app.get('/health')
def health(): return jsonify({'status':'ok','ffmpeg':command_exists('ffmpeg'),'ffprobe':command_exists('ffprobe'),'pixabay_configured':bool(PIXABAY_API_KEY),'groq_configured':bool(GROQ_API_KEY),'active_jobs':get_active_jobs(),'target_duration_seconds':VIDEO_DURATION})
def run_web(): app.run(host='0.0.0.0',port=PORT,debug=False,use_reloader=False)

# ---------------- Startup ----------------
def main():
    log.info('Starting ZIL; target duration=%s',VIDEO_DURATION)
    if not BOT_TOKEN: log.critical('BOT_TOKEN missing'); sys.exit(1)
    if not command_exists('ffmpeg') or not command_exists('ffprobe'): log.critical('FFmpeg/FFprobe missing; check Dockerfile'); sys.exit(1)
    threading.Thread(target=run_web,daemon=True).start()
    async def save_loop(application): application.bot_data['event_loop']=asyncio.get_running_loop()
    tg=Application.builder().token(BOT_TOKEN).post_init(save_loop).build()
    tg.add_handler(CommandHandler('start',start_command)); tg.add_handler(CommandHandler('test',test_command)); tg.add_handler(CommandHandler('make',make_command)); tg.add_handler(CommandHandler('status',status_command)); tg.add_handler(CommandHandler('diagnose',diagnose_command)); tg.add_handler(CommandHandler('last_error',last_error_command)); tg.add_handler(CommandHandler('health',health_command))
    log.info('Telegram polling starting')
    tg.run_polling(drop_pending_updates=False,allowed_updates=Update.ALL_TYPES)

if __name__=='__main__': main()
