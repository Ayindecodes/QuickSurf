FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Settings come from gunicorn.conf.py (port, workers, timeout).
CMD ["gunicorn", "quicksurf.wsgi:application"]
