"""
app/infrastructure/realtime/openai_provider.py — OpenAI Realtime bridge

Bridges browser WebSocket <-> OpenAI Realtime API (wss://api.openai.com/v1/realtime).
Intercepts `query_document` function calls and injects local retrieval results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from app.application.services.retrieval_service import QUERY_DOCUMENT_TOOL_SCHEMA, retrieval_service
from app.config import get_settings
from app.tracing import traced_span

logger = logging.getLogger("voice-doc-assistant")


class OpenAIRealtimeProvider:
    async def handle(self, client_ws: WebSocket, user, voice_gender: str = "female", custom_ai_name: str | None = None) -> None:
        settings = get_settings()
        import websockets as ws_client

        # Voice mapping: Aoede (female) -> alloy/nova, Charon (male) -> onyx
        voice_gender = (voice_gender or "female").lower()
        openai_voice = "alloy" if voice_gender == "female" else "onyx"
        # Spec: Aoede female, Charon/Puck male — map to OpenAI voices
        if voice_gender == "female":
            openai_voice = "nova"  # closest to Aoede warm female
        else:
            openai_voice = "onyx"  # deep male like Charon

        custom_name = (custom_ai_name or getattr(user, "custom_ai_name", None) or "Aria").strip() or "Aria"
        # Language handling — respect user's Select Language choice (default English)
        language = getattr(user, "language", None) or getattr(user, "preferred_language", None) or "english"
        lang_map = {"english": "English", "indian": "Hindi", "hindi": "Hindi", "spanish": "Spanish", "japanese": "Japanese", "french": "French", "korean": "Korean"}
        lang_name = lang_map.get(str(language).lower(), "English")
        system_instruction = (
            f"You are {custom_name}, a professional Document Assistant. The user's name is {user.username}. "
            f"Introduce yourself as {custom_name} only in your very first message. After that, do not repeat your name unless the user asks. "
            f"Use the user's name only in that first greeting — never start later answers with it. "
            f"Stay in character as {custom_name} and answer questions based on their data. User email is {user.email}. "
            f"Respond in {lang_name} only. All responses must be in clear, professional {lang_name}. "
        )

        openai_url = f"wss://api.openai.com/v1/realtime?model={settings.openai_realtime_model}"
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        session_id = uuid.uuid4().hex[:8]
        with traced_span("ws.session", attributes={"session.id": session_id, "provider": "openai", "user.id": user.email}):
            try:
                async with ws_client.connect(openai_url, extra_headers=headers) as upstream:
                    logger.info(f"[openai] Connected upstream session {session_id}")

                    session_update = {
                        "type": "session.update",
                        "session": {
                            "modalities": ["text", "audio"],
                            "instructions": system_instruction + " You have access to a tool `query_document(query: str)` that searches the user's uploaded documents. ALWAYS call query_document when the user asks about documents. Ground answers in context. Speak concisely.",
                            "voice": openai_voice,
                            "input_audio_format": "pcm16",
                            "output_audio_format": "pcm16",
                            "turn_detection": {"type": "server_vad"},
                            "tools": [
                                {
                                    "type": "function",
                                    "name": "query_document",
                                    "description": QUERY_DOCUMENT_TOOL_SCHEMA["description"],
                                    "parameters": QUERY_DOCUMENT_TOOL_SCHEMA["parameters"],
                                }
                            ],
                            "tool_choice": "auto",
                        },
                    }
                    await upstream.send(json.dumps(session_update))

                    async def client_to_upstream():
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
                                await upstream.send(json.dumps({"type": "input_audio_buffer.append", "audio": msg["data"]}))
                            elif msg.get("type") == "text" and msg.get("text"):
                                await upstream.send(
                                    json.dumps(
                                        {
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "message",
                                                "role": "user",
                                                "content": [{"type": "input_text", "text": msg["text"]}],
                                            },
                                        }
                                    )
                                )
                                await upstream.send(json.dumps({"type": "response.create"}))

                    async def upstream_to_client():
                        async for raw in upstream:
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            etype = event.get("type", "")

                            if etype in ("response.audio.delta", "response.output_audio.delta"):
                                b64audio = event.get("delta") or event.get("audio") or ""
                                if b64audio:
                                    await client_ws.send_json({"type": "audio_chunk", "data": b64audio, "sample_rate": 24000})
                            elif etype == "response.audio_transcript.delta":
                                await client_ws.send_json({"type": "text", "text": event.get("delta", ""), "partial": True})
                            elif etype == "response.done":
                                await client_ws.send_json({"type": "turn_complete"})
                            elif etype == "response.function_call_arguments.done":
                                call_id = event.get("call_id", "")
                                name = event.get("name", "")
                                args_raw = event.get("arguments", "{}")
                                if name == "query_document":
                                    try:
                                        args = json.loads(args_raw)
                                        query = args.get("query", "")
                                    except json.JSONDecodeError:
                                        query = args_raw
                                    with traced_span("tool.intercept", attributes={"tool.name": name, "query": query}):
                                        result = await retrieval_service.query(query, user_id=user.email)
                                    await client_ws.send_json({"type": "tool_call", "tool": name, "query": query, "result_preview": result[:400]})
                                    await upstream.send(
                                        json.dumps(
                                            {
                                                "type": "conversation.item.create",
                                                "item": {"type": "function_call_output", "call_id": call_id, "output": result},
                                            }
                                        )
                                    )
                                    await upstream.send(json.dumps({"type": "response.create"}))
                            elif etype == "response.output_item.done":
                                item = event.get("item", {})
                                if item.get("type") == "function_call" and item.get("name") == "query_document":
                                    call_id = item.get("call_id", "")
                                    args_raw = item.get("arguments", "{}")
                                    try:
                                        args = json.loads(args_raw)
                                        query = args.get("query", "")
                                    except json.JSONDecodeError:
                                        query = args_raw
                                    with traced_span("tool.intercept", attributes={"tool.name": "query_document", "query": query}):
                                        result = await retrieval_service.query(query, user_id=user.email)
                                    await client_ws.send_json({"type": "tool_call", "tool": "query_document", "query": query, "result_preview": result[:400]})
                                    await upstream.send(
                                        json.dumps(
                                            {
                                                "type": "conversation.item.create",
                                                "item": {"type": "function_call_output", "call_id": call_id, "output": result},
                                            }
                                        )
                                    )
                                    await upstream.send(json.dumps({"type": "response.create"}))
                            elif etype == "error":
                                logger.error(f"[openai] upstream error: {event}")
                                await client_ws.send_json({"type": "error", "message": str(event.get("error", event))})

                    done, pending = await asyncio.wait(
                        [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()

            except ws_client.exceptions.InvalidStatusCode as exc:  # type: ignore
                logger.error(f"[openai] upstream connect failed {exc.status_code}: {exc}")
                await client_ws.send_json({"type": "error", "message": f"Upstream connect failed: {exc.status_code}"})
            except Exception as exc:
                logger.exception(f"[openai] session {session_id} error: {exc}")
                try:
                    await client_ws.send_json({"type": "error", "message": str(exc)})
                except Exception:
                    pass
