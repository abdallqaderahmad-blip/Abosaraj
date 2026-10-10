import os, requests, random, threading, re, traceback, subprocess, glob, asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN=os.getenv("BOT_TOKEN")
WEBHOOK_URL=os.getenv("RENDER_EXTERNAL_URL")
web_app=Flask(__name__)
print("===== V61.2 DEBUG 254 =====")

def tg(c,t):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id":c,"text":t}, timeout=10)
    except: pass

def dl(url, out):
    try:
        r=requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code==200:
            open(out,'wb').write(r.content)
            sz=os.path.getsize(out)
            print(f"DL {url[:50]} size={sz}")
            if sz>40000:
                return True
    except Exception as e:
        print(f"DL err {e}")
    return False

async def voice(text, out):
    t=text.replace(" كان "," كان... ")
    try:
        import edge_tts
        c=edge_tts.Communicate(t, "ar-SA-ZariyahNeural", rate="+10%")
        await c.save(out)
        if os.path.exists(out) and os.path.getsize(out)>2000:
            print(f"VOICE OK size={os.path.getsize(out)}")
            return True
    except Exception as e:
        print(f"VOICE edge err {e}")
    try:
        from gtts import gTTS
        gTTS(text=t, lang='ar').save(out)
        print(f"VOICE gtts OK")
        return True
    except Exception as e:
        print(f"VOICE gtts err {e}")
    # fallback صمت
    subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","5",out], timeout=10)
    return True

def build(cid, parts):
    try:
        for f in glob.glob("*.mp4")+glob.glob("*.mp3"):
            try: os.remove(f)
            except: pass

        full="... ".join(parts)
        tg(cid,f"🧠 {full[:40]}...")

        # 1. جرب تحميل
        ok=False
        for url in [
            "https://cdn.pixabay.com/video/2020/06/04/41067-427219623_small.mp4",
            "https://cdn.pixabay.com/video/2022/10/23/136272-763624442_small.mp4",
        ]:
            tg(cid,f"⬇️ تحميل {url[-20:]}")
            if dl(url, "video.mp4"):
                ok=True
                break

        if not ok or not os.path.exists("video.mp4"):
            tg(cid,"⚠️ التحميل فشل - بعمل فيديو ملون بديل")
            # فيديو ملون بسيط - مستحيل يفشل
            subprocess.run("ffmpeg -y -f lavfi -i color=c=0x222222:s=720x1280:d=15:r=25 -vf format=yuv420p video.mp4", shell=True, timeout=20)

        # 2. صوت
        tg(cid,"🎙️ صوت بنت")
        loop2=asyncio.new_event_loop()
        asyncio.set_event_loop(loop2)
        loop2.run_until_complete(voice(full, "voice.mp3"))
        loop2.close()

        # تحقق من الملفات
        v_sz=os.path.getsize("video.mp4") if os.path.exists("video.mp4") else 0
        a_sz=os.path.getsize("voice.mp3") if os.path.exists("voice.mp3") else 0
        tg(cid,f"📁 video={v_sz} bytes, audio={a_sz} bytes")

        if v_sz<1000:
            tg(cid,"❌ video.mp4 فاضي - بعمل بديل")
            subprocess.run("ffmpeg -y -f lavfi -i color=c=0x222222:s=720x1280:d=15:r=25 -vf format=yuv420p video.mp4", shell=True, timeout=20)
        if a_sz<1000:
            tg(cid,"❌ voice.mp3 فاضي - بعمل صمت")
            subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","5","voice.mp3"], timeout=10)

        # 3. دمج - بدون crf معقد
        tg(cid,"🔗 دمج بسيط")
        cmd='ffmpeg -y -i video.mp4 -i voice.mp3 -c:v libx264 -preset ultrafast -c:a aac -shortest FINAL.mp4'
        print(f"RUN: {cmd}")
        result=subprocess.run(cmd, shell=True, timeout=60, capture_output=True, text=True)
        print(f"FFMPEG OUT: {result.stdout[-500:]}")
        print(f"FFMPEG ERR: {result.stderr[-1000:]}")

        if result.returncode!=0:
            tg(cid,f"❌ ffmpeg فشل:\n{result.stderr[-800:]}")
            # محاولة أخيرة بدون re-encode
            subprocess.run('ffmpeg -y -i video.mp4 -i voice.mp3 -c copy -shortest FINAL.mp4', shell=True, timeout=60)

        if not os.path.exists("FINAL.mp4"):
            tg(cid,"❌ FINAL.mp4 ما انعمل - بجرب طريقة أخيرة")
            subprocess.run('ffmpeg -y -f lavfi -i color=c=black:s=720x1280:d=10 -i voice.mp3 -c:v libx264 -c:a aac -shortest FINAL.mp4', shell=True, timeout=60)

        with open("FINAL.mp4","rb") as v:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
                data={"chat_id":cid,"caption":f"🏆 V61.2 DEBUG\n📝 {full[:100]}\nvideo={v_sz} audio={a_sz}"},
                files={"video":v}, timeout=120)
        tg(cid,"✅ شغال")

    except Exception as e:
        tg(cid,f"❌ {e}\n{traceback.format_exc()[:1000]}")

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw=re.sub(r'^/(zel|ظل)\s*','',update.message.text or "").strip()
    if len(raw)<8: raw="كان طفل فقير يبيع مناديل|ربته سيدة عجوز|أصبح طبيبا"
    parts=[p.strip() for p in raw.split("|") if p.strip()][:3]
    while len(parts)<3: parts.append(parts[-1])
    await update.message.reply_text("🏆 V61.2 DEBUG 254\nأرسل: /ظل جزء1|جزء2|جزء3")
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
def home(): return "V61.2 DEBUG"
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
