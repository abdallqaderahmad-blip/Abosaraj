import os
import requests
import json
import logging
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from groq import Groq

# --- إعدادات ---
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

flask_app = Flask(__name__)

def get_tg_app():
    return ApplicationBuilder().token(TOKEN).build()

groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# --- مولد الحكم ---
def generate_hikma():
    default = {
        "ar_top": "لا تنتظر ما في أيدي الناس",
        "ar_bottom": "فما كتبه الله لك سيأتيك",
        "en": "Don't wait for what is in people's hands",
        "scene_top": "yellow blob character looking sadly at empty hands of other people",
        "scene_bottom": "same yellow blob happy holding small gift falling from sky with light",
        "full": "لا تنتظر ما في أيدي الناس"
    }
    if not groq_client:
        return default
    try:
        prompt = 'اكتب حكمة بستايل hmisc بصيغة JSON فقط: {"ar_top":"سطر قصير","ar_bottom":"سطر ثاني","en":"english translation","scene_top":"top scene description yellow blob","scene_bottom":"bottom scene description peaceful","full":"الحكمة كاملة"}'
        res = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"Groq error: {e}")
        return default

def gen_img(desc):
    base = "hmisc style, hand-drawn watercolor storybook illustration on beige vintage paper texture, cute yellow blob character, minimalist soft colors, "
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(base + desc)}?width=1080&height=1350&model=flux&nologo=true&seed={abs(hash(desc)) % 10000}"
    try:
        r = requests.get(url, timeout=50)
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
        "/hikma - يصنع صورتين حكمة\n"
        "/idea - فكرة نصية فقط"
    )

async def hikma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ بصنع الفكرة... ثواني")
    try:
        data = generate_hikma()
        await update.message.reply_text(f"💡 {data['full']}\nEN: {data['en']}\n\nبصنع الصور...")

        loop = asyncio.get_running_loop()
        p1 = await loop.run_in_executor(None, lambda: gen_img(data['scene_top']))
        p2 = await loop.run_in_executor(None, lambda: gen_img(data['scene_bottom']))

        if p1:
            await update.message.reply_photo(photo=open(p1, 'rb'), caption=data['ar_top'])
        if p2:
            caption2 = f"{data['ar_bottom']}\n\n{data['en']}\n\n#حكمة"
            await update.message.reply_photo(photo=open(p2, 'rb'), caption=caption2)
        else:
            await update.message.reply_text("❌ فشل إنشاء الصور، جرب مرة ثانية")
    except Exception as e:
        logging.error(e)
        await update.message.reply_text(f"❌ خطأ: {e}")

async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = generate_hikma()
    await update.message.reply_text(f"{d['full']}\n\n{d['en']}")

# --- صفحات الويب ---
@flask_app.route('/')
def home():
    return "Hmisc Factory Live ✅ - افتح /setwebhook لتفعيل البوت"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        tg_app = get_tg_app()
        tg_app.add_handler(CommandHandler("start", start))
        tg_app.add_handler(CommandHandler("hikma", hikma_cmd))
        tg_app.add_handler(CommandHandler("idea", idea_cmd))

        data = request.get_json(force=True)
        update = Update.de_json(data, tg_app.bot)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(tg_app.process_update(update))
        loop.close()
    except Exception as e:
        print(f"Webhook error: {e}")
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN:
        return "❌ BOT_TOKEN مش موجود في Render > Environment", 500
    try:
        # إصلاح مهم: نجبر https لأن تيليجرام لا يقبل http
        host = request.host_url.replace("http://", "https://")
        url = f"{host}{TOKEN}".rstrip('/')
        api_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={url}"

        r = requests.get(api_url, timeout=15)
        result = r.json()

        if result.get("ok"):
            return f"✅ تم التفعيل بنجاح!<br><br>Webhook: {url}<br><br>الان روح تيليجرام وابعت /start", 200
        else:
            return f"❌ فشل: {r.text}<br><br>URL: {url}", 400

    except Exception as e:
        return f"❌ Error: {str(e)}", 500

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
