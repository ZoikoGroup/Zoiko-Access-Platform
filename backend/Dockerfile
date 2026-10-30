FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer is cached until requirements change.
COPY requirements ./requirements
RUN pip install --default-timeout=120 --retries 10 -r requirements/dev.txt

COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 zoiko && chown -R zoiko:zoiko /app
USER zoiko

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
