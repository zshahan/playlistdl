from flask import Flask, send_from_directory, jsonify, request, Response
import subprocess
import os
import zipfile
import uuid
import shutil
import threading
import time
import re  # Add regex for capturing album/playlist name
import shlex
import queue
from urllib.parse import quote

from job_queue import JobQueue, JobStatus

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_ROOT = os.path.join(APP_ROOT, 'web')
if not os.path.isdir(STATIC_ROOT):
    STATIC_ROOT = os.path.join(os.path.dirname(APP_ROOT), 'web')

app = Flask(__name__, static_folder=STATIC_ROOT)
BASE_DOWNLOAD_FOLDER = os.getenv('BASE_DOWNLOAD_FOLDER', os.path.join(APP_ROOT, 'downloads'))
AUDIO_DOWNLOAD_PATH = os.getenv('AUDIO_DOWNLOAD_PATH', BASE_DOWNLOAD_FOLDER)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
ADMIN_DOWNLOAD_PATH = AUDIO_DOWNLOAD_PATH  # default to .env path
PORT = int(os.getenv('PORT', '5000'))
CLEANUP_INTERVAL = int(os.getenv('CLEANUP_INTERVAL', '300'))
# yt-dlp postprocessing (e.g. ffmpeg splitting a long compilation into one
# file per chapter) logs once per chapter, not continuously - so a big gap
# between lines is normal. Only treat it as stuck after this many seconds
# with zero output, rather than blocking the request forever.
DOWNLOAD_STALL_TIMEOUT = int(os.getenv('DOWNLOAD_STALL_TIMEOUT', '300'))
SSE_KEEPALIVE_INTERVAL = 15
# How often a running job's executor wakes up to notice a kill request or
# the stall timeout - independent of the SSE keepalive cadence above, which
# is about the HTTP connection rather than the job itself.
JOB_POLL_INTERVAL = 1
# How many downloads run at once; extra requests wait in the queue instead
# of all spawning spotdl/yt-dlp simultaneously.
MAX_CONCURRENT_JOBS = max(1, int(os.getenv('MAX_CONCURRENT_JOBS', '2')))

sessions = {}
job_queue = JobQueue(max_workers=MAX_CONCURRENT_JOBS)

os.makedirs(BASE_DOWNLOAD_FOLDER, exist_ok=True)

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session_id = str(uuid.uuid4())
        sessions[session_id] = username
        response = jsonify({"success": True})
        response.set_cookie('session', session_id)
        return response
    return jsonify({"success": False}), 401

def is_logged_in():
    session_id = request.cookies.get('session')
    return session_id in sessions

@app.route('/logout', methods=['POST'])
def logout():
    response = jsonify({"success": True})
    response.delete_cookie('session')  # Remove session cookie
    return response

@app.route('/check-login')
def check_login():
    is_logged_in_status = is_logged_in()
    return jsonify({"loggedIn": is_logged_in_status})


@app.route('/download')
def download_media():
    spotify_link = request.args.get('spotify_link')
    if not spotify_link:
        return jsonify({"status": "error", "output": "No link provided"}), 400

    is_admin = is_logged_in()
    job = create_download_job(spotify_link, is_admin)
    return Response(stream_job_log(job), mimetype='text/event-stream')


def build_download_command(link, temp_download_folder):
    if "spotify" in link:
        audio_providers_env = os.getenv('SPOTDL_AUDIO_PROVIDERS')
        command = ['spotdl']
        if audio_providers_env:
            providers = audio_providers_env.strip().split()
            command.extend(['--audio'] + providers)

        extra_args_env = os.getenv('SPOTDL_EXTRA_ARGS')
        if extra_args_env:
            command.extend(shlex.split(extra_args_env))

        command.extend([
            '--output', f"{temp_download_folder}/{{artist}}/{{album}}/{{title}}.{{output-ext}}",
            '--',
            link
        ])
    else:
        # Chapters (e.g. DJ mixes, compilation uploads) split into one file
        # per track via ffmpeg. Videos with no chapters produce only the
        # "default" file below - it's written to a sibling -raw folder and
        # execute_download_job() moves it into temp_download_folder only if
        # no chapter files showed up, so a single track still gets served
        # normally.
        raw_dir = f"{temp_download_folder}-raw"
        os.makedirs(raw_dir, exist_ok=True)

        command = ['yt-dlp', '-x', '--audio-format', 'mp3', '--split-chapters']

        ytdlp_extra_args_env = os.getenv('YTDLP_EXTRA_ARGS')
        if ytdlp_extra_args_env:
            command.extend(shlex.split(ytdlp_extra_args_env))

        command.extend([
            '-o', f"{raw_dir}/%(uploader)s - %(title)s.%(ext)s",
            '-o', f"chapter:{temp_download_folder}/%(uploader)s - %(title)s/%(section_number)03d - %(section_title)s.%(ext)s",
            link
        ])
    return command


def create_download_job(link, is_admin):
    """Build the download command and hand it to the job queue. The job id
    doubles as the session id for the temp download folder / download URL,
    same role the old ad-hoc session_id played."""
    session_id = str(uuid.uuid4())
    temp_download_folder = os.path.join(BASE_DOWNLOAD_FOLDER, session_id)
    os.makedirs(temp_download_folder, exist_ok=True)

    command = build_download_command(link, temp_download_folder)
    metadata = {
        "link": link,
        "is_admin": is_admin,
        "command": command,
        "temp_download_folder": temp_download_folder,
    }
    job = job_queue.submit(execute_download_job, metadata=metadata, job_id=session_id)

    if job.status == JobStatus.QUEUED:
        position = job_queue.queue_position(job.id)
        if position and position > 1:
            job.log(f"Queued for download (position {position})...")
        else:
            job.log("Waiting for a free download slot...")

    return job


def stream_job_log(job):
    """SSE stream that tails a job's log: past lines first, then live ones,
    with periodic keepalives so idle proxies don't drop the connection.
    Unlike the old request-scoped generator, closing this stream (the
    browser tab, a dropped connection) does NOT stop the job - it keeps
    running in the background and can be re-attached to or managed via the
    /jobs endpoints below."""
    yield f"event: job\ndata: {job.id}\n\n"

    log_queue = job.subscribe()
    try:
        while True:
            try:
                line = log_queue.get(timeout=SSE_KEEPALIVE_INTERVAL)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue

            if line is None:
                break

            yield f"data: {line}\n\n"
    finally:
        job.unsubscribe(log_queue)


def execute_download_job(job):
    command = job.metadata["command"]
    temp_download_folder = job.metadata["temp_download_folder"]
    is_admin = job.metadata["is_admin"]
    session_id = job.id

    album_name = None
    process = None
    try:
        print(f"🎧 Command being run: {' '.join(command)}")
        print(f"📁 Temp download folder: {temp_download_folder}")

        # stdin=DEVNULL so a tool in the chain can never block the whole
        # job by silently waiting on an interactive prompt.
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True,
        )
        job.process = process
        if job.kill_requested:
            process.kill()

        # Read the subprocess output on a background thread so this loop can
        # keep polling with a short timeout - that's what lets it notice a
        # kill request or a genuinely stuck subprocess without blocking on
        # I/O for the full stall timeout.
        line_queue = queue.Queue()

        def read_output():
            try:
                for line in process.stdout:
                    line_queue.put(line)
            finally:
                line_queue.put(None)  # sentinel: subprocess stdout closed

        threading.Thread(target=read_output, daemon=True).start()

        last_output_time = time.time()
        stalled = False
        while True:
            try:
                line = line_queue.get(timeout=JOB_POLL_INTERVAL)
            except queue.Empty:
                if job.kill_requested:
                    break
                if time.time() - last_output_time > DOWNLOAD_STALL_TIMEOUT:
                    stalled = True
                    break
                continue

            if line is None:
                break

            last_output_time = time.time()
            print(f"▶️ {line.strip()}")
            job.log(line.strip())

            # Capture album name for zipping later
            match = re.search(r'Found \d+ songs in (.+?) \(', line)
            if match:
                album_name = match.group(1).strip()

        if job.kill_requested:
            if process.poll() is None:
                process.kill()
            process.wait()
            job.log("Job killed by user request.")
            shutil.rmtree(temp_download_folder, ignore_errors=True)
            shutil.rmtree(f"{temp_download_folder}-raw", ignore_errors=True)
            job.finish(JobStatus.KILLED, error="Killed by user request.")
            return

        if stalled:
            process.kill()
            process.wait()
            error = f"Download produced no output for {DOWNLOAD_STALL_TIMEOUT}s and was aborted."
            job.log(f"Error: {error}")
            job.finish(JobStatus.FAILED, error=error)
            return

        process.wait()

        if process.returncode != 0:
            error = f"Download exited with code {process.returncode}."
            job.log(f"Error: {error}")
            job.finish(JobStatus.FAILED, error=error)
            return

        # Reconcile the yt-dlp -raw sibling folder (see build_download_command):
        # if chapters were split into temp_download_folder, discard the raw
        # whole-file copy; otherwise it's the only output, so promote it.
        raw_dir = f"{temp_download_folder}-raw"
        if os.path.isdir(raw_dir):
            has_split_files = any(
                files for _, _, files in os.walk(temp_download_folder)
            )
            if not has_split_files:
                for name in os.listdir(raw_dir):
                    shutil.move(os.path.join(raw_dir, name), os.path.join(temp_download_folder, name))
            shutil.rmtree(raw_dir, ignore_errors=True)

        # Gather all downloaded audio files
        downloaded_files = []
        for root, _, files in os.walk(temp_download_folder):
            for file in files:
                full_path = os.path.join(root, file)
                print(f"📄 Found file: {full_path}")
                downloaded_files.append(full_path)

        valid_audio_files = [f for f in downloaded_files if f.lower().endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg'))]

        if not valid_audio_files:
            error = "No valid audio files found. Please check the link."
            job.log(f"Error: {error}")
            job.finish(JobStatus.FAILED, error=error)
            return

        # ✅ ADMIN HANDLING
        if is_admin:
            for file_path in valid_audio_files:
                filename = os.path.basename(file_path)

                if 'General Conference' in filename and '｜' in filename:
                    speaker_name = filename.split('｜')[0].strip()
                    target_path = os.path.join(ADMIN_DOWNLOAD_PATH, speaker_name, filename)
                    print(f"🚚 Moving GC file to: {target_path}")
                else:
                    relative_path = os.path.relpath(file_path, temp_download_folder)
                    target_path = os.path.join(ADMIN_DOWNLOAD_PATH, relative_path)
                    print(f"🚚 Moving to default admin path: {target_path}")

                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                try:
                    shutil.move(file_path, target_path)
                except Exception as move_error:
                    print(f"❌ Failed to move {file_path} to {target_path}: {move_error}")


            shutil.rmtree(temp_download_folder, ignore_errors=True)
            job.log("Download completed. Files saved to server directory.")
            job.finish(JobStatus.COMPLETED)
            return  # ✅ Don’t try to serve/move anything else

        # ✅ PUBLIC USER HANDLING
        if len(valid_audio_files) > 1:
            zip_filename = f"{album_name}.zip" if album_name else "playlist.zip"
            zip_path = os.path.join(temp_download_folder, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in valid_audio_files:
                    arcname = os.path.relpath(file_path, start=temp_download_folder)
                    zipf.write(file_path, arcname=arcname)

            job.log(f"DOWNLOAD: {session_id}/{zip_filename}")

        else:
            relative_path = os.path.relpath(valid_audio_files[0], start=temp_download_folder)
            encoded_path = quote(relative_path)
            job.log(f"DOWNLOAD: {session_id}/{encoded_path}")

        job.finish(JobStatus.COMPLETED)

        # Schedule cleanup of the temp folder
        threading.Thread(target=delayed_delete, args=(temp_download_folder,)).start()

    except Exception as e:
        job.log(f"Error: {str(e)}")
        job.finish(JobStatus.FAILED, error=str(e))
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()


def delayed_delete(folder_path):
    time.sleep(CLEANUP_INTERVAL)
    shutil.rmtree(folder_path, ignore_errors=True)


def job_summary(job):
    summary = job.to_dict()
    summary["link"] = job.metadata.get("link")
    summary["is_admin"] = job.metadata.get("is_admin", False)
    return summary


@app.route('/jobs')
def list_jobs():
    # Listing reveals every job's id and link, which the id-scoped endpoints
    # below then let you kill/remove - so unlike those, this needs admin.
    if not is_logged_in():
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    return jsonify({"success": True, "jobs": [job_summary(j) for j in job_queue.list()]})


@app.route('/jobs/<job_id>')
def get_job(job_id):
    job = job_queue.get(job_id)
    if job is None:
        return jsonify({"success": False, "message": "Job not found"}), 404
    detail = job_summary(job)
    detail["log"] = job.get_log()
    return jsonify({"success": True, "job": detail})


@app.route('/jobs/<job_id>/stream')
def stream_job(job_id):
    job = job_queue.get(job_id)
    if job is None:
        return jsonify({"success": False, "message": "Job not found"}), 404
    return Response(stream_job_log(job), mimetype='text/event-stream')


@app.route('/jobs/<job_id>', methods=['DELETE'])
def remove_job(job_id):
    job = job_queue.get(job_id)
    if job is None:
        return jsonify({"success": False, "message": "Job not found"}), 404
    ok, message = job_queue.remove(job_id)
    if not ok:
        return jsonify({"success": False, "message": message}), 409
    temp_download_folder = job.metadata.get("temp_download_folder")
    if temp_download_folder:
        shutil.rmtree(temp_download_folder, ignore_errors=True)
        shutil.rmtree(f"{temp_download_folder}-raw", ignore_errors=True)
    return jsonify({"success": True})


@app.route('/jobs/<job_id>/kill', methods=['POST'])
def kill_job(job_id):
    job = job_queue.get(job_id)
    if job is None:
        return jsonify({"success": False, "message": "Job not found"}), 404
    ok, message = job_queue.kill(job_id)
    if not ok:
        return jsonify({"success": False, "message": message}), 409
    return jsonify({"success": True})


@app.route('/set-download-path', methods=['POST'])
def set_download_path():
    global ADMIN_DOWNLOAD_PATH
    if not is_logged_in():
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    data = request.get_json()
    new_path = data.get('path')

    if not new_path:
        return jsonify({"success": False, "message": "Path cannot be empty."}), 400

    # Optional: Validate the path, ensure it exists
    if not os.path.isdir(new_path):
        try:
            os.makedirs(new_path, exist_ok=True)
        except Exception as e:
            return jsonify({"success": False, "message": f"Cannot create path: {str(e)}"}), 500

    ADMIN_DOWNLOAD_PATH = new_path
    return jsonify({"success": True, "new_path": ADMIN_DOWNLOAD_PATH})


@app.route('/download-options')
def get_download_options():
    if not is_logged_in():
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    options_str = os.getenv('DOWNLOAD_OPTIONS', '')
    options = []
    for opt in options_str.split(','):
        opt = opt.strip()
        if not opt:
            continue
        if ':' in opt and not opt.startswith('/'):
            parts = opt.split(':', 1)
            label = parts[0].strip()
            path = parts[1].strip()
            options.append({"label": label, "path": path})
        else:
            options.append({"label": opt, "path": opt})

    return jsonify({
        "success": True,
        "options": options,
        "current_path": ADMIN_DOWNLOAD_PATH
    })


@app.route('/downloads/<session_id>/<path:filename>')
def serve_download(session_id, filename):
    session_download_folder = os.path.join(BASE_DOWNLOAD_FOLDER, session_id)
    full_path = os.path.join(session_download_folder, filename)

    print(f"📥 Requested filename: {filename}")
    print(f"📁 Resolved full path: {full_path}")

    if ".." in filename or filename.startswith("/"):
        return "Invalid filename", 400

    if not os.path.isfile(full_path):
        print("❌ File does not exist!")
        return "File not found", 404

    return send_from_directory(session_download_folder, filename, as_attachment=True)

def log_ytdlp_version():
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True, timeout=10, check=True)
        version = result.stdout.strip()
    except Exception as e:
        version = f'unknown ({e})'
    print(f"[startup] yt-dlp version: {version}", flush=True)

log_ytdlp_version()
if __name__ == '__main__':
    # threaded=True: downloads run on the job queue's own worker threads
    # regardless, but the dev server itself defaults to handling one HTTP
    # connection at a time - without this, a single open download's SSE
    # stream would block every other request (job listing, another user's
    # download) until it closed.
    app.run(host='0.0.0.0', port=PORT, threaded=True)
