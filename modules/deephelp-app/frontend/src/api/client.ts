import type { Workspace } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export class ApiClient {
  csrf = "";
  onWorkspace?: (value: Workspace) => void;
  constructor(private transport: typeof fetch = (...args) => fetch(...args)) {}
  async connect(signal?: AbortSignal): Promise<Workspace> {
    const value = await this.request<Workspace>(
      "/auth/session",
      "GET",
      undefined,
      signal,
      false,
    );
    this.csrf = value.csrf;
    this.onWorkspace?.(value);
    return value;
  }
  async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
    reconnect = true,
  ): Promise<T> {
    const response = await this.transport(path, {
      method,
      signal,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(this.csrf ? { "X-CSRF-Token": this.csrf } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (response.status === 401 && reconnect && path !== "/auth/session") {
      await this.connect(signal);
      // Reads can reconnect once. A write always needs another explicit action.
      if (method === "GET")
        return this.request(path, method, body, signal, false);
      throw new ApiError(
        "个人工作区已恢复。请核对后再次执行原操作；原消息编号仍保留。",
        401,
      );
    }
    const data = await response.json().catch(() => null);
    if (!response.ok)
      throw new ApiError(
        data?.reply ||
          data?.error?.message ||
          `服务请求失败（${response.status}），请检查对应服务。`,
        response.status,
      );
    return data as T;
  }
}
export const api = new ApiClient();
