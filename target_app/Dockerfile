FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 gunicorn==22.0.0
COPY app.py .
ENV PORT=8080 \
    GUNICORN_WORKERS=4 \
    GUNICORN_THREADS=2 \
    GUNICORN_TIMEOUT=120
EXPOSE 8080
CMD exec gunicorn -w ${GUNICORN_WORKERS} -k gthread --threads ${GUNICORN_THREADS} \
    --timeout ${GUNICORN_TIMEOUT} -b 0.0.0.0:${PORT} app:app