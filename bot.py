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
    payload = dict(chat_id=cid, text=txt[:4000])
    requests.post(u, json=payload, timeout=20)

def tg_photo(cid, url, cap=''):
    u = 'https://api.telegram.org/bot' + TOKEN + '/sendPhoto'
    payload = dict(chat_id=cid, photo=url, caption=cap)
    requests.post(u, json=payload, timeout=50)

def get_scenes(story):
    p = 'Return ONLY JSON array of 8 short english cartoon prompts. Story: ' + story
    c = groq_client.chat.completions.create(
        model='openai/gpt-oss-20b',
        messages=[dict(role='user', content=p)],
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
    return out[:8]

def gen_image(pr):
    try:
        if not FAL_KEY:
            raise Exception('no key')
        # بدون اقواس {} نهائيا
        args = dict()
        args['prompt'] = pr
        args['image_size'] = 'landscape_16_9'
        r = subscribe('fal-ai/flux/dev', arguments=args)
        imgs = r.get('images')
        first = imgs[0]
        return first.get('url')
    except Exception as e:
        print('fal fail ' + str(e))
        safe = requests.utils.quote(pr)
        base = 'https://image.pollinations.ai/prompt/'
        tail = '?width=1024&height=576&model=flux&nologo=true'
        return base + safe + tail

@flask_app.route('/')
def home():
    return 'Bot Live Final'

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
        tg_send(cid, 'Short
