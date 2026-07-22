# Crucible

Autonomous AI Agent Evaluation & Evolution Platform. Crucible generates adversarial scenarios, simulates multi-turn interactions against your AI agents, judges their behavior with LLM-as-a-judge, detects behavioral drift via embeddings, and proposes system prompt evolutions to fix failures.

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

Each node is budget-aware and the graph halts if `total_cost_usd` exceeds the configured maximum.

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
| Observability | OpenTelemetry (metrics + tracing) |

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
#   OPENAI_API_KEY (required for LLM operations)
#   JWT_SECRET_KEY (change from default for any non-local use)
#   CREDENTIAL_ENCRYPTION_KEY (generate via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

### 2. Start infrastructure

```bash
make up
# Starts: Postgres, Redis, Qdrant, MinIO
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

### Health
| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/health` | API + DB health check | Public |

## Multi-Tenancy

Every resource belongs to a `Tenant`. Users are scoped to exactly one tenant. All queries filter by `tenant_id` to ensure strict data isolation.

## Roles

- **Admin**: Full access — create agents, delete runs, set baselines, view AI Operations
- **Member**: Can trigger evaluations and view results, cannot modify agents or baselines

## Rate Limiting

- Auth endpoints (login/register): 10 req/min per IP
- General API: 60 req/min per IP
- Health/docs: unlimited

## Running Tests

```bash
# Unit tests (no infrastructure needed)
pytest tests/test_agents/ tests/test_connectors/ tests/test_core/ -v

# Integration tests (requires Postgres + Redis running)
make up
pytest tests/ -v
```

## Linting & Formatting

```bash
make lint    # ruff check
make format  # ruff format
```

## Production Deployment

```bash
docker-compose -f docker-compose.prod.yml up --build -d
```

This starts all services including nginx reverse proxy on port 80. Set production environment variables:
- `JWT_SECRET_KEY` — strong random secret
- `CREDENTIAL_ENCRYPTION_KEY` — Fernet key
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
- `ALLOW_ORIGINS` — your frontend domain

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_SECRET_KEY` | Yes | Secret for signing JWTs |
| `CREDENTIAL_ENCRYPTION_KEY` | Yes | Fernet key for encrypting agent auth configs |
| `OPENAI_API_KEY` | Yes (for LLM) | OpenAI API key for GPT-4o |
| `ANTHROPIC_API_KEY` | No | Fallback LLM provider |
| `POSTGRES_*` | Yes | Database connection |
| `REDIS_HOST/PORT` | Yes | Redis for broker + pubsub |
| `QDRANT_URL` | Yes | Vector database for drift analysis |
| `S3_ENDPOINT_URL` | Yes | MinIO/S3 for trace storage |

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
scripts/                     # Integration/load test scripts
```

## License

Private — All rights reserved.
