import os, json, logging, requests
from flask import Flask, request
from fal_client import subscribe
from groq import Groq

TOKEN = os.getenv('TELEGRAM_TOKEN')
GROQ_KEY = os.getenv('GROQ_API_KEY')
FAL_KEY = os.getenv('FAL_KEY')
if FAL_KEY:
    os.environ['FAL_KEY'] = FAL_KEY

groq_client = Groq(api_key=GROQ_KEY)
flask_app = Flask(__name__)

def tg_send(cid, txt):
    u = 'https://api.telegram.org/bot' + TOKEN + '/sendMessage'
    requests.post(u, json={'chat_id': cid, 'text': txt}, timeout=20)

def tg_photo(cid, url):
    u = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    requests.post(u, json={'chat_id': cid, 'photo': url}, timeout=40)

def get_scenes(story):
    p = 'Return JSON array 8 prompts. Story: ' + story
    c = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[{'role': 'user', 'content': p}],
        max_tokens=2000
    )
    raw = c.choices[0].message.content.strip()
    a = raw.find('[')
    b = raw.rfind(']') + 1
    data = json.loads(raw[a:b])
    out = []
    for x in data:
        if isinstance(x, str):
            out.append(x)
    return out

def gen_image(pr):
    r = subscribe('fal-ai/flux/dev', arguments={'prompt': pr, 'image_size': 'landscape_16_9'})
    return r['images'][0]['url']

@flask_app.route('/')
def home():
    return 'Bot Live'

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    msg = data.get('message')
    if not msg:
        return 'ok', 200
    cid = msg['chat']['id']
    txt = msg.get('text', '')
    if txt.startswith('/start'):
        tg_send(cid, 'Ready! Send story')
        return 'ok', 200
    if len(txt) < 15:
        tg_send(cid, 'Short')
        return 'ok', 200
    tg_send(cid, 'Analyzing...')
    scenes = get_scenes(txt)
    tg_send(cid, 'Drawing...')
    for pr in scenes:
        try:
            url = gen_image(pr)
            tg_photo(cid, url)
        except Exception as e:
            tg_send(cid, 'Fail ' + str(e)[:300])
    tg_send(cid, 'Done!')
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
