import os, telebot
TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def welcome(m):
    bot.reply_to(m, "🔥 بوت Human vs Machine شغال!")

@bot.message_handler(commands=['make'])
def make(m):
    bot.reply_to(m, "🎬 5 افكار كليبات بدون حقوق جاهزة:\n1- Robot wake up\n2- Empty city\n3- Last human\n4- AI watching you\n5- You are being watched")

bot.infinity_polling()
