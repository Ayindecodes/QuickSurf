FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# supercronic runs the scheduled jobs when self-hosting (deploy/crontab). Pinned and checksum-verified.
ARG TARGETARCH=amd64
ARG SUPERCRONIC_VERSION=v0.2.51
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sha=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123 ;; \
      arm64) sha=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94 ;; \
      *) echo "No supercronic checksum for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/usr/local/bin/supercronic')" \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "$sha  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The admin's static files are built into the image and served by whitenoise.
RUN SECRET_KEY=collectstatic-only-never-used-at-runtime python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --no-create-home app
USER app

# Settings come from gunicorn.conf.py (port, workers, threads, timeouts).
CMD ["gunicorn", "quicksurf.wsgi:application"]
