FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-dejavu-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/zil_video_jobs

EXPOSE 10000

CMD ["sh", "-c", "if [ -f app.py ]; then python app.py; elif [ -f main.py ]; then python main.py; else ls -la && exit 1; fi"]
