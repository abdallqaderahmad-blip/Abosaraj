import os, telebot
TOKEN = os.getenv("BOT_TOKEN")
print(f"TOKEN exists: {bool(TOKEN)}")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def s(m):
    bot.send_message(m.chat.id, "شغال! 🔥 جرب /reel cat")

@bot.message_handler(commands=['reel'])
def r(m):
    bot.send_message(m.chat.id, "PIXABAY مربوط: " + str(bool(os.getenv("PIXABAY_KEY"))))

bot.infinity_polling()
