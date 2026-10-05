# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
from flask import Flask, request
import telegram
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv('TELEGRAM_TOKEN')
GROQ_KEY = os.getenv('GROQ_API_KEY')
FAL_KEY = os.getenv('FAL_KEY')
if FAL_KEY:
    os.environ['FAL_KEY'] = FAL_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_KEY)
bot = telegram.Bot(token=TOKEN)
flask_app = Flask(__name__)

def get_scenes(story):
    prompt = 'Make 8 scenes JSON array. Story: ' + story
    comp = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=2000
    )
    raw = comp.choices[0].message.content.strip()
    a = raw.find('[')
    b = raw.rfind(']') + 1
    return json.loads(raw[a:b])

def gen_image(p):
    r = subscribe('fal-ai/flux/dev', arguments={'prompt': p, 'image_size': 'landscape_16_9'})
    return r['images'][0]['url']

@flask_app.route('/')
def home():
    return 'Bot Live'

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if 'message' not in data:
            return 'ok', 200
        chat_id = data['message']['chat']['id']
        text = data['message'].get('text', '')
        if text.startswith('/start'):
            bot.send_message(chat_id=chat_id, text='Ready')
            return 'ok', 200
        if len(text) < 10:
            bot.send_message(chat_id=chat_id, text='Short')
            return 'ok', 200
        bot.send_message(chat_id=chat_id, text='Analyzing')
        scenes = get_scenes(text)
        for s in scenes:
            pr = s.get('prompt', '')
            url = gen_image(pr)
            bot.send_photo(chat_id=chat_id, photo=url, caption='Scene')
        bot.send_message(chat_id=chat_id, text='Done')
    except Exception as e:
        logger.error(str(e))
    return 'ok', 200

if __name__ == '__main__':
    wh = 'https://abosaraj.onrender.com/webhook'
    api = 'https://api.telegram.org/bot' + TOKEN + '/setWebhook?url=' + wh
    try:
        requests.get(api, timeout=10)
    except:
        pass
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port)
