FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir email-validator

COPY . .

RUN mkdir -p keys

CMD ["python", "main.py"]
