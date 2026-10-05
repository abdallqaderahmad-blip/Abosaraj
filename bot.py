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
def home(): return "Bot Live - Multi Model", 200
def run_flask(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بك في بوت أبو سراج! 🎉\nأرسل قصة قصيرة ورح أحولها لـ 8 صور.")

def get_gemini_response(prompt):
    # نبدأ بالأكثر استقرارا
    models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash"]
    last_err = ""
    for m in models:
        try:
            print(f"Trying {m}")
            res = client.models.generate_content(model=m, contents=prompt)
            return res
        except Exception as e:
            last_err = str(e)
            print(f"{m} failed: {last_err}")
            time.sleep(1)
            continue
    raise Exception(last_err)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = update.message.text
    if story.startswith("/"): return
    await update.message.reply_text("⏳ جاري تحليل قصتك لـ 8 مشاهد...")
    prompt = f"حول القصة ل 8 مشاهد بصيغة JSON فقط بهذا الشكل [{{'scene':1,'prompt':'english cartoon prompt'}}] القصة: {story}"
    try:
        response = get_gemini_response(prompt)
        text = response.text.replace("```json","").replace("```","").strip()
        scenes = json.loads(text)
        for s in scenes[:8]:
            await update.message.reply_text(f"🎨 رسم المشهد {s['scene']}/8...")
            result = subscribe("fal-ai/flux/dev", arguments={"prompt": s['prompt'] + ", cute cartoon storybook, vibrant"})
            await update.message.reply_photo(photo=result['images'][0]['url'], caption=f"المشهد {s['scene']}")
        await update.message.reply_text("✅ خلصت! ابعت قصة جديدة")
    except Exception as e:
        await update.message.reply_text(f"خطأ مؤقت من جوجل، جرب بعد دقيقة:\n{e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    try: requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except: pass
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)
