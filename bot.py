import os, requests, telebot
from gtts import gTTS
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip
import yt_dlp

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
bot = telebot.TeleBot(TOKEN)

# يجيب فيديو لحاله
def get_video(query):
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q={query}&orientation=vertical&per_page=3"
    data = requests.get(url).json()
    return data['hits'][0]['videos']['medium']['url']

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🔥 اهلا! ابعت رابط تيك توك أو اكتب:\n/reel cat")

@bot.message_handler(commands=['reel'])
def reel(m):
    topic = m.text.replace('/reel','').strip()
    if not topic:
        bot.send_message(m.chat.id, "مثال: /reel cat playing")
        return
    s = bot.send_message(m.chat.id, f"⏳ بجيب فيديو لـ {topic} لحاله...")
    try:
        link = get_video(topic)
        open('bg.mp4','wb').write(requests.get(link).content)
        gTTS(topic, lang='ar').save('v.mp3')
        clip = VideoFileClip('bg.mp4').subclip(0,7).set_audio(AudioFileClip('v.mp3'))
        txt = TextClip(topic, fontsize=50, color='white', method='caption', size=(clip.w*0.8,None)).set_duration(clip.duration).set_pos('center')
        final = CompositeVideoClip([clip, txt])
        final.write_videofile('out.mp4', fps=24, logger=None)
        bot.send_video(m.chat.id, open('out.mp4','rb'), caption="✅ جاهز أوتوماتك")
        bot.delete_message(m.chat.id, s.message_id)
    except Exception as e:
        bot.send_message(m.chat.id, f"جرب كلمة انجليزية: {e}")

@bot.message_handler(func=lambda m: "tiktok.com" in m.text)
def download(m):
    bot.send_message(m.chat.id, "⏳ بحمل...")
    try:
        ydl_opts = {'format':'mp4','outtmpl':'t.mp4','quiet':True}
        with
