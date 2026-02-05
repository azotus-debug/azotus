FROM python:3.11

ENV PYTHONUNBUFFERED True

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run with python directly for maximum stability
CMD ["python", "main.py"]
