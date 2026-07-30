# Crucible

Autonomous AI Agent Evaluation & Evolution Platform. Crucible generates adversarial scenarios, simulates multi-turn interactions against your AI agents, judges their behavior with LLM-as-a-judge, detects behavioral drift via embeddings, and proposes system prompt evolutions to fix failures.

## Demo Results

Real evaluation run against a customer support agent (TechCorp Bot). The default
judge/generator model is configurable via `DEFAULT_JUDGE_MODEL` (currently an
OpenRouter-hosted Nemotron free tier); embeddings use `DEFAULT_EMBEDDING_MODEL`:

```
========== EVALUATION RUN ==========
Status: completed
Scenarios Generated: 3
Passed: 1 | Failed: 2
Average Score: 23.3 / 100

Drift Profile: baseline_missing (first run — no prior baseline to compare)

Evolution Suggestion:
  Failure Pattern: "The agent fails to address specific issues, provide working
  solutions, and handle errors gracefully. Root cause: prompt not tailored to
  specific customer scenarios."
  
  Suggested Prompt Fix: "You are a technical support agent for TechCorp,
  specializing in account issues, billing questions, and technical problems.
  Always maintain a professional tone, decompose complex requests into steps,
  and escalate to a human when confidence is below 80%..."
```

### Running the Demo Locally

```bash
# 1. Start infrastructure + Celery worker/beat
make up

# 2. Start the mock target agent
PYTHONPATH=src python scripts/mock_agent.py &

# 3. Start the API (hot reload)
make dev-backend

# 4. Run the demo script
PYTHONPATH=src python scripts/run_demo.py
```

SSRF protection is disabled when `APP_ENV=development` so the demo can reach a
target agent on localhost. It is enabled automatically outside development, and
can be forced on with `SSRF_PROTECTION_ENABLED=true`.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Next.js UI │────▶│  Nginx Proxy │────▶│  FastAPI API │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                 │
                    ┌────────────────────────────┬┴──────────────────┐
                    │                            │                    │
              ┌─────▼─────┐             ┌───────▼───────┐    ┌──────▼──────┐
              │  Postgres  │             │  Redis        │    │  MinIO/S3   │
              │  (data)    │             │  (pubsub/     │    │  (traces)   │
              └────────────┘             │   broker/     │    └─────────────┘
                                         │   cache)      │
                                         └───────┬───────┘
                                                 │
                                         ┌───────▼───────┐     ┌───────────┐
                                         │ Celery Worker │────▶│  Qdrant   │
                                         │ (LangGraph)   │     │  (vectors)│
                                         └───────────────┘     └───────────┘
```

### LangGraph Pipeline (per evaluation run)

```
Generate Scenarios → Simulate (multi-turn) → Judge → Analyze Drift → Evolve
```

Each node is budget-aware and the graph halts if `total_cost_usd` exceeds the configured
maximum. The budget is checked on every graph edge *and* between batches inside the
fan-out nodes, so a single node cannot blow past the limit before the next edge check.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI + Uvicorn |
| Database | PostgreSQL 16 (asyncpg + SQLAlchemy async) |
| Task Queue | Celery + Redis broker |
| AI Orchestration | LangGraph |
| LLM Gateway | LiteLLM + Instructor (structured output) |
| Vector DB | Qdrant |
| Object Storage | MinIO (dev) / S3 (prod) |
| Real-time | Redis Pub/Sub → SSE (sse-starlette) |
| Frontend | Next.js 16 + React 19 + React Query |
| Auth | JWT (access) + HttpOnly cookie (refresh) + bcrypt |
| Observability | OpenTelemetry (metrics + tracing), off by default — set `OTEL_ENABLED=true` |

## Models

Everything the pipeline needs for chat runs on **OpenRouter free-tier NVIDIA
Nemotron** models, so a single `OPENROUTER_API_KEY` is enough to run Crucible
end to end.

| Role | Default | Notes |
|------|---------|-------|
| Judge / Generator / Evolution | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | Verified to return valid structured output under Instructor's TOOLS mode |
| Fallbacks | `nemotron-3-nano-30b-a3b:free`, `nemotron-nano-9b-v2:free` | Both verified in TOOLS mode |
| Embeddings (drift only) | `gemini/gemini-embedding-001` | Reduced to `EMBEDDING_DIMENSIONS` (768) |

Two things worth knowing before changing these:

- **OpenRouter cannot do embeddings.** It is a chat-completions API with no
  embeddings endpoint, so `DEFAULT_EMBEDDING_MODEL` can never be an
  `openrouter/...` value. Startup rejects that outright with an explanation
  rather than failing once per trace at runtime.
- **`openrouter/openai/gpt-oss-20b:free` is not a usable fallback.** It returns a
  provider error under TOOLS mode, which would turn a transient primary failure
  into a hard one. It is deliberately excluded from `FALLBACK_MODELS`.

Drift analysis is the only feature that needs a second provider. Leave
`DEFAULT_EMBEDDING_MODEL` empty (or omit `GEMINI_API_KEY`) and the pipeline still
generates, simulates, judges and evolves — the drift profile simply reports
`embeddings_disabled` with the reason.

## Prerequisites

- Python 3.12+
- Node.js 20+
- Docker & Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python package manager)

## Quick Start

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env — at minimum set:
#   OPENROUTER_API_KEY (required — the only provider key needed to run)
#   GEMINI_API_KEY (optional — enables drift analysis; see Models below)
#   JWT_SECRET_KEY (change from default for any non-local use)
#   CREDENTIAL_ENCRYPTION_KEY (generate via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

### 2. Start infrastructure

```bash
make up
# Starts: Postgres, Redis, Qdrant, MinIO, the MinIO bucket initialiser,
# and the Celery worker + beat.
#
# JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY have no safe defaults, so compose
# fails immediately with a readable message if .env is missing them.
```

### 3. Install backend dependencies

```bash
make init
# Equivalent to: uv pip install -e ".[dev]"
```

### 4. Run database migrations

```bash
make db-upgrade
# Equivalent to: alembic upgrade head
```

### 5. Start the backend

```bash
PYTHONPATH=src uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 6. Start the Celery worker

```bash
PYTHONPATH=src celery -A backend.worker.celery_app worker --loglevel=info
```

### 7. Start the frontend

```bash
cd src/frontend
npm install
npm run dev
```

The app is now available at `http://localhost:3000`.

## API Endpoints

All endpoints are prefixed with `/api/v1`.

### Auth
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/auth/login` | OAuth2 login (returns access token + sets refresh cookie) | Public |
| POST | `/auth/register` | Create account + tenant | Public |
| POST | `/auth/refresh` | Refresh access token via HttpOnly cookie | Cookie |
| POST | `/auth/logout` | Revoke refresh token | Cookie |
| GET | `/auth/me` | Get current user | Bearer |

### Agent Configs
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/agent-configs/` | Create immutable agent config | Admin |
| GET | `/agent-configs/` | List configs for tenant | Bearer |
| GET | `/agent-configs/{id}` | Get specific config | Bearer |

### Evaluations
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/evaluations/` | Trigger new evaluation run | Bearer |
| GET | `/evaluations/` | List runs for tenant | Bearer |
| GET | `/evaluations/{id}` | Get run details | Bearer |
| POST | `/evaluations/{id}/cancel` | Cancel a running evaluation | Bearer |
| DELETE | `/evaluations/{id}` | Delete run + S3 traces | Admin |
| POST | `/evaluations/{id}/baseline` | Set as golden baseline | Admin |
| POST | `/evaluations/{id}/stream-ticket` | Get SSE auth ticket | Bearer |
| GET | `/evaluations/{id}/stream?ticket=` | SSE event stream | Ticket |

### Results

The judge's per-scenario output is served by these endpoints (and rendered on the
run detail page).

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/evaluations/{id}/results` | Scenarios joined with their trace + judgment | Bearer |
| GET | `/evaluations/{id}/scenarios` | Generated scenarios | Bearer |
| GET | `/evaluations/{id}/judgments` | Judge verdicts (`?passed=` filter) | Bearer |
| GET | `/evaluations/{id}/traces` | Trace metadata | Bearer |
| GET | `/evaluations/{id}/traces/{trace_id}/download` | Presigned raw trace URL (15 min) | Bearer |

### Team Management
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/auth/invite` | Add a member to your tenant | Admin |
| GET | `/auth/team` | List tenant members | Admin |
| DELETE | `/auth/team/{user_id}` | Deactivate a member and revoke their sessions | Admin |

### AI Operations
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/operations/drift` | Drift profiles for recent completed runs | Admin |
| GET | `/operations/summary` | Aggregated drift + baseline metrics | Admin |

### Health

Note: these two are **not** under `/api/v1`.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/health` | Liveness — no dependencies touched | Public |
| GET | `/health/ready` | Readiness — verifies the database | Public |

## Multi-Tenancy

Every resource belongs to a `Tenant`. Users are scoped to exactly one tenant. All queries filter by `tenant_id` to ensure strict data isolation.

## Roles

- **Admin**: Full access — create agents, delete runs, set baselines, manage the team,
  view AI Operations
- **Member**: Can trigger evaluations and read results, cannot modify agents or
  baselines and cannot access `/operations/*`

Email addresses are unique across the whole deployment. Registration and invites both
reject an address that already exists, because login resolves an account by email alone.

## Rate Limiting

- Auth endpoints (login/register): 10 req/min per client
- General API: 60 req/min per client
- Liveness (`/health`), docs and the OpenAPI schema: unlimited

Client identity comes from the socket peer address. `X-Forwarded-For` is only
consulted when `TRUSTED_PROXY_COUNT > 0`, and then the hop appended by your
trusted proxy is used — not the leftmost (caller-supplied) value. Set this
correctly or the limiter will bucket every request behind a proxy together.

If Redis is unavailable the limiter fails **open**, so requests are allowed
through un-throttled.

## Running Tests

```bash
# The whole suite (requires Postgres running; Redis for a few tests)
make up
make test
```

The suite runs against a **separate `<POSTGRES_DB>_test` database**, which it
creates and migrates with the real Alembic chain. `tests/conftest.py` refuses to
start if the resolved database name does not end in `_test`, because the fixtures
truncate tables between tests.

Running the migrations as part of the suite is deliberate: it makes model and
migration drift a test failure rather than a production surprise.

## Linting & Formatting

```bash
make lint       # ruff check src/ tests/
make format     # ruff format src/ tests/
make lint-fe    # eslint + tsc --noEmit in src/frontend
```

## Production Deployment

```bash
docker-compose -f docker-compose.prod.yml up --build -d
```

This starts all services including the nginx reverse proxy on port 80. All of the
following are **required** — compose fails fast with a readable message rather than
starting with an insecure default:

- `POSTGRES_PASSWORD`
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`
- `JWT_SECRET_KEY` — strong random secret
- `CREDENTIAL_ENCRYPTION_KEY` — Fernet key

Recommended:
- `ALLOW_ORIGINS` — your frontend domain (comma-separated list or JSON array)
- `TRUSTED_PROXY_COUNT=1` — required for correct rate limiting behind the bundled nginx
- `APP_ENV=production` (default here) — enables SSRF protection and the Secure cookie flag

Note: `NEXT_PUBLIC_API_URL` is a **build arg**, not a runtime variable. Next.js
inlines `NEXT_PUBLIC_*` at build time. Leave it empty when serving through the
bundled nginx so the browser uses same-origin relative URLs.

nginx terminates plain HTTP on port 80. Put TLS in front of it before exposing
this to the internet — the Secure cookie flag assumes HTTPS.

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | Yes | Secret for signing JWTs |
| `CREDENTIAL_ENCRYPTION_KEY` | Yes | Fernet key for encrypting agent auth configs |
| `OPENROUTER_API_KEY` | **Yes** | The only provider key the pipeline needs — serves the judge, generator and evolution models |
| `GEMINI_API_KEY` | Optional | Embeddings for drift analysis only. OpenRouter has no embeddings endpoint, so drift needs a separate provider. Without it drift reports `embeddings_disabled` and everything else still runs |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GROQ_API_KEY` | No | Unused by the default configuration |
| `POSTGRES_*` | Yes | Database connection |
| `REDIS_HOST/PORT` | Yes | Redis for broker + pubsub |
| `QDRANT_URL` | Yes | Vector database for drift analysis |
| `S3_ENDPOINT_URL` | Yes | MinIO/S3 for trace storage |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Yes | Object storage credentials |
| `TRUSTED_PROXY_COUNT` | Behind a proxy | Trusted proxy count; enables X-Forwarded-For parsing |
| `APP_ENV` | No | `production` enables SSRF protection and Secure cookies |
| `EMBEDDING_DIMENSIONS` | No | Must match the embedding model (default 768) |
| `OTEL_ENABLED` | No | Turn on OpenTelemetry export |

## Project Structure

```
src/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── agents/              # LangGraph nodes (generator, simulator, judge, analyzer, evolution)
│   ├── api/                 # REST endpoints + dependencies
│   ├── connectors/          # External service clients (LLM, S3, target agent)
│   ├── core/                # Config, security, events, telemetry, rate limiting
│   ├── db/                  # SQLAlchemy models, migrations, session
│   ├── schemas/             # Pydantic request/response models
│   └── worker/              # Celery tasks + beat schedule
├── frontend/
│   └── src/
│       ├── app/             # Next.js App Router pages
│       ├── components/      # React components
│       ├── contexts/        # Auth context
│       └── lib/             # API client (axios)
tests/                       # pytest suite
scripts/                     # Demo + local startup scripts (mock agent, run_demo)
```

## License

Private — All rights reserved.


## Architecture Decision Records

Key design decisions are documented in `docs/adr/`:

| ADR | Decision |
|-----|----------|
| [001](docs/adr/001-langgraph-orchestration.md) | LangGraph for evaluation orchestration (over plain async or Celery chains) |
| [002](docs/adr/002-redis-pubsub-sse.md) | Redis Pub/Sub → SSE for real-time streaming (over WebSockets or polling) |
| [003](docs/adr/003-ssrf-protection.md) | TOCTOU-safe SSRF protection via custom network backend (over DNS blocklists) |
