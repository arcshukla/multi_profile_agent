#!/usr/bin/env python3
"""
hf_admin.py
-----------
Local admin utilities for AI Profile Platform.

Reads config from .env (or your shell environment).  Add new utility groups
as new top-level subcommands following the same pattern.

Usage
-----
  # ── Space build monitor ───────────────────────────────────────────────────
  python hf_admin.py space status              # check build stage once
  python hf_admin.py space watch               # poll every 30 s (Ctrl+C to stop)
  python hf_admin.py space watch --interval 10 # poll every 10 s
  python hf_admin.py space watch --until-running  # stop automatically when RUNNING

  # ── Log file operations (reads from HF Dataset repo) ─────────────────────
  python hf_admin.py logs list                     # list all log files
  python hf_admin.py logs view app.log             # print full contents
  python hf_admin.py logs view app.log --tail 50   # last 50 lines
  python hf_admin.py logs delete app.log           # delete one log file
  python hf_admin.py logs clear                    # delete ALL log files

  # ── General file operations ───────────────────────────────────────────────
  python hf_admin.py files list                    # list all files in repo
  python hf_admin.py files list profiles/          # list under a prefix
  python hf_admin.py files view system/profiles.json
  python hf_admin.py files view system/token_ledger.jsonl --tail 20
  python hf_admin.py files delete profiles/slug/docs/old.pdf

Environment variables (set in .env or shell)
--------------------------------------------
  HF_SPACE_NAME     e.g. arcshukla/ai-profile-platform   (for space commands)
  HF_STORAGE_REPO   e.g. arcshukla/profile-storage        (for logs/files commands)
  HF_TOKEN          your HuggingFace write token           (for logs/files commands)
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Load .env from the project root (same directory as this script)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv optional — env vars may already be set in shell

HF_SPACE_NAME   = os.getenv("HF_SPACE_NAME", "")    # e.g. arcshukla/ai-profile-platform
HF_STORAGE_REPO = os.getenv("HF_STORAGE_REPO", "")  # e.g. arcshukla/profile-storage
HF_TOKEN        = os.getenv("HF_TOKEN", "")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_api():
    """Return an authenticated HfApi instance, or exit with a clear error."""
    if not HF_STORAGE_REPO:
        print("ERROR: HF_STORAGE_REPO is not set.")
        print("  Add it to .env:  HF_STORAGE_REPO=your-username/profile-storage")
        sys.exit(1)
    if not HF_TOKEN:
        print("ERROR: HF_TOKEN is not set.")
        print("  Add it to .env:  HF_TOKEN=hf_xxxxxxxxxxxx")
        sys.exit(1)
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("ERROR: huggingface_hub is not installed.")
        print("  Run:  pip install huggingface_hub")
        sys.exit(1)
    return HfApi(token=HF_TOKEN)


def _list_files(api, prefix: str = "") -> list[str]:
    files = list(api.list_repo_files(repo_id=HF_STORAGE_REPO, repo_type="dataset"))
    if prefix:
        prefix = prefix.rstrip("/") + "/"
        files = [f for f in files if f.startswith(prefix)]
    return sorted(files)


def _download_text(api, path_in_repo: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        local = api.hf_hub_download(
            repo_id=HF_STORAGE_REPO,
            repo_type="dataset",
            filename=path_in_repo,
            local_dir=tmp,
        )
        return Path(local).read_text(encoding="utf-8", errors="replace")


def _ts() -> str:
    """Current time as HH:MM:SS string."""
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# space — HF Space build monitor
# ---------------------------------------------------------------------------
# Uses the public HF API (no token needed) to poll runtime.stage.
# Mirrors the PowerShell snippet:
#   while ($true) {
#       $r = Invoke-RestMethod "https://huggingface.co/api/spaces/<id>"
#       Write-Host "$(Get-Date -Format 'HH:mm:ss') — $($r.runtime.stage)"
#       Start-Sleep 30
#   }
# ---------------------------------------------------------------------------

_HF_SPACE_API = "https://huggingface.co/api/spaces/{space_id}"

# Visual stage labels — makes it easy to read at a glance
_STAGE_LABEL = {
    "RUNNING":        "✓ RUNNING",
    "BUILDING":       "⏳ BUILDING",
    "BUILD_ERROR":    "✗ BUILD_ERROR",
    "STOPPED":        "■ STOPPED",
    "PAUSED":         "‖ PAUSED",
    "SLEEPING":       "~ SLEEPING",
    "NO_APP_FILE":    "! NO_APP_FILE",
    "CONFIG_ERROR":   "! CONFIG_ERROR",
    "RUNTIME_ERROR":  "✗ RUNTIME_ERROR",
}


def _fetch_space_stage(space_id: str) -> tuple[str, str]:
    """
    Return (stage, sha) for the given HF Space.
    stage is the runtime.stage string; sha is the latest commit short hash.
    Returns ("ERROR", "") on network or parse failure.
    """
    url = _HF_SPACE_API.format(space_id=space_id)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        stage = data.get("runtime", {}).get("stage", "UNKNOWN")
        sha   = (data.get("sha") or "")[:7]
        return stage, sha
    except Exception as e:
        return f"ERROR ({e})", ""


def _resolve_space(args) -> str:
    """Return space_id from CLI arg or HF_SPACE_NAME env, or exit."""
    space_id = getattr(args, "space", None) or HF_SPACE_NAME
    if not space_id:
        print("ERROR: Space ID not set.")
        print("  Add to .env:  HF_SPACE_NAME=your-username/your-space")
        print("  Or pass:      python hf_admin.py space status --space username/space")
        sys.exit(1)
    return space_id


def cmd_space_status(args):
    """Check Space build stage once and exit."""
    space_id = _resolve_space(args)
    stage, sha = _fetch_space_stage(space_id)
    label = _STAGE_LABEL.get(stage, stage)
    sha_part = f"  (commit {sha})" if sha else ""
    print(f"{_ts()}  {space_id}  →  {label}{sha_part}")


def cmd_space_watch(args):
    """
    Poll Space build stage every --interval seconds.
    Prints a line on each poll.  Stops automatically if --until-running is set
    and stage becomes RUNNING.  Ctrl+C exits cleanly.
    """
    space_id  = _resolve_space(args)
    interval  = args.interval
    auto_stop = args.until_running

    print(f"Watching '{space_id}' every {interval}s — Ctrl+C to stop")
    if auto_stop:
        print("(will stop automatically when stage is RUNNING)")
    print()

    prev_stage = None
    try:
        while True:
            stage, sha = _fetch_space_stage(space_id)
            label      = _STAGE_LABEL.get(stage, stage)
            sha_part   = f"  [{sha}]" if sha else ""
            changed    = " ◄ changed" if (prev_stage and stage != prev_stage) else ""
            print(f"{_ts()}  {label}{sha_part}{changed}")
            prev_stage = stage

            if auto_stop and stage == "RUNNING":
                print(f"\nSpace is RUNNING — stopping watch.")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.")


# ---------------------------------------------------------------------------
# logs — log file operations against HF Dataset repo
# ---------------------------------------------------------------------------

def cmd_logs_list(api, args):
    files = _list_files(api, prefix="logs/")
    if not files:
        print("No log files found in HF Dataset.")
        return
    print(f"Log files in '{HF_STORAGE_REPO}':")
    for f in files:
        print(f"  {f}")


def cmd_logs_view(api, args):
    path_in_repo = f"logs/{args.filename}"
    try:
        content = _download_text(api, path_in_repo)
    except Exception as e:
        print(f"ERROR: Could not download '{path_in_repo}': {e}")
        sys.exit(1)
    lines = content.splitlines()
    if args.tail:
        lines = lines[-args.tail:]
    print(f"--- {path_in_repo} ({len(lines)} lines) ---")
    print("\n".join(lines))


def cmd_logs_delete(api, args):
    path_in_repo = f"logs/{args.filename}"
    confirm = input(f"Delete '{path_in_repo}' from HF Dataset? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    try:
        api.delete_file(path_in_repo=path_in_repo, repo_id=HF_STORAGE_REPO, repo_type="dataset")
        print(f"Deleted: {path_in_repo}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_logs_clear(api, args):
    files = _list_files(api, prefix="logs/")
    if not files:
        print("No log files to delete.")
        return
    print("Log files to delete:")
    for f in files:
        print(f"  {f}")
    confirm = input(f"\nDelete all {len(files)} log file(s)? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    for f in files:
        try:
            api.delete_file(path_in_repo=f, repo_id=HF_STORAGE_REPO, repo_type="dataset")
            print(f"  Deleted: {f}")
        except Exception as e:
            print(f"  ERROR deleting {f}: {e}")


# ---------------------------------------------------------------------------
# push — seed HF dataset from local data (first-time setup)
# ---------------------------------------------------------------------------

_PUSH_DIRS = ["profiles", "system"]
_SKIP_DIRS = {"chromadb", "__pycache__"}
_SKIP_EXTS = {".pyc", ".sqlite3", ".bin"}


def cmd_push_seed(api, args):
    """
    Upload local profiles/ and system/ to the HF Dataset repo.
    Skips chromadb directories and binary files.
    Use this once to seed the repo from a local dev environment.
    """
    root = Path(__file__).parent
    dirs_to_push = [root / d for d in _PUSH_DIRS if (root / d).exists()]

    if not dirs_to_push:
        print("Nothing to push — no profiles/ or system/ directories found locally.")
        return

    files_to_push = []
    for folder in dirs_to_push:
        for f in folder.rglob("*"):
            if not f.is_file():
                continue
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            if f.suffix.lower() in _SKIP_EXTS:
                continue
            files_to_push.append(f)

    if not files_to_push:
        print("No files found to push.")
        return

    print(f"Files to upload to '{HF_STORAGE_REPO}':")
    for f in files_to_push:
        print(f"  {f.relative_to(root).as_posix()}")
    print(f"\nTotal: {len(files_to_push)} file(s)")

    if not args.yes:
        confirm = input("\nPush all files? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    pushed = 0
    failed = 0
    for f in files_to_push:
        rel = f.relative_to(root).as_posix()
        try:
            api.upload_file(
                path_or_fileobj=str(f),
                path_in_repo=rel,
                repo_id=HF_STORAGE_REPO,
                repo_type="dataset",
            )
            print(f"  ✓ {rel}")
            pushed += 1
        except Exception as e:
            print(f"  ✗ {rel}: {e}")
            failed += 1

    print(f"\nDone — {pushed} uploaded, {failed} failed.")


# ---------------------------------------------------------------------------
# files — general file operations against HF Dataset repo
# ---------------------------------------------------------------------------

def cmd_files_list(api, args):
    prefix = args.prefix or ""
    files  = _list_files(api, prefix=prefix)
    label  = f"under '{prefix}'" if prefix else "in repo"
    if not files:
        print(f"No files found {label}.")
        return
    print(f"Files {label} (repo: '{HF_STORAGE_REPO}'):")
    for f in files:
        print(f"  {f}")
    print(f"\nTotal: {len(files)} file(s)")


def cmd_files_view(api, args):
    path_in_repo = args.path.lstrip("/")
    try:
        content = _download_text(api, path_in_repo)
    except Exception as e:
        print(f"ERROR: Could not download '{path_in_repo}': {e}")
        sys.exit(1)
    lines = content.splitlines()
    if args.tail:
        lines = lines[-args.tail:]
    print(f"--- {path_in_repo} ({len(lines)} lines) ---")
    print("\n".join(lines))


def cmd_files_delete(api, args):
    path_in_repo = args.path.lstrip("/")
    confirm = input(f"Delete '{path_in_repo}' from HF Dataset? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return
    try:
        api.delete_file(path_in_repo=path_in_repo, repo_id=HF_STORAGE_REPO, repo_type="dataset")
        print(f"Deleted: {path_in_repo}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser + dispatch
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Admin utilities for AI Profile Platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="group", required=True)

    # ── space ─────────────────────────────────────────────────────────────────
    space_p   = sub.add_parser("space", help="HF Space build monitor")
    space_sub = space_p.add_subparsers(dest="action", required=True)

    # shared --space flag for both space subcommands
    _space_arg = argparse.ArgumentParser(add_help=False)
    _space_arg.add_argument(
        "--space", default="",
        help="Space ID, e.g. arcshukla/ai-profile-platform (overrides HF_SPACE_NAME)",
    )

    space_sub.add_parser(
        "status",
        parents=[_space_arg],
        help="Check build stage once and exit",
    )

    sw = space_sub.add_parser(
        "watch",
        parents=[_space_arg],
        help="Poll build stage continuously (Ctrl+C to stop)",
    )
    sw.add_argument(
        "--interval", type=int, default=30, metavar="SEC",
        help="Seconds between polls (default: 30)",
    )
    sw.add_argument(
        "--until-running", action="store_true",
        help="Stop automatically when stage becomes RUNNING",
    )

    # ── logs ──────────────────────────────────────────────────────────────────
    logs_p   = sub.add_parser("logs", help="Log file operations (HF Dataset repo)")
    logs_sub = logs_p.add_subparsers(dest="action", required=True)

    logs_sub.add_parser("list", help="List all log files")

    lv = logs_sub.add_parser("view", help="Print a log file")
    lv.add_argument("filename", help="e.g. app.log")
    lv.add_argument("--tail", type=int, default=0, metavar="N", help="Last N lines only")

    ld = logs_sub.add_parser("delete", help="Delete one log file")
    ld.add_argument("filename", help="e.g. app.log")

    logs_sub.add_parser("clear", help="Delete ALL log files")

    # ── files ─────────────────────────────────────────────────────────────────
    files_p   = sub.add_parser("files", help="General file operations (HF Dataset repo)")
    files_sub = files_p.add_subparsers(dest="action", required=True)

    fl = files_sub.add_parser("list", help="List files (optionally filtered by prefix)")
    fl.add_argument("prefix", nargs="?", default="", help="e.g. profiles/ or system/")

    fv = files_sub.add_parser("view", help="Print any text file")
    fv.add_argument("path", help="e.g. system/profiles.json")
    fv.add_argument("--tail", type=int, default=0, metavar="N", help="Last N lines only")

    fd = files_sub.add_parser("delete", help="Delete a file")
    fd.add_argument("path", help="e.g. profiles/slug/docs/old.pdf")

    # ── push — seed HF dataset from local data ────────────────────────────────
    push_p = sub.add_parser("push", help="Seed HF Dataset from local profiles/ and system/")
    push_sub = push_p.add_subparsers(dest="action", required=True)
    ps = push_sub.add_parser("seed", help="Upload local data to HF Dataset (first-time setup)")
    ps.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    # ── parse + dispatch ──────────────────────────────────────────────────────
    args = parser.parse_args()

    # space commands: no HfApi needed (public HTTP), dispatch directly
    if args.group == "space":
        if args.action == "status":
            cmd_space_status(args)
        elif args.action == "watch":
            cmd_space_watch(args)
        return

    # logs / files / push commands: need authenticated HfApi
    api = _get_api()

    dispatch = {
        ("logs",  "list"):   cmd_logs_list,
        ("logs",  "view"):   cmd_logs_view,
        ("logs",  "delete"): cmd_logs_delete,
        ("logs",  "clear"):  cmd_logs_clear,
        ("files", "list"):   cmd_files_list,
        ("files", "view"):   cmd_files_view,
        ("files", "delete"): cmd_files_delete,
        ("push",  "seed"):   cmd_push_seed,
    }
    handler = dispatch.get((args.group, args.action))
    if handler:
        handler(api, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
