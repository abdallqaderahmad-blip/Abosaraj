import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN=os.getenv("BOT_TOKEN")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)
print("===== V61.1 FIXED DOWNLOAD =====")

def tg(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def dl(url, out):
    try:
        r=requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code==200 and len(r.content)>40000:
            open(out,'wb').write(r.content)
            return True
    except: pass
    try:
        subprocess.run(f'ffmpeg -y -i "{url}" -t 10 -c copy {out}', shell=True, timeout=20)
        if os.path.exists(out) and os.path.getsize(out)>40000:
            return True
    except: pass
    return False

def fetch(q):
    vids={
        "sad": ["https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4","https://cdn.pixabay.com/video/2020/05/25/40128-424930862_small.mp4"],
        "woman": ["https://cdn.pixabay.com/video/2020/12/14/59530-491788045_small.mp4","https://cdn.pixabay.com/video/2021/08/04/84388-580045401_small.mp4"],
        "success": ["https://cdn.pixabay.com/video/2019/10/02/27569-364292065_small.mp4","https://cdn.pixabay.com/video/2020/05/25/40130-424931032_small.mp4"],
        "child": ["https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4"],
    }
    if "woman" in q or "rich" in q: return random.choice(vids["woman"])
    if "success" in q: return random.choice(vids["success"])
    if "child" in q: return random.choice(vids["child"])
    return random.choice(vids["sad"])

async def voice(text, out):
    t=text.replace(" كان "," كان... ")
    try:
        import edge_tts
        c=edge_tts.Communicate(t, "ar-SA-ZariyahNeural", rate="+10%")
        await c.save(out)
        if os.path.getsize(out)>2000: return True
    except: pass
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar').save(out)
        return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc","-t","4",out], timeout=8)
        return True

def build(cid, parts):
    try:
        for f in glob.glob("*.mp4")+glob.glob("*.mp3"):
            try: os.remove(f)
            except: pass
        full="... ".join(parts)
        tg(cid,f"🧠 {full[:40]}...")
        q="sad"
        if any(w in full for w in ["غنية","بنت","جميلة","سيدة"]): q="beautiful woman"
        if any(w in full for w in ["ملياردير","شركة","نجح","طبيب","أصبح"]): q="successful man"
        if any(w in full for w in ["طفل"]): q="sad child"
        tg(cid,f"🎬 {q}")
        for _ in range(3):
            if dl(fetch(q), "video.mp4"): break
        if not os.path.exists("video.mp4"):
            dl("https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4", "video.mp4")
        tg(cid,"🎙️ صوت بنت")
        loop2=asyncio.new_event_loop()
        asyncio.set_event_loop(loop2)
        loop2.run_until_complete(voice(full, "voice.mp3"))
        loop2.close()
        subprocess.run('ffmpeg -y -i video.mp4 -i voice.mp3 -t 15 -c:v libx264 -preset ultrafast -crf 28 -c:a aac -shortest FINAL.mp4', shell=True, check=True, timeout=60)
        with open("FINAL.mp4","rb") as v:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":f"🏆 V61.1 شغال\n📝 {full[:100]}"}, files={"video":v}, timeout=120)
        tg(cid,"✅ شغال")
    except Exception as e:
        tg(cid,f"❌ {e}\n{traceback.format_exc()[:600]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8: raw="كان طفل فقير يبيع مناديل|ربته سيدة عجوز|أصبح طبيبا"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V61.1 FIXED DOWNLOAD\nأرسل: /ظل جزء1|جزء2|جزء3")
    threading.Thread(target=build, args=(update.effective_chat.id, parts), daemon=True).start()

application=Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^/ظل'), zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))
loop=asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V61.1 FIXED"
@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data=request.get_json(force=True)
        upd=Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(upd))
    except: pass
    return 'ok'

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    if WEBHOOK_URL:
        try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/{BOT_TOKEN}", timeout=8)
        except: pass
    web_app.run(host='0.0.0.0', port=port)
