import os
import json
import requests
import asyncio
from flask import Flask, request
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
telegram_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا ابو سراج! أرسل قصة وبرسمها لك 8 صور 🥕🎨")

def get_scenes(story):
    prompt = "Return ONLY JSON array like [{\"scene\":1,\"prompt\":\"english cartoon prompt\"}] 4 scenes. Story: " + story
    chat = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )
    text = chat.choices[0].message.content
    s = text.find("[")
    e = text.rfind("]") + 1
    return json.loads(text[s:e])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return
    await update.message.reply_text("⚡️ Groq يحلل قصتك...")
    try:
        scenes = get_scenes(update.message.text)
        for sc in scenes[:4]:
            await update.message.reply_text("🎨 رسم المشهد " + str(sc["scene"]) + "...")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": sc["prompt"] + ", cute cartoon storybook, vibrant colors"})
            img_url = result["images"][0]["url"]
            await update.message.reply_photo(photo=img_url, caption="المشهد " + str(sc["scene"]))
        await update.message.reply_text("✅ خلصت! خد جزرتك 🥕")
    except Exception as e:
