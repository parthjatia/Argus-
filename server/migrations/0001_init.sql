-- Initial schema. Applied once by app/migrate.py inside a single transaction.

CREATE TABLE projects (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(id),
  key_hash TEXT NOT NULL UNIQUE,          -- SHA-256 hex of the key; never the raw key
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
  cost_usd NUMERIC(12,6),                 -- NULL until the worker computes it (Milestone 3)
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
