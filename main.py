import os
import requests
import json
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio
from groq import Groq

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")

flask_app = Flask(__name__)

# --- حل مشكلة الـ Event Loop ---
def get_tg_app():
    return ApplicationBuilder().token(TOKEN).build()

groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

def generate_hikma():
    if not groq_client:
        return {"ar_top":"لا تصنع هما","ar_bottom":"لم يكتبه الله عليك","en":"Don't worry","scene_top":"yellow blob drowning panicked","scene_bottom":"yellow blob calm underwater peaceful","full":"لا تصنع هما لم يكتبه الله عليك"}
    try:
        res = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role":"user","content":"اكتب حكمة JSON فقط: {\"ar_top\":\"سطر1\",\"ar_bottom\":\"سطر2\",\"en\":\"english\",\"scene_top\":\"top scene yellow blob\",\"scene_bottom\":\"bottom scene peaceful\",\"full\":\"كاملة\"}"}],
            response_format={"type":"json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except:
        return {"ar_top":"لا تنتظر ما في ايدي الناس","ar_bottom":"فما كتبه الله لك سيأتيك","en":"Don't wait","scene_top":"yellow blob sad looking at empty hands","scene_bottom":"yellow blob happy gift from sky","full":"لا تنتظر ما في ايدي الناس"}

def gen_img(desc):
    base = "hmisc style, hand-drawn watercolor on beige paper, cute yellow blob character, minimalist soft, "
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(base+desc)}?width=1080&height=1350&model=flux&nologo=true"
    try:
        r = requests.get(url, timeout=45)
        path = f"/tmp/{abs(hash(desc))}.jpg"
        with open(path,"wb") as f: f.write(r.content)
        return path
    except: return None

async def start(update, context):
    await update.message.reply_text("🎨 شغال ✅\n/hikma - يصنع حكمة")

async def hikma_cmd(update, context):
    await update.message.reply_text("⏳ بصنع...")
    data = generate_hikma()
    loop = asyncio.get_running_loop()
    p1 = await loop.run_in_executor(None, lambda: gen_img(data['scene_top']))
    p2 = await loop.run_in_executor(None, lambda: gen_img(data['scene_bottom']))
    if p1: await update.message.reply_photo(open(p1,'rb'), caption=data['ar_top'])
    if p2: await update.message.reply_photo(open(p2,'rb'), caption=f"{data['ar_bottom']}\n\n{data['en']}")

async def idea_cmd(update, context):
    d = generate_hikma()
    await update.message.reply_text(d['full'])

# --- Routes ---
@flask_app.route('/')
def home():
    return "Live ✅"

@flask_app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        tg_app = get_tg_app()
        tg_app.add_handler(CommandHandler("start", start))
        tg_app.add_handler(CommandHandler("hikma", hikma_cmd))
        tg_app.add_handler(CommandHandler("idea", idea_cmd))
        data = request.get_json(force=True)
        update = Update.de_json(data, tg_app.bot)
        # Fix event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(tg_app.process_update(update))
        loop.close()
    except Exception as e:
        print(e)
    return "ok", 200

@flask_app.route('/setwebhook')
def setwebhook():
    if not TOKEN:
        return "❌ BOT_TOKEN مش موجود", 500
    try:
        # طريقة مباشرة بدون event loop
        url = f"{request.host_url}{TOKEN}".rstrip('/')
        api = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={url}"
        r = requests.get(api, timeout=10)
        return f"✅ {r.text}<br><br>URL: {url}<br><br>روح ابعت /start في تيليجرام", 200
    except Exception as e:
        return f"❌ Error: {e}", 500

if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
