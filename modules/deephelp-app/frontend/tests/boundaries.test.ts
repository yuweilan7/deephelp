import { describe, it, expect, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";
import { LatestRead } from "../src/composables/latest";
import { newMessage, uncertain } from "../src/composables/message";
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
describe("browser business boundaries", () => {
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
