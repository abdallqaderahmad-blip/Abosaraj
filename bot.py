# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_KEY)
flask_app = Flask(__name__)
telegram_app = ApplicationBuilder().token(TOKEN).build()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(telegram_app.initialize())

def get_scenes(story):
    prompt = "Return ONLY JSON array 8 scenes: [{\"scene\":1,\"prompt\":\"english cartoon prompt\"}] Story: " + story
    comp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000
    )
    raw = comp.choices[0].message.content.strip()
    s = raw.find("[")
    e = raw.rfind("]") + 1
    if s == -1:
        raise ValueError("No JSON found")
