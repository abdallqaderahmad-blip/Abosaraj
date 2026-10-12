import os, asyncio, requests, textwrap
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont
import logging

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
MUSIC_URL = "https://cdn.pixabay.com/download/audio/2022/10/30/audio_fbd2a7f5c7.mp3" # Hold The Door style - حط رابطك

flask_app = Flask(__name__)
tg_app = ApplicationBuilder().token(TOKEN).build()
groq_client = Groq(api_key=GROQ_KEY)

# --- 1. مولد الأفكار بنفس أسلوب hmisc ---
def generate_hikma():
    prompt = """
    أنت كاتب حكم صفحة hmisc الشهيرة.
    اكتب حكمة واحدة قصيرة مؤثرة بنفس أسلوبهم:
    مثال: "لا تصنع هما لم يكتبه الله عليك"

    المطلوب JSON فقط:
    {
      "ar_text_top": "لا تصنع هماً",
      "ar_text_bottom": "لم يكتبه الله عليك",
      "en_text": "Don't create a worry that Allah hasn't written for you",
      "scene_desc_top": "yellow blob character drowning in ocean, hands up, panicked",
      "scene_desc_bottom": "same character sitting calmly underwater, shallow water, peaceful, sand bottom",
      "full_hikma": "لا تنتظر ما في أيدي الناس فاعلم بما سيصلح لك..."
    }
    """
    res = groq_client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role":"user","content":prompt}],
        response_format={"type":"json_object"}
    )
    import json
    return json.loads(res.choices[0].message.content)

# --- 2. توليد الصور (باستخدام HuggingFace Flux) ---
def generate_image_hmisc(prompt_desc):
    # ستايل hmisc الثابت
    base_style = "hand-drawn storybook illustration on beige vintage paper texture, simple yellow blob character, minimalist, soft watercolor, hmisc style, "
    full_prompt = base_style + prompt_desc

    # هنا تستخدم API مجاني - مثال Pollinations
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(full_prompt)}?width=1080&height=1350&nologo=true&model=flux"
    r = requests.get(url)
    img_path = f"/tmp/{hash(prompt_desc)}.jpg"
    with open(img_path, "wb") as f:
        f.write(r.content)
    return img_path

# --- 3. صناعة الفيديو المتحرك ---
def create_reel_video(data):
    # حمل الصور
    top_img_path = generate_image_hmisc(data['scene_desc_top'])
    bottom_img_path = generate_image_hmisc(data['scene_desc_bottom'])

    # حول لصور MoviePy مع حركة Ken Burns (زوم بطيء)
    def make_clip(path, duration=4):
        clip = ImageClip(path).set_duration(duration)
        # حركة زوم ان بطيئة
        clip = clip.resize(lambda t: 1 + 0.05*t)
        return clip

    top_clip = make_clip(top_img_path, 4)
    bottom_clip = make_clip(bottom_img_path, 4)

    # ادمجهم فوق بعض
    final = clips_array([[top_clip],[bottom_clip]]).set_duration(8)

    # اضف النص العربي والانجليزي
    # (للبساطة هنضيفه كـ TextClip - لازم يكون عندك خط عربي)
    # لو ما عندك خط، البوت يرسل الفيديو بدون نص والموسيقى فقط

    # اضف الموسيقى
    try:
        # حمل الموسيقى
        music_path = "/tmp/music.mp3"
        if not os.path.exists(music_path):
            # استخدم ملف محلي تحطه في المشروع اسمه bg.mp3
            if os.path.exists("bg.mp3"):
                music_path = "bg.mp3"

        if os.path.exists(music_path):
            audio = AudioFileClip(music_path).subclip(0,8).volumex(0.3)
            final = final.set_audio(audio)
    except Exception as e:
        print(f"Music error: {e}")

    output_path = "/tmp/hmisc_reel.mp4"
    final.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")
    return output_path, data

# --- أوامر التيليجرام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 بوت Hmisc Reel Factory جاهز\n\n"
        "/hikma - يصنع فيديو حكمة جديد بنفس ستايل الصفحة\n"
        "/idea - يجيب فكرة فقط بدون فيديو"
    )

async def hikma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ بفكر في حكمة... وبصنع الصور... (ياخذ 30-40 ثانية)")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, generate_hikma)
        await update.message.reply_text(f"💡 الفكرة:\n{data['full_hikma']}\n\nEN: {data['en_text']}\n\nجاري تحويلها لفيديو متحرك...")

        video_path, final_data = await loop.run_in_executor(None, lambda: create_reel_video(data))

        caption = f"{final_data['ar_text_top']}\n{final_data['ar_text_bottom']}\n\n{final_data['en_text']}\n\n#hmisc #حكمة"
        await update.message.reply_video(video=open(video_path, 'rb'), caption=caption)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text(f"❌ خطأ: {e}")

async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = generate_hikma()
    await update.message.reply_text(f"AR: {data['full_hikma']}\n\nEN: {data['en_text']}\n\nScene: {data['scene_desc_top']} / {data['scene_desc_bottom']}")

tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CommandHandler("hikma", hikma_cmd))
tg_app.add_handler(CommandHandler("idea", idea_cmd))

@flask_app.route('/')
def home(): return "Hmisc Factory Live ✅"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), tg_app.bot)
        asyncio.run(tg_app.process_update(update))
    except Exception as e: print(e)
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    url = f"{request.host_url}{TOKEN}".rstrip('/')
    asyncio.run(tg_app.bot.set_webhook(url))
    return f"Webhook set {url}"

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000)))
