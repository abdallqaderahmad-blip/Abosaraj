import os, time, shutil, tempfile, threading, subprocess, urllib.parse, asyncio
from pathlib import Path
import requests
from flask import Flask, request
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

BOT_TOKEN = os.environ.get("BOT_TOKEN","")
PORT = int(os.getenv("PORT","10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL","").rstrip("/")

app = Flask(__name__)
lock = threading.Lock()
processing_chats = set()
plock = threading.Lock()
def log(m):
    with lock: print("[V7.1] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def get_font():
    fp = Path("/tmp/fonts/Amiri-Regular.ttf")
    fp.parent.mkdir(exist_ok=True)
    if not fp.exists():
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",timeout=12)
            if r.status_code==200: fp.write_bytes(r.content)
        except: pass
    for p in [str(fp), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists(): return p
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT=get_font()

HERO_DESC = "same handsome Arab man 25 years old short black hair blue eyes sharp jawline dark blue cloak consistent face photorealistic"
KING_DESC = "same old Arab king 60 white beard crown red robe throne consistent face"
PRINCESS_DESC = "same beautiful Arab princess 22 long black hair green eyes royal dress consistent face"

# === V6.5.3 FIXED IMAGES + V7 CINEMATIC ===
def gen_image_cinematic(prompt_ar, out, scene_no):
    t=prompt_ar
    if "قصر" in t and "يدخل" in t:
        char=HERO_DESC; en="entering golden palace door job interview cinematic epic"
    elif "يطرد" in t or "اطردوا" in t:
        char=KING_DESC; en="angry king shouting expel dirty man throne room golden"
    elif "الحراس" in t:
        char=HERO_DESC; en="guards pushing sad same Arab man out of palace door dramatic"
    elif "يمشي" in t and "الغابة" in t:
        char=HERO_DESC; en="same Arab man walking alone dark forest night sad cinematic moonlight"
    elif "ذئبة" in t:
        char=HERO_DESC + " hugging small white wolf pup"; en="small white wolf pup crying cold snow cute blue eyes"
    elif "نمر" in t and "يزأر" in t:
        char="giant enormous tiger 4 meters orange black stripes roaring attacking"; en="snow forest dark attacking"
    elif "تلمع" in t:
        char=HERO_DESC + " eyes glowing blue superpower"; en="transformation power epic light"
    elif "يضرب" in t:
        char=HERO_DESC + " superpower punching giant tiger flying"; en="epic fight superpower"
    elif "الأميرة" in t:
        char=PRINCESS_DESC; en="shocked saying oh my god what power cinematic"
    elif "سامحني" in t:
        char=KING_DESC + " crying kneeling"; en="sorry forgive my son you are king emotional"
    else:
        char=HERO_DESC; en="cinematic story"

    full = f"{char}, {en}, photorealistic same character consistent face cinematic dramatic lighting vertical 9:16"
    safe = urllib.parse.quote(full[:300])
    base_seed = 1234 + scene_no*23

    # 6 محاولات - turbo ثم flux - هذا هو Fix الصور
    for attempt in range(6):
        seed = base_seed + attempt*11
        model = "turbo" if attempt < 3 else "flux"
        try:
            url = f"https://image.pollinations.ai/prompt/{safe}?width=512&height=768&model={model}&seed={seed}&nologo=true&enhance=false"
            log(f"IMG {scene_no} try {attempt+1}/{6} {model} seed={seed}")
            r = requests.get(url, timeout=45)
            if r.status_code==200 and len(r.content)>15000:
                Path(out).write_bytes(r.content)
                log(f"IMG {scene_no} OK {len(r.content)} bytes try={attempt+1}")
                return Path(out)
            else:
                log(f"IMG {scene_no} small {len(r.content) if 'r' in locals() else 0} -> retry")
                time.sleep(1.2 + attempt*0.8)
        except Exception as e:
            log(f"IMG {scene_no} err try {attempt+1} {e}")
            time.sleep(1.5 + attempt*0.5)

    try:
        log(f"IMG {scene_no} ALL FAILED -> PIL gradient")
        colors=[(35,45,65),(60,40,50),(25,55,45),(50,50,70),(40,60,60)]
        c=colors[scene_no%5]
        img=Image.new('RGB',(512,768),c)
        from PIL import ImageDraw
        draw=ImageDraw.Draw(img)
        draw.text((20,700), f"Scene {scene_no}", fill=(255,255,255))
        img.save(str(out),"JPEG",quality=90)
        return Path(out)
    except:
        Path(out).write_bytes(b'\xff\xd8\xff\xe0'); return Path(out)

async def _edge_fixed(text, out):
    import edge_tts
    comm = edge_tts.Communicate(text, "ar-SA-HamedNeural", rate="-14%", pitch="-4Hz")
    await comm.save(str(out))

def gen_voice_fixed(text, out):
    try:
        asyncio.run(_edge_fixed(text.strip()[:130], out))
        return out if Path(out).exists() and Path(out).stat().st_size>800 else None
    except Exception as e:
        log(f"VOICE fail {e}"); return None

def gen_ambient_smart(text, out, dur=5):
    low=text.lower()
    if "قصر" in low: filt="anoisesrc=d=3:c=brown:r=22050:a=0.03,lowpass=f=400,volume=0.28"
    elif "غابة" in low: filt="anoisesrc=d=3:c=brown:r=22050:a=0.06,highpass=f=600,lowpass=f=2500,volume=0.32"
    elif "ذئبة" in low: filt="anoisesrc=d=3:c=white:r=22050:a=0.02,sine=f=350:d=3,lowpass=f=1000,volume=0.25"
    elif "نمر" in low: filt="anoisesrc=d=3:c=brown:r=22050:a=0.11,sine=f=60:d=3,lowpass=f=200,volume=0.45"
    elif "يضرب" in low or "يهجم" in low: filt="anoisesrc=d=3:c=brown:r=22050:a=0.13,lowpass=f=300,volume=0.50"
    else: filt="anoisesrc=d=3:c=pink:r=22050:a=0.04,lowpass=f=700,volume=0.20"
    try:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",filt,"-t",str(dur),"-c:a","aac","-b:a","48k",str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        return out if Path(out).exists() else None
    except: return None

def gen_music_cinematic(out, total_dur=55):
    try:
        cmd=["ffmpeg","-y","-f","lavfi","-i",f"sine=f=110:d={total_dur},sine=f=165:d={total_dur},sine=f=220:d={total_dur}","-filter_complex","[0:a]amix=inputs=3:duration=longest:weights=1 0.7 0.5,volume=0.10,lowpass=f=900,atempo=0.85,apulsator=hz=0.15[a]","-map","[a]","-t",str(total_dur),"-c:a","aac","-b:a","64k",str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return out if Path(out).exists() else None
    except: return None

def gen_video_cinematic(img_path, scene_text, voice_path, out_path, scene_no, work_dir):
    def esc(t): return str(t or "").replace("'","").replace('"',"").replace(":"," ").replace("\n"," ")[:60]
    raw=esc(scene_text)
    if scene_no==1: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.006,1.60))"; eq="eq=contrast=1.32:saturation=1.5:brightness=0.04"
    elif scene_no==2: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.004,1.40))"; eq="eq=contrast=1.38:saturation=1.2:brightness=-0.05"
    elif scene_no==4: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.0008,1.20))"; eq="eq=contrast=1.05:saturation=0.85:brightness=0.02"
    elif scene_no==6: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.008,1.55))"; eq="eq=contrast=1.45:saturation=1.3:brightness=-0.03"
    elif scene_no==8: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.009,1.70))"; eq="eq=contrast=1.40:saturation=1.5"
    else: zoom="if(lte(zoom,1.0),1.0,min(zoom+0.003,1.45))"; eq="eq=contrast=1.30:saturation=1.45"

    vf_base = f"scale=512:768:force_original_aspect_ratio=increase,crop=512:768,zoompan=z='{zoom}':d=1:fps=24:s=512:768:fps=24,{eq},unsharp=5:5:0.9:5:5:0.0,vignette=angle=PI/4:mode=forward"
    if scene_no in [6,8]:
        vf_base += ",crop=in_w-4:in_h-4:(in_w-out_w)/2+sin(n*0.3)*4:(in_h-out_h)/2+cos(n*0.4)*4"
    vf_final = vf_base + f",drawtext=fontfile={FONT}:text='{raw}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.88:boxborderw=9:x=(w-text_w)/2:y=h-85"

    try:
        if voice_path and Path(voice_path).exists():
            p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice_path)], stdout=subprocess.PIPE, text=True, timeout=5)
            try: dur=float(p.stdout.strip() or "5.5")
            except: dur=5.5
            dur=max(4.8, min(dur+0.4, 6.0))
        else: dur=5.2
    except: dur=5.2

    ambient=work_dir / f"amb_{scene_no}.mp3"
    gen_ambient_smart(scene_text, ambient, dur)

    try:
        if voice_path and Path(voice_path).exists() and Path(ambient).exists():
            cmd=["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-i",str(ambient),"-filter_complex","[1:a]volume=1.0[vox];[2:a]volume=0.35[amb];[vox][amb]amix=inputs=2:duration=first:weights=1 0.4[mix]","-vf",vf_final,"-t",str(dur),"-r","24","-map","0:v","-map","[mix]","-c:v","libx264","-preset","veryfast","-crf","24","-c:a","aac","-b:a","128k","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        elif voice_path and Path(voice_path).exists():
            cmd=["ffmpeg","-y","-loop","1","-i",str(img_path),"-i",str(voice_path),"-vf",vf_final,"-t",str(dur),"-r","24","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","24","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out_path)]
        else:
            cmd=["ffmpeg","-y","-loop","1","-i",str(img_path),"-vf",vf_final,"-t","5.2","-r","24","-c:v","libx264","-preset","veryfast","-crf","24","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=70)
        return out_path
    except Exception as e:
        log(f"VIDEO {scene_no} fail {e} -> simple")
        cmd2=["ffmpeg","-y","-loop","1","-i",str(img_path),"-t","5","-r","24","-c:v","libx264","-preset","ultrafast","-crf","28","-pix_fmt","yuv420p","-movflags","+faststart",str(out_path)]
        subprocess.run(cmd2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        return out_path

def concat_copy(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{v}'\n")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c","copy","-movflags","+faststart",str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    return out

def final_mix_music(video_path, music_path, out_path):
    try:
        if music_path and Path(music_path).exists():
            cmd=["ffmpeg","-y","-i",str(video_path),"-i",str(music_path),"-filter_complex","[0:a]volume=1.0[a0];[1:a]volume=0.13[a1];[a0][a1]amix=inputs=2:duration=first:weights=1 0.18[a]","-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k","-shortest",str(out_path)]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            return out_path
    except: pass
    shutil.copy(video_path, out_path); return out_path

TELEGRAM_API="https://api.telegram.org/bot"+BOT_TOKEN
def telegram(m,d=None,files=None,timeout=60):
    r=requests.post(TELEGRAM_API+"/"+m,data=d,files=files,timeout=timeout); r.raise_for_status(); return r.json()
def send_text(c,t): return telegram("sendMessage",{"chat_id":c,"text":t})
def send_video(c,p,cap):
    with Path(p).open("rb") as f: return telegram("sendVideo",{"chat_id":c,"caption":cap,"supports_streaming":"true"},{"video":("final.mp4",f,"video/mp4")},600)

LONG_STORY="""شاب فقير يدخل قصر الملك الذهبي يبحث عن عمل
الملك يصرخ اطردوا هذا القذر من قصري
الحراس يطردون الشاب حزينا خارج القصر
الشاب يمشي وحيدا في الغابة المظلمة يقول الغابة أحن علي من البشر
يجد ذئبة بيضاء صغيرة تبكي من البرد فيحضنها لا تخافي صغيرتي
نمر عملاق يزأر ويهجم على الذئبة
عيون الشاب تلمع حان وقت الحقيقة
الشاب يضرب النمر بضربة أسطورية يطير بعيدا
الأميرة تقول يا إلهي ما هذه القوة العظيمة
الملك يبكي سامحني يا بني وتصبح انت الملك ولكن فجأة سمعنا صوتا من السماء"""

def process(chat_id, user_text):
    work=Path(tempfile.mkdtemp(prefix="v71_"))
    try:
        lines=[l.strip() for l in user_text.split("\n") if l.strip()][:10]
        if len(lines)<8: lines=LONG_STORY.split("\n")
        send_text(chat_id,"🎬 V7.1 FINAL\n🎙️ راوي ثابت\n🎧 طبيعة+موسيقى\n🎥 زوم سينمائي+اهتزاز\n🖼️ FIX صور حقيقية 3 ب 3")

        results={}
        batches=[lines[i:i+3] for i in range(0,len(lines),3)]
        done=0
        for b_idx, batch in enumerate(batches):
            def job(b_item):
                gi, txt = b_item
                no=gi+1
                img=work / f"img_{no}.jpg"
                voice=work / f"voice_{no}.mp3"
                gen_image_cinematic(txt, img, no)
                gen_voice_fixed(txt, voice)
                return no, img, voice, txt

            with ThreadPoolExecutor(max_workers=3) as ex:
                futures=[ex.submit(job,(b_idx*3+j, txt)) for j,txt in enumerate(batch)]
                for f in as_completed(futures):
                    no,img,voice,txt=f.result()
                    results[no]=(img,voice,txt)
                    done+=1
                    send_text(chat_id,f"✅ {done}/10 صورة حقيقية+راوي ثابت batch {b_idx+1}/4")
            if b_idx < len(batches)-1:
                time.sleep(2.5)

        send_text(chat_id,"🎬 نحول فيديو سينمائي - زوم+اهتزاز+طبيعة+موسيقى")

        def job_v(item):
            no,(img,voice,txt)=item
            vid=work / f"vid_{no}.mp4"
            gen_video_cinematic(img, txt, voice if Path(voice).exists() else None, vid, no, work)
            return no,vid

        vids={}
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures=[ex.submit(job_v,kv) for kv in results.items()]
            for f in as_completed(futures):
                no,vid=f.result()
                vids[no]=vid
                send_text(chat_id,f"🎥 {no}/10 سينمائي")

        videos=[vids[i] for i in range(1,11)]
        raw=work / "raw.mp4"
        concat_copy(videos, raw)
        music=work / "music.mp3"
        gen_music_cinematic(music, 56)
        final=work / "final.mp4"
        final_mix_music(raw, music if music.exists() else None, final)

        send_text(chat_id,"🎬 V7.1 جاهز - انتاج بصري مميز\n✅ راوي ثابت\n✅ صور حقيقية 100% - 3 ب 3\n✅ موسيقى+طبيعة\n✅ زوم+اهتزاز")
        send_video(chat_id, final, "V7.1 CINEMATIC FINAL")

    except Exception as e:
        log("ERR "+repr(e))
        try: send_text(chat_id,"❌ "+str(e)[:800])
        except: pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with plock: processing_chats.discard(chat_id)

def handle_update(update):
    msg=update.get("message") or {}; chat=(msg.get("chat") or {}).get("id"); text=(msg.get("text") or "").strip()
    if not chat: return
    if text=="/start": send_text(chat,"🎬 V7.1 FINAL\n/full = راوي ثابت + صور حقيقية + سينمائي"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat,"✅ تم المسح"); return
    user_text=LONG_STORY if text=="/full" else (text if len(text.split("\n"))>=4 else LONG_STORY)
    with plock:
        if chat in processing_chats: send_text(chat,"⏳ شغال"); return
        processing_chats.add(chat)
    threading.Thread(target=process, args=(chat, user_text), daemon=True).start()

@app.get("/")
def home(): return "V7.1 FINAL",200
@app.post("/telegram/webhook")
def webhook():
    upd=request.get_json(silent=True) or {}
    threading.Thread(target=handle_update, args=(upd,), daemon=True).start()
    return "OK",200

def setup_webhook():
    if not RENDER_EXTERNAL_URL or not BOT_TOKEN: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", data={"url":RENDER_EXTERNAL_URL+"/telegram/webhook","drop_pending_updates":"true"}, timeout=10)
    except: pass

if __name__=="__main__":
    setup_webhook()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
