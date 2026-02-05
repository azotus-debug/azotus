FROM python:3.11-slim

WORKDIR /app

COPY cloud/review/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY config.py /app/config.py
COPY gcp_auth.py /app/gcp_auth.py
COPY gcs_jobs.py /app/gcs_jobs.py
COPY review_portal.py /app/review_portal.py
COPY templates/review_portal.html /app/templates/review_portal.html

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD ["python", "-u", "review_portal.py"]
