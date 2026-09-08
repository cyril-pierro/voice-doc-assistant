"""
app/presentation/websockets/stream.py — WebSocket endpoint

Presentation adapter for the realtime voice stream.
Delegates actual provider bridging to infrastructure/realtime factory.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from starlette.background import BackgroundTasks

from app.config import get_settings
from app.infrastructure.realtime.factory import get_provider
from app.presentation.dependencies.auth import resolve_user_from_ws

logger = logging.getLogger("voice-doc-assistant")
settings = get_settings()

router = APIRouter(tags=["realtime"])


class WSMessageType:
    """Protocol message types exchanged with the frontend over /ws/stream."""

    AUDIO_CHUNK = "audio_chunk"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    ERROR = "error"


@router.websocket("/ws/stream")
async def ws_stream(
    websocket: WebSocket,
    username: str | None = Query(default=None),
    email: str | None = Query(default=None),
    token: str | None = Query(default=None, description="JWT bearer token (preferred) — JS WebSocket passes ?token=<jwt> since headers not available"),
    voice_gender: str | None = Query(
        default=None, description="AI voice gender: male/female — maps to Aoede/Charon"),
    language: str | None = Query(
        default=None, description="Response language: english, indian/hindi, spanish, japanese, french, korean — default english"),
):
    """
    Main bidirectional voice stream — premium stateful onboarding.
    Auth via JWT bearer token (?token=<jwt> or Authorization: Bearer <jwt> or cookie) — primary,
    fallback to query params `?username=...&email=...` for guest/dev mode.
    Reads `voice_gender` (Aoede for female, Charon for male) and `username`
    to inject dynamic system instruction + voice profile into `client.aio.live.connect`.
    On disconnect, triggers a BackgroundTasks post-session summary worker that
    mock-SMTPs the transcript to the user's email.
    """
    # Use BackgroundTasks for WS — FastAPI idiomatic spec requires BackgroundTasks
    # For WebSocket we create it manually (not injected via HTTP dependency)
    background_tasks = BackgroundTasks()

    # ------------------------------------------------------------------
    # WebSocket Session Hydration — per spec: load user record from DB
    # based on active session, extract username, preferred_ai_gender,
    # custom_ai_name. This makes the AI stay in character as custom_ai_name
    # and address the user properly by username.
    # ------------------------------------------------------------------
    user = resolve_user_from_ws(websocket, username=username, email=email, token=token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing or invalid bearer token / username")
        return

    # Log auth type for observability
    logger.info(f"WS auth resolved: {user.username} <{user.email}> token_type={getattr(user, 'token_type', 'unknown')} token_via={'?token' if token else 'header/cookie/query'}")

    # Try to hydrate from DB — overrides query params with persisted profile
    # Uses email as key (unique, indexed) or JWT bearer token if present
    db_user = None
    db_custom_ai_name: str | None = None
    try:
        # Extract bearer token from all possible WS locations (priority: ?token > header > cookie)
        session_token = token or websocket.query_params.get("token")
        if not session_token:
            session_token = websocket.cookies.get("session_token")
        if not session_token:
            auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                session_token = auth[7:].strip()
            elif auth.count(".") == 2:
                session_token = auth.strip()

        # Try to load user from DB by email (primary) or by session token
        from app.core.database import get_db as _get_db
        from sqlalchemy import select as _select

        # We need to open a DB session manually since WebSocket has no Depends(get_db)
        # Use the async session factory directly
        from app.core.database import AsyncSessionLocal, User as _User

        # First, try JWT/session token lookup if present (more secure)
        if session_token:
            try:
                # Prefer JWT claims — stateless verification
                from app.auth.jwt import get_user_claims_from_any_token

                claims = get_user_claims_from_any_token(session_token)
                if claims and claims.get("email"):
                    email_for_lookup = claims["email"]
                    async with AsyncSessionLocal() as _db:
                        result = await _db.execute(_select(_User).where(_User.email == email_for_lookup.lower().strip()))
                        db_user = result.scalars().first()
                        if db_user:
                            logger.info(
                                f"Hydrated WS user from JWT/session_token: {db_user.username} <{db_user.email}> ai={db_user.custom_ai_name} gender={db_user.preferred_ai_gender} via={claims.get('token_type')}")
                # Fallback legacy direct store lookup (if JWT path missed)
                if db_user is None:
                    from app.auth.routes import get_session_by_token as _get_session

                    sess = _get_session(session_token)
                    if sess and sess.get("email"):
                        email_for_lookup = sess["email"]
                        async with AsyncSessionLocal() as _db:
                            result = await _db.execute(_select(_User).where(_User.email == email_for_lookup.lower().strip()))
                            db_user = result.scalars().first()
                            if db_user:
                                logger.info(
                                    f"Hydrated WS user from session_token (legacy): {db_user.username} <{db_user.email}> ai={db_user.custom_ai_name} gender={db_user.preferred_ai_gender}")
            except Exception as _e:
                logger.debug(f"Session token hydration failed: {_e}")

        # Fallback: lookup by email from query params / headers
        if db_user is None and user.email and user.email != "guest@example.com":
            try:
                async with AsyncSessionLocal() as _db:
                    result = await _db.execute(_select(_User).where(_User.email == user.email.lower().strip()))
                    db_user = result.scalars().first()
                    if db_user:
                        logger.info(
                            f"Hydrated WS user from DB by email: {db_user.username} <{db_user.email}> ai={db_user.custom_ai_name}")
            except Exception as _e:
                logger.debug(
                    f"DB hydration by email failed: {_e} — using query param user")

        # If DB user found, override the WebSocket user with DB values (enterprise single source of truth)
        if db_user:
            # Use DB's authoritative username, gender, custom name
            user.username = db_user.username
            user.email = db_user.email
            db_custom_ai_name = db_user.custom_ai_name
            # Override voice_gender with DB's preferred_ai_gender if not explicitly passed
            # Spec: preferred_ai_gender drives Aoede/Charon
            if not voice_gender or voice_gender.lower() not in ("male", "female"):
                # No explicit voice_gender from frontend — use DB preference
                voice_gender = db_user.preferred_ai_gender
    except Exception as _e:
        logger.warning(f"DB hydration skipped (using query param user): {_e}")

    # Normalize voice_gender per spec (now potentially from DB)
    voice_gender_norm = (voice_gender or websocket.query_params.get(
        "voice_gender") or "female").lower()
    if voice_gender_norm not in ("female", "male"):
        voice_gender_norm = "female"
    # If DB user overrode, ensure we use DB preference
    if db_user and db_user.preferred_ai_gender:
        db_gender = db_user.preferred_ai_gender.lower()
        if db_gender in ("male", "female"):
            # DB is authoritative — unless frontend explicitly sent a different gender, use DB
            # For now, respect DB as primary (enterprise personalization)
            # If frontend sent explicit gender that differs, log it but prefer DB
            if voice_gender and voice_gender.lower() != db_gender:
                logger.info(
                    f"Voice gender mismatch: frontend={voice_gender} vs DB={db_gender} — using DB preference per spec")
            voice_gender_norm = db_gender
    voice_name = "Aoede" if voice_gender_norm == "female" else "Charon"
    custom_ai_name = db_custom_ai_name or getattr(
        db_user, "custom_ai_name", None) or "Aria"

    # Language handling — user-selectable via Select Language menu on main page (default english)
    # Supported: english (en-US, default), indian/hindi (hi-IN), spanish (es-ES), japanese (ja-JP), french (fr-FR), korean (ko-KR)
    language_norm = (language or websocket.query_params.get(
        "language") or "english").lower().strip()
    # Also check language param that frontend sends as `language` query
    if language_norm not in ("english", "indian", "hindi", "spanish", "japanese", "french", "korean", "en", "hi", "es", "ja", "fr", "ko"):
        language_norm = "english"
    # Normalize aliases: hi -> indian, es -> spanish, etc.
    _lang_alias = {"hindi": "indian", "hi": "indian", "es": "spanish",
                   "ja": "japanese", "fr": "french", "ko": "korean", "en": "english"}
    language_norm = _lang_alias.get(language_norm, language_norm)
    # Attach to user object for providers to read (clean way without changing provider signature too much)
    try:
        user.language = language_norm  # type: ignore
        user.preferred_language = language_norm  # type: ignore
    except Exception:
        pass

    # Session tracking for summary worker
    import time
    import uuid

    session_id = uuid.uuid4().hex[:8]
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Seed transcript store with system event
    try:
        from app.application.services.summary_service import append_transcript

        append_transcript(
            session_id, "system", f"Session started — user={user.username} <{user.email}> voice={voice_name} ({voice_gender_norm}) docs via LangChain+pgvector")
        append_transcript(user.email, "system",
                          f"Session {session_id} started")
    except Exception:
        pass

    await websocket.accept()
    # FSM hook: immediately inform frontend of LISTENING state (blue wave) — instant mount
    try:
        await websocket.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai", "detail": "ready for user input"})
    except: pass
    # Use fresh settings for provider log (module-level settings may be stale due to .env caching)
    _fresh = get_settings()
    logger.info(
        f"WS /ws/stream connected: {user.username} <{user.email}> "
        f"provider={_fresh.resolved_provider} voice={voice_name} ({voice_gender_norm}) session={session_id}"
    )

    # Also inform client of the chosen voice + custom AI name (personalized greeting per spec)
    try:
        await websocket.send_json({
            "type": "text",
            "text": f"Hi {user.username}! I'm {custom_ai_name} ({voice_name}, {voice_gender_norm}) — your personalized document assistant. How can I help?",
            "voice": voice_name,
            "custom_ai_name": custom_ai_name,
        })
    except Exception:
        pass
    # Ensure client transitions to LISTENING after greeting
    try: await websocket.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai"}) 
    except: pass

    # Track whether user wants summary — per new spec, only send if user says yes
    # The frontend will send {type: "summary_choice", want_summary: true/false} when user clicks Yes/No
    # or when AI asks and user responds via voice. Default is None (no choice yet) -> don't auto-send.
    # We store the choice in a mutable dict so the provider and finally block can share it.
    # None = no choice yet, True/False after user decides
    summary_choice: dict = {"want_summary": None}

    # Wrap the websocket to intercept summary_choice messages without breaking provider
    original_receive = websocket.receive_text

    async def _intercept_receive():
        while True:
            data = await original_receive()
            try:
                import json as _json
                msg = _json.loads(data)
                if isinstance(msg, dict):
                    if msg.get("type") == "summary_choice":
                        want = bool(msg.get("want_summary"))
                        summary_choice["want_summary"] = want
                        logger.info(
                            f"Summary choice from {user.email}: {want} (via WS summary_choice)")
                        try:
                            await websocket.send_json({"type": "text", "text": f"Summary choice received: {'yes, will send to ' + user.email if want else 'no, ending without summary'}"})
                        except Exception:
                            pass
                        continue
                    if msg.get("type") == "state":
                        # FSM hook: frontend reports LISTENING/THINKING_SPEAKING state for observability
                        st = msg.get("state") or msg.get("fsm")
                        logger.info(f"Client FSM state from {user.email}: {st} (speaker={msg.get('speaker')})")
                        # Don't forward state to upstream — it's a telemetry control message
                        # Optionally echo back server's view of state
                        try: await websocket.send_json({"type": "state", "state": "ack", "fsm": st})
                        except: pass
                        continue
            except Exception:
                pass
            return data

    # Monkey-patch for the duration of the session
    websocket.receive_text = _intercept_receive  # type: ignore

    try:
        provider = get_provider()
        # Pass voice_gender + custom_ai_name + username-aware provider — supports both new and legacy signatures
        # Spec: Inject username, preferred_ai_gender, custom_ai_name into Gemini Live session
        try:
            await provider.handle(websocket, user, voice_gender=voice_gender_norm, custom_ai_name=custom_ai_name)
        except TypeError:
            try:
                await provider.handle(websocket, user, voice_gender=voice_gender_norm)
            except TypeError:
                await provider.handle(websocket, user)
    except WebSocketDisconnect:
        logger.info(
            f"WS disconnect: {user.email} session={session_id} summary_choice={summary_choice['want_summary']}")
    except Exception as exc:
        logger.exception(
            f"WS unhandled error for {user.email} session={session_id}: {exc}")
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=str(exc)[:120])
        except Exception:
            pass
    finally:
        # ------------------------------------------------------------------
        # Automated Session Summary Worker — CONDITIONAL per new spec
        # Spec: When user ends session, AI asks "Would you like a summary...?"
        # If user says yes -> send, if no -> don't. Either way end session.
        # We only send if summary_choice is explicitly True. If None (user just
        # closed browser without choosing), we do NOT auto-send to avoid spam.
        # ------------------------------------------------------------------
        if summary_choice["want_summary"] is not True:
            logger.info(
                f"Summary NOT sent for {user.email} session={session_id} — user declined or no choice (want_summary={summary_choice['want_summary']})")
            # Still clean up transcript store without sending
            try:
                from app.application.services.summary_service import clear_transcript
                clear_transcript(session_id)
                clear_transcript(user.email)
            except Exception:
                pass
        else:
            from app.application.services.summary_service import send_summary_email

            # BackgroundTasks is the FastAPI-idiomatic way — works for HTTP and WS
            # For WS we manually schedule it since the connection is already closing
            # Include custom_ai_name so summary shows which persona was used
            background_tasks.add_task(
                send_summary_email,
                username=user.username,
                email=user.email,
                voice_gender=voice_gender_norm,
                session_id=session_id,
                started_at=started_at,
                custom_ai_name=custom_ai_name,
            )
            # Trigger the background tasks without blocking the WS close
            # In HTTP, FastAPI does this automatically after response; for WS we await it
            # but don't let failures break the disconnect flow
            # try:
            #     await background_tasks()
            # except Exception as bg_exc:
            #     logger.warning(
            #         f"Background summary worker failed for {user.email}: {bg_exc}")
            #     # Fallback: direct call (still logs mock SMTP)
            #     try:
            #         send_summary_email(
            #             username=user.username,
            #             email=user.email,
            #             voice_gender=voice_gender_norm,
            #             session_id=session_id,
            #             started_at=started_at,
            #         )
            #     except Exception:
            #         pass
            # logger.info(
            #     f"Background summary worker queued for {user.email} session={session_id} -> {user.email} (voice={voice_name})")
