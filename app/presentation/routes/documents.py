"""
app/presentation/routes/documents.py — Document HTTP endpoints

Thin controllers: validate input, delegate to DocumentService, return DTOs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from pydantic import BaseModel

from app.application.services.document_service import document_service
from app.application.services.summary_service import send_summary_email
from app.presentation.dependencies.auth import SessionUser, get_session_user

logger = logging.getLogger("voice-doc-assistant")

router = APIRouter(prefix="/api", tags=["documents"])


@router.post("/upload")
@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    user: SessionUser = Depends(get_session_user),
):
    """Accept document upload, extract text, chunk, and store in memory.
    Auth via JWT Bearer (sessionStorage) if present, else guest fallback.
    No ?username/&email query params — identity comes from JWT when available."""
    result = await document_service.upload(file, user)
    logger.info(f"Uploaded {result['filename']} ({result['size_bytes']} bytes, {result['chunks']} chunks) for {user.email}")
    return result


@router.get("/documents")
async def list_documents(user: SessionUser = Depends(get_session_user)):
    """List documents — uses Bearer token if present, else guest (no ?username/&email)."""
    return document_service.list_for_user(user)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: SessionUser = Depends(get_session_user)):
    return document_service.delete(doc_id, user)


class SummaryRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    custom_ai_name: str | None = None
    voice_gender: str | None = None
    send_summary: bool = True


@router.post("/session/summary")
async def trigger_session_summary(
    payload: SummaryRequest,
    background_tasks: BackgroundTasks,
    user: SessionUser = Depends(get_session_user),
):
    """
    Explicit summary trigger — called when user says 'yes' to summary prompt.
    Spec: If user says yes then send, if no don't. Either way end session.
    This endpoint is the REST fallback for the WS summary_choice flow.
    Frontend calls this when user clicks 'Yes, send it' in the modal.
    """
    # Use authenticated user as source of truth, but allow payload override for flexibility
    target_email = (payload.email or user.email).lower().strip()
    target_username = payload.username or user.username
    # Find the actual user record to get custom_ai_name and gender if not provided
    custom_name = payload.custom_ai_name
    voice_gender = payload.voice_gender
    if not custom_name or not voice_gender:
        try:
            from app.core.database import AsyncSessionLocal, User
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(User).where(User.email == target_email))
                db_user = result.scalars().first()
                if db_user:
                    custom_name = custom_name or db_user.custom_ai_name
                    voice_gender = voice_gender or db_user.preferred_ai_gender
        except Exception:
            pass
    custom_name = custom_name or "Aria"
    voice_gender = (voice_gender or "female").lower()

    if not payload.send_summary:
        logger.info(f"Summary explicitly declined for {target_email} (via REST)")
        return {"message": "Summary not sent — session will end without email", "sent": False}

    # Queue the mock SMTP via BackgroundTasks (FastAPI idiomatic)
    background_tasks.add_task(
        send_summary_email,
        username=target_username,
        email=target_email,
        voice_gender=voice_gender,
        session_id=f"rest-{target_username}",
        custom_ai_name=custom_name,
    )
    logger.info(f"Explicit summary requested for {target_email} via REST — queued mock SMTP as {custom_name} ({voice_gender})")
    return {"message": f"Summary will be sent to {target_email}", "sent": True, "to": target_email, "as": custom_name}
