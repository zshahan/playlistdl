#!/bin/sh
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"

# Default stays root, matching the previous no-op behavior, so existing
# deployments that don't set PUID/PGID are unaffected.
if [ "$PUID" = "0" ] && [ "$PGID" = "0" ]; then
    exec "$@"
fi

if ! getent group "$PGID" >/dev/null 2>&1; then
    addgroup -g "$PGID" appgroup
fi
group_name=$(getent group "$PGID" | cut -d: -f1)

if ! getent passwd "$PUID" >/dev/null 2>&1; then
    adduser -D -u "$PUID" -G "$group_name" appuser
fi
user_name=$(getent passwd "$PUID" | cut -d: -f1)
home_dir=$(getent passwd "$PUID" | cut -d: -f6)

# Only the app's own scratch dir needs chowning - files land here first for
# every download (admin and public alike) before being moved or served, so
# this is what has to be writable by PUID:PGID. The mounted admin download
# path is left alone: it's user-managed and may be too large to recurse
# into on every start, and new files created inside it already end up
# owned by PUID:PGID once the app itself runs as that user below.
chown -R "$PUID:$PGID" /app/downloads

export HOME="$home_dir"
exec su-exec "$user_name:$group_name" "$@"
