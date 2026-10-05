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
        raise ValueError("No JSON found")
    return json.loads(raw[s:e])

def gen_image(p):
    full = p + ", cute cartoon storybook, Pixar style, vibrant colors, 4k"
    r = subscribe("fal-ai/flux/dev", arguments={"prompt": full, "image_size": "landscape_16_9"})
    return r["images"][0]["url"]

@flask_app.route("/")
def home():
    return "Bot Live gpt-oss-20b"

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
            bot.send_message(chat_id=chat_id, text="Bot Ready! Send story")
            return "ok", 200
        if text.startswith("/"):
            return "ok", 200
        if len(text) < 10:
            bot.send_message(chat_id=chat_id, text="Story too short")
            return "ok", 200
        bot.send_message(chat_id=chat_id, text="Analyzing with Groq...")
        scenes = get_scenes(text)
        bot.send_message(chat_id=chat_id, text="Found scenes, drawing now")
        for idx, sc in enumerate(scenes, 1):
            num = sc.get("scene", idx)
            pr = sc.get("prompt", "")
            if not pr:
                continue
            bot.send_message(chat_id=chat_id, text="Drawing scene")
            try:
                url = gen_image(pr)
                bot.send_photo(chat_id=chat_id, photo=url, caption="Scene")
            except Exception as e2:
                bot.send_message(chat_id=chat_id, text="Failed scene")
        bot.send_message(chat_id=chat_id, text="Done!")
    except Exception as e:
        logger.error("Error: %s", e)
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
