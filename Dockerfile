FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# EXPOSE is informational only for Docker; Render will provide $PORT at runtime
EXPOSE 8000

# Use the PORT environment variable provided by Render (do not hardcode 8000)
# Use shell form so $PORT is expanded at container start
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1"]
