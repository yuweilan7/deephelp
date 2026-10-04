CREATE TABLE IF NOT EXISTS dh_m20_orders (
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 record_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 version BIGINT UNSIGNED NOT NULL,
 paid DECIMAL(20,2) NOT NULL,
 discount DECIMAL(20,2) NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY (tenant_id,user_id,record_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dh_m20_activities (
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 record_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 version BIGINT UNSIGNED NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY (tenant_id,user_id,record_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dh_m20_coupons (
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 record_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 version BIGINT UNSIGNED NOT NULL,
 order_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 minimum_spend DECIMAL(20,2) NOT NULL,
 body JSON NOT NULL,
 PRIMARY KEY (tenant_id,user_id,record_id),
 FOREIGN KEY (tenant_id,user_id,order_id) REFERENCES dh_m20_orders(tenant_id,user_id,record_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dh_m20_order_activities (
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 order_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 activity_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 PRIMARY KEY (tenant_id,user_id,order_id,activity_id),
 FOREIGN KEY (tenant_id,user_id,order_id) REFERENCES dh_m20_orders(tenant_id,user_id,record_id),
 FOREIGN KEY (tenant_id,user_id,activity_id) REFERENCES dh_m20_activities(tenant_id,user_id,record_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS dh_m20_evidence (
 evidence_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
 tenant_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 user_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
 call_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL UNIQUE,
 snapshot JSON NOT NULL,
 result JSON NOT NULL,
 created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 INDEX owner_evidence (tenant_id,user_id,evidence_id)
) ENGINE=InnoDB;
