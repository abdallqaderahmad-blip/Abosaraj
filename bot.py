import os, json, threading, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
groq_client = Groq(api_key=GROQ_KEY)

flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return '<h1>Bot Live - Groq ✅</h1>'
def run_flask(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا! أرسل قصة ورح أحولها لـ 8 صور 🎨")

def get_scenes(story):
    prompt = f'Return ONLY JSON array: [{{"scene":1,"prompt":"english cartoon prompt"}}] Story: {story}'
    chat = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role":"user","content":prompt}])
    text = chat.choices[0].message.content
    s=text.find('['); e=text.rfind(']')+1
