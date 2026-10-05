# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
from flask import Flask, request
import telegram
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
bot = telegram.Bot(token=TOKEN)
flask_app = Flask(__name__)

def get_scenes(story):
    prompt = "Return ONLY JSON array 8 scenes like [{\"scene\":1,\"prompt\":\"english cartoon prompt\"}] Story: " + story
    comp = groq_client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000
    )
    raw = comp.choices[0].message.content.strip()
    s = raw.find("[")
    e = raw.rfind("]") + 1
    if s == -1:
        raise ValueError("No JSON in response: " + raw[:200])
    return json.loads(raw[s:e])

def gen_image(p):
    full = p + ", cute cartoon storybook, Pixar style, vibrant colors, 4k"
    r = subscribe("fal-ai/flux/dev", arguments={"prompt": full, "image_size": "landscape_16_9"})
    return r["images"][0]["url"]

@flask_app.route("/")
def home():
    return "Bot Live - gpt-oss-20b - No Event Loop"

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if "message" not in data:
            return "ok", 200
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if not text:
            return "ok", 200

        if text.startswith("/start"):
            bot.send_message(chat_id=chat_id, text="Bot Ready! Send me a story and I will create 8 cartoon images.")
            return "ok", 200

        if text.startswith("/"):
            return "ok", 200

        if len(text) < 10:
            bot.send_message(chat_id=chat_id, text="Story too short, send longer
