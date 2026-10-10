import os, asyncio, requests, random
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

async def make_voice(text, output="voice.mp3"):
    voice = "ar-SA-ZaydNeural"
    communicate = edge_tts.Communicate(text, voice, rate="-15%", pitch="-8Hz", volume="+10%")
    await communicate.save(output)
    return output

def download_pixabay(keyword, filename):
    try:
        url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={keyword}&orientation=vertical&per_page=10"
        r = requests.get(url, timeout=20)
        data = r.json()
        if data.get("hits"):
            video = random.choice(data["hits"])
            v_url = video["videos"]["medium"]["url"]
            with requests.get(v_url, stream=True, timeout=40) as resp:
                with open(filename, "wb") as f:
                    for chunk in resp.iter_content(1024*1024): f.write(chunk)
            return filename
    except: pass
    v_url = "https://cdn.pixabay.com/video/2020/07/30/45549-442790323_large.mp4"
    with requests.get(v_url, stream=True, timeout=40) as resp:
        with open(filename, "wb") as f:
            for chunk in resp.iter_content(1024*1024): f.write(chunk)
    return filename

def build_final():
    clips = []
    for i, sc in enumerate(SCENES):
        v_file = f"scene_{i}.mp4"; a_file = f"voice_{i}.mp3"
        download_pixabay(sc["keyword"], v_file)
        asyncio.run(make_voice(sc["text"], a_file))
        vc = VideoFileClip(v_file).subclip(0,5).resize((1080,1920)).set_duration(5)
        vc = vc.fx(vfx.speedx, 0.85).resize(lambda t: 1 + 0.08*t).fx(vfx.colorx, 0.85)
        ac = AudioFileClip(a_file)
        txt = TextClip(sc["text"], fontsize=55, color='white', font='DejaVu-Sans-Bold', method='caption', size=(900, None), stroke_color='black', stroke_width=2)
        txt = txt.set_duration(5).set_position(('center', 1400))
        vc = CompositeVideoClip([vc, txt]).set_audio(ac)
        clips.append(vc)
    final_video = concatenate_videoclips(clips, method="compose")
    final_video.write_videofile("V16_FINAL.mp4", fps=24, codec="libx264", audio_codec="aac", preset="ultrafast")
    return "V16_FINAL.mp4"

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 V16 ببني الفيديو...")
    try:
        path = build_final()
        await update.message.reply_video(video=open(path,"rb"))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(MessageHandler(filters.Regex(r'^(ظل|/ظل)$'), zel))

# تهيئة مرة وحدة
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V16 Webhook Active"

@web_app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        loop.run_until_complete(application.process_update(update))
    except Exception as e:
        print(f"Webhook error: {e}")
    return 'ok'

def run():
    port = int(os.environ.get("PORT", 10000))
    if WEBHOOK_URL:
        url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        try:
            requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={url}")
            print(f"Webhook set: {url}")
        except: pass
    web_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    run()
