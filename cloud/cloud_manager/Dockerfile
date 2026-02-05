FROM python:3.11-slim

WORKDIR /app

COPY cloud/cloud_manager/requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV PYTHONPATH=/app

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 cloud.cloud_manager.main:app
