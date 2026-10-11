FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-noto-core fonts-noto-extra fonts-noto-naskh fonts-dejavu-core fonts-liberation ca-certificates && rm -rf /var/lib/apt/lists/* && fc-cache -f
WORKDIR /app
COPY requirements.txt./requirements.txt
RUN python -m pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY app.py./app.py
RUN mkdir -p /tmp/zil_video_jobs
EXPOSE 10000
CMD ["python", "app.py"]
