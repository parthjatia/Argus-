-- EXAMPLE prices (USD per 1M tokens) so cost_usd is filled in for the demo.
-- They are illustrative, not authoritative: check your provider's current pricing.
-- A model can have several rows; the worker uses the one valid at the span's start_time.

INSERT INTO model_prices (model, input_per_1m_usd, output_per_1m_usd, valid_from) VALUES
  -- Debate Arena fake provider
  ('fake-small',  0.10,  0.40, '2024-01-01T00:00:00Z'),
  ('fake-large',  1.00,  4.00, '2024-01-01T00:00:00Z'),
  ('fake-large',  0.80,  3.20, '2026-06-01T00:00:00Z'),  -- price drop: shows versioning
  -- Local models via Ollama cost nothing per token
  ('llama3.1',    0.00,  0.00, '2024-01-01T00:00:00Z'),
  -- Paid API examples
  ('gpt-4o-mini', 0.15,  0.60, '2024-07-18T00:00:00Z'),
  ('gpt-4o',      2.50, 10.00, '2024-10-01T00:00:00Z')
ON CONFLICT (model, valid_from) DO NOTHING;
