import os
import asyncio
import requests
import json
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

# --- إعدادات ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN:
    print("❌ BOT_TOKEN مش موجود في Environment!")

flask_app = Flask(__name__)
tg_app = ApplicationBuilder().token(TOKEN).build() if TOKEN else None
groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# --- مولد الحكم hmisc ---
def generate_hikma():
    if not groq_client:
        return {
            "ar_top": "لا تصنع هماً",
            "ar_bottom": "لم يكتبه الله عليك",
            "en": "Don't create a worry Allah hasn't written for you",
            "scene_top": "yellow blob character drowning in ocean panicked hands up",
            "scene_bottom": "same yellow blob sitting calmly underwater shallow sand peaceful",
            "full": "لا تصنع هما لم يكتبه الله عليك"
        }
    prompt = """
    انت كاتب حكم صفحة hmisc الشهيرة.
    اكتب حكمة JSON فقط بهذا الشكل بدون اي كلام اضافي:
    {"ar_top":"سطر اول قصير","ar_bottom":"سطر ثاني","en":"English translation","scene_top":"description for top image in English, yellow blob character","scene_bottom":"description for bottom image in English, same character peaceful","full":"الحكمة كاملة بالعربي"}
    """
    try:
        res = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"Groq error: {e}")
        return {
            "ar_top": "لا تنتظر ما في أيدي الناس",
            "ar_bottom": "فما كتبه الله لك سيأتيك",
            "en": "Don't wait for what is in people's hands",
            "scene_top": "yellow blob looking at other people with empty hands sad",
            "scene_bottom": "same yellow blob holding small gift from sky happy",
            "full": "لا تنتظر ما في أيدي الناس"
        }

def gen_img(desc):
    base = "hmisc style, hand-drawn watercolor storybook illustration on beige vintage paper texture, simple cute yellow blob character, minimalist soft colors, "
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(base+desc)}?width=1080&height=1350&model=flux&nologo=true&seed={abs(hash(desc))%10000}"
    try:
        r = requests.get(url, timeout=40)
        path = f"/tmp/{abs(hash(desc))}.jpg"
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    except Exception as e:
        print(f"Image error: {e}")
        return None

# --- أوامر البوت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 Hmisc Factory شغال ✅\n\n"
        "/hikma - يصنع صورة حكمة بنفس ستايل الفيديو اللي بعته\n"
        "/idea - فكرة نصية فقط"
    )

async def hikma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ بصنع الفكرة... ثواني")
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, generate_hikma)
        await update.message.reply_text(f"💡 {data['full']}\n\nEN: {data['en']}\n\nبصنع الصور...")

        p1 = await loop.run_in_executor(None, lambda: gen_img(data['scene_top']))
        p2 = await loop.run_in_executor(None, lambda: gen_img(data['scene_bottom']))

        if p1:
            await update.message.reply_photo(photo=open(p1,'rb'), caption=data['ar_top'])
        if p2:
            caption2 = f"{data['ar_bottom']}\n\n{data['en']}\n\n#حكمة #hmisc"
            await update.message.reply_photo(photo=open(p2,'rb'), caption=caption2)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text(f"❌ خطأ: {e}")

async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = generate_hikma()
    await update.message.reply_text(f"AR: {data['full']}\nEN: {data['en']}")

if tg_app:
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("hikma", hikma_cmd))
    tg_app.add_handler(CommandHandler("idea", idea_cmd))

# --- صفحات الويب ---
@flask_app.route('/')
def home():
    return "Hmisc Factory Live ✅"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if not tg_app:
        return "BOT_TOKEN missing", 500
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, tg_app.bot)
        asyncio.run(tg_app.process_update(update))
    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN:
        return "❌ BOT_TOKEN مش موجود في Render > Environment > Add Variable", 500
    try:
        import asyncio
        url = f"{request.host_url}{TOKEN}".rstrip('/')

        async def set_it():
            await tg_app.bot.set_webhook(url)
            return url

        result_url = asyncio.run(set_it())
        return f"✅ Webhook set OK<br>URL: {result_url}<br><br>روح تيليجرام وابعت /hikma", 200

    except Exception as e:
        return f"❌ Error: {str(e)}<br><br>تأكد BOT_TOKEN صحيح وانه من @BotFather", 500

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
