-- The Debate Arena now runs only real models: Ollama llama3.2:3b (pro, con) and
-- openai/gpt-oss-120b on Groq's free tier (judge). Both cost nothing per token, so they are
-- priced explicitly at 0: "free" is a known fact, unlike a model with no price row (NULL cost).
-- The fake provider is gone, so its example prices are removed (fix forward, never edit 0002).

DELETE FROM model_prices WHERE model IN ('fake-small', 'fake-large');

INSERT INTO model_prices (model, input_per_1m_usd, output_per_1m_usd, valid_from) VALUES
  ('llama3.2:3b',         0.00, 0.00, '2024-01-01T00:00:00Z'),
  ('openai/gpt-oss-120b', 0.00, 0.00, '2024-01-01T00:00:00Z')
ON CONFLICT (model, valid_from) DO NOTHING;
