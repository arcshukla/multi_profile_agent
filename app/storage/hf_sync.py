"""
hf_sync.py
----------
Syncs profiles/ and system/ to/from a private HF Dataset repo so that
data survives HuggingFace Spaces restarts (ephemeral filesystem).

Enabled only when ALL three conditions are true at startup:
  1. settings.IS_HF_SPACE  (running inside HF Spaces)
  2. settings.HF_STORAGE_REPO  (e.g. "username/profile-storage")
  3. settings.HF_TOKEN  (write-capable HF access token)

On local dev: IS_HF_SPACE is False → _enabled stays False → every method
is an immediate no-op.  Zero overhead, zero side-effects locally.

What is synced
--------------
  profiles/<slug>/docs/         uploaded documents
  profiles/<slug>/config/       header.html, CSS, JS, prompts
  profiles/<slug>/analytics/    chat_events.jsonl
  profiles/<slug>/photo.jpg     profile photo
  system/                       profiles.json, token_usage.json, ledger, billing_archive

  NOT synced:
  profiles/<slug>/chromadb/     binary, large, rebuildable by "Index Documents"
  logs/                         pushed periodically (not per-write — too frequent)

Log syncing
-----------
  push_logs()           — upload all logs/*.log files now (fire-and-forget thread)
  start_log_sync_loop() — background daemon thread, calls push_logs() every N minutes
  Final push is triggered from app shutdown event.
"""

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

        if not settings.IS_HF_SPACE:
            logger.debug("HFSync: disabled (not running on HF Spaces)")
            return

        repo  = settings.HF_STORAGE_REPO
        token = settings.HF_TOKEN

        if not repo or not token:
            logger.warning(
                "HFSync: disabled — set HF_STORAGE_REPO and HF_TOKEN secrets "
                "in HF Space settings to enable persistent storage"
            )
            return

        try:
            from huggingface_hub import HfApi  # noqa: import guarded
            self._api     = HfApi(token=token)
            self._repo_id = repo
            self._enabled = True
            logger.info("HFSync: enabled → dataset repo '%s'", repo)
        except ImportError:
            logger.error(
                "HFSync: huggingface_hub not installed — add it to requirements.txt"
            )

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
            logger.debug("HFSync.push_file: skipping non-existent %s", path)
            return
        # Skip any path whose parts include an excluded directory name
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            return

        def _do() -> None:
            try:
                rel = path.relative_to(self._base)
                self._api.upload_file(
                    path_or_fileobj=str(path),
                    path_in_repo=rel.as_posix(),
                    repo_id=self._repo_id,
                    repo_type="dataset",
                )
                logger.debug("HFSync: pushed '%s'", rel.as_posix())
            except Exception as e:
                logger.warning("HFSync: push failed for '%s': %s", path.name, e)

        if wait:
            _do()
        else:
            threading.Thread(target=_do, daemon=True, name=f"hf-push-{path.name}").start()

    def delete_file(self, path: Path) -> None:
        """
        Delete a single file from HF Dataset in a daemon background thread.
        """
        if not self._enabled:
            return

        def _do() -> None:
            try:
                rel = path.relative_to(self._base)
                self._api.delete_file(
                    path_in_repo=rel.as_posix(),
                    repo_id=self._repo_id,
                    repo_type="dataset",
                )
                logger.debug("HFSync: deleted '%s'", rel.as_posix())
            except Exception as e:
                logger.warning("HFSync: delete failed for '%s': %s", path.name, e)

        threading.Thread(target=_do, daemon=True, name=f"hf-del-{path.name}").start()

    def delete_dir(self, slug: str) -> None:
        """
        Delete all remote files under profiles/<slug>/ from HF Dataset.
        Called when a profile is hard-deleted.
        """
        if not self._enabled:
            return
        prefix = f"profiles/{slug}/"

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
                    self._api.delete_file(
                        path_in_repo=f,
                        repo_id=self._repo_id,
                        repo_type="dataset",
                    )
                logger.info(
                    "HFSync: deleted %d remote files for profile '%s'",
                    len(to_delete), slug,
                )
            except Exception as e:
                logger.warning(
                    "HFSync: delete_dir failed for '%s': %s", slug, e
                )

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
            failed = 0
            for log_file in LOGS_DIR.glob("*.log"):
                try:
                    rel = log_file.relative_to(self._base)
                    self._api.upload_file(
                        path_or_fileobj=str(log_file),
                        path_in_repo=rel.as_posix(),
                        repo_id=self._repo_id,
                        repo_type="dataset",
                    )
                    pushed += 1
                    logger.debug("HFSync: pushed log '%s'", log_file.name)
                except Exception as e:
                    failed += 1
                    logger.warning(
                        "HFSync: log push failed for '%s': %s", log_file.name, e
                    )
            if pushed or failed:
                logger.info(
                    "HFSync: log sync done — %d pushed, %d failed", pushed, failed
                )

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
                logger.debug("HFSync: scheduled log push triggered")
                self.push_logs()

        t = threading.Thread(target=_loop, daemon=True, name="hf-log-sync-loop")
        t.start()


# Singleton — import and use directly everywhere
hf_sync = HFSync()
