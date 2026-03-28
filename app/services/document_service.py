"""
document_service.py
-------------------
Manage document uploads and deletions for profiles.

Each profile stores documents at: profiles/<slug>/docs/
Supported types: PDF, TXT, CSV, DOC, DOCX, MD
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.constants import ALLOWED_DOC_EXTENSIONS
from app.core.logging_config import get_logger, get_profile_logger
from app.models.api_models import DocumentInfo, DocumentListResponse
from app.storage.file_storage import ProfileFileStorage

logger = get_logger(__name__)


class DocumentService:

    def list_documents(self, slug: str) -> DocumentListResponse:
        fs = ProfileFileStorage(slug)
        docs = []
        for path in fs.list_documents():
            stat = path.stat()
            docs.append(DocumentInfo(
                filename=path.name,
                size_bytes=stat.st_size,
                uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            ))
        return DocumentListResponse(slug=slug, documents=docs)

    def upload_document(self, slug: str, filename: str, data: bytes) -> DocumentInfo:
        """
        Save a document file. Raises ValueError for unsupported extensions.
        """
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise ValueError(f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_DOC_EXTENSIONS)}")

        fs = ProfileFileStorage(slug)
        dest = fs.save_document(filename, data)
        stat = dest.stat()

        get_profile_logger(slug).info("Document uploaded: %s (%d bytes)", filename, len(data))

        return DocumentInfo(
            filename=filename,
            size_bytes=stat.st_size,
            uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        )

    def delete_document(self, slug: str, filename: str) -> bool:
        fs = ProfileFileStorage(slug)
        result = fs.delete_document(filename)
        if result:
            get_profile_logger(slug).info("Document deleted: %s", filename)
        return result


# Singleton
document_service = DocumentService()