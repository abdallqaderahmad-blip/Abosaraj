import telebot
import yt_dlp
import os

TOKEN = "حط توكن بوتك هان"  # جيبه من @BotFather
bot = telebot.TeleBot(TOKEN)

# رسالة الترحيب
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🔥 ابعتلي رابط تيكتوك أو انستا وأنا بحمله الك بدون علامة مائية\n\nجرب هلا!")

@bot.message_handler(func=lambda m: True)
def download(m):
    url = m.text
    if "tiktok.com" not in url and "instagram.com" not in url and "instagr.am" not in url:
        return

    msg = bot.send_message(m.chat.id, "⏳ بحمل... ثواني")
    
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': 'video.%(ext)s',
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file = ydl.prepare_filename(info)
        
        # ابعت الفيديو
        with open(file, 'rb') as f:
            bot.send_video(m.chat.id, f, caption="✅ تفضل بدون علامة\n\nبوت @اسم_بوتك")
        
        os.remove(file)
        bot.delete_message(m.chat.id, msg.message_id)

    except Exception as e:
        bot.edit_message_text("❌ الرابط مش شغال أو الفيديو خاص، جرب رابط ثاني", m.chat.id, msg.message_id)

bot.infinity_polling()
