import os, asyncio, requests, random, threading
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import *
import edge_tts

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

SCENES = [
    {"keyword": "sad girl dark", "text": "جلست ظل وحيدة... في عتمة لا يراها أحد"},
    {"keyword": "rain window", "text": "كانت تبحث عن ظل... يحميها من برد الوحدة"},
    {"keyword": "lonely street", "text": "في الشارع... الجميع يمضي ولا أحد يلتفت"},
    {"keyword": "prayer light", "text": "ثم جاء صوت أمها... بدعاء يشبه النور"}
]

async def make_voice(text, output):
    voice = "ar-SA-ZaydNeural"
    comm = edge_tts.Communicate(text, voice, rate="-15%", pitch="-8Hz", volume="+10%")
    await comm.save(output)
    return output

def download_video(keyword, filename):
    try:
        if PIXABAY_KEY:
            url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={keyword}&orientation=vertical&per_page=5"
            r = requests.get(url, timeout=15)
            data = r.json()
            if data.get("hits"):
                v_url = random.choice(data["hits"])["videos"]["medium"]["url"]
                with requests.get(v_url, stream=True, timeout=30) as resp:
                    with open(filename, "wb") as f:
                        for chunk in resp.iter_content(1024*512): f.write(chunk)
                return filename
    except: pass
    fb = "https://cdn.pixabay.com/video/2020/07/30/45549-442790323_large.mp4"
    with requests.get(fb, stream=True, timeout=30) as resp:
        with open(filename, "wb") as f:
            for chunk in resp.iter_content(1024*512): f.write(chunk)
    return filename

def build_video(chat_id, bot):
    try:
        clips = []
        for i, sc in enumerate(SCENES):
            v_file = f"scene_{i}.mp4"; a_file = f"voice_{i}.mp3"
            download_video(sc["keyword"], v_file)
            asyncio.run(make_voice(sc["text"], a_file))
            vc = VideoFileClip(v_file).subclip(0,5).resize((1080,1920)).set_duration(5)
            vc = vc.fx(vfx.speedx, 0.85).fx(vfx.colorx, 0.85)
            ac = AudioFileClip(a_file)
            txt = TextClip(sc["text"], fontsize=50, color='white', font='DejaVu-Sans-Bold', method='caption', size=(900, None), stroke_color='black', stroke_width=2)
            txt = txt.set_duration(5).set_position(('center', 1400))
            vc = CompositeVideoClip([vc, txt]).set_audio(ac)
            clips.append(vc)
        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile("FINAL.mp4", fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=None)
        
        # ابعث الفيديو
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_video(chat_id=chat_id, video=open("FINAL.mp4","rb"), caption="🎬 ظل V17"))
        loop.close()
    except Exception as e:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_message(chat_id=chat_id, text=f"❌ خطأ: {e}"))
        loop.close()

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("🎬 ببلش بناء الفيديو... 2-3 دقايق ⏳")
    # شغل بالخلفية عشان Webhook ما يعمل timeout
    threading.Thread(target=build_video, args=(chat_id, context.bot), daemon=True).start()

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.Regex(r'^(ظل|/ظل)$'), zel))

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V17 Working"

@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(update))
    except Exception as e:
        print(f"Webhook error: {e}")
    return 'ok'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    if WEBHOOK_URL:
        url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={url}")
        print(f"Webhook: {url}")
    web_app.run(host='0.0.0.0', port=port)
