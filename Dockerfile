FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py criteria.en.yaml ./
RUN useradd --uid 1000 --no-create-home bot && mkdir -p /app/data && chown bot /app/data
USER bot
CMD ["python", "bot.py"]
