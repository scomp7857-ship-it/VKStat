FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x /app/scripts/start-web.sh

EXPOSE 8000

# Default command suits the web service: run migrations, then serve.
# Worker / scheduler containers override this via docker-compose / render.yaml.
CMD ["/app/scripts/start-web.sh"]
