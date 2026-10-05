CREATE TABLE IF NOT EXISTS dh_m40_agent_tasks (
 task_id VARCHAR(128) PRIMARY KEY,
 run_id VARCHAR(32) NOT NULL REFERENCES dh_m08_runs(run_id),
 node_id VARCHAR(128) NOT NULL,
 query_hash VARCHAR(64) NOT NULL,
 claim_token VARCHAR(32) NOT NULL,
 state VARCHAR(32) NOT NULL,
 body JSON NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(run_id,node_id),
 UNIQUE(run_id,query_hash)
);
