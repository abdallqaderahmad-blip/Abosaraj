# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_KEY)
flask_app = Flask(__name__)
telegram_app = ApplicationBuilder().token(TOKEN).build()

# Initialize once at startup, not per request
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(telegram_app.initialize())

def get_scenes(story):
    prompt = "Return ONLY JSON array 8 scenes: [{\"scene\":1,\"prompt\":\"english cartoon prompt\"}] Story: " + story
    comp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000
    )
    raw = comp.choices[0].message.content.strip()
    s = raw.find("[")
    e = raw.rfind("]") + 1
    return json.loads(raw[s:e])

def gen_image(p):
    full = p + ", cute cartoon storybook, Pixar style, vibrant colors, 4k"
    r = subscribe("fal-ai/flux/dev", arguments={"prompt": full, "image_size": "landscape_16_9"})
    return r["images"][0]["url"]

async def start_cmd(update, context):
    await update.message.reply_text("Bot Ready! Send me a story and I will create 8 cartoon images.")

async def handle_story(update, context):
    if not update.message or not update.message.text:
        return
    txt = update.message.text.strip()
    if txt.startswith("/"):
        return
    if len(txt) < 10:
        await update.message.reply_text("Story too short")
        return
    await update.message.reply_text("Analyzing story with Groq...")
    try:
        scenes = get_scenes(txt)
        await update.message.reply_text("Found " + str(len(scenes)) + " scenes, drawing...")
        for idx, sc in enumerate(scenes, 1):
            num = sc.get("scene", idx)
            pr = sc.get("prompt", "")
            if not pr:
                continue
            await update.message.reply_text("Drawing scene " + str(num) + "/8")
            try:
                url = gen_image(pr)
                await update.message.reply_photo(photo=url, caption="Scene " + str(num))
            except Exception as e2:
                await update.message.reply_text("Failed scene " + str(num) + ": " + str(e2))
        await update.message.reply_text("Done! All images created.")
    except Exception as e:
        await update.message.reply_text("Error: " + str(e))

telegram_app.add_handler(CommandHandler("start", start_cmd))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_story))

@flask_app.route("/")
def home():
    return "Bot Live Final Fixed"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        upd = Update.de_json(data, telegram_app.bot)
        loop.run_until_complete(telegram_app.process_update(upd))
    except Exception as e:
        logger.error("webhook error: %s", e)
    return "ok", 200

if __name__ == "__main__":
    wh = "https://abosaraj.onrender.com/webhook"
    api = "https://api.telegram.org/bot" + TOKEN + "/setWebhook?url=" + wh
    try:
        requests.get(api, timeout=15)
    except:
        pass
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
