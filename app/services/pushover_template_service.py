"""
pushover_template_service.py
---------------------------
Manages named pushover message templates editable by admins.

Storage:
  Defaults : app/defaults/pushover_templates.json  (ships with the repo, never written)
  Overrides: system/pushover_templates.json         (written only when admin saves changes,
                                                     synced to HF Dataset alongside system/)

On startup the service reads from app/defaults/pushover_templates.json.
When an admin saves changes those are written to system/pushover_templates.json
which takes priority on subsequent reads.

Currently defined templates
--------------------------
  admin_alert — sent to the admin for important system events.

Available placeholders (all templates)
-------------------------------------
  {event}      — event description
  {profile}    — profile slug or name
  {details}    — additional details
"""

import json
from app.core.config import SYSTEM_DIR, DEFAULTS_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_STORE         = SYSTEM_DIR   / "pushover_templates.json"  # admin overrides (HF-synced)
_DEFAULTS_FILE = DEFAULTS_DIR / "pushover_templates.json"  # shipped defaults (repo)


def _load_defaults() -> dict:
    try:
        return json.loads(_DEFAULTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load default pushover templates from %s: %s", _DEFAULTS_FILE, e)
        return {}


def get_all_templates() -> dict:
    defaults = _load_defaults()
    if not _STORE.exists():
        return defaults
    try:
        raw = json.loads(_STORE.read_text(encoding="utf-8"))
        merged = dict(defaults)
        merged.update(raw)
        return merged
    except Exception:
        return defaults


def save_template(name: str, data: dict) -> None:
    templates = get_all_templates()
    templates[name] = data
    _STORE.write_text(json.dumps(templates, indent=2, ensure_ascii=False), encoding="utf-8")


def restore_default(name: str) -> None:
    defaults = _load_defaults()
    if name not in defaults:
        return
    templates = get_all_templates()
    templates[name] = defaults[name]
    _STORE.write_text(json.dumps(templates, indent=2, ensure_ascii=False), encoding="utf-8")


def get_template(name: str) -> dict:
    return get_all_templates().get(name, {})
