# CLAUDE.md — Argus: LLM Observability Platform

Argus is a self-hosted tracing tool for LLM applications (a small, focused Langfuse).
A Python SDK records every model call and tool call as nested spans. A FastAPI + Redis + Postgres
pipeline ingests them, and a Next.js dashboard shows traces, costs, latency and errors.

The repo also contains the **AI Debate Arena**: a demo multi-agent app (two LLMs debate, a third judges)
that exists only to produce realistic traces, including injected failures.

This is a one-person portfolio project, built in about one week. Favour simple, correct and
well-explained code over clever or "enterprise" code.

---

## Working agreement for Claude (read first)

I'm building this to learn and to explain it in interviews. Follow these rules:

1. **Speed over explanations. Don't wait for an OK before coding**, including core components.
   Make sensible decisions yourself, follow this file, and record non-obvious choices in
   "Design decisions".
2. **Dependencies:** well-known packages that fit the Stack below may be added without asking;
   list them in the summary. Prefer the standard library where reasonable.
3. **Only stop and ask** if truly blocked or a decision is hard to undo.
4. **After each milestone:** run all tests and ruff, fix failures, and give a summary of at most
   5 lines. **Never run `git commit`**: leave changes for me to review and commit, and never add
   Claude as co-author or contributor (no `Co-Authored-By` / "Generated with" lines).
5. **Keep `docs/LEARNING.md`:** for every core component (SDK context/nesting, SDK exporter,
   API key auth, ingestion + idempotency, Redis Stream worker), add a short beginner-friendly
   explanation of how it works and what would break without it.
6. **Never put secrets in code.** Use `.env` (git-ignored) and keep `.env.example` up to date.
7. If something in this file is wrong or outdated, say so and fix it.

---

## Stack

| Area | Choice |
|---|---|
| SDK | Python 3.12, standard library (`contextvars`, `threading`, `queue`), `httpx` |
| Server | FastAPI, Pydantic v2, `psycopg` (v3, async) + `psycopg_pool`, Uvicorn |
| Buffer | Redis Streams (consumer group) |
| Database | PostgreSQL (JSONB for prompts/outputs) |
| Dashboard | Next.js (App Router) + TypeScript, Tailwind, Recharts |
| Demo LLMs | `fake` provider (offline, deterministic), Ollama (OpenAI-compatible API), optional paid API |
| Tooling | Docker Compose, pytest, ruff, k6 or Locust for load tests |

Ports: server `8000`, dashboard `3000`, Postgres `5432`, Redis `6379`, Ollama `11434` (host).

---

## Repository layout

```
argus/
├── CLAUDE.md
├── README.md                 # architecture diagram, design decisions, demo GIF
├── docker-compose.yml        # postgres, redis, server, worker, dashboard
├── .env.example
├── Makefile
├── sdk/                      # pip install -e sdk  (dist name argus-sdk, import argus)
│   ├── argus/
│   │   ├── __init__.py       # init(), observe, span, wrap_openai, flush, get_stats
│   │   ├── client.py         # global state: init(), active exporter, atexit flush
│   │   ├── context.py        # contextvars: current span
│   │   ├── spans.py          # Span object + span() context manager (sync + async)
│   │   ├── decorators.py     # @observe for sync + async functions
│   │   ├── exporter.py       # bounded queue + background batch sender
│   │   ├── serialize.py      # JSON-safe conversion + truncation
│   │   ├── models.py         # SpanEvent (dataclass, wire format)
│   │   └── integrations/openai.py   # wraps OpenAI-compatible clients
│   ├── examples/e2e_trace.py # sends a nested trace to a running server
│   └── tests/
├── server/
│   ├── app/
│   │   ├── main.py           # FastAPI app
│   │   ├── auth.py           # API key → project_id
│   │   ├── api/ingest.py     # POST /v1/ingest
│   │   ├── api/traces.py     # trace list + detail
│   │   ├── api/metrics.py    # cost, latency, error rate
│   │   ├── pricing.py        # cost from tokens using model_prices
│   │   ├── schemas.py
│   │   └── db.py
│   ├── app/streams.py        # stream / group names shared by API and worker
│   ├── app/storage.py        # idempotent batch INSERT + cost from model_prices
│   ├── worker/consumer.py    # Redis Stream → Postgres batch writer
│   ├── migrations/           # plain SQL files, applied in order (0002 = example prices)
│   └── tests/
├── dashboard/                # Next.js 15 (App Router) + Tailwind 4 + Recharts 3
│   ├── app/traces/           # list (filters, cursor paging) + [id] waterfall page
│   ├── app/metrics/          # cost / latency / error-rate page
│   ├── components/           # TraceView (waterfall + span detail), charts, badges
│   └── lib/                  # api.ts (server-side client), tree.ts, format.ts (+ tests)
├── demo/                     # Docker image built from the repo root (needs ../sdk)
│   ├── arena/
│   │   ├── debate.py         # one debate = one trace (python -m arena.debate)
│   │   ├── agents.py         # debater + judge prompts, JudgeVerdict validation + retry
│   │   ├── tools.py          # fake lookup_fact() with failure injection
│   │   ├── faults.py         # ARENA_* failure injection settings
│   │   └── llm.py            # provider switch: fake | ollama | openai (+ fault layer)
│   ├── topics.yaml
│   ├── traffic.py            # runs many debates to generate traces
│   └── tests/
└── loadtest/                # ingest.js (k6), run_load.py, chaos.py, results/
```

---

## Data flow

```
Demo app ──@observe──▶ SDK queue ──batch (HTTP)──▶ POST /v1/ingest ──XADD──▶ Redis Stream "spans"
                                                       (202 Accepted)              │
                                                                     XREADGROUP (group "writers")
                                                                                   ▼
                                                             worker: batch INSERT … ON CONFLICT DO NOTHING
                                                                                   ▼
                                                                    PostgreSQL ◀── server read API ◀── dashboard
```

---

## Event format (SDK → server)

The SDK sends a span **when it ends** (one event per finished span).

```json
{
  "batch": [
    {
      "span_id": "3f1c…-…",         // UUIDv4 (Python uuid.uuid4()), generated by the SDK
      "trace_id": "9a7e…-…",        // UUIDv4
      "parent_span_id": null,       // null = root span
      "name": "debate.round",
      "kind": "span",               // span | generation | tool
      "start_time": "2026-11-10T10:00:00.123Z",
      "end_time": "2026-11-10T10:00:01.456Z",
      "status": "ok",               // ok | error
      "error_message": null,
      "model": null,                // set for kind=generation
      "input": {},                  // JSON, truncated if large
      "output": {},
      "input_tokens": null,
      "output_tokens": null,
      "metadata": {}
    }
  ]
}
```

---

## Database schema (initial)

```sql
CREATE TABLE projects (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(id),
  key_hash TEXT NOT NULL UNIQUE,          -- store a hash, never the raw key
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);

CREATE TABLE spans (
  project_id UUID NOT NULL REFERENCES projects(id),
  id UUID NOT NULL,                       -- span_id from the SDK → idempotent inserts
  trace_id UUID NOT NULL,
  parent_span_id UUID,                    -- no FK: children may arrive before parents
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('span','generation','tool')),
  start_time TIMESTAMPTZ NOT NULL,
  end_time TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','error')),
  error_message TEXT,
  model TEXT,
  input JSONB,
  output JSONB,
  input_tokens INT,
  output_tokens INT,
  cost_usd NUMERIC(12,6),
  metadata JSONB,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, id)            -- span IDs are unique per project, not globally
);
CREATE INDEX spans_trace_idx   ON spans (project_id, trace_id);
CREATE INDEX spans_project_idx ON spans (project_id, start_time DESC);

CREATE TABLE model_prices (
  model TEXT NOT NULL,
  input_per_1m_usd NUMERIC(12,6) NOT NULL,
  output_per_1m_usd NUMERIC(12,6) NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (model, valid_from)
);
```

Trace-level views (total cost, duration, status) are computed from `spans` at read time.
Only add a `traces` summary table if reads become slow, and if so, update totals only for rows
actually inserted (`RETURNING`), so duplicate events never double-count cost.

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/ingest` | Header `X-API-Key`. Validate batch, XADD to Redis, return `202` |
| GET | `/v1/traces` | List traces. Filters: status, model, min cost, min duration, time range. Cursor pagination |
| GET | `/v1/traces/{trace_id}` | All spans of a trace (flat). The dashboard builds the tree |
| GET | `/v1/metrics/cost` | Cost per day, per model |
| GET | `/v1/metrics/latency` | p50 / p95 duration per model |
| GET | `/v1/metrics/errors` | Error rate over time |
| GET | `/health` | Liveness, plus DB and Redis connectivity |

---

## SDK requirements (core — explain before coding)

- **Public API:** `argus.init(api_key, host)`, `@argus.observe(name=None, kind="span")`,
  `with argus.span("name"):` and `argus.wrap_openai(client)`.
- **Nesting:** the current span lives in a `contextvars.ContextVar`. It must work for nested sync
  functions, `async` functions and concurrent `asyncio` tasks.
- **Errors in user code:** mark the span `status=error`, record the message, then re-raise.
  Never swallow the user's exception.
- **The SDK must never break or slow down the host app:**
  - Bounded queue (default 10,000 events). When full, drop the oldest and count drops.
  - Background daemon thread sends a batch every 1 s or every 100 events.
  - Retries with exponential backoff + jitter on network errors and 5xx. No retry on 4xx.
  - Short HTTP timeouts. SDK errors are logged, never raised into user code.
  - `atexit` flush with a time limit.
- **Payload size:** truncate `input` / `output` above a configurable limit (default 32 KB).

## Worker requirements (core — explain before coding)

- Consumer group `writers` on stream `spans`. Read up to 500 messages per batch.
- Insert with `INSERT … ON CONFLICT (project_id, id) DO NOTHING`, then `XACK` **only after the DB commit**.
- On startup and periodically, use `XAUTOCLAIM` to take over messages stuck with a dead consumer.
- Compute `cost_usd` from `model_prices` (the price valid at `start_time`).
- Cap the stream length with `MAXLEN ~` on `XADD`.

---

## Demo app: AI Debate Arena

- `run_debate(topic, pro_model, con_model, judge_model, rounds=3)`
- Each debate is one trace. Each round and turn is a span, each LLM call a generation, and each
  `lookup_fact()` call a tool span.
- The judge returns JSON `{winner, scores, reasoning}` validated with Pydantic. On invalid JSON,
  retry once. Both attempts must be visible in the trace.
- `LLM_PROVIDER=fake|ollama|openai` (Ollama and paid APIs via an OpenAI-compatible base URL).
  The `fake` provider returns canned answers with realistic token counts, for tests and load tests.
- Failure injection via env vars: `ARENA_TIMEOUT_RATE`, `ARENA_BAD_JSON_RATE`, `ARENA_SLOW_RATE`.
- `traffic.py` runs N debates in parallel to fill the dashboard.

## Dashboard

- `/traces`: table with name, start time, duration, cost, status, models. Filters.
- `/traces/[id]`: waterfall timeline of nested spans. Clicking a span shows input, output,
  tokens, cost and error.
- `/metrics`: cost per day, p50/p95 latency per model, error rate.

---

## Commands (target)

```bash
cp .env.example .env
docker compose up -d --build      # postgres, redis, server, worker, dashboard
make migrate                      # apply SQL migrations
make seed                         # create a demo project + print its API key
make demo TOPIC="..."             # one debate (LLM_PROVIDER from .env, default fake)
make traffic N=50                 # run many debates
make test                         # pytest for sdk + server
make lint                         # ruff
make load                         # k6 load test of the ingestion API (VUS=20 DURATION=30s)
make chaos                        # kill/restart worker + API during traffic, check exactly-once
```

---

## Testing checklist

- [x] SDK: nested sync spans get the correct parent
- [x] SDK: nested async spans and concurrent tasks don't mix up parents
- [x] SDK: exceptions mark the span as error and are re-raised
- [x] SDK: exporter never blocks the caller when the server is down
- [x] SDK: queue overflow drops oldest events and counts them
- [x] Server: invalid API key → 401; invalid batch → 422
- [x] Server: the same batch sent twice → each span stored once
- [x] Server: a child span arriving before its parent is stored and shown correctly (`buildTree` marks it "parent missing")
- [x] Worker: killed mid-batch → messages are reclaimed and stored, no duplicates
- [x] End-to-end: `make demo` produces a complete trace visible in the dashboard
- [x] Load test: record events/second and p95 ingest latency in the README
- [x] Chaos: traffic while killing/restarting worker and API → every accepted span stored exactly once

---

## Milestones

1. **Day 1:** Docker Compose, migrations, seed script, `POST /v1/ingest` writing straight to Postgres
   with idempotent inserts. Tests for auth and duplicates.
2. **Day 2:** SDK: context nesting, `@observe`, exporter with batching and retries,
   `wrap_openai`. Unit tests.
3. **Day 3:** Redis Stream + worker (consumer group, XACK, XAUTOCLAIM). Debate Arena with the
   fake provider and failure injection. `traffic.py`.
4. **Day 4:** Dashboard: trace list, waterfall view, span detail, metrics page.
5. **Day 5:** Load test, README (architecture diagram, design decisions, measured numbers),
   demo GIF. Stretch: PII redaction in the SDK, or prompt replay and compare.

---

## Code conventions

- Type hints everywhere; Pydantic v2 models at every boundary.
- `ruff` for linting and formatting.
- Times are UTC and timezone-aware (`TIMESTAMPTZ`, `datetime.now(timezone.utc)`).
- No ORM magic in hot paths: plain SQL via `psycopg` for inserts and queries.
- Small, focused commits with clear messages.

---

## Design decisions (keep updated)

| Decision | Why | Trade-off |
|---|---|---|
| SDK-generated span IDs + `ON CONFLICT DO NOTHING` | Retries can resend events; inserts must be idempotent | Relies on IDs being truly unique (UUIDv4) |
| Composite `PRIMARY KEY (project_id, id)` on spans; trace index on `(project_id, trace_id)` | A span ID is only unique within its project, so one project can never block (via `DO NOTHING`) or overwrite another project's spans | Every query must filter by `project_id` (it should anyway) |
| UUIDv4 from Python's `uuid` module, stored as Postgres `UUID` | Standard library, no dependency; `UUID` is 16 bytes and validated by Postgres | Random IDs aren't time-ordered, so B-tree inserts land on random index pages. **Future:** UUIDv7 for time-ordered IDs (in the stdlib from Python 3.14) |
| `cost_usd` stays NULL until the worker exists (Milestone 3); `model_prices` table created on Day 1 | Cost is computed in one place (the worker), not in a temporary Day-1 code path | Spans ingested before Milestone 3 have no cost unless backfilled |
| API keys stored as SHA-256 hashes (`argus_` + 32 random bytes) | A database leak doesn't leak usable keys; keys are long and random, so a fast hash is safe and allows an indexed lookup | A lost key can't be recovered, only replaced |
| Migrations: plain SQL files + a `schema_migrations` table, each file in one transaction, guarded by an advisory lock | Each file is applied exactly once and fully or not at all; no migration framework dependency | No down-migrations; fix forward with a new file |
| Tests use a separate `<db>_test` database, recreated per test | Tests never touch dev data and always start from a clean, freshly migrated schema | ~100 ms per DB test; switch to per-session DB + truncation if the suite grows |
| Ingest checks run in order: size (413) → API key (401) → validation (422) → insert (202) | Cheap checks first; unauthenticated callers learn nothing about the format. The body is parsed by hand (`model_validate_json`) because FastAPI parses declared bodies *before* dependencies | Slightly more code than a typed body parameter |
| `202 {"accepted": n}`, no "inserted" count | Once ingest goes through Redis (Milestone 3), the server can't know at request time how many spans are new; the contract stays the same | Clients can't see duplicates; the server logs the inserted count |
| One invalid span rejects the whole batch (`422`) | Simple, clear errors; invalid spans mean an SDK bug | The SDK doesn't retry 4xx, so the valid spans in that batch are lost |
| Unknown span fields are ignored (`extra="ignore"`) | A newer SDK can send new fields to an older server without losing data | Typos in field names aren't caught |
| Limits: 1,000 spans/batch, 5 MB body, 200-char names, `model` required for `generation` | Bound memory and row size per request | Real payloads above 5 MB are rejected (the SDK truncates at 32 KB per field) |
| Postgres errors → `503`; the pool opens without waiting | 5xx tells the SDK to retry; the server starts and `/health` reports the problem while Postgres is down | Requests wait up to `DB_POOL_TIMEOUT_S` (3 s) before the 503 |
| SDK uses stdlib `dataclasses`, not Pydantic | Host apps may pin Pydantic v1 or another v2; the SDK must not cause version conflicts. Only dependency: `httpx` | No automatic validation in the SDK, so it mirrors the server's limits by hand (name ≤ 200, `model` set for generations, …) |
| SDK sanitises every span to pass server validation | The server rejects the **whole batch** on one bad span, and the SDK doesn't retry 4xx; one bad span would lose 99 good ones | Server rules are duplicated in `spans.py` |
| `end_time` = `start_time` + monotonic `perf_counter()` duration | A wall-clock jump (NTP) can't produce `end_time < start_time`, which the server would reject | `end_time` can drift slightly from wall-clock time for very long spans |
| Queue = `deque` + one `threading.Condition`; drop-oldest on overflow | `enqueue()` is O(1), never blocks or does I/O; the newest data is usually the most useful | Oldest events are lost under sustained overload (counted in `get_stats()["dropped"]`) |
| Batches capped at 100 events **and** ~4 MB | Server rejects bodies over 5 MB (413); two 32 KB fields × 100 spans would exceed it | More requests when payloads are large |
| Retries: up to 5, exponential backoff with full jitter (`uniform(0, min(10s, 0.5s·2ⁿ))`), only for network errors and 5xx | Jitter stops many clients retrying in lockstep after an outage; 4xx won't succeed on retry | A long outage (> ~30 s of retries) loses those batches (counted as `failed`) |
| `atexit` flush with a 5 s limit; daemon thread | Spans aren't lost on normal exit, but a dead server can't hang shutdown | Spans still queued after 5 s, or on a hard kill, are lost |
| `wrap_openai` is duck-typed and detects async by the **result** (`isawaitable`) | No `openai` dependency; OpenAI's decorators hide whether `create` is async | Streaming responses are passed through without output/tokens |
| No `contextvars` propagation into new threads | Python doesn't copy context into `threading.Thread` / executors | Spans in worker threads start new traces unless the caller uses `contextvars.copy_context().run(...)` |
| One stream message per span; the `XADD`s of a request in one `MULTI` | Worker batches by message count and trimming is per span; `MULTI` keeps a request all-or-nothing in Redis | More messages than one per batch |
| `MAXLEN ~ 1,000,000` on `XADD` (`STREAM_MAXLEN`) | Bounds Redis memory if the worker is down | A very long worker outage can trim spans that were never stored |
| Worker is synchronous (redis-py + psycopg), a separate `worker` service, same image as the server | Simplest correct loop; scale out by running more workers in the group | One batch at a time per worker process |
| `XACK` only after `COMMIT`; `XAUTOCLAIM` after 60 s idle (`CLAIM_MIN_IDLE_MS`) on startup and every 30 s | At-least-once delivery + idempotent inserts = each span stored exactly once | A dead worker's messages wait at least 60 s before being reclaimed |
| Stable `WORKER_NAME`; on startup and after errors the worker re-reads its own pending messages (`XREADGROUP ... 0`) | A restarted worker finishes its own messages at once instead of waiting for `XAUTOCLAIM` | Two workers must never share a name |
| Poison messages: batch retried row by row, bad ones to the `spans:dead` stream + `XACK` | One bad message can't block ingestion forever | Dead letters need manual inspection |
| `cost_usd` computed inside the `INSERT` (scalar subquery on `model_prices`, latest `valid_from <= start_time`) | One statement, always the current price table, no cache to go stale; history keeps the price that applied then | Price changes don't re-price stored spans (by design) |
| No price or no tokens: `cost_usd` NULL; free models priced explicitly at 0 | "Unknown" and "free" are different facts | Unpriced models show no cost until a price row is added |
| Arena: every provider (including `fake`) is an OpenAI-shaped client wrapped by `wrap_openai` | One code path; the fake provider produces the same span shapes as Ollama/OpenAI | The fake mimics only the response fields we use |
| Faults are injected in a client layer *inside* `wrap_openai` | Injected timeouts and slowness appear in the generation span, like real ones |  |
| Judge: JSON mode + Pydantic `JudgeVerdict`; one retry that includes the validation error; each attempt is a `judge.attempt` span | Both attempts visible in the trace; the retry tells the model what was wrong | Two invalid answers fail the debate (`JudgeFailed`) |
| LLM timeouts retried once per call; a turn that still fails is recorded and the debate continues; a failed judge fails the debate | A realistic mix of partial failures and failed traces |  |
| `traffic.py` uses threads | Each thread starts with an empty context, so each debate is its own trace |  |
| Demo dependencies: PyYAML (topics), `openai` (only for ollama/openai, imported lazily) | Well-known; the fake provider needs no network or API key |  |
| Read API uses the same `X-API-Key` auth; the dashboard calls it only from server components with `ARGUS_API_KEY` from its env | One auth mechanism; the key never reaches the browser | The dashboard shows a single project (the key's) |
| Trace status = root span status (fallback: any span failed while the root is missing) | A trace that recovered from a failed child is a success; failed children are counted separately | `status=error` filters on the root, not on "any error" |
| Keyset pagination on `(start_time, trace_id)`, opaque base64 cursor, `limit + 1` probe | Stable pages while new traces keep arriving; no `OFFSET` scans | Only "next" and "first page" navigation |
| Trace summaries are aggregated from `spans` on every list request | Simplest correct design (see schema notes); no summary table to keep consistent | Cost grows with a project's span count; add a `traces` table only if it gets slow |
| Error rate per bucket uses `generate_series` to fill empty buckets; empty = `null`, not 0% | No misleading gaps or fake zeros | Hourly over 30 days = 720 points (the UI switches to daily buckets above 7 days) |
| Dashboard: filters are a plain GET form (state in the URL) | Shareable links, back button works, no client state | Full page reload on Apply |
| Chart colours: reference palette slots, assigned by the sorted list of models seen in the last 90 days; > 8 models fold into "Other" | A model keeps its colour when the time range changes (colour follows the entity) | One extra metrics query |
| Latency per model drawn as HTML bars (p50 solid, to p95 lighter) instead of a chart | Two percentiles per model with model colours, no clash with the cost chart's colours |  |
| Dashboard unit tests use `node --test` with type stripping (no Jest/Vitest) | No extra dependency; runs in plain `node:22-alpine` | Tested modules must use erasable TypeScript only |
| Next.js `output: "standalone"` image; `npm test` + `next build` (type-check) gate the image build | Small runtime image; a broken tree builder or type error fails `up` | Dashboard code changes need an image rebuild |
| Load test = k6 in Docker on the compose network (`grafana/k6:0.54.0`), driven by `loadtest/run_load.py`; each run uses a fresh project | No host install; per-run project makes stored-row counts exact; measures both ingest (client view) and worker drain rate | k6 shares the machine's CPUs with the system under test |
| Chaos and load drivers are stdlib-only Python on the host | They must run `docker compose kill/start`; no extra dependencies | Need Python 3.12 on the host |
| API process count via `WEB_CONCURRENCY` (read by uvicorn), default 1 | Scale ingest without code changes (measured 9.1k → 21.9k spans/s with 4) | Each process has its own DB pool (max 10 connections) |
| `/health` returns `503` when Postgres or Redis is unreachable | Load balancers and `docker compose` healthchecks react to the status code, not the body | The body says which dependency failed, but details stay in server logs |
| Dev server image includes pytest + ruff; repo mounted at `/repo` | `make test` / `make lint` only need Docker on the host, same environment for everyone | Image is larger than a production image; a slim prod image is a later step |
| Send spans when they end, not start + end events | Simpler protocol, one row per span | Long-running spans are invisible until they finish |
| No FK on `parent_span_id` | Children can arrive before parents | The tree must be built and checked at read time |
| `202 Accepted` after XADD, write to DB in the worker | Fast ingest, absorbs bursts, DB slowdowns don't hit clients | Data sits briefly in Redis; needs Redis persistence (AOF) |
| Cost computed on the server | Prices change; one versioned source of truth | Server needs a maintained price table |
| SDK drops oldest events when the queue is full | Never slow down or crash the host app | Possible data loss under extreme load, so drops are counted |
