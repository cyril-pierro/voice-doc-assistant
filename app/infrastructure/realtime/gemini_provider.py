"""
app/infrastructure/realtime/gemini_provider.py — Gemini Live via google-genai

Infrastructure adapter (Clean Architecture) — bridges browser WebSocket
<-> Gemini Live `bidiGenerateContent` bidirectional stream.

Refactored per Senior AI Architect spec to fix critical loop error:
  - Stripped expired experimental candidate loop that iterated over
    preview tags shut down by Google. Those strings now consistently
    raise 1008 "is not found for API version v1beta" and trigger
    infinite retry spam.
  - Now uses explicit single-model targeting with immutable production fallback.

Design: single responsibility — manage Live session lifecycle, delegate
retrieval to `retrieval_service.query_document`, emit `traced_span` for
observability. No fallback loop — fail fast with actionable error if the
production model is rejected.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from app.application.services.retrieval_service import QUERY_DOCUMENT_TOOL_SCHEMA, retrieval_service
from app.config import get_settings
from app.tracing import traced_span

logger = logging.getLogger("voice-doc-assistant")


class GeminiLiveProvider:
    """
    Gemini Live provider via `google-genai` SDK.

    Clean Architecture: Infrastructure layer — depends inward on
    application/domain, exposes `handle(client_ws, user)` port.
    """

    def __init__(self) -> None:
        # Client is created per-request in handle() to pick up per-request
        # API key and to allow fresh settings (tests / env changes).
        # Stored as instance attribute so the spec-required pattern
        # `async with self.client.aio.live.connect(...)` is satisfied.
        self.client = None  # type: ignore

    async def handle(self, client_ws: WebSocket, user, voice_gender: str = "female", custom_ai_name: str | None = None) -> None:
        # Fresh settings per-request (supports runtime env changes / tests)
        settings = get_settings()

        # Resolve API key: prefer GOOGLE_API_KEY (google-genai canonical),
        # fallback to GEMINI_API_KEY / gemini_api_key for backwards compat
        api_key = settings.google_api_key or settings.GEMINI_API_KEY or settings.gemini_api_key  # type: ignore
        if not api_key:
            api_key = settings.GOOGLE_API_KEY  # type: ignore

        # Normalize voice_gender: female -> Aoede, male -> Charon (spec)
        voice_gender = (voice_gender or "female").lower()
        if voice_gender not in ("female", "male"):
            voice_gender = "female"
        gemini_voice = "Aoede" if voice_gender == "female" else "Charon"
        # Resolve custom AI name — from DB hydration or fallback
        custom_name = (custom_ai_name or getattr(user, "custom_ai_name", None) or "Aria").strip() or "Aria"
        # Language handling — user-selectable via Select Language menu on main page
        # Previous fix forced English to solve "speaking Indian" bug. Now language is a param.
        # Supported: English (en-US, default), Indian/Hindi (hi-IN), Spanish (es-ES),
        # Japanese (ja-JP), French (fr-FR), Korean (ko-KR)
        language = getattr(user, "language", None) or getattr(user, "preferred_language", None) or "english"
        # Also check if voice handler passed language via user object or via extra param
        # The stream.py will pass language via user.language if set, or we check a global
        # For now, respect the language passed as custom attribute on user or default to English
        # Mapping from UI selection
        lang_map = {
            "english": ("English", "en-US"),
            "indian": ("Hindi", "hi-IN"),
            "hindi": ("Hindi", "hi-IN"),
            "spanish": ("Spanish", "es-ES"),
            "japanese": ("Japanese", "ja-JP"),
            "french": ("French", "fr-FR"),
            "korean": ("Korean", "ko-KR"),
        }
        # Normalize language string
        lang_key = str(language).lower().strip()
        if lang_key not in lang_map:
            # Try to handle full names or codes
            for k, (name, code) in lang_map.items():
                if lang_key == name.lower() or lang_key == code.lower():
                    lang_key = k
                    break
            else:
                lang_key = "english"
        lang_name, lang_code = lang_map[lang_key]
        logger.info(f"[gemini] Language selected: {lang_name} ({lang_code}) for {user.username}")

        # System instruction injection — addresses user by name, stays in character, respects language
        system_instruction = (
            f"You are {custom_name}, a professional Document Assistant. "
            f"The user's name is {user.username}. Introduce yourself as {custom_name} only in your first reply, then do not repeat your name unless the user asks. "
            f"Use the user's name only in that first greeting — never start later answers with it. "
            f"Stay in character as {custom_name} throughout while answering questions based on their data. User email is {user.email} for follow-up. "
            f"Respond in {lang_name} only. All responses must be in clear, professional {lang_name}. "
            f"Speak concisely for voice. "
        )

        session_id = uuid.uuid4().hex[:8]
        with traced_span("ws.session", attributes={"session.id": session_id, "provider": "gemini-google-genai", "user.id": user.email, "voice.gender": voice_gender, "voice.name": gemini_voice}):
            if not api_key:
                logger.warning(f"[gemini] No API key for session {session_id}")
                try:
                    await client_ws.send_json({"type": "error", "message": "Gemini API key not configured. Set GOOGLE_API_KEY or GEMINI_API_KEY."})
                except Exception:
                    pass
                return

            # Lazy import — app must boot without google-genai installed
            try:
                from google import genai  # type: ignore
                from google.genai import types  # type: ignore
            except ImportError as exc:
                logger.error(f"[gemini] google-genai not installed: {exc}")
                try:
                    await client_ws.send_json({"type": "error", "message": "google-genai not installed. pip install google-genai"})
                except Exception:
                    pass
                return

            # ------------------------------------------------------------------
            # 1. Explicit Model Targeting — RESPECT ENV VAR (Fixes override bug)
            # ------------------------------------------------------------------
            # Read GEMINI_LIVE_MODEL directly from environment. The previous
            # implementation enforced "gemini-2.0-flash" and overwrote ANY
            # user-provided live model (e.g., gemini-2.0-flash-live-preview-04-09),
            # causing 1008 "is not found for API version v1beta" because
            # "gemini-2.0-flash" is NOT a live model — it's the base text model.
            # Live models that support bidiGenerateContent are:
            #   - gemini-2.0-flash-live-preview-04-09
            #   - gemini-2.5-flash-preview-native-audio-dialog
            # The correct fix is to USE the env var as-is.
            #
            # Using os.getenv directly ensures the value is read at request
            # time and respects what the user set in .env (e.g., live-preview).
            # ------------------------------------------------------------------
            # Also check settings (Pydantic loads .env) as fallback if os.getenv
            # returns None due to case or loading order — prefer env var first.
            raw_env_model = os.getenv("GEMINI_LIVE_MODEL")
            if raw_env_model is None:
                # Fallback to Pydantic settings (handles GEMINI_LIVE_MODEL from .env)
                raw_env_model = getattr(settings, "GEMINI_LIVE_MODEL", None) or getattr(settings, "gemini_live_model", None)
            # Immutable fallback is the LIVE preview model, NOT the base model
            # Base "gemini-2.0-flash" does NOT support bidiGenerateContent on v1beta
            model_name: str = raw_env_model or "gemini-2.0-flash-live-preview-04-09"
            if not model_name or not model_name.strip():
                model_name = "gemini-2.0-flash-live-preview-04-09"
            # Strip accidental "models/" prefix — SDK adds it automatically
            # Passing "models/gemini-..." results in "models/models/..." -> 1008
            if model_name.startswith("models/"):
                model_name = model_name.removeprefix("models/")
                logger.info(f"[gemini] Stripped 'models/' prefix -> {model_name}")
            # ------------------------------------------------------------------
            # 2. No Enforced Override — respect user's live model
            # ------------------------------------------------------------------
            # Previous code did: if model_name != "gemini-2.0-flash": model_name = "gemini-2.0-flash"
            # That silently discarded the user's valid live preview model and forced
            # the base model, which then failed with 1008. We now TRUST the env var.
            # Only log, do not override.
            # ------------------------------------------------------------------
            logger.info(f"[gemini] Using model={model_name} session={session_id} user={user.username} ai={custom_name} voice={gemini_voice} (from GEMINI_LIVE_MODEL env)")

            # Define query_document tool for Live API
            query_tool = types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="query_document",
                        description=QUERY_DOCUMENT_TOOL_SCHEMA["description"],
                        parameters={
                            "type": "OBJECT",
                            "properties": {
                                "query": {
                                    "type": "STRING",
                                    "description": "Natural language search query derived from user intent",
                                }
                            },
                            "required": ["query"],
                        },
                    )
                ]
            )

            # Dynamic system instruction + voice profile per spec
            # Voice: Aoede (female) / Charon (male) via speech_config
            # Language: Explicitly force English to prevent Hindi/Indian responses
            try:
                speech_config = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=gemini_voice)
                    ),
                    # Explicitly set language to English — prevents auto-detection of Hindi
                    language_code="en-US",
                )
            except TypeError:
                # Older SDK may not support language_code in SpeechConfig
                try:
                    speech_config = types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=gemini_voice)
                        )
                    )
                except Exception:
                    speech_config = None
            except Exception:
                # Fallback for SDK versions without SpeechConfig — will be omitted
                speech_config = None

            live_config_kwargs: dict = dict(
                response_modalities=["AUDIO"],
                tools=[query_tool],
                system_instruction=types.Content(parts=[types.Part(text=system_instruction)]),
            )
            if speech_config is not None:
                live_config_kwargs["speech_config"] = speech_config
            # Also try to set global language if supported
            try:
                # Some SDK versions support language_code at top level
                if hasattr(types.LiveConnectConfig, "model_fields") and "language_code" in getattr(types.LiveConnectConfig, "model_fields", {}):
                    live_config_kwargs["language_code"] = "en-US"
            except Exception:
                pass

            live_config = types.LiveConnectConfig(**live_config_kwargs)
            logger.info(f"[gemini] Live config: voice={gemini_voice} gender={voice_gender} lang=en-US user={user.username}")

            try:
                # Create client — stored on self per spec pattern
                self.client = genai.Client(api_key=api_key)

                # ------------------------------------------------------------------
                # 3. Clean Session Initialization (Spec)
                # ------------------------------------------------------------------
                # Single, non-looping `async with` — the ONLY correct way to
                # manage the bidiGenerateContent lifecycle. Previous multi-candidate
                # loop retried 4 models × 2 api_versions = 8 WebSocket handshakes,
                # spamming 1008 errors and masking the root cause. Clean init
                # opens exactly one session with the production model.
                # ------------------------------------------------------------------
                async with self.client.aio.live.connect(model=model_name, config=live_config) as session:
                    logger.info(f"[gemini] Live connected session={session_id} model={model_name}")

                    await client_ws.send_json({"type": "text", "text": f"Gemini Live connected ({model_name}) as {custom_name} — speak or type! Hi {user.username}!"})
                    # FSM hook: initial LISTENING state for frontend
                    try: await client_ws.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai", "detail": "ready for user input"}) 
                    except: pass

                    async def client_to_upstream():
                        """Browser mic/text -> Gemini Live."""
                        while True:
                            try:
                                raw = await client_ws.receive_text()
                            except WebSocketDisconnect:
                                break
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if msg.get("type") == "audio_chunk" and msg.get("data"):
                                # FSM hook: user started speaking → THINKING (clear silence timer, block echo)
                                try: await client_ws.send_json({"type": "state", "state": "thinking", "fsm": "THINKING_SPEAKING", "speaker": "user"}) 
                                except: pass
                                b64data: str = msg["data"]
                                try:
                                    pcm_bytes = base64.b64decode(b64data)
                                    blob = types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
                                    await session.send_realtime_input(audio=blob)
                                except Exception as exc:
                                    logger.warning(f"[gemini] audio send failed: {exc}")
                            elif msg.get("type") == "text" and msg.get("text"):
                                try: await client_ws.send_json({"type": "state", "state": "thinking", "fsm": "THINKING_SPEAKING", "speaker": "user"}) 
                                except: pass
                                text: str = msg["text"]
                                try:
                                    await session.send_client_content(
                                        turns=types.Content(role="user", parts=[types.Part(text=text)]),
                                        turn_complete=True,
                                    )
                                except Exception as exc:
                                    logger.warning(f"[gemini] text send failed: {exc}")
                                    try:
                                        await session.send(input=text)  # type: ignore
                                    except Exception:
                                        pass

                    async def upstream_to_client():
                        """Gemini Live -> browser."""
                        try:
                            async for response in session.receive():  # type: ignore
                                server_content = getattr(response, "server_content", None)
                                if server_content is not None:
                                    model_turn = getattr(server_content, "model_turn", None) or getattr(server_content, "modelTurn", None)
                                    if model_turn:
                                        parts = getattr(model_turn, "parts", []) or []
                                        for part in parts:
                                            inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
                                            if inline is not None:
                                                mime = getattr(inline, "mime_type", "") or getattr(inline, "mimeType", "") or ""
                                                data = getattr(inline, "data", None)
                                                if data and isinstance(mime, str) and mime.startswith("audio/"):
                                                    # FSM hook: server speaking → THINKING_SPEAKING (client should mute mic)
                                                    try: await client_ws.send_json({"type": "state", "state": "speaking", "fsm": "THINKING_SPEAKING", "speaker": "ai"}) 
                                                    except: pass
                                                    if isinstance(data, (bytes, bytearray)):
                                                        b64audio = base64.b64encode(bytes(data)).decode()
                                                    else:
                                                        b64audio = str(data)
                                                    await client_ws.send_json({"type": "audio_chunk", "data": b64audio, "sample_rate": 24000})
                                            text_val = getattr(part, "text", None)
                                            if text_val:
                                                await client_ws.send_json({"type": "text", "text": text_val})
                                    turn_complete = getattr(server_content, "turn_complete", None) or getattr(server_content, "turnComplete", None)
                                    if turn_complete:
                                        # FSM hook: turn finished → LISTENING (re-arm mic, reset 15s silence timer)
                                        try: await client_ws.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai"}) 
                                        except: pass
                                        await client_ws.send_json({"type": "turn_complete"})
                                        try: await client_ws.send_json({"type": "content_finished", "state": "listening"}) 
                                        except: pass
                                tool_call = getattr(response, "tool_call", None) or getattr(response, "toolCall", None)
                                if tool_call is not None:
                                    fns = getattr(tool_call, "function_calls", None) or getattr(tool_call, "functionCalls", []) or []
                                    for fc in fns:
                                        fc_name = getattr(fc, "name", "")
                                        if fc_name == "query_document":
                                            fc_id = getattr(fc, "id", None) or str(uuid.uuid4())
                                            fc_args = getattr(fc, "args", {}) or {}
                                            if isinstance(fc_args, str):
                                                try:
                                                    fc_args = json.loads(fc_args)
                                                except Exception:
                                                    fc_args = {"query": fc_args}
                                            query = fc_args.get("query", "") if isinstance(fc_args, dict) else str(fc_args)
                                            with traced_span("tool.intercept", attributes={"tool.name": "query_document", "query": query}):
                                                result = await retrieval_service.query(query, user_id=user.email)
                                            await client_ws.send_json(
                                                {"type": "tool_call", "tool": "query_document", "query": query, "result_preview": result[:400]}
                                            )
                                            try:
                                                if hasattr(session, "send_tool_response"):
                                                    await session.send_tool_response(  # type: ignore
                                                        function_responses=types.FunctionResponse(
                                                            id=fc_id, name="query_document", response={"result": result}
                                                        )
                                                    )
                                                else:
                                                    await session.send(input=json.dumps({"toolResponse": {"functionResponses": [{"id": fc_id, "name": "query_document", "response": {"result": result}}]}}))  # type: ignore
                                            except Exception as exc:
                                                logger.warning(f"[gemini] tool response send failed: {exc}")
                                                await session.send_client_content(  # type: ignore
                                                    turns=types.Content(role="tool", parts=[types.Part(text=result)]),
                                                    turn_complete=True,
                                                )
                                if isinstance(response, dict):
                                    tool_call_dict = response.get("toolCall", {})
                                    for fc in tool_call_dict.get("functionCalls", []):
                                        if fc.get("name") == "query_document":
                                            fc_id = fc.get("id", str(uuid.uuid4()))
                                            query = fc.get("args", {}).get("query", "")
                                            with traced_span("tool.intercept", attributes={"tool.name": "query_document", "query": query}):
                                                result = await retrieval_service.query(query, user_id=user.email)
                                            await client_ws.send_json({"type": "tool_call", "tool": "query_document", "query": query, "result_preview": result[:400]})
                                            await session.send_realtime_input(text=f"Tool result: {result}")  # type: ignore
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.warning(f"[gemini] upstream receive ended: {exc}")

                    # Concurrent bidi proxy — core of low-latency voice
                    done, pending = await asyncio.wait(
                        [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except asyncio.CancelledError:
                            pass

            except Exception as exc:
                err_msg = str(exc)
                if "1008" in err_msg or "is not found" in err_msg or "bidiGenerateContent" in err_msg:
                    logger.error(f"[gemini] Live session {session_id} rejected model={model_name} (1008): {exc}")
                    try:
                        await client_ws.send_json({
                            "type": "error",
                            "message": (
                                f"Gemini Live gateway rejected model '{model_name}' (1008). "
                                f"bidiGenerateContent requires a LIVE model (e.g., 'gemini-2.0-flash-live-preview-04-09' or "
                                f"'gemini-2.5-flash-preview-native-audio-dialog'), not the base 'gemini-2.0-flash'. "
                                f"Verify GEMINI_LIVE_MODEL={model_name} is a live model at https://aistudio.google.com. Original: {exc}"
                            )
                        })
                    except Exception:
                        pass
                    return
                logger.exception(f"[gemini] session {session_id} error: {exc}")
                try:
                    await client_ws.send_json({"type": "error", "message": str(exc)})
                except Exception:
                    pass
