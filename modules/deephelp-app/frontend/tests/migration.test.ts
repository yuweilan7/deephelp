import { describe, it, expect } from "vitest";
import {
  editorValues,
  recordBody,
  updateBody,
  type BusinessRecord,
} from "../src/api/business";
import {
  canStart,
  canCancel,
  canResume,
  type Task,
} from "../src/api/evaluation";
import { navigation } from "../src/content/navigation";
import { safeUrl } from "../src/api/learning";
describe("migrated capability boundaries", () => {
  it("keeps decimal strings and sends the original CAS version", () => {
    const record: BusinessRecord = {
      version: 7,
      data: {
        order_id: "000042",
        paid: { amount: "999999999999999.99", currency: "CNY" },
        discount: { amount: "0.01", currency: "CNY" },
        discount_status: "applied",
        sku_ids: ["SKU-1"],
        status: "active",
      },
    };
    const values = editorValues("orders", record),
      data = recordBody("orders", values);
    expect(data.paid).toEqual({
      amount: "999999999999999.99",
      currency: "CNY",
    });
    expect(data.order_id).toBe("000042");
    expect(updateBody(record, data).expected_version).toBe(7);
    expect(record.version).toBe(7);
  });
  it("separates cancelling, cancelled, interrupted and attempts without resetting budget", () => {
    const task: Task = {
      id: "id",
      state: "CANCELLING",
      attempt: 2,
      options: { action: "offline" },
    };
    expect(canStart([task])).toBe(false);
    expect(canCancel(task)).toBe(true);
    expect(canResume(task, 3)).toBe(false);
    task.state = "INTERRUPTED";
    expect(canResume(task, 3)).toBe(true);
    expect(canCancel(task)).toBe(false);
    task.attempt = 3;
    expect(canResume(task, 3)).toBe(false);
    task.state = "SUCCEEDED";
    task.attempt = 1;
    expect(canResume(task, 3)).toBe(false);
  });
  it("offers each learning task exactly once and rejects executable platform links", () => {
    expect(navigation.map((n) => n.path)).toEqual([
      "/",
      "/conversation",
      "/business",
      "/analysis",
      "/memory",
      "/tools",
      "/observation",
      "/evaluation",
    ]);
    expect(safeUrl("javascript:alert(1)")).toBeUndefined();
    expect(safeUrl("https://example.test/run")).toBe(
      "https://example.test/run",
    );
  });
});
