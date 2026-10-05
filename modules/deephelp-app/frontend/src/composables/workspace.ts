import { reactive } from "vue";
import { api } from "../api/client";
import type { Workspace, MessageInput, RunResponse } from "../api/types";

// Only identity/budget, the current run, and an uncertain original send cross pages.
export const workspace = reactive({
  connection: null as Workspace | null,
  error: "",
  session: crypto.randomUUID() as string,
  selected: null as { session_id: string; response: RunResponse } | null,
  pending: null as MessageInput | null,
  sending: false,
});
api.onWorkspace = (value) => {
  workspace.connection = value;
  workspace.error = "";
};
export async function connect() {
  try {
    await api.connect();
  } catch (e) {
    workspace.error = (e as Error).message;
  }
}
