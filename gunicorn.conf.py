# gunicorn.conf.py: gunicorn loads this automatically when started from the project root.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
threads = int(os.environ.get("GUNICORN_THREADS", 4))  # purchases mostly wait on VTpass/Paystack

# A purchase can wait on VTpass for up to ~30s (5s connect + 25s read). gunicorn's default 30s
# timeout would kill the worker mid-purchase and leave the money held, so allow comfortably more.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 90))
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
