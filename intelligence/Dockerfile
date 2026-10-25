FROM python:3.11-slim

# Run as an unprivileged user, never as root
RUN useradd -m -u 1000 app
WORKDIR /home/app/service

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Service code only: tests, notebooks and docs stay out of the image (see .dockerignore)
COPY *.py ./
USER app
ENV HF_HOME=/home/app/.cache/huggingface \
    LOG_DIR=/tmp/verifier-logs \
    PYTHONUNBUFFERED=1

# Render sets PORT (default 10000)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
