# Build context is the repo root (the demo needs ../sdk). See docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY sdk /repo/sdk
COPY demo/requirements.txt /repo/demo/requirements.txt
RUN pip install --no-cache-dir -e /repo/sdk -r /repo/demo/requirements.txt

COPY demo /repo/demo
WORKDIR /repo/demo
CMD ["python", "-m", "arena.debate"]
