FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/

RUN echo "========== PROJECT FILES ==========" \
    && find /app -maxdepth 4 -type f -not -path '*/.git/*' \
    && echo "===================================" \
    && test -f /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

RUN ffmpeg -version >/dev/null 2>&1 \
    && ffprobe -version >/dev/null 2>&1

# البحث عن ملف تشغيل البوت
RUN set -eu; \
    if [ -f /app/main.py ]; then \
        echo "main.py found"; \
    elif [ -f /app/app.py ]; then \
        echo "app.py found"; \
    elif [ -f /app/bot.py ]; then \
        echo "bot.py found"; \
    else \
        echo "ERROR: No main.py, app.py, or bot.py found."; \
        echo "Check the PROJECT FILES listed above."; \
        exit 1; \
    fi

CMD ["sh", "-c", "if [ -f /app/main.py ]; then exec python -u /app/main.py; elif [ -f /app/app.py ]; then exec python -u /app/app.py; elif [ -f /app/bot.py ]; then exec python -u /app/bot.py; else echo 'ERROR: Bot entry file not found'; exit 1; fi"]
