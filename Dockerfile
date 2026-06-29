FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache ca-certificates curl python3-dev git ffmpeg

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ✅ Pin pytube + install yt-dlp
RUN pip install --no-cache-dir \
    flask \
    spotdl \
    pytube==15.0.0 \
    yt-dlp

WORKDIR /app
RUN mkdir -p /app/downloads

COPY app /app
COPY web /app/web

EXPOSE 5000
CMD ["python3", "/app/main.py"]

