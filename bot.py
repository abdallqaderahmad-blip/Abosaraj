# -*- coding: utf-8 -*-
import os
import json
import logging
import requests
from flask import Flask, request
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
flask_app = Flask(__name__)

def tg_send(chat_id, text):
    url = 'https://api.telegram.org/bot' + TOKEN + '/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=15)

def tg_photo(chat_id, photo_url, caption):
    url = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    requests.post(url, json={'chat_id': chat_id, 'photo': photo_url, 'caption': caption}, timeout=30)

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
    return 'Bot Live - Requests Mode'

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if 'message' not in data:
            return 'ok', 200
        chat_id = data['message']['chat']['id']
        text = data['message'].get('text', '')
        if text.startswith('/start'):
            tg_send(chat_id, 'Ready! Send story')
            return 'ok', 200
        if len(text) < 10:
            tg_send(chat_id, 'Short story')
            return 'ok', 200
        tg_send(chat_id, 'Analyzing with Groq...')
        scenes = get_scenes(text)
        tg_send(chat_id, 'Drawing 8 scenes...')
        for s in scenes:
            pr = s.get('prompt', '')
            if not pr:
                continue
            try:
                url = gen_image(pr)
                tg_photo(chat_id, url, 'Scene')
            except Exception as e2:
                tg_send(chat_id, 'Failed: ' + str(e2))
        tg_send(chat_id, 'Done!')
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
