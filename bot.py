import os, json, threading, requests, time
from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
client = genai.Client(api_key=GEMINI)

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return '<h1 style="text-align:center;padding:60px;font-family:sans-serif">🎨 ابو سراج - Bot Live ✅<br><br><a href="https://t.me/YOUR_BOT">افتح البوت</a><br><br>abosaraj.onrender.com</h1>'

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بك في بوت أبو سراج! 🎉\nأرسل قصة قصيرة.")

def get_gemini_response(prompt):
    for m in ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash"]:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res.text:
                return res
        except:
            time.sleep(1)
            continue
    raise Exception("Gemini busy")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    if story.startswith("/"):
        return
    await update.message.reply_text("⏳ جاري التحليل...")
    prompt = f'JSON only [ {{"scene":1,"prompt":"english cartoon"}} ] story: {story}'
    try:
        response = get_gemini_response(prompt)
        text = response.text.replace("```json","").replace("```","").strip()
        scenes = json.loads(text)
        for s in scenes[:8]:
            await update.message.reply_text(f"🎨 مشهد {s['scene']}/8")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": s['prompt']})
            await update.message.reply_photo(photo=result['images'][0]['url'])
        await update.message.reply_text("✅ خلصت!")
    except Exception as e:
        await update.message.reply_text(f"جرب بعد دقيقة: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    try:
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except:
        pass
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)
