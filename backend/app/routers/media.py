"""Media asset routes (spec §5.9, FR-80~84)."""
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlmodel import Session, select

from .. import errors
from ..config import settings
from ..database import get_session
from ..deps import EDIT_ROLES, get_book_for_member, get_current_user
from ..models import MediaAsset, User

router = APIRouter(prefix="/api", tags=["media"])

# Allowed extensions per category (spec FR-81)
ALLOWED = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"},
    "video": {".mp4", ".webm"},
    "audio": {".mp3", ".wav"},
    "file": {".pdf", ".docx"},
}


def _categorize(ext: str) -> str | None:
    ext = ext.lower()
    for cat, exts in ALLOWED.items():
        if ext in exts:
            return cat
    return None


def _asset_dict(a: MediaAsset, session: Session) -> dict:
    uploader = session.get(User, a.uploaded_by) if a.uploaded_by else None
    return {
        "id": a.id, "book_id": a.book_id, "type": a.type, "url": a.url,
        "filename": a.filename, "mime_type": a.mime_type, "size_bytes": a.size_bytes,
        "ref_count": a.ref_count,
        "uploaded_by": a.uploaded_by,
        "uploader_name": uploader.name if uploader else None,
        "created_at": a.created_at,
    }


@router.get("/books/{book_id}/media", response_model=None)
def list_media(
    book_id: int,
    type: str | None = None,
    search: str | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    stmt = select(MediaAsset).where(MediaAsset.book_id == book_id)
    if type:
        stmt = stmt.where(MediaAsset.type == type)
    assets = session.exec(stmt).all()
    if search:
        assets = [a for a in assets if a.filename and search.lower() in a.filename.lower()]

    # Quota usage (spec FR-82)
    used = sum(a.size_bytes or 0 for a in session.exec(
        select(MediaAsset).where(
            MediaAsset.book_id == book_id, MediaAsset.type != "link"
        )
    ).all())
    return {"data": {
        "items": [_asset_dict(a, session) for a in assets],
        "quota_used": used,
        "quota_total": settings.book_quota,
    }}


@router.post("/books/{book_id}/media", response_model=None)
async def upload_media(
    book_id: int,
    file: UploadFile | None = File(default=None),
    url: str | None = Form(default=None),
    type: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    book, membership = get_book_for_member(session, book_id, user)
    if book.deleted_at is not None:
        raise errors.not_found("書籍不存在")
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("僅擁有者或編輯者可上傳素材")

    # External link (spec §6.5: links don't count toward quota)
    if url:
        asset = MediaAsset(
            book_id=book_id, type=type or "link", url=url,
            filename=url.split("/")[-1][:200] or "link", uploaded_by=user.id,
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return {"data": {"asset": _asset_dict(asset, session)}}

    if file is None:
        raise errors.bad_request("缺少檔案或連結")

    ext = os.path.splitext(file.filename or "")[1].lower()
    cat = _categorize(ext)
    if cat is None:
        raise errors.bad_request("不支援的格式")  # spec FR-81/400

    data = await file.read()
    size = len(data)
    if size > settings.max_file_size:  # spec FR-82/413
        raise errors.payload_too_large("檔案超過 50MB 上限")

    # Book quota check (spec FR-82/413)
    used = sum(a.size_bytes or 0 for a in session.exec(
        select(MediaAsset).where(
            MediaAsset.book_id == book_id, MediaAsset.type != "link"
        )
    ).all())
    if used + size > settings.book_quota:
        raise errors.payload_too_large("書籍素材已達 1GB，請先清理")

    # Save to ./storage/{book_id}/ (spec §6.5)
    book_dir = os.path.join(settings.storage_dir, str(book_id))
    os.makedirs(book_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(book_dir, stored_name)
    with open(path, "wb") as f:
        f.write(data)

    asset = MediaAsset(
        book_id=book_id, type=cat, url=f"/storage/{book_id}/{stored_name}",
        filename=file.filename, mime_type=file.content_type, size_bytes=size,
        uploaded_by=user.id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return {"data": {"asset": _asset_dict(asset, session)}}


@router.post("/media/{asset_id}/ref", response_model=None)
def increment_ref(
    asset_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Increment ref_count when inserted into content (spec §7.7 / flow 7)."""
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise errors.not_found("素材不存在")
    book, membership = get_book_for_member(session, asset.book_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("無權限")
    asset.ref_count += 1
    session.add(asset)
    session.commit()
    return {"data": {"ref_count": asset.ref_count}}


@router.delete("/media/{asset_id}", response_model=None)
def delete_media(
    asset_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise errors.not_found("素材不存在")
    book, membership = get_book_for_member(session, asset.book_id, user)
    if membership.role not in EDIT_ROLES:
        raise errors.forbidden("無權限刪除素材")
    # Backend allows delete even if ref_count > 0 (frontend double-confirms, FR-84).
    if asset.type != "link" and asset.url.startswith("/storage/"):
        path = os.path.join(settings.storage_dir, asset.url.replace("/storage/", "", 1))
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    session.delete(asset)
    session.commit()
    return {"data": {"success": True}}
