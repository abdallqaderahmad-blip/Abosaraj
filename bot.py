import os
import requests
import threading
import time
import telebot
from flask import Flask

print("BOT FILE LOADED")

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")

print(f"TOKEN ok: {bool(TOKEN)}")
print(f"PIXABAY ok: {bool(PIXABAY_KEY)}")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot running! Go to Telegram and send /reel cat"

@bot.message_handler(commands=["start"])
def start(m):
    bot.reply_to(m, "Bot working! Send /reel cat")

@bot.message_handler(commands=["reel"])
def reel_handler(m):
    print(f"Got reel command: {m.text}")
    topic = m.text.replace("/reel", "").strip()
    if not topic:
