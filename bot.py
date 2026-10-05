import os, json, threading, asyncio
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from fal_client import subscribe

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI = os.getenv("GEMINI_API_KEY")
FAL = os.getenv("FAL_KEY")
os.environ["FAL_KEY"] = FAL
genai.configure(api_key=GEMINI)
model = genai.GenerativeModel("gemini-1.5-flash")

app_flask = Flask(__name__)
@app_flask.route('/')
def home(): return "Bot is Live", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("جاهز! ابعت القصة")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    await update.message.reply_text("بحلل القصة...")
    try:
        prompt = f'Convert story to JSON only: {{"title":"x","scenes":[{{"prompt":"english video prompt"}}]}} Story:{story}'
        resp = model.generate_content(prompt)
        txt = resp.text.replace("```json","").replace("```","").strip()
        data = json.loads(txt)
        for i, sc in enumerate(data.get("scenes",[])[:8], 1):
            await update.message.reply_text(f"بنفذ مشهد {i}")
            r = subscribe("fal-ai/kling-video/o3/pro/text-to-video", arguments={"prompt": sc["prompt"]})
            video = r.get("video",{})
            url = video.get("url") if isinstance(video, dict) else r.get("url")
            if url:
                await update.message.reply_video(url, caption=f"مشهد {i}")
        await update.message.reply_text("خلصت ✅")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")

def run_bot():
    async def main():
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
        await app.run_polling()
    asyncio.run(main())

if __name__ == "__main__":
    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=int(os.getenv("PORT",10000))), daemon=True).start()
    run_bot()
