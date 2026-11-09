# Argus — How Every Part Works

Nov 9, 2026 · @Parth

Argus records every step of an LLM app and shows it on a dashboard. This page maps each part, what it does and why it exists.

## At a glance

&#91;embedded content: Argus architecture · every part and how data flows\]

Read it top to bottom: the SDK inside your app records each step, the backend stores it safely, and the dashboard shows it. The numbered sections below explain each box.

## The journey of one trace

One debate becomes one trace, and it passes through twelve steps before you see it on screen. Each step is handled by a different part, explained in the sections below.

1. **The Debate Arena runs.** The judge LLM is asked who won the debate.
2. **The SDK records the call** as a span: start time, prompt, answer, model, tokens and duration.
3. **The SDK puts the finished span in a queue in memory.** This is instant, so the debate never waits.
4. **About once a second, a background thread sends a batch** of spans to `POST /v1/ingest` with the API key.
5. **The Ingestion API checks the size, the key and the data format.** The key tells it which project the spans belong to.
6. **The API adds the batch to the Redis Stream** and immediately replies `202 Accepted`.
7. **The worker reads up to 500 waiting spans** from the stream.
8. **The worker adds each span's cost** from the price table.
9. **The worker inserts the spans into Postgres.** Duplicates are skipped automatically.
10. **After the save succeeds, the worker tells Redis "done"** (`XACK`), so those spans are never handed out again.
11. **You open the dashboard.** It asks the Read API for traces, which reads them from Postgres.
12. **The dashboard builds the tree** of debate, rounds, turns and calls, and draws the timeline.

## 1. Debate Arena: the app being watched

The Debate Arena is a small demo app whose only job is to produce realistic LLM activity for Argus to record. It lives in `demo/` and runs as an ordinary Python program.

- **Pro and Con agents:** Two LLMs argue for and against a topic over three rounds.
  - **Why:** Many LLM calls per run, nested into rounds and turns, produce rich traces.
  - **Why two models:** Using different models for each side makes cost and speed comparisons interesting.
- **Judge agent:** A third LLM reads the debate and must answer in fixed JSON: `{winner, scores, reasoning}`.
  - **Why:** Structured output is one of the most common failure points in real AI apps.
  - **What happens on bad JSON:** Pydantic rejects it and the judge is asked once more. Both attempts appear in the trace.
- **`lookup_fact()` tool:** A fake fact lookup the agents call for evidence.
  - **Why:** Real agents call tools, and tools fail. Because you wrote it, you control when it is slow or broken.
- **LLM provider switch (`LLM_PROVIDER`):**
  - `fake` returns instant canned answers with realistic token counts. Used for tests and load tests: free, fast and predictable.
  - `ollama` runs real models locally for free.
  - A paid API is optional, to show real costs.
- **Failure switches:** `ARENA_TIMEOUT_RATE`, `ARENA_BAD_JSON_RATE` and `ARENA_SLOW_RATE` make a chosen share of calls fail or slow down.
  - **Why:** A demo where nothing breaks proves nothing. Injected failures show that Argus captures errors and retries.
- **Traffic generator (`make traffic N=50`):** Runs many debates in parallel.
  - **Why:** It fills the dashboard with enough data for charts, and it stress-tests the pipeline.

## 2. Argus SDK: the recorder inside your app

The SDK is a small Python library, about 300–400 lines, that runs inside the Debate Arena's process. It records every step and sends it to the server without the app noticing. It lives in `sdk/argus/`.

- **`argus.init(api_key, host)`:** Connects the SDK to your Argus server once, at startup.
  - **Why:** Every batch needs the API key and the server address; setting them once keeps app code clean.
- **Span model (`models.py`):** Defines what one recorded step contains: IDs, name, kind (span, generation or tool), start and end time, status, input, output, model and tokens.
  - **Why:** One fixed shape means the server can validate every span the same way.
- **`@observe` decorator and `span()` (`decorators.py`):** Put above a function, it starts a span when the function starts and ends it when it returns.
  - **On errors:** It marks the span as an error, records the message, then re-raises the error.
  - **Why re-raise:** The app must behave exactly as it would without Argus. The SDK never hides the app's own errors.
- **Context tracking (`context.py`, using `contextvars`):** Remembers which span is currently running, so a new span knows its parent.
  - **Why `contextvars` and not a global variable:** In async code, several tasks run interleaved. A global variable would mix up their parents; `contextvars` keeps a separate value per task.
- **`wrap_openai(client)`:** Wraps an OpenAI-compatible client so every LLM call becomes a generation span automatically, with model, prompt, answer and tokens.
  - **Why:** Developers shouldn't have to decorate every single LLM call by hand.
- **Exporter (`exporter.py`):** Sends finished spans to the server in the background.
  - **Bounded queue (10,000 spans):** Finished spans wait here. Adding one is instant.
  - **Background thread:** Sends a batch every second or every 100 spans, whichever comes first. Batches mean far fewer network requests.
  - **Retries with backoff and jitter:** If the server is down or returns a 5xx error, it waits longer after each failed try, plus a little randomness so many apps don't retry at the same moment. It doesn't retry 4xx errors, because the request itself is wrong.
  - **Drop oldest when full:** If the server stays down and the queue fills up, the oldest spans are dropped and counted. Losing some trace data is better than using up the app's memory.
  - **Flush on exit:** When the app shuts down, remaining spans are sent within a short time limit.
- **The golden rule:** The SDK is a guest in someone else's app. It must never slow it down, crash it, or change its behaviour.

## 3. Ingestion API: the front door

The Ingestion API is the FastAPI endpoint `POST /v1/ingest`. Every span enters Argus through it, and it runs four checks in order, stopping at the first one that fails. Cheap checks come first, so a stranger can't make the server do expensive work.

| Order | Check | Fails when | Reply | Why it exists |
| --- | --- | --- | --- | --- |
| 1 | Size check | The request body is too large | `413` | Stops anyone from flooding the server with huge payloads |
| 2 | API key check | The key is missing, wrong or revoked | `401` | Only known apps may write data |
| 3 | Batch validation | The data doesn't match the span format | `422` | Bad data never reaches the database |
| 4 | Hand-off | Redis (later) or Postgres (now) is unreachable | `503` | Tells the SDK to retry later instead of losing data |
| — | All passed | — | `202` | "Accepted, I'll store it" |

- **How the key becomes a project:** The server hashes the key from the `X-API-Key` header and looks the hash up in `api_keys`. The matching row gives the `project_id`.
  - **Why hash:** The database only stores fingerprints of keys, so a leaked database contains no working keys.
  - **Why the same error for wrong and revoked keys:** An attacker can't learn which keys used to exist.
- **The most important rule:** The project comes only from the key, never from the request body. Otherwise a client could simply claim to be another project and write into its data.
- **`require_project` dependency:** The key check is a small reusable function that runs before the endpoint. The read endpoints reuse it later.
- **Pydantic models:** `IngestBatch` (1 to 1,000 spans) and the span model describe what valid data looks like. FastAPI checks every request against them automatically.
- **Why `202` and not `200`:** `202` means "accepted for processing." From Milestone 3, the spans are only queued in Redis at this point, and the worker saves them a moment later.
- **`/health`:** A separate endpoint that checks Postgres and Redis at the same time. It returns `200` when both answer and `503 degraded` when one doesn't, instead of crashing.

## 4. Redis Stream: the waiting line

The Redis Stream is a queue between the Ingestion API and the worker, added in Milestone 3. The API drops spans into it and replies at once; the worker picks them up and saves them. It works like the ticket rail in a restaurant: the waiter pins orders, the cooks take them when ready.

- **`XADD` (add to the line):** The API appends each batch to a stream named `spans`.
  - **Why:** Adding to Redis takes about a millisecond, so the API answers fast even when the database is slow.
- **Consumer group (`writers`):** Redis tracks which worker has taken which message, and which messages are still unconfirmed.
  - **Why:** Several workers can share the work without two of them saving the same message, and Redis knows what a crashed worker never finished.
- **Pending messages:** A message a worker has taken but not yet confirmed stays "pending" in Redis.
  - **Why:** If the worker crashes, nothing is lost. Another worker can claim those pending messages later.
- **Persistence (`--appendonly yes`):** Redis writes every change to a file on disk.
  - **Why:** Redis normally keeps data in memory only. Without this, a Redis restart would lose every span still waiting in line.
- **Length cap (`MAXLEN ~`):** The stream keeps only a limited number of recent entries.
  - **Why:** If the worker stops for a long time, the stream can't grow until it uses up all of Redis's memory.
- **Honest trade-off:** At Argus's size, writing straight to Postgres would also work, and Milestone 1 does exactly that. Redis is added to absorb bursts, decouple the API from database slowdowns, and allow efficient batch inserts. Knowing when this pays off is a good interview point.

## 5. Worker: the writer

The worker is a separate background program, `server/worker/consumer.py`, running in its own container. It moves spans from the Redis line into Postgres, and its order of operations is what guarantees nothing is lost or duplicated.

1. **Read a batch (`XREADGROUP`):** Take up to 500 waiting messages for this worker.
   - **Why batches:** One database insert of 500 rows is far faster than 500 separate inserts.
2. **Add costs:** For each LLM call, look up the model's price in `model_prices`, using the price valid at the span's start time, and calculate `cost_usd`.
   - **Why here and not in the SDK:** Prices change. One versioned price table on the server is the single source of truth, and old traces keep their original cost.
3. **Insert into Postgres with `ON CONFLICT (project_id, id) DO NOTHING`:** If a span with the same project and ID already exists, the new copy is silently skipped.
   - **Why:** Retries mean the same span can arrive twice. This makes saving idempotent: doing it twice has the same effect as doing it once.
4. **Commit the transaction:** The rows are now permanently saved.
5. **Confirm to Redis (`XACK`):** Only now does the worker tell Redis these messages are done.
   - **Why after the commit:** If the worker crashed between saving and confirming, Redis would hand the messages out again, and step 3 skips the duplicates. If it confirmed before saving and then crashed, the spans would be lost forever.
6. **Rescue stuck messages (`XAUTOCLAIM`):** On startup and regularly, the worker takes over messages that a crashed worker left pending for too long.
   - **Why:** Without this, spans taken by a dead worker would wait forever.

**The principle to remember:** at-least-once delivery plus idempotent inserts gives effectively-once storage. Every span is stored exactly once, even when parts crash.

## 6. PostgreSQL: the permanent record

Postgres is the database where every span is stored for good. It survives restarts and answers questions like "all failed calls yesterday" or "most expensive model this week."

| Table | What it holds | Unique key | Why it exists |
| --- | --- | --- | --- |
| `projects` | One row per customer or app using Argus | `id` | Keeps each user's data separate |
| `api_keys` | Hashed keys, each linked to a project | `key_hash` (unique) | Lets the API find a key's project instantly, without storing real keys |
| `spans` | One row per recorded step: times, status, model, input, output, tokens, cost | `(project_id, id)` | The core data of Argus |
| `model_prices` | Price per million input and output tokens, per model | `(model, valid_from)` | Cost calculation, with history when prices change |
| `schema_migrations` | Which migration files have been applied | file name | So each migration runs exactly once |

- **Composite key `(project_id, id)`:** A span is identified by its project and its ID together.
  - **Why:** With the ID alone, one project could send an ID that already exists in another project and block that span. Pairing it with the project keeps customers fully isolated.
- **UUIDs as IDs:** The SDK generates a random UUIDv4 for every span and trace.
  - **Why:** IDs are created in the app, before the server ever sees the span. That's what makes duplicate detection possible.
- **No foreign key on `parent_span_id`:** Normally a database would refuse a child whose parent doesn't exist yet.
  - **Why skip it:** Spans arrive out of order. A child often finishes, and is sent, before its parent does.
- **JSONB for input and output:** Prompts and answers have different shapes, so they're stored as flexible JSON that can still be searched.
- **Indexes:** `(project_id, trace_id)` loads one trace fast; `(project_id, start_time)` lists recent traces fast.
- **Migrations (`make migrate`):** Numbered SQL files change the structure in a fixed order. Each runs once, inside a transaction, so it's applied fully or not at all.
- **Separate test database:** Tests use `argus_test`, recreated for each test, so your real data is never touched.

## 7. Read API and dashboard: seeing the data

The Read API answers questions about stored traces, and the dashboard turns those answers into tables, timelines and charts. Both arrive in Milestone 4.

**Read API (same FastAPI server as ingestion)**

- **`GET /v1/traces`:** Lists traces, with filters for errors, model, minimum cost, minimum duration and time range.
  - **Cursor pagination:** Results come in pages; each page includes a pointer to where the next one starts. This stays fast even with millions of rows, unlike "skip the first 10,000."
- **`GET /v1/traces/{id}`:** Returns every span of one trace as a flat list.
  - **Why flat:** Building the tree is easier and safer in one place, the dashboard, where missing or late parents can be handled.
- **`GET /v1/metrics/cost`, `/latency`, `/errors`:** Cost per day and model, p50 and p95 duration, and error rate over time.
  - **p50 and p95:** The time half of calls finish within, and the time 95% finish within. p95 shows the slow outliers that averages hide.
- **Same key check:** Every read endpoint uses `require_project`, so you only ever see your own project's data.

**Dashboard (Next.js + TypeScript)**

- **`/traces`:** A table of traces with name, start time, duration, cost, status and models, plus filters.
- **`/traces/[id]`:** A waterfall timeline: each span is a bar placed by its start time and sized by its duration, indented under its parent. Clicking a bar shows its prompt, answer, tokens, cost and error.
- **`/metrics`:** Charts for cost per day, latency per model and error rate.
- **Building the tree:** The dashboard links each span to its parent by `parent_span_id`. A span whose parent hasn't arrived yet is shown at the top level instead of disappearing.
- **Why a separate app:** The dashboard only talks to the Read API, never to the database directly, so the backend stays in control of access.

## 8. Docker Compose and tooling: running it all

Docker Compose starts every backend part with one command, identically on any machine. Each part runs in its own container, a sealed box with exactly the software it needs.

- **`docker-compose.yml`:** The recipe listing the services: `postgres`, `redis` and `server` now; `worker` and `dashboard` later.
  - **Why:** No installing Postgres, Redis or Python versions on Windows by hand, and no "works on my machine" problems.
- **Health checks and start order:** Docker keeps asking Postgres and Redis "are you ready?", and the server only starts once both answer.
  - **Why:** Otherwise the server could start first and crash because the database isn't up yet.
- **Private network:** Containers reach each other by name, so the server connects to `postgres:5432` and `redis:6379`.
- **Ports to your computer:** Server on `localhost:8000` (its API docs at `/docs`), dashboard on `localhost:3000`. Ollama, if used, runs outside Docker on port `11434` and is reached from containers via `host.docker.internal`.
- **`.env` and `.env.example`:** Passwords and settings live in `.env`, which git ignores. `.env.example` shows which settings exist without real values.
  - **Why:** Secrets never end up in your code or on GitHub.
- **`server/Dockerfile` and `requirements*.txt`:** The recipe for the server's container: Python 3.12 plus the listed libraries. The dev image also contains pytest and ruff, so tests run inside Docker.
- **`Makefile`:** Shortcuts such as `make up`, `make test`, `make migrate` and `make seed`. On Windows without `make`, run the underlying `docker compose` commands instead.
- **pytest:** Automatic tests that prove each part works, including failure cases.
- **ruff:** Checks code style and common bugs, like a spell-checker for code.
- **git:** Every working step is committed, so you can always go back to a known good state.

## 9. When things fail

Every part of Argus has a planned answer for failure, and these are the strongest interview talking points in the project.

| What fails | What happens | Which part protects you |
| --- | --- | --- |
| A tool call or LLM call in the debate times out | The span is marked as an error with its message, and the retry appears as its own span | SDK `@observe` |
| The judge returns broken JSON | Pydantic rejects it; the judge is asked once more; both attempts are visible | Debate Arena + SDK |
| The Argus server is down | Spans wait in the SDK queue and are retried with backoff; the debate keeps running | SDK exporter |
| The server stays down for a long time | The oldest queued spans are dropped and counted, so the app never runs out of memory | SDK bounded queue |
| The same batch is sent twice | The second copy of each span is skipped | `ON CONFLICT (project_id, id) DO NOTHING` |
| A child span arrives before its parent | It's stored anyway and placed in the tree when the dashboard loads | No foreign key on `parent_span_id` |
| A request has a wrong or revoked key | Rejected with `401`; nothing is stored | `require_project` |
| A request contains invalid data | Rejected with `422`; nothing is stored | Pydantic models |
| Postgres is slow or briefly down | Spans wait in the Redis Stream until the worker can save them | Redis Stream |
| The worker crashes mid-batch | Unconfirmed messages stay pending and are reclaimed; duplicates are skipped | `XACK` after commit + `XAUTOCLAIM` |
| Redis restarts | Waiting spans are reloaded from disk | Redis append-only file |
| Postgres or Redis is unreachable | `/health` reports `503 degraded` instead of crashing | Health check |

## 10. Build status

The foundation is done and the ingest endpoint is being built. Update the Status column as each part is finished.

| Part | Milestone | Status |
| --- | --- | --- |
| Docker skeleton and `/health` | 1 | Done |
| Database tables, migrations, seed and hashed API keys | 1 | Done |
| API key check and `POST /v1/ingest` | 1 | In progress |
| Python SDK | 2 | Not started |
| Redis Stream and worker | 3 | Not started |
| Debate Arena with fake provider, failure switches and traffic | 3 | Not started |
| Ollama provider (optional) | 3 | Not started |
| Read API and dashboard | 4 | Not started |
| Chaos test, load test and README | 5 | Not started |
