CREATE TABLE IF NOT EXISTS dh_m10_sessions (
 tenant_id VARCHAR(128) NOT NULL,
 user_id VARCHAR(128) NOT NULL,
 session_id VARCHAR(128) NOT NULL,
 generation BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY (tenant_id,user_id,session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m10_members (
 tenant_id VARCHAR(128) NOT NULL,
 user_id VARCHAR(128) NOT NULL,
 session_id VARCHAR(128) NOT NULL,
 channel VARCHAR(128) NOT NULL,
 message_id VARCHAR(128) NOT NULL,
 question_id CHAR(32) NOT NULL,
 occurred_at DATETIME(6) NOT NULL,
 PRIMARY KEY (tenant_id,user_id,channel,message_id),
 KEY ix_m10_history (tenant_id,user_id,session_id,occurred_at),
 KEY ix_m10_question (question_id,occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m10_outbox (
 event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
 question_id CHAR(32) NOT NULL,
 version BIGINT NOT NULL,
 body JSON NOT NULL,
 reason JSON NOT NULL,
 done BOOLEAN NOT NULL DEFAULT FALSE,
 attempts INT NOT NULL DEFAULT 0,
 lease_token CHAR(32) NULL,
 lease_until DATETIME(6) NULL,
 last_error VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_m10_event_version (question_id,version),
 KEY ix_m10_pending (done,event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
