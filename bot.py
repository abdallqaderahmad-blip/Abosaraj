import os
import time
import threading
import requests
import telebot
from flask import Flask
from gtts import gTTS
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")

if not TOKEN:
    print("ERROR: BOT_TOKEN not found!")
    exit(1)

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "Abosaraj Bot is Live - 409 Fixed!"

def get_video(query):
    try:
        url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&per_page=3&safesearch=true&order=popular&orientation=vertical"
        data = requests.get(url, timeout=20).json()
        if data['hits']:
            return data['hits'][0]['videos']['medium']['url']
    except Exception as e:
        print(f"Pixabay Error: {e}")
    return None

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🔥 أهلا أبو سراج! البوت شغال 100%\n\nاكتب:\n/reel cat\n/reel horror\n/reel funny dog")

@bot.message_handler(commands=['reel'])
def reel(m):
    topic = m.text.replace('/reel','').strip()
    if not topic:
        bot.reply_to(m, "اكتب مثلا: /reel cat")
        return

    status = bot.reply_to(m, f"⏳ بجهز فيديو: {topic}...")
    try:
        video_url = get_video(topic)
        if not video_url:
            bot.send_message(m.chat.id, "❌ ما لقيت فيديو، جرب كلمة انجليزية: cat, dog, city")
            return

        # تحميل الفيديو
        r = requests.get(video_url, timeout=30)
        open('bg.mp4','wb').write(r.content)

        # صوت
        gTTS(text=topic, lang='en', slow=False).save('v.mp3')

        # دمج
        audio = AudioFileClip('v.mp3')
        clip = VideoFileClip('bg.mp4').subclip(0, min(7, VideoFileClip('bg.mp4').duration))
        clip = clip.set_audio(audio).set_duration(audio.duration + 0.5)

        txt = TextClip(topic, fontsize=50, color='white', stroke_color='black', stroke_width=2, method='caption', size=(clip.w*0.8,None)).set_duration(clip.duration).set_pos('center')
        final = CompositeVideoClip([clip, txt])
        final.write_videofile('out.mp4', fps=24, codec='libx
