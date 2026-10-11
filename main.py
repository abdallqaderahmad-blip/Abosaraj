import os, re, json, time, uuid, logging, threading, subprocess, tempfile
from pathlib import Path
from urllib.parse import quote
import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
POLL_IMG = "https://image.pollinations.ai/prompt/"
PORT = int(os.getenv("PORT", "10000"))
FPS, DUR, W, H = 24, 6.5, 720, 1280

DATA = Path(tempfile.gettempdir()) / "zil"
DATA.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ZIL")
flask_app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent":"ZIL-V84"})
ACTIVE = 0

def sh(cmd, t=300):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=t)
    if r.returncode!=0:
        raise RuntimeError((r.stderr or r.stdout)[-2000:])
    return r

def tg_msg(cid, txt):
    try: HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":cid,"text":txt[:4000]}, timeout=30)
    except: pass

def tg_vid(cid, path, cap):
    try:
        with open(path,"rb") as f:
            HTTP.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":cap[:1000]}, files={"video":(Path(path).name,f,"video/mp4")}, timeout=600)
    except Exception as e: log.error(e)

def extract_json(s):
    s = re.sub(r"^```.*?\n","",s.strip(),flags=re.S); s=re.sub(r"\n```$","",s.strip())
    try: return json.loads(s)
    except: return json.loads(s[s.find("{"):s.rfind("}")+1])

def groq(msgs):
    h={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}
    p={"model":GROQ_MODEL,"messages":msgs,"temperature":0.9,"max_tokens":3000,"response_format":{"type":"json_object"}}
    r=HTTP.post(GROQ_URL,headers=h,json=p,timeout=120); r.raise_for_status()
    return extract_json(r.json()["choices"][0]["message"]["content"])

def ideas(n=3):
    sys_msg='Return JSON {"ideas":[{"text_ar":"حكمة عربية قصيرة","top":"top illusion English","bottom":"bottom reality English","emotion":"anxiety"}]} Style yellow #FFC83D parchment #F5E6C8 split.'
    try:
        d=groq([{"role":"system","content":sys_msg},{"role":"user","content":f"make {n}"}])
        if d.get("ideas"): return d["ideas"][:n]
    except Exception as e: log.warning(e)
    return [{"text_ar":"لا تصنع هموماً\nلم يكتبها الله عليك","top":"yellow character drowning deep ocean panicking illusion","bottom":"same yellow character sitting shallow water to knees calm reality","emotion":"anxiety"}]*n

def gen_img(prompt,dest,seed):
    url=POLL_IMG+quote(prompt,safe="")
    r=HTTP.get(url,params={"width":768,"height":1365,"seed":seed,"nologo":"true","model":"flux","enhance":"true"},timeout=180); r.raise_for_status()
    Path(dest).write_bytes(r.content)

def make_vis(idea,wd,i):
    raw=wd/f"raw_{i}.jpg"; cl=wd/f"cl_{i}.png"; vid=wd/f"vis_{i}.mp4"
    prompt=f"Split screen top bottom, top:{idea['top']}, bottom:{idea['bottom']}, watercolor parchment #F5E6C8 yellow character #FFC83D no text 9:16"
    if not cl.exists():
        if not raw.exists(): gen_img(prompt,raw,1000+i*137)
        sh(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-vf","crop=iw:ih-90:0:0,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",str(cl)],60)
    if not vid.exists():
        fr=int(DUR*FPS); vf=f"zoompan=z='if(lte(zoom,1.0),1.0,min(zoom+0.0003,1.04))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},trim=end_frame={fr},setpts=PTS-STARTPTS,format=yuv420p"
        sh(["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-framerate",str(FPS),"-i",str(cl),"-vf",vf,"-t",str(DUR),"-an","-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-r",str(FPS),str(vid)],120)
    return vid

def add_txt(src,txt_ar,dst,wd,i):
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper; from bidi.algorithm import get_display
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov)
    try: font=ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",40)
    except: font=ImageFont.load_default()
    def fix(t):
        try: return get_display(arabic_reshaper.reshape(t.strip()))
        except: return t
    y=85
    for line in txt_ar.split("\n"):
        if not line.strip(): continue
        tx=fix(line)
        bb=d.textbbox((0,0),tx,font=font); ww=bb[2]-bb[0]
        d.text(((W-ww)//2+2,y+2),tx,font=font,fill=(245,230,200,180)); d.text(((W-ww)//2,y),tx,font=font,fill=(28,18,8,255)); y+=56
    png=wd/f"txt_{i}.png"; ov.save(png)
    sh(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),"-loop","1","-framerate",str(FPS),"-i",str(png),"-filter_complex","[0:v][1:v]overlay=0:0:shortest=1[v]","-map","[v]","-t",str(DUR),"-an","-c:v","libx264","-preset","ultrafast","-crf","23","-pix_fmt","yuv420p","-r",str(FPS),str(dst)],60)
    return dst

def add_mus(src,dst,emo):
    freq=110 if emo=="anxiety" else 196
    mu=src.parent/f"m_{src.stem}.m4a"
    sh(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","lavfi","-i",f"sine=frequency={freq}:duration={DUR}","-af",f"volume=0.14,lowpass=f=900,afade=t=in:d=0.6,afade=t=out:st={DUR-1}:d=1","-ar","44100","-ac","2","-c:a","aac","-b:a","64k",str(mu)],30)
    sh(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(src),"-i",str(mu),"-c:v","copy","-map","0:v:0","-map","1:a:0","-shortest","-movflags","+faststart",str(dst)],30)
    return dst

def worker(jid,cid,ideas):
    global ACTIVE
    try:
        tg_msg(cid,f"✨ hmisc {len(ideas)} فيديو 6.5ث بدون علامة مائية")
        for i,idea in enumerate(ideas):
            wd=DATA/f"{jid}_{i}"; wd.mkdir(parents=True,exist_ok=True)
            tg_msg(cid,f"🎨 {i+1}/{len(ideas)} {idea['text_ar'][:30]}")
            vis=make_vis(idea,wd,i); cap=wd/f"cap_{i}.mp4"; add_txt(vis,idea["text_ar"],cap,wd,i)
            final=wd/f"HMISC_{i+1}.mp4"; add_mus(cap,final,idea.get("emotion","anxiety"))
            tg_vid(cid,final,idea["text_ar"])
        tg_msg(cid,"🎉 اكتمل")
    except Exception as e:
        log.exception(e); tg_msg(cid,f"❌ {e}")
    finally: ACTIVE=max(0,ACTIVE-1)

async def cmd_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 ZIL V84\n/hmisc 1\n/hmisc 5")

async def cmd_hmisc(update:Update, context:ContextTypes.DEFAULT_TYPE):
    global ACTIVE
    if ACTIVE>=1:
        await update.message.reply_text("⏳ في عمل شغال"); return
    n=1
    if context.args:
        try: n=int(context.args[0])
        except: pass
    n=max(1,min(10,n)); jid=uuid.uuid4().hex[:8]; ACTIVE+=1
    await update.message.reply_text(f"✨ توليد {n} أفكار")
    def run(): worker(jid,update.effective_chat.id,ideas(n))
    threading.Thread(target=run,daemon=True).start()

@flask_app.route("/")
def home(): return jsonify({"ok":True,"v":"84","active":ACTIVE})

@flask_app.route("/health")
def hl(): return jsonify({"ok":True,"bot":bool(BOT_TOKEN),"groq":bool(GROQ_API_KEY)})

def run_flask(): flask_app.run(host="0.0.0.0",port=PORT,use_reloader=False,threaded=True)

def main():
    threading.Thread(target=run_flask,daemon=True).start(); time.sleep(2)
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing");
        while True: time.sleep(3600)
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start)); app.add_handler(CommandHandler("hmisc",cmd_hmisc))
    log.info(f"Live on {PORT}"); app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
