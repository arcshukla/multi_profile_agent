"""
api/documents.py  —  Document upload / delete endpoints
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from app.models.api_models import SuccessResponse, DocumentListResponse
from app.services.document_service import document_service
from app.services.profile_service import profile_service
from app.storage.file_storage import ProfileFileStorage

router = APIRouter(prefix="/api/profiles/{slug}/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(slug: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    return document_service.list_documents(slug)


@router.post("", status_code=201)
async def upload_document(slug: str, file: UploadFile = File(...)):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    try:
        data = await file.read()
        doc = document_service.upload_document(slug, file.filename, data)
        return doc
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{filename}/view")
def view_document(slug: str, filename: str):
    """Serve a document file so the browser can open/preview it natively."""
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    fs = ProfileFileStorage(slug)
    file_path = fs.docs_dir / filename
    if not file_path.exists():
        raise HTTPException(404, f"Document '{filename}' not found")

    import mimetypes
    media_type, _ = mimetypes.guess_type(filename)
    media_type = media_type or "application/octet-stream"

    ext = filename.rsplit(".", 1)[-1].lower()
    browser_viewable = {"pdf", "txt", "md", "csv"}
    disposition = "inline" if ext in browser_viewable else "attachment"

    # Browsers have no native CSV renderer — serve as plain text so it displays inline
    if ext == "csv":
        media_type = "text/plain; charset=utf-8"

    return FileResponse(
        str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.delete("/{filename}")
def delete_document(slug: str, filename: str):
    if not profile_service.profile_exists(slug):
        raise HTTPException(404, f"Profile '{slug}' not found")
    ok = document_service.delete_document(slug, filename)
    if not ok:
        raise HTTPException(404, f"Document '{filename}' not found")
    return SuccessResponse(message=f"'{filename}' deleted")