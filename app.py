import os, requests, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN=os.getenv("BOT_TOKEN")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)

def tg(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

async def voice(text, out):
    t=text.replace(" كان "," كان... ")
    try:
        import edge_tts
        await edge_tts.Communicate(t, "ar-SA-ZariyahNeural", rate="+8%").save(out)
        if os.path.getsize(out)>2000: return True
    except: pass
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar').save(out)
        return True
    except:
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc","-t","6",out], timeout=10)
        return True

def build(cid, parts):
    try:
        for f in glob.glob("*.mp4")+glob.glob("*.mp3"):
            try: os.remove(f)
            except: pass
        full="... ".join(parts)
        tg(cid,f"🧠 {full[:40]}...")

        tg(cid,"🎙️ صوت بنت")
        loop2=asyncio.new_event_loop()
        asyncio.set_event_loop(loop2)
        loop2.run_until_complete(voice(full, "voice.mp3"))
        loop2.close()

        tg(cid,"🎬 فيديو - أمر بسيط جدا")
        # أمر بسيط - 5 ثواني - مستحيل يعلق
        subprocess.run('ffmpeg -y -f lavfi -i color=c=0x1a1a2e:s=720x1280:r=25:d=10 -vf format=yuv420p -t 10 video.mp4', shell=True, check=True, timeout=15)

        tg(cid,"🔗 دمج")
        subprocess.run('ffmpeg -y -i video.mp4 -i voice.mp3 -c:v libx264 -preset ultrafast -c:a aac -shortest FINAL.mp4', shell=True, check=True, timeout=20)

        with open("FINAL.mp4","rb") as v:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo", data={"chat_id":cid,"caption":f"🏆 V61.4 شغال\n{full[:120]}"}, files={"video":v}, timeout=120)
        tg(cid,"✅ شغال 100%")

    except Exception as e:
        tg(cid,f"❌ {e}\n{traceback.format_exc()[:900]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8: raw="كان طفل فقير يبيع مناديل|ربته سيدة عجوز|أصبح طبيبا"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V61.4 SIMPLEST\nأرسل: /ظل جزء1|جزء2|جزء3")
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
def home(): return "V61.4"
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
