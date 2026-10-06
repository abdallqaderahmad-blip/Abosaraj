from flask import Flask
import threading, os

app = Flask(__name__)
@app.route('/')
def home():
    return "Bot Abosaraj is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

# ↓↓↓↓ من هون وتحت لازم يكون كود البوت القديم تبعك كلو ↓↓↓↓

import fal_client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
# ... باقي الكود تبعك ...

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(...):
    ...

# ....

if __name__ == "__main__":
    app
