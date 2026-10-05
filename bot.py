import os, time, threading, requests, telebot
from flask import Flask
from gtts import gTTS
from moviepy.editor import VideoFileClip, AudioFileClip

TOKEN = os.getenv("BOT_TOKEN")
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Live!"

def get_video(q):
    try:
        url = "https://pixabay.com/api/videos/"
        params = {"key": PIXABAY_KEY, "q": q, "per_page": 3}
        data = requests.get(url, params=params, timeout=20).json()
        return data["hits"][0]["videos"]["medium"]["url"]
    except:
        return None

@bot.message_handler(commands=["start"])
def start_cmd(m):
    bot.send_message(m.chat.id, "Bot is working! Send /reel cat")

@bot.message_handler(commands=["reel"])
def reel_cmd(m):
    topic = m.text.replace("/reel", "").strip()
    if not topic:
        topic = "cat"
    s = bot.reply_to(m, "Loading " + topic)
    try:
        vurl = get_video(topic)
        if not vurl:
            bot.send_message(m.chat.id, "No video found")
            return
        open("bg.mp4", "wb").write(requests.get(vurl, timeout=30).content)
        gTTS(topic, lang="en").save("v.mp3")
        vc = VideoFileClip("bg.mp4").subclip(0, 5)
        ac = AudioFileClip("v.mp3")
        final = vc.set_audio(ac)
        final.write_videofile("out.mp4", fps=24, logger=None)
        bot.send_video(m.chat.id, open("out.mp4", "rb"))
        bot.delete_message(m.chat.id, s.message_id)
    except Exception as e:
        bot.send_message(m.chat.id, "Error: " + str(e))
        print(e)

def run_bot():
    bot.remove_webhook()
    time.sleep(2)
    bot.infinity_polling(skip_pending=True)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
