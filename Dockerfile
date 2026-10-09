FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# تثبيت ffmpeg مع تجاهل أخطاء الخطوط
RUN apt-get update || true
RUN apt-get install -y ffmpeg || true
RUN rm -rf /var/lib/apt/lists/* || true

CMD ["python", "bot.py"]
