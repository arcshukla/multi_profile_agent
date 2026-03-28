"""
chroma_manager.py
-----------------
Thin wrapper around ChromaDB that manages one persistent client per profile.

Design:
  - Each profile has its own PersistentClient at profiles/<slug>/chromadb/
  - Clients are cached in memory for the lifetime of the process
  - Thread-safe lazy initialisation
"""

import threading
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.constants import CHROMA_COLLECTION_NAME
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_clients: dict[str, chromadb.PersistentClient] = {}
_lock = threading.Lock()


def get_chroma_client(db_path: str) -> chromadb.PersistentClient:
    """
    Return (or create) a PersistentClient for the given db_path.
    Cached for process lifetime — safe for concurrent access.
    """
    with _lock:
        if db_path not in _clients:
            Path(db_path).mkdir(parents=True, exist_ok=True)
            _clients[db_path] = chromadb.PersistentClient(
                path=db_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info("Created ChromaDB client at '%s'", db_path)
        return _clients[db_path]


def get_collection(db_path: str, collection_name: str = CHROMA_COLLECTION_NAME):
    """Get or create a collection for the given db_path."""
    client = get_chroma_client(db_path)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def drop_client_cache(db_path: str) -> None:
    """Remove a client from the cache (call after deleting the chroma folder)."""
    with _lock:
        _clients.pop(db_path, None)
