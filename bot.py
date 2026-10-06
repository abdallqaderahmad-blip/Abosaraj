from flask import Flask
import threading, os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1. سيرفر وهمي مشان Render ما يعمل Timeout ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot Abosaraj is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading
