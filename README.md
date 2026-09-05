# Audio Downloader

A self-hosted web application for downloading songs, albums, or playlists from Spotify and YouTube as MP3 files. The application provides a web interface for users to input links, which are then downloaded as audio files using `spotdl` (for Spotify) or `yt-dlp` (for YouTube).

## Features

- **Download Spotify and YouTube playlists**: Automatically detects and processes playlists based on the URL.
- **Chapter splitting for YouTube links**: If a YouTube video has chapters (e.g. a DJ mix or compilation upload), it's automatically split into one MP3 per chapter instead of downloaded as a single track. Videos without chapters download as before.
- **Session-based download directories**: Isolates each user session to a unique download directory.
- **Admin Mode**: Enables admin users to download directly to a specified folder on the server.
- **Progress bar and download logs**: View download progress and logs in real-time via the web interface.
- **Auto-cleanup**: Deletes temporary session download folders after a specified time.
- **Organized Downloads**: Downloads are structured by artist and album folders, maintaining organization across downloads.
<!--- **Admin mode**: Allows the admin to specify a custom directory for downloads.
-->
## Prerequisites

- **Docker** and **Docker Compose** installed on your system.

## Installation

**Run with Docker Compose**:
   Use the provided `docker-compose.yaml` configuration to start the container.
```yaml
services:
  playlistdl:
    image: ghcr.io/zshahan/playlistdl:latest
    container_name: playlistdl
    ports:
      - "4827:5000"
    environment:
      #Direct Server Download
      - ADMIN_USERNAME= #Insert unique username here!
      - ADMIN_PASSWORD= #Insert unique password here!

      - AUDIO_DOWNLOAD_PATH=${AUDIO_DOWNLOAD_PATH}  # Use the env variable
      - CLEANUP_INTERVAL=300  # Optional
      - DOWNLOAD_OPTIONS=${DOWNLOAD_OPTIONS:-}  # Optional: Comma-separated options
      - PUID=${PUID:-0}  # Optional: your host user's uid (`id -u`)
      - PGID=${PGID:-0}  # Optional: your host user's gid (`id -g`)
    volumes:
      - ${AUDIO_DOWNLOAD_PATH}:${AUDIO_DOWNLOAD_PATH}  # Reference env variable here as well


```

## Usage

1. **Access the Web Interface**:
   Open a browser and navigate to `http://localhost:5000` (replace `localhost` with your server IP if remote).

2. **Download a Playlist**:
   - Enter a Spotify or YouTube playlist URL.
   - Click **Download** to start the process.
   - Monitor download progress and logs via the interface.
3. **Admin Mode**:
   - Click the **Admin** button to log in with your credentials.
   - Once logged in, a message will appear in red indicating, "Now downloading directly to your server!"
   - Enter the playlist or album link as usual, and files will be saved to the designated admin folder on your server.
   - If `DOWNLOAD_OPTIONS` are configured, a dropdown menu allows you to choose between predefined folders. Changing the selection automatically updates and saves the path.
   - Select **Custom Path...** to display a text box where you can manually type a folder path without restarting the container. The folder can only be set to paths within the mounted directory.
<!--
3. **Admin Mode**:
   - Click the **Admin** button to log in with your credentials.
   - Once logged in, specify a custom folder name where the files will be downloaded.
-->
## Configuration

### Environment Variables

- `CLEANUP_INTERVAL`: (Optional) Sets the cleanup interval for session-based download folders (both ZIPs and single tracks). Defaults to `300` seconds (5 minutes). Stale folders older than this are also safely swept periodically by a backup job.
- `DOWNLOAD_STALL_TIMEOUT`: (Optional) Seconds of complete silence from `yt-dlp`/`spotdl` (no new log lines) before an in-progress download is aborted and reported as failed. Defaults to `300` (5 minutes). Guards against a hung download (e.g. a chapter-splitting step that gets stuck) blocking the request forever instead of surfacing an error.
- `CLEANUP_INTERVAL`: (Optional) How long a completed public download's ZIP/track stays available before its temporary folder is deleted. Defaults to `300` seconds (5 minutes).
- `ADMIN_USERNAME` and `ADMIN_PASSWORD`: (Optional) Sets the login credentials for admin access.
- `AUDIO_DOWNLOAD_PATH`: Sets the folder for admin-mode downloads. Files downloaded as an admin are stored here. This is set in your .env file.
- `DOWNLOAD_OPTIONS`: (Optional) A comma-separated list of folders for the Admin dropdown (e.g. `Pop:/media/...,Rock:/media/...`). Can contain `Label:Path` pairs or simple paths.
- `PUID` and `PGID`: (Optional) Defaults to `0`/`0` (root), matching prior behavior. Set these to a non-root uid/gid (e.g. your host user's, via `id -u`/`id -g`) and the app runs as that user, so files it writes into the mounted download path come out already owned by it instead of root - useful if another system needs to read them without you first running `chown`/`chmod`.
- `SPOTDL_AUDIO_PROVIDERS`: (Optional) Sets the audio provider(s) for spotDL (e.g. `piped`, `youtube`, `soundcloud`, `bandcamp`, `youtube-music`). Space-separated list. If you get blocked by YouTube Music, set this to `piped` or `youtube`.
- `SPOTDL_EXTRA_ARGS`: (Optional) Extra command-line arguments to pass to `spotdl` (e.g. `--dont-filter-results` to disable strict metadata/title matching filters).
- `YTDLP_EXTRA_ARGS`: (Optional) Extra command-line arguments to pass to `yt-dlp` for direct YouTube links (e.g. `--extractor-args "youtube:player_client=default,mweb"` as a workaround if YouTube breaks extraction again before the next image rebuild). Parsed with shell-style quoting, so arguments containing spaces must be quoted.
- `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET`: (Optional) If you hit Spotify's rate limits ("Your application has reached a rate/request limit"), you can create an application in the Spotify Developer Dashboard and provide your personal API credentials here to bypass the rate limits.

## Technical Overview

- **Backend**: Flask application that handles download requests and manages session-based directories.
- **Frontend**: Simple HTML/JavaScript interface for input, progress display, and log viewing.
- **Tools**:
  - `spotdl` for downloading Spotify playlists.
  - `yt-dlp` for downloading YouTube playlists as MP3s.

## Notes

- This application is intended for personal use. Make sure to follow copyright laws and only download media you’re authorized to use.
- Ensure that the `downloads` directory has appropriate permissions if running on a remote server.

## Troubleshooting

- **Permissions**: Ensure the `downloads` directory has the correct permissions for Docker to write files. If files land owned by root and another system can't read them without a manual `chown`/`chmod`, set `PUID`/`PGID` (see Environment Variables) to the uid/gid that system expects.
- **Port Conflicts**: If port 5000 is in use, adjust the port mapping in the `docker-compose.yaml` file.
- **YouTube downloads failing (e.g. "The page needs to be reloaded")**: This means `yt-dlp` has fallen behind a YouTube change. The image is rebuilt weekly (and on every push to `master`) with `yt-dlp` forced to its latest release, so `docker compose pull && docker compose up -d` usually resolves it. Check `docker logs playlistdl` at startup for the `[startup] yt-dlp version: ...` line to confirm what's actually running. If YouTube breaks something yt-dlp hasn't shipped a fix for yet, `YTDLP_EXTRA_ARGS` (see Environment Variables) lets you try a workaround without rebuilding.
- **"Error occurred while downloading" even though `docker logs playlistdl` shows the download and audio extraction finished**: The web UI shows this generic message whenever the browser's connection drops instead of receiving a clean success/error response - it doesn't necessarily mean `yt-dlp` failed. This can happen if a long chapter-splitting step (e.g. a large DJ mix/compilation with many chapters) goes quiet for a while and something between the browser and the server (a reverse proxy, VPN, etc.) times out the idle connection. The app now sends periodic keepalives during silent stretches and only gives up (killing the download and reporting a clear error) after `DOWNLOAD_STALL_TIMEOUT` seconds of true silence, so a genuine hang is reported explicitly instead of just going quiet. If you still hit this, check the container logs for what actually happened around that time.

## Support This Project

If you like this project, consider supporting it with a donation!

[![Donate via Stripe](https://img.shields.io/badge/Donate-Stripe-blue?style=flat&logo=stripe)](https://buy.stripe.com/6oEdU3dWS19C556dQQ)

