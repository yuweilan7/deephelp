import { describe, it, expect, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";
import { LatestRead } from "../src/composables/latest";
import { newMessage, uncertain } from "../src/composables/message";
import { editorValues, recordBody } from "../src/api/business";
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
describe("browser business boundaries", () => {
  it.each([null, 0, 7])("preserves maintained capacity %s when reopened and saved", (remaining) => {
    const data = {
      activity_id: "ACT-42", eligibility: "eligible", remaining_slots: remaining,
      reason: "合成资格维护",
    };
    const editor = editorValues("eligibilities", { version: 2, data });
    expect(editor.remaining_slots).toBe(remaining === null ? "unknown" : String(remaining));
    expect(recordBody("eligibilities", editor)).toEqual(data);
  });
  it.each(["", "  ", "-1", "1.5", "abc", "9007199254740993"])(
    "rejects invalid capacity %s instead of silently writing unknown or zero", (remaining) => {
      const editor = editorValues("eligibilities", null);
      editor.remaining_slots = remaining;
      expect(() => recordBody("eligibilities", editor)).toThrow("非负整数");
    },
  );
  it("reconnects reads once but never automatically repeats writes", async () => {
    const transport = vi
      .fn()
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ csrf: "new" }))
      .mockResolvedValueOnce(json({ answer: 42 }));
    const api = new ApiClient(transport);
    expect(await api.request("/history")).toEqual({ answer: 42 });
    expect(transport).toHaveBeenCalledTimes(3);
    transport
      .mockReset()
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ csrf: "new" }));
    await expect(
      api.request("/converse", "POST", { message_id: "original" }),
    ).rejects.toMatchObject({ status: 401 });
    expect(transport).toHaveBeenCalledTimes(2);
  });
  it("discards stale reads even if the transport ignores cancellation", async () => {
    const read = new LatestRead(),
      published: string[] = [];
    let resolve!: (v: string) => void;
    const stale = read.run(
      () =>
        new Promise<string>((r) => {
          resolve = r;
        }),
      (x) => published.push(x),
    );
    await read.run(
      async () => "current",
      (x) => published.push(x),
    );
    resolve("stale");
    await stale;
    expect(published).toEqual(["current"]);
    const cancelled = read.run(
      () =>
        new Promise<string>((r) => {
          resolve = r;
        }),
      (x) => published.push(x),
    );
    read.cancel();
    resolve("unmounted");
    await cancelled;
    expect(published).toEqual(["current"]);
  });
  it("binds new messages to the original question and preserves uncertain payloads", () => {
    const message = newMessage("s", "000042", {
      question_id: "q",
      version: 7,
      status: "WAITING_SLOT",
    });
    expect(message.question_hint).toBe("q");
    expect(message.expected_question_version).toBe(7);
    const wire = JSON.stringify(message);
    expect(uncertain(new TypeError("network"))).toBe(true);
    expect(uncertain(new ApiError("server", 503))).toBe(true);
    expect(uncertain(new ApiError("conflict", 409))).toBe(false);
    expect(JSON.stringify(message)).toBe(wire);
    expect(newMessage("s", "new", null).message_id).not.toBe(
      message.message_id,
    );
  });
});
