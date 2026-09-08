"""
app/infrastructure/realtime/mock_provider.py — Mock upstream loop

Implements the RealtimeProvider port for local development / tests.
No external API keys needed; simulates tool-calling interception
so the architecture is demonstrable on first clone.
"""

from __future__ import annotations

import array
import base64
import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from app.application.services.retrieval_service import retrieval_service
from app.config import get_settings
from app.infrastructure.repositories.memory import USER_DOCUMENTS, DOCUMENT_STORE
from app.tracing import traced_span

logger = logging.getLogger("voice-doc-assistant")


class MockRealtimeProvider:
    """Mock provider — heuristic tool trigger, silent audio placeholder."""

    async def handle(self, client_ws: WebSocket, user, voice_gender: str = "female", custom_ai_name: str | None = None) -> None:
        settings = get_settings()
        voice_gender = (voice_gender or "female").lower()
        gemini_voice = "Aoede" if voice_gender == "female" else "Charon"
        custom_name = (custom_ai_name or getattr(user, "custom_ai_name", None) or "Aria").strip() or "Aria"
        language = getattr(user, "language", None) or getattr(user, "preferred_language", None) or "english"
        lang_map = {"english": "English", "indian": "Hindi", "hindi": "Hindi", "spanish": "Spanish", "japanese": "Japanese", "french": "French", "korean": "Korean"}
        lang_name = lang_map.get(str(language).lower(), "English")
        system_instruction = (
            f"You are {custom_name}, a professional Document Assistant. The user's name is {user.username}. "
            f"Introduce yourself as {custom_name} only in your first message, then do not repeat your name unless asked. "
            f"Answer questions based on their data. "
            f"Respond in {lang_name} only. All responses must be in clear, professional {lang_name}. "
        )
        session_id = uuid.uuid4().hex[:8]
        with traced_span(
            "ws.session",
            attributes={
                "session.id": session_id,
                "user.id": user.email,
                "provider": "mock",
                "ws.protocol": "mock_loop",
                "voice.gender": voice_gender,
                "voice.name": gemini_voice,
            },
        ):
            await client_ws.send_json(
                {
                    "type": "text",
                    "text": f"Hello {user.username}! I'm {custom_name} ({gemini_voice}, {voice_gender}) — mock voice mode. {system_instruction} Try asking about your documents!",
                    "session_id": session_id,
                }
            )
            try: await client_ws.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai"})
            except: pass
            logger.info(f"[mock] WS session {session_id} for {user.email} voice={gemini_voice} gender={voice_gender} ai={custom_name} user={user.username}")

            while True:
                try:
                    raw = await client_ws.receive_text()
                except WebSocketDisconnect:
                    logger.info(f"[mock] WS disconnect session {session_id}")
                    break
                except Exception as exc:
                    logger.error(f"[mock] receive error: {exc}")
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {"type": "text", "text": raw}

                msg_type = msg.get("type", "text")

                if msg_type == "text" or "text" in msg:
                    user_text: str = msg.get("text", "") or msg.get("transcript", "")
                    if not user_text.strip():
                        continue
                    logger.info(f"[mock] transcript [{session_id}]: {user_text[:120]}")
                    # Record for post-session summary worker (email-keyed)
                    try:
                        from app.application.services.summary_service import append_transcript
                        append_transcript(user.email, "user", user_text)
                        append_transcript(session_id, "user", user_text)
                    except Exception:
                        pass

                    try: await client_ws.send_json({"type": "state", "state": "thinking", "fsm": "THINKING_SPEAKING", "speaker": "user"})
                    except: pass
                    with traced_span("llm.inference", attributes={"input.text": user_text[:500]}) as span:
                        doc_keywords = [
                            "document", "file", "pdf", "upload", "context", "summarize", "what does",
                            "what is", "what are", "how", "when", "where", "why", "policy", "refund",
                            "find", "search", "explain", "tell me", "according", "mention",
                        ]
                        lower = user_text.lower()
                        has_keyword = any(kw in lower for kw in doc_keywords)
                        has_question = "?" in user_text or len(user_text.split()) >= 3
                        has_docs = bool(USER_DOCUMENTS.get(user.email) or DOCUMENT_STORE)
                        should_call_tool = (has_keyword and has_question) or (has_docs and has_question)

                        if should_call_tool:
                            with traced_span("tool.intercept", attributes={"tool.name": "query_document"}):
                                tool_result = await retrieval_service.query(user_text, user_id=user.email)
                                await client_ws.send_json(
                                    {
                                        "type": "tool_call",
                                        "tool": "query_document",
                                        "query": user_text,
                                        "result_preview": tool_result[:400],
                                    }
                                )
                                answer = (
                                    f"Based on your documents, here's what I found: {tool_result[:600]}. "
                                    f"Let me know if you'd like me to elaborate — I retrieved {len(tool_result)} characters of context."
                                )
                        else:
                            answer = (
                                f"You said: '{user_text}'. I'm in mock voice mode — "
                                "upload a document and ask about it to see the query_document tool in action."
                            )

                        span.set_attribute("output.text", answer[:500])
                        span.set_attribute("output.length", len(answer))

                        # Record assistant answer for summary
                        try:
                            from app.application.services.summary_service import append_transcript
                            append_transcript(user.email, "assistant", answer)
                            append_transcript(session_id, "assistant", answer)
                        except Exception:
                            pass

                        try: await client_ws.send_json({"type": "state", "state": "speaking", "fsm": "THINKING_SPEAKING", "speaker": "ai"})
                        except: pass
                        await client_ws.send_json({"type": "text", "text": answer})

                        silent_samples = array.array("h", [0] * 1600)  # 100ms @16kHz
                        silent_b64 = base64.b64encode(silent_samples.tobytes()).decode()
                        await client_ws.send_json(
                            {
                                "type": "audio_chunk",
                                "data": silent_b64,
                                "sample_rate": settings.sample_rate,
                                "mock": True,
                                "text": answer,
                            }
                        )
                        await client_ws.send_json({"type": "turn_complete"})
                        try: await client_ws.send_json({"type": "state", "state": "listening", "fsm": "LISTENING", "speaker": "ai"})
                        except: pass
                        try: await client_ws.send_json({"type": "content_finished", "state": "listening"})
                        except: pass

                elif msg_type == "audio_chunk":
                    b64data: str = msg.get("data", "")
                    try:
                        pcm_bytes = base64.b64decode(b64data) if b64data else b""
                    except Exception:
                        pcm_bytes = b""
                    if len(pcm_bytes) > 3200:
                        await client_ws.send_json({"type": "vad", "state": "speech"})
