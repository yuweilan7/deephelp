CREATE TABLE IF NOT EXISTS dh_m44_corpus_workspaces (
 tenant_id VARCHAR(128) NOT NULL,
 user_id VARCHAR(128) NOT NULL,
 catalog_version VARCHAR(128) NOT NULL,
 revision BIGINT NOT NULL CHECK (revision >= 0),
 body JSON NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (tenant_id,user_id,catalog_version)
);
