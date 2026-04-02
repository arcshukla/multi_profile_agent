"""
hf_sync.py
----------
Syncs profiles/ and system/ to/from a private HF Dataset repo so that
data survives HuggingFace Spaces restarts (ephemeral filesystem).

Enabled when ALL of the following are true at startup:
  1. settings.IS_LOCAL is False  (not a local dev environment)
  2. settings.HF_STORAGE_REPO is set  (e.g. "username/profile-storage")
  3. settings.HF_TOKEN is set  (write-capable HF access token)

On local dev (IS_LOCAL=TRUE): sync is disabled → every method is an
immediate no-op.  Zero overhead, zero side-effects locally.

What is synced
--------------
  profiles/<slug>/docs/         uploaded documents
  profiles/<slug>/config/       header.html, CSS, JS, prompts
  profiles/<slug>/analytics/    chat_events.jsonl
  profiles/<slug>/photo.jpg     profile photo
  system/                       users.json, token_usage.json, ledger, billing_archive

  NOT synced:
  profiles/<slug>/chromadb/     binary, large, rebuildable by "Index Documents"
  logs/                         pushed periodically (not per-write — too frequent)

Log syncing
-----------
  push_logs()           — upload all logs/*.log files now (fire-and-forget thread)
  start_log_sync_loop() — background daemon thread, calls push_logs() every N minutes
  Final push is triggered from app shutdown event.
"""

import queue
import threading
import time
from pathlib import Path

from app.core.config import settings, BASE_DIR, LOGS_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Directory names anywhere in a path that must never be pushed to HF
_EXCLUDED_DIRS = {"chromadb"}


class HFSync:
    """
    Thin wrapper around huggingface_hub for file-level sync.

    All public methods are safe to call unconditionally — they silently
    become no-ops when sync is disabled (local dev or missing config).
    """

    def __init__(self) -> None:
        self._enabled  = False
        self._api      = None
        self._repo_id: str = ""
        self._base     = BASE_DIR
        self._upload_queue: queue.Queue = queue.Queue()
        self._worker_started = False
        # (path → (size, mtime)) snapshot after the last successful log push
        self._log_push_state: dict[str, tuple[int, float]] = {}

        repo  = settings.HF_STORAGE_REPO
        token = settings.HF_TOKEN

        logger.info(
            "HFSync init | IS_LOCAL=%s | HF_STORAGE_REPO=%s",
            settings.IS_LOCAL,
            repo or "(not set)",
        )

        if settings.IS_LOCAL:
            logger.info("HFSync: DISABLED — IS_LOCAL=TRUE, skipping HF dataset sync")
            return

        if not repo or not token:
            logger.warning(
                "HFSync: DISABLED — HF_STORAGE_REPO and HF_TOKEN must both be set "
                "to enable persistent storage. Data will be lost on restart."
            )
            return

        try:
            from huggingface_hub import HfApi  # noqa: import guarded
            self._api     = HfApi(token=token)
            self._repo_id = repo
            self._enabled = True
            logger.info("HFSync: enabled → dataset repo '%s'", repo)
            self._validate()
        except ImportError:
            logger.error(
                "HFSync: huggingface_hub not installed — add it to requirements.txt"
            )

    # ── Serial upload worker (prevents 412 concurrency conflicts) ─────────────

    def _ensure_worker(self) -> None:
        """Start the single serial upload worker thread if not already running."""
        if self._worker_started:
            return
        self._worker_started = True

        def _slug_from(repo_path: str) -> str:
            """Extract profile slug from repo path for log context.
            'profiles/<slug>/...' → '<slug>', anything else → 'system'."""
            parts = repo_path.split("/")
            return parts[1] if len(parts) >= 2 and parts[0] == "profiles" else "system"

        def _worker() -> None:
            logger.info("HFSync: upload worker started")
            while True:
                item = self._upload_queue.get()
                if item is None:
                    break
                action, *args = item
                try:
                    if action == "_call":
                        fn, = args
                        fn()
                    elif action == "upload":
                        path_str, repo_path = args
                        slug = _slug_from(repo_path)
                        self._api.upload_file(
                            path_or_fileobj=path_str,
                            path_in_repo=repo_path,
                            repo_id=self._repo_id,
                            repo_type="dataset",
                        )
                        logger.info("HFSync: pushed slug=%s path=%s", slug, repo_path)
                    elif action == "delete":
                        repo_path, = args
                        slug = _slug_from(repo_path)
                        self._api.delete_file(
                            path_in_repo=repo_path,
                            repo_id=self._repo_id,
                            repo_type="dataset",
                        )
                        logger.info("HFSync: deleted slug=%s path=%s", slug, repo_path)
                except Exception as e:
                    repo_path = args[0] if args else "?"
                    slug = _slug_from(repo_path) if isinstance(repo_path, str) else "?"
                    logger.warning(
                        "HFSync: worker error action=%s slug=%s path=%s error=%s",
                        action, slug, repo_path, e,
                    )
                finally:
                    self._upload_queue.task_done()

        threading.Thread(target=_worker, daemon=True, name="hf-upload-worker").start()

    # ── Startup validation ────────────────────────────────────────────────────

    def _validate(self) -> None:
        """
        Check read + write access to the dataset repo at startup.
        Logs clearly so Space logs show exactly what's wrong.
        Runs synchronously during __init__ — fast (single API call).
        """
        try:
            info = self._api.repo_info(repo_id=self._repo_id, repo_type="dataset")
            logger.info(
                "HFSync: repo '%s' accessible (private=%s)",
                self._repo_id, info.private,
            )
        except Exception as e:
            logger.error(
                "HFSync: CANNOT ACCESS dataset repo '%s' — check HF_TOKEN and "
                "that the repo exists. Error: %s", self._repo_id, e
            )
            self._enabled = False
            return

        # Quick write-permission check: upload a tiny sentinel file
        try:
            import io
            self._api.upload_file(
                path_or_fileobj=io.BytesIO(b"ok"),
                path_in_repo=".hfsync_writecheck",
                repo_id=self._repo_id,
                repo_type="dataset",
            )
            self._api.delete_file(
                path_in_repo=".hfsync_writecheck",
                repo_id=self._repo_id,
                repo_type="dataset",
            )
            logger.info("HFSync: write access confirmed for '%s'", self._repo_id)
        except Exception as e:
            logger.error(
                "HFSync: NO WRITE ACCESS to dataset repo '%s' — HF_TOKEN must have "
                "write permission. Sync DISABLED. Error: %s", self._repo_id, e
            )
            self._enabled = False

    # ── Startup: pull everything from HF Dataset ──────────────────────────────

    def pull(self) -> None:
        """
        Download profiles/ and system/ from HF Dataset to the local disk.

        Blocking by design — must complete before the app starts serving requests.
        Call via asyncio.run_in_executor() from the async startup event so the
        event loop is not blocked.

        On first run (empty repo) snapshot_download succeeds with zero files.
        On error: logs the problem and continues with whatever is on disk.
        """
        if not self._enabled:
            return
        try:
            from huggingface_hub import snapshot_download
            logger.info("HFSync: pulling from '%s' ...", self._repo_id)
            snapshot_download(
                repo_id=self._repo_id,
                repo_type="dataset",
                local_dir=str(self._base),
                allow_patterns=["profiles/**", "system/**"],
                ignore_patterns=["profiles/*/chromadb/**"],
                token=settings.HF_TOKEN,
            )
            logger.info("HFSync: pull complete from '%s'", self._repo_id)
        except Exception as e:
            logger.warning(
                "HFSync: pull failed — app will start with local disk state. "
                "Error: %s", e
            )

    # ── Per-write push (fire-and-forget background threads) ───────────────────

    def push_file(self, path: Path, wait: bool = False) -> None:
        """
        Upload a single file to HF Dataset.

        wait=False (default): fire-and-forget daemon thread — safe for
          frequent writes (CSS, prompts, analytics) where losing a write
          on restart is acceptable.
        wait=True: blocks until the upload completes — use for critical
          user-initiated writes (e.g. profile photo) where losing the
          file on a Space restart would be confusing.

        Silently skipped for chromadb paths or when sync is disabled.
        """
        if not self._enabled:
            return
        if not path.exists():
            logger.warning("HFSync.push_file: skipping non-existent path '%s'", path)
            return
        # Skip any path whose parts include an excluded directory name
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            return

        try:
            rel = path.relative_to(self._base)
        except ValueError:
            logger.warning("HFSync.push_file: path '%s' not relative to base", path)
            return

        self._ensure_worker()
        if wait:
            # Block until this specific item is processed
            done = threading.Event()
            def _sync_upload():
                try:
                    self._api.upload_file(
                        path_or_fileobj=str(path),
                        path_in_repo=rel.as_posix(),
                        repo_id=self._repo_id,
                        repo_type="dataset",
                    )
                    logger.debug("HFSync: pushed '%s'", rel.as_posix())
                except Exception as e:
                    logger.warning("HFSync: push failed for '%s': %s", path.name, e)
                finally:
                    done.set()
            self._upload_queue.put(("_call", _sync_upload))
            done.wait()
        else:
            self._upload_queue.put(("upload", str(path), rel.as_posix()))

    def delete_file(self, path: Path) -> None:
        """
        Delete a single file from HF Dataset via the serial upload queue.
        """
        if not self._enabled:
            return
        try:
            rel = path.relative_to(self._base)
        except ValueError:
            logger.warning("HFSync.delete_file: path '%s' not relative to base", path)
            return
        self._ensure_worker()
        self._upload_queue.put(("delete", rel.as_posix()))

    def delete_dir(self, slug: str, wait: bool = False) -> None:
        """
        Delete all remote files under profiles/<slug>/ from HF Dataset.
        Called when a profile is hard-deleted.

        wait=False (default): fire-and-forget background thread.
        wait=True: blocks until all remote files for this profile are deleted.
          Use this during hard-delete so a same-slug recreate cannot race with
          the deletion and restore old files on the next hf_sync.pull().
        """
        if not self._enabled:
            return
        prefix = f"profiles/{slug}/"
        done_event = threading.Event() if wait else None

        def _do() -> None:
            try:
                all_files = list(
                    self._api.list_repo_files(
                        repo_id=self._repo_id, repo_type="dataset"
                    )
                )
                to_delete = [f for f in all_files if f.startswith(prefix)]
                if not to_delete:
                    logger.debug("HFSync.delete_dir: no remote files for '%s'", slug)
                    return
                for f in to_delete:
                    self._upload_queue.put(("delete", f))
                logger.info(
                    "HFSync: enqueued deletion of %d remote files for profile '%s'",
                    len(to_delete), slug,
                )
            except Exception as e:
                logger.warning(
                    "HFSync: delete_dir failed for '%s': %s", slug, e
                )
            finally:
                if done_event is not None:
                    # Enqueue a sentinel after all deletes so done_event is set
                    # only after the serial worker finishes processing them.
                    self._upload_queue.put(("_call", lambda: done_event.set()))

        self._ensure_worker()
        if wait:
            _do()  # run synchronously so all deletes are enqueued before we block
            done_event.wait()
        else:
            threading.Thread(target=_do, daemon=True, name=f"hf-deldir-{slug}").start()

    # ── Log syncing ───────────────────────────────────────────────────────────

    def push_logs(self) -> None:
        """
        Upload all current *.log files in logs/ to HF Dataset.

        Fire-and-forget (background thread).  Called periodically by
        start_log_sync_loop() and once more on shutdown.
        """
        if not self._enabled:
            return

        def _do() -> None:
            pushed = 0
            skipped = 0
            failed = 0
            new_state: dict[str, tuple[int, float]] = {}
            for log_file in LOGS_DIR.glob("*.log"):
                try:
                    stat = log_file.stat()
                    key = str(log_file)
                    snapshot = (stat.st_size, stat.st_mtime)
                    if self._log_push_state.get(key) == snapshot:
                        skipped += 1
                        new_state[key] = snapshot
                        continue
                    rel = log_file.relative_to(self._base)
                    self._upload_queue.put(("upload", str(log_file), rel.as_posix()))
                    new_state[key] = snapshot
                    pushed += 1
                except Exception as e:
                    failed += 1
                    logger.warning(
                        "HFSync: log enqueue failed for '%s': %s", log_file.name, e
                    )
            self._log_push_state = new_state
            if pushed or failed:
                logger.info(
                    "HFSync: log sync enqueued — %d files, %d skipped (unchanged), %d errors",
                    pushed, skipped, failed,
                )

        self._ensure_worker()
        threading.Thread(target=_do, daemon=True, name="hf-push-logs").start()

    def start_log_sync_loop(self, interval_minutes: int = 5) -> None:
        """
        Start a persistent daemon thread that calls push_logs() every
        interval_minutes.  Safe to call multiple times — only the first
        call starts the loop.
        """
        if not self._enabled:
            return

        interval_sec = interval_minutes * 60

        def _loop() -> None:
            logger.info(
                "HFSync: log sync loop started (interval=%d min)", interval_minutes
            )
            while True:
                time.sleep(interval_sec)
                self.push_logs()

        t = threading.Thread(target=_loop, daemon=True, name="hf-log-sync-loop")
        t.start()


# Singleton — import and use directly everywhere
hf_sync = HFSync()
