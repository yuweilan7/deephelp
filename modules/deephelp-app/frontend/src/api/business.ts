export type BusinessKind = "orders" | "coupons" | "activities";
export interface Money {
  amount: string;
  currency: "CNY";
}
export interface BusinessRecord {
  version: number;
  data: Record<string, string | string[] | Money>;
}
export interface Field {
  key: string;
  label: string;
  options?: string[];
}
export const fields: Record<BusinessKind, Field[]> = {
  orders: [
    { key: "order_id", label: "订单编号" },
    { key: "paid", label: "实付金额（CNY）" },
    { key: "discount", label: "优惠金额（CNY）" },
    {
      key: "discount_status",
      label: "优惠状态",
      options: ["applied", "not_eligible", "missing"],
    },
    { key: "sku_ids", label: "商品编号（逗号分隔）" },
    { key: "status", label: "记录状态", options: ["active", "inactive"] },
  ],
  coupons: [
    { key: "coupon_id", label: "优惠券编号" },
    { key: "order_id", label: "所属订单编号" },
    { key: "minimum_spend", label: "门槛金额（CNY）" },
    {
      key: "status",
      label: "优惠券状态",
      options: [
        "usable",
        "expired",
        "threshold_not_met",
        "already_used",
        "not_started",
        "scope_mismatch",
        "frozen",
        "revoked",
      ],
    },
    { key: "valid_from", label: "开始时间（含时区）" },
    { key: "valid_until", label: "结束时间（含时区）" },
  ],
  activities: [
    { key: "activity_id", label: "活动编号" },
    { key: "label", label: "活动名称" },
    { key: "rules", label: "规则说明" },
    { key: "status", label: "活动状态", options: ["active", "inactive"] },
    { key: "valid_from", label: "开始时间（含时区）" },
    { key: "valid_until", label: "结束时间（含时区）" },
  ],
};
export function editorValues(
  kind: BusinessKind,
  record: BusinessRecord | null,
): Record<string, string> {
  return Object.fromEntries(
    fields[kind].map((f) => {
      const value = record?.data[f.key];
      let text =
        typeof value === "object" && !Array.isArray(value)
          ? value.amount
          : Array.isArray(value)
            ? value.join(",")
            : value;
      text ??=
        f.options?.[0] ??
        (["paid", "discount", "minimum_spend"].includes(f.key)
          ? "0.00"
          : f.key === "sku_ids"
            ? "SKU-1"
            : f.key === "valid_from"
              ? new Date(Date.now() - 86400000).toISOString()
              : f.key === "valid_until"
                ? new Date(Date.now() + 365 * 86400000).toISOString()
                : "");
      return [f.key, text];
    }),
  );
}
export function recordBody(kind: BusinessKind, values: Record<string, string>) {
  const data: BusinessRecord["data"] = {};
  for (const f of fields[kind]) {
    const value = values[f.key].trim();
    data[f.key] = ["paid", "discount", "minimum_spend"].includes(f.key)
      ? { amount: value, currency: "CNY" }
      : f.key === "sku_ids"
        ? value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : value;
  }
  if (kind === "activities") data.scope = "owner";
  return data;
}
export function updateBody(
  record: BusinessRecord,
  data: BusinessRecord["data"],
) {
  return { expected_version: record.version, data };
}
