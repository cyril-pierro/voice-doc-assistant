# Voice-Native Document AI Assistant

> **Talk to your documents.** A production-ready, voice-first RAG prototype with real-time bidirectional audio streaming, LangChain document processing, pgvector semantic search, and full LLM observability — built to run on free cloud tiers.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![WebSockets](https://img.shields.io/badge/WebSockets-Bidirectional-8A2BE2?style=flat-square)](https://fastapi.tiangolo.com/advanced/websockets/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-38BDF8?style=flat-square&logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![LangChain](https://img.shields.io/badge/LangChain-0.3-1C3C3C?style=flat-square&logo=langchain&logoColor=white)](https://python.langchain.com/)
[![pgvector](https://img.shields.io/badge/pgvector-Postgres-336791?style=flat-square&logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-Tracing-orange?style=flat-square&logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)
[![License MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Deploy: Render](https://img.shields.io/badge/Deploy-Render-46E3B7?style=flat-square)](https://render.com/)
[![Deploy: Vercel](https://img.shields.io/badge/Deploy-Vercel-black?style=flat-square&logo=vercel)](https://vercel.com/)

**Live Demo** · [Frontend on Vercel](https://voice-doc-assistant.vercel.app) · [Backend on Render](https://voice-doc-assistant.onrender.com) · [API Docs](https://voice-doc-assistant.onrender.com/docs)

> 🔑 **Zero API keys required to try it.** The entire realtime loop, tool calling, and evaluation runs in **mock mode** on first `git clone`. Add `OPENAI_API_KEY` or `GOOGLE_API_KEY` when you want live voice. Vector search works offline via deterministic hashing when no OpenAI key is set. **Enterprise auth works offline too** — SQLite locally, PostgreSQL on Render via `DATABASE_URL`.

> 🆕 **Latest Update (JWT Auth + Enterprise DB):** Premium 4-screen onboarding with **JWT bearer auth** (`PyJWT` HS256, `bcrypt` + SQLAlchemy 2.0), **dynamic SQLite → PostgreSQL (asyncpg)** switch, and **WebSocket session hydration** — sign in returns `access_token`/`session_token` (Bearer), REST + `WS /ws/stream?token=<jwt>` authenticate via `Authorization: Bearer <token>` / cookie / `?token`, AI greets you as `custom_ai_name` and speaks as `Aoede`/`Charon` per your saved profile. See [Enterprise Auth & Persistence](#enterprise-auth--persistence) below.

---

## Table of Contents

- [Why This Project?](#why-this-project)
- [Demo](#demo)
- [Architecture](#architecture)
- [Features That Get You Hired](#features-that-get-you-hired)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [WebSocket Protocol — Hydrated](#websocket-protocol--hydrated)
- [Frontend Deep Dive — 4-Screen Premium Auth](#frontend-deep-dive--4-screen-premium-auth)
- [Enterprise Auth & Persistence](#enterprise-auth--persistence)
- [LLM Observability & Tracing](#llm-observability--tracing)
- [Multilingual Evaluation Framework](#multilingual-evaluation-framework)
- [Vector Search & LangChain](#vector-search--langchain)
- [Deployment — Free Tier](#deployment--free-tier)
- [What I'd Build Next](#what-id-build-next)
- [Recruiter FAQ](#recruiter-faq)
- [License](#license)

---

## Why This Project?

Most "Chat with PDF" demos are **text-in, text-out** wrappers around an LLM. This project is different:

| Typical RAG Demo | This Project |
|---|---|
| HTTP request/response | **Persistent bidirectional WebSocket** — browser mic ↔ server ↔ realtime voice provider |
| No voice, or TTS bolted on | **Native 16 kHz PCM16 streaming** via Web Audio API + ordered playback queue |
| Tool calls are an afterthought | **First-class `query_document` interception** inside the audio generation loop |
| Naive `latin-1` PDF fallback | **LangChain loaders** (`PyPDFLoader`, `Docx2txtLoader`, `UnstructuredExcelLoader`) + `RecursiveCharacterTextSplitter` |
| Keyword Jaccard only | **pgvector cosine search** (`text-embedding-3-small`) with hashing fallback + hybrid keyword blend |
| "It works on my machine" | **Free-tier deployable** (Render + Vercel), single `Dockerfile`, `$PORT` aware, **in-memory vector fallback** when no Postgres |
| No observability | **OpenInference / OpenTelemetry tracing** on every span — Phoenix **gRPC** (fixes 405), Arize Cloud |
| English-only evals, if any | **13-sample multilingual suite** (EN/FR/ES/DE/JA/AR + code-switching) with Faithfulness & Relevance scoring |
| Tool logs spoken aloud | **Tool logs are visual-only** — speaker plays only final assistant audio, not conversion logs |
| No auth, guest only | **Enterprise JWT auth** — `PyJWT` HS256 bearer (`access_token`/`session_token` on signup/signin, `GET /me`, `POST /refresh`), `passlib[bcrypt]`, **SQLAlchemy 2.0** `User` (username/email/hashed_password/gender/custom_ai_name), **SQLite (dev) ↔ PostgreSQL (prod, asyncpg)** via `DATABASE_URL` |

**What it proves on a resume:** Real-time systems, full-stack ownership, **LangChain + pgvector RAG**, **enterprise auth + dynamic DB**, LLM Ops, multilingual NLP awareness, and production pragmatism (mock mode, cost-aware provider switching, graceful degradation, in-memory fallback).

---

## Demo

### 30-Second Flow — Now with Enterprise Auth

1. **Sign Up** → Choose `Username`, `Email`, `Password`, `Preferred Voice Gender` (Aoede/Charon), `Custom AI Name` (e.g., *Milo*) → `POST /api/auth/signup` → `bcrypt` hash → **JWT `access_token` + `session_token`** (`Bearer`, 7-day `HS256`) + `httponly` cookie → **SQLite** locally / **PostgreSQL** on Render
2. **Sign In** → `POST /api/auth/signin` → **JWT bearer `access_token`** + `httponly` cookie (`Jwt ` + `Authorization: Bearer <token>` for all later calls) → frontend stores `session_token`/`username`/`custom_ai_name`/`voiceGender` and shows **Configuration Screen**
3. **Upload** a PDF/DOCX/XLSX → **LangChain extraction** → `RecursiveCharacterTextSplitter` → **pgvector embeddings** → indexed per-user (isolated via `user.email`)
4. **Launch** → 3s cinematic (orbs + `pgvector` progress) → **Main Dashboard** as your AI `Aria`/`Milo` greets you: *“Hi Alice! I'm Aria (Aoede) — ...”* (system instruction injected via `client.aio.live.connect(speech_config=Aoede/Charon)`)
5. **Tap 🎙️** and say *"What does my document say about refunds?"* → Watch **`query_document` fire** with `similarity=0.87` → hear grounded answer (tool logs never spoken) → on disconnect, **BackgroundTasks** emails a mock SMTP summary to your validated `email`

### Screenshots

> Add your own — placeholders below render nicely on GitHub.

| Session & Upload | Voice Stream & Visualizer | Tool Log |
|---|---|---|
| ![session](docs/screenshots/session.png) | ![visualizer](docs/screenshots/visualizer.png) | ![tools](docs/screenshots/tools.png) |

**Tip for your portfolio:** Record a 45-second Loom showing upload → voice question → tool call → spoken answer. Hiring managers *watch* that.

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser["Browser — public/app.js + index.html"]
        MIC[🎙️ MediaDevices.getUserMedia]
        AW[AudioWorklet / ScriptProcessor\nPCM16 16kHz 100ms chunks]
        VIS[Canvas Visualizer\nAnalyserNode]
        PLAY[Playback Queue\nAudioContext + GainNode]
        AUTH[Premium Auth UI\nSign In / Sign Up\nGender + Custom AI Name]
        MIC --> AW --> WS1
        WS2 --> PLAY --> VIS
        AUTH -.-> WS1
    end

    subgraph Server["FastAPI — Clean Architecture"]
        direction TB
        COMP[app/main.py\nComposition Root + Lifespan]
        CORE[app/core/database.py\nDynamic SQLite/Postgres\nasyncpg vs aiosqlite]
        AUTH_BE[app/auth/routes.py\nPOST /api/auth/signup · signin → JWT access_token\npasslib bcrypt + PyJWT HS256 + session cookie]
        AUTH_JWT[app/auth/jwt.py\ncreate_access_token/decode/verify\nJWT_SECRET_KEY, exp=7d, issuer=voice-doc-assistant]
        PRES[Presentation\nroutes/health, routes/documents (Bearer JWT)\nwebsockets/stream?token=<jwt> (hydrates DB)\ndependencies/auth (Bearer/Cookie/Query)]
        APPL[Application\nservices/document_service (LangChain)\nservices/retrieval_service (Vector+Hybrid)\nservices/summary_service]
        INFRA[Infrastructure\nrepositories/pgvector (+memory fallback)\ndocument/langchain_extractor + splitter\nembeddings/openai + local hashing\nrealtime/factory + providers]
        DOM[Domain\nentities.Document + Chunk + User\ninterfaces.DocumentRepository + TextExtractor]
        DB[(SQLite dev_voice_assistant.db\nor Postgres pgvector)]
        WS1 -- "WS /ws/stream?token=<jwt> or\n?username&voice_gender + Bearer cookie" --> PRES --> APPL --> DOM
        PRES -- "upload/list/delete (Authorization: Bearer <jwt>)" --> APPL --> INFRA
        AUTH_BE --> AUTH_JWT --> CORE --> DB
        APPL -- "provider == mock?" --> MOCK[Mock Loop\nheuristic + personalized greeting]
        APPL -- "provider == openai" --> OAI[OpenAI Realtime\nwss://api.openai.com/v1/realtime]
        APPL -- "provider == gemini" --> GEM[Gemini Live via google-genai\nbidiGenerateContent v1beta\nspeech_config Aoede/Charon]
        MOCK & OAI & GEM -- "tool: query_document(query)" --> INFRA
        INFRA -- "context block (vector similarity)" --> APPL
        APPL -- "audio_chunk + text (as custom_ai_name)" --> WS2
        INFRA -.-> DB
        COMP -. wires .-> PRES & APPL & INFRA & DOM & CORE
    end

    subgraph Obs["Observability"]
        TRACES[OpenInference spans\nws.session, llm.inference,\ntool.query_document, embedding.batch]
        PHOENIX[(Phoenix / OTLP / Console\n gRPC fixes 405)]
        TRACES --> PHOENIX
    end

    subgraph Eval["Evaluation"]
        EVAL[evaluation/run_evals.py\n13 multilingual samples\nFaithfulness + Relevance]
    end

    subgraph Vector["Vector Store"]
        PG[(pgvector Postgres\nvector(1536) HNSW\nor in-memory cosine)]
        PG -.-> INFRA
    end
```

**Key design decisions:**

- **No build step on the frontend** — vanilla JS + Tailwind CDN means `public/` deploys anywhere as static files.
- **Provider abstraction** — `settings.resolved_provider` (`auto` → `google-genai` gemini → `openai` → `mock`) lets the same WebSocket handler run with or without spend. **Gemini Live now uses `os.getenv("GEMINI_LIVE_MODEL", "gemini-2.0-flash-live-preview-04-09")` single target — no multi-candidate 1008 loop.**
- **Ordered audio scheduling** — `nextPlayTime` prevents overlapping/glitchy playback.
- **Graceful mock + vector fallback** — Works fully offline; tool calling simulated via keyword + hashing embeddings; LangChain/pgvector degrade to in-memory when not configured.
- **Tool logs are silent** — `public/app.js` filters `isToolConversion` and TTS is off by default; only final assistant audio plays.

---

## Features That Get You Hired

| Area | What It Does | Why It Matters |
|---|---|---|
| **Realtime Voice** | AudioWorklet captures float32 → PCM16, base64 chunks → WS; upstream audio scheduled via `AudioBufferSourceNode` | Proves you can build low-latency streaming, not just CRUD |
| **Tool Calling** | `query_document(query: str)` JSON schema registered with upstream model; intercepted, executed locally, injected back as `function_call_output` | Production RAG pattern — model *decides* when to retrieve |
| **Document Processing (LangChain)** | `LangChainExtractor` tries `PyPDFLoader` → `pypdf`, `Docx2txtLoader` → `python-docx`, `UnstructuredExcelLoader` → `openpyxl` → `latin-1` fallback; **fixes “binary fallback”** for PDF/DOCX/XLSX | Shows you handle real binary formats, not just `utf-8` |
| **Chunking (LangChain)** | `LangChainSplitter` wraps `RecursiveCharacterTextSplitter(800,100, separators=["\n\n","\n"," ",""])` — respects paragraphs/sentences vs naive sliding window | Better grounding, multilingual-aware |
| **Vector Search (pgvector)** | `PgVectorRepository` — `text-embedding-3-small` (1536d) via `OpenAIEmbeddingProvider` with deterministic hashing fallback; `vector(1536)` column + `ivfflat`/`HNSW`, cosine `ORDER BY embedding <=> $1`, per-user `WHERE user_email`, `threshold` & `HYBRID_ALPHA` | Semantic search: `cancellation policy` matches German `Kündigungsfrist`; in-memory fallback when no `VECTOR_DB_URL` |
| **Enterprise Auth & DB** | **JWT bearer auth** (`PyJWT` HS256, `JWT_SECRET_KEY`, `exp` 7d, `iss=voice-doc-assistant`, `GET /me`, `POST /refresh`) + **SQLAlchemy 2.0** `User` (`id` UUID, `username`/`email` unique indexed, `hashed_password` bcrypt, `preferred_ai_gender`, `custom_ai_name`, `created_at`), **dynamic engine** `postgresql+asyncpg` (prod, `DATABASE_URL`) ↔ `sqlite+aiosqlite` (`./dev_voice_assistant.db` dev), `passlib[bcrypt]`, `POST /api/auth/signup|signin` return `access_token`/`session_token` + `httponly` cookie + `Authorization: Bearer <jwt>` for `documents` + `WS /ws/stream?token=<jwt>`, **WebSocket hydration** — AI stays in character as `custom_ai_name` via `LiveConnectConfig(speech_config=Aoede/Charon, system_instruction="You are {custom_ai_name}...")` | Production-grade **stateless JWT** + env-aware persistence — SQLite locally, Postgres on Render; personalized voice & persona, 401 on invalid/expired token |
| **Legacy Auth (fallback)** | Lightweight `?username&email` + headers still supported for guest/demo when DB not used | Zero-cost demo path kept |
| **Observability** | Every `ws.session`, `llm.inference`, `tool.intercept`, `embedding.batch`, `vector.search` wrapped in `traced_span()` with OpenInference conventions; **Phoenix `register(protocol="grpc", headers={"api_key": ...})` fixes 405** | Shows LLM Ops maturity — you measure, not just ship |
| **Multilingual Eval** | `evaluation/run_evals.py` across 7 language buckets, CJK char-bigram fallback, per-language breakdown | Demonstrates awareness of tokenization, hallucination, and i18n pitfalls |
| **Free-Tier Ready** | `Dockerfile`, `$PORT`, CORS `*`, `StaticFiles` mount at `/app`, `.env.example`, **in-memory vector fallback** | Reviewer can deploy in 5 minutes without Postgres |

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| **Backend** | Python 3.11+, FastAPI, **SQLAlchemy 2.0** (`async` + `asyncpg`/`aiosqlite`), `PyJWT` (HS256 bearer), `websockets`, Uvicorn, Pydantic Settings | Async WebSocket + DB + stateless JWT, `Depends(get_db)`, `Bearer` via header/cookie/`?token`, `lifespan` dynamic engine, `passlib[bcrypt]` |
| **Frontend** | HTML5, Tailwind CSS (CDN), vanilla JS (ES Module) — **4-screen premium auth flow** | Web Audio API, AudioWorklet, `navigator.mediaDevices`, Canvas, togglable Sign In/Sign Up, `localStorage session_token` -> `Authorization: Bearer <jwt>` + `WS ?token=` |
| **AI** | **google-genai** (`gemini-2.0-flash-live-preview-04-09` via `bidiGenerateContent` v1beta, `os.getenv` single target) / OpenAI Realtime (`gpt-4o-realtime-preview`) — now **personalized** `LiveConnectConfig(speech_config=Aoede/Charon, system_instruction="You are {custom_ai_name}...")` | Auto-selected; `self.client.aio.live.connect(model=model_name)` pattern |
| **Document** | **LangChain** (`langchain`, `langchain-community`, `langchain-text-splitters`) + `pypdf`, `python-docx`, `openpyxl`, `docx2txt`, `unstructured` | `LangChainExtractor` + `LangChainSplitter` adapters in `infrastructure/document/` |
| **Retrieval** | **pgvector** (Postgres `vector(1536)`) + `asyncpg` + **in-memory cosine** fallback; `OpenAIEmbeddingProvider` (`text-embedding-3-small`) + hashing fallback; hybrid keyword blend | `PgVectorRepository` in `infrastructure/repositories/pgvector.py` |
| **Auth & DB** | **PyJWT** + **SQLAlchemy 2.0** + **asyncpg**/`aiosqlite` + **passlib[bcrypt]** + `email-validator` | `app/auth/jwt.py` (`create_access_token`/`decode`/`verify`, `JWT_SECRET_KEY`/`HS256`/`7d`), `app/core/database.py` (dynamic `DATABASE_URL` switch), `app/auth/routes.py` (`/api/auth/signup|signin` -> `access_token`, `/me`, `/refresh`, `/signout`), `User` model |
| **Observability** | OpenTelemetry SDK, `opentelemetry-exporter-otlp-proto-grpc` (fixes 405), OpenInference `OpenAIInstrumentor`, Arize Phoenix (`register(protocol="grpc", headers={"api_key": ...})`) | `PHOENIX_COLLECTOR_ENDPOINT` per docs, console fallback for placeholder keys |
| **Evaluation** | `ragas` (`faithfulness`, `answer_relevance`), `datasets` (`Dataset`), custom TF-IDF + char-bigram fallback | Offline heuristic when no `OPENAI_API_KEY`, `evaluate()` when key present |
| **Deploy** | Docker, Render / Hugging Face Spaces (backend, now with **Postgres**), Vercel / Netlify (frontend) | Single-service fallback via `app.mount("/app", StaticFiles)`, `DATABASE_URL` auto-switch |
| **Tooling** | `Pydantic Settings`, `python-multipart`, `pytest` + `httpx`, `pgvector`, `sentence-transformers` (optional) | 12-factor config, tested WS + upload flows |

---

## Project Structure — Clean Architecture

The backend follows **Clean Architecture** (Robert C. Martin). `app/main.py` is a *thin composition root* (~130 lines) that wires everything — all business logic lives in inner layers with zero framework coupling.

```
voice-doc-assistant/
├── app/
│   ├── config.py                          # 12-factor Settings — now with VECTOR_DB_URL, EMBEDDING_*, CHUNK_*, DATABASE_URL
│   ├── tracing.py                         # OpenInference / OTel setup — register(protocol="grpc", headers={"api_key":...}) fixes 405
│   ├── main.py                            # ★ Composition Root + lifespan (init_db) — only wiring
│   │
│   ├── core/                              # Enterprise persistence
│   │   └── database.py                    # Dynamic engine: postgresql+asyncpg (prod, DATABASE_URL) ↔ sqlite+aiosqlite (dev, ./dev_voice_assistant.db), Base, User model, get_db(), init_db()
│   ├── auth/                              # Enterprise JWT auth
│   │   ├── jwt.py                         # JWT HS256 — create_access_token/decode_access_token/verify_token/get_user_claims_from_any_token (PyJWT, 7d, issuer voice-doc-assistant)
│   │   └── routes.py                      # POST /api/auth/signup|signin -> JWT access_token+session_token+cookie, GET /me (Bearer), POST /refresh, POST /signout; passlib bcrypt, retype_password, gender, custom_ai_name
│   ├── domain/                            # Pure business entities (no dependencies)
│   │   ├── entities.py                    # Document + Chunk dataclasses
│   │   └── interfaces.py                  # Ports: DocumentRepository, TextExtractor, TextSplitterPort, EmbeddingProvider, RealtimeProvider
│   │
│   ├── application/services/              # Use cases — orchestrate domain + infra
│   │   ├── document_service.py            # Upload / list / delete — LangChainExtractor + LangChainSplitter + embedding + pgvector
│   │   ├── retrieval_service.py           # query_document — async vector cosine + hybrid keyword fallback
│   │   └── summary_service.py             # Session summary worker — BackgroundTasks + mock SMTP to email
│   │
│   ├── infrastructure/                    # Adapters — how the outside world is reached
│   │   ├── document/                      # LangChain adapters
│   │   │   ├── langchain_extractor.py     # PyPDFLoader/Docx2txtLoader/UnstructuredExcelLoader + pypdf/docx/openpyxl fallback
│   │   │   └── langchain_splitter.py      # RecursiveCharacterTextSplitter(800,100) + fallback
│   │   ├── embeddings/                    # Embedding providers
│   │   │   ├── openai_embeddings.py       # text-embedding-3-small + hashing fallback (offline)
│   │   │   └── local_embeddings.py        # Factory + sentence_transformers
│   │   ├── repositories/
│   │   │   ├── memory.py                  # In-memory DocumentRepository (kept for fallback)
│   │   │   └── pgvector.py                # PgVectorRepository (pgvector + in-memory FAISS-like, auto-selects)
│   │   └── realtime/
│   │       ├── factory.py                 # Provider selection (auto → gemini/google-genai → openai → mock)
│   │       ├── mock_provider.py           # Heuristic + personalized greeting as custom_ai_name
│   │       ├── openai_provider.py         # wss://api.openai.com/v1/realtime + dynamic voice/instruction
│   │       └── gemini_provider.py         # google-genai Live — os.getenv("GEMINI_LIVE_MODEL") + speech_config Aoede/Charon + system_instruction as custom_ai_name
│   │
│   └── presentation/                      # HTTP & WS — thin controllers (JWT-aware)
│       ├── dependencies/auth.py           # SessionUser (+user_id/gender/custom_ai_name/token_type), get_session_user (Bearer/Cookie/?token -> verify or 401 else guest), get_current_user (strict Bearer 401), resolve_user_from_ws (WS JWT via ?token/header/cookie), bearer_scheme
│       ├── routes/
│       │   ├── health.py                  # GET /health (public)
│       │   ├── documents.py               # POST /api/upload, GET/DELETE /api/documents (Authorization: Bearer <jwt> -> 401 if invalid, guest fallback if no token)
│       │   └── auth.py (via app/auth/routes.py) # POST /api/auth/signup|signin (JWT), GET /me, POST /refresh, POST /signout
│       └── websockets/stream.py           # WS /ws/stream?token=<jwt>&username&voice_gender&language — JWT via ?token/header/cookie (401 if invalid), hydrates User from DB, injects LiveConnectConfig, BackgroundTasks summary
│
├── public/
│   ├── index.html                         # Tailwind SPA — 4 screens: Landing/Auth (togglable Sign In/Sign Up), Config, Cinematic Processing (3s), Dashboard (TTS off for tools)
│   └── app.js                             # Premium auth (fetch /api/auth/*, validation, localStorage), voice cards, Web Audio + isToolConversion filter
├── evaluation/
│   ├── run_evals.py                       # Ragas + datasets + heuristic (Dataset.from_dict, evaluate([faithfulness, answer_relevance]))
│   └── eval_results.json                  # Generated
├── requirements.txt                       # Now includes sqlalchemy, asyncpg, aiosqlite, passlib[bcrypt], PyJWT, langchain, pgvector, google-genai
├── Dockerfile                             # Python 3.11-slim, respects $PORT, serves /app static
├── .env.example                           # Now with DATABASE_URL, VECTOR_DB_URL, EMBEDDING_*, JWT_SECRET_KEY, PHOENIX_COLLECTOR_ENDPOINT
└── README.md
```

> **Dependency rule:** `presentation → application → domain ← infrastructure`. Inner layers never import FastAPI or `websockets`. `DocumentService` now injects `TextExtractor`/`TextSplitterPort`/`EmbeddingProvider` via ports — `infrastructure/document/` is the only place that imports `langchain`. `PgVectorRepository` is the only place that imports `asyncpg`/`pgvector`.

**Required files from the spec (all complete, no placeholders):**

| Spec | File | Lines | Canonical Location |
|---|---|---|---|
| 1 | `app/config.py` | 220+ | `app/config.py` — now with `VECTOR_DB_URL`, `EMBEDDING_*`, `CHUNK_*`, `PHOENIX_COLLECTOR_ENDPOINT`, `GEMINI_LIVE_MODEL` |
| 2 | `app/main.py` | ~265 | **Thin** — wiring + `register(protocol="grpc", headers={"api_key":...})` + `require_auth` + `async with self.client.aio.live.connect` |
| 2a | Document RAG | 235 | `app/application/services/document_service.py` — LangChain extraction/splitting + vector embedding |
| 2b | Document CRUD | 68+ | `app/infrastructure/repositories/memory.py` + new `pgvector.py` (vector search) |
| 2c | Auth | 100 | `app/presentation/dependencies/auth.py` — `SessionUser`, `get_session_user` |
| 2d | REST | 26+40 | `app/presentation/routes/health.py` + `documents.py` |
| 2e | WS Orchestration | 62+132+174+314 | `presentation/websockets/stream.py` + `infrastructure/realtime/{mock,openai,gemini}_provider.py` (gemini now single-model, no loop) |
| 3 | `evaluation/run_evals.py` | 514+ | Ragas + datasets + heuristic (Dataset.from_dict) |
| 4 | `public/index.html` | 232 | Polished Tailwind UI — TTS toggle now off by default (tool logs silent) |
| 5 | `public/app.js` | 725 | AudioWorklet, base64 PCM16, playback queue, `isToolConversion` filter |

---

## Quick Start

### Prerequisites

- Python 3.11+
- A modern browser (Chrome/Edge/Firefox — AudioWorklet requires secure context; `localhost` is fine)
- *Optional:* `GOOGLE_API_KEY` or `OPENAI_API_KEY` for live voice, `VECTOR_DB_URL` for persistent pgvector (else in-memory vectors)

### 1. Clone & Install

```bash
git clone https://github.com/cyril-pierro/voice-doc-assistant.git
cd voice-doc-assistant

python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# For local Postgres + pgvector (optional, for persistent vectors):
# docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pass --name pgvector pgvector/pgvector:pg16
```

### 2. Configure (Optional)

```bash
cp .env.example .env
# Edit .env — or just run in mock mode with no keys & SQLite
# For persistent auth + vectors on Render: set DATABASE_URL=postgresql://... (auto-switches from SQLite)
# For pgvector: set VECTOR_DB_URL=postgresql://user:pass@localhost:5432/voice_docs
# For local embeddings (no OpenAI spend): set EMBEDDING_PROVIDER=sentence_transformers
```

> **Mock + SQLite + in-memory vector is the default.** No Postgres, no OpenAI key, no Phoenix needed — `dev_voice_assistant.db` is auto-created, `text-embedding-3-small` falls back to hashing, and `pgvector` falls back to in-memory cosine. Works on first `git clone`.

### 3. Run

```bash
uvicorn app.main:app --reload --port 8000
# Lifespan will auto-create tables (users, documents, chunks) on first boot — no Alembic needed for prototype
```

- **Frontend (served by FastAPI):** http://localhost:8000/app/
- **API docs (Swagger):** http://localhost:8000/docs — try `POST /api/auth/signup` → `POST /api/auth/signin` → `POST /api/upload` → `WS /ws/stream`
- **Health:** http://localhost:8000/health

### 4. Try It — JWT Auth Flow

```bash
# 1) Sign Up (creates User with bcrypt hash, custom AI name, voice gender) -> JWT bearer
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"secret123","retype_password":"secret123","preferred_ai_gender":"female","custom_ai_name":"Aria"}'
# → {"user":{"id":"...","custom_ai_name":"Aria","preferred_ai_gender":"female"},"session_token":"<jwt>","access_token":"<jwt>","token_type":"bearer","expires_in":604800}

# 2) Sign In (validates hash, returns JWT bearer + httponly cookie)
curl -X POST http://localhost:8000/api/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"secret123"}' -c cookies.txt
# → {"access_token":"eyJhbGciOiJIUzI1NiJ...","token_type":"bearer"} + sets session_token httponly cookie
# Save token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/signin -H "Content-Type: application/json" -d '{"email":"alice@example.com","password":"secret123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 3) Verify token
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer $TOKEN"
# → {"id":"...","username":"alice","email":"alice@example.com","custom_ai_name":"Aria"}
curl http://localhost:8000/api/auth/me --cookie "session_token=$TOKEN"  # also works via cookie
curl "http://localhost:8000/api/auth/me?token=$TOKEN"                   # or via ?token query

# 4) Upload as authenticated user (Bearer required; guest fallback only if no token and AUTH_REQUIRE_EMAIL=false)
curl -X POST "http://localhost:8000/api/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@contract.pdf"
# → {"chunks": 12, "vector": true, "embedding_model": "text-embedding-3-small"}
# 401 if token invalid/expired: {"detail":"Invalid or expired bearer token"}

# 5) List / delete with bearer
curl http://localhost:8000/api/documents -H "Authorization: Bearer $TOKEN"

# 6) Refresh token (issue new 7-day JWT)
curl -X POST http://localhost:8000/api/auth/refresh -H "Authorization: Bearer $TOKEN"

# 7) Connect WS — authenticate via ?token=<jwt> (JS WebSocket can't set headers)
# Backend hydrates User from DB via JWT email, injects LiveConnectConfig(speech_config=Aoede, system_instruction="You are Aria...")
# Frontend does: new WebSocket(`ws://localhost:8000/ws/stream?token=${TOKEN}&voice_gender=female&language=english`)
# Also accepts: Authorization: Bearer header (if proxy) or session_token cookie
# On disconnect, BackgroundTasks logs: [MOCK SMTP] To: alice@example.com Subject: Your Voice-Doc Summary — Session ... with Aria
# WS with invalid token -> close code 1008
```

# Run the multilingual eval (no keys needed)
python evaluation/run_evals.py --pretty
cat evaluation/eval_results.json  # now includes dataset_columns + ragas_used
```

---

## Configuration

All settings are in `app/config.py` and load from environment / `.env` (Pydantic Settings). The most important for a demo:

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | `dev-secret-change-in-production-please-set-JWT_SECRET_KEY` | **HS256 signing secret** for JWT bearer tokens — set a strong random value in production (`openssl rand -hex 32`). Rotating it invalidates all tokens. |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `JWT_EXPIRE_MINUTES` | `10080` (7 days) | JWT expiry — also `httponly` cookie `max_age` |
| `DATABASE_URL` | *(none)* | **Dynamic DB switch** — if `postgresql://...` → `postgresql+asyncpg` (Render, Neon), else `sqlite+aiosqlite:///./dev_voice_assistant.db` (local). Also `VECTOR_DB_URL` alias for pgvector. |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | *(none)* | Google AI key — enables Gemini Live (`gemini-2.0-flash-live-preview-04-09` via `os.getenv`) |
| `GEMINI_LIVE_MODEL` | `gemini-2.0-flash-live-preview-04-09` | **Single production live model** — `bidiGenerateContent` only accepts live preview, not base `gemini-2.0-flash` |
| `OPENAI_API_KEY` | *(none)* | `sk-...` — enables OpenAI Realtime (`gpt-4o-realtime-preview`) |
| `VOICE_PROVIDER` | `auto` | `auto` / `openai` / `gemini` / `mock` — `auto` prefers `google-genai` |
| `VECTOR_DB_URL` / `DATABASE_URL` | *(none)* | Postgres DSN for pgvector; if None, uses **in-memory cosine** (free tier) |
| `EMBEDDING_PROVIDER` | `openai` | `openai` or `sentence_transformers` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | `text-embedding-3-small` (1536d) or `multilingual-e5` for JP/AR |
| `EMBEDDING_DIM` | `1536` | Must match `pgvector` column |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `100` | LangChain `RecursiveCharacterTextSplitter` params |
| `VECTOR_TOP_K` / `VECTOR_THRESHOLD` / `HYBRID_ALPHA` | `3` / `None` / `0.5` | Retrieval tuning |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006/v1/traces` | **Per docs** — Phoenix Cloud: `https://app.phoenix.arize.com/s/...` |
| `PHOENIX_API_KEY` | *(none)* | For Arize Cloud (header `api_key`) |
| `ENABLE_TRACING` / `TRACING_EXPORTER` | `true` / `console` | `console` (local) or `phoenix` (cloud, uses `protocol="grpc"` — fixes 405) |
| `MAX_UPLOAD_MB` / `MAX_DOCUMENTS_IN_MEMORY` | `10` / `50` | Legacy limits (pgvector ignores `MAX_DOCUMENTS`) |

**Enterprise Auth DB:** No extra config — `DATABASE_URL` drives both `User` table and pgvector. For local dev, keep it empty to use SQLite file `dev_voice_assistant.db` (gitignored, auto-created via `Base.metadata.create_all`).

See `.env.example:1` for a complete, commented template.

---

## API Reference

### `GET /health`

Liveness probe. No auth.

```json
{
  "status": "ok",
  "service": "Voice-Doc Assistant",
  "version": "0.1.0",
  "provider": "mock",
  "documents": 3,
  "tracing": true
}
```

### Auth — Bearer JWT

All protected routes accept **JWT bearer tokens** issued by `POST /api/auth/signup|signin`:

```
Authorization: Bearer <access_token>
Cookie: session_token=<jwt>
Query: ?token=<jwt>  (for WebSocket, JS can't set headers)
```

Invalid/expired tokens → `401 {"detail":"Invalid or expired bearer token"}` with `WWW-Authenticate: Bearer`. Legacy `?username&email` / `X-Username` fallback still works for guest/demo when no token is sent (if `AUTH_REQUIRE_EMAIL=false`).

### `POST /api/upload`

Upload a document — now via **LangChain**. Auth via **Bearer JWT** (preferred) or query params/headers (guest fallback).

```bash
# Authenticated (recommended)
curl -X POST "http://localhost:8000/api/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@contract.pdf"
# Guest fallback (dev only, no token)
curl -X POST "http://localhost:8000/api/upload?username=alice&email=alice@example.com" \
  -F "file=@contract.pdf"
```

| Param | Location | Description |
|---|---|---|
| `file` | multipart | ` .txt`, `.md`, `.pdf`, `.docx`, `.xlsx`, `.xls`, `.json`, `.csv` (10 MB) |
| `Authorization` | header | `Bearer <jwt>` — validated via `PyJWT` HS256, 401 if invalid |
| `session_token` | cookie | `httponly` JWT set by signin/signup, accepted as Bearer fallback |
| `token` | query | `?token=<jwt>` — accepted for WS and REST convenience |
| `username` / `X-Username` | query/header | Legacy guest fallback — defaults to `guest` if no Bearer |
| `email` / `X-Email` | query/header | Legacy guest fallback — defaults to `guest@example.com` |

**Response (now with vector):**

```json
{
  "id": "a1b2c3d4e5f6",
  "filename": "contract.pdf",
  "size_bytes": 48210,
  "text_length": 12403,
  "chunks": 16,
  "preview": "Refund Policy: 30 days...",
  "uploaded_by": "alice@example.com",
  "vector": true,
  "embedding_model": "text-embedding-3-small"
}
```

### `POST /api/auth/signup`

Enterprise sign-up — creates `User` with `bcrypt` hash, **issues JWT bearer**. Validates `password == retype_password`, checks `email`/`username` unique.

```bash
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "email": "alice@example.com",
    "password": "secret123",
    "retype_password": "secret123",
    "preferred_ai_gender": "female",
    "custom_ai_name": "Aria"
  }'
# → 201 { "message":"Account created","user":{"id":"...","custom_ai_name":"Aria"},"session_token":"<jwt>","access_token":"<jwt>","token_type":"bearer","expires_in":604800 }
# Also sets httponly cookie `session_token` (JWT, 7d) + `user_email`/`username` cookies
# JWT payload: sub=id, username, email, preferred_ai_gender, custom_ai_name, iat, exp, iss=voice-doc-assistant, type=access
```

| Field | Required | Validation |
|---|---|---|
| `username` | yes | 2-50 chars, `^[a-zA-Z0-9_-]+$`, unique |
| `email` | yes | `EmailStr`, unique, indexed, lowercased |
| `password` | yes | 6-128 chars, `bcrypt` hashed (never plain) — SHA-256 pre-hash beyond 72B for bcrypt limit |
| `retype_password` | yes | Must equal `password` |
| `preferred_ai_gender` | yes | `male`/`female` → drives `Aoede`/`Charon` |
| `custom_ai_name` | yes | 1-50 chars — AI stays in character as this name |

### `POST /api/auth/signin`

Validates `email`+`password` against `hashed_password`, **issues JWT bearer**.

```bash
curl -X POST http://localhost:8000/api/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"secret123"}' -c cookies.txt
# → 200 { "message":"Signed in","user":{"custom_ai_name":"Aria"},"session_token":"<jwt>","access_token":"<jwt>","token_type":"bearer","expires_in":604800 }
# Also sets httponly cookie
# 401 on bad credentials
```

### `GET /api/auth/me`

Returns current user if **JWT bearer valid** (header/cookie/`?token`). No guest fallback.

```bash
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer $TOKEN"  # 200 {id,username,email,...}
curl http://localhost:8000/api/auth/me --cookie "session_token=$TOKEN"   # via cookie
curl "http://localhost:8000/api/auth/me?token=$TOKEN"                    # via query
# 401 {"detail":"Missing bearer token"} or {"detail":"Invalid or expired token: ..."}
```

### `POST /api/auth/refresh`

Issues a new JWT if current token still valid (extends 7d).

```bash
curl -X POST http://localhost:8000/api/auth/refresh -H "Authorization: Bearer $TOKEN"
# → 200 { "access_token":"<new jwt>", "expires_in":604800 }
```

### `POST /api/auth/signout`

Clears `session_token`/`user_email`/`username` cookies. Client should also clear `localStorage session_token`.

```bash
curl -X POST http://localhost:8000/api/auth/signout -b cookies.txt
```

### `GET /api/documents` / `DELETE /api/documents/{id}`

`Authorization: Bearer <jwt>` validated; invalid → 401. Authenticated user sees own docs (via `USER_DOCUMENTS`/`user.email` per-user isolation, vector `WHERE user_email`). Guest (no token, `AUTH_REQUIRE_EMAIL=false`) sees all — dev/demo mode.

```bash
curl http://localhost:8000/api/documents -H "Authorization: Bearer $TOKEN"
curl -X DELETE http://localhost:8000/api/documents/<id> -H "Authorization: Bearer $TOKEN"
```

### Enterprise Persistence — `app/core/database.py`

- **Dynamic switch:** `DATABASE_URL` env var → if `postgresql://` → `create_async_engine("postgresql+asyncpg://...", pool_pre_ping=True)`; else `sqlite+aiosqlite:///./dev_voice_assistant.db` (`check_same_thread=False`)
- **SQLAlchemy 2.0:** `DeclarativeBase`, `Mapped[str]`, `mapped_column`, `AsyncSession`, `async_sessionmaker`, `select(User).where(...)`
- **User model:** `id: String(36) UUID PK`, `username` unique indexed, `email` unique indexed, `hashed_password` String(255), `preferred_ai_gender` String(10), `custom_ai_name` String(100), `created_at` DateTime UTC
- **Lifespan:** `app/main.py` `lifespan` calls `await init_db()` → `Base.metadata.create_all` on startup (no `alembic` needed for prototype; compatible with `alembic revision --autogenerate`)
- **Local:** `dev_voice_assistant.db` auto-created, gitignored. **Prod (Render):** set `DATABASE_URL=postgresql://user:pass@host/db` (Neon/Supabase) → auto-switches to `asyncpg` with pooling.

---

## WebSocket Protocol — Hydrated + JWT

**Endpoint:** `WS /ws/stream?token=<jwt>&voice_gender=female&language=english` — **JWT bearer auth** (primary) + **hydrated from DB** per spec.
Also accepts `Authorization: Bearer <jwt>` header (if proxy forwards) or `session_token` cookie, fallback to `?username=&email=` for guest/dev.

On connect, backend validates **JWT via `get_user_claims_from_any_token()`** (PyJWT HS256, `exp`/`iss` check; invalid → close `1008`), loads `User` by `email` from **SQLite/Postgres** via `AsyncSessionLocal` + `select(User).where(email==...)`, extracts `username`, `preferred_ai_gender`, `custom_ai_name`, and injects into `LiveConnectConfig`:

```python
# app/presentation/websockets/stream.py (hydration)
# session_token -> User -> username/custom_ai_name/gender
user.username = db_user.username  # DB is authoritative
custom_ai_name = db_user.custom_ai_name  # e.g., "Milo"
voice_name = "Aoede" if db_user.preferred_ai_gender=="female" else "Charon"
# -> gemini_provider: LiveConnectConfig(
#      speech_config=VoiceConfig(prebuilt_voice_config=Aoede/Charon),
#      system_instruction="You are Milo, ... The user's name is Alice... Stay in character as Milo..."
#    )
```

On `BackgroundTasks` disconnect, `summary_service.send_summary_email(username, email, voice_gender, session_id, custom_ai_name)` logs mock SMTP to the validated `email`.

**Endpoint (legacy query still works for guest):** `WS /ws/stream?username=alice&email=alice@example.com`
**Frontend now does:** ``new WebSocket(`ws://host/ws/stream?token=${localStorage.session_token}&voice_gender=${gender}&language=${lang}`)`` — `public/app.js:buildWsUrl()` + `getAuthHeaders()` for REST.

**Auth matrix:**

| Client | How to send JWT | Server check |
|---|---|---|
| REST (`/api/upload`, `/api/documents`, `/api/auth/me`) | `Authorization: Bearer <jwt>` (preferred) or `Cookie: session_token=<jwt>` or `?token=<jwt>` | `get_session_user` / `_extract_token_payload` → 401 if present but invalid, guest fallback if no token and `AUTH_REQUIRE_EMAIL=false` |
| WebSocket (`/ws/stream`) | `?token=<jwt>` (JS can't set headers) or `Authorization` header or `Cookie` | `resolve_user_from_ws` → 1008 if token invalid, `None` if `AUTH_REQUIRE_EMAIL=true` and no token |
| Guest/demo | `?username=alice&email=alice@example.com` + `X-Username`/`X-Email` | Allowed only when no Bearer sent and `AUTH_REQUIRE_EMAIL=false` |

### Client → Server

```json
{ "type": "audio_chunk", "data": "<base64 PCM16 16kHz 100ms>" }
{ "type": "text", "text": "What does my document say about refunds?" }
```

### Server → Client

```json
{ "type": "text", "text": "Gemini Live connected (gemini-2.0-flash-live-preview-04-09) — speak or type!" }
{ "type": "tool_call", "tool": "query_document", "query": "...", "result_preview": "[Source 1 | contract.pdf | similarity=0.87]..." }
{ "type": "text", "text": "Based on your documents..." }
{ "type": "audio_chunk", "data": "<base64 PCM16 24kHz>", "sample_rate": 24000 }
{ "type": "turn_complete" }
```

### The `query_document` Loop

```python
# app/infrastructure/realtime/gemini_provider.py — now single-model, no loop
model_name = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.0-flash-live-preview-04-09")
self.client = genai.Client(api_key=api_key)
async with self.client.aio.live.connect(model=model_name, config=live_config) as session:
    # 1. Forward browser audio → session.send_realtime_input(audio=Blob)
    # 2. On tool_call -> await retrieval_service.query(query, user_id) (vector cosine)
    # 3. Inject -> session.send_tool_response(FunctionResponse(...))
    # 4. Forward audio/text -> browser

# app/application/services/retrieval_service.py — vector-first
query_embedding = await embedding_provider.embed(query)
results = await vector_repo.search(query_embedding, user_id, top_k=3)  # pgvector or in-memory
# fallback to keyword if no hits
```

---

## Frontend Deep Dive — 4-Screen Premium Auth

**Files:** `public/index.html` + `public/app.js` — **4 screens: Landing (togglable Sign In/Sign Up) → Config → Cinematic Processing (3s) → Dashboard**

- **Screen 1 — Landing & Auth:** Hero + `Get Started` → premium auth card with tabs `Sign In` (Email+Password) / `Sign Up` (Username, Email, Password, Retype Password, **Voice Gender cards ♀ Aoede / ♂ Charon**, **Custom AI Name** `What do you want to call your AI assistant?`). Subtitle: *“Your email will be used to send an automated conversation summary, and the AI model will adapt dynamically to your username.”* `fetch POST /api/auth/signup|signin` with validation, stores **`access_token`/`session_token` JWT** in `localStorage session_token` + `httponly` cookie + `username`/`custom_ai_name`, then `showScreen("config")`. Later REST calls use `getAuthHeaders() → Authorization: Bearer <jwt>`, WS uses `buildWsUrl() → ?token=<jwt>`.
- **Screen 2 — Config:** Drag-drop upload (now LangChain) + voice selector (synced from signup, changeable) + `Launch Assistant` → uploads pending files → `startProcessing()`
- **Screen 3 — Cinematic:** Glowing orbs, `progressBar` 0→100% over 3s, steps `Initializing vocal synthesis...` → `Indexing via pgvector...` → `Tuning Aoede/Charon...`
- **Screen 4 — Dashboard:** Visualizer, pulsing mic, session trackers, log card — `isToolConversion` filter keeps tool logs silent, TTS off by default.

**Files:** `public/index.html` (Tailwind SPA), `public/app.js` (streaming + premium auth state)

| Concern | Implementation | File |
|---|---|---|
| **Capture** | `getUserMedia` → `AudioWorklet` (PCM16) → base64; fallback `ScriptProcessor` | `public/app.js:180` |
| **Playback** | `base64ToArrayBuffer` → `AudioBuffer` → `AudioBufferSourceNode` @ `nextPlayTime` → `GainNode` | `public/app.js:380` |
| **TTS** | **Now filtered** — `isToolConversion = text.includes("[Source")` → **tool logs never spoken**, toggle **off by default** (`<input id="ttsToggle">` no `checked`) | `public/app.js:308`, `public/index.html:184` |
| **Visualizer** | `AnalyserNode` 64-bar spectrum + waveform | `public/app.js:430` |
| **No build** | ES Module, Tailwind CDN | `public/index.html:12` |

---

## Enterprise Auth & Persistence — JWT

**Why it matters on a resume:** Shows you can ship **stateful + stateless, secure, env-aware** backends — SQLite locally (zero setup, `dev_voice_assistant.db` gitignored) → PostgreSQL on Render via `DATABASE_URL` (Neon/Supabase) with `asyncpg` pooling, `bcrypt` hashing, **stateless JWT bearer** + `WebSocket` personalization.

- **JWT Layer `app/auth/jwt.py`:** `PyJWT` HS256 — `create_access_token(user_id, username, email, preferred_ai_gender, custom_ai_name, expires_minutes=JWT_EXPIRE_MINUTES)` → `payload{sub, username, email, preferred_ai_gender, custom_ai_name, iat, exp, iss="voice-doc-assistant", type="access"}`; `decode_access_token()` verifies `exp`+`iss`+`HS256`; `verify_token()` safe wrapper; `get_user_claims_from_any_token()` tries JWT then legacy opaque fallback (for migration). `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_EXPIRE_MINUTES` in `app/config.py`.
- **DB Layer `app/core/database.py`:** `Base(DeclarativeBase)`, `User` model, `_resolve_database_url()` checks `DATABASE_URL` env or `Settings.DATABASE_URL` — `postgresql://` → `postgresql+asyncpg://` (prod), else `sqlite+aiosqlite:///./dev_voice_assistant.db` (dev). `lifespan` in `app/main.py` calls `await init_db()` → `Base.metadata.create_all` (Alembic-compatible).
- **Auth `app/auth/routes.py`:** `CryptContext(schemes=["bcrypt"])` → `hash_password`/`verify_password` (never plain text, SHA-256 pre-hash beyond 72B). `SignupRequest` validates `password==retype_password`, `email`/`username` unique (409), `preferred_ai_gender` male/female, `custom_ai_name`. `SigninRequest` validates hash, **returns `access_token`+`session_token` (both JWT) + `token_type:bearer`+`expires_in` + `httponly` cookie** + `user` object. `GET /me` (Bearer/cookie/`?token` → 401/200), `POST /refresh` (re-issues 7d JWT), `POST /signout` (clears cookies). `_create_session_token()` is now JWT primary (legacy opaque via `_create_legacy_session_token` only on fallback).
- **Dependencies `app/presentation/dependencies/auth.py`:** `SessionUser` now `user_id`/`preferred_ai_gender`/`custom_ai_name`/`token_type`/`language`. `get_session_user(request, ...)` — **Bearer priority** header>cookie>`?token` → `get_user_claims_from_any_token()` → `SessionUser` or `401` if token present but invalid; else legacy `X-Username`/`?username&email` else `guest` (if `AUTH_REQUIRE_EMAIL=false`). `get_current_user()` strict 401 if no valid Bearer. `resolve_user_from_ws()` same for WS → `None` → `1008` on invalid. `bearer_scheme = HTTPBearer(auto_error=False)` for Swagger `Authorize`.
- **Hydration `app/presentation/websockets/stream.py`:** On `WS /ws/stream?token=<jwt>&voice_gender=&language=`, validates JWT via `resolve_user_from_ws`, extracts `token` from `?token`>header>cookie, loads `User` via `AsyncSessionLocal` + `select(User).where(email==claims.email)` (or email from JWT), overrides `user.username`/`preferred_ai_gender`/`custom_ai_name` as source of truth, then `LiveConnectConfig(speech_config=Aoede/Charon, system_instruction="You are {custom_ai_name}... The user's name is {username}...")`. Frontend `public/app.js:buildWsUrl()` appends `&token=${sessionToken}` and `getAuthHeaders()` sends `Authorization: Bearer`.
- **Summary Worker `app/application/services/summary_service.py`:** `BackgroundTasks` on `finally:` → `send_summary_email(username, email, voice_gender, session_id, custom_ai_name, transcript)` logs `[MOCK SMTP] To: email Subject: Your Voice-Doc Summary — Session ... with Aria` with transcript.

**Try it:**
```bash
# Local SQLite (no DATABASE_URL needed) — creates ./dev_voice_assistant.db on first run
# Production: set DATABASE_URL=postgresql://user:pass@host/db on Render + JWT_SECRET_KEY=$(openssl rand -hex 32)
# Rotate JWT_SECRET_KEY -> all tokens invalidated (stateless)
```

---

## LLM Observability & Tracing

Every span is wrapped in `traced_span()` (`app/tracing.py`) with OpenInference conventions. **Fixes:**

- **405 → `protocol="grpc"`:** `phoenix.otel.register(protocol="grpc", headers={"api_key": ...})` (Arze Cloud only accepts gRPC, not HTTP)
- **401 spam:** Dummy JWT (`ApiKey:1/2`) detected → fallback to `console` exporter, strips `/s/...` share path to host-only for gRPC

| Span | Attributes | When |
|---|---|---|
| `ws.session` | `session.id`, `user.id`, `provider` | WS connect |
| `llm.inference` | `input.text`, `output.text` | Each utterance |
| `tool.query_document` | `retrieval.mode` (`vector`/`keyword_fallback`), `similarity` | `query_document` |
| `embedding.batch` / `vector.search` | `model`, `dim`, `top_k`, `similarity` | Ingestion/retrieval |

```bash
# Console — default, no Phoenix needed
TRACING_EXPORTER=console uvicorn app.main:app --reload
# Phoenix Cloud per docs — now with correct endpoint + gRPC
PHOENIX_COLLECTOR_ENDPOINT='https://app.phoenix.arize.com/s/fiopapa32' PHOENIX_API_KEY=... TRACING_EXPORTER=phoenix uvicorn app.main:app --reload
```

---

## Multilingual Evaluation Framework

`evaluation/run_evals.py:44` — 13 samples, `ragas` + `datasets`:

- **Dataset:** `Dataset.from_dict({question, contexts, ground_truth, answer})` per spec — `EN/FR/ES/DE/JA/AR + code-switching`
- **Metrics:** `ragas.evaluate(dataset, metrics=[faithfulness, answer_relevance])` when `OPENAI_API_KEY` set, else heuristic TF-IDF + CJK char-bigram fallback → `evaluation/eval_results.json` with `dataset_columns`, `ragas_used`
- **Run:** `python evaluation/run_evals.py --pretty` (offline) or with `OPENAI_API_KEY` for LLM judge

---

## Vector Search & LangChain

**Why pgvector?** Your `retrieval_service:55` Jaccard (`refund == return` → 0) now becomes cosine (`cancellation policy` ≈ `Kündigungsfrist`). Added after you requested “full langchain adapter” and “vector db choice” (chosen **pgvector** per `memory.py:8` roadmap).

- **Ingestion:** `LangChainExtractor` → `RecursiveCharacterTextSplitter` → `OpenAIEmbeddingProvider` (or hashing) → `PgVectorRepository.save_with_embeddings()` (both Postgres + in-memory)
- **Retrieval:** `RetrievalService.query()` is now **async** `vector -> keyword hybrid`; `HYBRID_ALPHA=0.5` will enable RRF blend when you turn it on
- **Clean:** `DocumentService(extractor, splitter, embedding_provider, vector_repo)` all injected via `domain/interfaces.py` Ports — `domain` never imports `langchain`
- **Try paraphrase:** Upload doc with `Refund policy: 30 days`, query `return procedure` — vector returns `similarity=0.45` where keyword returned `"No relevant context"`

---

## Deployment — Free Tier

### Single Service (Easiest) — now with optional Postgres

**Render:**
1. Web Service → Docker (`Dockerfile` respects `$PORT`)
2. Env: `VOICE_PROVIDER=mock` (no keys) or `GOOGLE_API_KEY` + `VECTOR_DB_URL` (for persistent vectors)
3. Health: `/health`

**Without `VECTOR_DB_URL`:** Uses **in-memory vectors** — no Postgres needed, survives until restart (perfect for demo). Add `pgvector` later via `docker run pgvector/pgvector:pg16` or Neon/Supabase.

**Env for prod (Phoenix Cloud + pgvector):**
```bash
VOICE_PROVIDER=gemini
GOOGLE_API_KEY=AIza...
GEMINI_LIVE_MODEL=gemini-2.0-flash-live-preview-04-09
VECTOR_DB_URL=postgresql://user:pass@host:5432/db
PHOENIX_COLLECTOR_ENDPOINT='https://app.phoenix.arize.com/s/...'
PHOENIX_API_KEY=...
```

---

## What I'd Build Next

| Now | Next | Why |
|---|---|---|
| **pgvector + hashing** | **Hybrid RRF** (`HYBRID_ALPHA` already in config) + `Cohere rerank` | Better precision |
| In-memory fallback | **Persistent Postgres** with `HNSW` + `S3` for originals | Scale beyond 50 docs |
| `latin-1` fallback still exists | **Unstructured + OCR** for scanned PDFs | Real-world PDFs |
| Console fallback for dummy key | **Alerting** on `retrieval.latency_ms` p95 | SLOs |
| Single WS | **Barge-in**, VAD tuning | Voice UX |

---

## Recruiter FAQ

**"Does it actually talk?"** Yes — `google-genai` Live (`self.client.aio.live.connect(model="gemini-2.0-flash-live-preview-04-09")`) or mock silent placeholder. Tool logs are **never spoken**.

**"Can I run it without API keys or Postgres?"** Yes — `mock` + `hashing embeddings` + `in-memory vectors` + `console` tracing. `git clone` → `pip install -r requirements.txt` → `uvicorn app.main:app` → upload PDF/DOCX/XLSX (now via LangChain, not binary fallback) → paraphrase search works.

**"What did you actually build?"** 3,500+ lines across 20+ modules. Check `app/infrastructure/document/langchain_extractor.py` (PyPDFLoader → pypdf fallback), `app/infrastructure/repositories/pgvector.py` (pgvector + in-memory), `app/application/services/retrieval_service.py` (vector-first, async).

---

## Development

```bash
pip install mypy ruff
ruff check app/ evaluation/
mypy app/

pytest -q
python -c "from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); print(c.get('/health').json())"
python evaluation/run_evals.py --pretty
# Test vector paraphrase:
# curl -X POST "http://localhost:8000/api/upload?username=test&email=test@example.com" -F "file=@test.txt"
# python -c "import asyncio; from app.application.services.retrieval_service import retrieval_service; print(asyncio.run(retrieval_service.query('return procedure', 'test@example.com'))[:300])"
```

---

## License

[MIT](LICENSE)

---

<p align="center">
  Built for reviewers who <code>git clone</code> first and ask questions later.<br/>
  <a href="https://github.com/cyril-pierro/voice-doc-assistant">⭐ Star it if you liked the architecture</a>
</p>
