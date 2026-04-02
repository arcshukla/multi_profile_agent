"""
constants.py
------------
Application-wide constants. No logic — pure values.
"""

from pathlib import PurePosixPath

# ── Profile directory layout (relative to profile base folder) ──────────────
PROFILE_DOCS_DIR     = "docs"
PROFILE_CHROMADB_DIR = "chromadb"
PROFILE_CONFIG_DIR   = "config"
PROFILE_PHOTO_FILE   = "photo.jpg"
PROFILE_PROMPTS_FILE = "config/prompts.py"
PROFILE_HEADER_FILE  = "config/header.html"
PROFILE_SLIDES_FILE  = "config/slides.json"
PROFILE_CSS_FILE     = "config/profile.css"
PROFILE_JS_FILE      = "config/profile.js"

# ── Allowed document extensions for upload ───────────────────────────────────
ALLOWED_DOC_EXTENSIONS = {".pdf", ".txt", ".csv", ".doc", ".docx", ".md"}

# ── Document upload limits ────────────────────────────────────────────────────
MAX_FILE_SIZE_PDF   = 5 * 1024 * 1024   # 5 MB  — PDFs can be larger
MAX_FILE_SIZE_OTHER = 1 * 1024 * 1024   # 1 MB  — TXT, CSV, DOCX, MD
MAX_DOCS_PER_PROFILE = 3                 # max files per profile

# ── Profile statuses ─────────────────────────────────────────────────────────
STATUS_ENABLED  = "enabled"
STATUS_DISABLED = "disabled"
STATUS_DELETED  = "deleted"
STATUS_INDEXING = "indexing"

# ── Index history entry statuses ─────────────────────────────────────────────
INDEX_STATUS_SUCCESS = "success"
INDEX_STATUS_FAILED  = "failed"
INDEX_STATUS_RUNNING = "running"

# ── ChromaDB collection name (per-profile) ───────────────────────────────────
CHROMA_COLLECTION_NAME = "profile_docs"

# ── Default profile topics (same taxonomy as existing app) ───────────────────
DEFAULT_PROFILE_TOPICS = [
    "contact",
    "summary",
    "experience",
    "education",
    "skills",
    "awards",
    "recommendations",
    "other",
]

# ── Pagination ────────────────────────────────────────────────────────────────
ADMIN_PAGE_SIZE = 20

# ── Chat history window (turns kept in context) ───────────────────────────────
CHAT_HISTORY_WINDOW = 4

# ── Notification event types ─────────────────────────────────────────────────
EVENT_LEAD       = "lead"
EVENT_UNKNOWN    = "unknown_question"
EVENT_CHAT       = "chat"
