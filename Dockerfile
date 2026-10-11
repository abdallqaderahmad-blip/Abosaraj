FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# تثبيت الأدوات اللازمة لمعالجة الفيديو والصوت
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملف المكتبات
COPY requirements.txt /app/requirements.txt

# التأكد من وجود ملف المكتبات
RUN test -f /app/requirements.txt

# تثبيت مكتبات Python
RUN pip install --no-cache-dir -r /app/requirements.txt

# نسخ ملفات المشروع
COPY . /app/

# عرض الملفات والتأكد من وجود ملفات التشغيل المطلوبة
RUN echo "=== Files in /app ===" \
    && ls -la /app \
    && test -f /app/main.py \
    && test -f /app/requirements.txt

# التأكد من توفر FFmpeg وFFprobe
RUN ffmpeg -version >/dev/null 2>&1 \
    && ffprobe -version >/dev/null 2>&1

# تشغيل بوت ظل ZIL
CMD ["python", "-u", "/app/main.py"]
