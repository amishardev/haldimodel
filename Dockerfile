# Haldi curcumin-purity estimator — CPU ONLY. No GPU, no ML model, no weights.
# The whole "analysis" is numpy/OpenCV arithmetic over two images.
FROM python:3.11-slim

# opencv-python-headless wheels are largely self-contained; libglib2.0-0 covers
# older wheels that still link against it. No CUDA / no GPU runtime needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so this layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV PYTHONUNBUFFERED=1 \
    PORT=8000
EXPOSE 8000

# NOTE ON PERSISTENCE: calibration_data.json is written next to /app. On an
# ephemeral filesystem (most PaaS free tiers) it is wiped on every redeploy.
# Mount a persistent volume at /app, or move CALIBRATION_FILE to a mounted path.
#
# 1 worker is plenty: a request is ~0.5 s of CPU. Scale with --workers 2..4 only
# if you have the RAM (each worker can peak ~400 MB on a 12 MP image pair).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
