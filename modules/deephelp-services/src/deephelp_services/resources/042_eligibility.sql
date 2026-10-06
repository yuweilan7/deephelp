CREATE TABLE IF NOT EXISTS dh_m20_eligibilities (
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    record_id VARCHAR(64) NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, user_id, record_id),
    FOREIGN KEY (tenant_id, user_id, record_id)
        REFERENCES dh_m20_activities (tenant_id, user_id, record_id)
);
