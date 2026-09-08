"""
app/infrastructure/realtime/factory.py — Provider selection

Decides which realtime upstream to use based on settings.resolved_provider.
This is the infrastructure-level factory; application does not know about
concrete providers (Dependency Inversion).
"""

from __future__ import annotations

from app.config import get_settings


def get_provider_name() -> str:
    return get_settings().resolved_provider


def get_provider():
    """Return a provider instance with a .handle(client_ws, user) coroutine."""
    provider = get_provider_name()
    if provider == "openai":
        from app.infrastructure.realtime.openai_provider import OpenAIRealtimeProvider

        return OpenAIRealtimeProvider()
    if provider == "gemini":
        from app.infrastructure.realtime.gemini_provider import GeminiLiveProvider

        return GeminiLiveProvider()
    # default: mock (also when keys missing)
    from app.infrastructure.realtime.mock_provider import MockRealtimeProvider

    return MockRealtimeProvider()
