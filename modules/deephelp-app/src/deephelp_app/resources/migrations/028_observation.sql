CREATE TABLE IF NOT EXISTS dh_m28_observation_runs (
  run_id VARCHAR(128) NOT NULL,
  trace_id VARCHAR(32) NOT NULL,
  span_id VARCHAR(16) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  operation_id VARCHAR(128),
  predecessor_trace_id VARCHAR(32),
  predecessor_span_id VARCHAR(16),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS dh_m28_observation_operation
  ON dh_m28_observation_runs(operation_id, kind, created_at);
