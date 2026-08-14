# iSgela container image.
#
# Built so a demo can be run from an image if we run out of runway before a
# proper deployment. Two stages: dependencies are compiled into a virtualenv in
# the builder, and only the finished virtualenv is copied forward, so pip and
# its caches never reach the runtime image.

# --------------------------------------------------------------------------- #
# builder
# --------------------------------------------------------------------------- #

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Only the requirements file, so this layer is cached until dependencies change
# rather than on every source edit.
COPY requirements.txt ./

# psycopg2-binary and Pillow both ship wheels for this platform, so no compiler
# toolchain is needed. If that ever changes, build-essential and libpq-dev go here.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# --------------------------------------------------------------------------- #
# runtime
# --------------------------------------------------------------------------- #

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8000

# A non-root user, so a container escape does not start as root.
RUN groupadd --system isgela \
    && useradd --system --gid isgela --home-dir /app --no-create-home isgela

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=isgela:isgela . .

# collectstatic reads settings, which insist on a database URL, so throwaway
# values are supplied for the build only. Nothing connects to a database here.
RUN SECRET_KEY=build-time-only \
    DATABASE_URL="postgresql://build:build@localhost:5432/build?sslmode=disable" \
    python manage.py collectstatic --noinput --clear \
    && chown -R isgela:isgela /app/staticfiles

USER isgela

EXPOSE 1520

# A plain TCP check rather than an HTTP request: it confirms the server is
# accepting connections without depending on what ALLOWED_HOSTS is set to.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ.get('PORT', 8000))), timeout=4)"

ENTRYPOINT ["./docker-entrypoint.sh"]

# --timeout 180 matters: marking is synchronous and a paper can take up to a
# minute to come back. Gunicorn's 30 second default would kill those requests.
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT} --workers 3 --threads 2 --timeout 180 --access-logfile - --error-logfile -"]
