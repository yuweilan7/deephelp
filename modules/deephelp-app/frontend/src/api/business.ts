export type BusinessKind =
  | "orders"
  | "coupons"
  | "activities"
  | "eligibilities"
  | "attempts"
  | "refunds"
  | "coupon_returns";
export interface Money {
  amount: string;
  currency: "CNY";
}
export interface BusinessRecord {
  version: number;
  data: Record<string, string | string[] | Money | number | null>;
}
export interface Field {
  key: string;
  label: string;
  options?: string[];
}
export const fields: Record<BusinessKind, Field[]> = {
  attempts: [
    { key: "attempt_id", label: "下单尝试编号" },
    {
      key: "status",
      label: "尝试状态",
      options: ["failed", "succeeded", "unknown"],
    },
    { key: "failure_code", label: "来源原因代码（未知填 unknown）" },
    { key: "reason", label: "来源原因（合成维护记录）" },
    { key: "follow_up", label: "来源后续指引（查询不会执行）" },
  ],
  refunds: [
    { key: "refund_id", label: "退款编号" },
    { key: "order_id", label: "所属订单编号" },
    { key: "coupon_id", label: "关联优惠券编号（须属于该订单）" },
    {
      key: "status",
      label: "退款事实状态",
      options: ["pending", "completed", "failed", "unknown"],
    },
    { key: "reason", label: "来源退款说明" },
  ],
  coupon_returns: [
    { key: "refund_id", label: "退款编号（须已维护同主体退款）" },
    { key: "coupon_id", label: "来源回退券编号" },
    {
      key: "status",
      label: "回退来源状态",
      options: ["pending", "returned", "failed", "unknown"],
    },
    { key: "reason", label: "来源回退说明（关联矛盾将显示 conflict）" },
  ],
  eligibilities: [
    { key: "activity_id", label: "所属活动编号（须已维护同主体活动）" },
    {
      key: "eligibility",
      label: "资格状态",
      options: ["eligible", "ineligible", "unknown"],
    },
    {
      key: "remaining_slots",
      label: "剩余名额（非负整数，unknown表示未维护）",
    },
    { key: "reason", label: "资格记录依据（合成资料）" },
  ],
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
        value != null && typeof value === "object" && !Array.isArray(value)
          ? value.amount
          : Array.isArray(value)
            ? value.join(",")
            : value;
      if (f.key === "remaining_slots" && value == null) text = "unknown";
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
      return [f.key, String(text)];
    }),
  );
}
export function recordBody(kind: BusinessKind, values: Record<string, string>) {
  const data: BusinessRecord["data"] = {};
  for (const f of fields[kind]) {
    const value = values[f.key].trim();
    if (f.key === "remaining_slots") {
      if (value === "unknown") data[f.key] = null;
      else {
        if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))
          throw new Error(
            "剩余名额必须是可准确表示的非负整数，未维护时填写 unknown。",
          );
        data[f.key] = Number(value);
      }
      continue;
    }
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
