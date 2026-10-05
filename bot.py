import os, threading, telebot
from flask import Flask

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Live!"

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "✅ شغال! جرب /reel cat")

@bot.message_handler(commands=['reel'])
def reel(m):
    topic = m.text.replace('/reel','').strip() or "cat"
    bot.reply_to(m, f"⏳ بجهز ريل لـ {topic} (النسخة التجريبية شغالة)")

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
