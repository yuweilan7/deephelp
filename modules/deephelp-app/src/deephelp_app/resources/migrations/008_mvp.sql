CREATE TABLE IF NOT EXISTS dh_m08_messages (
 tenant_id VARCHAR(128) COLLATE "C" NOT NULL,
 user_id VARCHAR(128) COLLATE "C" NOT NULL,
 channel VARCHAR(128) COLLATE "C" NOT NULL,
 message_id VARCHAR(128) COLLATE "C" NOT NULL,
 payload_hash VARCHAR(64) NOT NULL,
 run_id VARCHAR(32) NOT NULL,
 body JSON NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (tenant_id,user_id,channel,message_id),
 CONSTRAINT uq_m08_message_run UNIQUE (run_id)
);
CREATE TABLE IF NOT EXISTS dh_m08_runs (
 run_id VARCHAR(32) PRIMARY KEY,
 question_id VARCHAR(32) NOT NULL,
 status VARCHAR(32) NOT NULL,
 response JSON NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS dh_m08_questions (
 question_id VARCHAR(32) PRIMARY KEY,
 tenant_id VARCHAR(128) COLLATE "C" NOT NULL,
 user_id VARCHAR(128) COLLATE "C" NOT NULL,
 session_id VARCHAR(128) COLLATE "C" NOT NULL,
 version BIGINT NOT NULL CHECK (version >= 0),
 status VARCHAR(32) NOT NULL,
 body JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_m08_session ON dh_m08_questions (tenant_id,user_id,session_id);
CREATE INDEX IF NOT EXISTS ix_m10_run_question ON dh_m08_runs(question_id,status);
