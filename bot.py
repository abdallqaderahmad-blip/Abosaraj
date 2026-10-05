import os, json, requests
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq
import asyncio

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
groq_client = Groq(api_key=GROQ_KEY)

flask_app = Flask(__name__)
app = ApplicationBuilder().token(TOKEN).build()

async def start(update, context):
    await update.message.reply_text("أهلا! أرسل قصة والبوت رح يرسمها 8 صور 🎨")

def get_scenes(story):
    prompt = f'Return ONLY JSON array: [{{"scene":1,"prompt":"english cartoon prompt"}}] 8 scenes Story: {story}'
    chat = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role":"user","content":prompt}])
    text = chat.choices[0].message.content
    s = text.find('['); e = text.rfind(']')+1
    return json.loads(text[s:e])

async def handle_message(update, context):
    if not update.message or not update.message.text or update.message.text.startswith("/"): return
    await update.message.reply_text("⚡️ جاري التحليل...")
    try:
        scenes = get_scenes(update.message.text)
        for sc in scenes[:4]:
            await update.message.reply_text(f"🎨 رسم المشهد {sc['scene']}...")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": sc['prompt']+", cute cartoon storybook vibrant"})
            await update.message.reply_photo(photo=result['images'][0]['url'], caption=f"المشهد {sc['scene']}")
        await update.message.reply_text("✅ خلصت! خد جزرتك 🥕")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@flask_app.route('/')
def home(): return 'Bot Live ✅'

@flask_app.route(f'
