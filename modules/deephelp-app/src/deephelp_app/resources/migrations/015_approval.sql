CREATE TABLE IF NOT EXISTS dh_m15_checkpoints (
 thread_id VARCHAR(128) NOT NULL,
 checkpoint_ns VARCHAR(128) NOT NULL,
 checkpoint_id VARCHAR(64) NOT NULL,
 parent_id VARCHAR(64) NULL,
 checkpoint_type VARCHAR(32) NOT NULL,
 checkpoint LONGBLOB NOT NULL,
 metadata_type VARCHAR(32) NOT NULL,
 metadata LONGBLOB NOT NULL,
 PRIMARY KEY (thread_id,checkpoint_ns,checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m15_checkpoint_writes (
 thread_id VARCHAR(128) NOT NULL,
 checkpoint_ns VARCHAR(128) NOT NULL,
 checkpoint_id VARCHAR(64) NOT NULL,
 task_id VARCHAR(128) NOT NULL,
 write_index INT NOT NULL,
 channel VARCHAR(128) NOT NULL,
 value_type VARCHAR(32) NOT NULL,
 value LONGBLOB NOT NULL,
 task_path TEXT NOT NULL,
 PRIMARY KEY (thread_id,checkpoint_ns,checkpoint_id,task_id,write_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m15_operations (
 operation_id VARCHAR(128) PRIMARY KEY,
 run_id CHAR(32) NOT NULL,
 question_id CHAR(32) NOT NULL,
 status VARCHAR(32) NOT NULL,
 approval_status VARCHAR(32) NOT NULL,
 expires_at DATETIME(6) NOT NULL,
 body JSON NOT NULL,
 lease_token CHAR(32) NULL,
 lease_until DATETIME(6) NULL,
 UNIQUE KEY uq_m15_run (run_id),
 KEY ix_m15_question (question_id,status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m15_audit (
 event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
 operation_id VARCHAR(128) NOT NULL,
 kind VARCHAR(32) NOT NULL,
 body JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 KEY ix_m15_audit (operation_id,event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m15_synthetic_effects (
 operation_id VARCHAR(128) PRIMARY KEY,
 binding_hash CHAR(64) NOT NULL,
 body JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m15_synthetic_calls (
 call_id CHAR(32) PRIMARY KEY,
 operation_id VARCHAR(128) NOT NULL,
 kind VARCHAR(32) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 KEY ix_m15_calls (operation_id,kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
