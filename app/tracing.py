"""
app/tracing.py — OpenInference + Arize Phoenix tracing

Strictly implements the spec:
  - Checks ENABLE_TRACING
  - Initializes Phoenix tracer provider
  - Calls OpenInference's OpenAIInstrumentor().instrument() pointing to Phoenix

Also provides a generic `traced_span()` helper for manual spans
(tool execution, retrieval, ws sessions) compatible with free-tier
deploys where Phoenix is not running.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from app.config import get_settings

logger = logging.getLogger(__name__)

# Lazy OTEL imports — app must boot even if observability deps missing.
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.semconv.resource import ResourceAttributes

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

_tracer_provider_configured = False
_phoenix_registered = False


def setup_tracing() -> None:
    """
    Initialize tracing per strict spec:

    1. Check if tracing is enabled (ENABLE_TRACING / effective_tracing_enabled).
    2. Initialize Arize Phoenix tracer provider.
    3. Call OpenInference's OpenAIInstrumentor().instrument(tracer_provider=...).

    Falls back gracefully to console exporter if Phoenix not available,
    so the app never crashes on free-tier without observability deps.
    """
    global _tracer_provider_configured, _phoenix_registered
    if _tracer_provider_configured:
        return

    settings = get_settings()

    # Spec field: ENABLE_TRACING (alias: effective_tracing_enabled)
    is_enabled = getattr(settings, "ENABLE_TRACING", True)
    # Backwards-compat: also check effective_tracing_enabled if defined
    if hasattr(settings, "effective_tracing_enabled"):
        is_enabled = settings.effective_tracing_enabled
    if hasattr(settings, "tracing_enabled") and settings.tracing_enabled is not None:
        # If legacy field explicitly set, honor it
        is_enabled = settings.tracing_enabled
    # Also check exporter == "none"
    if getattr(settings, "tracing_exporter", None) == "none":
        is_enabled = False

    if not is_enabled:
        logger.info("Tracing disabled via ENABLE_TRACING=false")
        return

    if not _OTEL_AVAILABLE:
        logger.warning("opentelemetry not installed — tracing disabled (pip install opentelemetry-sdk)")
        return

    tracer_provider = None
    project_name = getattr(settings, "PHOENIX_PROJECT_NAME", None) or getattr(settings, "phoenix_project_name", None) or "voice-doc-assistant"
    # Support both PHOENIX_COLLECTOR_ENDPOINT (docs) and PHOENIX_ENDPOINT (legacy)
    # Docs sample: PHOENIX_COLLECTOR_ENDPOINT='https://app.phoenix.arize.com/s/...'
    phoenix_endpoint = (
        getattr(settings, "PHOENIX_COLLECTOR_ENDPOINT", None)
        or getattr(settings, "PHOENIX_ENDPOINT", None)
        or getattr(settings, "phoenix_endpoint", None)
        or getattr(settings, "collector_endpoint", None)
    )
    # Also check env directly (in case .env uses PHOENIX_COLLECTOR_ENDPOINT)
    if not phoenix_endpoint:
        import os as _os_ep
        phoenix_endpoint = _os_ep.getenv("PHOENIX_COLLECTOR_ENDPOINT") or _os_ep.getenv("PHOENIX_ENDPOINT")
    # For Phoenix Cloud gRPC, the /s/... suffix is a share link, not OTLP.
    # The gRPC exporter expects host only; we keep the full URL for Phoenix register
    # which handles it, but for manual fallback we normalize.
    _is_cloud_share = phoenix_endpoint and "app.phoenix.arize.com" in phoenix_endpoint and "/s/" in phoenix_endpoint
    if _is_cloud_share:
        # Keep original for register (which may handle it), but log
        logger.info(f"Phoenix Cloud endpoint {phoenix_endpoint} contains /s/... share path — register will handle gRPC normalization")

    # Detect placeholder Phoenix key that would cause 401 UNAUTHENTICATED spam
    # The default .env ships with demo JWTs (ApiKey:1, ApiKey:2) — treat as missing and fallback to console
    # This prevents gRPC exporter from spamming 401 every few seconds when no real cloud key is configured.
    # Real Phoenix Cloud keys are opaque, not JWTs with jti=ApiKey:N — check decoded payload.
    def _is_placeholder_phoenix_key(key: str | None) -> bool:
        if not key:
            return False
        # Known dummy JWTs from .env
        if key in (
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJBcGlLZXk6MSJ9.w5r_prVDcTppPqSG7ELMHzlTIYcCM9o15cWDf9tEXsc",
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJBcGlLZXk6MiJ9.Ou_7aPdngF1jQsrIjo8A7LLUVH2P0azJaS3KxU8vU7c",
        ):
            return True
        # Generic check: JWT with ApiKey in payload
        try:
            if key.count(".") == 2:
                import base64, json
                payload_b64 = key.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                if isinstance(payload, dict) and payload.get("jti", "").startswith("ApiKey:"):
                    return True
        except Exception:
            pass
        return False

    _phoenix_key_for_check = getattr(settings, "PHOENIX_API_KEY", None) or getattr(settings, "phoenix_api_key", None) or phoenix_endpoint and ""
    import os as _os_check
    _env_key = _os_check.getenv("PHOENIX_API_KEY")
    _check_key = _env_key or _phoenix_key_for_check
    if _is_placeholder_phoenix_key(_check_key):
        logger.warning(
            f"PHOENIX_API_KEY looks like placeholder demo JWT ({_check_key[:20]}...) — Phoenix Cloud will reject it with 401. "
            "Using console exporter to avoid spam. Replace with a real key from https://app.phoenix.arize.com -> Settings -> API Keys, "
            "or set TRACING_EXPORTER=console for local dev."
        )
        # Clear the dummy so headers are not sent and endpoint falls back
        try:
            object.__setattr__(settings, "PHOENIX_API_KEY", None)
            object.__setattr__(settings, "phoenix_api_key", None)
        except Exception:
            pass
        if "PHOENIX_API_KEY" in _os_check.environ:
            # Don't delete, just note — keep for user visibility
            pass
        # For this run, force console to avoid gRPC 401 loop
        # We keep phoenix_endpoint as None so fallback uses console
        if phoenix_endpoint and "app.phoenix.arize.com" in phoenix_endpoint:
            logger.info("Falling back to console tracing due to placeholder Phoenix key")
            phoenix_endpoint = None
            # Force exporter to console by temporarily overriding setting
            try:
                object.__setattr__(settings, "tracing_exporter", "console")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 1. Try Arize Phoenix registration (spec-required path)
    #    BUGFIX: Must force gRPC — Phoenix Cloud drops HTTP with 405
    # ------------------------------------------------------------------ #
    try:
        from phoenix.otel import register  # arize-phoenix
        import os

        # Phoenix register returns a TracerProvider configured for Phoenix
        # When phoenix_endpoint is None, it uses default localhost:6006
        # Securely extract PHOENIX_API_KEY for Arize Cloud
        _phoenix_api_key = os.getenv("PHOENIX_API_KEY") or getattr(settings, "PHOENIX_API_KEY", None) or getattr(settings, "phoenix_api_key", None)
        _phoenix_headers = {"api_key": _phoenix_api_key} if _phoenix_api_key else None

        # CRITICAL: protocol="grpc" eliminates 405 Method Not Allowed on Phoenix Cloud
        # HTTP/JSON (default) is rejected by the cloud ingress; gRPC uses HTTP/2 streams
        register_kwargs: dict = {
            "project_name": project_name,
            "protocol": "grpc",  # MANDATORY — see bug #2
        }
        if phoenix_endpoint:
            register_kwargs["endpoint"] = phoenix_endpoint
        if _phoenix_headers:
            register_kwargs["headers"] = _phoenix_headers

        tracer_provider = register(**register_kwargs)
        _phoenix_registered = True
        logger.info(
            f"Phoenix tracing registered (project={project_name}, protocol=grpc, "
            f"endpoint={phoenix_endpoint or 'default'}, headers={'api_key:***' if _phoenix_headers else 'none'})"
        )
    except ImportError:
        logger.warning("arize-phoenix not installed — falling back to manual OTLP/Console tracer (pip install arize-phoenix)")
    except Exception as exc:
        logger.warning(f"Phoenix register failed ({exc}) — falling back to manual tracer")

    # ------------------------------------------------------------------ #
    # 2. Fallback: manual TracerProvider with Console or OTLP exporter
    #    BUGFIX: Must use gRPC for Phoenix Cloud — HTTP triggers 405
    # ------------------------------------------------------------------ #
    if tracer_provider is None:
        resource = Resource.create({ResourceAttributes.SERVICE_NAME: project_name})
        tracer_provider = TracerProvider(resource=resource)

        exporter = None
        exporter_name = getattr(settings, "tracing_exporter", "console")
        if exporter_name == "console":
            exporter = ConsoleSpanExporter()
            logger.info("Tracing exporter: console (stdout)")
        elif exporter_name in ("phoenix", "otlp"):
            # Try gRPC first (required for Phoenix Cloud), then HTTP fallback
            # gRPC exporter uses opentelemetry-exporter-otlp-proto-grpc
            # which is mandatory to avoid 405 on https://app.phoenix.arize.com
            _headers = None
            _api_key = getattr(settings, "PHOENIX_API_KEY", None) or getattr(settings, "phoenix_api_key", None)
            if _api_key:
                _headers = {"api_key": _api_key}
            # Attempt gRPC
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCExporter  # type: ignore

                endpoint = phoenix_endpoint or "http://localhost:4317"
                # gRPC endpoint must be host only — strip HTTP path and share links
                # https://app.phoenix.arize.com/s/fiopapa32 -> https://app.phoenix.arize.com
                # https://app.phoenix.arize.com/v1/traces -> https://app.phoenix.arize.com
                if endpoint and "app.phoenix.arize.com" in endpoint:
                    from urllib.parse import urlparse
                    _p = urlparse(endpoint)
                    endpoint = f"{_p.scheme}://{_p.netloc}"
                elif endpoint and endpoint.endswith("/v1/traces"):
                    endpoint = endpoint.replace("/v1/traces", "")
                    if "localhost" in endpoint and ":6006" in endpoint:
                        endpoint = endpoint.replace(":6006", ":4317")
                exporter = GRPCExporter(endpoint=endpoint, headers=_headers)
                logger.info(f"Tracing exporter: OTLP gRPC -> {endpoint} (headers={'api_key:***' if _headers else 'none'})")
            except ImportError:
                logger.warning("opentelemetry-exporter-otlp-proto-grpc not installed, trying HTTP fallback")
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                    endpoint = phoenix_endpoint or "http://localhost:6006/v1/traces"
                    exporter = OTLPSpanExporter(endpoint=endpoint, headers=_headers)
                    logger.info(f"Tracing exporter: OTLP HTTP fallback -> {endpoint}")
                except ImportError:
                    logger.warning("opentelemetry-exporter-otlp not installed, falling back to console")
                    exporter = ConsoleSpanExporter()
            except Exception as exc:
                logger.warning(f"gRPC exporter init failed ({exc}), falling back to console")
                exporter = ConsoleSpanExporter()
        else:
            exporter = ConsoleSpanExporter()

        if exporter is not None:
            tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

        # Only set global if Phoenix didn't already do it
        try:
            trace.set_tracer_provider(tracer_provider)
        except Exception:
            pass  # Already set by Phoenix

    _tracer_provider_configured = True

    # ------------------------------------------------------------------ #
    # 3. Spec-required: OpenInference OpenAI instrumentation
    # ------------------------------------------------------------------ #
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        # Instrument OpenAI SDK calls to emit spans to the Phoenix provider
        # Spec: OpenAIInstrumentor().instrument() pointing to Arize Phoenix tracer provider
        if tracer_provider is not None:
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
        else:
            OpenAIInstrumentor().instrument()
        logger.info("OpenInference OpenAIInstrumentor instrumented -> Phoenix")
    except ImportError:
        logger.warning("openinference-instrumentation-openai not installed — OpenAI spans will not be auto-instrumented (pip install openinference-instrumentation-openai)")
    except Exception as exc:
        logger.warning(f"OpenAIInstrumentor instrumentation failed: {exc}")

    logger.info(f"Tracing initialized (service={project_name}, enabled={is_enabled})")


def get_tracer(name: str = "voice-doc-assistant"):
    """Return a tracer — falls back to no-op if OTEL unavailable."""
    if not _OTEL_AVAILABLE:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.start_as_current_span = _noop_span
        return mock
    return trace.get_tracer(name)


@contextmanager
def _noop_span(*args, **kwargs) -> Generator[None, None, None]:
    yield


@contextmanager
def traced_span(
    name: str,
    attributes: dict | None = None,
    kind: object | None = None,
) -> Generator[object, None, None]:
    """
    Convenience span helper that degrades gracefully when OTEL unavailable.

    Usage:
        with traced_span("tool.query_document", {"query": q}) as span:
            result = do_lookup(q)
            span.set_attribute("result.length", len(result))
    """
    # Check spec field ENABLE_TRACING
    settings = get_settings()
    is_enabled = getattr(settings, "ENABLE_TRACING", True)
    if hasattr(settings, "effective_tracing_enabled"):
        is_enabled = settings.effective_tracing_enabled
    if not _OTEL_AVAILABLE or not is_enabled:
        yield _DummySpan()
        return

    tracer = get_tracer()
    try:
        from opentelemetry.trace import SpanKind

        span_kind = kind or SpanKind.INTERNAL
    except ImportError:
        span_kind = kind

    with tracer.start_as_current_span(name, kind=span_kind) as span:  # type: ignore
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        try:
            yield span
        except Exception as exc:
            try:
                span.record_exception(exc)  # type: ignore
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, str(exc)))  # type: ignore
            except Exception:
                pass
            raise


class _DummySpan:
    """No-op span for mock mode / missing OTEL."""

    def set_attribute(self, *args, **kwargs) -> None:
        pass

    def add_event(self, *args, **kwargs) -> None:
        pass

    def record_exception(self, *args, **kwargs) -> None:
        pass

    def set_status(self, *args, **kwargs) -> None:
        pass
