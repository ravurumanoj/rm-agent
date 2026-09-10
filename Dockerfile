FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir . gunicorn

COPY app ./app
COPY main.py ./

EXPOSE 8000
# WEB_CONCURRENCY controls worker count; shell form so the env var expands at container start.
CMD gunicorn main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-4}
