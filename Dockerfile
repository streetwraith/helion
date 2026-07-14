# helion — Coolify build image.
# Mirrors prod's interpreter (3.11.9); slim base because the app has no native-lib
# deps (grep found no matplotlib/PIL/pdfkit/etc.; psycopg2-binary ships its own libpq).
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first for layer caching. requirements.txt is the single source of truth.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Collect static for WhiteNoise. settings.py reads several env vars at import time with
# no defaults (SECRET_KEY, DATABASE_URL, ESI_*, CELERY_BEAT_SCHEDULER), so pass throwaway
# build-only values on this one layer — collectstatic never touches the DB or network.
RUN SECRET_KEY=build-only \
    DATABASE_URL=postgres://u:p@localhost:5432/db \
    ESI_CLIENT_ID=build ESI_CLIENT_SECRET=build \
    ESI_CLIENT_CALLBACK_URL=https://build.invalid/sso/callback/ \
    ESI_USER_CONTACT_EMAIL=build@build.invalid \
    CELERY_BEAT_SCHEDULER=django_celery_beat.schedulers:DatabaseScheduler \
    python manage.py collectstatic --noinput

# Run as non-root (matches prod's 'code' user).
RUN useradd -ms /bin/bash app && chown -R app:app /app
USER app

EXPOSE 8000

# Default command = web (gunicorn); worker/beat override this in docker-compose.coolify.yaml.
# --bind 0.0.0.0 (not gunicorn's 127.0.0.1 default) so coolify-proxy can reach it.
CMD ["gunicorn", "helion.wsgi", "--bind", "0.0.0.0:8000", "--timeout", "600", "--log-file", "-"]
