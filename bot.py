import os, requests, threading, telebot
from flask import Flask
from gtts import gTTS
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def home(): return "Abosaraj Bot is Live!"

def get_video(q):
    try:
        r = requests.get(f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={q}&per_page=3&orientation=vertical").json()
        return r['hits'][0]['videos']['medium']['url']
    except: return None

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🔥 اهلا ابو سراج!\nاكتب: /reel قط يلعب")

@bot.message_handler(commands=['reel'])
def reel(m):
    topic = m.text.replace('/reel','').strip()
    if not topic:
        bot.reply_to(m, "اكتب: /reel cat"); return
    msg = bot.reply_to(m, f"⏳ بجيب فيديو {topic}...")
    try:
        url = get_video(topic)
        open('bg.mp4','wb').write(requests.get(url).content)
        gTTS(topic, lang='ar').save('v.mp3')
        clip = VideoFileClip('bg.mp4').subclip(0,7)
        clip = clip.set_audio(AudioFileClip('v.mp3'))
        txt = TextClip(topic, fontsize=40, color='white', method='caption', size=(clip.w*0.8,None)).set_duration(clip.duration).set_pos('center')
        final = CompositeVideoClip([clip, txt])
        final.write_videofile('out.mp4', fps=24, logger=None)
        bot.send_video(m.chat.id, open('out.mp4','rb'), caption="✅ جاهز")
        bot.delete_message(m.chat.id, msg.message_id)
    except Exception as e:
        bot.send_message(m.chat.id, f"جرب كلمة انجليزية: {e}")

def run_bot():
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
