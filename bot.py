import os, telebot
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def welcome(m):
    bot.reply_to(m, "🔥 بوت Human vs 1 جاهز!")

@bot.message_handler(commands=['make'])
def make(m):
    bot.reply_to(m, "🎬 1 5 \nفوق جاهزة")

# --- هذا الجزء عشان Render ما يطفي البوت ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is Live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

bot.infinity_polling()
