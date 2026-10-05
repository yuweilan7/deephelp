import { ref, shallowRef, onBeforeUnmount } from "vue";
import { api } from "../api/client";
import { LatestRead } from "./latest";
export function useRead<T>() {
  const data = shallowRef<T | null>(null),
    error = ref(""),
    loading = ref(false);
  const current = new LatestRead();
  function clear() {
    current.cancel();
    data.value = null;
    error.value = "";
    loading.value = false;
  }
  async function load(path: string, keepData = false) {
    if (!keepData) data.value = null;
    error.value = "";
    loading.value = true;
    try {
      await current.run(
        (signal) => api.request<T>(path, "GET", undefined, signal),
        (value) => {
          data.value = value;
          loading.value = false;
        },
      );
    } catch (e) {
      error.value = (e as Error).message;
      loading.value = false;
    }
  }
  onBeforeUnmount(clear);
  return { data, error, loading, load, clear };
}
export function useAction() {
  const error = ref(""),
    busy = ref(false),
    notice = ref("");
  async function run(action: () => Promise<void>) {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    notice.value = "";
    try {
      await action();
    } catch (e) {
      error.value = (e as Error).message;
    } finally {
      busy.value = false;
    }
  }
  return { error, busy, notice, run };
}
