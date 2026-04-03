"""
conftest.py
-----------
Shared fixtures for the multiprofile test suite.

Every test gets a fully isolated, empty temp directory tree via pytest's
tmp_path fixture.  No test ever reads from or writes to the real
system/, profiles/, logs/, or static/ directories.

Strategy
--------
Services bind their storage paths at *import time* as module-level constants
(e.g. ``_STORE = SYSTEM_DIR / "billing.json"``).  Simply patching
``app.core.config.SYSTEM_DIR`` is not enough because the constant in each
service module is already a separate object.  We must therefore patch both:

  1. The ``app.core.config`` attribute  (covers code that reads it lazily)
  2. The already-bound name inside each service module  (covers code that
     captured it at import time via ``from app.core.config import X``)

After each test monkeypatch reverts every patch automatically — nothing leaks
between tests, and the real data directories are never touched.
"""

import pytest


@pytest.fixture(autouse=True)
def isolate_data_dirs(tmp_path, monkeypatch):
    """
    Redirect all service file-I/O to a fresh temporary directory tree.
    Applied automatically before every test; reverted automatically after.
    """
    system_dir   = tmp_path / "system"
    profiles_dir = tmp_path / "profiles"
    static_dir   = tmp_path / "static"
    logs_dir     = tmp_path / "logs"

    for d in (system_dir, profiles_dir, static_dir, logs_dir):
        d.mkdir()

    # ── 1. Patch app.core.config module attributes ────────────────────────────
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "SYSTEM_DIR",   system_dir)
    monkeypatch.setattr(cfg, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(cfg, "STATIC_DIR",   static_dir)
    monkeypatch.setattr(cfg, "LOGS_DIR",     logs_dir)

    # Settings object attributes used as file paths
    monkeypatch.setattr(cfg.settings, "BILLING_FILE",        system_dir / "billing.json")
    monkeypatch.setattr(cfg.settings, "TOKEN_LEDGER_FILE",   system_dir / "token_ledger.jsonl")
    monkeypatch.setattr(cfg.settings, "INDEX_HISTORY_FILE",  system_dir / "index_history.log")
    monkeypatch.setattr(cfg.settings, "BILLING_ARCHIVE_DIR", system_dir / "billing_archive")

    # ── 2. Patch already-bound names inside each service/storage module ───────

    # token_service
    import app.services.token_service as ts
    monkeypatch.setattr(ts, "_STORE",  system_dir / "token_usage.json")
    monkeypatch.setattr(ts, "_LEDGER", system_dir / "token_ledger.jsonl")

    # billing_service
    import app.services.billing_service as bs
    monkeypatch.setattr(bs, "_STORE",  system_dir / "billing.json")
    monkeypatch.setattr(bs, "_QR_DIR", static_dir / "qr")

    # user_service
    import app.services.user_service as us
    monkeypatch.setattr(us, "_USERS_FILE", system_dir / "users.json")

    # email_template_service  (override store; defaults file stays as repo copy)
    import app.services.email_template_service as ets
    monkeypatch.setattr(ets, "_STORE", system_dir / "email_templates.json")

    # llm_prompts_service
    import app.services.llm_prompts_service as lps
    monkeypatch.setattr(lps, "_STORE", system_dir / "llm_prompts.json")

    # pushover_template_service
    import app.services.pushover_template_service as pts
    monkeypatch.setattr(pts, "_STORE", system_dir / "pushover_templates.json")

    # file_storage — PROFILES_DIR was captured via `from ... import` at module load
    import app.storage.file_storage as fs_mod
    monkeypatch.setattr(fs_mod, "PROFILES_DIR", profiles_dir)

    # log_service — LOGS_DIR was captured via `from ... import` at module load
    import app.services.log_service as ls
    monkeypatch.setattr(ls, "LOGS_DIR", logs_dir)

    # ── 3. Silence HFSync — never push/pull during tests ─────────────────────
    import app.storage.hf_sync as hf
    monkeypatch.setattr(hf.hf_sync, "push_file",   lambda *a, **kw: None)
    monkeypatch.setattr(hf.hf_sync, "delete_dir",  lambda *a, **kw: None)
    monkeypatch.setattr(hf.hf_sync, "pull",        lambda *a, **kw: None)

    yield tmp_path
