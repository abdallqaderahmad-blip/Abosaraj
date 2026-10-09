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
    with lock: print("[V9 THILL] " + time.strftime("%H:%M:%S") + " " + m, flush=True)

def get_font():
    fp = Path("/tmp/fonts/Cairo-Bold.ttf")
    fp.parent.mkdir(exist_ok=True)
    if not fp.exists():
        try:
            r=requests.get("https://github.com/google/fonts/raw/main/ofl/cairo/Cairo-Bold.ttf",timeout=12)
            if r.status_code==200: fp.write_bytes(r.content)
        except: pass
    for p in [str(fp), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(p).exists(): return p
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT=get_font()

HERO = "same handsome Arab man 25 years short black hair intense blue eyes dark cloak consistent face ID 7761 photorealistic"

THILL_20S = [
    ("طردوه صغيراً... قالوا بلا قوة", "hero", "Arab man 25 short black hair blue eyes dark cloak walking away from huge palace gate sad epic back view rain"),
    ("لكن الحارس الأبيض كان ينتظره ثمانية عشر عاماً", "hero", "same Arab man kneeling in dark magical forest meeting giant white wolf eyes glowing loyalty cinematic"),
    ("في القصر... شعرت بختمها يحترق", "princess", "beautiful Arab princess 22 long black hair blue glowing rune on neck burning shocked in palace room"),
    ("وعاد الوريث ليسترد عرشه", "hero", "same Arab man close up face eyes exploding bright blue light power awakening wind storm epic"),
]

def gen_img(prompt_en, out, scene_no):
    full = f"{HERO if scene_no!=3 else 'beautiful Arab princess 22 blue rune'}, {prompt_en}, photorealistic cinematic 9:16 vertical dramatic lighting"
    safe = urllib.parse.quote(full[:300])
    base = 7761 + scene_no*33
    for attempt in range(6):
        seed = base + attempt*17
        model = "turbo" if attempt<3 else "flux"
        try:
            url = f"https://image.pollinations.ai/prompt/{safe}?width=720&height=1280&model={model}&seed={seed}&nologo=true&enhance=true"
            r=requests.get(url, timeout=50)
            if r.status_code==200 and len(r.content)>18000:
                Path(out).write_bytes(r.content)
                log(f"IMG {scene_no} OK")
                return Path(out)
            time.sleep(1.5)
        except Exception as e:
            log(f"IMG {scene_no} err {e}")
            time.sleep(1.5)
    img = Image.new('RGB',(720,1280),(20+scene_no*10,30,50))
    img.save(str(out), "JPEG", quality=90)
    return Path(out)

async def _edge(text, out):
    import edge_tts
    rate = "-12%" if len(text) > 25 else "-8%"
    pitch = "-6Hz" if "الوريث" in text else "-3Hz"
    comm = edge_tts.Communicate(text[:85], "ar-SA-HamedNeural", rate=rate, pitch=pitch)
    await comm.save(str(out))

def gen_voice(text, out):
    try:
        asyncio.run(_edge(text, out))
        return out if Path(out).exists() and Path(out).stat().st_size>800 else None
    except: return None

def gen_video_thill(img, text, voice, out, no, work):
    def esc(t): return str(t).replace("'","").replace('"',"").replace(":"," ").replace("\n"," ")[:70]
    cap = esc(text)
    if no==4:
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.020,2.1))"; dur_target=5.0
        eq="eq=contrast=1.5:saturation=1.8:brightness=0.12,unsharp=5:5:1.2"
    elif no==2:
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.008,1.65))"; dur_target=5.0
        eq="eq=contrast=1.35:saturation=1.5"
    else:
        zoom="if(lte(zoom,1.0),1.0,min(zoom+0.010,1.75))"; dur_target=5.0
        eq="eq=contrast=1.4:saturation=1.5:brightness=0.05"
    vf = f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,zoompan=z='{zoom}':d=1:fps=30:s=720:1280:fps=30,{eq},vignette=angle=PI/4"
    if no==4:
        vf += ",crop=in_w-8:in_h-8:(in_w-out_w)/2+sin(n*0.7)*8:(in_h-out_h)/2+cos(n*0.8)*8"
    vf += f",drawtext=fontfile={FONT}:text='{cap}':fontcolor=white:fontsize=42:box=1:boxcolor=black@0.85:boxborderw=14:x=(w-text_w)/2:y=(h-text_h)/2+180:line_spacing=10"
    if no==1:
        vf += f",drawtext=fontfile={FONT}:text='طردوه... سيعود ملكاً':fontcolor=yellow:fontsize=30:box=1:boxcolor=red@0.85:boxborderw=10:x=(w-text_w)/2:y=h-200"
    try:
        if voice and Path(voice).exists():
            p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(voice)], stdout=subprocess.PIPE, text=True, timeout=5)
            try: vdur=float(p.stdout.strip() or "3.2")
            except: vdur=3.2
            dur=max(4.2, min(vdur+1.2, dur_target))
        else: dur=dur_target
    except: dur=dur_target
    try:
        if voice and Path(voice).exists():
            cmd=["ffmpeg","-y","-loop","1","-i",str(img),"-i",str(voice),"-vf",vf,"-t",str(dur),"-r","30","-map","0:v","-map","1:a","-c:v","libx264","-preset","veryfast","-crf","22","-c:a","aac","-pix_fmt","yuv420p","-shortest","-movflags","+faststart",str(out)]
        else:
            cmd=["ffmpeg","-y","-loop","1","-i",str(img),"-vf",vf,"-t",str(dur),"-r","30","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return out
    except:
        cmd2=["ffmpeg","-y","-loop","1","-i",str(img),"-t","5","-r","30","-c:v","libx264","-preset","ultrafast","-crf","26","-pix_fmt","yuv420p","-movflags","+faststart",str(out)]
        subprocess.run(cmd2, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        return out

def concat_and_ending(videos, out_final, work):
    lf=work / "concat.txt"
    with lf.open("w", encoding="utf-8") as f:
        for v in videos: f.write(f"file '{v}'\n")
    raw=work / "raw_4.mp4"
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf),"-c","copy","-movflags","+faststart",str(raw)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    ending_img = work / "ending.jpg"
    Image.new('RGB',(720,1280),(5,10,25)).save(str(ending_img))
    ending_vid = work / "ending.mp4"
    vf_end = f"scale=720:1280,drawtext=fontfile={FONT}:text='عاد الوريث...':fontcolor=white:fontsize=48:box=1:boxcolor=black@0.7:boxborderw=12:x=(w-text_w)/2:y=(h-text_h)/2-80,drawtext=fontfile={FONT}:text='الجزء 2 - غداً 8 مساءً':fontcolor=#00D4FF:fontsize=42:box=1:boxcolor=black@0.85:boxborderw=12:x=(w-text_w)/2:y=(h-text_h)/2+60"
    subprocess.run(["ffmpeg","-y","-loop","1","-i",str(ending_img),"-vf",vf_end,"-t","2.5","-r","30","-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p","-movflags","+faststart",str(ending_vid)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    lf2=work / "concat2.txt"
    with lf2.open("w", encoding="utf-8") as f:
        f.write(f"file '{raw}'\n")
        f.write(f"file '{ending_vid}'\n")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lf2),"-c","copy","-movflags","+faststart",str(out_final)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return out_final

TELEGRAM_API="https://api.telegram.org/bot"+BOT_TOKEN
def telegram(m,d=None,files=None,timeout=60):
    r=requests.post(TELEGRAM_API+"/"+m,data=d,files=files,timeout=timeout); r.raise_for_status(); return r.json()
def send_text(c,t): return telegram("sendMessage",{"chat_id":c,"text":t})
def send_video(c,p,cap):
    with Path(p).open("rb") as f: return telegram("sendVideo",{"chat_id":c,"caption":cap,"supports_streaming":"true"},{"video":("thill_ep1.mp4",f,"video/mp4")},600)

def process(chat_id):
    work=Path(tempfile.mkdtemp(prefix="v9_thill_"))
    try:
        send_text(chat_id,"🎬 V9 مصنع مونتاج ظل\n📦 4 مشاهد - 20 ثانية\n⏳ تمديد حركة + نهاية الجزء 2")
        results={}
        def job(item):
            gi, (txt, key, en) = item
            no=gi+1
            img=work / f"img_{no}.jpg"
            voice=work / f"voice_{no}.mp3"
            gen_img(en, img, no)
            gen_voice(txt, voice)
            return no, img, voice, txt
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures=[ex.submit(job,(i, data)) for i, data in enumerate(THILL_20S)]
            for f in as_completed(futures):
                no,img,voice,txt=f.result()
                results[no]=(img,voice,txt)
                send_text(chat_id,f"✅ مشهد {no}/4: {txt[:20]}...")
        send_text(chat_id,"🎬 بعمل مونتاج 20ث مع تمديد حركة")
        vids={}
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures=[]
            for no in range(1,5):
                img,voice,txt=results[no]
                out_vid=work / f"vid_{no}.mp4"
                futures.append(ex.submit(gen_video_thill, img, txt, voice, out_vid, no, work))
            for f in as_completed(futures):
                vid=f.result()
                num=int(vid.stem.split("_")[1])
                vids[num]=vid
                send_text(chat_id,f"🎥 مونتاج {len(vids)}/4")
        videos=[vids[i] for i in range(1,5)]
        final=work / "final_20s.mp4"
        concat_and_ending(videos, final, work)
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(final)], stdout=subprocess.PIPE, text=True, timeout=5)
        try: dur=float(p.stdout.strip() or "22.5")
        except: dur=22.5
        send_text(chat_id,f"🔥 مصنع مونتاج ظل جاهز {int(dur)}ث")
        send_video(chat_id, final, f"🔥 ظل - الحلقة 1 (20ث)\n\n{THILL_20S[0][0]}\nالجزء 2 - غداً 8 مساءً 👑\n\n#ظل #ThillLegacy")
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
    if text=="/start":
        send_text(chat,"🎬 V9 مصنع مونتاج ظل\n/ظل = يصنع حلقة 20 ثانية كاملة\n4 مشاهد + راوي غامض + تمديد حركة\nنهاية: الجزء 2 غداً 8 مساءً 👑")
        return
    if text in ["/ظل","/thill","/hook","/مونتاج"]:
        with plock:
            if chat in processing_chats:
                send_text(chat,"⏳ مصنع شغال... انتظر")
                return
            processing_chats.add(chat)
        threading.Thread(target=process, args=(chat,), daemon=True).start()
        return
    if text=="/clear":
        with plock: processing_chats.clear()
        send_text(chat,"✅ تم المسح"); return

@app.get("/")
def home(): return "V9 THILL FACTORY 20S - READY",200
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
