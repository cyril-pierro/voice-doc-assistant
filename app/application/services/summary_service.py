"""
app/application/services/summary_service.py — Session Summary Worker

Implements the post-session cleanup routine per spec:
- Triggered on WebSocket disconnect via FastAPI BackgroundTasks
- Packages transcribed interaction logs
- Logs a mock SMTP mailing block to the user's validated email

Clean Architecture: Application service — depends on nothing infrastructure-specific.
The "SMTP" is a structured log (no real sending) to keep the prototype free-tier and secure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("voice-doc-assistant")


# In-memory transcript store — per-session logs
# Keyed by session_id or user email + timestamp
_TRANSCRIPT_STORE: dict[str, list[dict[str, Any]]] = {}


def append_transcript(session_key: str, role: str, text: str) -> None:
    """Append a turn to the transcript store. Called from WS handlers."""
    if not text or not text.strip():
        return
    entry = {
        "role": role,
        "text": text[:2000],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _TRANSCRIPT_STORE.setdefault(session_key, []).append(entry)
    # Keep last 50 turns per session to bound memory
    if len(_TRANSCRIPT_STORE[session_key]) > 50:
        _TRANSCRIPT_STORE[session_key] = _TRANSCRIPT_STORE[session_key][-50:]


def get_transcript(session_key: str) -> list[dict[str, Any]]:
    return list(_TRANSCRIPT_STORE.get(session_key, []))


def clear_transcript(session_key: str) -> None:
    _TRANSCRIPT_STORE.pop(session_key, None)


def _build_summary_text(
    username: str,
    email: str,
    voice_gender: str,
    session_id: str,
    transcript: list[dict[str, Any]],
    started_at: str | None = None,
    custom_ai_name: str | None = None,
) -> str:
    started = started_at or datetime.now(timezone.utc).isoformat()
    ended = datetime.now(timezone.utc).isoformat()
    ai_name = custom_ai_name or "Aria"
    lines = [
        f"Voice-Doc Assistant — Session Summary (as {ai_name})",
        f"===================================",
        f"User: {username} <{email}>",
        f"Assistant: {ai_name} ({'Aoede (female)' if voice_gender == 'female' else 'Charon (male)'} — {voice_gender})",
        f"Session: {session_id}",
        f"Started: {started}",
        f"Ended: {ended}",
        f"Turns: {len(transcript)}",
        f"",
        f"Transcript:",
        f"---------",
    ]
    if not transcript:
        lines.append("(No transcribed turns — user connected but did not speak.)")
    else:
        for i, turn in enumerate(transcript, 1):
            who = turn["role"].upper()
            lines.append(f"{i}. [{who} @ {turn['timestamp']}] {turn['text']}")
    lines.extend([
        f"",
        f"Documents in context at disconnect: (see /api/documents?email={email})",
        f"Vector: pgvector / in-memory • LangChain splitter",
        f"",
        f"— End of summary —",
    ])
    return "\n".join(lines)


def send_summary_email(
    username: str,
    email: str,
    voice_gender: str = "female",
    session_id: str = "unknown",
    transcript: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
    custom_ai_name: str | None = None,
) -> None:
    """
    Mock SMTP mailing block — logs to stdout / logger.

    Spec: "package a summary of the transcribed interaction logs and
    print/log a mock SMTP mailing block to sending the data to the user's
    validated email."

    In production, replace the logger block with:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["To"] = email
        msg["Subject"] = f"Your Voice-Doc session {session_id}"
        msg.set_content(summary_text)
        with smtplib.SMTP(os.getenv("SMTP_HOST"), 587) as s:
            s.starttls()
            s.login(...)
            s.send_message(msg)
    """
    if transcript is None:
        # Try to fetch from store by session_id or email
        transcript = get_transcript(session_id) or get_transcript(email) or []

    summary_text = _build_summary_text(username, email, voice_gender, session_id, transcript, started_at, custom_ai_name)
    ai_name = custom_ai_name or "Aria"

    # === MOCK SMTP BLOCK — structured log per spec ===
    # This is the "automated conversation summary" the frontend subtitle promised.
    smtp_block = f"""
================================================================================
[MOCK SMTP] To: {email}
[MOCK SMTP] From: noreply@voice-doc-assistant.local
[MOCK SMTP] Subject: Your Voice-Doc Summary — Session {session_id} with {ai_name}
[MOCK SMTP] X-Voice-Gender: {voice_gender} (Aoede/Charon)
[MOCK SMTP] X-User: {username}
[MOCK SMTP] X-AI-Name: {ai_name}
--------------------------------------------------------------------------------
{summary_text}
--------------------------------------------------------------------------------
[MOCK SMTP] Status: Queued (mock) — in production this would be sent via SMTP_HOST
================================================================================
"""
    # Log via logger (captured by Phoenix console) and print (visible in Render logs)
    logger.info(smtp_block)
    print(smtp_block, flush=True)

    # Optionally clear transcript after sending to bound memory
    # Keep email-keyed transcript for 5 minutes for debugging, then clear
    # For now, clear immediately to avoid leak
    clear_transcript(session_id)
    # Don't clear email-keyed if different
    if session_id != email:
        clear_transcript(email)


# Helper for WS handlers to record transcripts without importing store directly
__all__ = ["append_transcript", "get_transcript", "clear_transcript", "send_summary_email"]
