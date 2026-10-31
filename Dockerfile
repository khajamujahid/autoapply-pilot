FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
COPY . .
ENV DATA_DIR=/data
EXPOSE 8077
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
