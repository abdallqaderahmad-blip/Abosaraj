import os, asyncio, requests, random, threading, re
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from moviepy.editor import *
import edge_tts
from PIL import Image
Image.ANTIALIAS = Image.LANCZOS

BOT_TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
web_app = Flask(__name__)

# ألوان سينمائية
CINEMATIC_FILTER = {"brightness": 0.85, "contrast": 1.1}

# كلمات مفتاحية ذكية حسب النص
def get_keyword_for_text(text):
    text = text.lower()
    if any(w in text for w in ["ليل", "عتمة", "ظلام", "وحيدة"]): return "dark night lonely girl"
    if any(w in text for w in ["مطر", "دموع", "بكاء"]): return "rain window sad"
    if any(w in text for w in ["شارع", "طريق", "يمضي"]): return "lonely street night"
    if any(w in text for w in ["أم", "دعاء", "نور", "أمل"]): return "sunlight prayer hope"
    if any(w in text for w in ["حب", "قلب"]): return "love couple sunset"
    return "cinematic sad girl"

async def make_mysterious_voice(text, output):
    try:
        # راوي غامض - Zayd مع إعدادات غامضة
        comm = edge_tts.Communicate(text, "ar-SA-ZaydNeural", rate="-20%", pitch="-15Hz", volume="+15%")
        await comm.save(output)
        return output
    except:
        from gtts import gTTS
        tts = gTTS(text=text, lang='ar', slow=True)
        tts.save(output)
        return output

def download_clean_video(keyword, filename):
    # Pixabay فيديوهات نظيفة بدون علامة
    try:
        if PIXABAY_KEY:
            url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={keyword}&orientation=vertical&per_page=20&editors_choice=true"
            r = requests.get(url, timeout=15)
            data = r.json()
            if data.get("hits"):
                # اختار فيديو نظيف بدون ناس بعلامات
                v = random.choice(data["hits"])
                v_url = v["videos"]["medium"]["url"]
                with requests.get(v_url, stream=True, timeout=40) as resp:
                    with open(filename, "wb") as f:
                        for chunk in resp.iter_content(1024*512): f.write(chunk)
                return filename
    except Exception as e:
        print(f"pixabay error {e}")
    # fallback نظيف 100%
    fallbacks = [
        "https://cdn.pixabay.com/video/2020/07/30/45549-442790323_large.mp4",
        "https://cdn.pixabay.com/video/2023/01/144197-786717912_large.mp4"
    ]
    v_url = random.choice(fallbacks)
    with requests.get(v_url, stream=True, timeout=40) as resp:
        with open(filename, "wb") as f:
            for chunk in resp.iter_content(1024*512): f.write(chunk)
    return filename

def build_cinematic_video(script_lines, chat_id, bot):
    try:
        clips = []
        for i, line in enumerate(script_lines):
            line = line.strip()
            if not line: continue
            v_file = f"scene_{i}.mp4"; a_file = f"voice_{i}.mp3"
            
            keyword = get_keyword_for_text(line)
            download_clean_video(keyword, v_file)
            asyncio.run(make_mysterious_voice(line, a_file))
            
            # فيديو سينمائي بدون كلام على الشاشة
            vc = VideoFileClip(v_file).subclip(0,6).resize((1080,1920)).set_duration(6)
            # ألوان سينمائية غامقة + زوم بطيء
            vc = vc.fx(vfx.speedx, 0.80).fx(vfx.colorx, 0.85).fx(vfx.lum_contrast, lum= -10, contrast=15)
            vc = vc.resize(lambda t: 1 + 0.06*t) # كين بيرنز
            
            ac = AudioFileClip(a_file)
            vc = vc.set_audio(ac)
            clips.append(vc)
        
        final = concatenate_videoclips(clips, method="compose")
        
        # تأثير صوتي خلفي خفيف غامض
        try:
            # موسيقى غامضة حزينة من Pixabay بدون حقوق
            music_url = "https://cdn.pixabay.com/download/audio/2022/10/30/audio_8ef11cc012.mp3"
            r = requests.get(music_url, timeout=15); open("bg.mp3","wb").write(r.content)
            bg = AudioFileClip("bg.mp3").subclip(0, final.duration).volumex(0.08).audio_fadein(1).audio_fadeout(1)
            final = final.set_audio(CompositeAudioClip([bg, final.audio]))
        except: pass
        
        final.write_videofile("ZEL_FINAL.mp4", fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=None, threads=2)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_video(chat_id=chat_id, video=open("ZEL_FINAL.mp4","rb"), caption="🎬 ظل - نسخة نظيفة سينمائية"))
        loop.close()
    except Exception as e:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.send_message(chat_id=chat_id, text=f"❌ خطأ: {e}"))
        loop.close()

async def zel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # اقرأ النص اللي بعته المستخدم بعد /zel
    full_text = update.message.text or ""
    # شيل /zel أو ظل
    script = re.sub(r'^/(zel|ظل)\s*', '', full_text).strip()
    script = re.sub(r'^(ظل)\s*', '', script).strip()
    
    if not script or len(script) < 5:
        # افتراضي إذا ما بعت نص
        lines = [
            "جلست ظل وحيدة في عتمة لا يراها أحد",
            "كانت تبحث عن ظل يحميها من برد الوحدة",
            "في الشارع الجميع يمضي ولا أحد يلتفت",
            "ثم جاء صوت أمها بدعاء يشبه النور"
        ]
    else:
        # قسم النص لجمل - كل جملة مشهد
        lines = re.split(r'[.!؟\n]+', script)
        lines = [l.strip() for l in lines if len(l.strip()) > 3][:6] # max 6 مشاهد
    
    await update.message.reply_text(f"🎬 فهمت القصة - {len(lines)} مشاهد\nببني فيديو نظيف بدون كلام على الشاشة + راوي غامض... ⏳")
    threading.Thread(target=build_cinematic_video, args=(lines, chat_id, context.bot), daemon=True).start()

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("zel", zel))
application.add_handler(CommandHandler("start", zel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, zel))

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())
loop.run_until_complete(application.start())

@web_app.route('/')
def home(): return "V19 Cinematic Clean"

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
