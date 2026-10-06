CREATE TABLE IF NOT EXISTS dh_m46_memories (
 tenant_id VARCHAR(128) COLLATE "C" NOT NULL,
 user_id VARCHAR(128) COLLATE "C" NOT NULL,
 memory_id VARCHAR(64) NOT NULL,
 revision BIGINT NOT NULL,
 source_session VARCHAR(128) COLLATE "C" NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY(tenant_id,user_id,memory_id)
);
CREATE TABLE IF NOT EXISTS dh_m46_memory_versions (
 tenant_id VARCHAR(128) COLLATE "C" NOT NULL,
 user_id VARCHAR(128) COLLATE "C" NOT NULL,
 memory_id VARCHAR(64) NOT NULL,
 revision BIGINT NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY(tenant_id,user_id,memory_id,revision)
);
CREATE TABLE IF NOT EXISTS dh_m46_scopes (
 tenant_id VARCHAR(128) COLLATE "C" NOT NULL,
 user_id VARCHAR(128) COLLATE "C" NOT NULL,
 target_session VARCHAR(128) COLLATE "C" NOT NULL,
 revision BIGINT NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY(tenant_id,user_id,target_session)
);
CREATE INDEX IF NOT EXISTS ix_m46_sources ON dh_m46_memories(tenant_id,user_id,source_session);
