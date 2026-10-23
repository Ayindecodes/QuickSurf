# gunicorn.conf.py: gunicorn loads this automatically when started from the project root.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
threads = int(os.environ.get("GUNICORN_THREADS", 4))  # purchases mostly wait on VTpass/Paystack

# A purchase can wait on VTpass for up to ~30s (5s connect + 25s read). gunicorn's default 30s
# timeout would kill the worker mid-purchase and leave the money held, so allow comfortably more.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 90))
graceful_timeout = 30

# Keep idle connections open longer than the proxy in front (Render's) does, so the proxy, not gunicorn,
# closes them. With gunicorn's 2s default, a request sent on a connection gunicorn was just closing is
# reset; the load test (scripts/load_test.py) saw that about once per 1,000 requests.
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 75))

accesslog = "-"
errorlog = "-"
