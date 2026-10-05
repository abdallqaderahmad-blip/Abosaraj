import os
import telebot

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    print("ERROR: BOT_TOKEN not found in Environment!")
    exit(1)

print("Starting bot...")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start','reel'])
def handle(m):
    bot.reply_to(m, "✅ البوت شغال! Pixabay مربوط: " + str(bool(os.getenv("PIXABAY_KEY"))))

print("Bot polling...")
bot.infinity_polling()
