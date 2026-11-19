FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=3000
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libass9 fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY public ./public
RUN mkdir -p data/outputs data/work
EXPOSE 3000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-3000}"]
