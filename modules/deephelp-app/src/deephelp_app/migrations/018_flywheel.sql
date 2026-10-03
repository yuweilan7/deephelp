CREATE TABLE IF NOT EXISTS dh_m18_candidates (
 candidate_id CHAR(64) PRIMARY KEY,
 run_id CHAR(32) NOT NULL UNIQUE,
 revision BIGINT NOT NULL,
 body JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m18_reviews (
 candidate_id CHAR(64) NOT NULL,
 revision BIGINT NOT NULL,
 body JSON NOT NULL,
 reviewed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY(candidate_id,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m18_tasks (
 job_id BIGINT AUTO_INCREMENT PRIMARY KEY,
 candidate_id CHAR(64) NOT NULL,
 revision BIGINT NOT NULL,
 route VARCHAR(16) NOT NULL,
 state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
 attempts INT NOT NULL DEFAULT 0,
 lease_token CHAR(32) NULL,
 lease_until DATETIME(6) NULL,
 UNIQUE KEY uq_m18_task(candidate_id,revision,route)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS dh_m18_derivatives (
 candidate_id CHAR(64) NOT NULL,
 revision BIGINT NOT NULL,
 route VARCHAR(16) NOT NULL,
 artifact_path VARCHAR(512) NOT NULL,
 artifact_sha256 CHAR(64) NOT NULL,
 PRIMARY KEY(candidate_id,revision,route,artifact_sha256,artifact_path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
