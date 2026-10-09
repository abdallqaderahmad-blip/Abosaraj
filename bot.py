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
    with lock: print("[V8 HOOK] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

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

HERO = "same handsome Arab man 25 short black hair blue eyes dark cloak consistent face"
KING = "same old Arab king 60 white beard crown red robe consistent face"

# 7 مشاهد فقط - هوك 20 ثانية
HOOK_STORY = [
    ("طردوه من القصر لأنه فقير... لكنهم ما بيعرفوا من هو!", "hero", "Arab man kicked out palace sad dramatic hook"),
    ("الحراس رموه بالشارع كالقمامة", "hero", "guards pushing Arab man out dramatic"),
    ("مشى وحيدا بالغابة... الغابة أحن من البشر", "hero", "sad Arab man walking dark forest night cinematic"),
    ("فجأة... وجد ذئبة صغيرة تبكي من البرد", "hero", "small white wolf pup crying snow cute"),
    ("نمر عملاق هجم ليأكلها!", "tiger", "giant tiger roaring attacking snow"),
    ("هنا... عيونه أضاءت! حان وقت الحقيقة", "hero", "Arab hero eyes glowing blue power epic"),
    ("ضربة واحدة... النمر طار 100 متر! الجزء 2؟", "hero", "Arab hero punching giant tiger flying epic slow motion"),
]

def gen_img(prompt_en, out, scene_no):
    full = f"{HERO if scene_no!=5 else 'giant tiger'}, {prompt_en}, photorealistic cinematic 9:16 vertical"
    safe = urllib.parse.quote(full[:280])
    base = 1234 + scene_no*31
    for attempt in range(6):
        seed = base + attempt*13
        model = "turbo" if attempt<3 else "flux"
        try:
            url = f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model={model}&seed={seed}&nologo=true"
            r=requests.get(url, timeout=45)
            if r.status_code==200 and len(r.content)>15000:
                Path(out).write_bytes(r.content)
                log(f"IMG {scene_no} OK")
                return Path(out)
            time.sleep(1.2)
        except: time.sleep(1.2)
    Image.new('RGB',(720,1280),(30+scene_no*8,40,60)).save(str(out),"JPEG",90)
    return Path(out)

async def _edge(text, out):
    import edge_tts
    # راوي هوك - سريع ومشوق
    rate = "-5%" if "طردوه" in text else "-10%"
    comm = edge_tts.Communicate(text[:90], "ar-SA-HamedNeural", rate=rate, pitch="-2Hz")
    await comm.save(str(out))

def gen_voice(text, out):
    try:
        asyncio.run(_edge(text, out))
        return out if Path(out).exists() and Path(out).stat().st_size>800 else None
    except: return None

def gen_video_hook(img, text, voice, out, no, work):
    def esc(t): return str(t).replace("'","").replace('"',"").replace(":"," ")[:65]
    cap = esc(text)

    # هوك سينمائي - كل مشهد حركة مختلفة
    if no==1: # هوك - زوم سريع جدا
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.012,1.80))"; eq="eq=contrast=1.4:saturation=1.6:brightness=0.05"
    elif no==5: # نمر - اهتزاز قوي
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.015,1.90))"; eq="eq=contrast=1.5:saturation=1.4"
    elif no==6: # قوة - يلمع
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.010,1.75))"; eq="eq=contrast=1.45:saturation=1.8:brightness=0.10"
    elif no==7: # ضربة - اسرع شي + slowmo وهمي
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.018,2.0))"; eq="eq=contrast=1.5:saturation=1.7"
    else:
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.007,1.60))"; eq="eq=contrast=1.3:saturation=1.3"

    # ترجمة كبيرة بالنص - ستايل ريلز
    vf = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=30:s=720:1280:fps=30,{eq},unsharp=5:5:1.0,vignette=angle=PI/4"

    # اهتزاز لمشهد 5 و 7
    if no in [5,7]:
        vf += ",crop=in_w-6:in_h-6:(in_w-out_w)/2+sin(n*0.5)*6:(in_h-out_h)/2+cos(n*0.6)*6"

    # نص كبير بالنص - هوك ستايل
    # سطرين - فوق وتحت
    vf += f",drawtext=fontfile={FONT}:text='{cap}':fontcolor=white:fontsize=38:box=1:boxcolor=black@0.90:boxborderw=12:x=(w-text_w)/2:y=(h-text_h)/2-100:line_spacing=8"

    # لو مشهد 1 - نضيف "شاهد للنهاية" صغير
    if no==1:
        vf += f",drawtext=fontfile={FONT}:text='شاهد للنهاية 🔥':fontcolor=yellow:fontsize=28:box=1:boxcolor=red@0.85:boxborderw=8:x=(w-text_w)/2:y=h-180"

    try:
        if voice and Path(voice).exists():
            p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice)], stdout=subprocess.PIPE, text=True, timeout=5)
            try: dur=float(p.stdout.strip() or "3")
            except: dur=3
            dur=max(2.8, min(dur+0.2, 3.5)) # 3 ثواني بس لكل مشهد
        else: dur=3.0
    except: dur=3.0

    # SFX بسيط مجاني
    sfx_filter = "anoisesrc=d=0.3:c=brown:r=22050:a=0.08,volume=0.4" if no in [5,7] else "anullsrc=d=0.1:r=22050"
    sfx = work / f"sfx_{no}.mp3"
    try:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",sfx_filter,"-t","0.3","-c:a","aac",str(sfx)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except: pass

    try:
        if voice and Path(voice).exists() and Path(sfx).exists() and no in [5,7]:
            cmd=["ffmpeg","-y","-loop","1","-i",str(img),"-i",str(voice),"-i",str(sfx),"-filter_complex","[1:a][2:a]amix=inputs=2:duration=first:weights=1 0.6[m]","-vf",vf,"-t",str(dur),"-r","30","-map","0:v","-map","[m]","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out)]
        elif voice and Path(voice).exists():
            cmd=["ffmpeg","-y","-loop","1","-i",str(img),"-i",str(voice),"-vf",vf,"-t",str(dur),"-r","30","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out)]
        else:
            cmd=["ffmpeg","-y","-loop","1","-i",str(img),"-vf",vf,"-t",str(dur),"-r","30","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        return out
    except Exception as e:
        log(f"VIDEO {no} fail {e}")
        cmd2=["ffmpeg","-y","-loop","1","-i",str(img),"-t","3","-r","30","-c:v","libx264","-preset","ultrafast","-crf","26","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
        subprocess.run(cmd2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return out

def concat(videos, out):
    lf=out.parent / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{v}'\n")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c","copy","-movflags","+faststart",str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return out

TELEGRAM_API="https://api.telegram.org/bot"+BOT_TOKEN
def telegram(m,d=None,files=None,timeout=60):
    r=requests.post(TELEGRAM_API+"/"+m,data=d,files=files,timeout=timeout); r.raise_for_status(); return r.json()
def send_text(c,t): return telegram("sendMessage",{"chat_id":c,"text":t})
def send_video(c,p,cap):
    with Path(p).open("rb") as f: return telegram("sendVideo",{"chat_id":c,"caption":cap,"supports_streaming":"true"},{"video":("final.mp4",f,"video/mp4")},600)

def process(chat_id):
    work=Path(tempfile.mkdtemp(prefix="v8hook_"))
    try:
        send_text(chat_id,"🎬 V8 HOOK 20S\n🔥 7 مشاهد بس - 20 ثانية\n⚡ هوك أول ثانيتين\n📱 نص كبير بالنص - ريلز\n🎥 زوم سريع + اهتزاز")

        results={}
        # batch 3 ب 3 - عشان الصور ما تفشل
        batches=[HOOK_STORY[i:i+3] for i in range(0,len(HOOK_STORY),3)]
        done=0
        for b_idx, batch in enumerate(batches):
            def job(item):
                gi, (txt, key, en) = item
                no=gi+1
                img=work / f"img_{no}.jpg"
                voice=work / f"voice_{no}.mp3"
                gen_img(en, img, no)
                gen_voice(txt, voice)
                return no, img, voice, txt

            with ThreadPoolExecutor(max_workers=3) as ex:
                futures=[ex.submit(job,(b_idx*3+j, data)) for j,data in enumerate(batch)]
                for f in as_completed(futures):
                    no,img,voice,txt=f.result()
                    results[no]=(img,voice,txt)
                    done+=1
                    send_text(chat_id,f"✅ {done}/7 هوك")
            if b_idx < len(batches)-1: time.sleep(2.0)

        send_text(chat_id,"🎬 نحول هوك سينمائي 20ث")

        vids={}
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures=[ex.submit(lambda item: (item[0], gen_video_hook(item[1][0], item[1][2], item[1][1], work / f"vid_{item[0]}.mp4", item[0], work)), kv) for kv in results.items()]
            for f in as_completed(futures):
                no,vid=f.result()
                vids[no]=vid
                send_text(chat_id,f"🎥 {no}/7")

        videos=[vids[i] for i in range(1,8)]
        raw=work / "raw.mp4"
        concat(videos, raw)

        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(raw)], stdout=subprocess.PIPE, text=True, timeout=5)
        try: dur=float(p.stdout.strip() or "20")
        except: dur=20

        send_text(chat_id,f"🔥 HOOK 20S جاهز {int(dur)}ث\n✅ هوك أول ثانيتين\n✅ نص كبير بالنص\n✅ 7 مشاهد سريعة\n✅ cliffhanger للجزء 2")
        send_video(chat_id, raw, f"🔥 HOOK 20S - {int(dur)}ث - ريلز احترافي\n\nطردوه لأنه فقير... لكنه سيصدمهم!\nالجزء 2؟ 👇")

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
    if text=="/start": send_text(chat,"🎬 V8 HOOK 20S\n/hook = ريلز 20 ثانية احترافي يضرب"); return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat,"✅ تم المسح"); return
    with plock:
        if chat in processing_chats: send_text(chat,"⏳ شغال"); return
        processing_chats.add(chat)
    threading.Thread(target=process, args=(chat,), daemon=True).start()

@app.get("/")
def home(): return "V8 HOOK 20S",200
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
