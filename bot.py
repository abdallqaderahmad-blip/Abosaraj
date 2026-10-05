import os
import json
import threading
import requests
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
    return "Bot Live - Abosaraj", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

async def start(update
