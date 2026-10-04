CREATE TABLE IF NOT EXISTS dh_m08_messages (
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 channel VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 message_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 run_id CHAR(32) NOT NULL,
 body JSON NOT NULL,
 received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY (tenant_id,user_id,channel,message_id),
 UNIQUE KEY uq_m08_message_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m08_runs (
 run_id CHAR(32) PRIMARY KEY,
 question_id CHAR(32) NOT NULL,
 status VARCHAR(32) NOT NULL,
 response JSON NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 finished_at DATETIME(6) NULL,
 UNIQUE KEY uq_m08_run_question (question_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m08_questions (
 question_id CHAR(32) PRIMARY KEY,
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 session_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 version BIGINT NOT NULL,
 status VARCHAR(32) NOT NULL,
 body JSON NOT NULL,
 KEY ix_m08_session (tenant_id,user_id,session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
