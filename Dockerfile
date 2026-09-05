FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache ca-certificates curl python3-dev git ffmpeg su-exec

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir \
    flask \
    spotdl

# spotdl pulls in yt-dlp transitively, capped at whatever version range
# spotdl currently declares compatible. YouTube ships extractor-breaking
# changes far more often than spotdl updates that range, so force yt-dlp
# to the newest release on every build regardless of spotdl's pin.
# --no-deps skips re-checking spotdl's declared constraint - this is what
# makes the upgrade "stick" instead of pip capping it back down.
# Roll back a build that breaks spotdl's yt-dlp API usage with:
#   docker build --build-arg YTDLP_VERSION=2026.8.19 .
ARG YTDLP_VERSION=""
RUN pip install --no-cache-dir --no-deps -U "yt-dlp${YTDLP_VERSION:+==${YTDLP_VERSION}}" \
    && yt-dlp --version

WORKDIR /app
RUN mkdir -p /app/downloads

COPY app /app
COPY web /app/web
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:5000/ || exit 1

# PUID/PGID (both default to 0/root, preserving prior behavior) let files
# written into the mounted download path come out owned by a uid/gid you
# choose, instead of always root, so another system can read them without
# a manual chown/chmod pass.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "/app/main.py"]

