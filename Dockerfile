FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
COPY . .
EXPOSE 8077
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8077/healthz')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077"]
