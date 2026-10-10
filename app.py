import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
BOT_TOKEN=os.getenv("BOT_TOKEN"); PIXABAY_KEY=os.getenv("PIXABAY_KEY") or os.getenv("PEXELS_KEY"); WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)
print("===== V50 ULTRA LIGHT FFmpeg ONLY =====")
def tg_send(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass
def dl(url,path):
    try:
        r=requests.get(url,timeout=20)
        if r.status_code==200 and len(r.content)>40000:
            open(path,"wb").write(r.content); return True
    except: pass
    return False
def voice(t,o):
    try:
        from gtts import gTTS; gTTS(text=t,lang='ar',slow=False).save(o); return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","3","-q:a","9","-acodec","libmp3lame",o],timeout=10); return True
def build(cid, parts):
    try:
        for f in glob.glob("s*")+glob.glob("a*")+glob.glob("p*")+["FINAL.mp4","list.txt"]:
            try: os.remove(f)
            except: pass
        qs=["poor man","rich woman office","billionaire luxury"]
        backs=["https://cdn.pixabay.com/video/2020/12/13/59398-490696104_small.mp4","https://cdn.pixabay.com/video/2019/11/03/28976-370981083_small.mp4"]
        vids=[]
        for i in range(3):
            v=f"s_{i}.mp4"; a=f"a_{i}.mp3"; p=f"p_{i}.mp4"
            tg_send(cid,f"🎬 {i+1}/3 سريع")
            ok=False
            try:
                j=requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={qs[i]}&per_page=5",timeout=8).json()
                if j.get("hits"):
                    url=random.choice(j["hits"])["videos"]["small"]["url"]
                    if dl(url,v): ok=True
            except: pass
            if not ok:
                for b in backs:
                    if dl(b,v): ok=True; break
            if not ok: raise Exception("فشل تحميل")
            tg_send(cid,f"🎙️ {i+1}/3")
            voice(parts[i],a)
            tg_send(cid,f"✂️ {i+1}/3 FFmpeg")
            # FFmpeg فقط - خفيف وسريع
            dur=[3,7,9][i]
            # قص + مقاس 720x1280 + دمج صوت
            subprocess.run(f'ffmpeg -y -i {v} -i {a} -vf "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,eq=contrast=1.1:saturation=1.2" -t {dur} -c:v libx264 -preset ultrafast -b:v 800k -c:a aac -shortest {p}', shell=True, check=True, timeout=60)
            vids.append(p)
            tg_send(cid,f"✅ {i+1}/3 جاهز")
        tg_send(cid,"🔗 دمج")
        with open("list.txt","w") as f:
            for x in vids: f.write(f"file '{x}'\n")
        subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i","list.txt","-c","copy","FINAL.mp4"],check=True,timeout=20)
        with open("FINAL.mp4","rb") as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":"🏆 V50 ULTRA LIGHT\n⚡ FFmpeg Only\n✅ سريع"}, files={"video":f}, timeout=90)
        tg_send(cid,"🏆 V50 جاهز ✅")
    except Exception as e:
        tg_send(cid,f"❌ {e}\n{traceback.format_exc()[:700]}")
async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8: raw="كان مجرد سواق فقير الكل بضحك عليه|البنت الغنية اختارتو قدام الكل|ما بيعرفو انه ملياردير مخفي واشترى الشركة"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V50 ULTRA LIGHT ⚡")
    threading.Thread(target=build, args=(update.effective_chat.id, parts), daemon=True).start()
application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel)); application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel)); application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))
loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(application.initialize()); loop.run_until_complete(application.start())
@web_app.route('/')
def home(): return "V50 LIGHT"
@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try: data=request.get_json(force=True); update=Update.de_json(data, application.bot); loop.run_until_complete(application.process_update(update))
    except: pass
    return 'ok'
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if WEBHOOK_URL:
        try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=8)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
