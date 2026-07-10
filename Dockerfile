FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser \
    && install -d -o appuser -g appuser -m 700 /var/run/tg-v-chat

COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir .

USER appuser

CMD ["python", "-m", "tg_v_chat.runtime", "--role", "bot"]
