"""Generic in-process job queue: bounded worker threads, add/remove/kill by id."""

import threading
import time
import uuid
from collections import deque
from enum import Enum

JOB_RETENTION_SECONDS = 24 * 60 * 60


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    REMOVED = "removed"


TERMINAL_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.KILLED, JobStatus.REMOVED)


class Job:
    """One unit of work. `target(job)` runs on a worker thread and must call
    `job.finish(status)` before returning (a normal return with no explicit
    finish is treated as success). `target` should poll `job.kill_requested`
    and stop promptly - killing `job.process` itself if it owns a subprocess -
    so `JobQueue.kill` can interrupt it.
    """

    def __init__(self, job_id, target, metadata=None):
        self.id = job_id
        self.target = target
        self.metadata = metadata or {}
        self.status = JobStatus.QUEUED
        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None
        self.error = None
        self.process = None
        self._kill_requested = threading.Event()
        self._log_lines = []
        self._subscribers = []
        self._lock = threading.RLock()

    @property
    def kill_requested(self):
        return self._kill_requested.is_set()

    def log(self, line):
        with self._lock:
            self._log_lines.append(line)
            subs = list(self._subscribers)
        for q in subs:
            q.put(line)

    def get_log(self):
        with self._lock:
            return list(self._log_lines)

    def subscribe(self):
        """Return a Queue that replays past log lines then streams new ones,
        ending with a None sentinel once the job reaches a terminal status."""
        import queue as _queue
        q = _queue.Queue()
        with self._lock:
            for line in self._log_lines:
                q.put(line)
            if self.status in TERMINAL_STATUSES:
                q.put(None)
            else:
                self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def finish(self, status, error=None):
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return
            self.status = status
            self.error = error
            self.finished_at = time.time()
            subs = list(self._subscribers)
            self._subscribers.clear()
        for q in subs:
            q.put(None)

    def to_dict(self):
        with self._lock:
            return {
                "id": self.id,
                "status": self.status.value,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
            }


class JobQueue:
    def __init__(self, max_workers=2):
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self.max_workers = max_workers
        self._jobs = {}
        self._jobs_lock = threading.RLock()
        self._pending = deque()
        self._pending_lock = threading.Lock()
        self._wakeup = threading.Semaphore(0)
        self._shutdown = False
        self._workers = [
            threading.Thread(target=self._worker_loop, daemon=True, name=f"job-worker-{i}")
            for i in range(max_workers)
        ]
        for w in self._workers:
            w.start()

    def submit(self, target, metadata=None, job_id=None):
        self._prune_old_jobs()
        job = Job(job_id or str(uuid.uuid4()), target, metadata)
        with self._jobs_lock:
            self._jobs[job.id] = job
        with self._pending_lock:
            self._pending.append(job.id)
        self._wakeup.release()
        return job

    def get(self, job_id):
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._jobs_lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def queue_position(self, job_id):
        """1-based position in the pending queue, or None if not waiting."""
        with self._pending_lock:
            try:
                return self._pending.index(job_id) + 1
            except ValueError:
                return None

    def remove(self, job_id):
        """Pull a not-yet-started job out of the queue entirely."""
        job = self.get(job_id)
        if job is None:
            return False, "Job not found."
        message = "Job removed from queue before it started."
        with job._lock:
            if job.status != JobStatus.QUEUED:
                return False, f"Job is {job.status.value}, not queued; cannot remove."
            job.status = JobStatus.REMOVED
            job.finished_at = time.time()
            # Append the log line and snapshot subscribers in the same
            # locked section so a subscriber can't be cleared (or a new one
            # added) between the message and the sentinel that ends its
            # stream - otherwise a live listener misses this final line.
            job._log_lines.append(message)
            subs = list(job._subscribers)
            job._subscribers.clear()
        with self._pending_lock:
            try:
                self._pending.remove(job_id)
            except ValueError:
                pass
        for q in subs:
            q.put(message)
            q.put(None)
        return True, None

    def kill(self, job_id):
        """Terminate a currently-running job."""
        job = self.get(job_id)
        if job is None:
            return False, "Job not found."
        with job._lock:
            if job.status != JobStatus.RUNNING:
                return False, f"Job is {job.status.value}, not running; cannot kill."
            job._kill_requested.set()
            process = job.process
        if process is not None and process.poll() is None:
            process.kill()
        return True, None

    def shutdown(self):
        self._shutdown = True
        for _ in self._workers:
            self._wakeup.release()

    def _prune_old_jobs(self):
        cutoff = time.time() - JOB_RETENTION_SECONDS
        with self._jobs_lock:
            stale = [
                jid for jid, j in self._jobs.items()
                if j.status in TERMINAL_STATUSES and j.finished_at and j.finished_at < cutoff
            ]
            for jid in stale:
                del self._jobs[jid]

    def _worker_loop(self):
        while True:
            self._wakeup.acquire()
            if self._shutdown:
                return
            job_id = None
            with self._pending_lock:
                if self._pending:
                    job_id = self._pending.popleft()
            if job_id is None:
                continue
            job = self.get(job_id)
            if job is None:
                continue
            with job._lock:
                if job.status != JobStatus.QUEUED:
                    continue
                job.status = JobStatus.RUNNING
                job.started_at = time.time()
            try:
                job.target(job)
            except Exception as e:
                job.finish(JobStatus.FAILED, error=str(e))
            else:
                job.finish(JobStatus.COMPLETED)
