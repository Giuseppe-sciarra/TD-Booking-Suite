# Base and libraries of each release: scripts/aggiorna-tutto.sh passes newer ones at night (compose.override.yaml)
ARG PYTHON_IMAGE=python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Debian security fixes published after the base image
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 tdbooking && useradd --uid 10001 --gid tdbooking --create-home tdbooking
WORKDIR /app
ARG REQUIREMENTS=requirements.lock
COPY ${REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY --chown=tdbooking:tdbooking . .
RUN PYTHONPATH=/app python scripts/build_static.py
USER 10001:10001
EXPOSE 8000
CMD ["sh","-c","python manage.py check_runtime && exec gunicorn tdbooking.wsgi:application --no-control-socket --bind 0.0.0.0:8000 --workers 3 --threads 4 --timeout 60 --max-requests 2000 --max-requests-jitter 200 --log-level warning"]
