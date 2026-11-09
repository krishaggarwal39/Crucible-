# Crucible Dashboard

Next.js App Router frontend for Crucible. See the [root README](../../README.md) for the
full stack, architecture and API reference.

## Running locally

The frontend expects the API on the same origin (proxied) or at
`NEXT_PUBLIC_API_URL`. From the repository root:

```bash
make up            # infrastructure + Celery worker/beat
make dev-backend   # API on :8000
make dev-frontend  # this app on :3000
```

Or directly:

```bash
npm ci
npm run dev
```

## Scripts

| Command | Purpose |
|---------|---------|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Production build |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest suite |

## Configuration

`NEXT_PUBLIC_API_URL` is the only frontend-specific variable, and it is a **build-time**
value — Next.js inlines `NEXT_PUBLIC_*` into the bundle when `npm run build` runs. Setting
it as a container runtime environment variable has no effect; pass it as a Docker build arg
instead (see `Dockerfile` and `docker-compose.prod.yml`).

Leave it empty when the frontend and API are served through the same origin (the bundled
nginx setup). The browser then uses relative URLs, which is also what the SSE `EventSource`
connection needs.

## Conventions

- **Auth**: the access token lives in memory only; the refresh token is an HttpOnly cookie.
  `src/lib/api.ts` handles refresh-on-401 with a shared in-flight promise so concurrent
  failures trigger one refresh, not N.
- **Server state**: React Query throughout. Query keys come from `src/lib/queryKeys.ts` —
  use the factory rather than inline string arrays, so components sharing an endpoint also
  share a cache entry.
- **Polling**: gate `refetchInterval` on `isActiveStatus(...)` so finished runs stop
  polling.
- **Styling**: CSS modules for auth pages and layout chrome; inline styles elsewhere. Shared
  design tokens and the `.glass`, `.animate-spin`, `.animate-fade-in` and `.sr-only`
  utilities live in `src/app/globals.css`. There is no Tailwind in this project, so utility
  class names must be defined there before use.
- **Accessibility**: pair every `<label>` with `htmlFor`/`id`, and give icon-only controls
  an `aria-label`.

## Structure

```
src/
├── app/                  # App Router pages
│   ├── (auth)/           # login, register
│   ├── agents/           # list, detail, create
│   ├── evaluations/      # list, detail (results + live logs), create
│   └── operations/       # drift analytics (admin)
├── components/
│   ├── auth/             # ProtectedRoute
│   ├── dashboard/        # EvaluationTable, ScenarioResults, BaselineToggle
│   └── layout/           # Header, Sidebar, ClientLayout
├── contexts/             # AuthContext
└── lib/                  # api client, query key factory
```
