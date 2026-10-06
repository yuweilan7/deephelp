CREATE TABLE IF NOT EXISTS dh_m20_attempts (
 tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, record_id TEXT NOT NULL,
 version BIGINT NOT NULL CHECK(version >= 1), body TEXT NOT NULL,
 PRIMARY KEY(tenant_id,user_id,record_id)
);
CREATE TABLE IF NOT EXISTS dh_m20_refunds (
 tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, record_id TEXT NOT NULL,
 version BIGINT NOT NULL CHECK(version >= 1), body TEXT NOT NULL,
 order_id TEXT NOT NULL, coupon_id TEXT NOT NULL,
 PRIMARY KEY(tenant_id,user_id,record_id),
 FOREIGN KEY(tenant_id,user_id,order_id) REFERENCES dh_m20_orders(tenant_id,user_id,record_id),
 FOREIGN KEY(tenant_id,user_id,coupon_id) REFERENCES dh_m20_coupons(tenant_id,user_id,record_id)
);
CREATE TABLE IF NOT EXISTS dh_m20_coupon_returns (
 tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, record_id TEXT NOT NULL,
 version BIGINT NOT NULL CHECK(version >= 1), body TEXT NOT NULL,
 coupon_id TEXT NOT NULL,
 PRIMARY KEY(tenant_id,user_id,record_id),
 FOREIGN KEY(tenant_id,user_id,record_id) REFERENCES dh_m20_refunds(tenant_id,user_id,record_id),
 FOREIGN KEY(tenant_id,user_id,coupon_id) REFERENCES dh_m20_coupons(tenant_id,user_id,record_id)
);
